import json
import os
from pathlib import Path
import tempfile
import unittest
from harness import runner as h


def event(**data):
    return json.dumps(data) + '\n'


def claude_result(text='OK', is_error=False, **extra):
    return event(type='result', subtype='success' if not is_error else 'error_during_execution',
                 is_error=is_error, result=text, **extra)


def codex_result(text='OK'):
    return event(type='item.completed', item={'type':'agent_message','text':text}) + event(type='turn.completed', usage={'input_tokens':10,'output_tokens':2})


class EnvironmentTests(unittest.TestCase):
    def test_env_expansion_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'.env'
            p.write_text('AGENTS_ROOT="${HOME}/work"\nSKILLS_ROOT="${AGENTS_ROOT}/skills"\n')
            env=h.load_dotenv(p, {'HOME':d,'GH_TOKEN':'existing'})
            self.assertEqual(env['SKILLS_ROOT'], d+'/work/skills')
            self.assertEqual(env['GH_TOKEN'],'existing')

    def test_dotenv_rejects_shell_expressions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'.env'; p.write_text('AGENTS_ROOT="$(touch injected)"')
            with self.assertRaises(ValueError): h.load_dotenv(p, {})
            self.assertFalse((Path(d)/'injected').exists())

    def test_auth_context_retained_nested_markers_removed(self):
        env={'HOME':'home','PATH':'path','GH_TOKEN':'gh','ANTHROPIC_API_KEY':'api',
             'CODEX_HOME':'codex','CLAUDE_CONFIG_DIR':'claude','CLAUDECODE':'1',
             'CLAUDE_CODE_SIMPLE':'1','CODEX_THREAD_ID':'parent'}
        actual=h.child_env(env)
        for key in ['HOME','PATH','GH_TOKEN','ANTHROPIC_API_KEY','CODEX_HOME','CLAUDE_CONFIG_DIR']:
            self.assertEqual(actual[key],env[key])
        for key in ['CLAUDECODE','CLAUDE_CODE_SIMPLE','CODEX_THREAD_ID']:
            self.assertNotIn(key,actual)
        self.assertIn('CLAUDECODE',env)

    def test_redaction(self):
        token='example-private-test-value'
        text=h.redact('Authorization: Bearer '+token+'\nvalue='+token,{'GH_TOKEN':token})
        self.assertNotIn(token,text)
        self.assertIn('[REDACTED]',text)


class ClassifierTests(unittest.TestCase):
    def test_claude_usage_limit(self):
        r=h.classify('claude',claude_result("You've hit your limit · resets 7pm",True),'',1)
        self.assertEqual(r.status,'usage_limit')

    def test_structured_limit(self):
        stream=event(type='assistant', error='rate_limit', message={'content':[]})
        self.assertEqual(h.classify('claude',stream,'',1).status,'usage_limit')

    def test_auth_not_limit(self):
        r=h.classify('claude',claude_result('API Error: 401 Invalid authentication credentials',True),'',1)
        self.assertEqual(r.status,'auth_error')

    def test_tool_output_cannot_trigger_fallback(self):
        stream=event(type='user',message={'content':[{'type':'tool_result','content':"You've hit your limit"}]})
        self.assertEqual(h.classify('claude',stream,'',1).status,'failed')

    def test_success_mentioning_limit_is_success(self):
        self.assertEqual(h.classify('claude',claude_result('Fix the rate limit parser'),' ',0).status,'completed')

    def test_warning_does_not_override_success(self):
        stream=event(type='rate_limit_event',rate_limit_info={'status':'allowed_warning'})+claude_result()
        self.assertEqual(h.classify('claude',stream,'',0).status,'completed')

    def test_permission_denied_not_success(self):
        out=claude_result('I could not read files',permission_denials=[{'tool_name':'Read'}])
        self.assertEqual(h.classify('claude',out,'',0).status,'permission_denied')

    def test_budget_exhaustion_not_usage_limit(self):
        out=event(type='result',subtype='error_max_budget_usd',is_error=True)
        self.assertEqual(h.classify('claude',out,'',1).status,'budget_exhausted')

    def test_missing_terminal_result_not_success(self):
        self.assertEqual(h.classify('codex',event(type='thread.started'),' ',0).status,'failed')

    def test_empty_claude_terminal_result_is_not_success(self):
        self.assertEqual(h.classify('claude',claude_result(''),'',0).status,'failed')

    def test_codex_success(self):
        r=h.classify('codex',codex_result(),' ',0)
        self.assertEqual(r.status,'completed'); self.assertEqual(r.text,'OK')

    def test_network_error_not_limit(self):
        self.assertEqual(h.classify('claude',claude_result('Connection timeout',True),'',1).status,'failed')


class CommandTests(unittest.TestCase):
    def test_claude_review_without_confirmation_or_write_tools(self):
        c=h.build_command('claude','review','sonnet','low')
        self.assertEqual(c[c.index('--permission-mode')+1],'dontAsk')
        self.assertEqual(c[c.index('--tools')+1],'Read,Grep,Glob')
        self.assertIn('--safe-mode',c)
        self.assertNotIn('--bare',c)
        self.assertNotIn('--dangerously-skip-permissions',c)

    def test_codex_explicit_model_and_readonly(self):
        c=h.build_command('codex','review','gpt-5.6-luna','low')
        self.assertEqual(c[c.index('-m')+1],'gpt-5.6-luna')
        self.assertEqual(c[c.index('-s')+1],'read-only')
        self.assertIn('approval_policy="never"',c)
        self.assertIn('multi_agent',c)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); (self.root/'work').mkdir(); (self.root/'vault').mkdir()
        self.env={'PATH':os.environ.get('PATH',''),'HOME':str(self.root)}
        self.job=h.Job(self.root/'work',self.root/'vault','Review changed behavior')
        self.review=json.dumps({'verdict':'approve','findings':[],'limitations':[]})

    def executor(self, responses):
        self.calls=[]
        def run(argv,env,cwd,prompt,timeout):
            self.calls.append((argv,cwd,prompt,timeout))
            return responses[len(self.calls)-1]
        return run

    def test_quota_fallback_preserves_workspace_and_prompt(self):
        run=self.executor([h.ProcessResult(1,claude_result("You've hit your limit",True)),h.ProcessResult(0,codex_result(self.review))])
        result=h.run_job(self.job,self.env,run)
        self.assertEqual(result['status'],'completed')
        self.assertEqual([x[0][0] for x in self.calls],['claude','codex'])
        self.assertEqual(self.calls[0][1],self.calls[1][1])
        self.assertIn(self.job.prompt,self.calls[1][2])
        self.assertIn('引き継ぎ',self.calls[1][2])
        self.assertLessEqual(self.calls[1][3],self.calls[0][3])
        self.assertTrue((Path(result['run_dir'])/'result.json').is_file())

    def test_auth_failure_never_falls_back(self):
        run=self.executor([h.ProcessResult(1,claude_result('Please run /login',True))])
        result=h.run_job(self.job,self.env,run)
        self.assertEqual(result['status'],'auth_error'); self.assertEqual(len(self.calls),1)

    def test_timeout_never_falls_back(self):
        run=self.executor([h.ProcessResult(124,timed_out=True)])
        self.assertEqual(h.run_job(self.job,self.env,run)['status'],'timeout')
        self.assertEqual(len(self.calls),1)

    def test_codex_failure_is_not_retried(self):
        run=self.executor([h.ProcessResult(1,claude_result("You've hit your limit",True)),h.ProcessResult(1,event(type='turn.failed',error={'message':'usage limit reached'}))])
        self.assertNotEqual(h.run_job(self.job,self.env,run)['status'],'completed')
        self.assertEqual(len(self.calls),2)

    def test_review_question_is_incomplete(self):
        run=self.executor([h.ProcessResult(0,claude_result('May I review these files?'))])
        self.assertEqual(h.run_job(self.job,self.env,run)['status'],'review_incomplete')

    def test_review_findings_are_not_approval(self):
        text=json.dumps({'verdict':'request_changes','findings':[{'issue':'bug'}],'limitations':[]})
        run=self.executor([h.ProcessResult(0,claude_result(text))])
        self.assertEqual(h.run_job(self.job,self.env,run)['status'],'review_findings')

    def test_missing_vault_is_not_created(self):
        self.job.vault=self.root/'missing'
        with self.assertRaises(ValueError): h.run_job(self.job,self.env,self.executor([]))
        self.assertFalse(self.job.vault.exists())

    def test_no_fallback_option(self):
        self.job.fallback=False
        run=self.executor([h.ProcessResult(1,claude_result("You've hit your limit",True))])
        self.assertEqual(h.run_job(self.job,self.env,run)['status'],'usage_limit')
        self.assertEqual(len(self.calls),1)

    def test_persisted_secrets_redacted(self):
        self.env['GH_TOKEN']='sensitive-fixture-token'
        self.job.prompt+=' '+self.env['GH_TOKEN']
        run=self.executor([h.ProcessResult(0,claude_result(self.review))])
        result=h.run_job(self.job,self.env,run)
        for p in Path(result['run_dir']).rglob('*'):
            if p.is_file(): self.assertNotIn(self.env['GH_TOKEN'],p.read_text())

    def test_existing_live_process_is_not_duplicated(self):
        run_dir=self.root/'vault'/'01-Projects'/'agent-runs'/'existing'
        run_dir.mkdir(parents=True)
        state={'status':'running','pid':os.getpid(),'identity':h.process_identity(os.getpid())}
        h.save(run_dir/'0-claude-state.json',state,self.env)
        h.save(run_dir/'result.json',{'run_dir':str(run_dir),'workspace':str(self.job.workspace),
                                      'mode':'review','status':'running','attempts':[]},self.env)
        calls=[]
        result=h.run_job(self.job,self.env,lambda *args: calls.append(args),run_dir=run_dir)
        self.assertEqual(result['status'],'running')
        self.assertEqual(len(calls),0)
        self.assertEqual(result['active_process']['status'],'alive')

    def test_corrupt_persisted_result_is_explicitly_incomplete(self):
        run_dir=self.root/'vault'/'01-Projects'/'agent-runs'/'corrupt'
        run_dir.mkdir(parents=True)
        (run_dir/'result.json').write_text('{"status":')
        result=h.resume_job(self.job,run_dir,self.env,self.executor([]))
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['error'],'corrupt_result_record')

    def test_corrupt_process_state_blocks_replacement(self):
        run_dir=self.root/'vault'/'01-Projects'/'agent-runs'/'bad-state'
        run_dir.mkdir(parents=True)
        h.save(run_dir/'result.json',{'run_dir':str(run_dir),'workspace':str(self.job.workspace),
                                      'mode':'review','status':'running','attempts':[]},self.env)
        (run_dir/'0-claude-state.json').write_text('{"status":')
        calls=[]
        result=h.resume_job(self.job,run_dir,self.env,lambda *args: calls.append(args))
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['process_reconciliation'][0]['reason'],'corrupt_state_record')
        self.assertEqual(len(calls),0)

if __name__=='__main__': unittest.main()

class ProcessTests(unittest.TestCase):
    def test_timeout_is_bounded(self):
        import sys
        r=h.execute([sys.executable,'-c','import time; time.sleep(30)'],dict(os.environ),Path.cwd(),'',.05)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.code,124)

    def test_prompt_is_stdin_not_shell_code(self):
        import sys
        text='$(touch should-not-exist); `id` "quoted"'
        r=h.execute([sys.executable,'-c','import sys; print(sys.stdin.read())'],dict(os.environ),Path.cwd(),text,5)
        self.assertEqual(r.stdout.strip(),text)

    def test_missing_executable(self):
        r=h.execute(['agents-test-nonexistent-executable'],dict(os.environ),Path.cwd(),'',1)
        self.assertEqual(r.code,127)

    def test_startup_oserror_is_a_process_failure(self):
        from unittest.mock import patch
        with patch.object(h.subprocess, 'Popen', side_effect=PermissionError('blocked')):
            r=h.execute(['blocked'],dict(os.environ),Path.cwd(),'',1)
        self.assertEqual(r.code,126)
        self.assertIn('blocked',r.stderr)

    def test_incremental_output_files_are_redacted(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            out=root/'stdout.jsonl'; err=root/'stderr.log'
            env=dict(os.environ, GH_TOKEN='incremental-secret-value')
            script="import sys,time; print('part incremental-secret-value', flush=True); print('problem incremental-secret-value', file=sys.stderr, flush=True); time.sleep(0.01)"
            r=h.execute([sys.executable,'-c',script],env,Path.cwd(),'',2,
                        stdout_path=out,stderr_path=err,redaction_env=env)
            self.assertEqual(r.code,0)
            self.assertTrue(out.is_file()); self.assertTrue(err.is_file())
            self.assertNotIn('incremental-secret-value',out.read_text()+err.read_text())
            self.assertIn('[REDACTED]',out.read_text()+err.read_text())

    def test_save_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            h.save(p,{'status':'ok'},{})
            self.assertEqual(json.loads(p.read_text())['status'],'ok')
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            self.assertFalse(any(x.name.startswith('.state.json.') for x in Path(d).iterdir()))

    def test_doctor_failed_inference_is_failure(self):
        from unittest.mock import patch
        import contextlib,io
        with tempfile.TemporaryDirectory() as d:
            report={'tools':{'claude':{'auth_status_exit':0}},'claude_inference':{'status':'auth_error','exit_code':1}}
            with patch.dict(os.environ,{'AGENTS_VAULT_ROOT':d}), patch.object(h,'doctor',return_value=report), contextlib.redirect_stdout(io.StringIO()):
                code=h.main(['--environment','current','--env-file',str(Path(d)/'missing'), 'doctor','--probe'])
            self.assertEqual(code,2)

class ReviewFixTests(unittest.TestCase):
    def test_unknown_claude_error_retains_description(self):
        text='The requested model is temporarily overloaded'
        r=h.classify('claude',claude_result(text,True),'',1)
        self.assertEqual(r.status,'failed'); self.assertIn(text,r.text)

    def test_fenced_review_json(self):
        text='```json\n'+json.dumps({'verdict':'request_changes','findings':[{'issue':'bug'}],'limitations':[]})+'\n```'
        self.assertEqual(h.review_verdict(text),'review_findings')

    def test_approval_with_nonblocking_notes(self):
        text=json.dumps({'verdict':'approve','findings':[],'limitations':['Only reviewed requested files']})
        self.assertEqual(h.review_verdict(text),'completed')

    def test_probe_has_no_allowed_tools(self):
        from unittest.mock import patch
        calls=[]
        def execute(argv,*args):
            calls.append(argv)
            if argv[:3]==['claude','auth','status']:
                return h.ProcessResult(0,json.dumps({'loggedIn':True}))
            return h.ProcessResult(0,claude_result())
        with patch.object(h,'execute',side_effect=execute): h.doctor(dict(os.environ),dict(os.environ),True)
        probe=calls[-1]
        self.assertEqual(probe[probe.index('--tools')+1],'')
        self.assertNotIn('--allowedTools',probe)

    def test_classify_keeps_actual_model_unknown_when_provider_omits_it(self):
        result=h.classify('claude',claude_result(),'',0)
        self.assertIsNone(result.actual_model)
        self.assertFalse(result.model_verified)

    def test_classify_records_provider_model_when_present(self):
        result=h.classify('claude',claude_result(model='claude-sonnet-4-5'),'',0)
        self.assertEqual(result.actual_model,'claude-sonnet-4-5')
        self.assertTrue(result.model_verified)

    def test_doctor_reports_root_provenance_without_secret_values(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); vault=root/'vault'; vault.mkdir()
            env_file=root/'.env'; env_file.write_text('AGENTS_ROOT='+str(root)+'\nAGENTS_VAULT_ROOT='+str(vault)+'\nGH_TOKEN=secret-value\n')
            env=h.load_dotenv(env_file,{'PATH':os.environ.get('PATH',''),'HOME':str(root)})
            with patch.object(h,'execute',return_value=h.ProcessResult(127,stderr='missing')):
                report=h.doctor(env,dict(os.environ),False,env_file=env_file)
            self.assertEqual(report['roots']['AGENTS_ROOT']['provenance'],'env_file')
            self.assertTrue(report['roots']['AGENTS_VAULT_ROOT']['exists'])
            serialized=json.dumps(report)
            self.assertNotIn('secret-value',serialized)

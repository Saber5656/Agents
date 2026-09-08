import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch
from harness import runner as h
from harness.tasks import TaskStore


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

    def test_codex_run_adds_only_explicit_task_store_and_vault_dirs(self):
        dirs = ['/tmp/agents/.local', '/tmp/agents-vault']
        c=h.build_command('codex','run','gpt-5.6-luna','low',add_dirs=dirs)
        self.assertEqual(c[c.index('--add-dir')+1], dirs[0])
        second=c.index('--add-dir', c.index('--add-dir')+1)
        self.assertEqual(c[second+1], dirs[1])
        self.assertNotIn('--add-dir', h.build_command('codex','run','gpt-5.6-luna','low'))


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

    def test_run_capture_registers_discoveries_and_preserves_assigned_role_boundary(self):
        root=self.root; db=root/'.local'/'tasks.sqlite3'
        with TaskStore(db, agents_root=root, vault_root=root/'vault') as store:
            origin=store.create_task(purpose='assigned fixture', repository='Saber5656/Agents')
        self.env['AGENTS_ROOT']=str(root)
        self.job=h.Job(root/'work',root/'vault','Implement the assigned fixture',mode='run',provider='codex',
                       task_id=origin['id'],task_store_db=db)
        marker=json.dumps({'agents_worker_capture': {'discoveries': [{
            'event_key':'unrelated-1','purpose':'Unrelated improvement',
            'expected_result':'A separate test documents the behavior',
            'evidence_links':['vault://worker/discovery-1'],
            'repository':'Saber5656/Agents'}]}})
        result=h.run_job(self.job,self.env,self.executor([h.ProcessResult(0,codex_result(marker))]))
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['capture_status'],'captured')
        with TaskStore(db, agents_root=root, vault_root=root/'vault') as store:
            discovered=store.list_tasks(repository='Saber5656/Agents')
            self.assertEqual(len(discovered),2)
            followup=next(x for x in discovered if x['source_task_id']==origin['id'])
            self.assertEqual(followup['issueization_state'],'unissued')
            self.assertEqual(followup['evidence_links'],['vault://worker/discovery-1'])
        prompt=self.calls[0][2]
        self.assertIn(origin['id'],prompt)
        self.assertIn('GitHub Issue',prompt)
        command=json.loads((Path(result['run_dir'])/'0-codex-command.json').read_text())
        add_dirs=[command[i+1] for i,item in enumerate(command[:-1]) if item=='--add-dir']
        self.assertEqual(add_dirs,[str((root/'.local').resolve()),str((root/'vault').resolve())])

    def test_capture_retry_correlates_event_and_merges_evidence(self):
        root=self.root; db=root/'.local'/'tasks.sqlite3'
        with TaskStore(db, agents_root=root, vault_root=root/'vault') as store:
            origin=store.create_task(purpose='assigned fixture')
        first=json.dumps({'agents_worker_capture': {'originating_task_id':origin['id'], 'discoveries': [{
            'event_key':'same-event','purpose':'Captured follow-up','evidence_links':['vault://first']}]}})
        second=json.dumps({'agents_worker_capture': {'originating_task_id':origin['id'], 'discoveries': [{
            'event_key':'same-event','purpose':'Captured follow-up','evidence_links':['vault://first','vault://retry']}]}})
        first_rows=h.capture_discoveries(task_id=origin['id'],task_store_db=db,agents_root=root,vault_root=root/'vault',text=first)
        second_rows=h.capture_discoveries(task_id=origin['id'],task_store_db=db,agents_root=root,vault_root=root/'vault',text=second)
        self.assertEqual(first_rows[0]['id'],second_rows[0]['id'])
        self.assertEqual(second_rows[0]['evidence_links'],['vault://first','vault://retry'])

    def test_run_capture_rejects_malformed_discovery_without_lying_about_completion(self):
        root=self.root; db=root/'.local'/'tasks.sqlite3'
        with TaskStore(db, agents_root=root, vault_root=root/'vault') as store:
            origin=store.create_task(purpose='assigned fixture')
        self.env['AGENTS_ROOT']=str(root)
        self.job=h.Job(root/'work',root/'vault','assigned',mode='run',provider='codex',task_id=origin['id'],task_store_db=db)
        malformed=json.dumps({'agents_worker_capture': {'discoveries':[{'purpose':'missing event key'}]}})
        result=h.run_job(self.job,self.env,self.executor([h.ProcessResult(0,codex_result(malformed))]))
        self.assertEqual(result['status'],'incomplete')
        self.assertEqual(result['capture_status'],'incomplete')
        self.assertIn('capture',result['capture_error'])

    def test_resume_captures_terminal_output_before_reporting_completion(self):
        root=self.root; db=root/'.local'/'tasks.sqlite3'
        with TaskStore(db, agents_root=root, vault_root=root/'vault') as store:
            origin=store.create_task(purpose='assigned capture recovery')
        self.env['AGENTS_ROOT']=str(root)
        job=h.Job(root/'work',root/'vault','assigned',mode='run',provider='codex',
                  task_id=origin['id'],task_store_db=db)
        run_dir=root/'vault'/'capture-interrupted'; run_dir.mkdir()
        text=json.dumps({'agents_worker_capture':{'discoveries':[
            {'event_key':'recovered-event','purpose':'Recovered follow-up',
             'evidence_links':['vault://recovered']} ]}})
        h.save(run_dir/'result.json',{'status':'running','mode':'run','workspace':str(job.workspace),
            'capture_config':{'task_id':origin['id'],'task_store_db':str(db.resolve()),
                              'agents_root':str(root.resolve()),'vault_root':str(job.vault.resolve())},
            'attempts':[{'provider':'codex','status':'running'}]},self.env)
        h.save(run_dir/'0-codex-state.json',{'attempt':0,'provider':'codex','status':'completed','exit_code':0},self.env)
        h.save(run_dir/'0-codex-stdout.jsonl',codex_result(text),self.env)
        calls=[]
        result=h.resume_job(job,run_dir,self.env,lambda *args:calls.append(args))
        self.assertEqual('completed',result['status'])
        self.assertEqual('captured',result.get('capture_status'))
        again=h.resume_job(job,run_dir,self.env,lambda *args:calls.append(args))
        self.assertEqual(result['captured_discoveries'],again['captured_discoveries'])
        self.assertEqual([],calls)
        with TaskStore(db, agents_root=root, vault_root=root/'vault') as store:
            self.assertEqual(2,len(store.list_tasks()))
        job.task_id='different-origin'
        mismatch=h.resume_job(job,run_dir,self.env,lambda *args:calls.append(args))
        self.assertEqual('capture_configuration_mismatch',mismatch.get('error'))
        job.task_id=origin['id']
        h.save(run_dir/'0-codex-stdout.jsonl',codex_result(json.dumps({
            'agents_worker_capture':{'discoveries':[{'purpose':'missing event key'}]}})),self.env)
        invalid=h.resume_job(job,run_dir,self.env,lambda *args:calls.append(args))
        self.assertEqual('incomplete',invalid['status'])
        self.assertEqual('incomplete',invalid['capture_status'])
        self.assertEqual([],calls)

    def test_review_command_never_grants_capture_write_directories(self):
        command=h.build_command('codex','review','gpt-5.6-luna','low',add_dirs=['/tmp/write'])
        self.assertNotIn('--add-dir',command)

    def test_elapsed_seconds_is_per_attempt(self):
        import time
        def run(argv, env, cwd, prompt, timeout):
            time.sleep(.02)
            if argv[0] == 'claude':
                return h.ProcessResult(1, claude_result("You've hit your limit", True))
            return h.ProcessResult(0, codex_result(self.review))
        result = h.run_job(self.job, self.env, run)
        attempts = json.loads((Path(result['run_dir'])/'result.json').read_text())['attempts']
        self.assertGreaterEqual(attempts[0]['elapsed_seconds'], .01)
        self.assertGreaterEqual(attempts[1]['elapsed_seconds'], .01)
        self.assertLess(attempts[1]['elapsed_seconds'], attempts[0]['elapsed_seconds'] + .1)

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

    def test_run_directory_lock_prevents_concurrent_launch(self):
        import fcntl
        run_dir=self.root/'vault'/'locked';run_dir.mkdir()
        calls=[]
        with (run_dir/'.runner.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            r=h.run_job(self.job,self.env,lambda *args:calls.append(args),run_dir=run_dir)
        self.assertEqual('running',r['status'])
        self.assertEqual([],calls)

    def test_resume_completed_preserves_terminal_status(self):
        run=h.run_job(self.job,self.env,self.executor([h.ProcessResult(0,claude_result('{"verdict":"approve","findings":[],"limitations":[]}'))]))
        calls=[]
        resumed=h.resume_job(self.job,Path(run['run_dir']),self.env,lambda *args:calls.append(args))
        self.assertEqual('completed',resumed['status'])
        self.assertEqual([],calls)

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

    def test_large_persistent_stdin_is_bounded_by_timeout(self):
        import sys, time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            started = time.monotonic()
            r = h.execute([sys.executable, '-c', 'import time; time.sleep(30)'], dict(os.environ),
                          Path.cwd(), 'x' * (1024 * 1024 * 8), .05,
                          stdout_path=root/'out', stderr_path=root/'err', redaction_env={})
            self.assertTrue(r.timed_out)
            self.assertLess(time.monotonic() - started, 5)

    def test_completed_state_retains_start_identity_after_reap(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            state_path = Path(d) / 'state.json'
            r = h.execute([sys.executable, '-c', 'print("done")'], {}, Path.cwd(), '', 3,
                          state_path=state_path, redaction_env={})
            self.assertEqual(r.code, 0)
            state = json.loads(state_path.read_text())
            self.assertIsNotNone(state.get('identity'))
            self.assertIn('final_identity', state)

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

    def test_stream_redacts_multiline_key_and_split_long_secret(self):
        import sys
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); secret='S'*400
            env=dict(os.environ, API_KEY=secret)
            begin=('-'*5)+'BEGIN PRIVATE KEY'+('-'*5); end=('-'*5)+'END PRIVATE KEY'+('-'*5)
            payload='\n'.join(['prefix',begin,'private-key-body',end,('x'*500)+secret,''])
            script='import sys; sys.stdout.write('+repr(payload)+'); sys.stdout.flush()'
            r=h.execute([sys.executable,'-c',script],env,Path.cwd(),'',3,
                stdout_path=root/'out',stderr_path=root/'err',redaction_env=env)
            self.assertEqual(0,r.code)
            self.assertNotIn('private-key-body',r.stdout)
            self.assertNotIn(secret,r.stdout)

    def test_uninspectable_process_is_unknown_not_terminated(self):
        from unittest.mock import patch
        with patch.object(h.os,'kill',side_effect=PermissionError('not inspectable')):
            result=h.reconcile_process({'pid':123,'identity':{'command':'worker'}})
        self.assertEqual('unknown',result['status'])


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

    def test_terminal_root_provenance_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); vault = root/'vault'; vault.mkdir()
            env = {'AGENTS_ROOT': str(root), 'AGENTS_VAULT_ROOT': str(vault)}
            with unittest.mock.patch.object(h, 'execute', return_value=h.ProcessResult(127, stderr='missing')):
                report = h.doctor(env, {}, False, environment_mode='terminal')
            self.assertEqual(report['roots']['AGENTS_ROOT']['provenance'], 'terminal_environment')

    def test_review_verdict_is_persisted_atomically_with_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); work = root/'work'; vault = root/'vault'; work.mkdir(); vault.mkdir()
            job = h.Job(work, vault, 'Review')
            review = json.dumps({'verdict': 'request_changes', 'findings': [{'issue': 'bug'}], 'limitations': []})
            result = h.run_job(job, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root)},
                               lambda *args: h.ProcessResult(0, claude_result(review)))
            record = json.loads((Path(result['run_dir'])/'result.json').read_text())
            self.assertEqual(record['status'], 'review_findings')
            self.assertEqual(record['attempts'][0]['status'], 'review_findings')

    def test_structurally_corrupt_result_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); work = root/'work'; vault = root/'vault'; work.mkdir(); vault.mkdir()
            run_dir = vault/'existing'; run_dir.mkdir()
            (run_dir/'result.json').write_text('[]')
            job = h.Job(work, vault, 'Review')
            result = h.resume_job(job, run_dir, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root)},
                                  lambda *args: self.fail('provider must not run'))
            self.assertEqual(result['error'], 'corrupt_result_record')
            self.assertEqual((run_dir/'result.json').read_text(), '[]')

    def test_resume_rejects_persisted_workspace_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); work_a = root/'a'; work_b = root/'b'; vault = root/'vault'
            work_a.mkdir(); work_b.mkdir(); vault.mkdir()
            run_dir = vault/'existing'; run_dir.mkdir()
            h.save(run_dir/'result.json', {'run_dir': str(run_dir), 'workspace': str(work_a),
                                           'mode': 'review', 'status': 'running', 'attempts': []}, {})
            job = h.Job(work_b, vault, 'Review')
            result = h.resume_job(job, run_dir, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root)},
                                  lambda *args: self.fail('provider must not run'))
            self.assertEqual(result['error'], 'workspace_mismatch')

    def test_timeout_context_index_is_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); work = root/'work'; vault = root/'vault'; work.mkdir(); vault.mkdir()
            job = h.Job(work, vault, 'Review')
            result = h.run_job(job, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root)},
                               lambda *args: h.ProcessResult(124, timed_out=True))
            index = json.loads((Path(result['run_dir'])/'context-index.json').read_text())
            self.assertEqual(result['status'], 'timeout')
            self.assertTrue(index['complete'])
            self.assertEqual(index['truncation'], 'none')

    def test_resume_reconciles_captured_terminal_output_before_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); work = root/'work'; vault = root/'vault'; work.mkdir(); vault.mkdir()
            run_dir = vault/'existing'; run_dir.mkdir()
            text = json.dumps({'verdict': 'approve', 'findings': [], 'limitations': []})
            h.save(run_dir/'result.json', {'run_dir': str(run_dir), 'workspace': str(work),
                                           'mode': 'review', 'status': 'running',
                                           'attempts': [{'provider': 'claude', 'status': 'running'}]}, {})
            h.save(run_dir/'0-claude-state.json', {'run_dir': str(run_dir), 'attempt': 0,
                                                   'provider': 'claude', 'status': 'running',
                                                   'pid': 99999999, 'identity': {'command': 'dead'}}, {})
            h.save(run_dir/'0-claude-stdout.jsonl', claude_result(text), {})
            h.save(run_dir/'0-claude-stderr.log', '', {})
            job = h.Job(work, vault, 'Review')
            calls = []
            result = h.resume_job(job, run_dir, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root)},
                                  lambda *args: calls.append(args))
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(calls, [])
            record = json.loads((run_dir/'result.json').read_text())
            self.assertTrue(record['attempts'][0]['reconciled_from_output'])

    def test_resume_reconciles_terminal_state_when_result_save_was_interrupted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); work = root/'work'; vault = root/'vault'; work.mkdir(); vault.mkdir()
            run_dir = vault/'existing'; run_dir.mkdir()
            text = json.dumps({'verdict': 'request_changes', 'findings': [{'issue': 'bug'}], 'limitations': []})
            h.save(run_dir/'result.json', {'run_dir': str(run_dir), 'workspace': str(work),
                                           'mode': 'review', 'status': 'running',
                                           'attempts': [{'provider': 'claude', 'status': 'running'}]}, {})
            h.save(run_dir/'0-claude-state.json', {'run_dir': str(run_dir), 'attempt': 0,
                                                   'provider': 'claude', 'status': 'review_findings',
                                                   'pid': 99999999, 'identity': {'command': 'dead'},
                                                   'exit_code': 0}, {})
            h.save(run_dir/'0-claude-stdout.jsonl', claude_result(text), {})
            h.save(run_dir/'0-claude-stderr.log', '', {})
            job = h.Job(work, vault, 'Review')
            calls = []
            result = h.resume_job(job, run_dir, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root)},
                                  lambda *args: calls.append(args))
            self.assertEqual(result['status'], 'review_findings')
            self.assertEqual(calls, [])

    def test_context_index_lists_output_before_provider_start(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); work = root/'work'; vault = root/'vault'; work.mkdir(); vault.mkdir()
            job = h.Job(work, vault, 'Review')
            observed = {}
            def execute(argv, env, cwd, prompt, timeout):
                run_dir = next(vault.rglob('result.json')).parent
                observed['stdout'] = (run_dir/'0-claude-stdout.jsonl').exists()
                observed['stderr'] = (run_dir/'0-claude-stderr.log').exists()
                observed['index'] = [x['path'] for x in json.loads((run_dir/'context-index.json').read_text())['records']]
                return h.ProcessResult(0, claude_result('OK'))
            result = h.run_job(job, {'PATH': os.environ.get('PATH', ''), 'HOME': str(root)}, execute)
            self.assertTrue(observed['stdout'] and observed['stderr'])
            self.assertIn('0-claude-stdout.jsonl', observed['index'])


class ParentRecoveryRegressionTests(unittest.TestCase):
    def test_older_terminal_attempt_cannot_hide_later_live_process(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            summary={'status':'running','mode':'run','attempts':[{'provider':'claude','status':'usage_limit'},{'provider':'codex','status':'running'}]}
            h.save(root/'0-claude-state.json',{'attempt':0,'provider':'claude','status':'usage_limit','exit_code':1},{})
            h.save(root/'0-claude-stdout.jsonl',json.dumps({'type':'result','is_error':True,'result':'usage limit'}),{})
            h.save(root/'1-codex-state.json',{'attempt':1,'provider':'codex','status':'running','pid':os.getpid(),'identity':h.process_identity(os.getpid())},{})
            active=h._reconcile_existing(root,summary,{})
            self.assertEqual(active['status'],'alive')
            self.assertEqual(summary['status'],'running')

    def test_tool_output_model_field_is_not_provider_identity(self):
        output='\n'.join([json.dumps({'type':'item.completed','item':{'type':'command_execution','model':'spoofed'}}),
                            json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'done'}}),
                            json.dumps({'type':'turn.completed','usage':{}})])
        result=h.classify('codex',output,'',0)
        self.assertIsNone(result.actual_model)
        self.assertFalse(result.model_verified)

    def test_slow_output_collector_preserves_success_and_complete_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source='import time;time.sleep(2.2)\n'+h._COLLECTOR_SOURCE
            with patch.object(h,'_COLLECTOR_SOURCE',source):
                result=h.execute([sys.executable,'-c','print("retained output")'],dict(os.environ),root,'',10,
                                 stdout_path=root/'out',stderr_path=root/'err',state_path=root/'state.json')
            self.assertEqual(result.code,0)
            self.assertIn('retained output',result.stdout)

    def test_pending_collector_never_returns_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);work=root/'work';vault=root/'vault';work.mkdir();vault.mkdir()
            job=h.Job(work,vault,'Review')
            result=h.run_job(job,{'PATH':os.environ.get('PATH',''),'HOME':str(root)},
                lambda *args:h.ProcessResult(0,claude_result('{"verdict":"approve","findings":[],"limitations":[]}'),output_pending=True))
            self.assertEqual(result['status'],'incomplete')
            self.assertFalse(json.loads((Path(result['run_dir'])/'context-index.json').read_text())['complete'])

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness import runner as h


def exported(message='DONE', model='swe-2-medium', metrics=None, **step):
    return json.dumps({'type': 'devin_result', 'exit_code': 0, 'trajectory': {
        'schema_version': 'ATIF-v1.7', 'agent': {'name': 'devin', 'model_name': 'SWE-2 Medium'},
        'steps': [{'source': 'agent', 'model_name': model, 'message': message, 'tool_calls': [], **step}],
        'final_metrics': metrics if metrics is not None else {'total_prompt_tokens': 30, 'total_completion_tokens': 5, 'total_cached_tokens': 10}}})


class DevinProviderTests(unittest.TestCase):
    def test_devin_command_selects_adapter_and_explicit_swe2(self):
        c = h.build_command('devin', 'run', 'swe-2-medium', 'low')
        self.assertTrue(c[1].endswith('/harness/devin.py'))
        self.assertEqual(c[c.index('--model') + 1], 'swe-2-medium')
        self.assertNotIn('codex', c)

    def test_devin_review_rejected_without_claiming_readonly_enforcement(self):
        with self.assertRaisesRegex(ValueError, 'review'):
            h.build_command('devin', 'review', 'swe-2-medium', 'low')

    def test_unknown_provider_never_silently_selects_codex(self):
        with self.assertRaises(ValueError):
            h.build_command('typo', 'run', 'swe-2-medium', 'low')

    def test_devin_reports_terminal_response_model_and_usage(self):
        r = h.classify('devin', exported(), '', 0)
        self.assertEqual(r.status, 'completed')
        self.assertEqual(r.text, 'DONE')
        self.assertEqual(r.actual_model, 'swe-2-medium')
        self.assertEqual(r.usage, {'input_tokens': 30, 'output_tokens': 5, 'cached_input_tokens': 10})

    def test_plain_text_and_intermediate_export_are_not_success(self):
        for output in ['DONE', exported(tool_calls=[{'name': 'read'}]), '{}', exported(message='')]:
            self.assertNotEqual(h.classify('devin', output, '', 0).status, 'completed')

    def test_devin_failed_exit_and_missing_metrics_are_honest(self):
        self.assertEqual(h.classify('devin', exported(), 'Unauthorized', 1).status, 'auth_error')
        r = h.classify('devin', exported(metrics={}), '', 0)
        self.assertIsNone(r.usage)
        self.assertIsNone(h.classify('devin', exported(model=None), '', 0).actual_model)

    def test_error_result_keeps_claude_usage(self):
        out = json.dumps({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                          'result': "You've hit your limit", 'usage': {'input_tokens': 9, 'output_tokens': 2}})
        r = h.classify('claude', out, '', 1)
        self.assertEqual(r.status, 'usage_limit')
        self.assertEqual(r.usage, {'input_tokens': 9, 'output_tokens': 2})

    def test_claude_actual_model_can_come_from_model_usage(self):
        out = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                          'result': 'DONE', 'modelUsage': {'claude-sonnet-4-6': {'inputTokens': 3}},
                          'usage': {'input_tokens': 3}})
        self.assertEqual(h.classify('claude', out, '', 0).actual_model, 'claude-sonnet-4-6')

    def test_nonfinite_negative_usage_is_not_counted(self):
        self.assertEqual(h._usage_record({'input_tokens': float('nan'), 'output_tokens': -1})['available'], False)

    def test_job_routes_devin_and_keeps_effort_honest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            job = h.Job(root, root, 'task', mode='run', provider='devin')
            calls = []
            def execute(argv, env, cwd, prompt, timeout):
                calls.append(argv)
                return h.ProcessResult(0, exported())
            result = h.run_job(job, dict(os.environ), executor=execute)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['attempts'][0]['provider'], 'devin')
            self.assertEqual(result['attempts'][0]['requested_model'], 'swe-2-medium')
            self.assertEqual(result['attempts'][0]['requested_effort'], 'medium')
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()

class DevinExecutionBoundaryTests(unittest.TestCase):
    def test_rejected_tool_is_permission_failure_with_observed_usage(self):
        output = exported(tool_calls=[{'function_name': 'write'}], observation={
            'results': [{'content': 'Tool execution was rejected by the user'}]})
        r = h.classify('devin', output, 'warning: rejected a tool call that requires confirmation.', 0)
        self.assertEqual(r.status, 'permission_denied')
        self.assertEqual(r.usage['input_tokens'], 30)

    def test_adapter_passes_prompt_file_and_scoped_write_permission(self):
        from harness import devin
        import io
        import contextlib
        observed = {}
        def native(argv, **kwargs):
            cfg = json.loads(Path(argv[argv.index('--config') + 1]).read_text())
            observed.update(cfg)
            observed['prompt'] = Path(argv[argv.index('--prompt-file') + 1]).read_text()
            self.assertIn('--sandbox', argv)
            self.assertNotIn('dangerous', argv)
            Path(argv[argv.index('--export') + 1]).write_text(json.dumps(json.loads(exported())['trajectory']))
            return type('Process', (), {'returncode': 0})()
        old_umask = os.umask(0o077)
        try:
            with patch.object(devin.subprocess, 'run', side_effect=native), patch.object(devin.sys, 'stdin', io.StringIO('do task')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(devin.main(['--model', 'swe-2-medium']), 0)
        finally:
            os.umask(old_umask)
        self.assertTrue(observed['prompt'].endswith('do task'))
        self.assertIn('write', observed['permissions']['deny'])
        self.assertIn('edit', observed['permissions']['deny'])
        self.assertFalse(observed['subagents_enabled'])

class UsageEntryPointTests(unittest.TestCase):
    def test_claude_known_alias_is_recorded_as_alias_resolution(self):
        observation = h._model_observation('sonnet', 'claude-sonnet-5', True)
        self.assertTrue(observation['model_verified'])
        self.assertFalse(observation['model_mismatch'])
        self.assertEqual(observation['match_type'], 'provider_alias')

    def test_usage_cli_is_readonly_and_uses_no_login_shell(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); runs = root/'01-Projects'/'agent-runs'; runs.mkdir(parents=True)
            with patch.dict(os.environ, {'AGENTS_VAULT_ROOT': d}), patch.object(h, 'terminal_environment', side_effect=AssertionError('login shell is unnecessary')), contextlib.redirect_stdout(io.StringIO()) as out:
                code = h.main(['--env-file', str(root/'missing'), 'usage', '--json'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())['usage_by_provider_model'], [])

class DevinFailureUsageTests(unittest.TestCase):
    def test_nonzero_rejection_and_auth_failure_keep_reported_usage(self):
        output = json.loads(exported())
        output['exit_code'] = 1
        for message, status in [('warning: rejected a tool call that requires confirmation.', 'permission_denied'), ('Unauthorized', 'auth_error')]:
            with self.subTest(status=status):
                result = h.classify('devin', json.dumps(output), message, 1)
                self.assertEqual(result.status, status)
                self.assertEqual(result.usage['input_tokens'], 30)
                self.assertEqual(result.actual_model, 'swe-2-medium')

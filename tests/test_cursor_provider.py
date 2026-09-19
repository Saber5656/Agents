import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness import runner as h


def stream(*events):
    return ''.join(json.dumps(event) + '\n' for event in events)


def success(text='DONE', **extra):
    return {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text, **extra}


class CursorProviderTests(unittest.TestCase):
    def test_command_uses_native_cli_sandbox_and_explicit_model(self):
        argv = h.build_command('cursor', 'run', 'selected-model', 'low')
        self.assertEqual(argv[0], 'cursor-agent')
        self.assertEqual(argv[argv.index('--model') + 1], 'selected-model')
        self.assertEqual(argv[argv.index('--sandbox') + 1], 'enabled')
        self.assertIn('--auto-review', argv)
        self.assertNotIn('--force', argv)
        self.assertNotIn('--approve-mcps', argv)
        self.assertEqual(argv[argv.index('--output-format') + 1], 'stream-json')

    def test_model_is_required_and_readonly_review_is_not_claimed(self):
        for model in (None, '', ' ', '--force'):
            with self.assertRaisesRegex(ValueError, 'model'):
                h.build_command('cursor', 'run', model, 'low')
        with self.assertRaisesRegex(ValueError, 'review'):
            h.build_command('cursor', 'review', 'selected-model', 'low')

    def test_terminal_required_and_usage_not_invented(self):
        output = stream({'type': 'system', 'subtype': 'init', 'model': 'Model Display Name'}, success())
        result = h.classify('cursor', output, '', 0)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.actual_model, 'Model Display Name')
        self.assertIsNone(result.usage)
        self.assertIsNone(h.classify('cursor', stream(success()), '', 0).actual_model)
        for output in ('DONE', stream({'type': 'assistant', 'message': {'content': 'DONE'}}), stream(success(''))):
            self.assertNotEqual(h.classify('cursor', output, '', 0).status, 'completed')

    def test_native_errors_and_tool_output_are_distinguished(self):
        for message, status in [('Authentication required', 'auth_error'), ('rate limit', 'usage_limit'), ('permission denied', 'permission_denied')]:
            self.assertEqual(h.classify('cursor', '', message, 1).status, status)
        output = stream({'type': 'tool_call', 'result': 'rate limit'}, success('Fixed rate limit parser'))
        self.assertEqual(h.classify('cursor', output, '', 0).status, 'completed')
        self.assertNotEqual(h.classify('cursor', stream(success()), 'process failed', 1).status, 'completed')
        self.assertEqual(h.classify('cursor', stream(success(permission_denials=[{'tool_name': 'Shell'}])), '', 0).status, 'permission_denied')

    def test_job_persists_cursor_context_without_provider_fallback_or_effort_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            job = h.Job(root, root, 'request body', mode='run', provider='cursor', cursor_model='selected-model')
            calls = []
            def execute(argv, env, cwd, prompt, timeout):
                calls.append((argv, prompt))
                return h.ProcessResult(1, '', 'rate limit')
            result = h.run_job(job, dict(os.environ), executor=execute)
            self.assertEqual(result['status'], 'usage_limit')
            self.assertEqual(len(calls), 1)
            self.assertIn('request body', calls[0][1])
            self.assertEqual(result['attempts'][0]['provider'], 'cursor')
            self.assertEqual(result['attempts'][0]['requested_model'], 'selected-model')
            self.assertIsNone(result['attempts'][0]['requested_effort'])
            self.assertIsNone(result['configured_limits']['effort'])
            self.assertTrue((Path(result['run_dir'])/'context-index.json').is_file())

    def test_cli_accepts_cursor_and_injects_common_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); prompt = root/'task.md'; prompt.write_text('request body')
            captured = []
            def run(job, env, **kwargs):
                captured.append(job)
                return {'status': 'completed'}
            with patch.dict(os.environ, {'AGENTS_VAULT_ROOT': d}), patch.object(h, 'run_job', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                code = h.main(['--environment', 'current', '--env-file', str(root/'missing'), 'run', '--provider', 'cursor', '--cursor-model', 'selected-model', '--workspace', d, '--prompt-file', str(prompt)])
            self.assertEqual(code, 0)
            self.assertEqual(captured[0].cursor_model, 'selected-model')
            self.assertIn((h.ROOT/'COMMON-AGENTS.md').read_text(), captured[0].prompt)


if __name__ == '__main__':
    unittest.main()

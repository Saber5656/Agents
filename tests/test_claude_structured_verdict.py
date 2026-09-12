import json
from pathlib import Path
import tempfile
import unittest

from harness import runner


class StructuredVerdictTests(unittest.TestCase):
    def test_success_uses_structured_result_instead_of_preamble(self):
        verdict = {'verdict': 'approve', 'findings': [], 'limitations': []}
        stream = json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                             'result': 'Review complete. Returning verdict.', 'structured_output': verdict})
        result = runner.classify('claude', stream, '', 0)
        self.assertEqual(json.loads(result.text), verdict)

    def test_error_never_uses_attached_approval(self):
        stream = json.dumps({'type': 'result', 'subtype': 'error', 'is_error': True,
                             'result': 'Authentication failed',
                             'structured_output': {'verdict': 'approve', 'findings': []}})
        self.assertEqual(runner.classify('claude', stream, '', 1).status, 'auth_error')

    def test_review_job_requests_cli_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / 'vault'; vault.mkdir()
            seen = []
            def execute(argv, *args):
                seen.append(argv)
                return runner.ProcessResult(0, json.dumps({'type': 'result', 'subtype': 'success',
                    'is_error': False, 'result': 'Done.',
                    'structured_output': {'verdict': 'approve', 'findings': [], 'limitations': []}}))
            result = runner.run_job(runner.Job(root, vault, 'review', fallback=False), {}, executor=execute)
            self.assertEqual(result['status'], 'completed')
            schema = json.loads(seen[0][seen[0].index('--json-schema') + 1])
            self.assertIn('verdict', schema['required'])

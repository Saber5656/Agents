import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from harness import runner, service


class RoutingIntegrityTests(unittest.TestCase):
    def test_concurrent_workers_do_not_replace_shared_execute(self):
        original = runner.execute
        both_inside = threading.Barrier(2)
        finish_second = threading.Event()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                vault = root / 'vault'; vault.mkdir()
                spec = {'workspace': str(root), 'agents_root': str(root),
                        'vault_root': str(vault), 'context': 'test', 'model': 'gpt-5.6-luna',
                        'effort': 'low', 'timeout': 10, 'provider': 'claude'}
                def run(job, *args, **kwargs):
                    both_inside.wait(timeout=3)
                    if job.run_dir.name == 'second':
                        finish_second.wait(timeout=3)
                    return {'status': 'completed'}
                with mock.patch.object(runner, 'run_job', side_effect=run), ThreadPoolExecutor(2) as pool:
                    first = pool.submit(service.default_executor, {**spec, 'prompt': 'first', 'run_dir': str(vault / 'first')})
                    second = pool.submit(service.default_executor, {**spec, 'prompt': 'second', 'run_dir': str(vault / 'second')})
                    first.result(timeout=4)
                    finish_second.set()
                    second.result(timeout=4)
                self.assertIs(runner.execute, original)
        finally:
            finish_second.set()
            runner.execute = original

    def test_in_progress_fallback_without_outcome_is_not_cached_as_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '0-claude-outcome.json').write_text(json.dumps({'exit_code': 0, 'output_pending': False, 'provider_status': 'usage_limit'}))
            (root / '0-claude-process-state.json').write_text(json.dumps({'status': 'completed'}))
            (root / '1-codex-process-state.json').write_text(json.dumps({'status': 'running'}))
            self.assertEqual(service.ServiceStore._cached_verification(root), (False, None))

    def test_quota_with_zero_exit_is_not_cached_as_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '0-claude-outcome.json').write_text(json.dumps({'exit_code': 0, 'output_pending': False, 'provider_status': 'usage_limit'}))
            (root / '0-claude-process-state.json').write_text(json.dumps({'status': 'completed'}))
            self.assertEqual(service.ServiceStore._cached_verification(root), (False, None))


if __name__ == '__main__':
    unittest.main()

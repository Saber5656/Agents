import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from harness.service import ServiceStore, default_executor
from harness.tasks import TaskStore


class DevinServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / 'vault'; self.vault.mkdir()
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        p = patch.dict(os.environ, {'AGENTS_ROOT': str(self.root), 'AGENTS_VAULT_ROOT': str(self.vault)}, clear=True)
        p.start(); self.addCleanup(p.stop)
        self.tasks = TaskStore(self.root / 'tasks.sqlite3'); self.addCleanup(self.tasks.close)
        self.service = ServiceStore(self.root / 'service.sqlite3', self.tasks); self.addCleanup(self.service.close)
        self.task = self.tasks.create_task(purpose='SWE2 work', repository='org/repo')

    def test_devin_enrollment_has_swe2_model_and_effort(self):
        job = self.service.enroll(self.task['id'], self.workspace, 'task', 'context', provider='devin')
        self.assertEqual(job['provider'], 'devin')
        self.assertEqual(job['model'], 'swe-2-medium')
        self.assertEqual(job['effort'], 'medium')

    def test_unrequested_devin_models_rejected(self):
        with self.assertRaisesRegex(ValueError, 'SWE-2'):
            self.service.enroll(self.task['id'], self.workspace, 'task', 'context', provider='devin', model='opus')

    def test_executor_keeps_devin_selection(self):
        spec = {'workspace': str(self.workspace), 'vault_root': str(self.vault), 'agents_root': str(self.root),
                'run_dir': str(self.vault/'run'), 'prompt': 'task', 'context': 'context',
                'provider': 'devin', 'model': 'swe-2-high', 'effort': 'high', 'timeout': 60}
        with patch('harness.runner.run_job', return_value={'status': 'completed'}) as run:
            default_executor(spec)
        job = run.call_args.args[0]
        self.assertEqual(job.provider, 'devin')
        self.assertEqual(job.devin_model, 'swe-2-high')
        self.assertFalse(job.fallback)

    def test_devin_auth_check_does_not_require_codex_login(self):
        from harness.service import _provider_login_check
        with patch('harness.service.subprocess.run', return_value=Mock(returncode=0, stdout='Logged in (via Devin).', stderr='')) as native:
            self.assertTrue(_provider_login_check('devin')({}))
        self.assertEqual(native.call_args.args[0], ['devin', 'auth', 'status'])

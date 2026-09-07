import json
import os
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from harness.tasks import TaskStore
from harness.service import (AuthError, ServiceStore, WorkspaceLock,
                             Scheduler, Launchd, load_agents_env)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"; self.vault.mkdir()
        self.workspace = self.root / "workspace"; self.workspace.mkdir()
        self.db = self.root / "service.sqlite3"
        self.env = {"AGENTS_ROOT": str(self.root), "AGENTS_VAULT_ROOT": str(self.vault)}
        self.patcher = mock.patch.dict(os.environ, self.env, clear=False); self.patcher.start()
        self.addCleanup(self.patcher.stop); self.addCleanup(self.tmp.cleanup)
        self.tasks = TaskStore(self.root / "tasks.sqlite3")
        self.task = self.tasks.create_task(purpose="scheduled work", repository="org/repo")
        self.service = ServiceStore(self.db, self.tasks)
        self.addCleanup(self.tasks.close); self.addCleanup(self.service.close)

    def test_enroll_preserves_request_context_and_updates(self):
        job = self.service.enroll(self.task["id"], self.workspace, "original prompt", "context v1")
        self.service.record_update(job["id"], "scope correction", ["vault://runs/update.json"])
        reopened = ServiceStore(self.db, self.tasks); reopened.close()
        detail = self.service.get_job(job["id"])
        self.assertEqual(detail["prompt"], "original prompt")
        self.assertEqual(detail["context"], "context v1")
        self.assertEqual(detail["updates"], [{"message": "scope correction", "evidence_links": ["vault://runs/update.json"]}])

    def test_dependency_requires_verified_complete_task(self):
        dependency = self.tasks.create_task(purpose="dependency", acceptance_evidence=["check"], completion_evidence=["main-sync:run"])
        task = self.tasks.create_task(purpose="dependent", dependencies=[dependency["id"]])
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        self.assertEqual(self.service.run_once(executor=lambda _: {"status": "completed"}), {"status": "blocked", "reason": "dependencies"})
        self.tasks.add_acceptance_evidence(dependency["id"], "check", verified=True)
        current = self.tasks.get_task(dependency["id"])
        self.tasks.update_task(dependency["id"], expected_version=current["version"], execution_status="verified")
        self.assertEqual(self.service.run_once(executor=lambda _: {"status": "completed"})["status"], "needs_verification")

    def test_attempt_timeout_is_per_attempt_and_success_needs_verification(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context", timeout=0.1)
        called = []
        def executor(spec):
            called.append(spec["timeout"])
            return {"status": "completed", "text": "success"}
        result = self.service.run_once(executor=executor)
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(called, [0.1])
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")
        self.assertEqual(len(self.service.list_attempts(job["id"])), 1)
        self.assertEqual(self.service.run_once(executor=executor)["status"], "idle")

    def test_failure_persists_retry_backoff_without_whole_task_max(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context", retry_base=0)
        result = self.service.run_once(executor=lambda _: {"status": "failed", "text": "temporary"})
        self.assertEqual(result["status"], "retry")
        self.assertEqual(self.service.get_job(job["id"])["attempts_count"], 1)
        result = self.service.run_once(executor=lambda _: {"status": "failed", "text": "temporary"})
        self.assertEqual(result["status"], "retry")
        self.assertEqual(self.service.get_job(job["id"])["attempts_count"], 2)

    def test_restart_recovers_running_attempt_as_needs_verification(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next()
        self.assertIsNotNone(claimed)
        claimed["_lock"].release()
        reopened = ServiceStore(self.db, self.tasks)
        detail = reopened.get_job(job["id"])
        self.assertEqual(detail["state"], "needs_verification")
        self.assertEqual(detail["attempts"][0]["status"], "needs_verification")
        reopened.close()

    def test_workspace_process_lock_blocks_second_owner_and_releases_on_exit(self):
        lock1 = WorkspaceLock(self.root / "same", self.root / "locks")
        lock2 = WorkspaceLock(self.root / "same", self.root / "locks")
        self.assertTrue(lock1.acquire())
        self.assertFalse(lock2.acquire(blocking=False))
        lock1.release()
        self.assertTrue(lock2.acquire(blocking=False))
        lock2.release()

    def test_service_subprocess_does_not_duplicate_live_lock(self):
        script = """import sys; from harness.service import WorkspaceLock; l=WorkspaceLock(sys.argv[1],sys.argv[2]); print(l.acquire(blocking=False), flush=True); input()"""
        lockdir = self.root / "locks"; lockdir.mkdir()
        p = subprocess.Popen([sys.executable, "-c", script, str(self.workspace), str(lockdir)], stdin=subprocess.PIPE, text=True, stdout=subprocess.PIPE)
        self.assertEqual(p.stdout.readline().strip(), "True")
        self.assertEqual(WorkspaceLock(self.workspace, lockdir).acquire(blocking=False), False)
        p.communicate("\n", timeout=5)

    def test_idle_loop_waits_on_event_without_busy_spin(self):
        event = threading.Event()
        scheduler = Scheduler(self.service, poll_interval=60, stop_event=event)
        calls = []
        scheduler._wait = lambda timeout: calls.append(timeout) or event.set()
        self.assertEqual(scheduler.run_forever(executor=lambda _: {"status": "completed"}), "stopped")
        self.assertEqual(calls, [60])

    def test_launchd_plist_has_absolute_python_and_no_secret_values(self):
        plist = Launchd(self.root, self.root / "service.sqlite3").generate("com.example.agents")
        self.assertIn(sys.executable, plist)
        self.assertIn("RunAtLoad", plist); self.assertIn("KeepAlive", plist)
        self.assertNotIn("API_KEY", plist); self.assertNotIn("SECRET", plist)
        self.assertNotIn("OPENAI_API_KEY", plist)
        payload = plistlib.loads(plist.encode())
        self.assertEqual(payload["ProgramArguments"][0], sys.executable)
        self.assertLess(payload["ProgramArguments"].index("--db"), payload["ProgramArguments"].index("run"))
        self.assertEqual(payload["EnvironmentVariables"]["AGENTS_ROOT"], str(self.root.resolve()))
        self.assertEqual(payload["EnvironmentVariables"]["AGENTS_VAULT_ROOT"], str(self.vault.resolve()))

    def test_dotenv_loader_rejects_shell_and_auth_guard_rejects_api_route(self):
        path = self.root / ".env"; path.write_text("AGENTS_ROOT=/safe\nSKILLS_ROOT=${AGENTS_ROOT}/skills\nCODEX_MODEL=gpt-5.6-luna\n")
        loaded = load_agents_env(path, {"AGENTS_ROOT": "old"})
        self.assertEqual(loaded["AGENTS_ROOT"], "/safe")
        self.assertEqual(loaded["SKILLS_ROOT"], "/safe/skills")
        path.write_text("CODEX_MODEL=$(touch injected)\n")
        with self.assertRaises(ValueError): load_agents_env(path, {})
        with self.assertRaises(AuthError):
            self.service.auth_guard({"OPENAI_API_KEY": "secret"}, login_check=lambda _: True)


if __name__ == "__main__":
    unittest.main()

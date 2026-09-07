import json
import os
import plistlib
from pathlib import Path
import subprocess
import sqlite3
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

    def test_restart_keeps_live_running_attempt_and_reconciles_dead_attempt(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next()
        self.assertIsNotNone(claimed)
        claimed["_lock"].release()
        reopened = ServiceStore(self.db, self.tasks)
        detail = reopened.get_job(job["id"])
        self.assertEqual(detail["state"], "running")
        with reopened.tx() as conn:
            conn.execute("UPDATE service_attempts SET pid=? WHERE job_id=?", (99999999, job["id"]))
        recovered = reopened.recover_stale_jobs(lambda *_: {"safe_to_resume": True, "reason": "receipt reconciled"})
        self.assertEqual(recovered[0]["state"], "retry")
        self.assertEqual(reopened.get_job(job["id"])["attempts"][0]["status"], "retry")
        reopened.close()

    def test_reconciler_error_leaves_dead_attempt_needing_verification(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        with self.service.tx() as conn:
            conn.execute("UPDATE service_attempts SET pid=? WHERE job_id=?", (99999999, job["id"]))
        recovered = self.service.recover_stale_jobs(lambda *_: (_ for _ in ()).throw(RuntimeError("receipt unavailable")))
        self.assertEqual(recovered[0]["state"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")

    def test_workspace_process_lock_blocks_second_owner_and_releases_on_exit(self):
        lock1 = WorkspaceLock(self.root / "same", self.root / "locks")
        lock2 = WorkspaceLock(self.root / "same", self.root / "locks")
        self.assertTrue(lock1.acquire())
        self.assertFalse(lock2.acquire(blocking=False))
        lock1.release()
        self.assertTrue(lock2.acquire(blocking=False))
        lock2.release()

    def test_workspace_and_global_resource_locks_are_both_exclusive(self):
        lockdir = self.root / "locks"
        same_workspace = WorkspaceLock(self.workspace, lockdir, "r1")
        other_resource = WorkspaceLock(self.workspace, lockdir, "r2")
        other_workspace = WorkspaceLock(self.root / "other", lockdir, "r2")
        self.assertTrue(same_workspace.acquire())
        self.assertFalse(other_resource.acquire(blocking=False))
        same_workspace.release()
        self.assertTrue(other_resource.acquire(blocking=False))
        self.assertFalse(other_workspace.acquire(blocking=False))
        other_resource.release()

    def test_verifier_agent_can_advance_success_to_verified(self):
        task = self.tasks.create_task(purpose="verify me", acceptance_evidence=["accepted"])
        self.tasks.add_acceptance_evidence(task["id"], "accepted", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        scheduler = Scheduler(self.service, verification_executor=lambda spec: {
            "acceptance": True, "merge": True, "main_sync": True, "evidence": "vault://verified"
        })
        result = scheduler.run_once(executor=lambda _: {"status": "completed", "text": "provider success"})
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.service.get_job(job["id"])["state"], "completed")

    def test_verify_requires_merge_and_main_sync_evidence(self):
        task = self.tasks.create_task(purpose="verify evidence", acceptance_evidence=["accepted"])
        self.tasks.add_acceptance_evidence(task["id"], "accepted", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        with self.assertRaises(ValueError):
            self.service.verify(job["id"], "vault://accepted-only")
        completed = self.service.verify(job["id"], "vault://merge;main-sync")
        self.assertEqual(completed["state"], "completed")

    def test_verifier_error_keeps_successful_implementation_needing_verification(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        scheduler = Scheduler(self.service, verification_executor=lambda _: (_ for _ in ()).throw(RuntimeError("agent unavailable")))
        result = scheduler.run_once(executor=lambda _: {"status": "completed", "text": "provider success"})
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["attempts_count"], 1)

    def test_scheduler_reserves_coordinator_slot_and_runs_bounded_pool(self):
        workspace2 = self.root / "workspace-2"; workspace2.mkdir()
        task2 = self.tasks.create_task(purpose="scheduled work 2")
        self.service.enroll(task2["id"], workspace2, "prompt 2", "context 2", resource="r2")
        self.service.enroll(self.task["id"], self.workspace, "prompt 1", "context 1", resource="r1")
        stop = threading.Event(); scheduler = Scheduler(self.service, poll_interval=0.01, stop_event=stop, max_workers=3, coordinator_reserved=1)
        active = 0; maximum = 0; guard = threading.Lock()
        def executor(_):
            nonlocal active, maximum
            with guard:
                active += 1; maximum = max(maximum, active)
            time.sleep(0.03)
            with guard: active -= 1
            stop.set()
            return {"status": "failed", "text": "retry"}
        scheduler.run_forever(executor=executor)
        self.assertEqual(scheduler.worker_capacity, 2)
        self.assertEqual(maximum, 2)

    def test_default_executor_uses_run_mode_explicit_roots_and_stable_run_dir(self):
        from harness.service import default_executor
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root), "vault_root": str(self.vault), "run_dir": str(self.vault / "run"), "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna", "effort": "low", "timeout": 1, "updates": []}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed"}) as run, mock.patch("harness.runner.resume_job") as resume:
            self.assertEqual(default_executor(spec)["status"], "completed")
            job = run.call_args.args[0]
            self.assertEqual(job.mode, "run"); self.assertEqual(job.provider, "codex"); self.assertEqual(job.vault, self.vault.resolve()); self.assertEqual(job.run_dir, Path(spec["run_dir"]))
            resume.assert_not_called()

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

    def test_launchd_install_backs_up_different_plist_and_start_is_idempotent(self):
        target = self.root / "LaunchAgents" / "service.plist"
        target.parent.mkdir(); target.write_text("old plist")
        launchd = Launchd(self.root, self.root / "service.sqlite3", self.vault)
        with mock.patch("harness.service.sys.platform", "darwin"):
            installed = launchd.install("com.example.agents", target)
            self.assertTrue(installed["changed"]); self.assertTrue(Path(installed["backup"]).is_file())
            again = launchd.install("com.example.agents", target)
            self.assertFalse(again["changed"])
            with mock.patch.object(launchd, "status", return_value=mock.Mock(returncode=0)) as status:
                launchd.start("com.example.agents", target)
                status.assert_called_once_with("com.example.agents")

    def test_dotenv_loader_rejects_shell_and_auth_guard_rejects_api_route(self):
        path = self.root / ".env"; path.write_text("AGENTS_ROOT=/safe\nSKILLS_ROOT=${AGENTS_ROOT}/skills\nCODEX_MODEL=gpt-5.6-luna\n")
        loaded = load_agents_env(path, {"AGENTS_ROOT": "old"})
        self.assertEqual(loaded["AGENTS_ROOT"], "/safe")
        self.assertEqual(loaded["SKILLS_ROOT"], "/safe/skills")
        path.write_text("CODEX_MODEL=$(touch injected)\n")
        with self.assertRaises(ValueError): load_agents_env(path, {})
        with self.assertRaises(AuthError):
            self.service.auth_guard({"OPENAI_API_KEY": "secret"}, login_check=lambda _: True)
        with mock.patch("harness.service.subprocess.run", return_value=mock.Mock(returncode=0, stdout="Logged in using API key", stderr="")):
            with self.assertRaises(AuthError): self.service.auth_guard({})
        with mock.patch("harness.service.subprocess.run", return_value=mock.Mock(returncode=0, stdout="Logged in using ChatGPT", stderr="")):
            self.assertTrue(self.service.auth_guard({}))

    def test_existing_db_parent_mode_is_preserved(self):
        dbdir = self.root / "existing"; dbdir.mkdir(); dbdir.chmod(0o755)
        db = dbdir / "service.sqlite3"
        ServiceStore(db, self.tasks).close()
        self.assertEqual(dbdir.stat().st_mode & 0o777, 0o755)
        self.assertEqual(db.stat().st_mode & 0o777, 0o600)

    def test_existing_service_schema_is_migrated_with_stable_run_dir(self):
        db = self.root / "legacy.sqlite3"
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE service_jobs (
              id TEXT PRIMARY KEY, task_id TEXT NOT NULL, workspace TEXT NOT NULL,
              resource TEXT NOT NULL DEFAULT 'default', prompt TEXT NOT NULL,
              context TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL,
              timeout REAL NOT NULL, retry_base REAL NOT NULL, retry_max REAL NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', attempts_count INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE service_updates (
              job_id TEXT NOT NULL, sequence INTEGER NOT NULL, message TEXT NOT NULL,
              evidence_links TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
              PRIMARY KEY(job_id, sequence)
            );
            CREATE TABLE service_attempts (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
              pid INTEGER, status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
              result_json TEXT, error TEXT
            );
        """)
        conn.commit(); conn.close()
        store = ServiceStore(db, self.tasks)
        self.addCleanup(store.close)
        job = store.enroll(self.task["id"], self.workspace, "prompt", "context")
        self.assertTrue(job["run_dir"].endswith(job["id"]))
        self.assertIn("identity", {row["name"] for row in store._conn.execute("PRAGMA table_info(service_attempts)")})


if __name__ == "__main__":
    unittest.main()

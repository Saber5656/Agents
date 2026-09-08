import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from harness.status import build_status
from harness.tasks import TaskStore


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.agents = self.root / "agents"
        self.vault = self.root / "vault"
        self.agents.mkdir()
        self.vault.mkdir()
        self.db = self.agents / ".local" / "tasks.sqlite3"
        self.env = {"AGENTS_ROOT": str(self.agents), "AGENTS_VAULT_ROOT": str(self.vault)}
        self.patcher = mock.patch.dict(os.environ, self.env, clear=False)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def store(self):
        return TaskStore(self.db, agents_root=self.agents, vault_root=self.vault)

    def test_aggregates_lifecycle_evidence_issueization_and_links(self):
        with self.store() as store:
            running = store.create_task(purpose="running", execution_status="running", assignee="worker")
            auth = store.create_task(purpose="auth", execution_status="auth_error")
            quality = store.create_task(purpose="quality", execution_status="review_findings")
            awaiting = store.create_task(purpose="awaiting", execution_status="awaiting_user")
            merged = store.create_task(
                purpose="merged unsynced", execution_status="merged_unsynced",
                acceptance_evidence=["acceptance"], completion_evidence=["receipt"],
                work_unit="wu-1")
            complete = store.create_task(
                purpose="complete", execution_status="completed",
                acceptance_evidence=["acceptance"], completion_evidence=["receipt"],
                work_unit="wu-1")
            for task in (merged, complete):
                store.add_acceptance_evidence(task["id"], "acceptance", verified=True)
            claim = store.claim_issueization(complete["id"], "batch")
            store.link_issue(complete["id"], "org/repo", 8,
                             "https://github.com/org/repo/issues/8",
                             claim_token=claim["claim_token"], verified=True,
                             readback={"repository": "org/repo", "number": 8,
                                       "url": "https://github.com/org/repo/issues/8"})
            store.link_pr(complete["id"], "org/repo", 12,
                          "https://github.com/org/repo/pull/12", title="delivery")
            incomplete = store.create_task(purpose="provider exit zero", execution_status="completed")
            verified = store.create_task(
                purpose="verified", execution_status="verified",
                acceptance_evidence=["acceptance"], completion_evidence=["receipt"])
            store.add_acceptance_evidence(verified["id"], "acceptance", verified=True)
            stranded = store.create_task(purpose="expired issue claim")
            store.claim_issueization(stranded["id"], "batch", lease_seconds=-1)

        before = self.db.read_bytes()
        result = build_status(db_path=self.db, service_db_path=None)

        self.assertTrue(result["read_only"])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual({task["purpose"]: task["lifecycle"] for task in result["tasks"]}, {
            "running": "running", "auth": "auth_failed", "quality": "quality_failed",
            "awaiting": "awaiting_user", "merged unsynced": "merged_unsynced",
            "complete": "complete", "provider exit zero": "awaiting_verification",
            "verified": "verified",
            "expired issue claim": "planned",
        })
        complete_view = next(task for task in result["tasks"] if task["purpose"] == "complete")
        self.assertEqual(complete_view["issueization"]["state"], "issued")
        self.assertEqual(complete_view["stages"]["acceptance"], "verified")
        self.assertEqual(complete_view["stages"]["publication"], "issued")
        self.assertEqual(complete_view["work_units"], ["wu-1"])
        self.assertEqual(complete_view["prs"][0]["url"], "https://github.com/org/repo/pull/12")
        stranded_view = next(task for task in result["tasks"] if task["purpose"] == "expired issue claim")
        self.assertEqual(stranded_view["issueization"]["state"], "reconcile_needed")
        self.assertFalse(result["complete"])
        self.assertEqual(result["diagnostics"]["drift"]["state"], "unknown")
        self.assertTrue(any("acceptance" in item["action"] for item in result["next_actions"]))

    def test_service_jobs_show_retry_hold_usage_and_errors_without_writes(self):
        service_db = self.root / ".local" / "service.sqlite3"
        service_db.parent.mkdir()
        conn = sqlite3.connect(service_db)
        conn.executescript("""
        CREATE TABLE service_jobs (
          id TEXT PRIMARY KEY, task_id TEXT, workspace TEXT, run_dir TEXT,
          resource TEXT, prompt TEXT, context TEXT, model TEXT, effort TEXT,
          timeout REAL, retry_base REAL, retry_max REAL, state TEXT,
          attempts_count INTEGER, next_attempt_at TEXT, last_error TEXT,
          created_at TEXT, updated_at TEXT);
        CREATE TABLE service_updates (
          job_id TEXT, sequence INTEGER, message TEXT, evidence_links TEXT,
          created_at TEXT, PRIMARY KEY(job_id, sequence));
        CREATE TABLE service_attempts (
          id TEXT PRIMARY KEY, job_id TEXT, attempt_number INTEGER, pid INTEGER,
          identity TEXT, run_dir TEXT, status TEXT, started_at TEXT,
          ended_at TEXT, result_json TEXT, error TEXT);
        """)
        conn.execute("INSERT INTO service_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("job-retry", "task-x", str(self.root / "work"), str(self.root / "run"),
                      "resource-a", "prompt", "context", "gpt-5.6-luna", "low", 10, 1, 60,
                      "retry", 2, "2099-01-01T00:00:00+00:00", "provider timeout",
                      "2026-01-01", "2026-01-01"))
        conn.execute("INSERT INTO service_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("job-hold", "task-y", str(self.root / "work"), str(self.root / "run2"),
                      "resource-b", "prompt", "context", "gpt-5.6-luna", "low", 10, 1, 60,
                      "held", 1, None, "extra-billing security hold", "2026-01-01", "2026-01-01"))
        conn.execute("INSERT INTO service_updates VALUES (?,?,?,?,?)",
                     ("job-retry", 1, "retrying", json.dumps(["vault://retry"]), "2026-01-01"))
        conn.execute("INSERT INTO service_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("attempt-1", "job-retry", 1, None, None, str(self.root / "run"), "failed",
                      "2026-01-01", "2026-01-01", json.dumps({"usage": {"input_tokens": 7, "output_tokens": 3}}),
                      "provider timeout"))
        conn.commit()
        before = service_db.read_bytes()
        conn.close()

        result = build_status(db_path=None, service_db_path=service_db)

        self.assertEqual(result["service"]["state"], "available")
        self.assertEqual(len(result["service"]["retries"]), 1)
        self.assertEqual(len(result["service"]["holds"]), 1)
        self.assertEqual(result["service"]["usage"]["input_tokens"], 7)
        retry_job = next(item for item in result["service"]["jobs"] if item["id"] == "job-retry")
        self.assertEqual(retry_job["updates"][0]["evidence_links"], ["vault://retry"])
        self.assertEqual(service_db.read_bytes(), before)

    def test_missing_and_corrupt_sources_are_uncertain_without_initialization(self):
        missing = self.root / "missing.sqlite3"
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not sqlite")
        before = corrupt.read_bytes()

        result = build_status(db_path=missing, service_db_path=corrupt)

        self.assertEqual(result["sources"]["tasks"]["state"], "missing")
        self.assertEqual(result["sources"]["service"]["state"], "corrupt")
        self.assertTrue(result["uncertain"])
        self.assertFalse(missing.exists())
        self.assertEqual(corrupt.read_bytes(), before)

    def test_cli_status_uses_current_environment_without_login_shell(self):
        from harness import runner
        env_file = self.root / ".env"
        env_file.write_text("AGENTS_ROOT=%s\nAGENTS_VAULT_ROOT=%s\n" % (self.agents, self.vault))
        with mock.patch.object(runner, "terminal_environment", side_effect=AssertionError("login shell")):
            with mock.patch("sys.stdout") as output:
                self.assertEqual(runner.main([
                    "--env-file", str(env_file), "status", "--db", str(self.db), "--json",
                ]), 0)
        rendered = "".join(call.args[0] for call in output.write.call_args_list if call.args)
        self.assertIn('"read_only": true', rendered)

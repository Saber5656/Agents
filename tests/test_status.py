import json
import ast
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
            "complete": "awaiting_verification", "provider exit zero": "awaiting_verification",
            "verified": "awaiting_verification",
            "expired issue claim": "planned",
        })
        complete_view = next(task for task in result["tasks"] if task["purpose"] == "complete")
        self.assertEqual(complete_view["issueization"]["state"], "issued")
        self.assertEqual(complete_view["stages"]["acceptance"], "verified")
        self.assertEqual(complete_view["stages"]["publication"], "unknown")
        self.assertEqual(complete_view["work_units"], ["wu-1"])
        self.assertEqual(complete_view["prs"][0]["url"], "https://github.com/org/repo/pull/12")
        stranded_view = next(task for task in result["tasks"] if task["purpose"] == "expired issue claim")
        self.assertEqual(stranded_view["issueization"]["state"], "reconcile_needed")
        self.assertFalse(result["complete"])
        self.assertEqual(result["diagnostics"]["drift"]["state"], "unknown")
        self.assertTrue(any("acceptance" in item["action"] for item in result["next_actions"]))

    def test_issueization_does_not_imply_publication_or_preempt_planned_work(self):
        with self.store() as store:
            task = store.create_task(purpose="still needs implementation", repository="org/repo")
            claim = store.claim_issueization(task["id"], "batch")
            store.link_issue(task["id"], "org/repo", 21,
                             "https://github.com/org/repo/issues/21",
                             claim_token=claim["claim_token"], verified=True,
                             readback={"repository": "org/repo", "number": 21,
                                       "url": "https://github.com/org/repo/issues/21"})

        task_view = build_status(db_path=self.db, service_db_path=None)["tasks"][0]
        self.assertEqual(task_view["issueization"]["state"], "issued")
        self.assertEqual(task_view["stages"]["issueization"], "issued")
        self.assertEqual(task_view["stages"]["publication"], "unknown")
        self.assertEqual(task_view["next_action"], "start or resume the planned task before issueization")

    def test_malformed_or_naive_claim_expiry_is_unknown(self):
        with self.store() as store:
            malformed = store.create_task(purpose="malformed claim")
            naive = store.create_task(purpose="naive claim")
            store.claim_issueization(malformed["id"], "batch")
            store.claim_issueization(naive["id"], "batch")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE tasks SET claim_expires_at=? WHERE purpose=?",
                         ("not-a-time", "malformed claim"))
            conn.execute("UPDATE tasks SET claim_expires_at=? WHERE purpose=?",
                         ("2026-01-01T00:00:00", "naive claim"))
            conn.commit()

        result = build_status(db_path=self.db, service_db_path=None)
        views = {task["purpose"]: task for task in result["tasks"]}
        for purpose in ("malformed claim", "naive claim"):
            self.assertEqual(views[purpose]["issueization"]["state"], "unknown")
            self.assertEqual(views[purpose]["issueization"]["timestamp_state"], "unknown")
            self.assertIn("inspect", views[purpose]["next_action"])

    def test_explicit_publication_receipt_drives_publication_stage(self):
        receipt = self.root / "vault" / "publication-receipt.json"
        commit = "a" * 40
        receipt.write_text(json.dumps({
            "review": {"acceptance": True, "criteria": [{"evidence": "acceptance"}]},
            "publication_readback": {
                "repository": "org/repo", "commit": commit, "main": commit,
                "commit_is_ancestor": True, "merge_base": commit,
            },
        }))
        with self.store() as store:
            task = store.create_task(
                purpose="published with receipt", repository="org/repo",
                execution_status="completed", acceptance_evidence=["acceptance"],
                completion_evidence=[str(receipt)])
            store.add_acceptance_evidence(task["id"], "acceptance", verified=True)
            claim = store.claim_issueization(task["id"], "batch")
            store.link_issue(task["id"], "org/repo", 22,
                             "https://github.com/org/repo/issues/22",
                             claim_token=claim["claim_token"], verified=True,
                             readback={"repository": "org/repo", "number": 22,
                                       "url": "https://github.com/org/repo/issues/22"})

        task_view = build_status(db_path=self.db, service_db_path=None)["tasks"][0]
        self.assertEqual(task_view["stages"]["issueization"], "issued")
        self.assertEqual(task_view["stages"]["publication"], "main_synced")
        self.assertEqual(task_view["publication_evidence"]["receipt"], str(receipt))

    def test_publication_receipt_states_distinguish_merged_and_pushed(self):
        merged_receipt = self.root / "vault" / "merged-receipt.json"
        commit = "b" * 40
        merged_receipt.write_text(json.dumps({
            "publication_readback": {
                "repository": "org/repo", "commit": commit, "main": "c" * 40,
                "commit_is_ancestor": True, "merge_base": commit,
            },
        }))
        pushed_receipt = self.root / "vault" / "pushed-receipt.json"
        pushed_receipt.write_text(json.dumps({
            "stage": "pushed", "status": "published", "published_sha": "d" * 40,
        }))
        with self.store() as store:
            merged = store.create_task(
                purpose="merged with receipt", repository="org/repo", execution_status="completed",
                acceptance_evidence=["acceptance"], completion_evidence=[str(merged_receipt)])
            pushed = store.create_task(
                purpose="pushed with receipt", repository="org/repo", execution_status="completed",
                acceptance_evidence=["acceptance"], completion_evidence=[str(pushed_receipt)])
            for task in (merged, pushed):
                store.add_acceptance_evidence(task["id"], "acceptance", verified=True)

        views = {task["purpose"]: task for task in build_status(
            db_path=self.db, service_db_path=None)["tasks"]}
        self.assertEqual(views["merged with receipt"]["stages"]["publication"], "merged")
        self.assertEqual(views["pushed with receipt"]["stages"]["publication"], "published")

    def test_reads_current_service_schema_read_only(self):
        service_source = Path(__file__).parents[2] / "agents-service" / "harness" / "service.py"
        tree = ast.parse(service_source.read_text())
        schema_node = next(node.value for node in tree.body
                           if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == "SCHEMA"
                                   for target in node.targets))
        schema = ast.literal_eval(schema_node)
        service_db = self.root / ".local" / "service-current.sqlite3"
        service_db.parent.mkdir()
        with sqlite3.connect(service_db) as conn:
            conn.executescript(schema)
            conn.execute("INSERT INTO service_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("job-current", "task-x", str(self.root / "work"), str(self.root / "run"),
                          "default", "prompt", "context", "gpt-5.6-luna", "low", 10, 1, 60,
                          "pending", 0, None, None, "2026-01-01", "2026-01-01"))
            conn.commit()
        before = service_db.read_bytes()
        report = build_status(db_path=None, service_db_path=service_db)
        self.assertEqual(report["service"]["state"], "available")
        self.assertEqual(report["service"]["jobs"][0]["id"], "job-current")
        self.assertEqual(service_db.read_bytes(), before)

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

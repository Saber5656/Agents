import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import stat

from harness.tasks import ConfigurationError, ConflictError, IssueizationError, TaskStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.db = self.root / "tasks.sqlite3"
        self.env = {"AGENTS_ROOT": str(self.root), "AGENTS_VAULT_ROOT": str(self.vault)}
        self.patcher = mock.patch.dict(os.environ, self.env, clear=False)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def store(self):
        return TaskStore(self.db)

    def test_roots_are_required_and_no_vault_is_guessed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                TaskStore(self.db)

    def test_restart_recovers_provenance_evidence_and_independent_issue_state(self):
        store = self.store()
        parent = store.create_task(
            purpose="assigned work", source="request:one", repository="org/repo",
            assignee="worker", priority="P0", evidence_links=["vault://run/one"],
            acceptance_evidence=["test:assigned"], execution_status="assigned",
            work_unit="wu-1")
        first = store.record_discovery(
            originating_task=parent["id"], discovery_key="event-1",
            purpose="follow-up one", expected_result="observable result",
            repository="org/repo", evidence_links=["vault://evidence/a"])
        second = store.record_discovery(
            originating_task=parent["id"], discovery_key="event-2",
            purpose="follow-up two", expected_result="second result",
            repository="org/repo", evidence_links=["vault://evidence/b"])
        store.close()
        restarted = self.store()
        self.assertEqual(restarted.get_task(parent["id"])["evidence_links"], ["vault://run/one"])
        self.assertEqual(restarted.get_task(first["id"])["source_task_id"], parent["id"])
        self.assertEqual(restarted.get_task(second["id"])["issueization_state"], "unissued")
        changed = restarted.update_task(parent["id"], expected_version=parent["version"], execution_status="verified")
        linked = restarted.link_issue(parent["id"], "org/repo", 123, "https://github.com/org/repo/issues/123", verified=True)
        self.assertEqual(changed["execution_status"], "verified")
        self.assertEqual(linked["issueization_state"], "issued")
        self.assertEqual(linked["execution_status"], "verified")

    def test_discovery_retry_is_idempotent_and_merges_new_evidence(self):
        store = self.store()
        origin = store.create_task(purpose="origin")
        first = store.record_discovery(originating_task=origin["id"], discovery_key="same-event",
                                       purpose="improvement", expected_result="done",
                                       evidence_links=["vault://old"])
        second = store.record_discovery(originating_task=origin["id"], discovery_key="same-event",
                                        purpose="improvement", expected_result="done",
                                        evidence_links=["vault://old", "vault://new"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["evidence_links"], ["vault://old", "vault://new"])
        self.assertIn(first["id"], {task["id"] for task in store.list_tasks(issueization_state="unissued")})

    def test_optimistic_concurrency_rejects_stale_write(self):
        store = self.store()
        task = store.create_task(purpose="race")
        newer = store.update_task(task["id"], expected_version=0, execution_status="running")
        self.assertEqual(newer["version"], 1)
        with self.assertRaises(ConflictError):
            store.update_task(task["id"], expected_version=0, execution_status="failed")
        self.assertEqual(store.get_task(task["id"])["execution_status"], "running")

    def test_concurrent_creates_and_one_interrupted_write_keep_acknowledged_rows(self):
        store = self.store()
        errors = []
        results = []

        def create(number):
            try:
                results.append(store.create_task(purpose=f"parallel-{number}"))
            except Exception as exc:  # pragma: no cover - diagnostic for a race
                errors.append(exc)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(len(store.list_tasks()), 12)

        with self.assertRaises(RuntimeError):
            store.create_task(purpose="rollback", _before_commit=lambda conn: (_ for _ in ()).throw(RuntimeError("stop")))
        self.assertEqual(len(store.list_tasks()), 12)

    def test_claim_ambiguous_retry_and_verified_issue_link_are_durable(self):
        store = self.store()
        task = store.create_task(purpose="issue me")
        claim = store.claim_issueization(task["id"], "batch-a")
        self.assertEqual(claim["issueization_state"], "claimed")
        ambiguous = store.mark_issueization_ambiguous(task["id"], claim["claim_token"], "remote timeout")
        self.assertEqual(ambiguous["issueization_state"], "ambiguous")
        retried = store.retry_issueization(task["id"], expected_version=ambiguous["version"])
        self.assertEqual(retried["issueization_state"], "retry")
        claim2 = store.claim_issueization(task["id"], "batch-b")
        issued = store.link_issue(task["id"], "org/repo", 9, "https://github.com/org/repo/issues/9", claim_token=claim2["claim_token"], verified=True)
        self.assertEqual(issued["issueization_state"], "issued")
        self.assertEqual(len(store.list_issue_links(task["id"])), 1)
        self.assertEqual(store.get_task(task["id"])["issueization_state"], "issued")

    def test_many_to_many_work_unit_issue_pr_and_acceptance_evidence(self):
        store = self.store()
        one = store.create_task(purpose="one", acceptance_evidence=["test:one"], completion_evidence=["run:one"])
        two = store.create_task(purpose="two", acceptance_evidence=["test:two"], completion_evidence=["run:two"])
        store.link_issue(one["id"], "org/repo", 1, "https://github.com/org/repo/issues/1", verified=True)
        store.link_issue(two["id"], "org/repo", 2, "https://github.com/org/repo/issues/2", verified=True)
        store.link_pr(one["id"], "org/repo", 10, "https://github.com/org/repo/pull/10")
        store.link_pr(two["id"], "org/repo", 10, "https://github.com/org/repo/pull/10")
        store.link_work_unit("wu", task_ids=[one["id"], two["id"]], issue_ids=[("org/repo", 1), ("org/repo", 2)], pr_ids=[("org/repo", 10)])
        detail = store.get_work_unit("wu")
        self.assertEqual({x["id"] for x in detail["tasks"]}, {one["id"], two["id"]})
        self.assertEqual(len(detail["issues"]), 2)
        self.assertEqual(len(detail["prs"]), 1)
        self.assertEqual(store.get_task(one["id"])["acceptance_evidence"], ["test:one"])
        self.assertEqual(store.get_task(one["id"])["completion_evidence"], ["run:one"])

    def test_completion_report_requires_unit_and_acceptance_evidence(self):
        store = self.store()
        requirement = store.create_requirement("ship parser", acceptance=["parser test"])
        task = store.create_task(purpose="parser", acceptance_evidence=["parser test"], completion_evidence=["run:parser"])
        store.link_requirement_task(requirement["id"], task["id"])
        self.assertFalse(store.completion_report()["complete"])
        current = store.get_task(task["id"])
        store.update_task(task["id"], expected_version=current["version"], execution_status="completed")
        store.add_acceptance_evidence(task["id"], "parser test", verified=True)
        self.assertTrue(store.completion_report()["complete"])

    def test_completion_report_requires_verified_acceptance_and_exposes_records(self):
        store = self.store()
        task = store.create_task(purpose="verify", acceptance_evidence=["check"] , completion_evidence=["run"])
        current = store.get_task(task["id"])
        store.update_task(task["id"], expected_version=current["version"], execution_status="completed")
        detail = store.get_task(task["id"])
        self.assertEqual(detail["acceptance_records"], [{"evidence": "check", "verified": False}])
        self.assertFalse(store.completion_report()["complete"])
        detail = store.add_acceptance_evidence(task["id"], "check", verified=True)
        self.assertEqual(detail["acceptance_records"], [{"evidence": "check", "verified": True}])
        self.assertTrue(store.completion_report()["complete"])

    def test_expired_claim_requires_reconciliation_before_new_claim(self):
        store = self.store()
        task = store.create_task(purpose="remote race")
        claim = store.claim_issueization(task["id"], "batch-a", lease_seconds=-1)
        candidates = store.list_issueization_candidates()
        candidate = next(x for x in candidates if x["id"] == task["id"])
        self.assertTrue(candidate["reconciliation_required"])
        with self.assertRaises(IssueizationError):
            store.claim_issueization(task["id"], "batch-b")
        store.reconcile_expired_claim(task["id"])
        self.assertEqual(store.get_task(task["id"])["issueization_state"], "ambiguous")

    def test_discovery_retry_increments_version_and_retains_expected_result_history(self):
        store = self.store()
        origin = store.create_task(purpose="origin")
        first = store.record_discovery(originating_task=origin["id"], discovery_key="event",
                                       purpose="follow-up", expected_result="old", evidence_links=["vault://a"])
        second = store.record_discovery(originating_task=origin["id"], discovery_key="event",
                                        purpose="follow-up", expected_result="corrected", evidence_links=["vault://b"])
        self.assertEqual(second["version"], first["version"] + 1)
        self.assertEqual(second["expected_result_history"], ["old", "corrected"])
        with self.assertRaises(ConflictError):
            store.update_task(second["id"], expected_version=first["version"], purpose="stale")

    def test_issue_link_requires_verified_exact_github_readback(self):
        store = self.store()
        task = store.create_task(purpose="link")
        with self.assertRaises(ValueError):
            store.link_issue(task["id"], "org/repo", 3, "https://example.test/issues/3", verified=True)
        with self.assertRaises(ValueError):
            store.link_issue(task["id"], "org/repo", 3, "https://github.com/other/repo/issues/3", verified=True)
        linked = store.link_issue(task["id"], "org/repo", 3, "https://github.com/org/repo/issues/3", verified=True,
                                  readback={"repository": "org/repo", "issue_id": 3, "url": "https://github.com/org/repo/issues/3"})
        self.assertEqual(linked["issueization_state"], "issued")

    def test_explicit_database_directory_and_sidecars_are_private(self):
        db_dir = self.root / "permissive"; db_dir.mkdir(mode=0o755); db_dir.chmod(0o755)
        db_path = db_dir / "tasks.sqlite3"
        store = TaskStore(db_path)
        store.create_task(purpose="private")
        store.close()
        self.assertEqual(stat.S_IMODE(db_dir.stat().st_mode), 0o700)
        for path in db_dir.glob("tasks.sqlite3*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)

    def test_requirements_remain_after_task_completion_and_scope_correction(self):
        store = self.store()
        reqs = [store.create_requirement(f"requirement {i}", acceptance=[f"check:{i}"]) for i in range(3)]
        one = store.create_task(purpose="first")
        store.link_requirement_task(reqs[0]["id"], one["id"])
        store.add_requirement_revision(reqs[1]["id"], "scope correction")
        store.update_task(one["id"], expected_version=0, execution_status="completed")
        self.assertEqual(len(store.list_requirements()), 3)
        self.assertEqual(store.get_requirement(reqs[1]["id"])["revisions"], ["scope correction"])

    def test_import_existing_backlog_links_without_creating_remote_issues(self):
        store = self.store()
        path = self.root / "issues.json"
        path.write_text(json.dumps([{"number": 77, "title": "Existing", "body": "accept", "repository": "org/repo"}]))
        imported = store.import_backlog(path, default_repository="fallback/repo")
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["issueization_state"], "issued")
        self.assertEqual(store.list_issue_links(imported[0]["id"])[0]["issue_id"], 77)
        self.assertEqual(len(store.list_tasks()), 1)


class CliTests(unittest.TestCase):
    def test_cli_list_json_and_detail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); vault = root / "vault"; vault.mkdir(); db = root / "tasks.sqlite3"
            env = dict(os.environ, AGENTS_ROOT=str(root), AGENTS_VAULT_ROOT=str(vault))
            create = subprocess.run([sys.executable, "-m", "harness.tasks", "create", "--db", str(db), "--purpose", "cli task", "--json"], env=env, text=True, capture_output=True)
            self.assertEqual(create.returncode, 0, create.stderr)
            task = json.loads(create.stdout)
            listed = subprocess.run([sys.executable, "-m", "harness.tasks", "list", "--db", str(db), "--json"], env=env, text=True, capture_output=True)
            self.assertEqual(json.loads(listed.stdout)[0]["id"], task["id"])
            shown = subprocess.run([sys.executable, "-m", "harness.tasks", "show", task["id"], "--db", str(db), "--json"], env=env, text=True, capture_output=True)
            self.assertEqual(json.loads(shown.stdout)["purpose"], "cli task")


if __name__ == "__main__":
    unittest.main()

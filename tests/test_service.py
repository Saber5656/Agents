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
                             Scheduler, Launchd, default_verifier, load_agents_env)


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

    def test_dependency_accepts_structured_service_receipt(self):
        dependency = self.tasks.create_task(
            purpose="receipt dependency", repository="Saber5656/Agents",
            acceptance_evidence=["check"],
        )
        self.tasks.add_acceptance_evidence(dependency["id"], "check", verified=True)
        receipt = self.vault / "acceptance.json"
        receipt.write_text(json.dumps({
            "review": {"acceptance": True, "findings": [], "criteria": [
                {"criterion": "check", "verified": True, "evidence": "test log"}
            ]},
            "publication_readback": {
                "repository": "Saber5656/Agents", "commit": "a" * 40,
                "main": "a" * 40, "mode": "direct_main"
            },
            "task_version": 3, "observed_at": "2026-09-08T00:00:00+00:00",
        }))
        current = self.tasks.get_task(dependency["id"])
        self.tasks.update_task(dependency["id"], expected_version=current["version"],
                               execution_status="verified")
        self.tasks.add_completion_evidence(dependency["id"], str(receipt))
        task = self.tasks.create_task(purpose="receipt dependent", dependencies=[dependency["id"]])
        self.service.enroll(task["id"], self.workspace, "prompt", "context")
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

    def test_attempts_use_distinct_run_dirs_and_latest_updates(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context", retry_base=0)
        seen = []
        self.service.run_once(executor=lambda spec: (seen.append(spec) or {"status": "failed", "text": "retry"}))
        self.service.record_update(job["id"], "latest correction", ["vault://latest"])
        self.service.run_once(executor=lambda spec: (seen.append(spec) or {"status": "failed", "text": "retry"}))
        self.assertNotEqual(seen[0]["run_dir"], seen[1]["run_dir"])
        self.assertEqual(seen[1]["updates"][-1]["message"], "latest correction")

    def test_auth_failure_finishes_claim_with_persisted_retry(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context", retry_base=0)
        with mock.patch.object(self.service, "auth_guard", side_effect=AuthError("not logged in")):
            result = self.service.run_once()
        self.assertEqual(result["status"], "retry")
        detail = self.service.get_job(job["id"])
        self.assertEqual(detail["state"], "retry")
        self.assertEqual(detail["attempts"][0]["status"], "retry")
        self.assertIn("not logged in", detail["last_error"])

    def test_task_store_failure_after_claim_is_persisted_retry_and_releases_lock(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context", retry_base=0)
        with mock.patch.object(self.tasks, "update_task", side_effect=RuntimeError("task db unavailable")):
            result = self.service.run_once(executor=lambda _: {"status": "completed"})
        self.assertEqual(result["status"], "retry")
        self.assertEqual(self.service.get_job(job["id"])["state"], "retry")
        lock = WorkspaceLock(self.workspace, self.root / ".local" / "service-locks")
        self.assertTrue(lock.acquire(blocking=False)); lock.release()

    def test_safe_reconciliation_resumes_same_attempt_directory(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        with self.service.tx() as conn:
            conn.execute("UPDATE service_attempts SET pid=?,identity=? WHERE job_id=?",
                         (99999999, json.dumps({"pid": 99999999}), job["id"]))
        self.assertEqual(self.service.recover_stale_jobs(
            lambda *_: {"safe_to_resume": True, "reason": "receipt is resumable"}),
            [{"job_id": job["id"], "state": "retry"}])
        seen = []
        self.service.run_once(executor=lambda spec: (seen.append(spec) or {"status": "failed"}))
        self.assertEqual(seen[0]["attempt"], 1)
        self.assertTrue(seen[0]["run_dir"].endswith("attempt-1"))
        self.assertEqual(len(self.service.list_attempts(job["id"])), 1)

    def test_default_resource_allows_parallel_workspaces(self):
        lockdir = self.root / "locks"
        first = WorkspaceLock(self.workspace, lockdir)
        second = WorkspaceLock(self.root / "other", lockdir)
        self.assertTrue(first.acquire(blocking=False))
        self.assertTrue(second.acquire(blocking=False))
        first.release(); second.release()

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

    def test_recovery_detects_surviving_provider_in_dead_worker_attempt(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        run_dir = Path(claimed["attempt_run_dir"]); run_dir.mkdir(parents=True)
        identity = self.service._process_identity(os.getpid())
        (run_dir / "0-codex-state.json").write_text(json.dumps({
            "status": "running", "pid": os.getpid(), "identity": identity}))
        with self.service.tx() as conn:
            conn.execute("UPDATE service_attempts SET pid=?, identity=? WHERE job_id=?", (99999999, json.dumps({"pid": 99999999}), job["id"]))
        self.assertEqual(self.service.recover_stale_jobs(lambda *_: {"safe_to_resume": True}), [])
        self.assertEqual(self.service.get_job(job["id"])["state"], "running")

    def test_recovery_rechecks_ownership_after_lock(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        states = iter(("dead", "alive"))
        with mock.patch.object(self.service, "_attempt_state", side_effect=lambda _: next(states)):
            recovered = self.service.recover_stale_jobs(lambda *_: {"safe_to_resume": True})
        self.assertEqual(recovered, [])
        self.assertEqual(self.service.get_job(job["id"])["state"], "running")

    def test_malformed_process_record_is_unknown_and_does_not_stop_other_recovery(self):
        other = self.tasks.create_task(purpose="other stale")
        other_workspace = self.root / "other-workspace"; other_workspace.mkdir()
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        other_job = self.service.enroll(other["id"], other_workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        claimed_other = self.service._claim_next(); claimed_other["_lock"].release()
        bad_dir = Path(claimed["attempt_run_dir"]); bad_dir.mkdir(parents=True)
        (bad_dir / "0-codex-state.json").write_text(json.dumps({"status": "running", "pid": "abc"}))
        with self.service.tx() as conn:
            conn.execute("UPDATE service_attempts SET pid=? WHERE id IN (?,?)",
                         (99999999, claimed["attempt_id"], claimed_other["attempt_id"]))
        recovered = self.service.recover_stale_jobs(lambda *_: {"safe_to_resume": True})
        self.assertEqual(recovered, [{"job_id": other_job["id"], "state": "retry"}])
        self.assertEqual(self.service.get_job(job["id"])["state"], "running")

    def test_reconciling_job_is_recovered_after_service_interruption(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        with self.service.tx() as conn:
            conn.execute("UPDATE service_jobs SET state='reconciling' WHERE id=?", (job["id"],))
            conn.execute("UPDATE service_attempts SET status='reconciling',pid=? WHERE job_id=?", (99999999, job["id"]))
        recovered = self.service.recover_stale_jobs(lambda *_: {"safe_to_resume": True, "reason": "receipt ok"})
        self.assertEqual(recovered[0]["state"], "retry")

    def test_verification_interruption_returns_to_verification_queue(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        self.service.run_once(executor=lambda _: {"status": "completed"})
        self.service._start_verification(job["id"])
        self.assertEqual(self.service.get_job(job["id"])["state"], "verifying")
        with self.service.tx() as conn:
            conn.execute("UPDATE service_jobs SET verification_pid=? WHERE id=?", (99999999, job["id"]))
        self.assertEqual(self.service.recover_interrupted_verification(), [job["id"]])
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")

    def test_other_scheduler_does_not_reclaim_live_verification(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        self.service.run_once(executor=lambda _: {"status": "completed"})
        self.assertTrue(self.service._start_verification(job["id"]))
        reopened = ServiceStore(self.db, self.tasks)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.recover_interrupted_verification(), [])
        self.assertEqual(reopened.get_job(job["id"])["state"], "verifying")

    def test_verification_recovery_waits_for_surviving_cli(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        self.service.run_once(executor=lambda _: {"status": "completed"})
        self.service._start_verification(job["id"])
        record = Path(job["run_dir"]) / "verification-fixture"
        record.mkdir(parents=True)
        (record / "process-state.json").write_text(json.dumps({"status": "running", "pid": os.getpid(),
            "identity": self.service._process_identity(os.getpid())}))
        with self.service.tx() as conn:
            conn.execute("UPDATE service_jobs SET verification_pid=? WHERE id=?", (99999999, job["id"]))
        self.assertEqual(self.service.recover_interrupted_verification(), [])

    def test_recovery_waits_for_output_collectors(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        run_dir = Path(claimed["attempt_run_dir"]); run_dir.mkdir(parents=True)
        (run_dir / "0-codex-state.json").write_text(json.dumps({
            "status": "collecting", "pid": 99999999, "collectors": [
                {"pid": os.getpid(), "identity": self.service._process_identity(os.getpid())}]}))
        with self.service.tx() as conn:
            conn.execute("UPDATE service_attempts SET pid=? WHERE job_id=?", (99999999, job["id"]))
        self.assertEqual(self.service.recover_stale_jobs(lambda *_: {"safe_to_resume": True}), [])

    def test_default_reconciliation_accepts_persisted_sqlite_attempt(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        run_dir = Path(claimed["attempt_run_dir"]); run_dir.mkdir(parents=True)
        (run_dir / "result.json").write_text(json.dumps({"status": "incomplete"}))
        with self.service.tx() as conn:
            conn.execute("UPDATE service_attempts SET pid=? WHERE job_id=?", (99999999, job["id"]))
        self.assertEqual(self.service.recover_stale_jobs(), [{"job_id": job["id"], "state": "retry"}])

    def test_scheduler_rechecks_survivors_that_die_after_startup(self):
        scheduler = Scheduler(self.service)
        with mock.patch.object(self.service, "recover_stale_jobs") as recover:
            scheduler.run_once(executor=lambda _: {"status": "completed"})
            scheduler.run_once(executor=lambda _: {"status": "completed"})
        self.assertEqual(recover.call_count, 2)

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

    def test_boolean_verdict_without_observations_cannot_complete_job(self):
        task = self.tasks.create_task(purpose="verify me", acceptance_evidence=["accepted"])
        self.tasks.add_acceptance_evidence(task["id"], "accepted", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        scheduler = Scheduler(self.service, verification_executor=lambda spec: {
            "acceptance": True, "merge": True, "main_sync": True, "evidence": "vault://verified"
        })
        result = scheduler.run_once(executor=lambda _: {"status": "completed", "text": "provider success"})
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")

    def test_verifier_findings_are_recorded_and_return_to_repair(self):
        task = self.tasks.create_task(purpose="verify findings", acceptance_evidence=["accepted"])
        self.tasks.add_acceptance_evidence(task["id"], "accepted", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        finding = {"issue": "missing test", "severity": "high"}
        disposition = {"status": "complete", "decisions": [],
                       "adopted_findings": [{"finding": finding, "decision": "adopt",
                                             "reason": "It blocks acceptance.",
                                             "evidence": ["vault://review"]}],
                       "rejected_findings": [], "separate_task_ids": []}
        with mock.patch("harness.service.decide_findings", return_value=disposition) as coordinator:
            result = Scheduler(self.service, verification_executor=lambda spec: {
                "acceptance": False, "merge": False, "main_sync": False,
                "findings": [finding], "evidence": None,
                "evidence_links": ["vault://review"],
            }).run_once(executor=lambda _: {"status": "completed", "text": "provider success"})
        coordinator.assert_called_once()
        self.assertEqual(result["status"], "retry")
        self.assertEqual(self.service.get_job(job["id"])["state"], "retry")
        self.assertIn("Verification finding adopted", self.service.get_job(job["id"])["updates"][-1]["message"])

    def test_rejected_finding_is_recorded_and_does_not_force_repair(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        finding = {"issue": "out of scope suggestion", "severity": "medium"}
        disposition = {"status": "complete", "decisions": [], "adopted_findings": [],
                       "rejected_findings": [{"finding": finding, "decision": "reject",
                                              "reason": "Outside the task scope.",
                                              "evidence": ["vault://review"]}],
                       "separate_task_ids": []}
        review = {"acceptance": True, "findings": [finding], "criteria": [],
                  "evidence_links": ["vault://review"]}
        with mock.patch("harness.service.decide_findings", return_value=disposition), \
             mock.patch.object(self.service, "verify", return_value={"state": "completed"}) as verify:
            result = Scheduler(self.service, verification_executor=lambda _: review).run_once(
                executor=lambda _: {"status": "completed"})
        self.assertEqual(result["status"], "verified")
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[1]["findings"], [])
        self.assertIn("Verification finding rejected", self.service.get_job(job["id"])["updates"][-1]["message"])

    def test_separate_finding_registers_local_follow_up_without_repair(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        finding = {"issue": "future improvement", "severity": "low"}
        disposition = {"status": "complete", "decisions": [], "adopted_findings": [],
                       "rejected_findings": [], "separate_task_ids": ["task-follow-up"],
                       "separated_findings": [{"finding": finding, "decision": "separate",
                                                "reason": "Track independently.",
                                                "evidence": ["vault://review"]}]}
        review = {"acceptance": True, "findings": [finding], "criteria": [],
                  "evidence_links": ["vault://review"]}
        with mock.patch("harness.service.decide_findings", return_value=disposition), \
             mock.patch.object(self.service, "verify", return_value={"state": "completed"}) as verify:
            result = Scheduler(self.service, verification_executor=lambda _: review).run_once(
                executor=lambda _: {"status": "completed"})
        self.assertEqual(result["status"], "verified")
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[1]["findings"], [])
        self.assertIn("task-follow-up", self.service.get_job(job["id"])["updates"][-1]["message"])

    def test_incomplete_finding_disposition_does_not_adopt(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        finding = {"issue": "unresolved review", "severity": "high"}
        review = {"acceptance": False, "findings": [finding], "evidence_links": ["vault://review"]}
        with mock.patch("harness.service.decide_findings", return_value={
            "status": "incomplete", "reason": "Astra decision unavailable",
        }) as coordinator:
            result = Scheduler(self.service, verification_executor=lambda _: review).run_once(
                executor=lambda _: {"status": "completed"})
        coordinator.assert_called_once()
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")
        self.assertFalse(any("adopted" in update["message"] for update in self.service.get_job(job["id"])["updates"]))

    def test_malformed_finding_disposition_does_not_adopt(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        finding = {"issue": "malformed disposition", "severity": "high"}
        review = {"acceptance": False, "findings": [finding], "evidence_links": ["vault://review"]}
        with mock.patch("harness.service.decide_findings", return_value={
            "status": "complete", "decisions": [], "adopted_findings": {"finding": finding},
            "rejected_findings": [], "separate_task_ids": [],
        }):
            result = Scheduler(self.service, verification_executor=lambda _: review).run_once(
                executor=lambda _: {"status": "completed"})
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")

    def test_default_verifier_requires_codex_terminal_and_inspects_saved_context(self):
        task = self.tasks.create_task(purpose="verify actual", acceptance_evidence=["A test passes"])
        self.tasks.add_acceptance_evidence(task["id"], "A test passes", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        events = "\n".join([
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({
                "acceptance": True, "merge": True, "main_sync": True,
                "findings": [], "criteria": [{"criterion_id": __import__("hashlib").sha256(b"A test passes").hexdigest(), "verified": True, "evidence": "test output"}], "evidence": "observed test and merged commit", "evidence_links": []})}}),
            json.dumps({"type": "turn.completed", "status": "completed", "usage": {"input_tokens": 3}}),
        ])
        from harness.runner import ProcessResult
        with mock.patch("harness.service.subprocess.run", return_value=mock.Mock(returncode=0, stdout="Logged in using ChatGPT", stderr="")), mock.patch("harness.runner.execute", return_value=ProcessResult(0, events, "")) as run:
            value = default_verifier({"job": self.service.get_job(job["id"]), "task": self.tasks.get_task(task["id"]),
                                      "agents_root": str(self.root), "vault_root": str(self.vault),
                                      "publication_snapshot": {"head": "a" * 40, "diff": "b" * 64}})
        self.assertTrue(value["acceptance"])
        self.assertEqual(value["criteria"][0]["criterion"], "A test passes")
        command = run.call_args.args[0]
        self.assertIn("read-only", command)
        self.assertIn("plugins", command)
        self.assertIn("stdout_path", run.call_args.kwargs)
        record = run.call_args.kwargs["stdout_path"].parent
        self.assertTrue((record / "prompt.json").is_file())
        self.assertEqual(json.loads((record / "usage.json").read_text())["input_tokens"], 3)
        prompt = json.loads(run.call_args.args[3])
        self.assertEqual(prompt["phase"], "pre_publication")
        self.assertIn("not a source defect", prompt["phase_instructions"])

    def test_verifier_criterion_ids_bind_to_exact_current_requirements(self):
        from harness.service import _acceptance_catalog, _bind_acceptance_ids
        task = {"acceptance_records": [{"evidence": "A long exact criterion."},
                                       {"evidence": "Another criterion."}]}
        catalog = _acceptance_catalog(task)
        self.assertEqual(catalog, _acceptance_catalog(task))
        verdict = {"criteria": [{"criterion_id": row["id"], "verified": True,
                                "evidence": "observed artifact"} for row in reversed(catalog)]}
        result = _bind_acceptance_ids(task, verdict)
        self.assertEqual([r["criterion"] for r in result["criteria"]],
                         ["Another criterion.", "A long exact criterion."])
        self.assertNotIn("criterion", verdict["criteria"][0])
        for invalid in ("unknown", catalog[0]["id"]):
            bad = {"criteria": [{"criterion_id": invalid, "criterion": "paraphrase"}]}
            with self.assertRaises(ValueError):
                _bind_acceptance_ids(task, bad)
        changed = {"acceptance_records": [{"evidence": "Changed criterion."}]}
        with self.assertRaises(ValueError):
            _bind_acceptance_ids(changed, verdict)

    def test_verification_without_evidence_is_requeued(self):
        task = self.tasks.create_task(purpose="verify incomplete", acceptance_evidence=["accepted"])
        self.tasks.add_acceptance_evidence(task["id"], "accepted", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        self.service.run_once(executor=lambda _: {"status": "completed"})
        self.service._start_verification(job["id"])
        result = self.service.verify_with_agent(job["id"], lambda _: {
            "acceptance": True, "merge": True, "main_sync": True, "findings": []})
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")

    def test_verify_requires_merge_and_main_sync_evidence(self):
        task = self.tasks.create_task(purpose="verify evidence", acceptance_evidence=["accepted"])
        self.tasks.add_acceptance_evidence(task["id"], "accepted", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        with self.assertRaises(ValueError):
            self.service.verify(job["id"], "vault://accepted-only")
        with self.assertRaises(ValueError):
            self.service.verify(job["id"], "vault://merge;main-sync")

    def test_verify_rejects_pending_job_even_with_valid_evidence(self):
        task = self.tasks.create_task(purpose="verify state", repository="Saber5656/Agents",
                                      acceptance_evidence=["accepted"])
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        evidence = {"acceptance": True, "findings": [],
                    "criteria": [{"criterion": "accepted", "verified": True,
                                   "evidence": "saved test"}],
                    "publication": {"commit": "a" * 40, "mode": "direct_main"}}
        with mock.patch("harness.service.observe_publication", return_value={"commit": "a" * 40}):
            with self.assertRaisesRegex(ValueError, "state"):
                self.service.verify(job["id"], evidence)
        self.assertEqual(self.service.get_job(job["id"])["state"], "pending")
        self.assertEqual(self.tasks.get_task(task["id"])["execution_status"], "planned")
        claimed = self.service._claim_next(); claimed["_lock"].release()
        with mock.patch("harness.service.observe_publication", return_value={"commit": "a" * 40}):
            with self.assertRaisesRegex(ValueError, "state"):
                self.service.verify(job["id"], evidence)
        self.assertEqual(self.service.get_job(job["id"])["state"], "running")

    def test_verifier_requires_every_exact_acceptance_criterion(self):
        task = self.tasks.create_task(purpose="verify exact", repository="Saber5656/Agents",
                                      acceptance_evidence=["test passes", "installed and exercised"])
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        verdict = {"acceptance": True, "findings": [], "evidence": "observed",
                   "criteria": [{"criterion": "test passes", "verified": True, "evidence": "test log"}],
                   "publication": {"commit": "a" * 40, "mode": "direct_main"}}
        with mock.patch("harness.service.observe_publication", return_value={"commit": "a" * 40}):
            outcome = Scheduler(self.service, verification_executor=lambda _: verdict).run_once(
                executor=lambda _: {"status": "completed"})
        self.assertEqual(outcome["status"], "needs_verification")
        self.assertFalse(any(row["verified"] for row in self.tasks.get_task(task["id"])["acceptance_records"]))

    def test_verified_criteria_and_actual_publication_complete_job(self):
        task = self.tasks.create_task(purpose="verify exact", repository="Saber5656/Agents",
                                      acceptance_evidence=["test passes"])
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        verdict = {"acceptance": True, "findings": [], "evidence": "observed",
                   "criteria": [{"criterion": "test passes", "verified": True, "evidence": "test log"}],
                   "publication": {"commit": "a" * 40, "mode": "direct_main"}}
        with mock.patch("harness.service.observe_publication", return_value={"commit": "a" * 40}) as observe:
            outcome = Scheduler(self.service, verification_executor=lambda _: verdict).run_once(
                executor=lambda _: {"status": "completed"})
        self.assertEqual(outcome["status"], "verified")
        self.assertTrue(self.tasks.get_task(task["id"])["acceptance_records"][0]["verified"])
        observe.assert_called_once()

    def test_publication_observes_real_git_and_github_main(self):
        from harness.service import observe_publication
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.workspace, text=True).strip()
        git("init", "-b", "main")
        git("-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid",
            "commit", "--allow-empty", "-m", "fixture")
        git("remote", "add", "origin", "git@github.com:Saber5656/Agents.git")
        sha = git("rev-parse", "HEAD")
        proof = {"commit": sha, "mode": "direct_main"}
        with mock.patch("harness.delivery.GitHub.api", return_value={"sha": sha}):
            result = observe_publication({"workspace": str(self.workspace)},
                                         {"repository": "Saber5656/Agents"}, proof)
        self.assertEqual(result["main"], sha)
        with mock.patch("harness.delivery.GitHub.api", side_effect=[{"sha": sha}, {"sha": "b" * 40}]):
            with self.assertRaisesRegex(ValueError, "moved"):
                observe_publication({"workspace": str(self.workspace)},
                                    {"repository": "Saber5656/Agents"}, proof)
        (self.workspace / "uncommitted").write_text("preserve")
        with self.assertRaisesRegex(ValueError, "uncommitted"):
            observe_publication({"workspace": str(self.workspace)},
                                {"repository": "Saber5656/Agents"}, proof)

    def test_publication_observation_preserves_unrelated_canonical_dirty_file(self):
        from harness.service import observe_publication
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.workspace, text=True).strip()
        git("init", "-b", "main")
        git("-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid",
            "commit", "--allow-empty", "-m", "fixture")
        git("remote", "add", "origin", "git@github.com:Saber5656/Agents.git")
        sha = git("rev-parse", "HEAD")
        (self.workspace / "unrelated-policy.md").write_text("preserve")
        proof = {"commit": sha, "mode": "direct_main", "files": ["src/change.py"]}
        with mock.patch("harness.delivery.GitHub.api", side_effect=[{"sha": sha}, {"sha": sha}]):
            result = observe_publication({"workspace": str(self.workspace)},
                                         {"repository": "Saber5656/Agents"}, proof)
        self.assertEqual(result["main"], sha)
        self.assertTrue((self.workspace / "unrelated-policy.md").exists())

    def test_incomplete_verification_persists_backoff_and_update_wakes_it(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context", retry_base=60)
        self.service.run_once(executor=lambda _: {"status": "completed"})
        self.assertTrue(self.service._start_verification(job["id"]))
        self.service.verify_with_agent(job["id"], lambda _: {"acceptance": False})
        self.assertIsNotNone(self.service.get_job(job["id"])["next_attempt_at"])
        self.assertFalse(self.service._start_verification(job["id"]))
        self.service.record_update(job["id"], "new acceptance evidence", ["vault://new"])
        self.assertTrue(self.service._start_verification(job["id"]))

    def test_verifier_error_keeps_successful_implementation_needing_verification(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        scheduler = Scheduler(self.service, verification_executor=lambda _: (_ for _ in ()).throw(RuntimeError("agent unavailable")))
        result = scheduler.run_once(executor=lambda _: {"status": "completed", "text": "provider success"})
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")
        self.assertEqual(self.service.get_job(job["id"])["attempts_count"], 1)

    def test_scheduler_resets_verifying_job_when_verifier_future_escapes(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context", retry_base=0)
        self.service.run_once(executor=lambda _: {"status": "completed"})
        stop = threading.Event()
        def escaped(_job_id, _verifier):
            stop.set()
            raise RuntimeError("unexpected verifier failure")
        scheduler = Scheduler(self.service, poll_interval=0.01, stop_event=stop,
                              verification_executor=lambda _: {"acceptance": False})
        with mock.patch.object(self.service, "verify_with_agent", side_effect=escaped):
            self.assertEqual(scheduler.run_forever(executor=lambda _: {"status": "failed"}), "stopped")
        self.assertEqual(self.service.get_job(job["id"])["state"], "needs_verification")

    def test_scheduler_surfaces_unattributed_worker_future_failure_for_restart(self):
        stop = threading.Event()
        scheduler = Scheduler(self.service, poll_interval=0.01, stop_event=stop)
        with mock.patch.object(self.service, "run_once", side_effect=RuntimeError("worker escaped")):
            with self.assertRaisesRegex(RuntimeError, "worker future failed"):
                scheduler.run_forever(executor=lambda _: {"status": "failed"})

    def test_scheduler_restarts_when_verification_cleanup_fails(self):
        job = self.service.enroll(self.task["id"], self.workspace, "prompt", "context")
        self.service.run_once(executor=lambda _: {"status": "completed"})
        scheduler = Scheduler(self.service, poll_interval=0.01,
                              verification_executor=lambda _: {"acceptance": False})
        with mock.patch.object(self.service, "verify_with_agent", side_effect=RuntimeError("escaped")), \
             mock.patch.object(self.service, "_reset_verification", side_effect=RuntimeError("db unavailable")):
            with self.assertRaisesRegex(RuntimeError, "verification cleanup failed"):
                scheduler.run_forever(executor=lambda _: {"status": "failed"})
        self.assertEqual(self.service.get_job(job["id"])["state"], "verifying")

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
        (self.root / "COMMON-AGENTS.md").write_text("COMMON POLICY MARKER")
        (self.root / "policies").mkdir()
        (self.root / "policies/repository-delivery.md").write_text("DELIVERY POLICY MARKER")
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root), "vault_root": str(self.vault), "run_dir": str(self.vault / "run"), "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna", "effort": "low", "timeout": 1, "updates": []}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed"}) as run, mock.patch("harness.runner.resume_job") as resume:
            self.assertEqual(default_executor(spec)["status"], "completed")
            job = run.call_args.args[0]
            self.assertEqual(job.mode, "run"); self.assertEqual(job.provider, "codex"); self.assertEqual(job.vault, self.vault.resolve()); self.assertEqual(job.run_dir, Path(spec["run_dir"]))
            self.assertIn("COMMON POLICY MARKER", job.prompt)
            self.assertIn("DELIVERY POLICY MARKER", job.prompt)
            self.assertIn("publication_proposal", job.prompt)
            resume.assert_not_called()

    def test_default_executor_passes_task_identity_when_runner_supports_it(self):
        from harness.service import default_executor
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna",
                "effort": "low", "timeout": 1, "updates": [], "task_id": self.task["id"]}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed"}) as run:
            default_executor(spec)
        job = run.call_args.args[0]
        self.assertEqual(getattr(job, "task_id", None), self.task["id"])

    def test_default_executor_preserves_structured_publication_proposal(self):
        from harness.service import default_executor
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna",
                "effort": "low", "timeout": 1, "updates": []}
        proposal = {"canonical_repo": "/tmp/canonical", "task_worktree": "/tmp/task",
                    "files": ["src/change.py"], "immutable_base": "a" * 40,
                    "commit_message": "Publish change", "remote": "/tmp/origin.git",
                    "vault_receipt": str(self.vault / "publication.json")}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed", "publication_proposal": proposal}):
            result = default_executor(spec)
        self.assertEqual(result["publication_proposal"], proposal)

    def test_default_executor_rejects_malformed_publication_proposal(self):
        from harness.service import default_executor
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna",
                "effort": "low", "timeout": 1, "updates": []}
        with mock.patch("harness.runner.run_job", return_value={"status": "completed", "publication_proposal": "self-approved"}):
            result = default_executor(spec)
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("publication_proposal", result)

    def test_progress_before_final_json_retains_publication_and_recovers_receipt(self):
        from harness.service import default_executor
        spec = {"workspace": str(self.workspace), "agents_root": str(self.root),
                "vault_root": str(self.vault), "run_dir": str(self.vault / "run"),
                "prompt": "prompt", "context": "context", "model": "gpt-5.6-luna",
                "effort": "low", "timeout": 1, "updates": []}
        proposal = {"files": ["harness/README.md"]}
        completed = {"status": "completed", "text": "Inspecting source and links.\n" +
                     json.dumps({"publication_proposal": proposal,
                                 "agents_worker_capture": {"discoveries": []}})}
        with mock.patch("harness.runner.run_job", return_value=completed):
            result = default_executor(spec)
        self.assertEqual(result["publication_proposal"], proposal)
        # A pre-fix attempt already persisted only the concatenated text.
        recovered = self.service._latest_publication_proposal({"attempts": [{"result": completed}]})
        self.assertEqual(recovered, proposal)

    def test_successful_worker_text_is_not_recorded_as_last_error(self):
        job = self.service.enroll(self.task["id"], self.workspace, "p", "c")
        self.service.run_once(executor=lambda _: {"status": "completed", "text": "Finished successfully"})
        self.assertIsNone(self.service.get_job(job["id"])["last_error"])

    def test_verification_publishes_worker_proposal_before_publication_readback(self):
        task = self.tasks.create_task(purpose="publish proposal", repository="Saber5656/Agents",
                                      acceptance_evidence=["publication is read back"])
        self.tasks.add_acceptance_evidence(task["id"], "publication is read back", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        proposal = {"canonical_repo": "/tmp/canonical", "task_worktree": "/tmp/task",
                    "files": ["src/change.py"], "immutable_base": "a" * 40,
                    "commit_message": "Publish change", "remote": "/tmp/origin.git",
                    "vault_receipt": str(self.vault / "publication.json")}
        self.service.run_once(executor=lambda _: {"status": "completed", "publication_proposal": proposal})
        self.assertTrue(self.service._start_verification(job["id"]))
        review = {"acceptance": True, "publication_readiness": True, "findings": [], "criteria": [
            {"criterion": "publication is read back", "verified": True, "evidence": "host readback"}],
            "publication_review": {"status": "complete", "decisions": [],
                                   "findings_complete": True}}
        with mock.patch.object(self.service, "_publish_proposal", return_value={"commit": "a" * 40, "mode": "direct_main"}) as publish, \
             mock.patch("harness.service.observe_publication", return_value={"commit": "a" * 40}):
            result = self.service.verify_with_agent(job["id"], lambda _: review)
        self.assertEqual(result["status"], "verified")
        publish.assert_called_once()

    def test_host_rejects_worker_self_reported_publication_digest(self):
        task = self.tasks.create_task(purpose="host publication CAS", repository="Saber5656/Agents")
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        canonical = self.root / "canonical"; canonical.mkdir()
        def run(path, *args):
            result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        run(canonical, "init", "-b", "main")
        run(canonical, "config", "user.name", "Fixture"); run(canonical, "config", "user.email", "fixture@example.invalid")
        (canonical / "src").mkdir(); (canonical / "src/change.py").write_text("before\n")
        run(canonical, "add", "."); run(canonical, "commit", "-m", "base")
        base = run(canonical, "rev-parse", "HEAD")
        worktree = self.root / "publication-task"
        run(canonical, "worktree", "add", "-b", "task/publication", str(worktree), base)
        (worktree / "src/change.py").write_text("after\n")
        proposal = {"repository": "Saber5656/Agents", "canonical_repo": str(canonical),
                    "task_worktree": str(worktree), "files": ["src/change.py"],
                    "immutable_base": base, "commit_message": "Publish change",
                    "remote": str(self.root / "origin.git"),
                    "vault_receipt": str(self.vault / "publication.json"),
                    "diff_digest": "0" * 64}
        review = {"status": "complete", "decisions": [], "findings_complete": True}
        with self.assertRaisesRegex(ValueError, "authorized GitHub remote"):
            self.service._publish_proposal(job, task, proposal, review)

    def test_publication_proposal_is_not_published_when_verdict_is_incomplete(self):
        task = self.tasks.create_task(purpose="incomplete publication", repository="Saber5656/Agents",
                                      acceptance_evidence=["publication is reviewed"])
        self.tasks.add_acceptance_evidence(task["id"], "publication is reviewed", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        proposal = {"canonical_repo": "/tmp/canonical", "task_worktree": "/tmp/task",
                    "files": ["src/change.py"], "immutable_base": "a" * 40,
                    "commit_message": "Publish change", "remote": "/tmp/origin.git",
                    "vault_receipt": str(self.vault / "publication.json")}
        self.service.run_once(executor=lambda _: {"status": "completed", "publication_proposal": proposal})
        self.assertTrue(self.service._start_verification(job["id"]))
        review = {"acceptance": False, "findings": [], "criteria": [],
                  "publication_review": {"status": "complete", "decisions": [],
                                         "findings_complete": True}}
        with mock.patch.object(self.service, "_publish_proposal") as publish:
            result = self.service.verify_with_agent(job["id"], lambda _: review)
        self.assertEqual(result["status"], "needs_verification")
        publish.assert_not_called()

    def test_publication_can_precede_final_acceptance_readback(self):
        task = self.tasks.create_task(purpose="publication readiness", repository="Saber5656/Agents",
                                      acceptance_evidence=["CI is green"])
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        proposal = {"canonical_repo": "/tmp/canonical", "task_worktree": "/tmp/task",
                    "files": ["src/change.py"], "immutable_base": "a" * 40,
                    "commit_message": "Publish change", "remote": "/tmp/origin.git"}
        self.service.run_once(executor=lambda _: {"status": "completed", "publication_proposal": proposal})
        self.assertTrue(self.service._start_verification(job["id"]))
        verdict = {"acceptance": False, "publication_readiness": True, "findings": [],
                   "criteria": [], "publication_review": {"status": "complete",
                   "reviewed": True, "reviewed_head": "a" * 40,
                   "reviewed_diff_digest": "b" * 64, "findings_complete": True,
                   "decisions": [{"finding_id": "none", "decision": "reject",
                                  "reason": "no finding", "evidence": ["vault://review" ]}]}}
        with mock.patch.object(self.service, "_publish_proposal", return_value={"commit": "a" * 40, "mode": "direct_main"}) as publish, \
             mock.patch("harness.service.observe_publication", return_value={"commit": "a" * 40}):
            result = self.service.verify_with_agent(job["id"], lambda _: verdict)
        self.assertEqual(result["status"], "needs_verification")
        publish.assert_called_once()

    def test_publication_review_must_account_for_all_findings(self):
        task = self.tasks.create_task(purpose="incomplete publication review", repository="Saber5656/Agents",
                                      acceptance_evidence=["publication is reviewed"])
        self.tasks.add_acceptance_evidence(task["id"], "publication is reviewed", verified=True)
        job = self.service.enroll(task["id"], self.workspace, "prompt", "context")
        proposal = {"canonical_repo": "/tmp/canonical", "task_worktree": "/tmp/task",
                    "files": ["src/change.py"], "immutable_base": "a" * 40,
                    "commit_message": "Publish change", "remote": "/tmp/origin.git",
                    "vault_receipt": str(self.vault / "publication.json")}
        self.service.run_once(executor=lambda _: {"status": "completed", "publication_proposal": proposal})
        self.assertTrue(self.service._start_verification(job["id"]))
        review = {"acceptance": True, "findings": [], "criteria": [
            {"criterion": "publication is reviewed", "verified": True, "evidence": "host readback"}],
            "publication_review": {"status": "complete", "decisions": [],
                                   "findings_complete": False}}
        with mock.patch.object(self.service, "_publish_proposal") as publish:
            result = self.service.verify_with_agent(job["id"], lambda _: review)
        self.assertEqual(result["status"], "needs_verification")
        publish.assert_not_called()

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

    def test_idle_run_does_not_poll_authentication(self):
        with mock.patch.object(self.service, "auth_guard", side_effect=AssertionError("auth must wait for work")):
            self.assertEqual(self.service.run_once(executor=None)["status"], "idle")

    def test_service_db_busy_timeout_honors_constructor(self):
        other = ServiceStore(self.root / "short.sqlite3", self.tasks, timeout=0.125)
        try:
            value = other._conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(value, 125)
        finally:
            other.close()

    def test_service_rejects_symlink_database_parent(self):
        target = self.root / "real"; target.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("symlink unavailable")
        with self.assertRaises(ValueError):
            ServiceStore(link / "service.sqlite3", self.tasks)

    def test_service_rejects_untrusted_database_ancestor(self):
        parent = self.root / "world-writable"; parent.mkdir(); parent.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "writable"):
            ServiceStore(parent / "service.sqlite3", self.tasks)

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

    def test_launchd_install_rejects_symlink_target(self):
        outside = self.root / "outside.plist"; outside.write_bytes(b"keep")
        target = self.root / "LaunchAgents" / "service.plist"
        target.parent.mkdir(); target.symlink_to(outside)
        launchd = Launchd(self.root, self.root / "service.sqlite3", self.vault)
        with mock.patch("harness.service.sys.platform", "darwin"):
            with self.assertRaises(ValueError):
                launchd.install("com.example.agents", target)
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_launchd_install_restores_exact_preimage_after_atomic_failure(self):
        target = self.root / "LaunchAgents" / "service.plist"
        target.parent.mkdir(); old = b"old plist\r\n\xff"; target.write_bytes(old)
        launchd = Launchd(self.root, self.root / "service.sqlite3", self.vault)
        real_replace = os.replace; calls = []
        def fail_target(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("simulated replacement failure")
            return real_replace(source, destination)
        with mock.patch("harness.service.sys.platform", "darwin"), \
             mock.patch("harness.service.os.replace", side_effect=fail_target):
            with self.assertRaisesRegex(OSError, "simulated"):
                launchd.install("com.example.agents", target)
        self.assertEqual(target.read_bytes(), old)
        backups = list(target.parent.glob(target.name + ".bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), old)

    def test_dotenv_loader_rejects_shell_and_auth_guard_rejects_api_route(self):
        path = self.root / ".env"; path.write_text("AGENTS_ROOT=/safe\nSKILLS_ROOT=${AGENTS_ROOT}/skills\nCODEX_MODEL=gpt-5.6-luna\n")
        loaded = load_agents_env(path, {"AGENTS_ROOT": "old"})
        self.assertEqual(loaded["AGENTS_ROOT"], "/safe")
        self.assertEqual(loaded["SKILLS_ROOT"], "/safe/skills")
        path.write_text("CODEX_MODEL=$(touch injected)\n")
        with self.assertRaises(ValueError): load_agents_env(path, {})
        with self.assertRaises(AuthError):
            self.service.auth_guard({"OPENAI_API_KEY": "secret"}, login_check=lambda _: True)
        with self.assertRaises(AuthError):
            self.service.auth_guard({"CODEX_API_KEY": "secret"}, login_check=lambda _: True)
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

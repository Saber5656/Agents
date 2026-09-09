"""Private acceptance evidence for Issue #24.

These tests exercise the existing ServiceStore/Scheduler against disposable
SQLite stores.  The provider and remote Issue adapter are deliberately fake;
the scheduler, dependency checks, workspace locks, and durable service state
are the production implementations under test.  This file is an acceptance
verification artifact, not a product behavior change, so TDD is inapplicable.
"""

from pathlib import Path
import json
import os
import threading
import tempfile
import time
import unittest

from harness.service import Scheduler, ServiceStore
from harness.tasks import TaskStore


class MockProvider:
    """Explicitly fake provider used only by this disposable acceptance."""

    def __init__(self, *, delay=0.03):
        self.delay = delay
        self.calls = []
        self._lock = threading.Lock()

    def execute(self, spec):
        with self._lock:
            self.calls.append({"task_id": spec["task_id"], "workspace": spec["workspace"]})
        time.sleep(self.delay)
        return {"status": "completed", "provider": "mock-provider"}


class MockRemote:
    """Explicitly fake Issue readback; no GitHub transport is reachable."""

    def __init__(self, fixture_issues):
        self.fixture_issues = fixture_issues
        self.readbacks = []

    def readback(self, issue_number):
        issue = self.fixture_issues[issue_number]
        self.readbacks.append(issue_number)
        return {"source": "mock-remote", "number": issue["number"], "url": issue["url"]}


class SchedulerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="issue24-scheduler-")
        self.root = Path(self.tmp.name)
        self.agents = self.root / "agents"
        self.agents.mkdir()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.tasks = TaskStore(self.root / "tasks.sqlite3", agents_root=self.agents,
                               vault_root=self.vault)
        self.service = ServiceStore(self.root / "service.sqlite3", self.tasks)
        # unittest cleanups run LIFO: close SQLite connections before removing
        # the temporary directory that contains their WAL/SHM files.
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.tasks.close)
        self.addCleanup(self.service.close)

    def _fixture(self, count=5, *, dependencies=None):
        dependencies = dependencies or {}
        issues = {}
        tasks = {}
        for number in range(1, count + 1):
            workspace = self.root / f"worktree-{number}"
            workspace.mkdir()
            issue = {
                "number": 900 + number,
                "url": f"mock://github/Agents/issues/{900 + number}",
            }
            task = self.tasks.create_task(
                purpose=f"fixture Issue #{issue['number']}",
                source="mock-remote",
                expected_result="mock provider completes the bounded fixture",
                repository="fixture/repository",
                dependencies=dependencies.get(number, []),
                evidence_links=[issue["url"]],
            )
            job = self.service.enroll(
                task["id"], workspace, f"fixture prompt {number}",
                f"fixture context {number}", retry_base=0,
                resource=f"fixture-resource-{number}",
            )
            issues[issue["number"]] = issue
            tasks[number] = {"task": task, "job": job, "workspace": workspace}
        # SQLite timestamps are second-granular; pin fixture creation order so
        # the same first queued job is selected after a store reopen.
        with self.service.tx() as conn:
            for number in range(1, count + 1):
                conn.execute("UPDATE service_jobs SET created_at=? WHERE id=?",
                             (f"2026-01-01T00:00:{number:02d}+00:00",
                              tasks[number]["job"]["id"]))
        return issues, tasks

    def test_five_fixture_issues_run_dependency_safe_branches_with_reserved_review_slot(self):
        """Five Issue fixtures run in two bounded branches then their dependent task."""
        # 1 and 2 are independent; 3 depends on 1; 4 depends on 2; 5 depends
        # on both branches.  These are five fixture Issues, not GitHub Issues.
        issues = {}
        graph_rows = {}
        for number in range(1, 6):
            issue = {"number": 900 + number,
                     "url": f"mock://github/Agents/issues/{900 + number}"}
            issues[issue["number"]] = issue
            dependencies = []
            if number == 3:
                dependencies = [graph_rows[1]["task"]["id"]]
            elif number == 4:
                dependencies = [graph_rows[2]["task"]["id"]]
            elif number == 5:
                dependencies = [graph_rows[3]["task"]["id"], graph_rows[4]["task"]["id"]]
            workspace = self.root / f"worktree-{number}"
            workspace.mkdir()
            task = self.tasks.create_task(
                purpose=f"fixture Issue #{issue['number']}", source="mock-remote",
                expected_result="mock provider completes the bounded fixture",
                repository="fixture/repository", dependencies=dependencies,
                evidence_links=[issue["url"]])
            job = self.service.enroll(
                task["id"], workspace, "fixture prompt", "fixture context",
                retry_base=0, resource=task["id"])
            graph_rows[number] = {"task": task, "job": job, "workspace": workspace}

        provider = MockProvider(delay=0.02)
        remote = MockRemote(issues)
        evidence = []
        execution_events = []
        for wave_numbers in ((1, 2), (3, 4), (5,)):
            wave = {number: graph_rows[number] for number in wave_numbers}
            starts = []
            active = 0
            maximum = 0
            lock = threading.Lock()
            barrier = threading.Barrier(len(wave)) if len(wave) > 1 else None
            pending_at_capacity = []

            def execute(spec, wave=wave):
                nonlocal active, maximum
                self.assertIn(spec["task_id"], {item["task"]["id"] for item in wave.values()})
                with lock:
                    starts.append(spec["task_id"])
                    active += 1
                    maximum = max(maximum, active)
                    execution_events.append({
                        "phase": "start", "task_id": spec["task_id"],
                        "workspace": spec["workspace"], "cwd": os.getcwd(),
                        "observed_at": time.time(),
                    })
                issue_number = next(n for n, item in graph_rows.items()
                                    if item["task"]["id"] == spec["task_id"])
                readback = remote.readback(900 + issue_number)
                self.assertEqual(readback["number"], 900 + issue_number)
                self.assertEqual(readback["url"], issues[900 + issue_number]["url"])
                if barrier is not None:
                    barrier.wait(timeout=2)
                    with lock:
                        if not pending_at_capacity:
                            pending_at_capacity.append(
                                len(self.service.list_jobs("pending")))
                result = provider.execute(spec)
                with lock:
                    active -= 1
                    execution_events.append({
                        "phase": "end", "task_id": spec["task_id"],
                        "workspace": spec["workspace"], "cwd": os.getcwd(),
                        "observed_at": time.time(),
                    })
                    if len(starts) == len(wave):
                        stop.set()
                return result

            stop = threading.Event()
            scheduler = Scheduler(self.service, poll_interval=0.005,
                                  max_workers=3, coordinator_reserved=1,
                                  stop_event=stop)
            # The production scheduler loop owns the bounded worker pool and
            # repeatedly reclaims only eligible rows.  The fixture callback
            # stops this disposable loop after its wave has run.
            timer = threading.Timer(5.0, stop.set)
            timer.daemon = True
            timer.start()
            try:
                self.assertEqual(scheduler.run_forever(executor=execute), "stopped")
            finally:
                timer.cancel()
            wave_job_ids = {item["job"]["id"] for item in wave.values()}
            self.assertTrue(all(job["state"] == "needs_verification"
                                for job in self.service.list_jobs()
                                if job["id"] in wave_job_ids),
                            self.service.list_jobs())
            self.assertEqual(set(starts), {item["task"]["id"] for item in wave.values()})
            self.assertEqual(len(starts), len(wave))
            self.assertLessEqual(maximum, 2)
            self.assertEqual(scheduler.worker_capacity, 2)
            if len(wave) == 2:
                self.assertGreaterEqual(pending_at_capacity[0], 1)
            for number in wave_numbers:
                task_id = graph_rows[number]["task"]["id"]
                self.tasks.add_acceptance_evidence(task_id, "mock fixture completed", verified=True)
                self.tasks.add_completion_evidence(task_id, "main-sync:mock-fixture")
                current = self.tasks.get_task(task_id)
                self.tasks.update_task(task_id, expected_version=current["version"],
                                       execution_status="verified")
            evidence.append({"wave": wave_numbers, "maximum_active": maximum,
                             "worker_capacity": scheduler.worker_capacity,
                             "started": starts,
                             "started_numbers": [next(n for n, item in graph_rows.items()
                                                      if item["task"]["id"] == task_id)
                                                 for task_id in starts],
                             "pending_at_capacity": pending_at_capacity})

        sequence = [number for item in evidence for number in item["started_numbers"]]
        self.assertEqual(set(sequence[:2]), {1, 2})
        self.assertEqual(set(sequence[2:4]), {3, 4})
        self.assertEqual(sequence[4:], [5])
        self.assertEqual(sorted(remote.readbacks), [901, 902, 903, 904, 905])
        self.assertEqual(len(provider.calls), 5)
        self.assertEqual(len(self.service.list_jobs()), 5)
        self.assertTrue(all(job["state"] == "needs_verification"
                            for job in self.service.list_jobs()))
        print("SCHEDULER_ACCEPTANCE_EVIDENCE=" + json.dumps({
            "provider": "mock-provider", "remote": "mock-remote",
            "fixture_issue_numbers": sorted(issues), "events": execution_events,
            "waves": evidence,
        }, sort_keys=True))

    def test_provider_quota_failure_and_service_reopen_preserve_queue_and_owner(self):
        """A mock quota failure is retryable and reopening keeps the same jobs."""
        _issues, rows = self._fixture(5)
        # The scheduler's durable creation order is the ownership identity we
        # follow across the simulated provider quota failure and reopen.
        first = self.service.list_jobs()[0]["id"]
        calls = []

        def quota_executor(spec):
            calls.append((spec["task_id"], spec["job_id"]))
            return {"status": "failed", "provider": "mock-provider",
                    "text": "mock provider quota exhausted"}

        first_result = self.service.run_once(executor=quota_executor)
        self.assertEqual(first_result["status"], "retry")
        self.assertEqual(first_result["job_id"], first)
        self.assertEqual(self.service.get_job(first)["state"], "retry")
        self.assertEqual(self.service.get_job(first)["attempts_count"], 1)
        queued_ids = {job["id"] for job in self.service.list_jobs("pending")}
        self.assertEqual(len(queued_ids), 4)

        self.service.close()
        reopened = ServiceStore(self.root / "service.sqlite3", self.tasks)
        self.addCleanup(reopened.close)
        self.assertEqual({job["id"] for job in reopened.list_jobs("pending")}, queued_ids)
        self.assertEqual(reopened.get_job(first)["id"], first)

        resumed = reopened.run_once(executor=lambda spec: calls.append(
            (spec["task_id"], spec["job_id"])) or {"status": "completed",
                                                      "provider": "mock-provider"})
        self.assertEqual(resumed["job_id"], first)
        self.assertEqual(reopened.get_job(first)["attempts_count"], 2)
        self.assertEqual([job["id"] for job in reopened.list_jobs()].count(first), 1)
        self.assertEqual(len(reopened.list_jobs()), 5)
        self.assertEqual(len({job_id for _task_id, job_id in calls}), 1)
        self.assertEqual(reopened.get_job(first)["attempts"][0]["result"]["provider"],
                         "mock-provider")


if __name__ == "__main__":
    unittest.main()

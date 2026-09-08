import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from harness.context import RequirementLedger, ContextError
from harness.service import ServiceStore
from harness.tasks import TaskStore


class SelectedEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.agents = self.root / "agents"
        self.agents.mkdir()
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.workspace = self.root / "worktree"
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Fixture"], check=True)
        (self.repo / "README").write_text("fixture\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "README"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "initial"], check=True)
        self.base = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-qb", "task/selected", str(self.workspace), self.base], check=True)
        self.tasks = TaskStore(self.root / "tasks.sqlite3", agents_root=self.agents, vault_root=self.vault)
        self.ledger = RequirementLedger(self.tasks, self.root / "ledger.json")
        self.requirement = self.ledger.add(
            "Implement the selected worker integration", acceptance=["worker evidence"], source="issue://67"
        )
        self.task = self.tasks.create_task(
            purpose="worker integration", repository="org/repo", acceptance_evidence=["worker evidence"]
        )
        self.tasks.link_requirement_task(self.requirement["id"], self.task["id"])
        self.tasks.link_work_unit("unit-67", task_ids=[self.task["id"]], purpose="selected unit")
        self.context_file = self.vault / "execution-01" / "criteria.json"
        self.context_file.parent.mkdir()
        self.context_file.write_text(json.dumps({"criterion": "worker evidence"}))
        self.service = ServiceStore(self.root / "service.sqlite3", self.tasks)
        self.addCleanup(self.service.close)
        self.addCleanup(self.tasks.close)
        self.addCleanup(self.tmp.cleanup)

    def test_selected_enrollment_persists_explicit_binding_and_is_idempotent(self):
        self.ledger.select(self.requirement["id"])
        first = self.service.enroll_selected(
            self.requirement["id"], self.task["id"], ledger=self.ledger,
            workspace=self.workspace, repository="org/repo", repository_path=self.repo,
            branch="task/selected", immutable_base=self.base,
            vault_reference="vault://execution-01/criteria.json",
            criteria=["worker evidence"], work_unit="unit-67",
        )
        second = self.service.enroll_selected(
            self.requirement["id"], self.task["id"], ledger=self.ledger,
            workspace=self.workspace, repository="org/repo", repository_path=self.repo,
            branch="task/selected", immutable_base=self.base,
            vault_reference="vault://execution-01/criteria.json",
            criteria=["worker evidence"], work_unit="unit-67",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["requirement_id"], self.requirement["id"])
        self.assertEqual(first["work_unit_id"], "unit-67")
        self.assertEqual(first["immutable_base"], self.base)
        self.assertEqual(first["vault_reference"], "vault://execution-01/criteria.json")
        self.assertEqual(json.loads(first["criteria_json"]), ["worker evidence"])
        self.assertIn(self.requirement["id"], first["context"])
        self.assertIn("vault://execution-01/criteria.json", first["context"])
        self.assertEqual(first["criteria"], ["worker evidence"])
        reopened = ServiceStore(self.root / "service.sqlite3", self.tasks)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get_job(first["id"])["id"], first["id"])

    def test_selection_must_be_explicit_and_task_must_be_linked(self):
        with self.assertRaises(ContextError):
            self.service.enroll_selected(
                self.requirement["id"], self.task["id"], ledger=self.ledger,
                workspace=self.workspace, repository="org/repo", repository_path=self.repo,
                branch="task/selected", immutable_base=self.base,
                vault_reference="vault://execution-01/criteria.json", criteria=["worker evidence"],
            )
        self.ledger.select(self.requirement["id"])
        other = self.tasks.create_task(purpose="unselected task", repository="org/repo")
        with self.assertRaises(ValueError):
            self.service.enroll_selected(
                self.requirement["id"], other["id"], ledger=self.ledger,
                workspace=self.workspace, repository="org/repo", repository_path=self.repo,
                branch="task/selected", immutable_base=self.base,
                vault_reference="vault://execution-01/criteria.json", criteria=["worker evidence"],
            )

    def test_selected_binding_reuses_existing_service_enrollment_and_updates(self):
        existing = self.service.enroll(self.task["id"], self.workspace, "original request", "original context")
        self.service.record_update(existing["id"], "scope correction", ["vault://execution-01/correction.json"])
        self.ledger.select(self.requirement["id"])
        selected = self.service.enroll_selected(
            self.requirement["id"], self.task["id"], ledger=self.ledger,
            workspace=self.workspace, repository="org/repo", repository_path=self.repo,
            branch="task/selected", immutable_base=self.base,
            vault_reference="vault://execution-01/criteria.json", criteria=["worker evidence"],
        )
        self.assertEqual(selected["id"], existing["id"])
        self.assertEqual(selected["prompt"], "original request")
        self.assertEqual(selected["context"], "original context")
        self.assertEqual(selected["updates"][0]["message"], "scope correction")

    def enroll_selected(self, **overrides):
        self.ledger.select(self.requirement["id"])
        args = dict(ledger=self.ledger, workspace=self.workspace,
                    repository="org/repo", repository_path=self.repo,
                    branch="task/selected", immutable_base=self.base,
                    vault_reference="vault://execution-01/criteria.json",
                    criteria=["worker evidence"])
        args.update(overrides)
        return self.service.enroll_selected(self.requirement["id"], self.task["id"], **args)

    def test_untracked_task_criteria_cannot_be_verified_away(self):
        self.tasks.create_requirement("other", acceptance=["extra requirement"],
                                      requirement_id="req_extra")
        self.tasks.link_requirement_task("req_extra", self.task["id"])
        self.ledger.select("req_extra")
        with self.assertRaisesRegex(ValueError, "task acceptance"):
            self.service.enroll_selected("req_extra", self.task["id"], ledger=self.ledger,
                workspace=self.workspace, repository="org/repo", repository_path=self.repo,
                branch="task/selected", immutable_base=self.base,
                vault_reference="vault://execution-01/criteria.json",
                criteria=["extra requirement"])
        self.assertEqual(self.service.list_jobs(), [])

    def test_adopted_job_forwards_selection_without_rewriting_original_context(self):
        from harness.service import default_executor
        original = self.service.enroll(self.task["id"], self.workspace, "original", "retained")
        selected = self.enroll_selected()
        captured = []
        self.service.run_once(executor=lambda spec: captured.append(spec) or {"status": "completed"})
        self.assertEqual(selected["context"], "retained")
        with mock.patch("harness.service.load_agents_env", return_value={}), \
             mock.patch("harness.runner.run_job", return_value={"status": "completed"}) as run:
            default_executor(captured[0])
        prompt = run.call_args.args[0].prompt
        self.assertIn("retained", prompt)
        self.assertIn("vault://execution-01/criteria.json", prompt)
        self.assertIn("worker evidence", prompt)
        self.assertIn(self.base, prompt)
        self.assertEqual(original["id"], selected["id"])

    def test_dependency_is_not_started_until_verified(self):
        self.ledger.select(self.requirement["id"])
        dependency = self.tasks.create_task(
            purpose="dependency", acceptance_evidence=["dep"], completion_evidence=["main-sync:receipt"]
        )
        task = self.tasks.create_task(
            purpose="dependent", repository="org/repo", dependencies=[dependency["id"]],
            acceptance_evidence=["worker evidence"]
        )
        req = self.ledger.add("dependent requirement", acceptance=["worker evidence"])
        self.tasks.link_requirement_task(req["id"], task["id"])
        self.ledger.select(req["id"])
        job = self.service.enroll_selected(
            req["id"], task["id"], ledger=self.ledger, workspace=self.workspace,
            repository="org/repo", repository_path=self.repo, branch="task/selected",
            immutable_base=self.base, vault_reference="vault://execution-01/criteria.json",
            criteria=["worker evidence"],
        )
        self.assertEqual(self.service.run_once(executor=lambda _: {"status": "completed"})["status"], "blocked")
        self.assertEqual(self.service.get_job(job["id"])["state"], "pending")

    def test_requirement_dependency_requires_its_declared_acceptance(self):
        dependency = self.ledger.add("dependency", acceptance=["required dependency acceptance"])
        selected = self.ledger.add(
            "selected dependent", acceptance=["worker evidence"],
            depends_on=[dependency["id"]],
        )
        dependency_task = self.tasks.create_task(
            purpose="dependency", repository="org/repo", execution_status="completed",
            acceptance_evidence=["unrelated evidence"], completion_evidence=["dep done"],
        )
        self.tasks.add_acceptance_evidence(
            dependency_task["id"], "unrelated evidence", verified=True
        )
        self.tasks.link_requirement_task(dependency["id"], dependency_task["id"])
        selected_task = self.tasks.create_task(
            purpose="selected", repository="org/repo", acceptance_evidence=["worker evidence"]
        )
        self.tasks.link_requirement_task(selected["id"], selected_task["id"])
        self.ledger.select(selected["id"])
        job = self.service.enroll_selected(
            selected["id"], selected_task["id"], ledger=self.ledger,
            workspace=self.workspace, repository="org/repo", repository_path=self.repo,
            branch="task/selected", immutable_base=self.base,
            vault_reference="vault://execution-01/criteria.json",
            criteria=["worker evidence"],
        )

        outcome = self.service.run_once(executor=lambda _: {"status": "completed"})

        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.service.get_job(job["id"])["state"], "pending")

    def test_requirement_acceptance_can_be_covered_by_separate_completed_tasks(self):
        dependency = self.ledger.add("two purposes", acceptance=["first", "second"])
        for criterion in ("first", "second"):
            task = self.tasks.create_task(purpose=criterion, execution_status="verified",
                acceptance_evidence=[criterion], completion_evidence=["main-sync:fixture"])
            self.tasks.add_acceptance_evidence(task["id"], criterion, verified=True)
            self.tasks.link_requirement_task(dependency["id"], task["id"])
        with self.tasks._tx() as conn:
            conn.execute("INSERT INTO requirement_dependencies VALUES (?, ?)",
                         (self.requirement["id"], dependency["id"]))
        job = self.enroll_selected()
        calls = []
        result = self.service.run_once(executor=lambda spec: calls.append(spec) or {"status":"completed"})
        self.assertEqual(result["status"], "needs_verification")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["task_id"], self.task["id"])

    def test_requirement_dependency_rejects_provider_success_without_delivery(self):
        dependency = self.ledger.add("dependency", acceptance=["required"])
        task = self.tasks.create_task(purpose="dependency", execution_status="verified",
            acceptance_evidence=["required"], completion_evidence=["provider reported success"])
        self.tasks.add_acceptance_evidence(task["id"], "required", verified=True)
        self.tasks.link_requirement_task(dependency["id"], task["id"])
        with self.tasks._tx() as conn:
            conn.execute("INSERT INTO requirement_dependencies VALUES (?, ?)",
                         (self.requirement["id"], dependency["id"]))
        self.enroll_selected()
        calls=[]
        result=self.service.run_once(executor=lambda spec: calls.append(spec) or {"status":"completed"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(calls, [])

    def test_requirement_dependency_without_linked_task_stays_blocked(self):
        dependency = self.ledger.add("missing dependency", acceptance=["missing acceptance"])
        selected = self.ledger.add(
            "selected dependent", acceptance=["worker evidence"],
            depends_on=[dependency["id"]],
        )
        selected_task = self.tasks.create_task(
            purpose="selected", repository="org/repo", acceptance_evidence=["worker evidence"]
        )
        self.tasks.link_requirement_task(selected["id"], selected_task["id"])
        self.ledger.select(selected["id"])
        service = ServiceStore(self.root / "missing-dependency-service.sqlite3", self.tasks)
        self.addCleanup(service.close)
        job = service.enroll_selected(
            selected["id"], selected_task["id"], ledger=self.ledger,
            workspace=self.workspace, repository="org/repo", repository_path=self.repo,
            branch="task/selected", immutable_base=self.base,
            vault_reference="vault://execution-01/criteria.json",
            criteria=["worker evidence"],
        )

        outcome = service.run_once(executor=lambda _: {"status": "completed"})

        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(service.get_job(job["id"])["state"], "pending")

    def test_requirement_dependency_chain_and_cycle_fail_closed(self):
        leaf = self.ledger.add("leaf", acceptance=["leaf acceptance"])
        middle = self.ledger.add(
            "middle", acceptance=["middle acceptance"], depends_on=[leaf["id"]]
        )
        selected = self.ledger.add(
            "selected", acceptance=["worker evidence"], depends_on=[middle["id"]]
        )
        leaf_task = self.tasks.create_task(
            purpose="leaf", repository="org/repo", acceptance_evidence=["leaf acceptance"]
        )
        self.tasks.link_requirement_task(leaf["id"], leaf_task["id"])
        middle_task = self.tasks.create_task(
            purpose="middle", repository="org/repo", execution_status="completed",
            acceptance_evidence=["middle acceptance"], completion_evidence=["middle done"],
        )
        self.tasks.add_acceptance_evidence(
            middle_task["id"], "middle acceptance", verified=True
        )
        self.tasks.link_requirement_task(middle["id"], middle_task["id"])
        selected_task = self.tasks.create_task(
            purpose="selected", repository="org/repo", acceptance_evidence=["worker evidence"]
        )
        self.tasks.link_requirement_task(selected["id"], selected_task["id"])
        self.ledger.select(selected["id"])
        job = self.service.enroll_selected(
            selected["id"], selected_task["id"], ledger=self.ledger,
            workspace=self.workspace, repository="org/repo", repository_path=self.repo,
            branch="task/selected", immutable_base=self.base,
            vault_reference="vault://execution-01/criteria.json",
            criteria=["worker evidence"],
        )

        outcome = self.service.run_once(executor=lambda _: {"status": "completed"})

        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.service.get_job(job["id"])["state"], "pending")

        # A corrupted or concurrently edited dependency graph must not cause
        # recursive scheduling or an unbounded walk. Both completed nodes
        # still form a cycle, so the selected job remains blocked.
        cycle_a = self.ledger.add("cycle A", acceptance=["cycle A acceptance"])
        cycle_b = self.ledger.add("cycle B", acceptance=["cycle B acceptance"])
        for requirement, criterion in ((cycle_a, "cycle A acceptance"),
                                       (cycle_b, "cycle B acceptance")):
            task = self.tasks.create_task(
                purpose=requirement["text"], repository="org/repo", execution_status="completed",
                acceptance_evidence=[criterion], completion_evidence=["cycle done"],
            )
            self.tasks.add_acceptance_evidence(task["id"], criterion, verified=True)
            self.tasks.link_requirement_task(requirement["id"], task["id"])
        with self.tasks._tx() as conn:
            conn.execute(
                "INSERT INTO requirement_dependencies VALUES (?, ?)",
                (cycle_a["id"], cycle_b["id"]),
            )
            conn.execute(
                "INSERT INTO requirement_dependencies VALUES (?, ?)",
                (cycle_b["id"], cycle_a["id"]),
            )
        self.ledger.select(cycle_a["id"])
        cycle_task = self.tasks.get_requirement(cycle_a["id"])["task_ids"][0]
        cycle_service = ServiceStore(self.root / "cycle-service.sqlite3", self.tasks)
        self.addCleanup(cycle_service.close)
        cycle_job = cycle_service.enroll_selected(
            cycle_a["id"], cycle_task, ledger=self.ledger,
            workspace=self.workspace, repository="org/repo", repository_path=self.repo,
            branch="task/selected", immutable_base=self.base,
            vault_reference="vault://execution-01/criteria.json",
            criteria=["cycle A acceptance"],
        )
        cycle_outcome = cycle_service.run_once(executor=lambda _: {"status": "completed"})

        self.assertEqual(cycle_outcome["status"], "blocked")
        self.assertEqual(cycle_service.get_job(cycle_job["id"])["state"], "pending")


if __name__ == "__main__":
    unittest.main()

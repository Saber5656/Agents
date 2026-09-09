"""Acceptance of canonical sync and dependency scheduling through real SQLite state."""
import json
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import unittest

from harness.delivery import sync_main
from harness.service import Scheduler, ServiceStore
from harness.tasks import TaskStore


def git(cwd, *args):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


class SyncSchedulerAcceptanceTests(unittest.TestCase):
    def test_scheduler_queues_dependency_until_real_main_sync_then_runs_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "canonical"
            canonical.mkdir()
            git(canonical, "init", "-b", "main")
            git(canonical, "config", "user.name", "Fixture")
            git(canonical, "config", "user.email", "fixture@example.invalid")
            (canonical / "README").write_text("base\n")
            git(canonical, "add", "README")
            git(canonical, "commit", "-m", "base")
            remote = root / "remote.git"
            git(root, "init", "--bare", str(remote))
            git(canonical, "remote", "add", "origin", str(remote))
            git(canonical, "push", "origin", "main")
            other = root / "other"
            git(root, "clone", "--branch", "main", str(remote), str(other))
            git(other, "config", "user.name", "Fixture")
            git(other, "config", "user.email", "fixture@example.invalid")
            (other / "sync").write_text("verified\n")
            git(other, "add", "sync")
            git(other, "commit", "-m", "integrated sync")
            git(other, "push", "origin", "main")
            merge_sha = git(other, "rev-parse", "HEAD")

            agents_root = root / "agents"
            agents_root.mkdir()
            vault_root = root / "vault"
            vault_root.mkdir()
            tasks = TaskStore(root / "tasks.sqlite3", agents_root=agents_root,
                              vault_root=vault_root)
            service = ServiceStore(root / "service.sqlite3", tasks)
            try:
                dependency = tasks.create_task(
                    purpose="canonical main synchronization",
                    repository="Saber5656/Agents",
                    acceptance_evidence=["main-sync readback"],
                )
                dependent = tasks.create_task(
                    purpose="dependent delivery",
                    repository="Saber5656/Agents",
                    dependencies=[dependency["id"]],
                    acceptance_evidence=["dependent worker reached"],
                )
                independent = tasks.create_task(
                    purpose="independent delivery",
                    repository="Saber5656/Agents",
                    acceptance_evidence=["independent worker reached"],
                )
                dependent_workspace = root / "dependent-workspace"
                independent_workspace = root / "independent-workspace"
                dependent_workspace.mkdir(); independent_workspace.mkdir()
                dependent_job = service.enroll(
                    dependent["id"], dependent_workspace, "dependent", "context",
                    retry_base=0,
                )
                independent_job = service.enroll(
                    independent["id"], independent_workspace, "independent", "context",
                    retry_base=0,
                )
                calls = []

                def before_sync(spec):
                    calls.append(spec["task_id"])
                    return {"status": "failed", "text": "independent reached before sync"}

                scheduler = Scheduler(service)
                first = scheduler.run_once(executor=before_sync)
                self.assertEqual(first["status"], "retry")
                self.assertEqual(calls, [independent["id"]])
                self.assertEqual(service.get_job(dependent_job["id"])["state"], "pending")
                self.assertEqual(service.get_job(independent_job["id"])["state"], "retry")
                self.assertEqual(tasks.get_task(dependency["id"])["execution_status"], "planned")

                lock_root = agents_root / ".local" / "service-locks"
                observed = sync_main(canonical, "main", merge_sha, str(remote),
                                     lock_root=lock_root)
                self.assertEqual(merge_sha, observed)
                self.assertEqual(merge_sha, git(canonical, "rev-parse", "HEAD"))
                receipt = vault_root / "main-sync-acceptance.json"
                receipt.write_text(json.dumps({
                    "repository": "Saber5656/Agents",
                    "merge_sha": merge_sha,
                    "remote_main": git(canonical, "rev-parse", "origin/main"),
                    "canonical_main": git(canonical, "rev-parse", "HEAD"),
                    "sync": "fast-forward",
                }))
                tasks.add_acceptance_evidence(
                    dependency["id"], "main-sync readback", verified=True)
                tasks.add_completion_evidence(dependency["id"], str(receipt))
                current = tasks.get_task(dependency["id"])
                tasks.update_task(dependency["id"], expected_version=current["version"],
                                  execution_status="verified")

                def after_sync(spec):
                    calls.append(spec["task_id"])
                    return {"status": "failed", "text": "dependent reached after sync"}

                second = scheduler.run_once(executor=after_sync)
                self.assertEqual(second["status"], "retry")
                self.assertEqual(calls, [independent["id"], dependent["id"]])
                self.assertEqual(service.get_job(dependent_job["id"])["attempts_count"], 1)
                self.assertEqual(service.get_job(dependent_job["id"])["state"], "retry")
                self.assertEqual(tasks.get_task(dependency["id"])["execution_status"], "verified")
                with sqlite3.connect(root / "service.sqlite3") as db:
                    self.assertEqual(2, db.execute("SELECT COUNT(*) FROM service_attempts").fetchone()[0])
            finally:
                service.close(); tasks.close()


if __name__ == "__main__":
    unittest.main()

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from harness.publication import selected_diff_digest, selected_tree_digest
from harness.service import ServiceStore
from harness.tasks import TaskStore


def _git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def publication_job(tmp_path):
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    _git(agents_root, "init", "-b", "main")
    _git(agents_root, "config", "user.name", "Fixture")
    _git(agents_root, "config", "user.email", "fixture@example.invalid")
    (agents_root / "tracked.txt").write_text("before\n")
    _git(agents_root, "add", "tracked.txt")
    _git(agents_root, "commit", "-m", "base")
    base = _git(agents_root, "rev-parse", "HEAD")
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(agents_root, "remote", "add", "origin", str(remote))
    _git(agents_root, "push", "origin", "main")
    workspace = tmp_path / "workspace"
    _git(agents_root, "worktree", "add", "-b", "task/change", str(workspace), base)
    (workspace / "tracked.txt").write_text("after\n")
    vault = tmp_path / "vault"
    vault.mkdir()
    tasks = TaskStore(tmp_path / "tasks.sqlite3", agents_root=agents_root, vault_root=vault)
    task = tasks.create_task(purpose="publication", repository="Saber5656/Agents")
    service = ServiceStore(tmp_path / "service.sqlite3", tasks)
    job = service.enroll(task["id"], workspace, "prompt", "context")
    yield service, tasks, task, job, agents_root, workspace, remote, base, vault
    service.close()
    tasks.close()


def _proposal(workspace, canonical, remote, base, vault):
    files = ["tracked.txt"]
    return {
        "repository": "Saber5656/Agents",
        "canonical_repo": str(canonical),
        "task_worktree": str(workspace),
        "files": files,
        "immutable_base": base,
        "commit_message": "Publish selected change",
        "remote": str(remote),
        "preimage_digest": selected_tree_digest(canonical, files),
        "diff_digest": selected_diff_digest(workspace, base, files),
    }


def _review(workspace, base, files, *, missing=None):
    diff = selected_diff_digest(workspace, base, files)
    value = {
        "status": "complete", "reviewed": True,
        "reviewed_head": _git(workspace, "rev-parse", "HEAD"),
        "reviewed_diff_digest": diff, "findings_complete": True,
        "decisions": [{"finding_id": "finding-1", "decision": "adopt",
                       "reason": "required change", "evidence": ["vault://review"],
                       "applied": True, "applied_evidence": ["vault://fix"]}],
    }
    if missing:
        value.pop(missing)
    return value


def test_host_rejects_missing_review_snapshot(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, "git@github.com:Saber5656/Agents.git", base, vault)
    review = _review(workspace, base, proposal["files"], missing="reviewed_head")
    with pytest.raises(ValueError, match="review head"):
        service._publish_proposal(job, task, proposal, review)


def test_host_binds_paths_receipt_and_ignores_worker_ci_metadata(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, "git@github.com:Saber5656/Agents.git", base, vault)
    proposal.update({"vault_receipt": str(Path("/tmp") / "attacker.json"),
                     "ci": {"status": "success"}})
    review = _review(workspace, base, proposal["files"])
    with mock.patch("harness.publication.publish_scoped",
                    return_value={"status": "published", "published_sha": "a" * 40}) as publish:
        result = service._publish_proposal(job, task, proposal, review)
    assert result["commit"] == "a" * 40
    spec = publish.call_args.args[0]
    assert spec["canonical_repo"] == str(canonical.resolve())
    assert spec["task_worktree"] == str(workspace.resolve())
    assert Path(spec["vault_receipt"]).parent == Path(job["run_dir"]).resolve()
    assert "ci" not in spec
    assert "ci_observer" not in spec


def test_host_accepts_bound_zero_finding_review_before_publication(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, "git@github.com:Saber5656/Agents.git", base, vault)
    review = _review(workspace, base, proposal["files"])
    review.update({"findings": [], "decisions": []})
    with mock.patch("harness.publication.publish_scoped",
                    return_value={"status": "published", "published_sha": "a" * 40}) as publish:
        result = service._publish_proposal(job, task, proposal, review)
    assert result["commit"] == "a" * 40
    assert publish.call_args.args[0]["review"]["findings"] == []
    assert publish.call_args.args[0]["review"]["decisions"] == []


def test_host_rejects_worker_selected_checkout(publication_job, tmp_path):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, "git@github.com:Saber5656/Agents.git", base, vault)
    proposal["canonical_repo"] = str(tmp_path / "attacker-checkout")
    review = _review(workspace, base, proposal["files"])
    with pytest.raises(ValueError, match="checkouts do not match"):
        service._publish_proposal(job, task, proposal, review)


def test_host_rejects_worker_selected_local_bare_remote(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, remote, base, vault)
    review = _review(workspace, base, proposal["files"])
    with pytest.raises(ValueError, match="authorized GitHub remote"):
        service._publish_proposal(job, task, proposal, review)


def test_pending_receipt_is_reconciled_without_restarting_verifier(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, "git@github.com:Saber5656/Agents.git", base, vault)
    review = _review(workspace, base, proposal["files"])
    service.run_once(executor=lambda _: {"status": "completed",
                                         "publication_proposal": proposal})
    assert service._start_verification(job["id"])
    receipt = Path(job["run_dir"])
    receipt.mkdir(parents=True, exist_ok=True)
    (receipt / "publication.json").write_text(json.dumps({
        "status": "pending", "published_sha": "a" * 40,
        "files": proposal["files"], "base": base,
        "preimage_digest": proposal["preimage_digest"],
        "diff_digest": proposal["diff_digest"], "review": review,
    }))
    verifier = mock.Mock()
    with mock.patch("harness.publication.publish_scoped",
                    return_value={"status": "pending", "published_sha": "a" * 40}):
        result = service.verify_with_agent(job["id"], verifier)
    assert result["status"] == "needs_verification"
    verifier.assert_not_called()


def test_published_receipt_requests_only_final_acceptance_review(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, "git@github.com:Saber5656/Agents.git", base, vault)
    review = _review(workspace, base, proposal["files"])
    service.run_once(executor=lambda _: {"status": "completed",
                                         "publication_proposal": proposal})
    assert service._start_verification(job["id"])
    receipt = Path(job["run_dir"])
    receipt.mkdir(parents=True, exist_ok=True)
    (receipt / "publication.json").write_text(json.dumps({
        "status": "published", "published_sha": "a" * 40,
        "files": proposal["files"], "base": base,
        "preimage_digest": proposal["preimage_digest"],
        "diff_digest": proposal["diff_digest"], "review": review,
    }))
    verifier = mock.Mock(return_value={"acceptance": False, "findings": []})
    with mock.patch("harness.publication.publish_scoped",
                    return_value={"status": "published", "published_sha": "a" * 40}), \
         mock.patch("harness.service.observe_publication", return_value={"commit": "a" * 40}), \
         mock.patch.object(service, "_publish_proposal") as publish:
        result = service.verify_with_agent(job["id"], verifier)
    assert result["status"] == "needs_verification"
    verifier.assert_called_once()
    assert verifier.call_args.args[0]["publication_readback"] == {"commit": "a" * 40}
    publish.assert_not_called()


def test_ci_failure_handoff_records_rebase_and_new_receipt_instruction(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    proposal = _proposal(workspace, canonical, "git@github.com:Saber5656/Agents.git", base, vault)
    review = _review(workspace, base, proposal["files"])
    service.run_once(executor=lambda _: {"status": "completed",
                                         "publication_proposal": proposal})
    assert service._start_verification(job["id"])
    receipt = Path(job["run_dir"])
    receipt.mkdir(parents=True, exist_ok=True)
    (receipt / "publication.json").write_text(json.dumps({
        "status": "failed", "published_sha": "a" * 40,
        "files": proposal["files"], "base": base,
        "preimage_digest": proposal["preimage_digest"],
        "diff_digest": proposal["diff_digest"], "review": review,
    }))
    verifier = mock.Mock()
    with mock.patch("harness.publication.publish_scoped",
                    return_value={"status": "failed", "published_sha": "a" * 40}):
        result = service.verify_with_agent(job["id"], verifier)
    assert result["status"] == "retry"
    verifier.assert_not_called()
    detail = service.get_job(job["id"])
    assert detail["state"] == "retry"
    assert "rebase" in detail["updates"][-1]["message"]
    assert "publication-2.json" in detail["updates"][-1]["message"]


def test_repair_instruction_is_passed_to_next_executor(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    # Move a claimed work item into the repair state as the host would after a
    # concrete publication conflict.
    service.run_once(executor=lambda _: {"status": "completed"})
    assert service._start_verification(job["id"])
    service._schedule_publication_repair(job["id"], "publication CI failed for SHA " + "a" * 40)
    seen = []
    service.run_once(executor=lambda spec: (seen.append(spec) or {"status": "failed"}))
    assert seen and "rebase" in seen[0]["updates"][-1]["message"]
    assert "immutable_base" in seen[0]["updates"][-1]["message"]


def test_repair_state_and_instruction_commit_atomically(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    service.run_once(executor=lambda _: {"status": "completed"})
    assert service._start_verification(job["id"])
    with service.tx() as conn:
        conn.execute("""CREATE TRIGGER fail_repair_update BEFORE INSERT ON service_updates
                        BEGIN SELECT RAISE(ABORT, 'injected repair update failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected repair update failure"):
        service._schedule_publication_repair(job["id"], "publication conflict")
    detail = service.get_job(job["id"])
    assert detail["state"] == "verifying"
    assert detail["attempts"][0]["status"] == "verifying"
    assert detail["updates"] == []
    with service.tx() as conn:
        conn.execute("DROP TRIGGER fail_repair_update")
    service._schedule_publication_repair(job["id"], "publication conflict")
    detail = service.get_job(job["id"])
    assert detail["state"] == "retry"
    assert "rebase" in detail["updates"][-1]["message"]


def test_ancestor_publication_readback_allows_dependent_task(publication_job):
    service, tasks, task, job, canonical, workspace, remote, base, vault = publication_job
    with service.tx() as conn:
        conn.execute("UPDATE service_jobs SET state='cancelled' WHERE id=?", (job["id"],))
    dependency = tasks.create_task(purpose="published ancestor", repository="Saber5656/Agents",
                                   acceptance_evidence=["check"])
    tasks.add_acceptance_evidence(dependency["id"], "check", verified=True)
    receipt = vault / "ancestor-acceptance.json"
    receipt.write_text(json.dumps({
        "review": {"acceptance": True, "findings": [],
                   "criteria": [{"criterion": "check", "verified": True,
                                  "evidence": "observed"}]},
        "publication_readback": {"repository": "Saber5656/Agents",
                                  "commit": "a" * 40, "main": "b" * 40,
                                  "commit_is_ancestor": True,
                                  "merge_base": "a" * 40,
                                  "mode": "direct_main"},
    }))
    current = tasks.get_task(dependency["id"])
    tasks.update_task(dependency["id"], expected_version=current["version"],
                      execution_status="verified")
    tasks.add_completion_evidence(dependency["id"], str(receipt))
    dependent = tasks.create_task(purpose="follows publication", repository="Saber5656/Agents",
                                  dependencies=[dependency["id"]])
    dep_job = service.enroll(dependent["id"], workspace, "prompt", "context")
    result = service.run_once(executor=lambda _: {"status": "completed"})
    assert result["status"] == "needs_verification"

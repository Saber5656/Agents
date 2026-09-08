"""Local bare-Git acceptance fixtures for scoped publication."""
import json
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from harness.publication import (
    PublicationError,
    selected_diff_digest,
    selected_tree_digest,
    publish_scoped,
)
import harness.publication as publication


def git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def fixture(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    git(canonical, "init", "-b", "main")
    git(canonical, "config", "user.name", "Fixture")
    git(canonical, "config", "user.email", "fixture@example.invalid")
    (canonical / "src").mkdir()
    (canonical / "src" / "selected.txt").write_text("before\n")
    (canonical / "src" / "unrelated.txt").write_text("untouched\n")
    git(canonical, "add", ".")
    git(canonical, "commit", "-m", "base")
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(canonical, "remote", "add", "origin", str(remote))
    git(canonical, "push", "origin", "main")
    base = git(canonical, "rev-parse", "HEAD")
    worktree = tmp_path / "task"
    git(canonical, "worktree", "add", "-b", "task/selected", str(worktree), base)
    vault = tmp_path / "vault"
    vault.mkdir()
    return canonical, worktree, remote, vault, base


def review(*, diff_digest=None, reviewed_head=None, applied=True):
    return {
        "status": "complete",
        "reviewed": True,
        "reviewed_diff_digest": diff_digest,
        "reviewed_head": reviewed_head,
        "findings_complete": True,
        "decisions": [{
            "finding_id": "finding-1",
            "decision": "adopt",
            "reason": "The selected change is required by the task.",
            "evidence": ["vault://review/receipt.json"],
            "applied": applied,
            "applied_evidence": ["vault://review/applied.json"] if applied else [],
        }],
    }


def make_spec(canonical, worktree, remote, vault, base, *, ci=None):
    files = ["src/selected.txt"]
    diff_digest = selected_diff_digest(worktree, base, files)
    return {
        "repository": "Saber5656/Agents",
        "canonical_repo": str(canonical),
        "task_worktree": str(worktree),
        "files": files,
        "preimage_digest": selected_tree_digest(canonical, files),
        "diff_digest": diff_digest,
        "review": review(diff_digest=diff_digest, reviewed_head=git(worktree, "rev-parse", "HEAD")),
        "commit_message": "Publish selected task change",
        "immutable_base": base,
        "vault_receipt": str(vault / "publication.json"),
        "remote": str(remote),
        "ci": ci,
    }


def test_publish_selected_files_and_read_back_remote(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    (worktree / "keep-untracked.txt").write_text("preserve\n")
    result = publish_scoped(make_spec(canonical, worktree, remote, vault, base))
    assert result["status"] == "published"
    published = result["published_sha"]
    assert git(canonical, "rev-parse", "HEAD") == published
    assert git(tmp_path := remote, "rev-parse", "refs/heads/main") == published
    assert (worktree / "keep-untracked.txt").read_text() == "preserve\n"
    assert json.loads((vault / "publication.json").read_text())["status"] == "published"


def test_privacy_rejection_leaves_worktree_and_main_unchanged(fixture):
    canonical, worktree, remote, vault, base = fixture
    before = git(canonical, "rev-parse", "HEAD")
    (worktree / "src" / "selected.txt").write_text("github_pat_" + "x" * 30 + "\n")
    with pytest.raises(PublicationError, match="privacy"):
        publish_scoped(make_spec(canonical, worktree, remote, vault, base))
    assert git(canonical, "rev-parse", "HEAD") == before
    assert git(worktree, "diff", "--cached") == ""


def test_privacy_rejects_private_key_and_generic_secret_assignments(fixture):
    canonical, worktree, remote, vault, base = fixture
    selected = worktree / "src" / "selected.txt"
    for index, value in enumerate(("-----BEGIN OPENSSH PRIVATE KEY-----\n", "api_key=secret-value-1234\n")):
        selected.write_text(value)
        spec = make_spec(canonical, worktree, remote, vault, base)
        spec["vault_receipt"] = str(vault / f"privacy-{index}.json")
        with pytest.raises(PublicationError, match="privacy"):
            publish_scoped(spec)


def test_unrelated_dirty_canonical_is_preserved_and_published(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    (canonical / "src" / "unrelated.txt").write_text("local edit\n")
    git(canonical, "add", "src/unrelated.txt")
    (canonical / "local.txt").write_text("keep local\n")
    before_status = git(canonical, "status", "--porcelain")
    result = publish_scoped(make_spec(canonical, worktree, remote, vault, base))
    assert result["status"] == "published"
    assert (canonical / "src" / "unrelated.txt").read_text() == "local edit\n"
    assert (canonical / "local.txt").read_text() == "keep local\n"
    assert git(canonical, "status", "--porcelain") == before_status


def test_selected_dirty_canonical_is_rejected_before_publication(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    (canonical / "src" / "selected.txt").write_text("local collision\n")
    with pytest.raises(PublicationError, match="selected"):
        publish_scoped(spec)
    assert git(canonical, "rev-parse", "HEAD") == base
    assert (canonical / "src" / "selected.txt").read_text() == "local collision\n"


def test_incomplete_review_blocks_before_git_mutation(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    spec["review"] = {"status": "incomplete", "decisions": []}
    with pytest.raises(PublicationError, match="review"):
        publish_scoped(spec)
    assert git(canonical, "rev-parse", "HEAD") == base


def test_pending_ci_does_not_claim_published(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    result = publish_scoped(make_spec(canonical, worktree, remote, vault, base,
                                      ci={"expected_sha": "wrong", "status": "pending"}))
    assert result["status"] == "pending"
    assert result["published_sha"] == git(canonical, "rev-parse", "HEAD")
    resumed = publish_scoped(make_spec(canonical, worktree, remote, vault, base,
                                       ci={"expected_sha": "wrong", "status": "pending"}))
    assert resumed["status"] == "pending"
    assert resumed["published_sha"] == result["published_sha"]


def test_repository_workflow_without_observed_checks_remains_pending():
    github = mock.Mock()
    github.api.return_value = {"total_count": 1}
    github.check_runs.return_value = []
    with mock.patch("harness.delivery.GitHub", return_value=github):
        assert publication._github_ci_status("Saber5656/Agents", "https://github.com/Saber5656/Agents.git", "a" * 40) == "pending"


def test_scp_github_remote_is_observed_as_production_not_local():
    github = mock.Mock()
    github.api.side_effect = [{"total_count": 1}, {"total_count": 0}]
    github.check_runs.return_value = [{"name": "test", "conclusion": "SUCCESS"}]
    github.required_checks.return_value = []
    with mock.patch("harness.delivery.GitHub", return_value=github):
        assert publication._github_ci_status(
            "Saber5656/Agents", "git@github.com:Saber5656/Agents.git", "a" * 40
        ) == "success"
    github.assert_not_called()


def test_unauthorized_github_remote_cannot_be_treated_as_local(fixture):
    canonical, worktree, remote, vault, base = fixture
    spec = make_spec(canonical, worktree, remote, vault, base)
    spec["remote"] = "git@github.com:attacker/Agents.git"
    with pytest.raises(PublicationError, match="authorized GitHub"):
        publication.publish_scoped(spec)


def test_required_check_must_be_present_and_successful():
    github = mock.Mock()
    github.api.return_value = {"total_count": 1}
    github.required_checks.return_value = [{"context": "test", "app_id": None}]
    github.check_runs.return_value = [{"name": "lint", "conclusion": "SUCCESS"}]
    with mock.patch("harness.delivery.GitHub", return_value=github):
        assert publication._github_ci_status("Saber5656/Agents", "https://github.com/Saber5656/Agents.git", "a" * 40) == "pending"
    github.check_runs.return_value = [{"name": "test", "conclusion": "FAILURE"}]
    with mock.patch("harness.delivery.GitHub", return_value=github):
        assert publication._github_ci_status("Saber5656/Agents", "https://github.com/Saber5656/Agents.git", "a" * 40) == "failed"


def test_selected_new_file_is_published_and_receipt_is_idempotent(fixture):
    canonical, worktree, remote, vault, base = fixture
    new_file = worktree / "src" / "new.txt"
    new_file.write_text("new source\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    spec["files"] = ["src/selected.txt", "src/new.txt"]
    spec["preimage_digest"] = selected_tree_digest(canonical, spec["files"])
    spec["diff_digest"] = selected_diff_digest(worktree, base, spec["files"])
    spec["review"]["reviewed_diff_digest"] = spec["diff_digest"]
    first = publish_scoped(spec)
    second = publish_scoped(spec)
    assert first["published_sha"] == second["published_sha"]
    assert git(canonical, "show", "--format=", "--name-only", first["published_sha"]) .splitlines() == ["src/new.txt"]


def test_explicit_remote_mismatch_is_rejected_before_commit(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    spec["remote"] = str(vault / "wrong.git")
    with pytest.raises(PublicationError, match="remote"):
        publish_scoped(spec)
    assert git(worktree, "diff", "--cached") == ""


def test_preimage_digest_distinguishes_missing_mode_and_symlink(fixture):
    canonical, worktree, remote, vault, base = fixture
    missing = selected_tree_digest(canonical, ["src/absent.txt"])
    (canonical / "src" / "absent.txt").write_text("<missing>")
    assert selected_tree_digest(canonical, ["src/absent.txt"]) != missing
    link = canonical / "src" / "link.txt"
    link.symlink_to("selected.txt")
    with pytest.raises(PublicationError, match="symlink"):
        selected_tree_digest(canonical, ["src/link.txt"])


def test_plain_ci_claim_is_not_accepted_as_observed_success(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base,
                     ci={"expected_sha": "will-match", "status": "success"})
    result = publish_scoped(spec)
    assert result["status"] == "pending"


def test_intermediate_secret_is_rejected_even_when_removed_before_publish(fixture):
    canonical, worktree, remote, vault, base = fixture
    selected = worktree / "src" / "selected.txt"
    selected.write_text("github_pat_" + "x" * 30 + "\n")
    git(worktree, "add", "src/selected.txt")
    git(worktree, "commit", "-m", "temporary secret")
    selected.write_text("after\n")
    git(worktree, "add", "src/selected.txt")
    git(worktree, "commit", "-m", "remove temporary secret")
    spec = make_spec(canonical, worktree, remote, vault, base)
    spec["review"]["reviewed_head"] = git(worktree, "rev-parse", "HEAD")
    with pytest.raises(PublicationError, match="privacy"):
        publish_scoped(spec)


def test_ci_observer_must_match_published_sha(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    observed = []
    def observe(sha):
        observed.append(sha)
        return {"head_sha": sha, "status": "success"}
    result = publish_scoped(make_spec(canonical, worktree, remote, vault, base,
                                      ci={"provider": "github"}) | {"ci_observer": observe})
    assert result["status"] == "published"
    assert observed == [result["published_sha"]]


def test_review_must_record_applied_evidence_for_adopted_finding(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    spec["review"]["decisions"][0]["applied"] = False
    spec["review"]["decisions"][0]["applied_evidence"] = []
    with pytest.raises(PublicationError, match="applied-fix"):
        publish_scoped(spec)


def test_merge_then_receipt_failure_resumes_without_duplicate_commit(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    import harness.publication as publication
    original = publication._save
    interrupted = {"value": False}
    def fail_after_merge(path, payload):
        if payload.get("stage") == "main_synced" and not interrupted["value"]:
            interrupted["value"] = True
            raise RuntimeError("simulated interruption")
        return original(path, payload)
    with mock.patch.object(publication, "_save", side_effect=fail_after_merge):
        with pytest.raises(RuntimeError):
            publish_scoped(spec)
    assert git(canonical, "rev-parse", "HEAD") != base
    resumed = publish_scoped(spec)
    assert resumed["status"] == "published"
    assert git(remote, "rev-parse", "refs/heads/main") == resumed["published_sha"]


def test_commit_then_receipt_failure_reconciles_existing_task_commit(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    import harness.publication as publication
    original = publication._save
    interrupted = {"value": False}
    def fail_after_commit(path, payload):
        if payload.get("stage") == "committed" and not interrupted["value"]:
            interrupted["value"] = True
            raise RuntimeError("simulated interruption")
        return original(path, payload)
    with mock.patch.object(publication, "_save", side_effect=fail_after_commit):
        with pytest.raises(RuntimeError):
            publish_scoped(spec)
    task_head = git(worktree, "rev-parse", "HEAD")
    resumed = publish_scoped(spec)
    assert resumed["status"] == "published"
    assert resumed["published_sha"] == task_head


def test_resume_rechecks_origin_binding_before_push(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base,
                     ci={"expected_sha": "wrong", "status": "pending"})
    first = publish_scoped(spec)
    wrong = vault / "wrong-origin.git"
    git(tmp_path := vault, "init", "--bare", str(wrong))
    git(canonical, "remote", "set-url", "origin", str(wrong))
    with pytest.raises(PublicationError, match="origin"):
        publish_scoped(spec)
    assert first["status"] == "pending"


def test_staged_file_change_is_detected_before_commit(fixture):
    canonical, worktree, remote, vault, base = fixture
    selected = worktree / "src" / "selected.txt"
    selected.write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    import harness.publication as publication
    original = publication._run
    def race(cwd, *args, **kwargs):
        result = original(cwd, *args, **kwargs)
        if args[:2] == ("add", "--"):
            selected.write_text("changed after staging\n")
        return result
    with mock.patch.object(publication, "_run", side_effect=race):
        with pytest.raises(PublicationError, match="after staging"):
            publish_scoped(spec)
    assert git(canonical, "rev-parse", "HEAD") == base


def test_published_receipt_rechecks_current_checkout(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    first = publish_scoped(spec)
    (canonical / "post.txt").write_text("changed\n")
    git(canonical, "add", "post.txt")
    git(canonical, "commit", "-m", "unrelated local change")
    with pytest.raises(PublicationError):
        publish_scoped(spec)


def test_published_receipt_does_not_hide_new_pending_ci(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base)
    first = publish_scoped(spec)
    assert first["status"] == "published"
    resumed = publish_scoped(spec | {"ci": {"provider": "github"}})
    assert resumed["status"] == "pending"
    assert json.loads((vault / "publication.json").read_text())["status"] == "pending"


def test_pending_receipt_reconciles_when_main_has_an_unrelated_advance(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base,
                     ci={"provider": "github"})
    first = publish_scoped(spec)
    assert first["status"] == "pending"
    (canonical / "src" / "unrelated.txt").write_text("parallel\n")
    git(canonical, "add", "src/unrelated.txt")
    git(canonical, "commit", "-m", "parallel publication")
    git(canonical, "push", "origin", "main")
    resumed = publish_scoped(spec)
    assert resumed["status"] == "pending"
    assert resumed["published_sha"] == first["published_sha"]
    assert git(canonical, "rev-parse", "HEAD") != resumed["published_sha"]


def test_ci_resume_allows_refreshed_review_evidence_for_same_binding(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base,
                     ci={"provider": "github"})
    first = publish_scoped(spec)
    refreshed = dict(spec)
    refreshed["review"] = json.loads(json.dumps(spec["review"]))
    refreshed["review"]["decisions"][0]["reason"] = "same decision, new host observation wording"
    refreshed["review"]["decisions"][0]["evidence"] = ["vault://review/refreshed"]
    resumed = publish_scoped(refreshed)
    assert first["status"] == resumed["status"] == "pending"


def test_ci_resume_rejects_changed_review_decision(fixture):
    canonical, worktree, remote, vault, base = fixture
    (worktree / "src" / "selected.txt").write_text("after\n")
    spec = make_spec(canonical, worktree, remote, vault, base,
                     ci={"provider": "github"})
    publish_scoped(spec)
    changed = dict(spec)
    changed["review"] = json.loads(json.dumps(spec["review"]))
    changed["review"]["decisions"][0]["decision"] = "reject"
    with pytest.raises(PublicationError, match="different publication unit"):
        publish_scoped(changed)

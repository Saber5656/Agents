"""Scoped, review-gated publication of one task unit to ``main``.

This module intentionally has a small surface.  It is a host-side executor: it
does not discover work, create issues, or bypass repository protection.  The
receipt is the recovery record when a process stops between Git operations.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from typing import Any


class PublicationError(ValueError):
    """An input, precondition, or recoverable publication error."""


_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SAFE_REL = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*\\).+")
_SECRET = re.compile(
    rb"(?:github_pat_|ghp_|gho_|ghs_|ghr_|sk-[A-Za-z0-9]|AKIA[0-9A-Z]{16}|"
    rb"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----|"
    rb"(?i:bearer\s+[A-Za-z0-9._~+/=-]{16,})|"
    rb"(?i:(?:api[_-]?key|secret|token|password|passwd|authorization)\s*[:=]\s*[^\s]{8,}))"
)
_PERSONAL_PATH = re.compile(rb"/(?:Users|home)/[^/\s]+")


def _json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validated_files(files: list[str] | tuple[str, ...]) -> list[str]:
    if not isinstance(files, (list, tuple)) or not files or not all(isinstance(v, str) for v in files) or len(set(files)) != len(files):
        raise PublicationError("selected files must be unique safe relative paths")
    if any(not isinstance(v, str) or not _SAFE_REL.match(v) or v.endswith("/") or Path(v) == Path(".") or any(ord(ch) < 32 for ch in v) for v in files):
        raise PublicationError("selected files must be unique safe relative paths")
    return sorted(files)


def selected_tree_digest(root: str | os.PathLike[str], files: list[str] | tuple[str, ...]) -> str:
    """Hash selected file paths and bytes, including missing-file markers."""
    hasher = hashlib.sha256()
    base = Path(root)
    for name in _validated_files(files):
        path = base / name
        hasher.update(name.encode("utf-8") + b"\0")
        try:
            info = path.lstat()
        except FileNotFoundError:
            hasher.update(b"MISSING\0")
            continue
        if stat.S_ISLNK(info.st_mode):
            raise PublicationError("selected path must not be a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise PublicationError("selected path is not a regular file")
        data = path.read_bytes()
        hasher.update(b"FILE\0" + str(stat.S_IMODE(info.st_mode)).encode() + b"\0")
        hasher.update(len(data).to_bytes(8, "big") + data)
    return hasher.hexdigest()


def _run(cwd: Path, *args: str, check: bool = True, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicationError(f"git operation unavailable: {type(exc).__name__}") from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise PublicationError("git operation failed" + (f": {detail[-1][:200]}" if detail else ""))
    return result


def _out(cwd: Path, *args: str) -> str:
    return _run(cwd, *args).stdout.decode("utf-8", "replace").strip()


def _names(cwd: Path, *args: str) -> list[str]:
    data = _run(cwd, *args).stdout
    return [part.decode("utf-8", "surrogateescape") for part in data.split(b"\0") if part]


def selected_diff_digest(root: str | os.PathLike[str], base: str, files: list[str] | tuple[str, ...]) -> str:
    """Hash selected path content and modes against ``base``.

    The representation is independent of the index, so a pre-commit digest
    remains identical after the selected untracked files are staged.
    """
    if not isinstance(base, str) or not _OID.fullmatch(base):
        raise PublicationError("diff base must be a full commit id")
    worktree = Path(root)
    selected = _validated_files(files)
    hasher = hashlib.sha256()
    for name in selected:
        path = worktree / name
        base_result = _run(worktree, "ls-tree", base, "--", name, check=False)
        base_line = base_result.stdout.splitlines()[0].decode("utf-8", "replace") if base_result.stdout.splitlines() else ""
        base_mode = base_line.split(None, 1)[0] if base_line else "MISSING"
        base_data = _run(worktree, "show", f"{base}:{name}", check=False).stdout if base_line else b""
        hasher.update(name.encode("utf-8") + b"\0BASE\0" + base_mode.encode() + b"\0" + len(base_data).to_bytes(8, "big") + base_data)
        try:
            info = path.lstat()
        except FileNotFoundError:
            hasher.update(b"CURRENT\0MISSING\0")
            continue
        if stat.S_ISLNK(info.st_mode):
            raise PublicationError("selected path must not be a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise PublicationError("selected path is not a regular file")
        data = path.read_bytes()
        hasher.update(b"CURRENT\0FILE\0" + str(stat.S_IMODE(info.st_mode)).encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return hasher.hexdigest()


def _status(cwd: Path) -> list[str]:
    return _names(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all")


def _status_overlaps(entries: list[str], files: list[str]) -> bool:
    selected = set(files)
    rename_source = False
    for entry in entries:
        if len(entry) >= 4 and entry[3:] in selected:
            return True
        if rename_source and entry in selected:
            return True
        rename_source = len(entry) >= 2 and entry[:2] in {"R ", " R", "C ", " C"}
    return False


def _is_privacy_safe(path: Path, data: bytes) -> bool:
    if _SECRET.search(data) or _PERSONAL_PATH.search(data):
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    # A host path in a commit is just as unsafe as one in a source file.
    return not _PERSONAL_PATH.search(text.encode())


def _privacy_history(root: Path, base: str, head: str, files: list[str]) -> None:
    """Check every unpublished commit, including paths removed later.

    Reviewing only ``base..head`` misses a secret that was introduced and
    removed by two unpublished commits.  Inspect each commit's complete patch
    and metadata so the content that would actually be sent to the remote is
    safe as a history, rather than merely safe as a final tree.
    """
    commits = _out(root, "rev-list", "--reverse", f"{base}..{head}").splitlines()
    for commit in commits:
        diff = _run(root, "show", "--format=", "--binary", "--no-renames", commit).stdout
        message = _run(root, "show", "-s", "--format=fuller", commit).stdout
        if (not _is_privacy_safe(Path(f"commit-{commit}"), diff)
                or not _is_privacy_safe(Path(f"commit-message-{commit}"), message)):
            raise PublicationError("privacy check rejected commit history")


def _validate_review(review: Any) -> None:
    if not isinstance(review, dict) or review.get("status") not in {"complete", "completed", "approved"} or not review.get("reviewed"):
        raise PublicationError("review is not finalized")
    decisions = review.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise PublicationError("review has no decisions")
    if not isinstance(review.get("reviewed_diff_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", review["reviewed_diff_digest"]):
        raise PublicationError("review is not bound to a selected diff")
    if not isinstance(review.get("reviewed_head"), str) or not _OID.fullmatch(review["reviewed_head"]):
        raise PublicationError("review is not bound to a worktree head")
    if review.get("findings_complete") is not True:
        raise PublicationError("review findings are not fully accounted for")
    ids: set[str] = set()
    for item in decisions:
        if not isinstance(item, dict) or not isinstance(item.get("finding_id"), str):
            raise PublicationError("review finding is invalid")
        ident = item["finding_id"]
        if ident in ids or item.get("decision") not in {"adopt", "reject", "separate"}:
            raise PublicationError("review decisions are not unique and explicit")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise PublicationError("review decision lacks reason")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(v, str) and v for v in evidence):
            raise PublicationError("review decision lacks evidence")
        if item["decision"] == "adopt":
            applied_evidence = item.get("applied_evidence")
            if item.get("applied") is not True or not isinstance(applied_evidence, list) or not applied_evidence:
                raise PublicationError("adopted review finding lacks applied-fix evidence")
        ids.add(ident)


def _validate_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise PublicationError("publication specification is required")
    if spec.get("repository") != "Saber5656/Agents":
        raise PublicationError("repository is not the authorized Agents repository")
    required = ("canonical_repo", "task_worktree", "files", "preimage_digest", "diff_digest", "review", "commit_message", "immutable_base", "vault_receipt", "remote")
    if any(not spec.get(key) for key in required):
        raise PublicationError("publication specification is incomplete")
    files = spec["files"]
    if not isinstance(files, list) or not files or not all(isinstance(v, str) for v in files) or len(set(files)) != len(files) or any(not _SAFE_REL.match(v) or v.endswith("/") or Path(v) == Path(".") for v in files):
        raise PublicationError("selected files must be unique safe relative paths")
    if not isinstance(spec["commit_message"], str) or not spec["commit_message"].strip() or "\n" in spec["commit_message"]:
        raise PublicationError("commit message must be a single non-empty line")
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", spec["commit_message"]):
        raise PublicationError("commit message must be authored in English")
    base = spec["immutable_base"]
    if not isinstance(base, str) or not _OID.fullmatch(base):
        raise PublicationError("immutable base must be a full commit id")
    if not isinstance(spec["preimage_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", spec["preimage_digest"]):
        raise PublicationError("preimage digest is invalid")
    if not isinstance(spec["diff_digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", spec["diff_digest"]):
        raise PublicationError("diff digest is invalid")
    if not isinstance(spec["vault_receipt"], str):
        raise PublicationError("Vault receipt must be an explicit absolute path")
    receipt = Path(spec["vault_receipt"]).expanduser()
    if not receipt.is_absolute() or receipt.name in {"", ".", ".."}:
        raise PublicationError("Vault receipt must be an explicit absolute path")
    for key in ("canonical_repo", "task_worktree"):
        if not isinstance(spec[key], str) or not Path(spec[key]).is_absolute():
            raise PublicationError(f"{key} must be an explicit absolute path")
    if not isinstance(spec["remote"], str) or not spec["remote"].strip():
        raise PublicationError("remote must be explicit")
    if (_is_nonlocal_remote(spec["remote"])
            and not (spec["remote"].startswith("file://")
                     or _authorized_github_remote(spec["repository"], spec["remote"]))):
        raise PublicationError("remote is not the authorized GitHub repository")
    _validate_review(spec["review"])
    return spec


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise PublicationError("publication receipt path must not be a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)


@contextlib.contextmanager
def _unit_lock(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise PublicationError("publication lock path must not be a symlink")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    stream = os.fdopen(fd, "a+")
    try:
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


def _remote_sha(canonical: Path) -> str | None:
    try:
        result = _run(canonical, "ls-remote", "origin", "refs/heads/main", check=False)
    except PublicationError:
        return None
    if result.returncode:
        return None
    line = result.stdout.decode("utf-8", "replace").splitlines()
    if not line:
        return None
    value = line[0].split("\t", 1)[0]
    return value if _OID.fullmatch(value) else None


def _remote_matches(canonical: Path, expected: str) -> bool:
    actuals = [_out(canonical, "remote", "get-url", "origin"), _out(canonical, "remote", "get-url", "--push", "origin")]
    def same(actual: str) -> bool:
        if actual == expected:
            return True
        if expected.startswith("file://"):
            return os.path.realpath(actual.removeprefix("file://")) == os.path.realpath(expected.removeprefix("file://"))
        if "://" not in expected and "://" not in actual:
            return os.path.realpath(actual) == os.path.realpath(expected)
        return False
    return all(same(actual) for actual in actuals)


def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _ci_status(ci: Any, sha: str, observer=None) -> str:
    if ci is None:
        return "not_configured"
    if observer is not None:
        if not callable(observer):
            return "pending"
        try:
            observed = observer(sha)
        except Exception:
            return "pending"
        if not isinstance(observed, dict) or observed.get("sha", observed.get("head_sha")) != sha:
            return "pending"
        state = observed.get("status")
        return state if state in {"success", "failed"} else "pending"
    if not isinstance(ci, dict):
        return "pending"
    # A caller-supplied status is metadata, not proof that GitHub observed the
    # commit.  Production callers must provide an observer backed by GitHub.
    return "pending"


def _authorized_github_remote(repository: str, remote: str) -> bool:
    """Return whether *remote* is an allowed URL for the production repo."""
    return remote in {
        f"git@github.com:{repository}.git",
        f"ssh://git@github.com/{repository}.git",
        f"https://github.com/{repository}.git",
        f"https://github.com/{repository}",
    }


def _looks_like_github_remote(remote: str) -> bool:
    return remote.startswith(("git@github.com:", "ssh://git@github.com/", "https://github.com/"))


def _is_nonlocal_remote(remote: str) -> bool:
    """Recognize URL/scp remotes that cannot be local bare test fixtures."""
    return remote.startswith(("file://", "http://", "https://", "ssh://", "git://")) or bool(
        re.match(r"^[^/\s@]+@[^/\s:]+:", remote)
    )


def _github_ci_status(repository: str, remote: str, sha: str) -> str:
    """Observe workflow/check state for a real GitHub remote.

    Local bare remotes are explicit test adapters and have no GitHub workflow
    service. A real remote with workflows requires an observed successful
    check set; missing, pending, or failed checks remain incomplete.
    """
    # A scp-style SSH URL has no ``://``.  Only the explicit production
    # allowlist may reach the GitHub observer; local fixtures remain outside
    # this path and can use the explicit callable adapter in ``_ci_status``.
    if not _authorized_github_remote(repository, remote):
        # Never downgrade a GitHub-looking but unauthorized remote to a local
        # ``not_configured`` result.  The publication validator rejects it;
        # this helper remains conservative when called directly.
        return "pending" if _looks_like_github_remote(remote) else "not_configured"
    try:
        from .delivery import GitHub
        github = GitHub(repository)
        workflows = github.api("actions/workflows?per_page=100")
        if not isinstance(workflows, dict) or int(workflows.get("total_count", 0)) == 0:
            return "not_configured"
        runs = github.check_runs(sha)
        if not isinstance(runs, list) or not runs:
            return "pending"
        required = github.required_checks("main")
        by_name = {}
        for run in runs:
            if not isinstance(run, dict):
                continue
            name = run.get("name") or run.get("context")
            if name:
                by_name.setdefault(name, []).append(run)
            raw_conclusion = run.get("conclusion")
            # GitHub check-runs use ``conclusion: null`` while a run is
            # queued/in progress; do not turn Python's ``None`` into the
            # terminal-looking string ``NONE``.
            if raw_conclusion is None:
                raw_conclusion = run.get("status", "")
            conclusion = str(raw_conclusion).upper()
            if conclusion not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                return "pending" if conclusion in {"", "QUEUED", "IN_PROGRESS", "PENDING"} else "failed"
        for requirement in required:
            matches = by_name.get(requirement.get("context"), [])
            if requirement.get("app_id") not in (None, -1):
                matches = [run for run in matches if (run.get("app") or {}).get("id") == requirement["app_id"]]
            if not matches:
                return "pending"
            # Required protection rules need an actual successful check-run
            # from the required producer.  Neutral/skipped are acceptable for
            # unrelated optional runs, but cannot satisfy a required check.
            if not any(str(run.get("conclusion", "")).upper() == "SUCCESS" for run in matches):
                if any(str(run.get("conclusion") or run.get("status", "")).upper()
                       in {"", "QUEUED", "IN_PROGRESS", "PENDING"} for run in matches):
                    return "pending"
                return "failed"
        return "success"
    except Exception:
        return "pending"


def _save(receipt: Path, payload: dict[str, Any]) -> None:
    payload["updated_at"] = time.time()
    _atomic_json(receipt, payload)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = None
                value = json.load(stream)
        finally:
            if fd is not None:
                os.close(fd)
    except (OSError, ValueError, TypeError) as exc:
        raise PublicationError("Vault receipt is unreadable") from exc
    if not isinstance(value, dict):
        raise PublicationError("Vault receipt has an invalid shape")
    return value


def publish_scoped(spec: dict[str, Any]) -> dict[str, Any]:
    """Publish one reviewed selected-file unit and return a durable outcome.

    The production path always invokes the local Git executable and therefore
    retains Git hooks and repository protection behaviour. Tests should use a
    disposable local bare remote rather than replacing these operations.
    """
    spec = _validate_spec(spec)
    canonical = Path(spec["canonical_repo"]).resolve()
    worktree = Path(spec["task_worktree"]).resolve()
    receipt = Path(spec["vault_receipt"]).expanduser()
    lock = receipt.with_name(receipt.name + ".lock")
    canonical_lock = canonical.parent / f".{canonical.name}.publication.lock"
    files = list(spec["files"])
    if receipt.is_symlink() or lock.is_symlink():
        raise PublicationError("Vault receipt paths must not be symlinks")
    # The canonical checkout is the shared serialization boundary.  A lock
    # derived only from the caller's receipt would allow two receipts to merge
    # or push the same main checkout concurrently.
    with _unit_lock(canonical_lock), _unit_lock(lock):
        # Preimage/diff and CI are observations that legitimately change while
        # a receipt is resumed.  Identity is the semantic publication unit.
        review = spec["review"]
        # Evidence prose and provider wording may be refreshed while CI is
        # pending.  The publication identity is the host-bound review state:
        # selected diff, review status, and each finding's decision.  A change
        # to those decisions invalidates the receipt; a re-review with the
        # same decisions does not.
        identity_review = {
            key: review[key] for key in ("status", "reviewed", "reviewed_diff_digest", "findings_complete")
            if key in review
        }
        identity_review["decisions"] = [
            {key: item[key] for key in ("finding_id", "decision", "applied") if key in item}
            for item in review["decisions"]
        ]
        # The reviewed worktree head advances after the executor creates the
        # task commit.  Keep it out of the identity while requiring the host
        # snapshot at service level before the first publication.
        identity_review.pop("reviewed_head", None)
        request_digest = _json_digest({k: spec[k] for k in ("repository", "canonical_repo", "task_worktree", "files", "commit_message", "immutable_base", "remote")} | {"review": identity_review})
        existing: dict[str, Any] | None = None
        if receipt.exists():
            existing = _read_json(receipt)
            if existing.get("request_digest") != request_digest:
                raise PublicationError("receipt belongs to a different publication unit")
            if existing.get("status") in {"published", "success"}:
                published = existing.get("published_sha")
                if not isinstance(published, str) or not _OID.fullmatch(published):
                    raise PublicationError("published receipt lacks a valid commit identity")
                if (not canonical.is_dir() or _out(canonical, "branch", "--show-current") != "main"
                        or _status_overlaps(_status(canonical), existing.get("files", files))):
                    raise PublicationError("published receipt canonical checkout no longer verifies")
                local_head = _out(canonical, "rev-parse", "HEAD")
                if (not _remote_matches(canonical, spec["remote"])
                        or (_run(canonical, "merge-base", "--is-ancestor", published, local_head, check=False).returncode
                            if local_head != published else 0)):
                    raise PublicationError("published receipt origin or canonical HEAD no longer verifies")
                remote_head = _remote_sha(canonical)
                if remote_head != local_head:
                    raise PublicationError("published receipt remote readback no longer verifies")
                observed_ci = (_ci_status(spec.get("ci"), published, spec.get("ci_observer"))
                               if spec.get("ci") is not None or spec.get("ci_observer") is not None
                               else _github_ci_status(spec["repository"], spec["remote"], published))
                if observed_ci in {"pending", "failed"} and existing.get("status") in {"published", "success"}:
                    # A stale receipt must not turn a newly pending or failed
                    # check set into a successful/not-configured result.
                    existing.update({"status": observed_ci, "ci": observed_ci})
                    _save(receipt, existing)
                return existing
        state: dict[str, Any] = existing or {"schema": 1, "request_digest": request_digest, "status": "planned", "attempts": 0}
        state["attempts"] = int(state.get("attempts", 0)) + 1
        state["files"] = files
        state["review"] = (existing or {}).get("review", spec["review"])
        state["base"] = spec["immutable_base"]
        state["preimage_digest"] = spec["preimage_digest"]
        state["diff_digest"] = spec["diff_digest"]
        _save(receipt, state)

        # A receipt that reached main sync is already past the preimage CAS.
        # On restart, re-check the recorded commit and remote before touching
        # any task worktree; this is what makes a pending CI receipt resumable.
        resume_head = state.get("task_head") if state.get("stage") in {"main_synced", "pushed", "push_unknown"} else None
        if resume_head:
            if not canonical.is_dir():
                raise PublicationError("canonical checkout is unavailable for receipt recovery")
            if (_out(canonical, "branch", "--show-current") != "main"
                    or _status_overlaps(_status(canonical), state.get("files", files))):
                raise PublicationError("canonical checkout is unavailable for receipt recovery")
            if not _remote_matches(canonical, spec["remote"]):
                raise PublicationError("configured origin no longer matches explicit remote")
            canonical_head = _out(canonical, "rev-parse", "HEAD")
            advanced_main = canonical_head != resume_head
            if advanced_main and _run(canonical, "merge-base", "--is-ancestor", resume_head, canonical_head, check=False).returncode:
                raise PublicationError("receipt commit is not an ancestor of canonical main")
            remote_sha = _remote_sha(canonical)
            if advanced_main:
                if remote_sha != canonical_head:
                    state.update({"status": "incomplete", "stage": "push_unknown", "published_sha": resume_head, "reason": "canonical main advanced but remote readback is not synchronized"})
                    _save(receipt, state)
                    return state
            elif remote_sha != resume_head:
                try:
                    _run(canonical, "push", "origin", "main", timeout=120)
                except PublicationError:
                    remote_sha = _remote_sha(canonical)
                    if remote_sha != resume_head:
                        state.update({"status": "incomplete", "stage": "push_unknown", "published_sha": resume_head, "reason": "push outcome could not be reconciled"})
                        _save(receipt, state)
                        return state
            expected_remote = canonical_head if advanced_main else resume_head
            if _remote_sha(canonical) != expected_remote:
                state.update({"status": "incomplete", "stage": "push_unknown", "published_sha": resume_head, "reason": "remote did not read back expected commit"})
                _save(receipt, state)
                return state
            ci_state = (_ci_status(spec.get("ci"), resume_head, spec.get("ci_observer"))
                        if spec.get("ci") is not None or spec.get("ci_observer") is not None
                        else _github_ci_status(spec["repository"], spec["remote"], resume_head))
            state.update({"stage": "pushed", "published_sha": resume_head, "ci": ci_state, "status": "published" if ci_state in {"not_configured", "success"} else ci_state})
            _save(receipt, state)
            return state

        if not canonical.is_dir() or not worktree.is_dir():
            raise PublicationError("canonical checkout or task worktree is missing")
        if canonical == worktree:
            raise PublicationError("canonical checkout and task worktree must be distinct")
        for name in files:
            if not _within(worktree, worktree / name) or (canonical / name).is_symlink() or (worktree / name).is_symlink():
                raise PublicationError("selected file escapes its checkout")
        if _out(canonical, "branch", "--show-current") != "main" or _out(worktree, "branch", "--show-current") in {"", "main", "master"}:
            raise PublicationError("canonical checkout must be main and task worktree must be a task branch")
        canonical_dirty = _status(canonical)
        if _status_overlaps(canonical_dirty, files):
            raise PublicationError("canonical checkout overlaps selected files")
        state["canonical_dirty_before"] = canonical_dirty
        _save(receipt, state)
        if not _remote_matches(canonical, spec["remote"]):
            raise PublicationError("configured origin does not match explicit remote")
        if not _run(canonical, "cat-file", "-e", f"{spec['immutable_base']}^{{commit}}", check=False).returncode == 0:
            raise PublicationError("immutable base is not a commit in canonical checkout")
        canonical_head = _out(canonical, "rev-parse", "HEAD")
        resumed_commit = state.get("stage") == "committed" and state.get("task_head")
        if canonical_head != spec["immutable_base"] and not (resumed_commit and canonical_head == resumed_commit):
            raise PublicationError("canonical main moved from immutable base")
        ancestor = _run(worktree, "merge-base", "--is-ancestor", spec["immutable_base"], "HEAD", check=False)
        if ancestor.returncode:
            raise PublicationError("task worktree is not based on immutable base")
        post_commit_resume = bool(state.get("stage") == "committed" and state.get("task_head") and _out(canonical, "rev-parse", "HEAD") == state.get("task_head"))
        if not post_commit_resume and selected_tree_digest(canonical, files) != spec["preimage_digest"]:
            raise PublicationError("selected-file preimage changed")
        if selected_diff_digest(worktree, spec["immutable_base"], files) != spec["diff_digest"]:
            raise PublicationError("selected-file diff changed")
        if not state.get("task_head"):
            reviewed_head = spec["review"]["reviewed_head"]
            current_task_head = _out(worktree, "rev-parse", "HEAD")
            if current_task_head != reviewed_head and _out(worktree, "log", "-1", "--format=%B").strip() != spec["commit_message"]:
                raise PublicationError("review is not bound to the current task head")
            if spec["review"]["reviewed_diff_digest"] != spec["diff_digest"]:
                raise PublicationError("review is not bound to the selected diff")
        for name in files:
            path = worktree / name
            if path.exists() and not _is_privacy_safe(path, path.read_bytes()):
                raise PublicationError("privacy check rejected selected file")
        if not _is_privacy_safe(Path(spec["commit_message"]), spec["commit_message"].encode()):
            raise PublicationError("privacy check rejected commit message")

        changed = _names(worktree, "diff", "--name-only", "-z", "--", *files)
        staged = _names(worktree, "diff", "--cached", "--name-only", "-z", "--")
        selected_untracked = any(item[3:] in files for item in _status(worktree) if item.startswith("?? "))
        if any(name not in files for name in staged):
            raise PublicationError("pre-existing staged files are outside selected unit")
        all_changes = _names(worktree, "diff", "--name-only", "-z", spec["immutable_base"], "--")
        if any(name not in files for name in all_changes):
            raise PublicationError("task worktree contains unselected committed changes")
        task_head = state.get("task_head")
        if task_head:
            if _out(worktree, "rev-parse", "HEAD") != task_head:
                raise PublicationError("task worktree changed after receipt commit")
        elif changed or staged or selected_untracked:
            # Recheck both CAS inputs immediately before staging/commit.  A
            # concurrent writer may have changed the worktree since the first
            # precondition read.
            if selected_tree_digest(canonical, files) != spec["preimage_digest"] or selected_diff_digest(worktree, spec["immutable_base"], files) != spec["diff_digest"]:
                raise PublicationError("selected unit changed before commit")
            _run(worktree, "add", "--", *files)
            if selected_diff_digest(worktree, spec["immutable_base"], files) != spec["diff_digest"]:
                raise PublicationError("selected unit changed after staging")
            staged_after_add = _names(worktree, "diff", "--cached", "--name-only", "-z", "--")
            if any(name not in files for name in staged_after_add):
                raise PublicationError("staging included an unselected file")
            _run(worktree, "commit", "-m", spec["commit_message"])
            task_head = _out(worktree, "rev-parse", "HEAD")
            if selected_diff_digest(worktree, spec["immutable_base"], files) != spec["diff_digest"]:
                raise PublicationError("committed selected content differs from reviewed diff")
            committed_changes = _names(worktree, "diff", "--name-only", "-z", spec["immutable_base"], "--")
            if any(name not in files for name in committed_changes):
                raise PublicationError("commit included an unselected file")
            state.update({"stage": "committed", "task_head": task_head})
            _save(receipt, state)
        else:
            task_head = _out(worktree, "rev-parse", "HEAD")
            if task_head == spec["immutable_base"]:
                raise PublicationError("selected unit has no changes")
            state.update({"stage": "committed", "task_head": task_head})
            _save(receipt, state)

        _privacy_history(worktree, spec["immutable_base"], task_head, files)

        current = _out(canonical, "rev-parse", "HEAD")
        if current == task_head:
            pass
        elif current == spec["immutable_base"]:
            _run(canonical, "merge", "--ff-only", task_head)
        else:
            raise PublicationError("canonical main moved before merge")
        state.update({"stage": "main_synced", "main_sha": task_head})
        _save(receipt, state)

        remote_sha = _remote_sha(canonical)
        if remote_sha != task_head:
            try:
                _run(canonical, "push", "origin", "main", timeout=120)
            except PublicationError:
                remote_sha = _remote_sha(canonical)
                if remote_sha != task_head:
                    state.update({"status": "incomplete", "stage": "push_unknown", "published_sha": task_head, "reason": "push outcome could not be reconciled"})
                    _save(receipt, state)
                    return state
        if _remote_sha(canonical) != task_head:
            state.update({"status": "incomplete", "stage": "push_unknown", "published_sha": task_head, "reason": "remote did not read back expected commit"})
            _save(receipt, state)
            return state
        canonical_dirty_after = _status(canonical)
        if _status_overlaps(canonical_dirty_after, files):
            raise PublicationError("canonical checkout overlaps selected files after publication")
        state.update({"stage": "pushed", "published_sha": task_head,
                      "canonical_dirty_after": canonical_dirty_after})
        ci_state = (_ci_status(spec.get("ci"), task_head, spec.get("ci_observer"))
                    if spec.get("ci") is not None or spec.get("ci_observer") is not None
                    else _github_ci_status(spec["repository"], spec["remote"], task_head))
        state["ci"] = ci_state
        state["status"] = "published" if ci_state in {"not_configured", "success"} else ci_state
        _save(receipt, state)
        return state


__all__ = ["PublicationError", "publish_scoped", "selected_diff_digest", "selected_tree_digest"]

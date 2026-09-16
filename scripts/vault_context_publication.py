"""Publish an inspected Vault snapshot as one commit onto an existing GitHub main.

The parent agent owns Vault inventory, redaction, and the decision to publish.
This module only re-verifies the selected, already-inspected snapshot bytes and
performs a single safe, non-force push per invocation. It never touches the
original Vault, the pre-existing history of ``main``, or any index outside a
disposable sparse checkout.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import time
import re
from pathlib import Path

from daily_it_news_delivery import (
    DeliveryError,
    RetryableDeliveryError,
    _git,
    _receipt,
    _validate_path,
)
from daily_it_news_runtime import read_verified, run_command

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from harness.delivery import public_text  # noqa: E402

BRANCH = "main"
TASK_BRANCH = "codex/vault-context-publication"
_ALLOWED_GITHUB_REPO = "Saber5656/obsidian-for-ai-agents"


def _authorized_remote(repo_url: str) -> bool:
    """Allow only the fixed GitHub repository, or a local fixture path for tests."""
    allowed = {
        f"git@github.com:{_ALLOWED_GITHUB_REPO}.git",
        f"ssh://git@github.com/{_ALLOWED_GITHUB_REPO}.git",
        f"https://github.com/{_ALLOWED_GITHUB_REPO}.git",
        f"https://github.com/{_ALLOWED_GITHUB_REPO}",
    }
    if repo_url in allowed:
        return True
    looks_remote = repo_url.startswith(("http://", "https://", "git://", "ssh://")) or bool(
        re.match(r"^[^/\s@]+@[^/\s:]+:", repo_url))
    return not looks_remote and Path(repo_url).is_absolute() and Path(repo_url).is_dir()


def _remote_blob(checkout: Path, ref: str, relative: str) -> str | None:
    tree = _git(["ls-tree", ref, "--", relative], checkout).split()
    return tree[2] if len(tree) >= 3 else None


def _tree_blobs(checkout, ref):
    result = {}
    for line in _git(['ls-tree', '-r', '-z', ref], checkout).split('\0'):
        if line:
            metadata, name = line.split('\t', 1)
            result[name] = metadata.split()[2]
    return result


def _blob_id(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def _read_snapshot(snapshot_root: Path, relative: str) -> bytes:
    path = _validate_path(relative)
    source = snapshot_root
    for part in path.parts:
        source = source / part
        if source.is_symlink():
            raise DeliveryError(f"snapshot ancestor is a symlink: {relative}")
    if not source.is_file():
        raise DeliveryError(f"snapshot file is missing: {relative}")
    data = b'' if source.stat().st_size == 0 else read_verified(source)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeliveryError(f"snapshot file is not valid UTF-8: {relative}") from exc
    return data


def publish_snapshot(repo_url: str, snapshot_root: Path | str, files: list[str],
                     run_root: Path | str, git_name: str, git_email: str,
                     gitleaks_bin: str, expected_blobs: dict[str, str | None]) -> dict:
    """Publish the selected, already-inspected snapshot files as one commit on main.

    ``expected_blobs`` must map every path in ``files`` to the Git blob SHA the
    caller last observed for it on the remote ``main`` (or ``None`` if the path
    does not exist there yet). A mismatch aborts the whole publication without
    writing anything.
    """
    if not _authorized_remote(repo_url):
        raise DeliveryError("repository is not the authorized Vault publication target")
    if not files:
        raise DeliveryError("no files selected for publication")
    if not gitleaks_bin:
        raise DeliveryError("gitleaks is required")
    if set(files) != set(expected_blobs or {}):
        raise DeliveryError("expected_blobs must list an expected blob (or null) for every selected path")
    if len(set(files)) != len(files):
        raise DeliveryError("duplicate path in selected files")

    snapshot_root = Path(snapshot_root)
    if snapshot_root.is_symlink():
        raise DeliveryError('snapshot root is a symlink')
    run_root = Path(run_root)
    contents: dict[str, bytes] = {}
    digests: dict[str, str] = {}
    for relative in files:
        if '\n' in relative or '\r' in relative or '\0' in relative:
            raise DeliveryError('unsupported control character in snapshot path')
        public_text(relative)
        data = _read_snapshot(snapshot_root, relative)
        contents[relative] = data
        digests[relative] = hashlib.sha256(data).hexdigest()

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            result = _publish_snapshot_once(repo_url, files, contents, digests, run_root,
                                            git_name, git_email, gitleaks_bin, expected_blobs)
            _receipt(run_root, "publication-review.json", {
                "review_type": "automated_snapshot_validation",
                "paths": files, "sha256": digests, "secret_scan": "passed",
                "private_paths": "absent",
            })
            return result
        except RetryableDeliveryError as exc:
            last_error = exc
            _receipt(run_root, f"publication-attempt-{attempt}.json", {"status": "failed", "error": str(exc)})
            if attempt == 3:
                raise
            time.sleep(0.5 * attempt)
    raise last_error  # pragma: no cover - loop always returns or raises above


def _publish_snapshot_once(repo_url, files, contents, digests, run_root, git_name, git_email,
                           gitleaks_bin, expected_blobs) -> dict:
    run_root.mkdir(parents=True, exist_ok=True)
    checkout = Path(tempfile.mkdtemp(prefix="vault-publication-", dir=run_root))
    result = {"repo_url": repo_url, "branch": BRANCH, "files": files, "status": "failed",
              "selected_sha256": digests}
    try:
        shutil.rmtree(checkout)
        _git(["init", str(checkout)])
        _git(["remote", "add", "origin", repo_url], checkout)
        if (_git(['remote', 'get-url', 'origin'], checkout) != repo_url
                or _git(['remote', 'get-url', '--push', 'origin'], checkout) != repo_url):
            raise DeliveryError('publication remote identity changed')
        _git(["fetch", "--depth=1", "--filter=blob:none", "origin", f"refs/heads/{BRANCH}"], checkout)
        fetched_head = _git(["rev-parse", "FETCH_HEAD"], checkout)
        _git(["sparse-checkout", "init", "--no-cone"], checkout)
        patterns = '\n'.join('/' + re.sub(r'([\\*?\[\]])', r'\\\1', p) for p in files) + '\n'
        sparse = run_command(['git', 'sparse-checkout', 'set', '--no-cone', '--stdin'],
                             cwd=checkout, input=patterns, text=True, capture_output=True, timeout=60)
        if sparse.returncode:
            raise DeliveryError('sparse snapshot selection failed')
        _git(["checkout", "-B", TASK_BRANCH, fetched_head], checkout)

        remote_tree = _tree_blobs(checkout, fetched_head)
        remote_blobs = {relative: remote_tree.get(relative) for relative in files}
        target_blobs = {relative: _blob_id(contents[relative]) for relative in files}
        changed_files = sorted(r for r in files if target_blobs[r] != remote_blobs[r])
        conflicts = sorted(r for r in changed_files if remote_blobs[r] != expected_blobs[r])
        if conflicts:
            raise DeliveryError(f"remote content changed for: {', '.join(conflicts)}; refusing to overwrite")

        destinations: dict[str, Path] = {}
        for relative in files:
            path = _validate_path(relative)
            destination = checkout
            for part in path.parts:
                destination = destination / part
                if destination.is_symlink():
                    raise DeliveryError(f"publication destination is a symlink: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents[relative])
            destinations[relative] = destination

        scan_dir = Path(tempfile.mkdtemp(prefix="vault-snapshot-scan-", dir=run_root))
        try:
            for relative in files:
                text = contents[relative].decode("utf-8")
                public_text(text)
                scan_target = scan_dir / relative
                scan_target.parent.mkdir(parents=True, exist_ok=True)
                scan_target.write_bytes(contents[relative])
            scan = run_command([gitleaks_bin, "dir", str(scan_dir), "--no-banner", "--redact"],
                               text=True, capture_output=True, timeout=120)
            if scan.returncode:
                raise DeliveryError("gitleaks detected a secret")
        finally:
            shutil.rmtree(scan_dir, ignore_errors=True)

        if not changed_files:
            result.update(status="no_op", commit=fetched_head, observed_remote=fetched_head,
                          selected_blob=target_blobs)
            _receipt(run_root, "publish-noop.json", result)
            return result

        _git(["config", "user.name", git_name], checkout)
        _git(["config", "user.email", git_email], checkout)
        pathspec = run_root / 'selected-paths.nul'
        pathspec.write_bytes('\0'.join(files).encode() + b'\0')
        _git(['--literal-pathspecs', 'add', '--pathspec-from-file=' + str(pathspec.resolve()), '--pathspec-file-nul'], checkout)
        staged = sorted(filter(None, _git(["diff", "--cached", "--name-only", '-z'], checkout).split('\0')))
        if staged != changed_files:
            raise DeliveryError("staged publication includes an unexpected path")
        message = f"docs: publish vault context snapshot ({len(files)} file(s))"
        public_text(message)
        _git(["commit", "-m", message], checkout)
        local_commit = _git(["rev-parse", "HEAD"], checkout)
        changed = sorted(filter(None, _git(["diff-tree", "--no-commit-id", "--name-only", '-z', "-r", local_commit], checkout).split('\0')))
        if changed != changed_files:
            raise DeliveryError("publication commit includes an unexpected path")
        committed_tree = _tree_blobs(checkout, local_commit)
        if any(committed_tree.get(r) != target_blobs[r] for r in files):
            raise DeliveryError('committed snapshot differs from verified bytes')
        public_text(_git(['show', '-s', '--format=fuller', local_commit], checkout))

        push_error = None
        try:
            _git(["push", "origin", f"HEAD:refs/heads/{BRANCH}"], checkout)
        except RetryableDeliveryError as exc:
            push_error = exc
        _git(["fetch", "--depth=1", "origin", f"refs/heads/{BRANCH}"], checkout)
        observed = _git(["rev-parse", "FETCH_HEAD"], checkout)
        observed_tree = _tree_blobs(checkout, observed)
        observed_blobs = {relative: observed_tree.get(relative) for relative in files}
        if any(observed_blobs[r] != target_blobs[r] for r in files):
            if push_error:
                raise push_error
            raise DeliveryError("published snapshot blob verification failed")
        result.update(status="published", commit=local_commit, observed_remote=observed,
                      selected_blob=target_blobs)
        _receipt(run_root, "publish-success.json", result)
        return result
    except Exception as exc:
        result.update(status="failed", error=str(exc))
        _receipt(run_root, "publish-failure.json", result)
        if isinstance(exc, DeliveryError):
            raise
        raise DeliveryError(str(exc)) from exc
    finally:
        shutil.rmtree(checkout, ignore_errors=True)

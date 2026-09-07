#!/usr/bin/env python3
"""Validate, commit, and fixed-push the reviewed publication evidence hunk."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

from atomic_file_ops import (
    AtomicTransactionError,
    allocate_private_entry_path,
    fsync_after_rename,
    link_no_replace_durable,
    mkdir_durable,
    open_absolute_directory_chain,
    publish_head_index_transaction,
    read_named_entry_contract,
    rename_no_replace,
    retain_path_no_replace,
    verify_rename_no_replace,
)
from evidence_hunk import canonical_patch
from git_diff_digest import git_diff_digest
from isolated_git_transport import (
    LOCAL_COMMAND_TIMEOUT_SECONDS,
    TransportError,
    run_local_command,
    run_transport,
)
from trusted_gitleaks import trusted_scan_invocation


class FinalizationError(RuntimeError):
    """Represent a failed evidence finalization."""


MAX_TASK_BYTES = 10 * 1024 * 1024
SCAN_TIMEOUT_SECONDS = 120


def validated_publisher_identity(runtime: dict[str, str]) -> tuple[str, str]:
    """Revalidate the private, context-bound Git identity at mutation time."""
    name = runtime.get("publisher_git_name")
    email = runtime.get("publisher_git_email")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 128
        or any(character in name for character in "\0\r\n<>")
        or not isinstance(email, str)
        or not email
        or len(email) > 254
        or re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", email) is None
    ):
        raise FinalizationError("publisher Git identity is invalid")
    return name, email


def context_bound_inputs(
    runtime: dict[str, str],
    pre: dict[str, object],
    context_bytes: bytes,
    expected_digest: str,
) -> tuple[dict[str, str], dict[str, object]]:
    """Reject valid-looking runtime substitutions after publication review."""
    if hashlib.sha256(context_bytes).hexdigest() != expected_digest:
        raise FinalizationError("publication context digest mismatch")
    context = json.loads(context_bytes)
    bound_runtime = context.get("runtime")
    bound_pre = context.get("pre_collection_state")
    if runtime != bound_runtime or pre != bound_pre:
        raise FinalizationError("finalization inputs differ from reviewed context")
    return bound_runtime, bound_pre


def clean_environment(
    publisher_identity: tuple[str, str] | None = None,
) -> dict[str, str]:
    """Remove Git/Gitleaks override variables while preserving credentials."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and not key.startswith("GITLEAKS_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_CONFIG_COUNT"] = "3"
    environment["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    environment["GIT_CONFIG_VALUE_0"] = "false"
    environment["GIT_CONFIG_KEY_1"] = "core.trustctime"
    environment["GIT_CONFIG_VALUE_1"] = "false"
    environment["GIT_CONFIG_KEY_2"] = "core.checkStat"
    environment["GIT_CONFIG_VALUE_2"] = "minimal"
    if publisher_identity is not None:
        name, email = publisher_identity
        environment["GIT_AUTHOR_NAME"] = name
        environment["GIT_AUTHOR_EMAIL"] = email
        environment["GIT_COMMITTER_NAME"] = name
        environment["GIT_COMMITTER_EMAIL"] = email
    return environment


def git(
    repo: str,
    *arguments: str,
    check: bool = True,
    git_dir: str | None = None,
    publisher_identity: tuple[str, str] | None = None,
    index_file: str | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git with hooks and ambient configuration disabled."""
    repository_arguments = (
        [f"--git-dir={git_dir}", f"--work-tree={repo}"]
        if git_dir is not None
        else ["-C", repo]
    )
    environment = clean_environment(publisher_identity)
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    if arguments and arguments[0] in {"ls-remote", "push", "fetch"}:
        if git_dir is None:
            raise FinalizationError(
                "network Git operation requires an explicit Git directory"
            )
        return run_transport(git_dir, *arguments, check=check, text=True)
    return run_local_command(
        [
            "git", *repository_arguments,
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false",
            "-c", "core.trustctime=false",
            "-c", "core.checkStat=minimal",
            "-c", "commit.gpgSign=false",
            *arguments,
        ],
        check=check,
        capture_output=True,
        text=True,
        env=environment,
        input=input_text,
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )


def control_digest(repo: str) -> str:
    """Hash local config and hooks before the network-enabled push."""
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    common_dir = Path(
        git(
            repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
    )
    digest = hashlib.sha256()
    git_marker = Path(repo) / ".git"
    digest.update(b"worktree-git-entry\0")
    digest.update(f"{git_marker.lstat().st_mode:o}".encode("ascii"))
    digest.update(b"\0")
    if git_marker.is_symlink():
        digest.update(b"symlink\0")
        digest.update(os.fsencode(os.readlink(git_marker)))
    elif git_marker.is_file():
        digest.update(b"file\0")
        digest.update(git_marker.read_bytes())
    else:
        digest.update(b"directory\0")
    seen_control_paths: set[Path] = set()
    for config_path in (common_dir / "config", git_dir / "config.worktree"):
        if config_path in seen_control_paths or not os.path.lexists(config_path):
            continue
        seen_control_paths.add(config_path)
        digest.update(b"config\0")
        digest.update(str(config_path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{config_path.lstat().st_mode:o}".encode("ascii"))
        digest.update(b"\0")
        if config_path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.fsencode(os.readlink(config_path)))
        else:
            digest.update(config_path.read_bytes())
        digest.update(b"\0")
    hooks = common_dir / "hooks"
    if os.path.lexists(hooks):
        digest.update(b"hooks\0")
        digest.update(f"{hooks.lstat().st_mode:o}".encode("ascii"))
        digest.update(b"\0")
        walk_hooks = False
        if hooks.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.fsencode(os.readlink(hooks)))
            digest.update(b"\0")
            try:
                target_mode = hooks.stat().st_mode
            except FileNotFoundError:
                digest.update(b"dangling\0")
            else:
                digest.update(f"{target_mode:o}".encode("ascii"))
                digest.update(b"\0")
                if hooks.is_dir():
                    digest.update(b"target-directory\0")
                    walk_hooks = True
                else:
                    digest.update(b"target-unsupported\0")
        elif not hooks.is_dir():
            digest.update(b"unsupported\0")
        else:
            digest.update(b"directory\0")
            walk_hooks = True
        if walk_hooks:
            for root, directories, files in os.walk(hooks, followlinks=False):
                directories.sort()
                files.sort()
                for name in directories:
                    path = Path(root) / name
                    digest.update(str(path.relative_to(common_dir)).encode("utf-8"))
                    digest.update(b"\0")
                    digest.update(f"{path.lstat().st_mode:o}".encode("ascii"))
                    digest.update(b"\0")
                    if path.is_symlink():
                        digest.update(b"symlink\0")
                        digest.update(os.fsencode(os.readlink(path)))
                    else:
                        digest.update(b"directory\0")
                    digest.update(b"\0")
                for filename in files:
                    path = Path(root) / filename
                    digest.update(str(path.relative_to(common_dir)).encode("utf-8"))
                    digest.update(b"\0")
                    digest.update(f"{path.lstat().st_mode:o}".encode("ascii"))
                    digest.update(b"\0")
                    if path.is_symlink():
                        digest.update(b"symlink\0")
                        digest.update(os.fsencode(os.readlink(path)))
                    else:
                        digest.update(path.read_bytes())
                    digest.update(b"\0")
    return digest.hexdigest()


def diff_digest(repo: str, relative: str) -> str:
    """Hash the exact unstaged binary evidence diff."""
    return git_diff_digest(repo, relative)


def cached_diff_digest(repo: str, relative: str) -> str:
    """Hash the exact staged binary evidence diff."""
    return git_diff_digest(repo, relative, cached=True)


def dirty_status(repo: str) -> tuple[bool, str]:
    """Return clean state and the contract's porcelain digest."""
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    return (not status, hashlib.sha256(status.encode("utf-8")).hexdigest())


def remote_head(repo: str, remote_url: str, git_dir: str | None = None) -> str:
    """Resolve the literal remote main URL without using a mutable remote name."""
    result = git(
        repo,
        "ls-remote",
        "--exit-code",
        remote_url,
        "refs/heads/main",
        git_dir=git_dir,
    ).stdout.split()
    if len(result) != 2:
        raise FinalizationError("could not resolve remote main")
    return result[0]


def require_fast_forward_target(
    repo: str,
    expected_remote: str,
    local_head: str,
    git_dir: str | None = None,
) -> None:
    """Prove the fixed non-force update preserves reviewed remote history."""
    result = git(
        repo,
        "merge-base",
        "--is-ancestor",
        expected_remote,
        local_head,
        check=False,
        git_dir=git_dir,
    )
    if result.returncode != 0:
        raise FinalizationError(
            "evidence push target is not a descendant of expected remote"
        )


def push_evidence_with_retry(
    repo: str,
    remote_url: str,
    git_dir: str,
    evidence_commit: str,
    before_remote: str,
) -> str:
    """Retry a fixed non-force push across transient verification errors."""
    require_fast_forward_target(repo, before_remote, evidence_commit, git_dir)
    remote = before_remote
    for _ in range(3):
        try:
            remote = remote_head(repo, remote_url, git_dir)
        except (
            FinalizationError,
            OSError,
            subprocess.SubprocessError,
            TransportError,
        ):
            continue
        if remote == evidence_commit:
            return remote
        if remote != before_remote:
            break
        git(
            repo,
            "push",
            remote_url,
            f"{evidence_commit}:refs/heads/main",
            check=False,
            git_dir=git_dir,
        )
        try:
            remote = remote_head(repo, remote_url, git_dir)
        except (
            FinalizationError,
            OSError,
            subprocess.SubprocessError,
            TransportError,
        ):
            continue
        if remote == evidence_commit:
            return remote
        if remote != before_remote:
            break
    raise FinalizationError("final evidence push failed")


def scan_staged(gitleaks_bin: str, repo: str, index_file: str | None = None) -> None:
    """Run pinned gitleaks against the exact staged evidence hunk."""
    environment = clean_environment()
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    try:
        with trusted_scan_invocation() as (scan_prefix, pass_fds):
            result = run_local_command(
                [
                    gitleaks_bin,
                    *scan_prefix,
                    "git",
                    "--staged",
                    repo,
                ],
                cwd="/",
                check=False,
                capture_output=True,
                text=True,
                env=environment,
                timeout=SCAN_TIMEOUT_SECONDS,
                pass_fds=pass_fds,
            )
    except subprocess.TimeoutExpired as exc:
        raise FinalizationError("gitleaks evidence scan exceeded its deadline") from exc
    if result.returncode != 0:
        raise FinalizationError("gitleaks rejected staged evidence")


def capture_complete(runtime_file: str) -> dict[str, object]:
    """Capture both Vaults through the canonical publication state helper."""
    helper = Path(__file__).with_name("capture-vault-state.py")
    completed = run_local_command(
        [str(helper), "--index-only", "--include-local-history", runtime_file],
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return json.loads(completed.stdout)


def validate_residual_after_commit(
    baseline: dict[str, object],
    current: dict[str, object],
    target: str,
    index_blob: str,
    worktree_candidate: bytes,
    repo: str,
) -> None:
    """Prove hunk-level transformation preserved every pre-existing residual."""
    for field in (
        "dirty_lines", "dirty_paths", "staged_paths", "git_control_sha256",
        "branch", "upstream", "operation_in_progress", "remote_head",
        "worktree_capture_scope",
    ):
        if current.get(field) != baseline.get(field):
            raise FinalizationError(f"evidence commit changed residual state: {field}")
    before_entries = {
        entry["path"]: entry
        for entry in baseline["dirty_entries"]
        if entry["path"] != target
    }
    after_entries = {
        entry["path"]: entry
        for entry in current["dirty_entries"]
        if entry["path"] != target
    }
    if after_entries != before_entries:
        raise FinalizationError("evidence commit changed non-target dirty bytes")
    before_metadata = {
        entry["path"]: entry
        for entry in baseline["dirty_metadata"]
        if entry["path"] != target
    }
    after_metadata = {
        entry["path"]: entry
        for entry in current["dirty_metadata"]
        if entry["path"] != target
    }
    if after_metadata != before_metadata:
        raise FinalizationError("evidence commit changed non-target metadata")
    before_index = [
        entry for entry in baseline["index_entries"] if entry["path"] != target
    ]
    after_index = [
        entry for entry in current["index_entries"] if entry["path"] != target
    ]
    if after_index != before_index:
        raise FinalizationError("evidence commit changed a non-owned index entry")
    target_index = [
        entry for entry in current["index_entries"] if entry["path"] == target
    ]
    if target_index != [
        {"path": target, "mode": "100644", "git_blob_oid": index_blob, "stage": 0}
    ]:
        raise FinalizationError("evidence index hunk differs from the planned variant")
    if stable_regular_bytes(Path(repo) / target) != worktree_candidate:
        raise FinalizationError("evidence worktree hunk differs from the planned variant")


def stable_regular_bytes(path: Path) -> bytes:
    """Read one stable regular file without following the final component."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FinalizationError("evidence target is not a regular file")
        if before.st_size > MAX_TASK_BYTES:
            raise FinalizationError("evidence input exceeds the allowed size")
        chunks = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.extend(chunk)
            if len(chunks) > MAX_TASK_BYTES:
                raise FinalizationError("evidence input grew beyond the allowed size")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise FinalizationError("evidence target changed while being read")
        return bytes(chunks)
    finally:
        os.close(descriptor)


EVIDENCE_REVIEW_REASON_CODES = frozenset(
    {
        "approved",
        "input_preparation_failed",
        "input_too_large",
        "process_failed",
        "result_missing",
        "canonical_validation_failed",
        "result_rejected",
        "legacy_failure",
    }
)


def read_evidence_review_diagnostic(
    path: Path | None,
    review_status: int,
    legacy_reason: str = "",
) -> dict[str, object]:
    """Load a bounded, structured review diagnostic without importing stderr."""
    empty_stderr_sha256 = hashlib.sha256(b"").hexdigest()
    if path is None:
        return {
            "process_status": review_status,
            "status": review_status,
            "reason_code": "approved" if review_status == 0 else "legacy_failure",
            "result_present": review_status == 0,
            "stderr_sha256": empty_stderr_sha256,
            "result_sha256": None,
        }
    try:
        diagnostic = json.loads(stable_regular_bytes(path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FinalizationError("evidence review diagnostic is unreadable") from exc
    if not isinstance(diagnostic, dict):
        raise FinalizationError("evidence review diagnostic is not an object")
    process_status = diagnostic.get("process_status")
    status = diagnostic.get("status")
    reason_code = diagnostic.get("reason_code")
    result_present = diagnostic.get("result_present")
    stderr_sha256 = diagnostic.get("stderr_sha256")
    result_sha256 = diagnostic.get("result_sha256")
    if (
        type(process_status) is not int
        or type(status) is not int
        or status != review_status
        or reason_code not in EVIDENCE_REVIEW_REASON_CODES
        or type(result_present) is not bool
        or not isinstance(stderr_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", stderr_sha256) is None
        or (
            result_sha256 is not None
            and (
                not isinstance(result_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", result_sha256) is None
            )
        )
        or (result_present and not isinstance(result_sha256, str))
        or (not result_present and result_sha256 is not None)
    ):
        raise FinalizationError("evidence review diagnostic contract is invalid")
    return {
        "process_status": process_status,
        "status": status,
        "reason_code": reason_code,
        "result_present": result_present,
        "stderr_sha256": stderr_sha256,
        "result_sha256": result_sha256,
    }


def index_file_contract(path: Path) -> tuple[bytes, list[int]]:
    """Read one stable no-follow shared index and seal its inode contract."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FinalizationError("Git index is not a regular file")
        content = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            content.extend(chunk)
        after = os.fstat(descriptor)
        contract = [
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ]
        if contract != [
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ]:
            raise FinalizationError("Git index changed while it was read")
        return bytes(content), contract
    finally:
        os.close(descriptor)


def prepare_shared_index_candidate(
    repo: str,
    git_dir: str,
    baseline: dict[str, object],
    target: str,
    index_blob: str,
    directory: Path,
) -> tuple[str, dict[str, object]]:
    """Apply only the reviewed evidence entry to the exact shared index."""
    index_path = Path(git_dir) / "index"
    content, identity = index_file_contract(index_path)
    if (
        hashlib.sha256(content).hexdigest() != baseline.get("index_sha256")
        or identity != baseline.get("index_identity")
    ):
        raise FinalizationError("shared Git index differs from evidence review")
    candidate_path = allocate_private_entry_path(
        directory,
        prefix=".evidence-shared-index-work-",
        entry_name="index",
    )
    descriptor = os.open(
        candidate_path,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        stat.S_IMODE(identity[3]),
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FinalizationError("could not prepare evidence Git index")
            view = view[written:]
        os.fchmod(descriptor, stat.S_IMODE(identity[3]))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{index_blob},{target}",
            git_dir=git_dir,
            index_file=candidate_path,
        )
        candidate_content, candidate_identity = index_file_contract(
            Path(candidate_path)
        )
        return candidate_path, {
            "sha256": hashlib.sha256(candidate_content).hexdigest(),
            "identity": candidate_identity,
        }
    except Exception as original:
        try:
            retain_path_no_replace(
                candidate_path,
                label="failed evidence shared-index candidate",
                prefix=".evidence-shared-index-retained-",
                allow_missing=True,
            )
        except AtomicTransactionError as cleanup_error:
            raise FinalizationError(
                f"evidence shared-index cleanup failed closed: {cleanup_error}"
            ) from original
        raise


def publish_evidence_head_and_index(
    repo: str,
    git_dir: str,
    baseline: dict[str, object],
    base_head: str,
    candidate_head: str,
    candidate_index_path: str,
    candidate_index_contract: dict[str, object],
    progress: dict[str, bool],
) -> None:
    """Publish evidence HEAD/index through the shared durable transaction."""
    expected_digest = baseline.get("index_sha256")
    expected_identity = baseline.get("index_identity")
    if not isinstance(expected_digest, str) or not isinstance(expected_identity, list):
        raise FinalizationError("reviewed evidence index contract is malformed")
    try:
        publish_head_index_transaction(
            git_dir,
            base_head=base_head,
            candidate_head=candidate_head,
            expected_index_sha256=expected_digest,
            expected_index_identity=expected_identity,
            candidate_index_path=candidate_index_path,
            candidate_index_sha256=str(candidate_index_contract.get("sha256")),
            candidate_index_identity=candidate_index_contract.get("identity"),
            read_head=lambda: git(
                repo, "rev-parse", "HEAD", git_dir=git_dir
            ).stdout.strip(),
            update_head=lambda new, old: git(
                repo, "update-ref", "HEAD", new, old, git_dir=git_dir
            ),
            mutation_tracker=progress,
        )
    except AtomicTransactionError as exc:
        raise FinalizationError(str(exc)) from exc


def git_object_bytes(repo: str, git_dir: str, object_spec: str) -> bytes:
    """Read one Git object through an explicit size gate before allocation."""
    size = git(
        repo, "cat-file", "-s", object_spec, git_dir=git_dir
    ).stdout.strip()
    try:
        object_size = int(size)
    except ValueError as exc:
        raise FinalizationError("evidence Git object size is invalid") from exc
    if object_size < 0 or object_size > MAX_TASK_BYTES:
        raise FinalizationError("evidence Git object exceeds the allowed size")
    completed = run_local_command(
        [
            "git", f"--git-dir={git_dir}", f"--work-tree={repo}",
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false",
            "-c", "core.trustctime=false",
            "-c", "core.checkStat=minimal",
            "cat-file", "blob", object_spec,
        ],
        check=False,
        capture_output=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    content = completed.stdout
    if completed.returncode != 0 or len(content) != object_size:
        raise FinalizationError("evidence Git object is unavailable or unstable")
    return content


def private_candidate(plan: dict[str, object], prefix: str) -> bytes:
    """Read one sealed run candidate and bind it to its planned digest."""
    content = stable_regular_bytes(Path(str(plan[f"{prefix}_candidate_path"])))
    if hashlib.sha256(content).hexdigest() != plan[f"{prefix}_candidate_sha256"]:
        raise FinalizationError(f"sealed {prefix} evidence candidate changed")
    return content


def write_all(descriptor: int, content: bytes) -> None:
    """Write a complete candidate or fail on zero progress."""
    offset = 0
    while offset < len(content):
        count = os.write(descriptor, content[offset:])
        if count <= 0:
            raise FinalizationError("evidence candidate write made no progress")
        offset += count


def descriptor_bytes(descriptor: int) -> tuple[bytes, os.stat_result]:
    """Read bounded regular bytes while retaining descriptor identity."""
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_TASK_BYTES:
        raise FinalizationError("evidence worktree target is not a bounded regular file")
    os.lseek(descriptor, 0, os.SEEK_SET)
    content = bytearray()
    while chunk := os.read(descriptor, 1024 * 1024):
        content.extend(chunk)
        if len(content) > MAX_TASK_BYTES:
            raise FinalizationError("evidence worktree target exceeded the size limit")
    after = os.fstat(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mode,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mode,
    ):
        raise FinalizationError("evidence worktree target changed while being read")
    return bytes(content), after


def descriptor_matches_candidate(
    descriptor: int, receipt: dict[str, object]
) -> bool:
    """Bind rollback authority to this run's exact inode, bytes, size, and mode."""
    try:
        content, metadata = descriptor_bytes(descriptor)
    except FinalizationError:
        return False
    return (
        (metadata.st_dev, metadata.st_ino) == tuple(receipt["candidate_identity"])
        and metadata.st_size == receipt["candidate_size"]
        and metadata.st_mode == receipt["candidate_mode"]
        and hashlib.sha256(content).hexdigest() == receipt["candidate_sha256"]
    )


def descriptor_matches_original(
    descriptor: int, receipt: dict[str, object]
) -> bool:
    """Bind the saved source to its exact pre-install inode and metadata."""
    try:
        content, metadata = descriptor_bytes(descriptor)
    except FinalizationError:
        return False
    return (
        (metadata.st_dev, metadata.st_ino) == tuple(receipt["original_identity"])
        and metadata.st_size == receipt["original_size"]
        and metadata.st_mode == receipt["original_full_mode"]
        and metadata.st_mtime_ns == receipt["original_mtime_ns"]
        and hashlib.sha256(content).hexdigest() == receipt["original_sha256"]
    )


def open_target_parent(
    repo: str, target: str
) -> tuple[int, str, tuple[tuple[int, int], ...]]:
    """Open a target parent and return every traversed directory identity."""
    relative = PurePosixPath(target)
    raw_components = target.split("/")
    if (
        not target
        or relative.is_absolute()
        or not relative.parts
        or tuple(raw_components) != relative.parts
        or any(component in {"", ".", ".."} for component in raw_components)
    ):
        raise FinalizationError("evidence target is not a safe relative path")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    opened: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        opened.append(os.open(repo, flags))
        metadata = os.fstat(opened[-1])
        identities.append((metadata.st_dev, metadata.st_ino))
        for component in relative.parts[:-1]:
            opened.append(os.open(component, flags, dir_fd=opened[-1]))
            metadata = os.fstat(opened[-1])
            identities.append((metadata.st_dev, metadata.st_ino))
    except OSError as exc:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise FinalizationError(
            "evidence target parent contains a symlink or non-directory"
        ) from exc
    parent_fd = opened.pop()
    for descriptor in reversed(opened):
        os.close(descriptor)
    return parent_fd, relative.name, tuple(identities)


def verify_target_parent_chain(
    repo: str,
    target: str,
    expected: object,
) -> None:
    """Rebind a target to the reviewed Vault root and every parent inode."""
    descriptor, _name, identities = open_target_parent(repo, target)
    try:
        normalized = tuple(tuple(item) for item in expected)  # type: ignore[arg-type]
        if identities != normalized:
            raise FinalizationError("evidence target parent chain changed")
    except (TypeError, ValueError) as exc:
        raise FinalizationError("evidence target parent receipt is invalid") from exc
    finally:
        os.close(descriptor)


def open_receipt_quarantine(
    receipt: dict[str, object]
) -> tuple[int, int]:
    """Reopen the private original quarantine and verify its directory inode."""
    name = str(receipt["original_quarantine_name"])
    if (
        "/" in name
        or "\\" in name
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
        or not name.startswith(".publication-evidence-original-")
    ):
        raise FinalizationError("evidence quarantine receipt is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    root_fd, root_chain = open_absolute_directory_chain(
        str(receipt["original_quarantine_root"])
    )
    descriptor = -1
    try:
        root_metadata = os.fstat(root_fd)
        if (root_metadata.st_dev, root_metadata.st_ino) != tuple(
            receipt["original_quarantine_root_identity"]
        ) or root_chain != tuple(
            tuple(item) for item in receipt["original_quarantine_root_chain"]
        ):
            raise FinalizationError("evidence quarantine root changed")
        descriptor = os.open(name, flags, dir_fd=root_fd)
        metadata = os.fstat(descriptor)
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (
            (metadata.st_dev, metadata.st_ino)
            != tuple(receipt["original_quarantine_identity"])
            or (named.st_dev, named.st_ino)
            != tuple(receipt["original_quarantine_identity"])
        ):
            raise FinalizationError("evidence original quarantine changed")
        return root_fd, descriptor
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_fd)
        raise


def open_receipt_candidate_quarantine(
    receipt: dict[str, object]
) -> tuple[int, int]:
    """Reopen the durable candidate reservation and bind its full root chain."""
    name = str(receipt["candidate_quarantine_name"])
    if (
        "/" in name
        or "\\" in name
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
        or not name.startswith(".publication-evidence-candidate-")
    ):
        raise FinalizationError("evidence candidate quarantine receipt is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    root_fd, root_chain = open_absolute_directory_chain(
        str(receipt["original_quarantine_root"])
    )
    descriptor = -1
    try:
        root_metadata = os.fstat(root_fd)
        if (
            (root_metadata.st_dev, root_metadata.st_ino)
            != tuple(receipt["original_quarantine_root_identity"])
            or root_chain
            != tuple(
                tuple(item) for item in receipt["original_quarantine_root_chain"]
            )
        ):
            raise FinalizationError("evidence candidate quarantine root changed")
        descriptor = os.open(name, flags, dir_fd=root_fd)
        metadata = os.fstat(descriptor)
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (
            (metadata.st_dev, metadata.st_ino)
            != tuple(receipt["candidate_quarantine_identity"])
            or (named.st_dev, named.st_ino)
            != tuple(receipt["candidate_quarantine_identity"])
        ):
            raise FinalizationError("evidence candidate quarantine changed")
        return root_fd, descriptor
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_fd)
        raise


def restore_quarantined_entry(
    quarantine_fd: int,
    parent_fd: int,
    target_name: str,
    entry_name: str = "artifact",
) -> bool:
    """Restore a quarantined entry only when no concurrent occupant exists."""
    try:
        rename_no_replace(quarantine_fd, entry_name, parent_fd, target_name)
        fsync_after_rename(quarantine_fd, parent_fd)
        return True
    except FileExistsError:
        return False


def replace_worktree_candidate(
    repo: str,
    target: str,
    expected_sha256: str,
    candidate: bytes,
    quarantine_root: str | None = None,
    recovery_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    """Install a candidate after durably recording both recovery tombstones."""
    parent_fd, target_name, parent_chain = open_target_parent(repo, target)
    quarantine_root = quarantine_root or str(Path(repo) / ".git")
    quarantine_root_fd = -1
    original_fd = -1
    candidate_fd = -1
    installed_fd = -1
    quarantine_fd = -1
    candidate_quarantine_fd = -1
    quarantine_name = f".publication-evidence-original-{secrets.token_hex(16)}"
    candidate_quarantine_name = (
        f".publication-evidence-candidate-{secrets.token_hex(16)}"
    )
    original_retained = False
    detached_original = False
    receipt = recovery_receipt if recovery_receipt is not None else {}
    try:
        quarantine_root_fd, quarantine_root_chain = open_absolute_directory_chain(
            quarantine_root
        )
        if os.fstat(quarantine_root_fd).st_dev != os.fstat(parent_fd).st_dev:
            raise FinalizationError(
                "evidence quarantine is not on the target filesystem"
            )
        original_fd = os.open(
            target_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        original, before = descriptor_bytes(original_fd)
        if hashlib.sha256(original).hexdigest() != expected_sha256:
            raise FinalizationError("evidence worktree source changed after review")
        original_contract = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        )
        current = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        current_bytes, current_descriptor_metadata = descriptor_bytes(original_fd)
        if (
            (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_mode,
            )
            != original_contract
            or (current_descriptor_metadata.st_dev, current_descriptor_metadata.st_ino)
            != (before.st_dev, before.st_ino)
            or current_bytes != original
        ):
            raise FinalizationError("evidence worktree target raced before replacement")
        verify_target_parent_chain(repo, target, parent_chain)

        # Retain and describe the reviewed original before any candidate file is
        # created.  Every subsequent fault can therefore emit actionable,
        # descriptor-bound recovery evidence.
        mkdir_durable(quarantine_name, 0o700, parent_fd=quarantine_root_fd)
        quarantine_fd = os.open(
            quarantine_name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=quarantine_root_fd,
        )
        verify_rename_no_replace(quarantine_fd)
        # Give the reviewed original a durable private name before this process
        # removes its canonical name. A later pathname race can no longer turn
        # the exact inode into an unnamed open descriptor.
        link_no_replace_durable(
            parent_fd, target_name, quarantine_fd, "artifact"
        )
        original_retained = True
        held = os.stat("artifact", dir_fd=quarantine_fd, follow_symlinks=False)
        held_bytes, held_descriptor_metadata = descriptor_bytes(original_fd)
        if (
            (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino)
            or (held_descriptor_metadata.st_dev, held_descriptor_metadata.st_ino)
            != (before.st_dev, before.st_ino)
            or held_descriptor_metadata.st_size != before.st_size
            or held_descriptor_metadata.st_mode != before.st_mode
            or held_bytes != original
        ):
            raise FinalizationError(
                "evidence install raced before original reservation"
            )
        root_metadata = os.fstat(quarantine_root_fd)
        quarantine_metadata = os.fstat(quarantine_fd)
        receipt.update(
            {
                "repo": repo,
                "target": target,
                "original": original,
                "original_mode": stat.S_IMODE(before.st_mode),
                "original_full_mode": before.st_mode,
                "original_mtime_ns": before.st_mtime_ns,
                "original_sha256": hashlib.sha256(original).hexdigest(),
                "original_identity": (before.st_dev, before.st_ino),
                "original_size": before.st_size,
                "original_quarantine_name": quarantine_name,
                "original_quarantine_root": quarantine_root,
                "original_quarantine_root_identity": (
                    root_metadata.st_dev,
                    root_metadata.st_ino,
                ),
                "original_quarantine_root_chain": quarantine_root_chain,
                "original_quarantine_identity": (
                    quarantine_metadata.st_dev,
                    quarantine_metadata.st_ino,
                ),
                "target_parent_chain": parent_chain,
                "original_restored": True,
                "original_detached": False,
                "canonical_candidate_installed": False,
            }
        )

        mkdir_durable(
            candidate_quarantine_name, 0o700, parent_fd=quarantine_root_fd
        )
        candidate_quarantine_fd = os.open(
            candidate_quarantine_name,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=quarantine_root_fd,
        )
        verify_rename_no_replace(candidate_quarantine_fd)
        candidate_quarantine_metadata = os.fstat(candidate_quarantine_fd)
        candidate_fd = os.open(
            "artifact",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            stat.S_IMODE(before.st_mode),
            dir_fd=candidate_quarantine_fd,
        )
        os.fchmod(candidate_fd, stat.S_IMODE(before.st_mode))

        def record_candidate_contract() -> None:
            candidate_bytes, candidate_metadata = descriptor_bytes(candidate_fd)
            receipt.update(
                {
                    "candidate_quarantine_name": candidate_quarantine_name,
                    "candidate_quarantine_identity": (
                        candidate_quarantine_metadata.st_dev,
                        candidate_quarantine_metadata.st_ino,
                    ),
                    "rollback_candidate_quarantine_name": candidate_quarantine_name,
                    "rollback_candidate_quarantine_identity": (
                        candidate_quarantine_metadata.st_dev,
                        candidate_quarantine_metadata.st_ino,
                    ),
                    "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                    "candidate_identity": (
                        candidate_metadata.st_dev,
                        candidate_metadata.st_ino,
                    ),
                    "candidate_size": candidate_metadata.st_size,
                    "candidate_mode": candidate_metadata.st_mode,
                }
            )

        # Populate a valid empty candidate contract immediately. If a bounded
        # write fails, replace it with the exact partial-file contract before
        # propagating the original error.
        record_candidate_contract()
        try:
            write_all(candidate_fd, candidate)
            os.fsync(candidate_fd)
            os.fsync(candidate_quarantine_fd)
        except Exception:
            try:
                os.fsync(candidate_fd)
                os.fsync(candidate_quarantine_fd)
                record_candidate_contract()
            except Exception:
                pass
            raise
        record_candidate_contract()
        candidate_metadata = os.fstat(candidate_fd)
        candidate_sha256 = hashlib.sha256(candidate).hexdigest()
        if receipt["candidate_sha256"] != candidate_sha256:
            raise FinalizationError("evidence candidate write is incomplete")

        rename_no_replace(parent_fd, target_name, quarantine_fd, "detached")
        detached_original = True
        receipt["original_restored"] = False
        receipt["original_detached"] = True
        fsync_after_rename(parent_fd, quarantine_fd)
        detached = os.stat(
            "detached", dir_fd=quarantine_fd, follow_symlinks=False
        )
        if (detached.st_dev, detached.st_ino) != (before.st_dev, before.st_ino):
            if restore_quarantined_entry(
                quarantine_fd, parent_fd, target_name, "detached"
            ):
                detached_original = False
                receipt["original_restored"] = True
                receipt["original_detached"] = False
                raise FinalizationError(
                    "evidence install raced; replacement was restored"
                )
            raise FinalizationError(
                "evidence install raced; replacement and original were retained"
            )
        try:
            detached_content, detached_contract = read_named_entry_contract(
                quarantine_fd, "detached", max_bytes=before.st_size
            )
        except AtomicTransactionError as exc:
            raise FinalizationError(
                "evidence retained original changed after detachment"
            ) from exc
        if (
            detached_contract[:5]
            != [
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mode,
                before.st_mtime_ns,
            ]
            or hashlib.sha256(detached_content).hexdigest() != expected_sha256
        ):
            raise FinalizationError(
                "evidence retained original changed after detachment"
            )
        try:
            # Keep the exact candidate under its private durable name for the
            # full transaction.  Publishing a hardlink means a concurrent
            # unlink/replace of the canonical path can never strand the only
            # surviving inode on this process's open descriptor.
            link_no_replace_durable(
                candidate_quarantine_fd, "artifact", parent_fd, target_name
            )
        except OSError as exc:
            if restore_quarantined_entry(
                quarantine_fd, parent_fd, target_name, "detached"
            ):
                detached_original = False
                receipt["original_restored"] = True
                receipt["original_detached"] = False
                raise FinalizationError(
                    "evidence install collision; original was restored"
                ) from exc
            raise FinalizationError(
                "evidence install collision; original was quarantined"
            ) from exc
        receipt["canonical_candidate_installed"] = True
        installed_fd = os.open(
            target_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        installed = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        installed_bytes, installed_metadata = descriptor_bytes(installed_fd)
        if (
            (installed.st_dev, installed.st_ino)
            != (candidate_metadata.st_dev, candidate_metadata.st_ino)
            or (installed_metadata.st_dev, installed_metadata.st_ino)
            != (candidate_metadata.st_dev, candidate_metadata.st_ino)
            or installed_metadata.st_size != candidate_metadata.st_size
            or installed_metadata.st_mode != candidate_metadata.st_mode
            or hashlib.sha256(installed_bytes).hexdigest() != candidate_sha256
        ):
            raise FinalizationError(
                "evidence candidate changed after installation; original retained"
            )
        verify_target_parent_chain(repo, target, parent_chain)
        return receipt
    finally:
        if original_fd >= 0:
            os.close(original_fd)
        if candidate_fd >= 0:
            os.close(candidate_fd)
        if installed_fd >= 0:
            os.close(installed_fd)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if candidate_quarantine_fd >= 0:
            os.close(candidate_quarantine_fd)
        if not original_retained and quarantine_root_fd >= 0:
            try:
                os.rmdir(quarantine_name, dir_fd=quarantine_root_fd)
            except FileNotFoundError:
                pass
        if quarantine_root_fd >= 0:
            os.close(quarantine_root_fd)
        os.close(parent_fd)


def finalize_worktree_candidate(receipt: dict[str, object]) -> None:
    """Verify final state while retaining the original as a private tombstone."""
    parent_fd, target_name, parent_chain = open_target_parent(
        str(receipt["repo"]), str(receipt["target"])
    )
    quarantine_root_fd = -1
    quarantine_fd = -1
    candidate_quarantine_root_fd = -1
    candidate_quarantine_fd = -1
    candidate_fd = -1
    retained_candidate_fd = -1
    original_fd = -1
    try:
        if parent_chain != tuple(
            tuple(item) for item in receipt["target_parent_chain"]
        ):
            raise FinalizationError("evidence target parent chain changed")
        quarantine_root_fd, quarantine_fd = open_receipt_quarantine(receipt)
        (
            candidate_quarantine_root_fd,
            candidate_quarantine_fd,
        ) = open_receipt_candidate_quarantine(receipt)
        original_fd = os.open(
            "artifact",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=quarantine_fd,
        )
        candidate_fd = os.open(
            target_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        retained_candidate_fd = os.open(
            "artifact",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=candidate_quarantine_fd,
        )
        original_named = os.stat(
            "artifact", dir_fd=quarantine_fd, follow_symlinks=False
        )
        candidate_named = os.stat(
            target_name, dir_fd=parent_fd, follow_symlinks=False
        )
        retained_candidate_named = os.stat(
            "artifact", dir_fd=candidate_quarantine_fd, follow_symlinks=False
        )
        if (
            (original_named.st_dev, original_named.st_ino)
            != tuple(receipt["original_identity"])
            or (candidate_named.st_dev, candidate_named.st_ino)
            != tuple(receipt["candidate_identity"])
            or (retained_candidate_named.st_dev, retained_candidate_named.st_ino)
            != tuple(receipt["candidate_identity"])
            or not descriptor_matches_original(original_fd, receipt)
            or not descriptor_matches_candidate(candidate_fd, receipt)
            or not descriptor_matches_candidate(retained_candidate_fd, receipt)
        ):
            raise FinalizationError(
                "evidence transaction changed before final verification"
            )
        verify_target_parent_chain(
            str(receipt["repo"]),
            str(receipt["target"]),
            receipt["target_parent_chain"],
        )
        os.fsync(quarantine_fd)
        os.fsync(candidate_quarantine_fd)
        os.fsync(quarantine_root_fd)
        os.fsync(candidate_quarantine_root_fd)
        os.fsync(parent_fd)
    finally:
        if original_fd >= 0:
            os.close(original_fd)
        if candidate_fd >= 0:
            os.close(candidate_fd)
        if retained_candidate_fd >= 0:
            os.close(retained_candidate_fd)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if candidate_quarantine_fd >= 0:
            os.close(candidate_quarantine_fd)
        if quarantine_root_fd >= 0:
            os.close(quarantine_root_fd)
        if candidate_quarantine_root_fd >= 0:
            os.close(candidate_quarantine_root_fd)
        os.close(parent_fd)


def rollback_worktree_candidate(receipt: dict[str, object]) -> None:
    """Restore the original and retain the failed candidate as a tombstone."""
    parent_fd, target_name, parent_chain = open_target_parent(
        str(receipt["repo"]), str(receipt["target"])
    )
    canonical_candidate_fd = -1
    retained_candidate_fd = -1
    original_fd = -1
    original_quarantine_root_fd = -1
    original_quarantine_fd = -1
    candidate_quarantine_root_fd = -1
    candidate_quarantine_fd = -1
    try:
        (
            original_quarantine_root_fd,
            original_quarantine_fd,
        ) = open_receipt_quarantine(receipt)
        (
            candidate_quarantine_root_fd,
            candidate_quarantine_fd,
        ) = open_receipt_candidate_quarantine(receipt)
        if parent_chain != tuple(
            tuple(item) for item in receipt["target_parent_chain"]
        ):
            raise FinalizationError("evidence rollback parent chain changed")
        original_fd = os.open(
            "artifact",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=original_quarantine_fd,
        )
        if not descriptor_matches_original(original_fd, receipt):
            raise FinalizationError("evidence rollback original changed")
        retained_candidate_fd = os.open(
            "artifact",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=candidate_quarantine_fd,
        )
        held = os.stat(
            "artifact", dir_fd=candidate_quarantine_fd, follow_symlinks=False
        )
        if (
            (held.st_dev, held.st_ino) != tuple(receipt["candidate_identity"])
            or not descriptor_matches_candidate(retained_candidate_fd, receipt)
        ):
            raise FinalizationError("evidence rollback candidate tombstone changed")
        try:
            canonical_candidate_fd = os.open(
                target_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            canonical_candidate_fd = -1
        if canonical_candidate_fd >= 0:
            current = os.stat(
                target_name, dir_fd=parent_fd, follow_symlinks=False
            )
            if (
                (current.st_dev, current.st_ino)
                != tuple(receipt["candidate_identity"])
                or not descriptor_matches_candidate(
                    canonical_candidate_fd, receipt
                )
            ):
                raise FinalizationError(
                    "evidence rollback refused after target replacement"
                )
            # Move the canonical name into the already durable reservation.
            # The `artifact` hardlink remains the recovery contract even when
            # this extra diagnostic name cannot be created.
            rename_no_replace(
                parent_fd, target_name, candidate_quarantine_fd, "worktree"
            )
            fsync_after_rename(parent_fd, candidate_quarantine_fd)
            try:
                moved_content, moved_contract = read_named_entry_contract(
                    candidate_quarantine_fd,
                    "worktree",
                    max_bytes=int(receipt["candidate_size"]),
                )
                moved_matches = (
                    moved_contract[:4]
                    == [
                        int(receipt["candidate_identity"][0]),
                        int(receipt["candidate_identity"][1]),
                        int(receipt["candidate_size"]),
                        int(receipt["candidate_mode"]),
                    ]
                    and hashlib.sha256(moved_content).hexdigest()
                    == receipt["candidate_sha256"]
                )
            except (AtomicTransactionError, KeyError, TypeError, ValueError):
                moved_matches = False
            if not moved_matches:
                try:
                    rename_no_replace(
                        candidate_quarantine_fd,
                        "worktree",
                        parent_fd,
                        target_name,
                    )
                    fsync_after_rename(candidate_quarantine_fd, parent_fd)
                    raise FinalizationError(
                        "evidence rollback retained candidate changed; "
                        "replacement was restored"
                    )
                except FileExistsError as exc:
                    raise FinalizationError(
                        "evidence rollback retained candidate changed; "
                        "replacement and tombstones were retained"
                    ) from exc
        try:
            link_no_replace_durable(
                original_quarantine_fd, "artifact", parent_fd, target_name
            )
            receipt["original_restored"] = True
            receipt["original_detached"] = False
            receipt["canonical_candidate_installed"] = False
        except OSError as exc:
            raise FinalizationError(
                "evidence rollback collision; both tombstones were retained"
            ) from exc
        if not descriptor_matches_candidate(retained_candidate_fd, receipt):
            raise FinalizationError(
                "evidence rollback candidate changed in quarantine"
            )
        try:
            restored_fd = os.open(
                target_name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise FinalizationError(
                "evidence rollback original disappeared after restore"
            ) from exc
        try:
            restored_named = os.stat(
                target_name, dir_fd=parent_fd, follow_symlinks=False
            )
            if (
                (restored_named.st_dev, restored_named.st_ino)
                != tuple(receipt["original_identity"])
                or not descriptor_matches_original(original_fd, receipt)
                or not descriptor_matches_original(restored_fd, receipt)
            ):
                raise FinalizationError(
                    "evidence rollback original changed after restore"
                )
        finally:
            os.close(restored_fd)
        verify_target_parent_chain(
            str(receipt["repo"]),
            str(receipt["target"]),
            receipt["target_parent_chain"],
        )
        os.fsync(original_quarantine_root_fd)
        os.fsync(candidate_quarantine_root_fd)
        os.fsync(parent_fd)
    finally:
        if canonical_candidate_fd >= 0:
            os.close(canonical_candidate_fd)
        if retained_candidate_fd >= 0:
            os.close(retained_candidate_fd)
        if original_fd >= 0:
            os.close(original_fd)
        if original_quarantine_fd >= 0:
            os.close(original_quarantine_fd)
        if candidate_quarantine_fd >= 0:
            os.close(candidate_quarantine_fd)
        if candidate_quarantine_root_fd >= 0:
            os.close(candidate_quarantine_root_fd)
        if original_quarantine_root_fd >= 0:
            os.close(original_quarantine_root_fd)
        os.close(parent_fd)


def isolated_evidence_commit(
    runtime: dict[str, str],
    plan: dict[str, object],
    publisher_identity: tuple[str, str],
    directory: Path,
) -> tuple[str, str, str, bytes]:
    """Build, but do not publish, a HEAD-only evidence commit and index variant."""
    repo = runtime["agents_vault_root"]
    git_dir = runtime["agents_git_dir"]
    target = str(plan["target_path"])
    expected_head = str(plan["base_head"])
    if git(repo, "rev-parse", "HEAD", git_dir=git_dir).stdout.strip() != expected_head:
        raise FinalizationError("Agents HEAD changed after evidence planning")
    head_candidate = private_candidate(plan, "head")
    index_candidate = private_candidate(plan, "index")
    review_patch = stable_regular_bytes(Path(str(plan["review_patch_path"])))
    if hashlib.sha256(review_patch).hexdigest() != plan["evidence_diff_sha256"]:
        raise FinalizationError("sealed evidence review patch changed")
    current_head_blob = git_object_bytes(
        repo, git_dir, f"{expected_head}:{target}"
    )
    if hashlib.sha256(current_head_blob).hexdigest() != plan["head_source_sha256"]:
        raise FinalizationError("evidence HEAD source differs from review")
    if canonical_patch(target, current_head_blob, head_candidate) != review_patch:
        raise FinalizationError("evidence candidate differs from reviewed hunk")
    head_blob = run_local_command(
        ["git", f"--git-dir={git_dir}", f"--work-tree={repo}", "hash-object", "-w", "--stdin"],
        input=head_candidate,
        check=True,
        capture_output=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    ).stdout.decode("ascii").strip()
    index_blob = run_local_command(
        ["git", f"--git-dir={git_dir}", f"--work-tree={repo}", "hash-object", "-w", "--stdin"],
        input=index_candidate,
        check=True,
        capture_output=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    ).stdout.decode("ascii").strip()
    index_path = allocate_private_entry_path(
        directory,
        prefix=".evidence-index-work-",
        entry_name="index",
    )
    index_contract = None
    try:
        git(repo, "read-tree", expected_head, git_dir=git_dir, index_file=index_path)
        git(
            repo, "update-index", "--add", "--cacheinfo", f"100644,{head_blob},{target}",
            git_dir=git_dir, index_file=index_path,
        )
        if git(
            repo, "diff", "--cached", "--check", git_dir=git_dir,
            index_file=index_path, check=False,
        ).returncode != 0:
            raise FinalizationError("evidence-only index failed diff check")
        scan_staged(runtime["gitleaks_bin"], repo, index_path)
        tree = git(repo, "write-tree", git_dir=git_dir, index_file=index_path).stdout.strip()
        message = "docs(task): record daily publication evidence"
        commit = git(
            repo, "commit-tree", tree, "-p", expected_head, git_dir=git_dir,
            publisher_identity=publisher_identity, input_text=message + "\n",
        ).stdout.strip()
        index_content, index_identity = index_file_contract(Path(index_path))
        index_contract = {
            "sha256": hashlib.sha256(index_content).hexdigest(),
            "identity": index_identity,
        }
    finally:
        try:
            retain_path_no_replace(
                index_path,
                expected=index_contract,
                label="evidence-only temporary index",
                prefix=".evidence-index-retained-",
                allow_missing=index_contract is None,
            )
        except AtomicTransactionError as cleanup_error:
            raise FinalizationError(
                f"evidence-only index cleanup failed closed: {cleanup_error}"
            )
    if git(
        repo, "show", "-s", "--format=%P", commit, git_dir=git_dir
    ).stdout.strip() != expected_head:
        raise FinalizationError("evidence commit parent differs from expected HEAD")
    paths = [
        value for value in git(
            repo, "diff", "--name-only", "--no-renames", "-z",
            expected_head, commit, git_dir=git_dir,
        ).stdout.split("\0") if value
    ]
    if paths != [target]:
        raise FinalizationError("evidence commit contains an unexpected path")
    return commit, head_blob, index_blob, index_candidate


def evidence_recovery(
    receipt: dict[str, object],
    head_updated: bool,
    index_updated: bool,
) -> dict[str, object]:
    """Expose bounded, repo-relative tombstone data for manual recovery."""
    original_root_fd = -1
    original_quarantine_fd = -1
    candidate_root_fd = -1
    candidate_quarantine_fd = -1
    original_fd = -1
    candidate_fd = -1
    try:
        original_root_fd, original_quarantine_fd = open_receipt_quarantine(receipt)
        candidate_root_fd, candidate_quarantine_fd = (
            open_receipt_candidate_quarantine(receipt)
        )
        original_fd = os.open(
            "artifact",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=original_quarantine_fd,
        )
        candidate_fd = os.open(
            "artifact",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=candidate_quarantine_fd,
        )
        if not descriptor_matches_original(
            original_fd, receipt
        ) or not descriptor_matches_candidate(candidate_fd, receipt):
            raise FinalizationError("evidence recovery tombstone contract changed")
    finally:
        for descriptor in (
            original_fd,
            candidate_fd,
            original_quarantine_fd,
            candidate_quarantine_fd,
            original_root_fd,
            candidate_root_fd,
        ):
            if descriptor >= 0:
                os.close(descriptor)
    target = str(receipt["target"])
    relative = PurePosixPath(target)
    if (
        relative.is_absolute()
        or str(relative) != target
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in target)
        or "\\" in target
    ):
        raise FinalizationError("evidence recovery target is not repo-relative")
    base_head = str(receipt["base_head"])
    candidate_head = str(receipt["candidate_head"])
    if not re.fullmatch(r"[0-9a-f]{40}", base_head) or not re.fullmatch(
        r"[0-9a-f]{40}", candidate_head
    ):
        raise FinalizationError("evidence recovery HEAD contract is invalid")
    recovery: dict[str, object] = {
        "target_path": target,
        "quarantine_scope": "agents_git_dir",
        "quarantine_root_identity": list(
            receipt["original_quarantine_root_identity"]
        ),
        "base_head": base_head,
        "candidate_head": candidate_head,
        "original_restored": bool(receipt.get("original_restored", False)),
        "original_tombstone": {
            "directory": str(receipt["original_quarantine_name"]),
            "directory_identity": list(
                receipt["original_quarantine_identity"]
            ),
            "entry": "artifact",
            "identity": list(receipt["original_identity"]),
            "sha256": str(receipt["original_sha256"]),
            "size": int(receipt["original_size"]),
            "mode": int(receipt["original_full_mode"]),
        },
        "candidate": {
            "identity": list(receipt["candidate_identity"]),
            "sha256": str(receipt["candidate_sha256"]),
            "size": int(receipt["candidate_size"]),
            "mode": int(receipt["candidate_mode"]),
        },
        "head_updated": head_updated,
        "index_updated": index_updated,
    }
    rollback_name = receipt.get("rollback_candidate_quarantine_name")
    if isinstance(rollback_name, str):
        if (
            "/" in rollback_name
            or "\\" in rollback_name
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in rollback_name
            )
            or not rollback_name.startswith(
                ".publication-evidence-candidate-"
            )
        ):
            raise FinalizationError(
                "evidence recovery candidate tombstone is invalid"
            )
        recovery["rollback_candidate_tombstone"] = {
            "directory": rollback_name,
            "directory_identity": list(
                receipt["rollback_candidate_quarantine_identity"]
            ),
            "entry": "artifact",
            "identity": list(receipt["candidate_identity"]),
            "sha256": str(receipt["candidate_sha256"]),
            "size": int(receipt["candidate_size"]),
            "mode": int(receipt["candidate_mode"]),
        }
    return recovery


def partial_result(
    runtime: dict[str, str],
    pre: dict[str, object],
    initial: dict[str, object],
    reason: str,
    receipt: dict[str, object] | None = None,
    head_updated: bool = False,
    index_updated: bool = False,
    evidence_review: dict[str, object] | None = None,
) -> dict[str, object]:
    """Capture actual local/remote evidence state after any failure."""
    finalization_commits: list[str] = []

    def observed_vault(key: str, prefix: str) -> dict[str, object]:
        nonlocal finalization_commits
        repo = runtime[f"{prefix}_vault_root"]
        local_known = True
        try:
            head: str | None = git(repo, "rev-parse", "HEAD").stdout.strip()
            clean = (
                False
                if pre.get(key, {}).get("worktree_capture_scope") == "index_only"
                else dirty_status(repo)[0]
            )
            if key == "agents_vault":
                finalization_commits = git(
                    repo,
                    "rev-list",
                    "--reverse",
                    f"{initial[key]['local_head']}..{head}",
                ).stdout.splitlines()
        except (
            FinalizationError,
            OSError,
            subprocess.SubprocessError,
            TransportError,
        ):
            head = None
            clean = False
            local_known = False
            if key == "agents_vault":
                finalization_commits = []
        try:
            remote: str | None = remote_head(
                repo,
                runtime[f"{prefix}_remote_url"],
                runtime[f"{prefix}_git_dir"],
            )
        except (
            FinalizationError,
            OSError,
            subprocess.SubprocessError,
            TransportError,
        ):
            remote = None
        hashes = [
            *initial[key].get("commit_hashes", []),
            *(finalization_commits if key == "agents_vault" else []),
        ]
        observed = dict(initial[key])
        observed.update(
            {
                "commit_status": (
                    "complete" if hashes else ("not_started" if local_known else "failed")
                ),
                "commit_hashes": hashes,
                "push_status": (
                    "complete"
                    if head is not None and remote is not None and remote == head
                    else "failed"
                ),
                "local_head": head,
                "remote_head": remote,
                "clean": clean,
            }
        )
        return observed

    agents = observed_vault("agents_vault", "agents")
    user = observed_vault("user_vault", "user")
    result = dict(initial)
    result.update(
        {
            "outcome": "partial_publication",
            "phase": "evidence_finalization",
            "agents_vault": agents,
            "user_vault": user,
            "evidence_finalization_commit": (
                finalization_commits[-1]
                if finalization_commits
                else None
            ),
            "next_action": reason,
        }
    )
    if receipt is not None:
        result["evidence_recovery"] = evidence_recovery(
            receipt, head_updated, index_updated
        )
    if evidence_review is not None:
        result["evidence_review"] = evidence_review
    return result


def publish_success_result_after_cleanup(
    output: Path,
    result: dict[str, object],
    shared_index_candidate: tuple[str, dict[str, object]],
) -> None:
    """Publish success only after the private shared-index entry is retained."""
    retain_path_no_replace(
        shared_index_candidate[0],
        expected=shared_index_candidate[1],
        label="evidence shared-index candidate",
        prefix=".evidence-shared-index-retained-",
    )
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str]) -> int:
    """Finalize reviewed evidence and emit the final automation result."""
    if len(argv) not in {9, 10, 11}:
        print(
            "usage: commit-push-publication-evidence.py RUNTIME PRE INITIAL "
            "EVIDENCE_PLAN EVIDENCE_REVIEW FINAL REVIEW_STATUS CONTEXT "
            "[REVIEW_FAILURE_REASON] [EVIDENCE_REVIEW_DIAGNOSTIC]",
            file=sys.stderr,
        )
        return 64
    output = Path(argv[6])
    review_failure_reason = argv[9] if len(argv) == 10 else ""
    evidence_review_diagnostic: dict[str, object] | None = None
    runtime: dict[str, str] = {}
    pre: dict[str, object] = {}
    initial: dict[str, object] = {}
    worktree_receipt: dict[str, object] | None = None
    head_updated = False
    index_updated = False
    mutation_progress = {"head_updated": False, "index_updated": False}
    shared_index_candidate: tuple[str, dict[str, object]] | None = None
    try:
        runtime = json.loads(stable_regular_bytes(Path(argv[1])))
        pre = json.loads(stable_regular_bytes(Path(argv[2])))
        initial = json.loads(stable_regular_bytes(Path(argv[3])))
        plan = json.loads(stable_regular_bytes(Path(argv[4])))
        runtime, pre = context_bound_inputs(
            runtime,
            pre,
            stable_regular_bytes(Path(argv[8])),
            plan["publication_context_sha256"],
        )
        publisher_identity = validated_publisher_identity(runtime)
        review_status = int(argv[7])
        evidence_review_diagnostic = read_evidence_review_diagnostic(
            Path(argv[10]) if len(argv) == 11 else None,
            review_status,
            review_failure_reason,
        )
        if len(argv) == 11 and evidence_review_diagnostic["result_present"]:
            review_result_sha256 = evidence_review_diagnostic["result_sha256"]
            actual_review_sha256 = hashlib.sha256(
                stable_regular_bytes(Path(argv[5]))
            ).hexdigest()
            if review_result_sha256 != actual_review_sha256:
                raise FinalizationError("evidence review result digest differs from diagnostic")
        if review_status != 0:
            raise FinalizationError(
                review_failure_reason
                or (
                    "evidence review is not approved and digest-bound: "
                    f"{evidence_review_diagnostic['reason_code']}"
                )
            )
        review = json.loads(stable_regular_bytes(Path(argv[5])))
        if review != {
            "outcome": "approved",
            "target_path": plan["target_path"],
            "evidence_diff_sha256": plan["evidence_diff_sha256"],
            "publication_context_sha256": plan["publication_context_sha256"],
            "review_status": "quality_ok",
            "next_action": None,
        }:
            raise FinalizationError("evidence review is not approved and digest-bound")
        repo = runtime["agents_vault_root"]
        target = plan["target_path"]
        if target == ".obsidian" or target.startswith(".obsidian/"):
            raise FinalizationError("evidence target is forbidden")
        if git(repo, "branch", "--show-current").stdout.strip() != "main":
            raise FinalizationError("Agents Vault is not on main")
        if git(repo, "rev-parse", "HEAD").stdout.strip() != initial[
            "agents_vault"
        ]["local_head"]:
            raise FinalizationError("Agents Vault moved after the initial push")
        if control_digest(repo) != pre["agents_vault"]["git_control_sha256"]:
            raise FinalizationError("Git config or hooks changed")
        baseline = plan["pre_evidence_state"]
        prepared_state = capture_complete(argv[1])
        if prepared_state != baseline:
            raise FinalizationError("Vault state changed after evidence planning")
        evidence_commit, _head_blob, index_blob, index_candidate = (
            isolated_evidence_commit(runtime, plan, publisher_identity, output.parent)
        )
        shared_index_candidate = prepare_shared_index_candidate(
            repo,
            str(runtime["agents_git_dir"]),
            baseline["agents_vault"],
            target,
            index_blob,
            output.parent,
        )
        # Candidate construction mutates only the object database. Rebind both
        # Vaults immediately before the first worktree/ref/index mutation.
        if capture_complete(argv[1]) != baseline:
            raise FinalizationError("Vault state changed before evidence commit")
        worktree_candidate = private_candidate(plan, "worktree")
        worktree_receipt = {
            "base_head": str(plan["base_head"]),
            "candidate_head": evidence_commit,
        }
        worktree_receipt = replace_worktree_candidate(
            repo,
            target,
            str(plan["worktree_source_sha256"]),
            worktree_candidate,
            str(runtime["agents_git_dir"]),
            recovery_receipt=worktree_receipt,
        )
        publish_evidence_head_and_index(
            repo,
            str(runtime["agents_git_dir"]),
            baseline["agents_vault"],
            str(plan["base_head"]),
            evidence_commit,
            shared_index_candidate[0],
            shared_index_candidate[1],
            mutation_progress,
        )
        head_updated = mutation_progress["head_updated"]
        index_updated = mutation_progress["index_updated"]
        committed_state = capture_complete(argv[1])
        if committed_state["user_vault"] != baseline["user_vault"]:
            raise FinalizationError("User Vault changed during evidence commit")
        validate_residual_after_commit(
            baseline["agents_vault"],
            committed_state["agents_vault"],
            target,
            index_blob,
            worktree_candidate,
            repo,
        )
        if committed_state["agents_vault"]["local_head"] != evidence_commit:
            raise FinalizationError("evidence commit HEAD mismatch")
        if control_digest(repo) != pre["agents_vault"]["git_control_sha256"]:
            raise FinalizationError("Git config or hooks changed during finalization")
        finalize_worktree_candidate(worktree_receipt)
        before_remote = remote_head(
            repo, runtime["agents_remote_url"], runtime["agents_git_dir"]
        )
        if before_remote != initial["agents_vault"]["remote_head"]:
            raise FinalizationError("remote main raced before evidence push")
        remote = push_evidence_with_retry(
            repo,
            str(runtime["agents_remote_url"]),
            str(runtime["agents_git_dir"]),
            evidence_commit,
            before_remote,
        )
        clean = (
            False
            if committed_state["agents_vault"].get("worktree_capture_scope")
            == "index_only"
            else dirty_status(repo)[0]
        )
        if remote != evidence_commit:
            raise FinalizationError("final evidence state is not published")
        user_repo = runtime["user_vault_root"]
        user_head = git(user_repo, "rev-parse", "HEAD").stdout.strip()
        user_remote = remote_head(
            user_repo, runtime["user_remote_url"], runtime["user_git_dir"]
        )
        user_clean = (
            False
            if committed_state["user_vault"].get("worktree_capture_scope")
            == "index_only"
            else dirty_status(user_repo)[0]
        )
        if (
            git(user_repo, "branch", "--show-current").stdout.strip() != "main"
            or user_head != initial["user_vault"]["local_head"]
            or user_remote != initial["user_vault"]["remote_head"]
            or user_remote != user_head
            or control_digest(user_repo)
            != pre["user_vault"]["git_control_sha256"]
            or control_digest(repo)
            != pre["agents_vault"]["git_control_sha256"]
        ):
            raise FinalizationError("final two-Vault state changed during evidence work")
        agents = dict(initial["agents_vault"])
        agents.update(
            {
                "commit_hashes": [
                    *initial["agents_vault"]["commit_hashes"],
                    evidence_commit,
                ],
                "push_status": "complete",
                "local_head": evidence_commit,
                "remote_head": remote,
                "clean": clean,
            }
        )
        user = dict(initial["user_vault"])
        user["clean"] = user_clean
        final = dict(initial)
        final.update(
            {
                "outcome": "success",
                "phase": "evidence_finalization",
                "agents_vault": agents,
                "user_vault": user,
                "evidence_finalization_commit": evidence_commit,
                "evidence_recovery": evidence_recovery(
                    worktree_receipt, head_updated, index_updated
                ),
                "evidence_review": evidence_review_diagnostic,
                "next_action": None,
            }
        )
        if shared_index_candidate is None:
            raise FinalizationError("evidence shared-index candidate is unavailable")
        completed_shared_index_candidate = shared_index_candidate
        # Ownership leaves this scope before retention starts. If the pathname
        # was replaced, the helper retains the third-party inode and raises;
        # the generic finally block must not attempt to claim it a second time.
        shared_index_candidate = None
        publish_success_result_after_cleanup(
            output, final, completed_shared_index_candidate
        )
        return 0
    except (
        AtomicTransactionError,
        FinalizationError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        TransportError,
    ) as exc:
        head_updated = mutation_progress["head_updated"]
        index_updated = mutation_progress["index_updated"]
        recovery_receipt = (
            worktree_receipt
            if isinstance(worktree_receipt, dict)
            and "original_identity" in worktree_receipt
            and "candidate_identity" in worktree_receipt
            else None
        )
        if recovery_receipt is not None and not head_updated:
            if recovery_receipt.get("canonical_candidate_installed") or recovery_receipt.get(
                "original_detached"
            ):
                try:
                    rollback_worktree_candidate(recovery_receipt)
                except Exception as rollback_exc:
                    exc = FinalizationError(
                        f"{exc}; evidence worktree rollback failed: {rollback_exc}"
                    )
            else:
                recovery_receipt["original_restored"] = True
        print(f"evidence finalization failed:{exc}", file=sys.stderr)
        if runtime and pre and initial:
            try:
                output.write_text(
                    json.dumps(
                        partial_result(
                            runtime,
                            pre,
                            initial,
                            f"Repair evidence finalization without force: {exc}",
                            recovery_receipt,
                            head_updated,
                            index_updated,
                            evidence_review_diagnostic,
                        ),
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except Exception as capture_exc:
                print(f"could not capture partial state:{capture_exc}", file=sys.stderr)
        return 75
    finally:
        if shared_index_candidate is not None:
            try:
                retain_path_no_replace(
                    shared_index_candidate[0],
                    expected=shared_index_candidate[1],
                    label="evidence shared-index candidate",
                    prefix=".evidence-shared-index-retained-",
                )
            except AtomicTransactionError as cleanup_error:
                raise FinalizationError(
                    f"evidence shared-index cleanup failed closed: {cleanup_error}"
                )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""Materialize dirty blobs and local-only commit patches for publication review."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

from git_diff_digest import unified_diff_added_content
from isolated_git_transport import (
    LOCAL_COMMAND_TIMEOUT_SECONDS,
    kill_process_group,
    run_local_command,
)
from trusted_gitleaks import trusted_scan_invocation


MAX_BLOB_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_GUARD_PATCH_BYTES = (2 * MAX_BLOB_BYTES) + (1024 * 1024)
SCAN_TIMEOUT_SECONDS = 30
OID_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class DirtySnapshotError(RuntimeError):
    """Raised when captured dirty state cannot be materialized safely."""


def run_bounded(arguments: list[str], limit: int) -> bytes:
    """Read bounded stdout under one wall deadline and reap the process group."""
    process = subprocess.Popen(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd="/",
        env=clean_git_environment(),
        start_new_session=True,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + LOCAL_COMMAND_TIMEOUT_SECONDS
    chunks: list[bytes] = []
    total = 0
    finished = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise DirtySnapshotError("Git review input exceeded its deadline")
            chunk = os.read(
                process.stdout.fileno(), min(65536, limit + 1 - total)
            )
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise DirtySnapshotError("review input exceeds per-file size limit")
            chunks.append(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DirtySnapshotError("Git review input exceeded its deadline")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise DirtySnapshotError("Git review input exceeded its deadline") from exc
        finished = True
    finally:
        selector.close()
        process.stdout.close()
        if not finished:
            kill_process_group(process)
    if return_code != 0:
        raise DirtySnapshotError("Git review input is unavailable")
    return b"".join(chunks)


def clean_git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_LITERAL_PATHSPECS"] = "1"
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    environment["GIT_CONFIG_COUNT"] = "3"
    environment["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    environment["GIT_CONFIG_VALUE_0"] = "false"
    environment["GIT_CONFIG_KEY_1"] = "core.trustctime"
    environment["GIT_CONFIG_VALUE_1"] = "false"
    environment["GIT_CONFIG_KEY_2"] = "core.checkStat"
    environment["GIT_CONFIG_VALUE_2"] = "minimal"
    return environment


def clean_scan_environment() -> dict[str, str]:
    """Return a Git- and gitleaks-config-neutral scanner environment."""
    environment = clean_git_environment()
    for key in tuple(environment):
        if key.startswith("GITLEAKS_"):
            environment.pop(key)
    return environment


def read_regular_beneath(root: Path, relative: PurePosixPath) -> bytes:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    opened = [os.open(root, directory_flags)]
    try:
        current = opened[-1]
        for component in relative.parts[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            opened.append(current)
        descriptor = os.open(
            relative.parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BLOB_BYTES:
                raise DirtySnapshotError("dirty worktree input is not a bounded regular file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 65536):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise DirtySnapshotError("dirty worktree input changed while being read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def read_blob(git_dir: str, worktree: str, relative: str, oid: str) -> bytes:
    if OID_PATTERN.fullmatch(oid) is None:
        raise DirtySnapshotError("dirty entry has an invalid Git object ID")
    size = run_local_command(
        ["git", f"--git-dir={git_dir}", "cat-file", "-s", oid],
        check=False,
        capture_output=True,
        text=True,
        cwd="/",
        env=clean_git_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    if size.returncode == 0:
        try:
            object_size = int(size.stdout.strip())
        except ValueError as exc:
            raise DirtySnapshotError("dirty blob size is invalid") from exc
        if object_size > MAX_BLOB_BYTES:
            raise DirtySnapshotError("dirty blob exceeds per-file size limit")
        content = run_bounded(
            ["git", f"--git-dir={git_dir}", "cat-file", "blob", oid],
            MAX_BLOB_BYTES,
        )
    else:
        content = read_regular_beneath(Path(worktree), PurePosixPath(relative))
    hashed = run_local_command(
        ["git", f"--git-dir={git_dir}", "hash-object", "--stdin"],
        input=content,
        check=True,
        capture_output=True,
        cwd="/",
        env=clean_git_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    if hashed.stdout.decode("ascii").strip() != oid:
        raise DirtySnapshotError("dirty blob bytes do not match captured object ID")
    if len(content) > MAX_BLOB_BYTES:
        raise DirtySnapshotError("dirty blob exceeds per-file size limit")
    return content


def read_head_blob(git_dir: str, head: object, relative: str) -> bytes:
    """Read the exact HEAD blob for one path without consulting the worktree."""
    if not isinstance(head, str) or OID_PATTERN.fullmatch(head) is None:
        raise DirtySnapshotError("review state HEAD is unavailable for residual guard")
    listing = run_bounded(
        ["git", f"--git-dir={git_dir}", "ls-tree", "-z", head, "--", relative],
        64 * 1024,
    )
    if not listing:
        return b""
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        raise DirtySnapshotError("HEAD residual identity is ambiguous")
    metadata, listed_path = records[0].split(b"\t", 1)
    fields = metadata.split()
    if listed_path != os.fsencode(relative) or len(fields) != 3:
        raise DirtySnapshotError("HEAD residual identity does not match captured path")
    if fields[1] != b"blob":
        return b""
    oid = fields[2].decode("ascii")
    size = run_local_command(
        ["git", f"--git-dir={git_dir}", "cat-file", "-s", oid],
        check=False,
        capture_output=True,
        text=True,
        cwd="/",
        env=clean_git_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    try:
        object_size = int(size.stdout.strip()) if size.returncode == 0 else -1
    except ValueError as exc:
        raise DirtySnapshotError("HEAD residual size is invalid") from exc
    if object_size < 0 or object_size > MAX_BLOB_BYTES:
        raise DirtySnapshotError("HEAD residual exceeds deterministic guard bounds")
    return run_bounded(
        ["git", f"--git-dir={git_dir}", "cat-file", "blob", oid],
        MAX_BLOB_BYTES,
    )


def diff_added_bytes(baseline: bytes, candidate: bytes) -> tuple[bytes, bool]:
    """Return exact no-index added lines, or the full candidate for binary data."""
    with tempfile.TemporaryDirectory(prefix="daily-news-residual-guard-") as temporary:
        root = Path(temporary)
        before = root / "before"
        after = root / "after"
        before.write_bytes(baseline)
        after.write_bytes(candidate)
        try:
            completed = run_local_command(
                [
                    "git", "diff", "--no-index", "--no-color", "--unified=0",
                    "--binary", "--no-ext-diff", "--no-textconv", "--",
                    str(before), str(after),
                ],
                cwd="/",
                check=False,
                capture_output=True,
                env=clean_git_environment(),
                timeout=SCAN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise DirtySnapshotError("residual guard diff exceeded its deadline") from exc
    if completed.returncode not in {0, 1}:
        raise DirtySnapshotError("residual guard diff is unavailable")
    if len(completed.stdout) > MAX_GUARD_PATCH_BYTES:
        raise DirtySnapshotError("residual guard diff exceeds its size limit")
    binary = any(
        line == b"GIT binary patch" for line in completed.stdout.splitlines()
    )
    if binary:
        return candidate, True
    added = unified_diff_added_content(completed.stdout)
    return added, False


def gitleaks_rejects(gitleaks_bin: str, content: bytes) -> bool:
    """Scan only candidate additions with the pinned scanner and no ambient config."""
    if not content:
        return False
    try:
        with trusted_scan_invocation() as (scan_prefix, pass_fds):
            completed = run_local_command(
                [
                    gitleaks_bin,
                    *scan_prefix,
                    "stdin",
                ],
                input=content,
                cwd="/",
                check=False,
                capture_output=True,
                env=clean_scan_environment(),
                timeout=SCAN_TIMEOUT_SECONDS,
                pass_fds=pass_fds,
            )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return completed.returncode != 0


def residual_guard_reason(
    runtime: dict[str, object],
    state: dict[str, object],
    git_key: str,
    relative: str,
    content: bytes,
) -> str | None:
    """Return a deterministic defer reason for an unsafe residual candidate."""
    if relative == ".obsidian" or relative.startswith(".obsidian/"):
        return "dirty_entry_forbidden_obsidian_path"
    head = state.get("local_head")
    gitleaks_bin = runtime.get("gitleaks_bin")
    if not isinstance(head, str) or not isinstance(gitleaks_bin, str):
        return "dirty_entry_residual_guard_unavailable"
    try:
        baseline = read_head_blob(str(runtime[git_key]), head, relative)
        added, binary = diff_added_bytes(baseline, content)
    except (DirtySnapshotError, OSError, subprocess.SubprocessError):
        return "dirty_entry_residual_guard_unavailable"
    home_bytes = os.fsencode(str(Path.home()))
    guarded_content = content if binary else added
    if home_bytes and home_bytes in guarded_content:
        return "dirty_entry_added_machine_home_path"
    if gitleaks_rejects(gitleaks_bin, guarded_content):
        return "dirty_entry_secret_scan_rejected"
    return None


def read_commit_patch(
    git_dir: str,
    commit: str,
    parents: list[str],
    expected_sha256: str | None = None,
) -> bytes:
    """Read one immutable local-only commit as a bounded first-parent patch."""
    if OID_PATTERN.fullmatch(commit) is None or any(
        OID_PATTERN.fullmatch(parent) is None for parent in parents
    ):
        raise DirtySnapshotError("local commit has an invalid Git object ID")
    if parents:
        arguments = [
            "git", f"--git-dir={git_dir}", "diff", "--binary", "--full-index",
            "--no-ext-diff", "--no-textconv", parents[0], commit,
        ]
    else:
        arguments = [
            "git", f"--git-dir={git_dir}", "diff-tree", "--root", "-p",
            "--binary", "--full-index", "--no-ext-diff", "--no-textconv", commit,
        ]
    content = run_bounded(arguments, MAX_BLOB_BYTES)
    if (
        expected_sha256 is not None
        and hashlib.sha256(content).hexdigest() != expected_sha256
    ):
        raise DirtySnapshotError("local commit patch differs from expected digest")
    return content


def mkdir_exclusive(parent_descriptor: int, name: str) -> int:
    os.mkdir(name, 0o700, dir_fd=parent_descriptor)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_descriptor)


def write_exclusive(directory_descriptor: int, name: str, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_descriptor)
    try:
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_relative(value: object) -> str:
    if not isinstance(value, str):
        raise DirtySnapshotError("dirty path is not a string")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\x00" in value:
        raise DirtySnapshotError("dirty path is not safely repo-relative")
    return value


def materialize(
    runtime: dict[str, object], pre_state: dict[str, object], destination: Path
) -> dict[str, object]:
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_descriptor = os.open(destination, root_flags)
    total_bytes = {"agents": 0, "user": 0}
    manifest: dict[str, object] = {"version": 4, "vaults": {}, "local_commits": {}}
    try:
        snapshots_descriptor = mkdir_exclusive(destination_descriptor, "dirty-snapshots")
        try:
            for label, state_key, git_key in (
                ("agents", "agents_vault", "agents_git_dir"),
                ("user", "user_vault", "user_git_dir"),
            ):
                vault_descriptor = mkdir_exclusive(snapshots_descriptor, label)
                try:
                    state = pre_state[state_key]
                    entries = state["dirty_entries"]
                    if not isinstance(entries, list):
                        raise DirtySnapshotError("dirty entries are not a list")
                    unstable_snapshot = (
                        state.get("capture_reason") == "vault_state_snapshot_unstable"
                    )
                    seen: set[str] = set()
                    output_entries: list[dict[str, object]] = []
                    for index, entry in enumerate(entries):
                        relative = validate_relative(entry["path"])
                        if relative in seen:
                            raise DirtySnapshotError("dirty path is duplicated")
                        seen.add(relative)
                        mode = entry["mode"]
                        oid = entry["git_blob_oid"]
                        if unstable_snapshot:
                            output_entries.append(
                                {
                                    "path": relative,
                                    "git_blob_oid": oid,
                                    "mode": mode,
                                    "snapshot": None,
                                    "sha256": None,
                                    "materialization_status": "deferred",
                                    "materialization_reason": (
                                        "vault_state_snapshot_unstable"
                                    ),
                                }
                            )
                            continue
                        if relative == ".obsidian" or relative.startswith(
                            ".obsidian/"
                        ):
                            output_entries.append(
                                {
                                    "path": relative,
                                    "git_blob_oid": oid,
                                    "mode": mode,
                                    "snapshot": None,
                                    "sha256": None,
                                    "materialization_status": "deferred",
                                    "materialization_reason": (
                                        "dirty_entry_forbidden_obsidian_path"
                                    ),
                                }
                            )
                            continue
                        if mode is None and oid is None:
                            output_entries.append(
                                {
                                    "path": relative,
                                    "git_blob_oid": None,
                                    "mode": None,
                                    "snapshot": None,
                                    "sha256": None,
                                    "materialization_status": "not_required",
                                    "materialization_reason": None,
                                }
                            )
                            continue
                        deferred_reason = None
                        if mode not in {"100644", "100755"} or not isinstance(oid, str):
                            deferred_reason = "dirty_entry_mode_not_materializable"
                            content = b""
                        else:
                            try:
                                content = read_blob(
                                    str(runtime[git_key]),
                                    str(runtime[f"{label}_vault_root"]),
                                    relative,
                                    oid,
                                )
                            except (DirtySnapshotError, OSError, subprocess.SubprocessError):
                                content = b""
                                deferred_reason = "dirty_entry_snapshot_unavailable"
                        if deferred_reason is None:
                            deferred_reason = residual_guard_reason(
                                runtime,
                                pre_state[state_key],
                                git_key,
                                relative,
                                content,
                            )
                        if (
                            deferred_reason is None
                            and total_bytes[label] + len(content) > MAX_TOTAL_BYTES
                        ):
                            deferred_reason = "review_snapshot_total_size_limit"
                        if deferred_reason is not None:
                            output_entries.append(
                                {
                                    "path": relative,
                                    "git_blob_oid": oid,
                                    "mode": mode,
                                    "snapshot": None,
                                    "sha256": None,
                                    "materialization_status": "deferred",
                                    "materialization_reason": deferred_reason,
                                }
                            )
                            continue
                        total_bytes[label] += len(content)
                        filename = f"{index:04d}.blob"
                        write_exclusive(vault_descriptor, filename, content)
                        output_entries.append(
                            {
                                "path": relative,
                                "git_blob_oid": oid,
                                "mode": mode,
                                "snapshot": f"dirty-snapshots/{label}/{filename}",
                                "sha256": hashlib.sha256(content).hexdigest(),
                                "materialization_status": "available",
                                "materialization_reason": None,
                            }
                        )
                    manifest["vaults"][state_key] = output_entries
                finally:
                    os.close(vault_descriptor)
        finally:
            os.close(snapshots_descriptor)
        commits_descriptor = mkdir_exclusive(destination_descriptor, "commit-snapshots")
        try:
            for label, state_key, git_key in (
                ("agents", "agents_vault", "agents_git_dir"),
                ("user", "user_vault", "user_git_dir"),
            ):
                vault_descriptor = mkdir_exclusive(commits_descriptor, label)
                try:
                    state = pre_state[state_key]
                    commits = state.get("local_commits", [])
                    if not isinstance(commits, list):
                        raise DirtySnapshotError("local commits are not a list")
                    unstable_snapshot = (
                        state.get("capture_reason") == "vault_state_snapshot_unstable"
                    )
                    output_commits: list[dict[str, object]] = []
                    for index, commit in enumerate(commits):
                        if unstable_snapshot:
                            output_commits.append(
                                {
                                    **commit,
                                    "patch_sha256": None,
                                    "snapshot": None,
                                    "sha256": None,
                                    "materialization_status": "blocked",
                                    "materialization_reason": (
                                        "vault_state_snapshot_unstable"
                                    ),
                                }
                            )
                            continue
                        parents = commit.get("parents")
                        if not isinstance(parents, list):
                            raise DirtySnapshotError("local commit metadata is invalid")
                        try:
                            content = read_commit_patch(
                                str(runtime[git_key]),
                                str(commit.get("commit")),
                                [str(parent) for parent in parents],
                            )
                            unavailable = (
                                total_bytes[label] + len(content) > MAX_TOTAL_BYTES
                            )
                        except (DirtySnapshotError, OSError, subprocess.SubprocessError):
                            content = b""
                            unavailable = True
                        if unavailable:
                            output_commits.append(
                                {
                                    **commit,
                                    "patch_sha256": None,
                                    "snapshot": None,
                                    "sha256": None,
                                    "materialization_status": "blocked",
                                    "materialization_reason": "local_commit_snapshot_unavailable",
                                }
                            )
                            continue
                        total_bytes[label] += len(content)
                        filename = f"{index:04d}.patch"
                        write_exclusive(vault_descriptor, filename, content)
                        output_commits.append(
                            {
                                **commit,
                                "patch_sha256": hashlib.sha256(content).hexdigest(),
                                "snapshot": f"commit-snapshots/{label}/{filename}",
                                "sha256": hashlib.sha256(content).hexdigest(),
                                "materialization_status": "available",
                                "materialization_reason": None,
                            }
                        )
                    manifest["local_commits"][state_key] = output_commits
                finally:
                    os.close(vault_descriptor)
        finally:
            os.close(commits_descriptor)
        encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n").encode()
        write_exclusive(destination_descriptor, "dirty-snapshots.json", encoded)
    finally:
        os.close(destination_descriptor)
    return manifest


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: stage-dirty-review-inputs.py RUNTIME_CONTEXT PRE_STATE DESTINATION",
            file=sys.stderr,
        )
        return 64
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre_state = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        destination = Path(argv[3])
        if not destination.is_absolute():
            raise DirtySnapshotError("destination must be absolute")
        materialize(runtime, pre_state, destination)
        os.chmod(destination, 0o000)
    except (
        DirtySnapshotError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"dirty review snapshot failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

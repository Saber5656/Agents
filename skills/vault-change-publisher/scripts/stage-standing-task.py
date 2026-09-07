#!/usr/bin/env python3
"""Copy validated standing/authorization tasks into an isolated run boundary."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path


MAX_TASK_BYTES = 2 * 1024 * 1024
SNAPSHOT_RETRY_ATTEMPTS = 30
SNAPSHOT_RETRY_DELAY_SECONDS = 1.0
RETRYABLE_FILESYSTEM_ERRNOS = frozenset(
    errno_value
    for errno_value in (
        getattr(errno, "EDEADLK", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EBUSY", None),
    )
    if errno_value is not None
)
RETRYABLE_SNAPSHOT_ERRORS = frozenset(
    {
        "standing task changed while being read",
    }
)


class SnapshotError(RuntimeError):
    """Raised when the standing task cannot be snapshotted safely."""


def open_beneath(root: Path, relative: Path) -> tuple[int, list[int]]:
    """Open a regular file beneath root without following any path component."""
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise SnapshotError("standing task is not a safe Vault-relative path")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    opened = [os.open(root, directory_flags)]
    try:
        current = opened[-1]
        for component in relative.parts[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            opened.append(current)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=current)
        return descriptor, opened
    except BaseException:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise


def read_regular_beneath(root: Path, relative: Path) -> bytes:
    descriptor, opened = open_beneath(root, relative)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SnapshotError("standing task is not a regular file")
        if metadata.st_size > MAX_TASK_BYTES:
            raise SnapshotError("standing task exceeds size limit")
        chunks: list[bytes] = []
        remaining = MAX_TASK_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > MAX_TASK_BYTES:
            raise SnapshotError("standing task exceeds size limit")
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
            raise SnapshotError("standing task changed while being read")
        content.decode("utf-8")
        return content
    except UnicodeDecodeError as exc:
        raise SnapshotError("standing task is not UTF-8") from exc
    finally:
        os.close(descriptor)
        for directory_descriptor in reversed(opened):
            os.close(directory_descriptor)


def read_regular_beneath_with_retry(root: Path, relative: Path) -> bytes:
    """Retry bounded File Provider locks and within-read identity churn."""
    for attempt in range(SNAPSHOT_RETRY_ATTEMPTS):
        try:
            return read_regular_beneath(root, relative)
        except OSError as exc:
            if (
                exc.errno not in RETRYABLE_FILESYSTEM_ERRNOS
                or attempt + 1 >= SNAPSHOT_RETRY_ATTEMPTS
            ):
                raise
            time.sleep(SNAPSHOT_RETRY_DELAY_SECONDS)
        except SnapshotError as exc:
            if (
                str(exc) not in RETRYABLE_SNAPSHOT_ERRORS
                or attempt + 1 >= SNAPSHOT_RETRY_ATTEMPTS
            ):
                raise
            time.sleep(SNAPSHOT_RETRY_DELAY_SECONDS)
    raise SnapshotError("Vault task snapshot retry budget was exhausted")


def write_exclusive(staging_root: Path, destination_name: str, content: bytes) -> None:
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    staging_descriptor = os.open(staging_root, directory_flags)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(destination_name, flags, 0o600, dir_fd=staging_descriptor)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.close(staging_descriptor)


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(
            "usage: stage-standing-task.py RUNTIME_CONTEXT DESTINATION "
            "[standing|authorization]",
            file=sys.stderr,
        )
        return 64
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        task_kind = argv[3] if len(argv) == 4 else "standing"
        if task_kind not in ("standing", "authorization"):
            raise SnapshotError("task kind must be standing or authorization")
        source = Path(runtime[f"{task_kind}_task_path"])
        destination_name = f"{task_kind}-task.md"
        agents_root = Path(runtime["agents_vault_root"])
        staging_root = Path(argv[2])
        if not source.is_absolute() or not agents_root.is_absolute() or not staging_root.is_absolute():
            raise SnapshotError("source and destination must be absolute")
        try:
            relative = source.relative_to(agents_root)
        except ValueError as exc:
            raise SnapshotError(f"{task_kind} task is outside Agents Vault") from exc
        content = read_regular_beneath_with_retry(agents_root, relative)
        if task_kind == "authorization":
            expected_digest = runtime["authorization_task_sha256"]
            actual_digest = hashlib.sha256(content).hexdigest()
            if actual_digest != expected_digest:
                raise SnapshotError("authorization task digest does not match pinned evidence")
        write_exclusive(staging_root, destination_name, content)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, SnapshotError) as exc:
        print(f"Vault task snapshot failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

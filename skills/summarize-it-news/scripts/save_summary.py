#!/usr/bin/env python3
"""Persist one summary with descriptor-relative, no-follow filesystem operations."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JST_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")


class SaveError(RuntimeError):
    """Represent a fail-closed summary persistence error."""


def copy_stream(source_fd: int, target_fd: int) -> None:
    """Copy bytes between open descriptors and fsync the completed target."""
    while chunk := os.read(source_fd, 1024 * 1024):
        view = memoryview(chunk)
        while view:
            written = os.write(target_fd, view)
            if written <= 0:
                raise SaveError("could not write summary content")
            view = view[written:]
    os.fsync(target_fd)


def open_child_directory(parent_fd: int, name: str) -> int:
    """Open or create one directory below parent without following symlinks."""
    try:
        os.mkdir(name, mode=0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise SaveError("target path contains a symlink or non-directory") from exc


def save_summary(
    mode: str,
    output_root: Path,
    summary_date: str,
    content_file: Path,
    started_at: str,
) -> dict[str, object]:
    """Create one collision-safe summary and return the output contract."""
    if mode not in {"scheduled_automation", "interactive_manual"}:
        raise SaveError("unsupported summary save mode")
    if not output_root.is_absolute():
        raise SaveError("output root must be absolute")
    if mode == "scheduled_automation":
        try:
            resolved_output = output_root.resolve(strict=True)
        except OSError as exc:
            raise SaveError("output root must be a real directory") from exc
        collection_root_value = os.environ.get("COLLECTION_OUTPUT_ROOT")
        if not collection_root_value:
            raise SaveError(
                "scheduled automation requires COLLECTION_OUTPUT_ROOT"
            )
        collection_root = Path(collection_root_value).expanduser()
        if not collection_root.is_absolute():
            raise SaveError("COLLECTION_OUTPUT_ROOT must be absolute")
        try:
            resolved_output.relative_to(collection_root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise SaveError(
                "scheduled output root must stay below COLLECTION_OUTPUT_ROOT"
            ) from exc
        for key in ("AGENTS_VAULT_ROOT", "USER_VAULT_ROOT"):
            configured = os.environ.get(key)
            if not configured:
                continue
            vault_root = Path(configured).expanduser()
            if not vault_root.is_absolute():
                raise SaveError(f"{key} must be absolute when configured")
            try:
                resolved_output.relative_to(vault_root.resolve(strict=True))
            except ValueError:
                continue
            except OSError as exc:
                raise SaveError(f"{key} must resolve to a real directory") from exc
            raise SaveError("scheduled automation must not save directly to a Vault")
    if not DATE_PATTERN.fullmatch(summary_date):
        raise SaveError("summary date must use YYYY-MM-DD")
    if not JST_PATTERN.fullmatch(started_at):
        raise SaveError("collection started_at must be ISO 8601 JST")
    try:
        parsed_date = datetime.strptime(summary_date, "%Y-%m-%d").date()
        parsed_started_at = datetime.fromisoformat(started_at)
    except ValueError as exc:
        raise SaveError("summary date or collection start time is invalid") from exc
    if parsed_started_at.utcoffset() != timedelta(hours=9):
        raise SaveError("collection started_at must use the +09:00 offset")
    if parsed_started_at.date() != parsed_date:
        raise SaveError("summary date must match collection started_at in JST")

    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(content_file, source_flags)
    except OSError as exc:
        raise SaveError("content file is not a readable regular file") from exc
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise SaveError("content file is not a readable regular file")

        root_flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            root_flags |= os.O_NOFOLLOW
        try:
            root_fd = os.open(output_root, root_flags)
        except OSError as exc:
            raise SaveError("output root must be a real directory") from exc

        directory_fd = root_fd
        opened_fds = [root_fd]
        try:
            for component in summary_date.split("-"):
                directory_fd = open_child_directory(directory_fd, component)
                opened_fds.append(directory_fd)

            base = f"SUMMARY-IT-NEWS-{summary_date}"
            for index in range(1, 10000):
                suffix = "" if index == 1 else f"-{index}"
                filename = f"{base}{suffix}.md"
                target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    target_flags |= os.O_NOFOLLOW
                try:
                    target_fd = os.open(
                        filename, target_flags, 0o644, dir_fd=directory_fd
                    )
                except OSError as exc:
                    if exc.errno == errno.EEXIST:
                        continue
                    raise SaveError("could not create summary") from exc
                try:
                    copy_stream(source_fd, target_fd)
                except Exception as exc:
                    os.close(target_fd)
                    try:
                        os.unlink(filename, dir_fd=directory_fd)
                    except OSError as cleanup_exc:
                        raise SaveError(
                            "could not remove incomplete summary"
                        ) from cleanup_exc
                    if isinstance(exc, SaveError):
                        raise
                    raise SaveError("could not copy summary content") from exc
                else:
                    os.close(target_fd)
                completed_at = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(
                    timespec="seconds"
                )
                target_path = output_root.joinpath(
                    *summary_date.split("-"), filename
                )
                return {
                    "summary_status": "created",
                    "summary_path": str(target_path),
                    "collection_started_at": started_at,
                    "collection_completed_at": completed_at,
                }
            raise SaveError("summary collision limit exceeded")
        finally:
            for descriptor in reversed(opened_fds):
                os.close(descriptor)
    finally:
        os.close(source_fd)


def main(argv: list[str]) -> int:
    """Parse CLI arguments and emit exactly one JSON result."""
    if len(argv) != 6:
        result = {
            "summary_status": "failed",
            "reason": (
                "usage: save_summary.py MODE OUTPUT_ROOT YYYY-MM-DD "
                "CONTENT_FILE STARTED_AT"
            ),
            "summary_path": None,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 64
    try:
        result = save_summary(
            argv[1], Path(argv[2]), argv[3], Path(argv[4]), argv[5]
        )
    except SaveError as exc:
        result = {
            "summary_status": "failed",
            "reason": str(exc),
            "summary_path": None,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""Execute a read-only review command with one digest-bound stdin snapshot."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path


MAX_REQUEST_BYTES = 900_000
MAX_METRICS_BYTES = 256 * 1024


class PinnedReviewError(RuntimeError):
    """Raised when the request or its audit metrics are not stable."""


def read_stable(path: Path, maximum: int) -> bytes:
    """Read a regular file through one no-follow descriptor and seal its inode."""
    # O_NONBLOCK prevents an attacker-controlled FIFO/device replacement from
    # making the preflight wait forever before fstat can reject it.
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PinnedReviewError(f"review input is not a regular file: {path}")
        if before.st_size > maximum:
            raise PinnedReviewError(f"review input exceeds the allowed size: {path}")
        content = bytearray()
        while chunk := os.read(descriptor, min(1024 * 1024, maximum + 1 - len(content))):
            content.extend(chunk)
            if len(content) > maximum:
                raise PinnedReviewError(f"review input grew beyond the allowed size: {path}")
        after = os.fstat(descriptor)
        contract = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if contract != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise PinnedReviewError(f"review input changed while it was read: {path}")
        return bytes(content)
    finally:
        os.close(descriptor)


def validate_request(request: bytes, metrics_path: Path, expected_digest: str) -> None:
    """Bind the bytes about to be consumed to the preparation metrics."""
    metrics_bytes = read_stable(metrics_path, MAX_METRICS_BYTES)
    if hashlib.sha256(metrics_bytes).hexdigest() != expected_digest:
        raise PinnedReviewError("review input metrics digest mismatch")
    try:
        metrics = json.loads(metrics_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PinnedReviewError("review input metrics are unreadable") from exc
    if not isinstance(metrics, dict) or metrics.get("status") != "ready":
        raise PinnedReviewError("review input metrics are not ready")
    if metrics.get("publication_context_projection") != "review_bounded_v2":
        raise PinnedReviewError("review input projection version is invalid")
    request_bytes = metrics.get("request_bytes")
    request_chars = metrics.get("request_chars")
    request_digest = metrics.get("request_sha256")
    if (
        type(request_bytes) is not int
        or type(request_chars) is not int
        or request_bytes != len(request)
        or request_bytes > MAX_REQUEST_BYTES
        or request_bytes < 0
        or not isinstance(request_digest, str)
        or hashlib.sha256(request).hexdigest() != request_digest
    ):
        raise PinnedReviewError("review request bytes do not match sealed metrics")
    try:
        actual_chars = len(request.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PinnedReviewError("review request is not valid UTF-8") from exc
    if actual_chars != request_chars or request_chars > MAX_REQUEST_BYTES or request_chars < 0:
        raise PinnedReviewError("review request character count does not match sealed metrics")


def run(request_path: Path, metrics_path: Path, metrics_digest: str, command: list[str]) -> int:
    """Read once, validate once, and pipe exactly those bytes to the child."""
    if not command:
        raise PinnedReviewError("review command is missing")
    request = read_stable(request_path, MAX_REQUEST_BYTES)
    validate_request(request, metrics_path, metrics_digest)
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            process.communicate(input=request)
        except (BrokenPipeError, OSError) as exc:
            process.kill()
            process.wait()
            raise PinnedReviewError(f"review command stdin failed: {exc}") from exc
    except OSError as exc:
        raise PinnedReviewError(f"review command could not start: {exc}") from exc
    return process.returncode if process.returncode >= 0 else 128 - process.returncode


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    if len(argv) < 5 or argv[4] != "--":
        print(
            "usage: run-pinned-review.py REQUEST METRICS METRICS_SHA -- COMMAND [ARGS...]",
            file=sys.stderr,
        )
        return 64
    try:
        return run(Path(argv[1]), Path(argv[2]), argv[3], argv[5:])
    except (OSError, PinnedReviewError, ValueError) as exc:
        print(f"pinned review execution failed:{exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

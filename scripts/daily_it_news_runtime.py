#!/usr/bin/env python3
"""Process and file-read primitives shared by the daily IT-news runner.

run_command isolates each child in its own POSIX process group so a timeout
can terminate the whole subtree (including grandchildren) instead of leaving
orphans that keep writing to shared files. read_verified guards against
transient empty/locked reads of completed artefacts without masking real
corruption.
"""
from __future__ import annotations

import errno
import hashlib
import os
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

_TERM_GRACE = 2.0
_KILL_GRACE = 2.0
_TRANSIENT_ERRNOS = {errno.EDEADLK, errno.EAGAIN, errno.ETIMEDOUT, errno.EBUSY, errno.EIO}


class ProcessStartError(OSError):
    """The command has not started, so it cannot have produced remote effects."""


def _killpg(pid: int, sig: int) -> None:
    try:
        # start_new_session pins the group id to the original child pid. The
        # leader may already be reaped while descendants still hold that group.
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def run_command(argv: list[str], *, input: str | bytes | None = None, text: bool | None = None,
                 capture_output: bool = False, cwd: str | Path | None = None,
                 env: dict[str, str] | None = None, timeout: float | None = None,
                 **kwargs: Any) -> subprocess.CompletedProcess:
    popen_kwargs: dict[str, Any] = dict(cwd=cwd, env=env, text=text, **kwargs)
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    stdin = subprocess.PIPE if input is not None else None
    stdout = subprocess.PIPE if capture_output else None
    stderr = subprocess.PIPE if capture_output else None
    try:
        proc = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, **popen_kwargs)
    except OSError as exc:
        raise ProcessStartError(exc.errno, str(exc)) from exc
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)
    except subprocess.TimeoutExpired as first:
        out, err = first.output, first.stderr
        if os.name == "posix":
            _killpg(proc.pid, signal.SIGTERM)
            try:
                out, err = proc.communicate(timeout=_TERM_GRACE)
            except subprocess.TimeoutExpired:
                _killpg(proc.pid, signal.SIGKILL)
                try:
                    out, err = proc.communicate(timeout=_KILL_GRACE)
                except subprocess.TimeoutExpired:
                    for stream in (proc.stdin, proc.stdout, proc.stderr):
                        if stream is not None:
                            try:
                                stream.close()
                            except OSError:
                                pass
                    try:
                        proc.wait(timeout=_KILL_GRACE)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                # Descendants may ignore TERM and close their inherited pipes,
                # allowing communicate() to return before they have exited.
                _killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
            try:
                out, err = proc.communicate(timeout=_KILL_GRACE)
            except subprocess.TimeoutExpired:
                pass
        raise subprocess.TimeoutExpired(argv, timeout, output=out, stderr=err) from None


def _read_once(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"not a regular file: {path}")
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise RuntimeError(f"empty read: {path}")
        return data
    finally:
        os.close(fd)


def read_verified(path: str | Path, expected_sha256: str | None = None, *, retries: int = 3,
                   delay: float = 0.5, _sleep=time.sleep, _reader=None) -> bytes:
    path = Path(path)
    reader = _reader if _reader is not None else _read_once
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            data = reader(path)
        except OSError as exc:
            if exc.errno in _TRANSIENT_ERRNOS and attempt < retries - 1:
                last_exc = exc
                _sleep(delay)
                continue
            raise
        except RuntimeError as exc:
            if attempt < retries - 1:
                last_exc = exc
                _sleep(delay)
                continue
            raise
        if not data:
            if attempt < retries - 1:
                last_exc = RuntimeError(f"empty read: {path}")
                _sleep(delay)
                continue
            raise RuntimeError(f"empty read: {path}")
        if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
            if attempt < retries - 1:
                last_exc = RuntimeError(f"sha256 mismatch for {path}")
                _sleep(delay)
                continue
            raise RuntimeError(f"sha256 mismatch for {path}")
        return data
    raise last_exc if last_exc is not None else RuntimeError(f"failed to read {path}")

#!/usr/bin/env python3
"""Bind every publication scan to the reviewed Gitleaks default-rule config."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


CONFIG_NAME = "gitleaks-default.toml"
EXPECTED_CONFIG_SHA256 = (
    "e57d46cacfc7941601ef6ef3a831f9d020e636972d1dc9d0c3678996da125bfe"
)
MAX_CONFIG_BYTES = 4096


class TrustedGitleaksError(OSError):
    """Represent a missing, replaced, or modified trusted scanner config."""


def validated_config_bytes() -> bytes:
    """Read the adjacent no-follow config through one stable descriptor."""
    path = Path(__file__).with_name(CONFIG_NAME)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CONFIG_BYTES:
            raise TrustedGitleaksError("trusted Gitleaks config is not a bounded file")
        content = bytearray()
        while chunk := os.read(descriptor, 4096):
            content.extend(chunk)
            if len(content) > MAX_CONFIG_BYTES:
                raise TrustedGitleaksError("trusted Gitleaks config exceeds its bound")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise TrustedGitleaksError("trusted Gitleaks config changed while read")
    finally:
        os.close(descriptor)
    result = bytes(content)
    if hashlib.sha256(result).hexdigest() != EXPECTED_CONFIG_SHA256:
        raise TrustedGitleaksError("trusted Gitleaks config digest is invalid")
    return result


def validated_config_path() -> str:
    """Return the adjacent path only after validating its current bytes."""
    validated_config_bytes()
    return str(Path(__file__).with_name(CONFIG_NAME).absolute())


@contextmanager
def trusted_scan_invocation() -> Iterator[tuple[list[str], tuple[int, ...]]]:
    """Yield exact v8 flags bound to an inherited immutable config snapshot."""
    content = validated_config_bytes()
    with tempfile.TemporaryFile(mode="w+b") as snapshot:
        snapshot.write(content)
        snapshot.flush()
        os.fchmod(snapshot.fileno(), 0o400)
        os.lseek(snapshot.fileno(), 0, os.SEEK_SET)
        descriptor = snapshot.fileno()
        yield (
            [
                "--no-banner",
                "--redact",
                "--ignore-gitleaks-allow",
                "--gitleaks-ignore-path",
                os.devnull,
                "--config",
                f"/dev/fd/{descriptor}",
            ],
            (descriptor,),
        )

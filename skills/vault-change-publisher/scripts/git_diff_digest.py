#!/usr/bin/env python3
"""Compute evidence diff digests under one isolated Git contract."""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import TypeVar

from isolated_git_transport import LOCAL_COMMAND_TIMEOUT_SECONDS, run_local_command


DiffText = TypeVar("DiffText", str, bytes)


def clean_git_environment() -> dict[str, str]:
    """Remove Git overrides and disable system/global configuration."""
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


def unified_diff_added_content(patch: DiffText) -> DiffText:
    """Extract added hunk content without confusing `+++` content with headers."""
    binary = isinstance(patch, bytes)
    hunk_prefix = b"@@ " if binary else "@@ "
    addition = b"+" if binary else "+"
    context = b" " if binary else " "
    deletion = b"-" if binary else "-"
    marker = b"\\" if binary else "\\"
    empty = b"" if binary else ""
    in_hunk = False
    added: list[DiffText] = []
    lf = b"\n" if binary else "\n"
    segments = patch.split(lf)
    for index, segment in enumerate(segments):
        line = segment + (lf if index < len(segments) - 1 else empty)
        if line.startswith(hunk_prefix):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith(addition):
            added.append(line[1:])
            continue
        if line.startswith((context, deletion, marker)):
            continue
        in_hunk = False
    return empty.join(added)  # type: ignore[return-value]


def git_diff_digest(repo: str, relative: str, *, cached: bool = False) -> str:
    """Hash the exact raw binary diff for one repo-relative target."""
    command = [
        "git",
        "-C",
        repo,
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.trustctime=false",
        "-c",
        "core.checkStat=minimal",
        "diff",
    ]
    if cached:
        command.append("--cached")
    command.extend(("--binary", "--no-ext-diff", "--no-textconv", "--", relative))
    result = run_local_command(
        command,
        check=True,
        capture_output=True,
        env=clean_git_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return hashlib.sha256(result.stdout).hexdigest()

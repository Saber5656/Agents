#!/usr/bin/env python3
"""Fetch both Vault main refs through fixed, sanitized Git transports."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from isolated_git_transport import (
    IsolatedGitTransport,
    LOCAL_COMMAND_TIMEOUT_SECONDS,
    TransportError,
    run_local_command,
)


class FetchError(RuntimeError):
    """Represent an invalid runtime context or rejected fixed fetch."""


def clean_environment() -> dict[str, str]:
    """Remove ambient Git overrides while preserving transport credentials."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
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
    return environment


def fetch_main(repo: str, git_dir: str, remote_url: str) -> None:
    """Fetch one literal remote main into the exact tracking ref without force."""
    fetched = None
    last_error: Exception | None = None
    with IsolatedGitTransport(git_dir) as transport:
        for attempt in range(3):
            try:
                before = transport.run(
                    "ls-remote", "--exit-code", remote_url, "refs/heads/main"
                ).stdout.split()
                if len(before) != 2:
                    raise FetchError("could not resolve fixed remote main")
                result = transport.run(
                    "fetch",
                    "--no-tags",
                    "--no-recurse-submodules",
                    "--no-write-fetch-head",
                    remote_url,
                    "refs/heads/main:refs/remotes/origin/main",
                    check=False,
                )
                if result.returncode != 0:
                    raise FetchError("fixed main fetch command failed")
                candidate = transport.run(
                    "rev-parse", "--verify", "refs/remotes/origin/main"
                ).stdout.strip()
                after = transport.run(
                    "ls-remote", "--exit-code", remote_url, "refs/heads/main"
                ).stdout.split()
                if len(after) == 2 and before[0] == candidate == after[0]:
                    fetched = candidate
                    break
                last_error = FetchError("fixed main fetch did not stabilize")
            except (FetchError, subprocess.SubprocessError, TransportError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(0.2)
    if fetched is None:
        raise FetchError("fixed main fetch did not stabilize") from last_error
    local_command = [
        "git",
        f"--git-dir={git_dir}",
        f"--work-tree={repo}",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.trustctime=false",
        "-c",
        "core.checkStat=minimal",
    ]
    existing = run_local_command(
        [*local_command, "rev-parse", "--verify", "refs/remotes/origin/main"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    old = existing.stdout.strip() if existing.returncode == 0 else "0" * 40
    if old != "0" * 40 and old != fetched:
        ancestry = run_local_command(
            [*local_command, "merge-base", "--is-ancestor", old, fetched],
            check=False,
            capture_output=True,
            text=True,
            env=clean_environment(),
            timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
        )
        if ancestry.returncode != 0:
            raise FetchError("fixed main fetch would move tracking history backwards")
    run_local_command(
        [*local_command, "update-ref", "refs/remotes/origin/main", fetched, old],
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )


def main(argv: list[str]) -> int:
    """Load the resolved context and fetch both exact Vault main refs."""
    if len(argv) != 2:
        print("usage: fetch-vault-main.py RUNTIME_CONTEXT", file=sys.stderr)
        return 64
    try:
        runtime = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        for prefix in ("agents", "user"):
            fetch_main(
                runtime[f"{prefix}_vault_root"],
                runtime[f"{prefix}_git_dir"],
                runtime[f"{prefix}_remote_url"],
            )
        return 0
    except (
        FetchError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        TransportError,
    ) as exc:
        print(f"fixed fetch blocked:{exc}", file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

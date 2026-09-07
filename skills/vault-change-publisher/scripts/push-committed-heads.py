#!/usr/bin/env python3
"""Validate local publication commits, then push only fixed main refs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from isolated_git_transport import (
    LOCAL_COMMAND_TIMEOUT_SECONDS,
    TransportError,
    run_local_command,
    run_transport,
)
from trusted_gitleaks import trusted_scan_invocation


class PushError(RuntimeError):
    """Represent invalid local publication state or a rejected push."""


SCAN_TIMEOUT_SECONDS = 120


def clean_git_environment() -> dict[str, str]:
    """Disable ambient Git controls and repository-local fsmonitor commands."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.trustctime",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "core.checkStat",
            "GIT_CONFIG_VALUE_2": "minimal",
        }
    )
    return environment


def read_regular_nofollow(path: Path) -> bytes:
    """Read one stable regular control file without following a symlink."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PushError("publication review is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
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
            raise PushError("publication review changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def git(
    repo: str,
    *arguments: str,
    check: bool = True,
    git_dir: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git without a shell and capture sanitized text output."""
    environment = clean_git_environment()
    repository_arguments = (
        [f"--git-dir={git_dir}", f"--work-tree={repo}"]
        if git_dir is not None
        else ["-C", repo]
    )
    if arguments and arguments[0] in {"ls-remote", "push", "fetch"}:
        if git_dir is None:
            raise PushError("network Git operation requires an explicit Git directory")
        return run_transport(git_dir, *arguments, check=check, text=True)
    return run_local_command(
        [
            "git", *repository_arguments,
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false",
            "-c", "core.trustctime=false",
            "-c", "core.checkStat=minimal",
            *arguments,
        ],
        check=check,
        capture_output=True,
        text=True,
        env=environment,
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )


def dirty_digest(repo: str) -> str:
    """Hash the complete porcelain status used by the publication contract."""
    status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
    return hashlib.sha256(status.encode("utf-8")).hexdigest()


def current_local(repo: str, pre_state: dict[str, object]) -> dict[str, object]:
    """Capture local progress relative to the immutable pre-collection state."""
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    digest = (
        str(pre_state["dirty_digest"])
        if pre_state.get("worktree_capture_scope") == "index_only"
        else dirty_digest(repo)
    )
    commits = git(
        repo,
        "rev-list",
        "--reverse",
        f"{pre_state['local_head']}..{head}",
    ).stdout.splitlines()
    clean = (
        pre_state.get("worktree_capture_scope") == "full"
        and digest == hashlib.sha256(b"").hexdigest()
    )
    return {
        "commit_status": "complete" if commits else "not_started",
        "commit_hashes": commits,
        "pre_local_head": pre_state["local_head"],
        "local_head": head,
        "pre_dirty_digest": pre_state["dirty_digest"],
        "post_dirty_digest": digest,
        "clean": clean,
    }


def git_control_digest(repo: str) -> str:
    """Hash repository config and hooks that could redirect a later push."""
    git_dir = Path(
        git(repo, "rev-parse", "--absolute-git-dir").stdout.strip()
    )
    common_dir = Path(
        git(
            repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
    )
    control = hashlib.sha256()
    git_marker = Path(repo) / ".git"
    control.update(b"worktree-git-entry\0")
    control.update(f"{git_marker.lstat().st_mode:o}".encode("ascii"))
    control.update(b"\0")
    if git_marker.is_symlink():
        control.update(b"symlink\0")
        control.update(os.fsencode(os.readlink(git_marker)))
    elif git_marker.is_file():
        control.update(b"file\0")
        control.update(git_marker.read_bytes())
    else:
        control.update(b"directory\0")
    seen_control_paths: set[Path] = set()
    for config_path in (common_dir / "config", git_dir / "config.worktree"):
        if config_path in seen_control_paths or not os.path.lexists(config_path):
            continue
        seen_control_paths.add(config_path)
        control.update(b"config\0")
        control.update(str(config_path).encode("utf-8"))
        control.update(b"\0")
        control.update(f"{config_path.lstat().st_mode:o}".encode("ascii"))
        control.update(b"\0")
        if config_path.is_symlink():
            control.update(b"symlink\0")
            control.update(os.fsencode(os.readlink(config_path)))
        else:
            control.update(config_path.read_bytes())
        control.update(b"\0")
    hooks = common_dir / "hooks"
    if os.path.lexists(hooks):
        control.update(b"hooks\0")
        control.update(f"{hooks.lstat().st_mode:o}".encode("ascii"))
        control.update(b"\0")
        walk_hooks = False
        if hooks.is_symlink():
            control.update(b"symlink\0")
            control.update(os.fsencode(os.readlink(hooks)))
            control.update(b"\0")
            try:
                target_mode = hooks.stat().st_mode
            except FileNotFoundError:
                control.update(b"dangling\0")
            else:
                control.update(f"{target_mode:o}".encode("ascii"))
                control.update(b"\0")
                if hooks.is_dir():
                    control.update(b"target-directory\0")
                    walk_hooks = True
                else:
                    control.update(b"target-unsupported\0")
        elif not hooks.is_dir():
            control.update(b"unsupported\0")
        else:
            control.update(b"directory\0")
            walk_hooks = True
        if walk_hooks:
            for root, directories, files in os.walk(hooks, followlinks=False):
                directories.sort()
                files.sort()
                for name in directories:
                    path = Path(root) / name
                    control.update(str(path.relative_to(common_dir)).encode("utf-8"))
                    control.update(b"\0")
                    control.update(f"{path.lstat().st_mode:o}".encode("ascii"))
                    control.update(b"\0")
                    if path.is_symlink():
                        control.update(b"symlink\0")
                        control.update(os.fsencode(os.readlink(path)))
                    else:
                        control.update(b"directory\0")
                    control.update(b"\0")
                for filename in files:
                    path = Path(root) / filename
                    control.update(str(path.relative_to(common_dir)).encode("utf-8"))
                    control.update(b"\0")
                    control.update(f"{path.lstat().st_mode:o}".encode("ascii"))
                    control.update(b"\0")
                    if path.is_symlink():
                        control.update(b"symlink\0")
                        control.update(os.fsencode(os.readlink(path)))
                    else:
                        control.update(path.read_bytes())
                    control.update(b"\0")
    return control.hexdigest()


def changed_paths(repo: str, old: str, new: str) -> list[str]:
    """Return all repo-relative paths changed across one commit range."""
    output = git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        old,
        new,
    ).stdout
    return sorted(value for value in output.split("\0") if value)


def commit_paths(repo: str, commit: str) -> list[str]:
    """Return repo-relative paths changed by one commit."""
    output = git(
        repo,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--name-only",
        "--no-renames",
        "-r",
        "-z",
        commit,
    ).stdout
    return sorted(value for value in output.split("\0") if value)


def commit_patch_sha256(repo: str, commit: str, parents: list[str]) -> str:
    """Hash the deterministic first-parent patch used by publication review."""
    arguments = (
        [
            "diff", "--binary", "--full-index", "--no-ext-diff",
            "--no-textconv", parents[0], commit,
        ]
        if parents
        else [
            "diff-tree", "--root", "-p", "--binary", "--full-index",
            "--no-ext-diff", "--no-textconv", commit,
        ]
    )
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    content = run_local_command(
        [
            "git", "-C", repo,
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "core.fsmonitor=false",
            "-c", "core.trustctime=false",
            "-c", "core.checkStat=minimal",
            *arguments,
        ],
        check=True,
        capture_output=True,
        env=environment,
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    ).stdout
    return hashlib.sha256(content).hexdigest()


def existing_commit_metadata(
    repo: str, pre_state: dict[str, object]
) -> list[dict[str, object]]:
    """Reconstruct the local-only history captured before collection."""
    commits = git(
        repo,
        "rev-list",
        "--reverse",
        "--topo-order",
        f"{pre_state['remote_head']}..{pre_state['local_head']}",
    ).stdout.splitlines()
    result: list[dict[str, object]] = []
    for commit in commits:
        parents = git(repo, "show", "-s", "--format=%P", commit).stdout.split()
        message = git(repo, "show", "-s", "--format=%B", commit).stdout.rstrip("\n")
        if parents:
            changed = changed_paths(repo, parents[0], commit)
        else:
            changed = commit_paths(repo, commit)
        result.append(
            {
                "commit": commit,
                "parents": parents,
                "tree": git(repo, "show", "-s", "--format=%T", commit).stdout.strip(),
                "message": message,
                "changed_paths": changed,
                "patch_sha256": commit_patch_sha256(repo, commit, parents),
            }
        )
    return result


def blob_sha256(repo: str, head: str, relative: str) -> str:
    """Hash one committed blob without checking out or following a symlink."""
    result = run_local_command(
        [
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
            "show",
            f"{head}:{relative}",
        ],
        check=True,
        capture_output=True,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("GIT_")
            },
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.trustctime",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "core.checkStat",
            "GIT_CONFIG_VALUE_2": "minimal",
        },
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def validate_blob_mode(repo: str, head: str, relative: str) -> None:
    """Require a normal non-executable Git blob for an installed Markdown artifact."""
    output = git(repo, "ls-tree", "-z", head, "--", relative).stdout
    if not output.endswith("\0") or output.count("\0") != 1:
        raise PushError("committed artifact tree entry is missing or ambiguous")
    line = output[:-1]
    metadata, separator, listed_path = line.partition("\t")
    fields = metadata.split()
    if (
        not separator
        or listed_path != relative
        or len(fields) != 3
        or fields[0] != "100644"
        or fields[1] != "blob"
    ):
        raise PushError("committed artifact is not a regular 100644 blob")


def validate_dirty_entry(
    repo: str, head: str, entry: dict[str, object]
) -> None:
    """Bind an approved pre-existing dirty path to its reviewed Git blob and mode."""
    output = git(repo, "ls-tree", "-z", head, "--", str(entry["path"])).stdout
    if output and (not output.endswith("\0") or output.count("\0") != 1):
        raise PushError("committed dirty path tree entry is ambiguous")
    line = output[:-1] if output else ""
    if entry["git_blob_oid"] is None:
        if line:
            raise PushError("approved deletion still exists in the committed tree")
        return
    metadata, separator, listed_path = line.partition("\t")
    fields = metadata.split()
    if (
        not separator
        or listed_path != entry["path"]
        or len(fields) != 3
        or fields[0] != entry["mode"]
        or fields[1] != "blob"
        or fields[2] != entry["git_blob_oid"]
    ):
        raise PushError("committed dirty path differs from approved blob or mode")


def validate_scope(
    repo: str,
    pre_state: dict[str, object],
    reported: dict[str, object],
    manifest: dict[str, object],
    carried: bool = False,
) -> None:
    """Bind actual commits and artifact blobs to an approved review manifest."""
    existing_commits = existing_commit_metadata(repo, pre_state)
    captured_identity = [
        {key: value for key, value in commit.items() if key != "patch_sha256"}
        for commit in existing_commits
    ]
    if (
        captured_identity != pre_state.get("local_commits", [])
        or existing_commits != manifest["approved_existing_commits"]
    ):
        raise PushError("local-only history differs from approved existing commits")
    if any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for commit in existing_commits
        for path in commit["changed_paths"]
    ):
        raise PushError("local-only history contains a forbidden .obsidian path")
    approved_groups = manifest["commit_groups"]
    approved_paths = sorted(
        path for group in approved_groups for path in group["paths"]
    )
    actual_paths = changed_paths(
        repo, str(pre_state["local_head"]), str(reported["local_head"])
    )
    if actual_paths != approved_paths:
        raise PushError("committed paths differ from approved initial scope")
    if any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in actual_paths
    ):
        raise PushError("publication commit contains a forbidden .obsidian path")
    for entry in manifest["approved_dirty_entries"]:
        validate_dirty_entry(
            repo, str(reported["local_head"]), entry
        )
    if carried:
        approved_existing_ids = [
            commit["commit"] for commit in manifest["approved_existing_commits"]
        ]
        artifact_targets = [
            artifact["target_path"] for artifact in manifest["reviewed_artifacts"]
        ]
        if (
            manifest.get("commit_required") is not False
            or approved_groups
            or not reported["commit_hashes"]
            or any(
                commit not in approved_existing_ids
                for commit in reported["commit_hashes"]
            )
            or len(artifact_targets) != 1
            or not any(
                artifact_targets[0] in commit_paths(repo, commit)
                for commit in reported["commit_hashes"]
            )
        ):
            raise PushError("carried commit does not match approved local-only history")
    else:
        if len(reported["commit_hashes"]) != len(approved_groups):
            raise PushError("commit count differs from approved commit groups")
        for commit, group in zip(reported["commit_hashes"], approved_groups):
            paths = set(commit_paths(repo, commit))
            if (
                paths != set(group["paths"])
                or git(repo, "show", "-s", "--format=%s", commit).stdout.strip()
                != group["message"]
            ):
                raise PushError("commit does not exactly match its ordered approved group")
    for artifact in manifest["reviewed_artifacts"]:
        validate_blob_mode(
            repo,
            str(reported["local_head"]),
            str(artifact["target_path"]),
        )
        if (
            blob_sha256(
                repo,
                str(reported["local_head"]),
                str(artifact["target_path"]),
            )
            != artifact["source_sha256"]
        ):
            raise PushError("committed artifact blob hash differs from review")


def scan_commits(gitleaks_bin: str, repo: str, old: str, new: str) -> None:
    """Scan the exact candidate commit range with pinned gitleaks."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GITLEAKS_") and not key.startswith("GIT_")
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
    try:
        with trusted_scan_invocation() as (scan_prefix, pass_fds):
            result = run_local_command(
                [
                    gitleaks_bin,
                    *scan_prefix,
                    "git",
                    "--log-opts",
                    f"{old}..{new}",
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
        raise PushError("gitleaks history scan exceeded its deadline") from exc
    if result.returncode != 0:
        raise PushError("gitleaks rejected the candidate commit range")


def validate_local(
    repo: str,
    pre_state: dict[str, object],
    reported: dict[str, object],
    current_state: dict[str, object] | None = None,
    artifact_path: str | None = None,
    carried: bool = False,
) -> str:
    """Bind a reported commit sequence to its mode-specific checkout state."""
    branch = git(repo, "branch", "--show-current").stdout.strip()
    local_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    mode = reported.get("publication_mode")
    if pre_state.get("worktree_capture_scope") == "index_only":
        if (
            current_state is None
            or current_state.get("worktree_capture_scope") != "index_only"
            or not isinstance(current_state.get("dirty_digest"), str)
        ):
            raise PushError("index-only residual state was not recaptured")
        actual_dirty = str(current_state["dirty_digest"])
    else:
        actual_dirty = dirty_digest(repo)
    if mode == "blocked":
        if (
            local_head != pre_state["local_head"]
            or reported.get("local_head") != local_head
            or reported.get("pre_local_head") != pre_state["local_head"]
            or reported.get("commit_hashes")
            or reported.get("commit_status") not in {"not_started", "not_required"}
            or actual_dirty != pre_state["dirty_digest"]
            or reported.get("post_dirty_digest") != actual_dirty
            or current_state is None
            or current_state.get("git_control_sha256") != pre_state.get("git_control_sha256")
        ):
            raise PushError("blocked Vault changed before push")
        return local_head
    upstream = git(repo, "rev-parse", "--abbrev-ref", "@{u}").stdout.strip()
    if branch != "main" or upstream != "origin/main":
        raise PushError("publication checkout is not main tracking origin/main")
    if local_head != reported["local_head"]:
        raise PushError("reported local head does not match repository")
    if carried:
        if (
            pre_state["local_head"] != local_head
            or reported.get("commit_status") != "complete"
            or not reported.get("commit_hashes")
            or reported.get("post_dirty_digest") != actual_dirty
            or current_state is None
        ):
            raise PushError("carried publication state is inconsistent")
        commits = git(
            repo,
            "rev-list",
            "--reverse",
            f"{reported['pre_local_head']}..{local_head}",
        ).stdout.splitlines()
        if commits != reported["commit_hashes"]:
            raise PushError("carried commit sequence no longer matches local history")
        for field in (
            "dirty_lines", "dirty_paths", "dirty_entries", "dirty_metadata",
            "staged_paths", "index_entries",
            "dirty_worktree_sha256", "dirty_digest", "diff_snapshot_sha256",
            "git_control_sha256", "branch", "upstream", "operation_in_progress",
            "remote_head", "worktree_capture_scope",
        ):
            if current_state.get(field) != pre_state.get(field):
                raise PushError(f"carried publication state changed before push: {field}")
        return local_head
    if reported["pre_local_head"] != pre_state["local_head"]:
        raise PushError("reported pre-publication head does not match captured state")
    if reported["pre_dirty_digest"] != pre_state["dirty_digest"]:
        raise PushError("reported pre-publication dirty digest does not match")
    if actual_dirty != reported["post_dirty_digest"]:
        raise PushError("reported residual status differs from the checkout")
    commits = git(
        repo,
        "rev-list",
        "--reverse",
        f"{pre_state['local_head']}..{local_head}",
    ).stdout.splitlines()
    if commits != reported["commit_hashes"]:
        raise PushError("reported commit sequence does not match local history")
    status = reported["commit_status"]
    if status == "complete" and not commits:
        raise PushError("complete commit status has no commits")
    if status == "not_required" and commits:
        raise PushError("not_required commit status changed history")
    if mode == "sweep":
        if actual_dirty != hashlib.sha256(b"").hexdigest() or not reported["clean"]:
            raise PushError("sweep publication checkout is not clean")
    elif mode == "own_only":
        if current_state is None or artifact_path is None:
            raise PushError("own_only residual state was not captured")
        for field in (
            "dirty_lines", "dirty_paths", "dirty_entries", "dirty_metadata",
            "staged_paths", "dirty_worktree_sha256", "dirty_digest",
            "diff_snapshot_sha256", "git_control_sha256", "branch", "upstream",
            "operation_in_progress", "remote_head", "worktree_capture_scope",
        ):
            if current_state.get(field) != pre_state.get(field):
                raise PushError(f"own_only residual changed before push: {field}")
        before_index = [
            entry for entry in pre_state["index_entries"]
            if entry["path"] != artifact_path
        ]
        after_index = [
            entry for entry in current_state["index_entries"]
            if entry["path"] != artifact_path
        ]
        if after_index != before_index:
            raise PushError("own_only changed a non-owned index entry")
    else:
        raise PushError("reported publication mode is invalid")
    return local_head


def capture_complete(runtime_file: str) -> dict[str, object]:
    """Reuse the canonical state helper immediately before fixed pushes."""
    helper = Path(__file__).with_name("capture-vault-state.py")
    result = run_local_command(
        [str(helper), "--index-only", "--include-local-history", runtime_file],
        check=True,
        capture_output=True,
        text=True,
        env=clean_git_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return json.loads(result.stdout)


def capture_one(repo: str) -> dict[str, object]:
    """Capture one Vault so a failure in its peer cannot suppress publication."""
    helper = Path(__file__).with_name("capture-vault-state.py")
    spec = importlib.util.spec_from_file_location("publication_capture_one", helper)
    if spec is None or spec.loader is None:
        raise PushError("could not load canonical Vault state helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.capture(
        repo,
        include_local_history=True,
        worktree_scope="index_only",
    )


def validate_and_push_one(
    runtime: dict[str, object],
    pre_state: dict[str, object],
    reported: dict[str, object],
    manifest: dict[str, object],
    artifact_path: str,
    prefix: str,
) -> tuple[str, str, str | None]:
    """Validate and fixed-push one Vault without consulting its peer Vault."""
    repo = str(runtime[f"{prefix}_vault_root"])
    git_dir = str(runtime[f"{prefix}_git_dir"])
    expected_remote = str(pre_state["remote_head"])
    observed_remote = expected_remote
    mode = reported.get("publication_mode")
    carried = mode != "blocked" and manifest.get("commit_required") is False
    try:
        if mode != manifest.get("publication_mode"):
            raise PushError("commit result mode differs from the approved review")
        state = capture_one(repo)
        local_head = validate_local(
            repo, pre_state, reported, state, artifact_path, carried
        )
        if git_control_digest(repo) != pre_state["git_control_sha256"]:
            raise PushError("Git config or hooks changed during local publication")
        if mode != "blocked":
            validate_scope(repo, pre_state, reported, manifest, carried)
            scan_commits(
                str(runtime["gitleaks_bin"]), repo,
                str(pre_state["remote_head"]), local_head,
            )
        push_status, observed_remote = push_one_independently(
            repo,
            str(runtime[f"{prefix}_remote_url"]),
            local_head,
            expected_remote,
            str(mode),
            git_dir,
        )
        return push_status, observed_remote, None
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
        TransportError,
        PushError,
    ) as exc:
        try:
            observed_remote = remote_head(
                repo, str(runtime[f"{prefix}_remote_url"]), git_dir
            )
        except (OSError, subprocess.SubprocessError, TransportError, PushError):
            pass
        return (
            "not_started" if mode == "blocked" else "failed",
            observed_remote,
            str(exc),
        )


def remote_head(repo: str, remote_url: str, git_dir: str | None = None) -> str:
    """Read the remote main object ID without trusting a stale local ref."""
    output = git(
        repo,
        "ls-remote",
        "--exit-code",
        remote_url,
        "refs/heads/main",
        git_dir=git_dir,
    ).stdout
    fields = output.split()
    if len(fields) != 2:
        raise PushError("could not resolve remote main")
    return fields[0]


def require_fast_forward_target(
    repo: str,
    expected_remote: str,
    local_head: str,
    git_dir: str | None = None,
) -> None:
    """Prove the fixed non-force update cannot rewrite reviewed history."""
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
        raise PushError("fixed push target is not a descendant of expected remote")


def push_one(
    repo: str,
    remote_url: str,
    local_head: str,
    required: bool,
    before: str,
    git_dir: str | None = None,
) -> tuple[str, str]:
    """Push exactly one validated object ID to refs/heads/main."""
    if before == local_head:
        return ("not_required", before)
    if not required:
        raise PushError("not_required publication unexpectedly needs a push")
    require_fast_forward_target(repo, before, local_head, git_dir)
    observed = before
    for _ in range(3):
        try:
            observed = remote_head(repo, remote_url, git_dir)
        except (PushError, subprocess.SubprocessError, TransportError):
            continue
        if observed == local_head:
            return ("complete", observed)
        if observed != before:
            return ("failed", observed)
        result = git(
            repo,
            "push",
            remote_url,
            f"{local_head}:refs/heads/main",
            check=False,
            git_dir=git_dir,
        )
        if result.returncode == 0:
            try:
                observed = remote_head(repo, remote_url, git_dir)
            except (PushError, subprocess.SubprocessError, TransportError):
                continue
            if observed == local_head:
                return ("complete", observed)
    return ("failed", observed)


def push_one_independently(
    repo: str,
    remote_url: str,
    local_head: str,
    expected_remote: str,
    publication_mode: str,
    git_dir: str | None = None,
) -> tuple[str, str]:
    """Attempt one Vault without allowing its race to suppress the other Vault."""
    try:
        observed = remote_head(repo, remote_url, git_dir)
    except (OSError, subprocess.SubprocessError, TransportError, PushError):
        return (
            "not_started" if publication_mode == "blocked" else "failed",
            expected_remote,
        )
    if publication_mode == "blocked":
        return ("not_started", observed)
    if publication_mode not in {"sweep", "own_only"}:
        return ("failed", observed)
    if observed == local_head:
        return (
            "not_required" if expected_remote == local_head else "complete",
            observed,
        )
    if observed != expected_remote:
        return ("failed", observed)
    try:
        return push_one(
            repo,
            remote_url,
            local_head,
            observed != local_head,
            observed,
            git_dir,
        )
    except (OSError, subprocess.SubprocessError, TransportError, PushError):
        return ("failed", observed)


def final_vault(
    reported: dict[str, object],
    push_status: str,
    remote: str | None,
    pre_state: dict[str, object] | None = None,
) -> dict[str, object]:
    """Convert one local commit result into the final publication shape."""
    commit_hashes = list(reported["commit_hashes"])
    if pre_state is not None and push_status in {"complete", "not_required"}:
        existing_hashes = [
            str(commit["commit"])
            for commit in pre_state.get("local_commits", [])
        ]
        commit_hashes = existing_hashes + [
            commit for commit in commit_hashes if commit not in existing_hashes
        ]
    return {
        "commit_status": "complete" if commit_hashes else reported["commit_status"],
        "commit_hashes": commit_hashes,
        "push_status": push_status,
        "local_head": reported["local_head"],
        "remote_head": remote,
        "clean": reported["clean"],
        "publication_mode": reported["publication_mode"],
        "deferred_cleanup": list(reported.get("deferred_cleanup", [])),
    }


def main(argv: list[str]) -> int:
    """Validate a ready result, perform fixed pushes, and emit final JSON."""
    if len(argv) != 10:
        print(
            "usage: push-committed-heads.py RUNTIME PRE_STATE COMMIT_RESULT "
            "RESULT PROCESS_STATUS CONTEXT REVIEW PLAN REVIEW_SHA",
            file=sys.stderr,
        )
        return 64
    output_path = Path(argv[4])
    runtime: dict[str, object] = {}
    pre: dict[str, object] = {}
    committed: dict[str, object] = {}
    try:
        runtime = json.loads(read_regular_nofollow(Path(argv[1])))
        pre = json.loads(read_regular_nofollow(Path(argv[2])))
        context_path = Path(argv[6])
        context_bytes = read_regular_nofollow(context_path)
        context = json.loads(context_bytes)
        review_bytes = read_regular_nofollow(Path(argv[7]))
        if hashlib.sha256(review_bytes).hexdigest() != argv[9]:
            raise PushError("publication review changed after validation")
        review = json.loads(review_bytes)
        plan = json.loads(read_regular_nofollow(Path(argv[8])))
        if review.get("publication_context_sha256") != hashlib.sha256(
            context_bytes
        ).hexdigest():
            raise PushError("approved review is not bound to publication context")
        if (
            runtime != context.get("runtime")
            or pre != context.get("pre_collection_state")
            or plan != context.get("artifact_plan")
        ):
            raise PushError("fixed push inputs differ from reviewed context")
        if int(argv[5]) != 0:
            raise PushError("local publication did not pass canonical validation")
        try:
            committed = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            committed = {}
        if committed.get("outcome") not in {"ready_to_push", "partial_publication"}:
            raise PushError("local publication produced no independently pushable result")
        if committed.get("publication_mode") != {
            "agents_vault": committed["agents_vault"].get("publication_mode"),
            "user_vault": committed["user_vault"].get("publication_mode"),
        }:
            raise PushError("top-level and per-Vault publication modes disagree")
        if committed.get("deferred_cleanup") != {
            "agents_vault": committed["agents_vault"].get("deferred_cleanup"),
            "user_vault": committed["user_vault"].get("deferred_cleanup"),
        }:
            raise PushError("top-level and per-Vault deferred cleanup disagree")
        agents_artifact = review["agents_vault"]["reviewed_artifacts"][0][
            "target_path"
        ]
        user_artifact = review["user_vault"]["reviewed_artifacts"][0][
            "target_path"
        ]
        if committed.get("evidence_finalization_commit") is not None:
            raise PushError("evidence must be finalized after the first fixed pushes")
        if committed.get("daily_pipeline_status") != "complete":
            raise PushError("ready publication does not mark the pipeline complete")
        actual_agents = dict(committed["agents_vault"])
        actual_user = dict(committed["user_vault"])
        if (
            actual_agents["publication_mode"] != "blocked"
            and committed.get("advisory_path") != plan["advisory_target"]
        ):
            agents_push, agents_remote, agents_error = (
                "failed", str(pre["agents_vault"]["remote_head"]),
                "reported advisory path differs from the approved plan",
            )
        else:
            agents_push, agents_remote, agents_error = validate_and_push_one(
                runtime, pre["agents_vault"], actual_agents,
                review["agents_vault"], agents_artifact, "agents",
            )
        if (
            actual_user["publication_mode"] != "blocked"
            and committed.get("summary_path") != plan["summary_target"]
        ):
            user_push, user_remote, user_error = (
                "failed", str(pre["user_vault"]["remote_head"]),
                "reported summary path differs from the approved plan",
            )
        else:
            user_push, user_remote, user_error = validate_and_push_one(
                runtime, pre["user_vault"], actual_user,
                review["user_vault"], user_artifact, "user",
            )
        success = (
            agents_error is None
            and user_error is None
            and actual_agents["publication_mode"] != "blocked"
            and actual_user["publication_mode"] != "blocked"
            and agents_push in {"complete", "not_required"}
            and user_push in {"complete", "not_required"}
            and agents_remote == actual_agents["local_head"]
            and user_remote == actual_user["local_head"]
        )
        result = {
            "outcome": "partial_publication",
            "phase": "initial_fixed_push",
            "daily_pipeline_status": committed["daily_pipeline_status"],
            "summary_path": committed["summary_path"],
            "advisory_path": committed["advisory_path"],
            "notification_result": committed["notification_result"],
            "agents_vault": final_vault(
                actual_agents, agents_push, agents_remote,
                (
                    pre["agents_vault"]
                    if agents_push in {"complete", "not_required"}
                    else None
                ),
            ),
            "user_vault": final_vault(
                actual_user, user_push, user_remote,
                (
                    pre["user_vault"]
                    if user_push in {"complete", "not_required"}
                    else None
                ),
            ),
            "publication_mode": committed["publication_mode"],
            "deferred_cleanup": committed["deferred_cleanup"],
            "evidence_finalization_commit": committed[
                "evidence_finalization_commit"
            ],
            "next_action": (
                "Finalize and review actual push evidence, then push the Agents evidence commit."
                if success
                else "; ".join(
                    value for value in (
                        f"Agents Vault: {agents_error}" if agents_error else "",
                        f"User Vault: {user_error}" if user_error else "",
                        committed.get("next_action") or "",
                    ) if value
                ) or "Repair the failed plain main push without force or history rewrite."
            ),
        }
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        TransportError,
        PushError,
    ) as exc:
        print(f"fixed push blocked:{exc}", file=sys.stderr)
        if runtime and pre:
            try:
                unknown_local = False

                def fallback_local(key: str, prefix: str) -> dict[str, object]:
                    nonlocal unknown_local
                    try:
                        return current_local(
                            str(runtime[f"{prefix}_vault_root"]), pre[key]
                        )
                    except (
                        KeyError,
                        OSError,
                        TypeError,
                        subprocess.SubprocessError,
                        TransportError,
                        PushError,
                    ):
                        unknown_local = True
                        return {
                            "commit_status": "failed",
                            "commit_hashes": [],
                            "pre_local_head": pre[key]["local_head"],
                            "local_head": None,
                            "pre_dirty_digest": pre[key]["dirty_digest"],
                            "post_dirty_digest": "0" * 64,
                            "clean": False,
                        }

                def fallback_remote(prefix: str) -> str | None:
                    try:
                        return remote_head(
                            str(runtime[f"{prefix}_vault_root"]),
                            str(runtime[f"{prefix}_remote_url"]),
                            str(runtime[f"{prefix}_git_dir"]),
                        )
                    except (
                        KeyError,
                        OSError,
                        TypeError,
                        subprocess.SubprocessError,
                        TransportError,
                        PushError,
                    ):
                        return None

                actual_agents = fallback_local("agents_vault", "agents")
                actual_user = fallback_local("user_vault", "user")
                for key, actual in (
                    ("agents_vault", actual_agents),
                    ("user_vault", actual_user),
                ):
                    actual["publication_mode"] = committed.get(key, {}).get(
                        "publication_mode", "blocked"
                    )
                    actual["deferred_cleanup"] = committed.get(key, {}).get(
                        "deferred_cleanup", []
                    )
                agents_remote = fallback_remote("agents")
                user_remote = fallback_remote("user")
                progressed = (
                    unknown_local
                    or agents_remote is None
                    or user_remote is None
                    or any(
                        actual["publication_mode"] != "blocked"
                        for actual in (actual_agents, actual_user)
                    )
                    or any(
                    actual["local_head"] != before["local_head"]
                    or actual["post_dirty_digest"] != before["dirty_digest"]
                    for actual, before in (
                        (actual_agents, pre["agents_vault"]),
                        (actual_user, pre["user_vault"]),
                    )
                    )
                )
                fallback = {
                    "outcome": (
                        "partial_publication" if progressed else "blocked"
                    ),
                    "phase": "initial_fixed_push_validation",
                    "daily_pipeline_status": committed.get(
                        "daily_pipeline_status", "blocked"
                    ),
                    "summary_path": committed.get("summary_path"),
                    "advisory_path": committed.get("advisory_path"),
                    "notification_result": committed.get("notification_result"),
                    "agents_vault": final_vault(
                        actual_agents,
                        (
                            "complete"
                            if agents_remote is not None
                            and agents_remote == actual_agents["local_head"]
                            and actual_agents["commit_hashes"]
                            else "not_required"
                            if agents_remote is not None
                            and agents_remote == actual_agents["local_head"]
                            else "failed"
                        ),
                        agents_remote,
                    ),
                    "user_vault": final_vault(
                        actual_user,
                        (
                            "complete"
                            if user_remote is not None
                            and user_remote == actual_user["local_head"]
                            and actual_user["commit_hashes"]
                            else "not_required"
                            if user_remote is not None
                            and user_remote == actual_user["local_head"]
                            else "failed"
                        ),
                        user_remote,
                    ),
                    "publication_mode": committed.get("publication_mode", {
                        "agents_vault": "blocked", "user_vault": "blocked"
                    }),
                    "deferred_cleanup": committed.get("deferred_cleanup", {
                        "agents_vault": [], "user_vault": []
                    }),
                    "evidence_finalization_commit": None,
                    "next_action": (
                        f"Repair fixed-push validation without force: {exc}"
                    ),
                }
                output_path.write_text(
                    json.dumps(fallback, ensure_ascii=False), encoding="utf-8"
                )
            except Exception as capture_exc:
                print(
                    f"could not capture fixed-push partial state:{capture_exc}",
                    file=sys.stderr,
                )
        return 75
    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if success else 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

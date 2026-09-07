#!/usr/bin/env python3
"""Install reviewed artifacts and create only the approved local commits."""

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
from typing import Callable, Optional

from atomic_file_ops import (
    AtomicTransactionError,
    allocate_private_entry_path,
    fsync_after_rename,
    link_no_replace_durable,
    mkdir_durable,
    open_absolute_directory_chain,
    publish_head_index_transaction,
    read_named_entry_contract,
    recover_head_index_transaction,
    rename_no_replace,
    retain_path_no_replace,
    verify_rename_no_replace,
)
from git_diff_digest import unified_diff_added_content
from isolated_git_transport import LOCAL_COMMAND_TIMEOUT_SECONDS, run_local_command
from trusted_gitleaks import trusted_scan_invocation


CLEAN_DIGEST = hashlib.sha256(b"").hexdigest()
SCAN_TIMEOUT_SECONDS = 120


class CommitError(RuntimeError):
    """Represent a publication mutation that must fail closed."""


VOLATILE_INDEX_FIELDS = frozenset({"index_sha256", "index_identity"})


def same_semantic_vault_state(left: object, right: object) -> bool:
    """Compare Git/Vault meaning while ignoring index stat-cache serialization."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return {
        key: value for key, value in left.items() if key not in VOLATILE_INDEX_FIELDS
    } == {
        key: value for key, value in right.items() if key not in VOLATILE_INDEX_FIELDS
    }


def same_semantic_state_pair(left: object, right: object) -> bool:
    """Require the same two Vaults and semantic state for each Vault."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    keys = {"agents_vault", "user_vault"}
    return set(left) == keys and set(right) == keys and all(
        same_semantic_vault_state(left[key], right[key]) for key in keys
    )


def validated_publisher_identity(runtime: dict[str, object]) -> tuple[str, str]:
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
        raise CommitError("publisher Git identity is invalid")
    return name, email


def clean_environment(
    publisher_identity: tuple[str, str] | None = None,
) -> dict[str, str]:
    """Remove ambient Git overrides and prohibit lazy object retrieval."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and not key.startswith("GITLEAKS_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
            "LANG": "C",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.trustctime",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "core.checkStat",
            "GIT_CONFIG_VALUE_2": "minimal",
        }
    )
    if publisher_identity is not None:
        name, email = publisher_identity
        environment.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            }
        )
    return environment


def git(
    repo: str,
    git_dir: str,
    *arguments: str,
    check: bool = True,
    index_file: str | None = None,
    input_text: str | None = None,
    publisher_identity: tuple[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run local-only Git with explicit metadata and work-tree paths."""
    environment = clean_environment(publisher_identity)
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    return run_local_command(
        [
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
            "-c",
            "commit.gpgSign=false",
            *arguments,
        ],
        cwd="/",
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
        env=environment,
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )


def git_bytes(
    repo: str,
    git_dir: str,
    *arguments: str,
    index_file: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run local-only Git while preserving byte-exact patch line boundaries."""
    environment = clean_environment()
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = index_file
    return run_local_command(
        [
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
            "-c",
            "commit.gpgSign=false",
            *arguments,
        ],
        cwd="/",
        check=True,
        capture_output=True,
        env=environment,
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )


def safe_path(value: object) -> str:
    """Require a normalized repo-relative path outside .obsidian."""
    if not isinstance(value, str):
        raise CommitError("approved path is not a string")
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or path.parts[0] == ".obsidian"
        or str(path) != value
    ):
        raise CommitError("approved path is unsafe")
    return value


def current_state(repo: str, git_dir: str, pre: dict[str, object]) -> dict[str, object]:
    """Capture local progress in the canonical commit-result shape."""
    head = git(repo, git_dir, "rev-parse", "HEAD").stdout.strip()
    if pre.get("worktree_capture_scope") == "index_only":
        # own_only commits are constructed from an isolated index and publish
        # through HEAD/index CAS. Unrelated worktree bytes are deliberately
        # outside this transaction and must not be re-walked through iCloud.
        post_dirty_digest = str(pre["dirty_digest"])
        clean = False
    else:
        status = git(
            repo, git_dir, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout
        post_dirty_digest = hashlib.sha256(status.encode("utf-8")).hexdigest()
        clean = not status
    commits = git(
        repo,
        git_dir,
        "rev-list",
        "--reverse",
        f"{pre['local_head']}..{head}",
    ).stdout.splitlines()
    return {
        "commit_status": "complete" if commits else "not_started",
        "commit_hashes": commits,
        "pre_local_head": pre["local_head"],
        "local_head": head,
        "pre_dirty_digest": pre["dirty_digest"],
        "post_dirty_digest": post_dirty_digest,
        "clean": clean,
    }


def capture_exact(capture: str, runtime_file: str, expected: dict[str, object]) -> None:
    """Require the current two-Vault state to equal the immutable pre-state."""
    completed = run_local_command(
        [capture, "--index-only", "--include-local-history", runtime_file],
        cwd="/",
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    if json.loads(completed.stdout) != expected:
        raise CommitError("Vault state changed after the approved review")


def capture_state(capture: str, runtime_file: str) -> dict[str, object]:
    """Capture the complete current state for both Vaults."""
    completed = run_local_command(
        [capture, "--index-only", "--include-local-history", runtime_file],
        cwd="/",
        check=True,
        capture_output=True,
        text=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    return json.loads(completed.stdout)


def validate_installed_scope(
    before: dict[str, object],
    current: dict[str, object],
    artifact_paths: dict[str, str],
) -> None:
    """Allow only the two installed artifacts beyond the reviewed dirty scope."""
    mutable_fields = {
        "dirty_lines",
        "dirty_paths",
        "dirty_entries",
        "dirty_metadata",
        "staged_paths",
        "index_sha256",
        "index_entries",
        "index_identity",
        "dirty_worktree_sha256",
        "dirty_digest",
        "diff_snapshot_sha256",
    }
    for key in ("agents_vault", "user_vault"):
        expected_state = before[key]
        current_state_value = current[key]
        if expected_state.get("worktree_capture_scope") == "index_only":
            if {
                field: value
                for field, value in current_state_value.items()
                if field not in VOLATILE_INDEX_FIELDS
            } != {
                field: value
                for field, value in expected_state.items()
                if field not in VOLATILE_INDEX_FIELDS
            }:
                raise CommitError(
                    "Vault control/index state changed during artifact installation"
                )
            # The exclusive installer receipt binds the new worktree file;
            # index-only capture intentionally cannot enumerate it.
            continue
        if {
            field: value
            for field, value in current_state_value.items()
            if field not in mutable_fields
        } != {
            field: value
            for field, value in expected_state.items()
            if field not in mutable_fields
        }:
            raise CommitError("Vault control state changed during artifact installation")
        artifact = artifact_paths.get(key)
        expected_paths = sorted(
            [*expected_state["dirty_paths"], *([artifact] if artifact else [])]
        )
        if current_state_value["dirty_paths"] != expected_paths:
            raise CommitError("manifest-external path changed during artifact installation")
        expected_entries = {
            entry["path"]: entry for entry in expected_state["dirty_entries"]
        }
        current_entries = {
            entry["path"]: entry for entry in current_state_value["dirty_entries"]
        }
        if any(current_entries.get(path) != entry for path, entry in expected_entries.items()):
            raise CommitError("reviewed dirty entry changed during artifact installation")
        expected_metadata = {
            entry["path"]: entry for entry in expected_state["dirty_metadata"]
        }
        current_metadata = {
            entry["path"]: entry for entry in current_state_value["dirty_metadata"]
        }
        if any(
            current_metadata.get(path) != entry
            for path, entry in expected_metadata.items()
        ):
            raise CommitError("reviewed dirty metadata changed during artifact installation")
        if (
            current_state_value["staged_paths"] != expected_state["staged_paths"]
            or current_state_value["index_entries"] != expected_state["index_entries"]
        ):
            raise CommitError("existing staged state changed during artifact installation")
        if artifact is not None:
            artifact_entry = current_entries.get(artifact)
            if artifact_entry is None or artifact_entry.get("mode") != "100644" or not artifact_entry.get(
                "git_blob_oid"
            ):
                raise CommitError("installed artifact is not the only approved regular addition")


def capture_installed_scope(
    capture: str,
    runtime_file: str,
    before: dict[str, object],
    artifact_paths: dict[str, str],
) -> dict[str, object]:
    """Recapture both complete Vaults after installation and before any commit."""
    current = capture_state(capture, runtime_file)
    validate_installed_scope(before, current, artifact_paths)
    return current


def reviewed_inputs(
    context: dict[str, object],
    runtime: object,
    pre: object,
    collection: object,
    plan: object,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    """Return only context-bound publication inputs or reject mutable substitutes."""
    bound = (
        context["runtime"],
        context["pre_collection_state"],
        context["verified_collection"],
        context["artifact_plan"],
    )
    if (runtime, pre, collection, plan) != bound:
        raise CommitError("mutable publication inputs differ from reviewed context")
    return bound


def write_bound_json(directory: Path, name: str, value: object) -> tuple[Path, bytes]:
    """Create one context-derived, no-follow control input for child helpers."""
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path = directory / name
    if path.exists():
        if stable_regular_bytes(path) != content:
            raise CommitError("existing bound publication input differs from context")
        return path, content
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o400)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CommitError("could not write bound publication input")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path, content


def require_bound_bytes(path: Path, content: bytes) -> None:
    """Reject any change to a context-derived helper input."""
    if stable_regular_bytes(path) != content:
        raise CommitError("bound publication input changed during helper execution")


def require_filter_free(repo: str, git_dir: str, paths: list[str]) -> None:
    """Reject path-specific external clean filters before staging."""
    completed = git(repo, git_dir, "check-attr", "-z", "filter", "--", *paths)
    fields = completed.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 3:
        raise CommitError("could not parse Git attributes")
    for index in range(0, len(fields), 3):
        if fields[index + 1] != "filter" or fields[index + 2] not in {
            "unspecified",
            "unset",
        }:
            raise CommitError("approved path has an active Git filter")


def scan_staged(
    gitleaks_bin: str,
    repo: str,
    git_dir: str,
    paths: list[str],
    index_file: str,
) -> None:
    """Reject secrets and newly added machine-specific home paths before commit."""
    environment = clean_environment()
    environment["GIT_INDEX_FILE"] = index_file
    try:
        with trusted_scan_invocation() as (scan_prefix, pass_fds):
            completed = run_local_command(
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
                env=environment,
                timeout=SCAN_TIMEOUT_SECONDS,
                pass_fds=pass_fds,
            )
    except subprocess.TimeoutExpired as exc:
        raise CommitError("gitleaks staged scan exceeded its deadline") from exc
    if completed.returncode != 0:
        raise CommitError("gitleaks rejected the staged publication")
    patch = git_bytes(
        repo,
        git_dir,
        "diff",
        "--cached",
        "--no-color",
        "--unified=0",
        "--",
        *paths,
        index_file=index_file,
    ).stdout
    home_bytes = os.fsencode(str(Path.home()))
    if home_bytes and home_bytes in unified_diff_added_content(patch):
        raise CommitError("staged publication adds a machine-specific home path")
    if home_bytes:
        for path in paths:
            listed = git(
                repo,
                git_dir,
                "ls-files",
                "--stage",
                "-z",
                "--",
                path,
                index_file=index_file,
            ).stdout
            if not listed:
                continue
            candidate = run_local_command(
                [
                    "git",
                    f"--git-dir={git_dir}",
                    f"--work-tree={repo}",
                    "show",
                    f":{path}",
                ],
                cwd="/",
                check=True,
                capture_output=True,
                env=environment,
                timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
            ).stdout
            numstat = git(
                repo,
                git_dir,
                "diff",
                "--cached",
                "--numstat",
                "-z",
                "--",
                path,
                index_file=index_file,
            ).stdout
            if numstat.startswith("-\t-\t") and home_bytes in candidate:
                raise CommitError("candidate blob contains a machine-specific home path")


def stable_regular_bytes(path: Path) -> bytes:
    """Read stable, no-follow regular-file bytes."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CommitError("installed artifact is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
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
            raise CommitError("installed artifact changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def installed_artifact_receipt(
    path: Path,
    expected_sha256: str,
    vault_root: Path,
    quarantine_root: Path,
) -> dict[str, object]:
    """Build a fully directory-bound receipt for focused transaction tests."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CommitError("newly installed artifact is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino)
        content_contract = (before.st_size, before.st_mode)
        if identity != (after.st_dev, after.st_ino) or content_contract != (
            after.st_size,
            after.st_mode,
        ):
            raise CommitError("newly installed artifact changed while being read")
    finally:
        os.close(descriptor)
    if hashlib.sha256(b"".join(chunks)).hexdigest() != expected_sha256:
        raise CommitError("newly installed artifact differs from verified staging bytes")
    try:
        relative = path.relative_to(vault_root)
    except ValueError as exc:
        raise CommitError("newly installed artifact escaped the Vault") from exc
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    opened: list[int] = []
    quarantine_fd = -1
    reservation_fd = -1
    reservation_name = f".vault-publisher-install-{secrets.token_hex(16)}"
    try:
        opened.append(os.open(vault_root, flags))
        parent_chain = []
        metadata = os.fstat(opened[-1])
        parent_chain.append((metadata.st_dev, metadata.st_ino))
        for component in relative.parts[:-1]:
            opened.append(os.open(component, flags, dir_fd=opened[-1]))
            metadata = os.fstat(opened[-1])
            parent_chain.append((metadata.st_dev, metadata.st_ino))
        quarantine_fd, quarantine_chain = open_absolute_directory_chain(
            quarantine_root
        )
        quarantine_metadata = os.fstat(quarantine_fd)
        mkdir_durable(reservation_name, 0o700, parent_fd=quarantine_fd)
        reservation_fd = os.open(
            reservation_name, flags, dir_fd=quarantine_fd
        )
        reservation_metadata = os.fstat(reservation_fd)
        link_no_replace_durable(
            opened[-1], relative.name, reservation_fd, "artifact"
        )
        reserved = os.stat("artifact", dir_fd=reservation_fd, follow_symlinks=False)
        if (reserved.st_dev, reserved.st_ino) != identity:
            raise CommitError("installed artifact changed before publication")
    finally:
        if reservation_fd >= 0:
            os.close(reservation_fd)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        for opened_fd in reversed(opened):
            os.close(opened_fd)
    receipt = {
        "path": str(path),
        "vault_root": str(vault_root),
        "vault_root_identity": parent_chain[0],
        "target_parent_chain": tuple(parent_chain),
        "quarantine_root": str(quarantine_root),
        "quarantine_root_identity": (
            quarantine_metadata.st_dev,
            quarantine_metadata.st_ino,
        ),
        "quarantine_root_chain": tuple(quarantine_chain),
        "reservation_name": reservation_name,
        "reservation_identity": (
            reservation_metadata.st_dev,
            reservation_metadata.st_ino,
        ),
        "sha256": expected_sha256,
        "identity": identity,
        "size": content_contract[0],
        "mode": content_contract[1],
    }
    require_owned_artifact(receipt)
    return receipt


def validated_installer_receipt(
    value: object,
    expected_path: Path,
    expected_sha256: str,
    expected_vault_root: str,
    expected_quarantine_root: str,
) -> dict[str, object]:
    """Accept only the identity sealed by the installer's O_EXCL descriptor."""
    if not isinstance(value, dict) or set(value) != {
        "path",
        "vault_root",
        "vault_root_identity",
        "target_parent_chain",
        "quarantine_root",
        "quarantine_root_identity",
        "quarantine_root_chain",
        "reservation_name",
        "reservation_identity",
        "sha256",
        "identity",
        "size",
        "mode",
    }:
        raise CommitError("installer artifact receipt is malformed")
    identity = value.get("identity")
    vault_identity = value.get("vault_root_identity")
    parent_chain = value.get("target_parent_chain")
    quarantine_identity = value.get("quarantine_root_identity")
    quarantine_chain = value.get("quarantine_root_chain")
    reservation_identity = value.get("reservation_identity")
    reservation_name = value.get("reservation_name")
    valid_pair = lambda pair: (
        isinstance(pair, list)
        and len(pair) == 2
        and all(
            isinstance(field, int) and not isinstance(field, bool) and field >= 0
            for field in pair
        )
    )
    if (
        value.get("path") != str(expected_path)
        or value.get("vault_root") != expected_vault_root
        or value.get("quarantine_root") != expected_quarantine_root
        or value.get("sha256") != expected_sha256
        or not valid_pair(identity)
        or not valid_pair(vault_identity)
        or not valid_pair(quarantine_identity)
        or not isinstance(quarantine_chain, list)
        or not quarantine_chain
        or not all(valid_pair(pair) for pair in quarantine_chain)
        or quarantine_chain[-1] != quarantine_identity
        or not valid_pair(reservation_identity)
        or not isinstance(reservation_name, str)
        or not reservation_name.startswith(".vault-publisher-install-")
        or "/" in reservation_name
        or not isinstance(parent_chain, list)
        or not parent_chain
        or not all(valid_pair(pair) for pair in parent_chain)
        or parent_chain[0] != vault_identity
        or not isinstance(value.get("size"), int)
        or isinstance(value.get("size"), bool)
        or int(value["size"]) < 0
        or not isinstance(value.get("mode"), int)
        or isinstance(value.get("mode"), bool)
    ):
        raise CommitError("installer artifact receipt differs from the approved artifact")
    receipt = {
        "path": str(expected_path),
        "vault_root": expected_vault_root,
        "vault_root_identity": tuple(vault_identity),
        "target_parent_chain": tuple(tuple(pair) for pair in parent_chain),
        "quarantine_root": expected_quarantine_root,
        "quarantine_root_identity": tuple(quarantine_identity),
        "quarantine_root_chain": tuple(tuple(pair) for pair in quarantine_chain),
        "reservation_name": reservation_name,
        "reservation_identity": tuple(reservation_identity),
        "sha256": expected_sha256,
        "identity": tuple(identity),
        "size": int(value["size"]),
        "mode": int(value["mode"]),
    }
    require_owned_artifact(receipt)
    return receipt


def open_receipt_directories(
    receipt: dict[str, object],
) -> tuple[int, str, int]:
    """Reopen and bind the Vault root, every target parent, and Git-private root."""
    path = Path(str(receipt["path"]))
    vault_root = Path(str(receipt["vault_root"]))
    try:
        relative = path.relative_to(vault_root)
    except ValueError as exc:
        raise CommitError("installer receipt target escaped the Vault") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise CommitError("installer receipt target is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    opened: list[int] = []
    quarantine_fd = -1
    reservation_fd = -1
    try:
        opened.append(os.open(vault_root, flags))
        identities = []
        metadata = os.fstat(opened[-1])
        identities.append((metadata.st_dev, metadata.st_ino))
        for component in relative.parts[:-1]:
            opened.append(os.open(component, flags, dir_fd=opened[-1]))
            metadata = os.fstat(opened[-1])
            identities.append((metadata.st_dev, metadata.st_ino))
        if tuple(identities) != tuple(receipt["target_parent_chain"]):
            raise CommitError("installer receipt parent chain changed")
        quarantine_fd, quarantine_chain = open_absolute_directory_chain(
            str(receipt["quarantine_root"])
        )
        quarantine_metadata = os.fstat(quarantine_fd)
        if (
            (quarantine_metadata.st_dev, quarantine_metadata.st_ino)
            != tuple(receipt["quarantine_root_identity"])
            or quarantine_metadata.st_dev != os.fstat(opened[-1]).st_dev
            or quarantine_chain != tuple(receipt["quarantine_root_chain"])
        ):
            raise CommitError("installer receipt quarantine root changed")
        reservation_fd = os.open(
            str(receipt["reservation_name"]),
            flags,
            dir_fd=quarantine_fd,
        )
        reservation_metadata = os.fstat(reservation_fd)
        named_reservation = os.stat(
            str(receipt["reservation_name"]),
            dir_fd=quarantine_fd,
            follow_symlinks=False,
        )
        if (
            (reservation_metadata.st_dev, reservation_metadata.st_ino)
            != tuple(receipt["reservation_identity"])
            or (named_reservation.st_dev, named_reservation.st_ino)
            != tuple(receipt["reservation_identity"])
        ):
            raise CommitError("installer receipt reservation changed")
        parent_fd = opened.pop()
        for descriptor in reversed(opened):
            os.close(descriptor)
        os.close(quarantine_fd)
        quarantine_fd = -1
        return parent_fd, relative.name, reservation_fd
    except Exception:
        for descriptor in reversed(opened):
            os.close(descriptor)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if reservation_fd >= 0:
            os.close(reservation_fd)
        raise


def descriptor_matches_owned_artifact(
    descriptor: int, receipt: dict[str, object]
) -> bool:
    """Check stable inode/content fields without treating timestamps as identity."""
    before = os.fstat(descriptor)
    identity = (before.st_dev, before.st_ino)
    content_contract = (before.st_size, before.st_mode)
    if (
        not stat.S_ISREG(before.st_mode)
        or identity != tuple(receipt["identity"])
        or content_contract != (receipt["size"], receipt["mode"])
    ):
        return False
    os.lseek(descriptor, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while chunk := os.read(descriptor, 1024 * 1024):
        hasher.update(chunk)
    after = os.fstat(descriptor)
    return (
        identity == (after.st_dev, after.st_ino)
        and content_contract == (after.st_size, after.st_mode)
        and hasher.hexdigest() == receipt["sha256"]
    )


def require_owned_artifact(receipt: dict[str, object]) -> None:
    """Verify target bytes and all directory identities from the installer."""
    parent_fd, target_name, reservation_fd = open_receipt_directories(receipt)
    descriptor = os.open(
        target_name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        named = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        reserved = os.stat("artifact", dir_fd=reservation_fd, follow_symlinks=False)
        if (
            (named.st_dev, named.st_ino) != tuple(receipt["identity"])
            or (reserved.st_dev, reserved.st_ino) != tuple(receipt["identity"])
            or not descriptor_matches_owned_artifact(descriptor, receipt)
        ):
            raise CommitError("installer-owned artifact changed before publication")
    finally:
        os.close(descriptor)
        os.close(parent_fd)
        os.close(reservation_fd)


def owned_artifact_bytes(receipt: dict[str, object]) -> bytes:
    """Read only the installer-reserved inode while binding its worktree name."""
    parent_fd, target_name, reservation_fd = open_receipt_directories(receipt)
    target_fd = -1
    reserved_fd = -1
    try:
        target_fd = os.open(
            target_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        reserved_fd = os.open(
            "artifact",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=reservation_fd,
        )
        named = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        reserved_named = os.stat(
            "artifact", dir_fd=reservation_fd, follow_symlinks=False
        )
        if (
            (named.st_dev, named.st_ino) != tuple(receipt["identity"])
            or (reserved_named.st_dev, reserved_named.st_ino)
            != tuple(receipt["identity"])
            or not descriptor_matches_owned_artifact(target_fd, receipt)
            or not descriptor_matches_owned_artifact(reserved_fd, receipt)
        ):
            raise CommitError("installer-owned artifact changed before publication")
        os.lseek(reserved_fd, 0, os.SEEK_SET)
        content = b""
        while chunk := os.read(reserved_fd, 1024 * 1024):
            content += chunk
        if not descriptor_matches_owned_artifact(reserved_fd, receipt):
            raise CommitError("installer-owned artifact changed while being read")
        return content
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        if reserved_fd >= 0:
            os.close(reserved_fd)
        os.close(parent_fd)
        os.close(reservation_fd)


def rollback_owned_artifact(receipt: dict[str, object]) -> None:
    """Move the exact own artifact to a retained Git-private tombstone."""
    parent_descriptor, target_name, reservation_descriptor = (
        open_receipt_directories(receipt)
    )
    descriptor = -1
    quarantine_name = "rollback-worktree"
    quarantined = False
    try:
        descriptor = os.open(
            target_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if not descriptor_matches_owned_artifact(descriptor, receipt):
            raise CommitError("newly installed artifact changed; rollback refused")
        verify_rename_no_replace(reservation_descriptor)
        rename_no_replace(
            parent_descriptor,
            target_name,
            reservation_descriptor,
            quarantine_name,
        )
        quarantined = True
        fsync_after_rename(parent_descriptor, reservation_descriptor)
        quarantined_identity = os.stat(
            quarantine_name, dir_fd=reservation_descriptor, follow_symlinks=False
        )
        if (quarantined_identity.st_dev, quarantined_identity.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            try:
                rename_no_replace(
                    reservation_descriptor,
                    quarantine_name,
                    parent_descriptor,
                    target_name,
                )
                quarantined = False
                fsync_after_rename(reservation_descriptor, parent_descriptor)
            except FileExistsError:
                pass
            if not quarantined:
                raise CommitError(
                    "newly installed artifact was replaced; "
                    "the replacement was restored and rollback was refused"
                )
            raise CommitError(
                "newly installed artifact was replaced; rollback quarantined it"
            )
        try:
            retained_content, retained_identity = read_named_entry_contract(
                reservation_descriptor,
                quarantine_name,
                max_bytes=int(receipt["size"]),
            )
            retained_matches = (
                retained_identity[:4]
                == [
                    int(receipt["identity"][0]),
                    int(receipt["identity"][1]),
                    int(receipt["size"]),
                    int(receipt["mode"]),
                ]
                and hashlib.sha256(retained_content).hexdigest() == receipt["sha256"]
            )
        except (AtomicTransactionError, KeyError, TypeError, ValueError):
            retained_matches = False
        if not retained_matches:
            try:
                rename_no_replace(
                    reservation_descriptor,
                    quarantine_name,
                    parent_descriptor,
                    target_name,
                )
                quarantined = False
                fsync_after_rename(reservation_descriptor, parent_descriptor)
                raise CommitError(
                    "newly installed artifact changed during rollback; rollback refused"
                )
            except FileExistsError as exc:
                raise CommitError(
                    "newly installed artifact changed during rollback; "
                    "rollback quarantined it"
                ) from exc
        # Reopen the full chain after the move. The exact failed artifact is
        # intentionally retained below the Git-private quarantine root.
        rebound_parent, rebound_name, rebound_reservation = open_receipt_directories(
            receipt
        )
        try:
            if rebound_name != target_name:
                raise CommitError("rollback target binding changed")
        finally:
            os.close(rebound_parent)
            os.close(rebound_reservation)
        receipt["rollback_quarantine_name"] = quarantine_name
        receipt["rollback_quarantine_identity"] = (
            quarantined_identity.st_dev,
            quarantined_identity.st_ino,
        )
        os.fsync(reservation_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
        os.close(reservation_descriptor)


def write_blob(
    repo: str,
    git_dir: str,
    content: bytes,
    expected_oid: str | None = None,
) -> str:
    """Write one already-verified blob without filters or a shell."""
    completed = run_local_command(
        [
            "git",
            f"--git-dir={git_dir}",
            f"--work-tree={repo}",
            "hash-object",
            "-w",
            "--stdin",
        ],
        cwd="/",
        check=True,
        input=content,
        capture_output=True,
        env=clean_environment(),
        timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
    )
    oid = completed.stdout.decode("ascii").strip()
    if not oid or (expected_oid is not None and oid != expected_oid):
        raise CommitError("reviewed bytes do not match the approved Git blob")
    return oid


def ensure_reviewed_blob(
    repo: str, git_dir: str, path: str, expected_oid: str
) -> None:
    """Make an approved dirty blob available without accepting other bytes."""
    available = git(repo, git_dir, "cat-file", "-e", f"{expected_oid}^{{blob}}", check=False)
    if available.returncode == 0:
        return
    write_blob(
        repo,
        git_dir,
        stable_regular_bytes(Path(repo) / path),
        expected_oid,
    )


def update_index_entry(
    repo: str,
    git_dir: str,
    path: str,
    entry: tuple[str, str] | None,
    *,
    index_file: str | None = None,
) -> None:
    """Set or remove one exact index entry without reading worktree bytes."""
    if entry is None:
        git(
            repo,
            git_dir,
            "update-index",
            "--force-remove",
            "--",
            path,
            index_file=index_file,
        )
        return
    mode, oid = entry
    if git(repo, git_dir, "cat-file", "-t", oid).stdout.strip() != "blob":
        raise CommitError("approved Git object is not an available blob")
    git(
        repo,
        git_dir,
        "update-index",
        "--add",
        "--cacheinfo",
        f"{mode},{oid},{path}",
        index_file=index_file,
    )


def index_file_contract(path: Path) -> tuple[bytes, list[int]]:
    """Read one stable no-follow Git index and return its exact inode contract."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CommitError("Git index is not a regular file")
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
            raise CommitError("Git index changed while it was read")
        return bytes(content), contract
    finally:
        os.close(descriptor)


def prepare_shared_index_candidate(
    repo: str,
    git_dir: str,
    entries: dict[str, tuple[str, str] | None],
    pre: dict[str, object],
    temporary_directory: Path,
) -> tuple[str, dict[str, object]]:
    """Build the final shared index from the exact reviewed index bytes."""
    index_path = Path(git_dir) / "index"
    content, identity = index_file_contract(index_path)
    if (
        hashlib.sha256(content).hexdigest() != pre.get("index_sha256")
        or identity != pre.get("index_identity")
    ):
        raise CommitError("shared Git index differs from review")
    candidate_path = allocate_private_entry_path(
        temporary_directory,
        prefix=".publication-shared-index-work-",
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
                raise CommitError("could not prepare shared Git index")
            view = view[written:]
        os.fchmod(descriptor, stat.S_IMODE(identity[3]))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        for path, entry in entries.items():
            update_index_entry(
                repo,
                git_dir,
                path,
                entry,
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
                label="failed publication shared-index candidate",
                prefix=".publication-shared-index-retained-",
                allow_missing=True,
            )
        except AtomicTransactionError as cleanup_error:
            raise CommitError(
                f"publication shared-index cleanup failed closed: {cleanup_error}"
            ) from original
        raise


def publish_head_and_shared_index(
    repo: str,
    git_dir: str,
    pre: dict[str, object],
    candidate_head: str,
    candidate_index: str,
    candidate_index_contract: dict[str, object],
    mutation_tracker: dict[str, object] | None,
) -> None:
    """Publish the reviewed HEAD/index pair through the shared durable helper."""
    expected_digest = pre.get("index_sha256")
    expected_identity = pre.get("index_identity")
    if not isinstance(expected_digest, str) or not isinstance(expected_identity, list):
        raise CommitError("reviewed Git index contract is malformed")
    try:
        publish_head_index_transaction(
            git_dir,
            base_head=str(pre["local_head"]),
            candidate_head=candidate_head,
            expected_index_sha256=expected_digest,
            expected_index_identity=expected_identity,
            candidate_index_path=candidate_index,
            candidate_index_sha256=str(candidate_index_contract.get("sha256")),
            candidate_index_identity=candidate_index_contract.get("identity"),
            read_head=lambda: git(repo, git_dir, "rev-parse", "HEAD").stdout.strip(),
            update_head=lambda new, old: git(
                repo, git_dir, "update-ref", "HEAD", new, old
            ),
            mutation_tracker=mutation_tracker,
        )
    except AtomicTransactionError as exc:
        raise CommitError(str(exc)) from exc


def validate_final_worktree(
    repo: str,
    git_dir: str,
    manifest: dict[str, object],
    artifact_path: str,
    artifact_source_sha256: str,
    artifact_receipt: dict[str, object] | None = None,
) -> None:
    """Prove every final approved entry matches the worktree before any commit."""
    entries = {
        safe_path(entry["path"]): (
            None
            if entry["git_blob_oid"] is None
            else (str(entry["mode"]), str(entry["git_blob_oid"]))
        )
        for entry in manifest.get("approved_dirty_entries", [])
    }
    for path, entry in entries.items():
        if entry is not None:
            ensure_reviewed_blob(repo, git_dir, path, entry[1])
    artifact_path = safe_path(artifact_path)
    artifact_content = (
        owned_artifact_bytes(artifact_receipt)
        if artifact_receipt is not None
        else stable_regular_bytes(Path(repo) / artifact_path)
    )
    if hashlib.sha256(artifact_content).hexdigest() != artifact_source_sha256:
        raise CommitError("installed artifact digest differs from review")
    entries[artifact_path] = ("100644", write_blob(repo, git_dir, artifact_content))
    grouped_paths = [
        safe_path(path)
        for group in manifest.get("commit_groups", [])
        for path in group.get("paths", [])
    ]
    if sorted(grouped_paths) != sorted(entries):
        raise CommitError("approved groups do not cover the exact final worktree")
    for path, entry in entries.items():
        worktree_path = Path(repo) / path
        if entry is None:
            try:
                os.lstat(worktree_path)
            except FileNotFoundError:
                continue
            raise CommitError("approved deletion remains in the worktree")
        metadata = os.lstat(worktree_path)
        if not stat.S_ISREG(metadata.st_mode):
            raise CommitError("approved final path is not a regular file")
        actual_mode = "100755" if metadata.st_mode & 0o111 else "100644"
        if actual_mode != entry[0]:
            raise CommitError("worktree mode differs from the approved final mode")
        content = (
            owned_artifact_bytes(artifact_receipt)
            if path == artifact_path and artifact_receipt is not None
            else stable_regular_bytes(worktree_path)
        )
        write_blob(
            repo,
            git_dir,
            content,
            entry[1],
        )


def commit_groups(
    repo: str,
    git_dir: str,
    gitleaks_bin: str,
    pre: dict[str, object],
    manifest: dict[str, object],
    artifact_path: str,
    artifact_source_sha256: str,
    temporary_directory: Path,
    publisher_identity: tuple[str, str] | None = None,
    before_update: Callable[[], None] | None = None,
    publication_mode: str = "sweep",
    mutation_tracker: dict[str, object] | None = None,
    artifact_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create the ordered, minimal commits declared by the approved manifest."""
    if publication_mode not in {"sweep", "own_only"}:
        raise CommitError("blocked Vault cannot create publication commits")
    if git(repo, git_dir, "branch", "--show-current").stdout.strip() != "main":
        raise CommitError("Vault is not on main")
    if git(repo, git_dir, "rev-parse", "HEAD").stdout.strip() != pre["local_head"]:
        raise CommitError("Vault HEAD changed after review")
    groups = manifest.get("commit_groups")
    if not isinstance(groups, list) or not groups:
        raise CommitError("approved commit groups are missing")
    entries = {
        safe_path(entry["path"]): (
            None
            if entry["git_blob_oid"] is None
            else (str(entry["mode"]), str(entry["git_blob_oid"]))
        )
        for entry in manifest.get("approved_dirty_entries", [])
    }
    for path, entry in entries.items():
        if entry is not None:
            ensure_reviewed_blob(repo, git_dir, path, entry[1])
    artifact_path = safe_path(artifact_path)
    artifact_content = (
        owned_artifact_bytes(artifact_receipt)
        if artifact_receipt is not None
        else stable_regular_bytes(Path(repo) / artifact_path)
    )
    if hashlib.sha256(artifact_content).hexdigest() != artifact_source_sha256:
        raise CommitError("installed artifact digest differs from review")
    artifact_oid = write_blob(
        repo,
        git_dir,
        artifact_content,
    )
    entries[artifact_path] = ("100644", artifact_oid)
    current_head = str(pre["local_head"])
    for group in groups:
        message = group.get("message")
        paths = [safe_path(value) for value in group.get("paths", [])]
        if (
            not isinstance(message, str)
            or not message.strip()
            or "\n" in message
            or not paths
            or any(path not in entries for path in paths)
        ):
            raise CommitError("approved commit group is invalid")
        require_filter_free(repo, git_dir, paths)
        temporary_index = allocate_private_entry_path(
            temporary_directory,
            prefix=".publication-index-work-",
            entry_name="index",
        )
        temporary_index_contract = None
        try:
            git(
                repo,
                git_dir,
                "read-tree",
                current_head,
                index_file=temporary_index,
            )
            for path in paths:
                update_index_entry(
                    repo,
                    git_dir,
                    path,
                    entries[path],
                    index_file=temporary_index,
                )
            git(
                repo,
                git_dir,
                "-c",
                # Reviewed Markdown bytes are immutable and may intentionally end
                # with approved blank lines as well as Markdown hard breaks.
                "core.whitespace=-blank-at-eol,-blank-at-eof",
                "diff",
                "--cached",
                "--check",
                index_file=temporary_index,
            )
            scan_staged(gitleaks_bin, repo, git_dir, paths, temporary_index)
            tree = git(
                repo, git_dir, "write-tree", index_file=temporary_index
            ).stdout.strip()
            commit = git(
                repo,
                git_dir,
                "commit-tree",
                tree,
                "-p",
                current_head,
                input_text=message + "\n",
                publisher_identity=publisher_identity,
            ).stdout.strip()
            temporary_content, temporary_identity = index_file_contract(
                Path(temporary_index)
            )
            temporary_index_contract = {
                "sha256": hashlib.sha256(temporary_content).hexdigest(),
                "identity": temporary_identity,
            }
        finally:
            try:
                retain_path_no_replace(
                    temporary_index,
                    expected=temporary_index_contract,
                    label="publication commit temporary index",
                    prefix=".publication-index-retained-",
                    allow_missing=temporary_index_contract is None,
                )
            except AtomicTransactionError as cleanup_error:
                raise CommitError(
                    f"publication temporary-index cleanup failed closed: {cleanup_error}"
                )
        actual = sorted(
            value
            for value in git(
                repo,
                git_dir,
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "--no-renames",
                "-r",
                "-z",
                commit,
            ).stdout.split("\0")
            if value
        )
        if actual != sorted(paths):
            raise CommitError("created commit differs from its approved path group")
        if git(repo, git_dir, "show", "-s", "--format=%s", commit).stdout.strip() != message:
            raise CommitError("created commit message differs from approval")
        current_head = commit
    if mutation_tracker is not None:
        mutation_tracker["candidate_head"] = current_head
        mutation_tracker["candidate_commits"] = [
            value
            for value in git(
                repo,
                git_dir,
                "rev-list",
                "--reverse",
                f"{pre['local_head']}..{current_head}",
            ).stdout.splitlines()
            if value
        ]
    shared_index_candidate = prepare_shared_index_candidate(
        repo,
        git_dir,
        entries,
        pre,
        temporary_directory,
    )
    try:
        if before_update is not None:
            before_update()
        if artifact_receipt is not None:
            require_owned_artifact(artifact_receipt)
        publish_head_and_shared_index(
            repo,
            git_dir,
            pre,
            current_head,
            shared_index_candidate[0],
            shared_index_candidate[1],
            mutation_tracker,
        )
        if artifact_receipt is not None:
            require_owned_artifact(artifact_receipt)
    finally:
        try:
            retain_path_no_replace(
                shared_index_candidate[0],
                expected=shared_index_candidate[1],
                label="publication shared-index candidate",
                prefix=".publication-shared-index-retained-",
            )
        except AtomicTransactionError as cleanup_error:
            raise CommitError(
                f"publication shared-index cleanup failed closed: {cleanup_error}"
            )
    result = current_state(repo, git_dir, pre)
    if len(result["commit_hashes"]) != len(groups):
        raise CommitError("approved local commit count differs from review")
    if publication_mode == "sweep" and not result["clean"]:
        raise CommitError("sweep commits did not leave a clean Vault")
    if publication_mode == "own_only" and result["post_dirty_digest"] != pre[
        "dirty_digest"
    ]:
        raise CommitError("own_only changed the residual porcelain state")
    result["commit_status"] = "complete"
    result["publication_mode"] = publication_mode
    result["deferred_cleanup"] = list(manifest.get("deferred_cleanup", []))
    return result


def validate_post_commit_state(
    before: dict[str, object],
    after: dict[str, object],
    artifact_path: str,
    mode: str,
) -> None:
    """Prove sweep cleanliness or exact preservation of every residual path."""
    if mode == "sweep":
        if after["dirty_paths"] or after["staged_paths"]:
            raise CommitError("sweep publication left residual changes")
        return
    if mode != "own_only":
        raise CommitError("invalid post-commit publication mode")
    for field in (
        "dirty_lines",
        "dirty_paths",
        "dirty_entries",
        "dirty_metadata",
        "staged_paths",
        "dirty_worktree_sha256",
        "dirty_digest",
        "diff_snapshot_sha256",
        "dirty_materialization",
        "git_control_sha256",
        "worktree_capture_scope",
        "branch",
        "upstream",
        "operation_in_progress",
        "remote_head",
    ):
        if after.get(field) != before.get(field):
            raise CommitError(f"own_only changed residual state field: {field}")
    before_index = [
        entry for entry in before["index_entries"] if entry["path"] != artifact_path
    ]
    after_index = [
        entry for entry in after["index_entries"] if entry["path"] != artifact_path
    ]
    if after_index != before_index:
        raise CommitError("own_only changed a non-owned index entry")


def unchanged_vault_result(
    repo: str,
    git_dir: str,
    pre: dict[str, object],
    manifest: dict[str, object],
) -> dict[str, object]:
    """Return an explicit blocked result without mutating the Vault."""
    result = current_state(repo, git_dir, pre)
    if (
        result["local_head"] != pre["local_head"]
        or result["post_dirty_digest"] != pre["dirty_digest"]
    ):
        raise CommitError("blocked Vault changed during publication")
    result["commit_status"] = "not_started"
    result["publication_mode"] = "blocked"
    result["deferred_cleanup"] = list(manifest.get("deferred_cleanup", []))
    return result


def result_after_failure(
    runtime: dict[str, object],
    pre: dict[str, object],
    collection: dict[str, object],
    plan: dict[str, object],
    reason: str,
    publication_context_sha256: Optional[str] = None,
    capture: Optional[str] = None,
    runtime_file: Optional[str] = None,
    publication_modes: Optional[dict[str, str]] = None,
    deferred_cleanup: Optional[dict[str, list[dict[str, str]]]] = None,
) -> dict[str, object]:
    """Report actual local progress without hiding partially applied mutation."""
    results: dict[str, dict[str, object]] = {}
    changed = False
    zero_head = "0" * 40
    zero_digest = "0" * 64
    modes = publication_modes or {
        "agents_vault": "blocked",
        "user_vault": "blocked",
    }
    deferred = deferred_cleanup or {
        "agents_vault": [],
        "user_vault": [],
    }
    for key, prefix in (("agents_vault", "agents"), ("user_vault", "user")):
        try:
            state = current_state(
                str(runtime[f"{prefix}_vault_root"]),
                str(runtime[f"{prefix}_git_dir"]),
                pre[key],
            )
        except (KeyError, TypeError, OSError, subprocess.SubprocessError):
            before = pre.get(key, {}) if isinstance(pre, dict) else {}
            state = {
                "commit_status": "failed",
                "commit_hashes": [],
                "pre_local_head": before.get("local_head", zero_head),
                "local_head": zero_head,
                "pre_dirty_digest": before.get("dirty_digest", zero_digest),
                "post_dirty_digest": zero_digest,
                "clean": False,
            }
        if state["commit_hashes"]:
            state["commit_status"] = "failed"
        state["publication_mode"] = modes.get(key, "blocked")
        state["deferred_cleanup"] = list(deferred.get(key, []))
        changed = changed or state["local_head"] != state["pre_local_head"] or (
            state["post_dirty_digest"] != state["pre_dirty_digest"]
        )
        results[key] = state
    resumable_state = None
    if capture and runtime_file:
        try:
            resumable_state = capture_state(capture, runtime_file)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
            resumable_state = None
    result = {
        "outcome": "partial_publication" if changed else "blocked",
        "phase": "local_commit",
        "daily_pipeline_status": collection.get("daily_pipeline_status", "blocked"),
        "summary_path": plan.get("summary_target"),
        "advisory_path": plan.get("advisory_target"),
        "notification_result": collection.get("notification_result"),
        "agents_vault": results["agents_vault"],
        "user_vault": results["user_vault"],
        "publication_mode": modes,
        "deferred_cleanup": deferred,
        "evidence_finalization_commit": None,
        "next_action": reason,
    }
    if changed and publication_context_sha256 is not None:
        result["publication_context_sha256"] = publication_context_sha256
        result["resumable_state"] = resumable_state
    return result


def capture_one(capture: str, runtime_file: str, key: str) -> dict[str, object]:
    """Capture only the requested Vault contract from the canonical pair."""
    return capture_state(capture, runtime_file)[key]


def capture_one_exact(
    capture: str,
    runtime_file: str,
    key: str,
    expected: dict[str, object],
) -> None:
    """Bind one normal publication Vault to the complete reviewed snapshot."""
    if capture_one(capture, runtime_file, key) != expected:
        raise CommitError(f"{key} state changed after approved review")


def capture_one_semantic(
    capture: str,
    runtime_file: str,
    key: str,
    expected: dict[str, object],
) -> None:
    """Ignore raw stat-cache serialization only while resuming owned progress."""
    if not same_semantic_vault_state(
        capture_one(capture, runtime_file, key), expected
    ):
        raise CommitError(f"{key} semantic state changed after resumable progress")


def validate_installed_vault(
    before: dict[str, object],
    current: dict[str, object],
    artifact: str,
    *,
    allow_volatile_index: bool = False,
) -> None:
    """Allow one newly installed artifact while preserving one Vault residual."""
    if before.get("worktree_capture_scope") == "index_only":
        if {
            field: value
            for field, value in current.items()
            if field not in VOLATILE_INDEX_FIELDS
        } != {
            field: value
            for field, value in before.items()
            if field not in VOLATILE_INDEX_FIELDS
        }:
            raise CommitError(
                "Vault control/index state changed during artifact installation"
            )
        # validated_installer_receipt already binds exact target identity,
        # bytes, mode, parent directory, and absence from HEAD/shared index.
        return
    mutable_fields = {
        "dirty_lines", "dirty_paths", "dirty_entries", "dirty_metadata",
        "staged_paths", "index_entries",
        "dirty_worktree_sha256", "dirty_digest", "diff_snapshot_sha256",
    }
    if allow_volatile_index:
        mutable_fields.update(VOLATILE_INDEX_FIELDS)
    if {
        field: value for field, value in current.items() if field not in mutable_fields
    } != {
        field: value for field, value in before.items() if field not in mutable_fields
    }:
        raise CommitError("Vault control state changed during artifact installation")
    if current["dirty_paths"] != sorted([*before["dirty_paths"], artifact]):
        raise CommitError("manifest-external path changed during artifact installation")
    before_entries = {entry["path"]: entry for entry in before["dirty_entries"]}
    current_entries = {entry["path"]: entry for entry in current["dirty_entries"]}
    if any(current_entries.get(path) != entry for path, entry in before_entries.items()):
        raise CommitError("reviewed dirty entry changed during artifact installation")
    before_metadata = {entry["path"]: entry for entry in before["dirty_metadata"]}
    current_metadata = {entry["path"]: entry for entry in current["dirty_metadata"]}
    if any(current_metadata.get(path) != entry for path, entry in before_metadata.items()):
        raise CommitError("reviewed dirty metadata changed during artifact installation")
    if (
        current["staged_paths"] != before["staged_paths"]
        or current["index_entries"] != before["index_entries"]
    ):
        raise CommitError("existing staged state changed during artifact installation")
    artifact_entry = current_entries.get(artifact)
    if (
        artifact_entry is None
        or artifact_entry.get("mode") != "100644"
        or not artifact_entry.get("git_blob_oid")
    ):
        raise CommitError("installed artifact is not the only approved regular addition")


def vault_result_from_snapshot(
    snapshot: dict[str, object],
    pre: dict[str, object],
    *,
    commit_status: str,
    commit_hashes: list[str],
    publication_mode: str,
    deferred_cleanup: list[dict[str, str]],
) -> dict[str, object]:
    """Build a result from actual state without attributing third-party commits."""
    return {
        "commit_status": commit_status,
        "commit_hashes": commit_hashes,
        "pre_local_head": pre["local_head"],
        "local_head": snapshot["local_head"],
        "pre_dirty_digest": pre["dirty_digest"],
        "post_dirty_digest": snapshot["dirty_digest"],
        "clean": (
            snapshot.get("worktree_capture_scope") == "full"
            and not snapshot["dirty_lines"]
        ),
        "publication_mode": publication_mode,
        "deferred_cleanup": deferred_cleanup,
    }


def rollback_uncommitted_artifact(
    repo: str,
    git_dir: str,
    artifact: str,
    receipt: dict[str, object],
) -> None:
    """Remove an exact own artifact only while HEAD/index still omit its path."""
    if git(repo, git_dir, "ls-tree", "-z", "HEAD", "--", artifact).stdout:
        raise CommitError("artifact entered HEAD; rollback refused")
    if git(repo, git_dir, "ls-files", "--stage", "-z", "--", artifact).stdout:
        raise CommitError("artifact entered the shared index; rollback refused")
    rollback_owned_artifact(receipt)


def publish_one_vault(
    *,
    key: str,
    prefix: str,
    role: str,
    artifact_plan_key: str,
    collection_sha_key: str,
    runtime: dict[str, object],
    pre: dict[str, object],
    collection: dict[str, object],
    plan: dict[str, object],
    manifest: dict[str, object],
    installer: str,
    capture: str,
    runtime_file: str,
    collection_file: str,
    plan_file: str,
    output_directory: Path,
    publisher_identity: tuple[str, str],
    resume_state: dict[str, object] | None = None,
) -> tuple[
    dict[str, object], bool, bool, str | None, dict[str, object] | None
]:
    """Install/commit one Vault independently and report owned progress."""
    repo = str(runtime[f"{prefix}_vault_root"])
    git_dir = str(runtime[f"{prefix}_git_dir"])
    before = pre[key]
    mode = str(manifest["publication_mode"])
    deferred = list(manifest.get("deferred_cleanup", []))
    artifact_absolute = str(plan[artifact_plan_key])
    artifact = str(Path(artifact_absolute).relative_to(repo))
    if mode == "blocked":
        current = capture_one(capture, runtime_file, key)
        return (
            vault_result_from_snapshot(
                current,
                before,
                commit_status="not_started",
                commit_hashes=[],
                publication_mode="blocked",
                deferred_cleanup=deferred,
            ),
            False,
            False,
            "review blocked this Vault",
            None,
        )
    receipt: dict[str, object] | None = None
    tracker: dict[str, object] = {"head_updated": False}
    try:
        if resume_state is not None:
            capture_one_semantic(capture, runtime_file, key, resume_state)
            installed_state = resume_state
            validate_installed_vault(
                before,
                installed_state,
                artifact,
                allow_volatile_index=True,
            )
            # A prior process's installer receipt is not persisted.  Exact bytes
            # may be committed after the reviewed resume-state check, but path
            # metadata must never be rebound into rollback ownership here.
            receipt = None
        else:
            capture_one_exact(capture, runtime_file, key, before)
            installed = json.loads(
                run_local_command(
                    [installer, runtime_file, collection_file, plan_file, role],
                    cwd="/",
                    check=True,
                    capture_output=True,
                    text=True,
                    env=clean_environment(),
                    timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
                ).stdout
            )
            if (
                not isinstance(installed, dict)
                or installed.get("summary_target") != plan["summary_target"]
                or installed.get("advisory_target") != plan["advisory_target"]
                or set(installed)
                != {"summary_target", "advisory_target", "installed_receipt"}
            ):
                raise CommitError("installed artifact differs from the approved plan")
            receipt = validated_installer_receipt(
                installed["installed_receipt"],
                Path(artifact_absolute),
                str(collection[collection_sha_key]),
                repo,
                git_dir,
            )
            installed_state = capture_one(capture, runtime_file, key)
            validate_installed_vault(before, installed_state, artifact)
        validate_final_worktree(
            repo,
            git_dir,
            manifest,
            artifact,
            str(collection[collection_sha_key]),
            receipt,
        )
        result = commit_groups(
            repo,
            git_dir,
            str(runtime["gitleaks_bin"]),
            before,
            manifest,
            artifact,
            str(collection[collection_sha_key]),
            output_directory,
            publisher_identity,
            before_update=lambda: capture_one_exact(
                capture, runtime_file, key, installed_state
            ),
            publication_mode=mode,
            mutation_tracker=tracker,
            artifact_receipt=receipt,
        )
        receipt = None
        after = capture_one(capture, runtime_file, key)
        if after["local_head"] != result["local_head"]:
            raise CommitError("Vault HEAD differs from created commit")
        validate_post_commit_state(before, after, artifact, mode)
        return (
            result,
            True,
            False,
            None,
            None,
        )
    except (
        CommitError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        rollback_ok = receipt is None
        if receipt is not None and not tracker.get("head_updated"):
            try:
                rollback_uncommitted_artifact(repo, git_dir, artifact, receipt)
                rollback_ok = True
            except Exception as rollback_exc:
                exc = CommitError(f"{exc}; artifact rollback failed: {rollback_exc}")
                rollback_ok = False
        current = capture_one(capture, runtime_file, key)
        owned_commits = [str(value) for value in tracker.get("candidate_commits", [])]
        progressed = bool(tracker.get("head_updated"))
        result_mode = mode if progressed else "blocked"
        result = vault_result_from_snapshot(
            current,
            before,
            commit_status="failed" if progressed else "not_started",
            commit_hashes=owned_commits if progressed else [],
            publication_mode=result_mode,
            deferred_cleanup=deferred,
        )
        retry_safe = rollback_ok and not progressed
        return result, False, retry_safe, str(exc), None


def recover_interrupted_publications(runtime_path: str) -> dict[str, str]:
    """Recover durable per-Vault transactions before collection observes Git state."""
    runtime = json.loads(stable_regular_bytes(Path(runtime_path)))
    if not isinstance(runtime, dict):
        raise CommitError("runtime context is malformed during transaction recovery")
    recovered: dict[str, str] = {}
    for key, prefix in (("agents_vault", "agents"), ("user_vault", "user")):
        repo = runtime.get(f"{prefix}_vault_root")
        git_dir = runtime.get(f"{prefix}_git_dir")
        if not isinstance(repo, str) or not isinstance(git_dir, str):
            raise CommitError(f"{key} runtime paths are malformed during recovery")
        try:
            recovered[key] = recover_head_index_transaction(
                git_dir,
                read_head=lambda repo=repo, git_dir=git_dir: git(
                    repo, git_dir, "rev-parse", "HEAD"
                ).stdout.strip(),
                update_head=lambda new, old, repo=repo, git_dir=git_dir: git(
                    repo, git_dir, "update-ref", "HEAD", new, old
                ),
            )
        except AtomicTransactionError as exc:
            raise CommitError(f"{key} transaction recovery failed: {exc}") from exc
    return recovered


def verify_carried_artifact(
    repo: str,
    git_dir: str,
    head: str,
    artifact: str,
    expected_sha256: str,
) -> None:
    """Bind a retained same-run commit to its exact regular artifact blob."""
    path = safe_path(artifact)
    tree_entry = git(repo, git_dir, "ls-tree", "-z", head, "--", path).stdout
    records = [record for record in tree_entry.split("\0") if record]
    if len(records) != 1 or "\t" not in records[0]:
        raise CommitError("carried artifact is absent from the retained commit")
    metadata, actual_path = records[0].split("\t", 1)
    fields = metadata.split()
    if len(fields) != 3 or fields[0] != "100644" or fields[1] != "blob" or actual_path != path:
        raise CommitError("carried artifact is not a regular reviewed blob")
    content = git_bytes(repo, git_dir, "cat-file", "blob", fields[2]).stdout
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise CommitError("carried artifact blob differs from the current collection")


def main(argv: list[str]) -> int:
    """Publish User then Agents independently from one reviewed context."""
    if len(argv) == 3 and argv[1] == "--recover":
        try:
            print(
                json.dumps(
                    recover_interrupted_publications(argv[2]),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        except (
            CommitError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            print(f"publication transaction recovery failed closed: {exc}", file=sys.stderr)
            return 75
    if len(argv) not in {11, 12}:
        print(
            "usage: commit-reviewed-publication.py --recover RUNTIME | "
            "RUNTIME PRE COLLECTION PLAN CONTEXT REVIEW INSTALLER CAPTURE "
            "REVIEW_SHA OUTPUT [CARRIED_COMMIT_RESULT]",
            file=sys.stderr,
        )
        return 64
    output = Path(argv[10])
    runtime: dict[str, object] = {}
    pre: dict[str, object] = {}
    collection: dict[str, object] = {}
    plan: dict[str, object] = {}
    context_digest: str | None = None
    bound_runtime: Path | None = None
    modes: dict[str, str] | None = None
    deferred: dict[str, list[dict[str, str]]] | None = None
    try:
        runtime_input = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        pre_input = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        collection_input = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan_input = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        context_bytes = stable_regular_bytes(Path(argv[5]))
        context_digest = hashlib.sha256(context_bytes).hexdigest()
        context = json.loads(context_bytes)
        runtime, pre, collection, plan = reviewed_inputs(
            context, runtime_input, pre_input, collection_input, plan_input
        )
        publisher_identity = validated_publisher_identity(runtime)
        review_bytes = stable_regular_bytes(Path(argv[6]))
        if hashlib.sha256(review_bytes).hexdigest() != argv[9]:
            raise CommitError("publication review changed after validation")
        review = json.loads(review_bytes)
        if (
            review.get("outcome") != "approved"
            or review.get("publication_context_sha256") != context_digest
        ):
            raise CommitError("publication review is not approved and context-bound")
        modes = {
            "agents_vault": str(review["agents_vault"]["publication_mode"]),
            "user_vault": str(review["user_vault"]["publication_mode"]),
        }
        deferred = {
            "agents_vault": list(review["agents_vault"].get("deferred_cleanup", [])),
            "user_vault": list(review["user_vault"].get("deferred_cleanup", [])),
        }
        bound_runtime, _ = write_bound_json(output.parent, "bound-runtime.json", runtime)
        bound_collection, _ = write_bound_json(
            output.parent, "bound-collection.json", collection
        )
        bound_plan, _ = write_bound_json(
            output.parent, "bound-artifact-plan.json", plan
        )
        previous: dict[str, object] | None = None
        resume_pair: dict[str, object] | None = None
        same_context_resume = False
        carried_keys: set[str] = set()
        if output.exists():
            candidate = json.loads(stable_regular_bytes(output))
            if (
                candidate.get("outcome") != "partial_publication"
                or candidate.get("phase") != "local_commit"
                or candidate.get("publication_context_sha256") != context_digest
                or not isinstance(candidate.get("resumable_state"), dict)
            ):
                raise CommitError("existing commit result is not a resumable partial publication")
            resume_pair = capture_state(argv[8], str(bound_runtime))
            for resume_key, label in (
                ("agents_vault", "Agents"), ("user_vault", "User")
            ):
                if not same_semantic_vault_state(
                    resume_pair.get(resume_key),
                    candidate["resumable_state"].get(resume_key),
                ):
                    raise CommitError(
                        "Vaults no longer match the resumable result: "
                        f"{label} Vault no longer matches"
                    )
            previous = candidate
            same_context_resume = True
        elif len(argv) == 12:
            carried_bytes = stable_regular_bytes(Path(argv[11]))
            candidate = json.loads(carried_bytes)
            context_carried = context.get("carried_commit_result")
            context_carried_digest = context.get("carried_commit_result_sha256")
            if candidate is None:
                if context_carried is not None or context_carried_digest is not None:
                    raise CommitError("empty carried result differs from reviewed context")
            else:
                if (
                    not isinstance(candidate, dict)
                    or candidate != context_carried
                    or hashlib.sha256(carried_bytes).hexdigest()
                    != context_carried_digest
                    or candidate.get("outcome") != "partial_publication"
                    or candidate.get("phase") != "local_commit"
                    or not isinstance(candidate.get("resumable_state"), dict)
                ):
                    raise CommitError("carried commit result is not context-bound progress")
                current_pair = capture_state(argv[8], str(bound_runtime))
                if not same_semantic_state_pair(current_pair, pre):
                    raise CommitError("Vaults no longer match the carried commit result")
                for carry_key, carry_prefix, result_path_key, plan_key, sha_key in (
                    (
                        "agents_vault", "agents", "advisory_path",
                        "advisory_target", "advisory_sha256",
                    ),
                    (
                        "user_vault", "user", "summary_path",
                        "summary_target", "summary_sha256",
                    ),
                ):
                    carried_path = candidate.get(result_path_key)
                    if carried_path is None:
                        continue
                    claimed = candidate.get(carry_key)
                    if (
                        not isinstance(carried_path, str)
                        or carried_path != plan[plan_key]
                        or not isinstance(claimed, dict)
                        or claimed.get("commit_status") != "complete"
                        or not claimed.get("commit_hashes")
                        or claimed.get("local_head") != pre[carry_key]["local_head"]
                        or claimed.get("post_dirty_digest")
                        != pre[carry_key]["dirty_digest"]
                        or not same_semantic_vault_state(
                            candidate["resumable_state"].get(carry_key),
                            pre[carry_key],
                        )
                        or review[carry_key].get("publication_mode") != "own_only"
                        or review[carry_key].get("commit_required") is not False
                        or review[carry_key].get("commit_groups")
                    ):
                        raise CommitError("carried Vault publication contract is invalid")
                    repo = str(runtime[f"{carry_prefix}_vault_root"])
                    artifact = str(Path(carried_path).relative_to(repo))
                    verify_carried_artifact(
                        repo,
                        str(runtime[f"{carry_prefix}_git_dir"]),
                        str(claimed["local_head"]),
                        artifact,
                        str(collection[sha_key]),
                    )
                    carried_keys.add(carry_key)
                if not carried_keys:
                    raise CommitError("carried result contains no completed Vault")
                previous = candidate

        if same_context_resume and previous is not None:
            for completed_key, completed_prefix, completed_label in (
                ("agents_vault", "agents", "Agents"),
                ("user_vault", "user", "User"),
            ):
                claimed = previous[completed_key]
                if not claimed.get("commit_hashes"):
                    continue
                actual = current_state(
                    str(runtime[f"{completed_prefix}_vault_root"]),
                    str(runtime[f"{completed_prefix}_git_dir"]),
                    pre[completed_key],
                )
                if any(
                    actual[field] != claimed.get(field)
                    for field in (
                        "commit_hashes",
                        "local_head",
                        "clean",
                        "post_dirty_digest",
                    )
                ):
                    raise CommitError(
                        f"{completed_label} Vault no longer matches the resumable result"
                    )

        def run_vault(
            key: str,
            prefix: str,
            role: str,
            plan_key: str,
            sha_key: str,
        ) -> tuple[
            dict[str, object],
            bool,
            bool,
            str | None,
            dict[str, object] | None,
        ]:
            if key in carried_keys:
                current = capture_one(argv[8], str(bound_runtime), key)
                if not same_semantic_vault_state(current, pre[key]):
                    label = "Agents" if key == "agents_vault" else "User"
                    raise CommitError(f"{label} Vault changed after carried review")
                assert previous is not None
                claimed = dict(previous[key])
                claimed["publication_mode"] = review[key]["publication_mode"]
                claimed["deferred_cleanup"] = list(review[key]["deferred_cleanup"])
                claimed["post_dirty_digest"] = current["dirty_digest"]
                claimed["clean"] = (
                    current.get("worktree_capture_scope") == "full"
                    and not current["dirty_lines"]
                )
                return claimed, True, False, None, None
            if same_context_resume and previous is not None and previous[key].get("commit_hashes"):
                actual = current_state(
                    str(runtime[f"{prefix}_vault_root"]),
                    str(runtime[f"{prefix}_git_dir"]),
                    pre[key],
                )
                claimed = previous[key]
                if any(
                    actual[field] != claimed.get(field)
                    for field in (
                        "commit_hashes",
                        "local_head",
                        "clean",
                        "post_dirty_digest",
                    )
                ):
                    label = "Agents" if key == "agents_vault" else "User"
                    raise CommitError(
                        f"{label} Vault no longer matches the resumable result"
                    )
                actual["commit_status"] = "complete"
                actual["publication_mode"] = claimed["publication_mode"]
                actual["deferred_cleanup"] = list(claimed["deferred_cleanup"])
                return actual, True, False, None, None
            last: tuple[
                dict[str, object],
                bool,
                bool,
                str | None,
                dict[str, object] | None,
            ] | None = None
            attempts = 1 if review[key]["publication_mode"] == "blocked" else 3
            for _ in range(attempts):
                last = publish_one_vault(
                    key=key,
                    prefix=prefix,
                    role=role,
                    artifact_plan_key=plan_key,
                    collection_sha_key=sha_key,
                    runtime=runtime,
                    pre=pre,
                    collection=collection,
                    plan=plan,
                    manifest=review[key],
                    installer=argv[7],
                    capture=argv[8],
                    runtime_file=str(bound_runtime),
                    collection_file=str(bound_collection),
                    plan_file=str(bound_plan),
                    output_directory=output.parent,
                    publisher_identity=publisher_identity,
                    resume_state=(
                        resume_pair[key]
                        if same_context_resume and resume_pair is not None
                        else None
                    ),
                )
                result, succeeded, retry_safe, _reason, _rollback_handle = last
                if succeeded or result["commit_hashes"] or not retry_safe:
                    break
                if not same_semantic_vault_state(
                    capture_one(argv[8], str(bound_runtime), key), pre[key]
                ):
                    break
            assert last is not None
            return last

        # The daily summary is the primary availability objective. A failure in
        # Agents publication must not suppress the independently safe User commit.
        user, user_ok, user_retry_safe, user_reason, _user_rollback = run_vault(
            "user_vault",
            "user",
            "user_it_news_summary",
            "summary_target",
            "summary_sha256",
        )
        agents, agents_ok, agents_retry_safe, agents_reason, _agents_rollback = run_vault(
            "agents_vault",
            "agents",
            "agents_security_advisory",
            "advisory_target",
            "advisory_sha256",
        )
        # Keep every independently safe commit.  A peer failure is represented
        # as partial publication and must not roll back the primary daily
        # summary (or the independently safe advisory) before fixed push.
        progressed = bool(user["commit_hashes"] or agents["commit_hashes"])
        publishable = user_ok or agents_ok or progressed
        attempted_retry_safety = []
        attempted_retry_vaults = []
        if review["user_vault"]["publication_mode"] != "blocked" and not user_ok:
            attempted_retry_safety.append(user_retry_safe)
            if user_retry_safe:
                attempted_retry_vaults.append("user_vault")
        if review["agents_vault"]["publication_mode"] != "blocked" and not agents_ok:
            attempted_retry_safety.append(agents_retry_safe)
            if agents_retry_safe:
                attempted_retry_vaults.append("agents_vault")
        retry_disposition = (
            "replan"
            if attempted_retry_safety
            and all(attempted_retry_safety)
            and attempted_retry_vaults
            else "none"
        )
        reasons = [
            value
            for value in (
                f"User Vault: {user_reason}" if user_reason and not user_ok else None,
                f"Agents Vault: {agents_reason}" if agents_reason and not agents_ok else None,
            )
            if value
        ]
        outcome = (
            "partial_publication"
            if progressed and not (user_ok and agents_ok)
            else ("ready_to_push" if publishable else "blocked")
        )
        result = {
            "outcome": outcome,
            "phase": "local_commit",
            "daily_pipeline_status": "complete",
            "summary_path": plan["summary_target"] if user_ok else None,
            "advisory_path": plan["advisory_target"] if agents_ok else None,
            "notification_result": collection.get("notification_result"),
            "agents_vault": agents,
            "user_vault": user,
            "publication_mode": {
                "agents_vault": agents["publication_mode"],
                "user_vault": user["publication_mode"],
            },
            "deferred_cleanup": {
                "agents_vault": agents["deferred_cleanup"],
                "user_vault": user["deferred_cleanup"],
            },
            "evidence_finalization_commit": None,
            "retry_disposition": retry_disposition,
            "replan_vaults": (
                attempted_retry_vaults if retry_disposition == "replan" else []
            ),
            "next_action": "; ".join(reasons) if reasons else None,
        }
        if not publishable and result["next_action"] is None:
            result["next_action"] = "Both Vault publications are blocked by review"
        if outcome == "partial_publication":
            result["publication_context_sha256"] = context_digest
            result["resumable_state"] = capture_state(argv[8], str(bound_runtime))
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 0 if publishable else 75
    except (
        CommitError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Local publication failed closed: {exc}", file=sys.stderr)
        result = result_after_failure(
            runtime,
            pre,
            collection,
            plan,
            f"Local publication failed closed: {exc}",
            context_digest,
            argv[8] if bound_runtime is not None else None,
            str(bound_runtime) if bound_runtime is not None else None,
            modes,
            deferred,
        )
        try:
            output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return 75


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

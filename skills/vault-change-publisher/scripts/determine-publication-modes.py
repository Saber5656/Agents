#!/usr/bin/env python3
"""Derive per-Vault publication constraints from two immutable snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from copy import deepcopy
from pathlib import Path


class ModeError(RuntimeError):
    """Represent an invalid or incomplete publication-mode input."""


STATE_CHANGE_FIELDS = (
    "capture_status",
    "branch",
    "upstream",
    "local_head",
    "operation_in_progress",
    "git_control_sha256",
    "worktree_capture_scope",
    "index_entries",
    "dirty_worktree_sha256",
    "dirty_digest",
    "diff_snapshot_sha256",
    "dirty_materialization",
)

VOLATILE_INDEX_FIELDS = frozenset({"index_sha256", "index_identity"})


def digest(value: object) -> str:
    """Hash one canonical JSON value."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def semantic_state(value: dict[str, object]) -> dict[str, object]:
    """Exclude Git index stat-cache serialization from a state identity."""
    return {
        key: item for key, item in value.items() if key not in VOLATILE_INDEX_FIELDS
    }


def target_binding(root: object, target: object) -> tuple[str, bool]:
    """Return a normalized target and whether its planned slot became unsafe."""
    if not isinstance(root, str) or not isinstance(target, str):
        raise ModeError("publication target is not a string")
    root_path = Path(root)
    target_path = Path(target)
    if not root_path.is_absolute() or not target_path.is_absolute():
        raise ModeError("publication target is not absolute")
    try:
        relative = target_path.relative_to(root_path)
    except ValueError as exc:
        raise ModeError("publication target escapes its Vault") from exc
    if not relative.parts or ".." in relative.parts:
        raise ModeError("publication target is invalid")
    current = root_path
    conflict = False
    for component in relative.parts[:-1]:
        current /= component
        if not os.path.lexists(current):
            break
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            conflict = True
            break
    if os.path.lexists(target_path):
        conflict = True
    return str(relative), conflict


def vault_mode(
    initial: dict[str, object],
    current: dict[str, object],
    artifact_target: str,
    target_conflict: bool = False,
) -> dict[str, object]:
    """Classify one Vault without treating unrelated dirty state as failure."""
    changed_fields = [
        field for field in STATE_CHANGE_FIELDS if initial.get(field) != current.get(field)
    ]
    reasons: list[str] = []
    mode = "sweep"
    retry_disposition = "none"
    if current.get("capture_status", "available") != "available":
        mode = "blocked"
        reasons.append("vault_state_capture_unavailable")
    if current.get("branch") != "main" or current.get("upstream") != "origin/main":
        mode = "blocked"
        reasons.append("main_tracking_origin_main_required")
    if current.get("history_relation") not in {"equal", "local_ahead"}:
        mode = "blocked"
        reasons.append("remote_ahead_or_diverged_history")
    if (
        current.get("history_relation") == "local_ahead"
        and current.get("history_capture_status") != "available"
    ):
        mode = "blocked"
        reasons.append("local_history_review_material_unavailable")
    if current.get("operation_in_progress") is not False:
        mode = "blocked"
        reasons.append("active_git_operation")
    if target_conflict or artifact_target in current.get("dirty_paths", []):
        mode = "blocked"
        reasons.append("planned_target_changed_before_publication")
        retry_disposition = "replan"
    if initial.get("git_control_sha256") != current.get("git_control_sha256"):
        mode = "blocked"
        reasons.append("git_control_plane_changed")
    if mode != "blocked" and changed_fields:
        mode = "own_only"
        reasons.append("vault_state_changed_during_collection")
    if mode != "blocked" and current.get("staged_paths"):
        mode = "own_only"
        reasons.append("existing_staged_changes")
    if (
        mode != "blocked"
        and current.get("worktree_capture_scope") == "index_only"
    ):
        mode = "own_only"
        reasons.append("worktree_scan_intentionally_omitted")
    if not reasons:
        reasons.append("stable_sweep_candidate")
    return {
        "required_mode": mode,
        "state_changed": bool(changed_fields),
        "changed_fields": changed_fields,
        "reasons": reasons,
        "artifact_target": artifact_target,
        "retry_disposition": retry_disposition,
        "initial_state_sha256": digest(semantic_state(initial)),
        "review_state_sha256": digest(semantic_state(current)),
    }


def apply_residual_guards(
    mode_hint: dict[str, object], snapshot_manifest: dict[str, object]
) -> dict[str, object]:
    """Make sealed residual deferrals a deterministic per-Vault mode floor."""
    if snapshot_manifest.get("version") != 4:
        raise ModeError("dirty snapshot manifest version is invalid")
    vaults = snapshot_manifest.get("vaults")
    local_commits = snapshot_manifest.get("local_commits")
    if not isinstance(vaults, dict) or set(vaults) != {
        "agents_vault",
        "user_vault",
    } or not isinstance(local_commits, dict) or set(local_commits) != {
        "agents_vault",
        "user_vault",
    }:
        raise ModeError("dirty snapshot manifest Vault set is invalid")
    result = deepcopy(mode_hint)
    for key in ("agents_vault", "user_vault"):
        entry = result.get(key)
        snapshots = vaults.get(key)
        commit_snapshots = local_commits.get(key)
        if (
            not isinstance(entry, dict)
            or not isinstance(snapshots, list)
            or not isinstance(commit_snapshots, list)
        ):
            raise ModeError("publication mode hint shape is invalid")
        deferred_paths = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                raise ModeError("dirty snapshot entry is invalid")
            status = snapshot.get("materialization_status")
            path = snapshot.get("path")
            if status not in {"available", "deferred", "not_required"}:
                raise ModeError("dirty snapshot materialization status is invalid")
            if not isinstance(path, str):
                raise ModeError("dirty residual path is invalid")
            if status == "deferred":
                deferred_paths.append(path)
        deferred_paths.sort()
        if len(deferred_paths) != len(set(deferred_paths)):
            raise ModeError("deferred residual path is duplicated")
        entry["guard_deferred_paths"] = deferred_paths
        blocked_commits = []
        for snapshot in commit_snapshots:
            if not isinstance(snapshot, dict):
                raise ModeError("local commit snapshot entry is invalid")
            status = snapshot.get("materialization_status")
            commit = snapshot.get("commit")
            if status not in {"available", "blocked"}:
                raise ModeError("local commit materialization status is invalid")
            if not isinstance(commit, str):
                raise ModeError("local commit identity is invalid")
            if status != "available":
                blocked_commits.append(commit)
        blocked_commits.sort()
        if len(blocked_commits) != len(set(blocked_commits)):
            raise ModeError("blocked local commit is duplicated")
        entry["guard_blocked_commits"] = blocked_commits
        reasons = entry.get("reasons")
        if not isinstance(reasons, list) or any(
            not isinstance(reason, str) for reason in reasons
        ):
            raise ModeError("publication mode reasons are invalid")
        if blocked_commits:
            entry["required_mode"] = "blocked"
            entry["retry_disposition"] = "none"
            entry["reasons"] = [
                reason for reason in reasons if reason != "stable_sweep_candidate"
            ] + ["sealed_local_history_guard_blocked"]
            continue
        if deferred_paths and entry.get("required_mode") == "sweep":
            entry["required_mode"] = "own_only"
            entry["reasons"] = [
                reason for reason in reasons if reason != "stable_sweep_candidate"
            ] + ["sealed_residual_guard_deferred"]
    return result


def main(argv: list[str]) -> int:
    """Read runner-owned inputs and emit deterministic per-Vault mode hints."""
    if len(argv) == 4 and argv[1] == "--apply-residual-guards":
        try:
            mode_hint = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
            snapshot_manifest = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
            result = apply_residual_guards(mode_hint, snapshot_manifest)
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ModeError,
        ) as exc:
            print(f"publication residual guard application failed:{exc}", file=sys.stderr)
            return 75
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if len(argv) != 5:
        print(
            "usage: determine-publication-modes.py INITIAL CURRENT PLAN RUNTIME\n"
            "   or: determine-publication-modes.py --apply-residual-guards MODE_HINT MANIFEST",
            file=sys.stderr,
        )
        return 64
    try:
        initial = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        current = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        runtime = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        agents_target, agents_conflict = target_binding(
            runtime["agents_vault_root"], plan["advisory_target"]
        )
        user_target, user_conflict = target_binding(
            runtime["user_vault_root"], plan["summary_target"]
        )
        result = {
            "version": 1,
            "agents_vault": vault_mode(
                initial["agents_vault"],
                current["agents_vault"],
                agents_target,
                agents_conflict,
            ),
            "user_vault": vault_mode(
                initial["user_vault"],
                current["user_vault"],
                user_target,
                user_conflict,
            ),
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, ModeError) as exc:
        print(f"publication mode determination failed:{exc}", file=sys.stderr)
        return 75
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

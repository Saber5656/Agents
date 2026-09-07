#!/usr/bin/env python3
"""Validate a digest-bound read-only publication review result."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from copy import deepcopy
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Optional


class ReviewError(RuntimeError):
    """Represent a review result that cannot authorize mutation."""


VOLATILE_INDEX_FIELDS = frozenset({"index_sha256", "index_identity"})
REVIEW_REQUEST_LIMIT_CHARS = 900_000
REVIEW_REQUEST_LIMIT_BYTES = 900_000
REVIEW_VAULTS = frozenset({"agents_vault", "user_vault"})
REVIEW_MODE_RANK = {"sweep": 0, "own_only": 1, "blocked": 2}


def same_semantic_vault_state(
    left: object, right: object
) -> bool:
    """Compare Git/Vault meaning while ignoring index stat-cache serialization."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return {
        key: value for key, value in left.items() if key not in VOLATILE_INDEX_FIELDS
    } == {
        key: value for key, value in right.items() if key not in VOLATILE_INDEX_FIELDS
    }


def sha256(path: Path) -> str:
    """Hash one runner-owned context file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_regular_nofollow(path: Path) -> bytes:
    """Read a stable regular snapshot without following a final-component symlink."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ReviewError("authorization snapshot is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
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
            raise ReviewError("authorization snapshot changed while being validated")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def sha256_regular_nofollow(path: Path) -> str:
    """Hash one stable regular snapshot."""
    return hashlib.sha256(read_regular_nofollow(path)).hexdigest()


def validate_review_input_contract(
    metrics_path: Path,
    expected_metrics_digest: str,
    context_path: Path,
) -> dict[str, str]:
    """Verify bounded review metrics and derive its deterministic mode floor."""
    metrics_bytes = read_regular_nofollow(metrics_path)
    if hashlib.sha256(metrics_bytes).hexdigest() != expected_metrics_digest:
        raise ReviewError("review input metrics digest mismatch")
    try:
        metrics = json.loads(metrics_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("review input metrics are unreadable") from exc
    if not isinstance(metrics, dict) or metrics.get("status") != "ready":
        raise ReviewError("review input metrics are not ready")
    if metrics.get("publication_context_projection") != "review_bounded_v2":
        raise ReviewError("review input projection version is invalid")
    if metrics.get("publication_context_sha256") != sha256_regular_nofollow(
        context_path
    ):
        raise ReviewError("review input metrics context digest mismatch")
    for field in ("request_chars", "request_bytes"):
        value = metrics.get(field)
        if type(value) is not int or value < 0:
            raise ReviewError("review input metrics size is invalid")
    if (
        metrics["request_chars"] > REVIEW_REQUEST_LIMIT_CHARS
        or metrics["request_bytes"] > REVIEW_REQUEST_LIMIT_BYTES
    ):
        raise ReviewError("review input metrics exceed the configured request limit")

    def bounded_vault_list(field: str) -> list[str]:
        values = metrics.get(field)
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or value not in REVIEW_VAULTS for value in values)
            or values != sorted(set(values))
        ):
            raise ReviewError(f"review input metrics {field} is invalid")
        return values

    residual_vaults = bounded_vault_list("residual_review_budget_vaults")
    history_vaults = bounded_vault_list("history_review_budget_vaults")
    expected_floor = {"agents_vault": "sweep", "user_vault": "sweep"}
    for vault in history_vaults:
        expected_floor[vault] = "blocked"
    for vault in residual_vaults:
        if expected_floor[vault] == "sweep":
            expected_floor[vault] = "own_only"
    mode_floor = metrics.get("mode_floor")
    if mode_floor != expected_floor:
        raise ReviewError("review input metrics mode floor is inconsistent")
    if any(mode not in REVIEW_MODE_RANK for mode in mode_floor.values()):
        raise ReviewError("review input metrics mode floor is invalid")
    omitted_fields = metrics.get("omitted_fields")
    if (
        not isinstance(omitted_fields, list)
        or any(not isinstance(field, str) or not field for field in omitted_fields)
        or omitted_fields != sorted(set(omitted_fields))
    ):
        raise ReviewError("review input metrics omitted fields are invalid")
    return mode_floor


def read_regular_beneath(root: Path, relative: PurePosixPath) -> bytes:
    """Read a stable regular file without following any relative path component."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    opened = [os.open(root, flags)]
    try:
        current = opened[-1]
        for component in relative.parts[:-1]:
            current = os.open(component, flags, dir_fd=current)
            opened.append(current)
        descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ReviewError("dirty snapshot is not a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 65536):
                chunks.append(chunk)
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
                raise ReviewError("dirty snapshot changed while being validated")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def validate_dirty_snapshots(
    context: dict[str, object], pre: dict[str, object]
) -> dict[str, object]:
    """Bind staged dirty blobs to the runner-captured pre-collection entries."""
    manifest_path = Path(context["dirty_snapshot_manifest_file"])
    expected_manifest_digest = context["dirty_snapshot_manifest_sha256"]
    manifest_bytes = read_regular_nofollow(manifest_path)
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_digest:
        raise ReviewError("dirty snapshot manifest digest mismatch")
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if manifest.get("version") != 4 or set(manifest.get("vaults", {})) != {
        "agents_vault",
        "user_vault",
    } or set(manifest.get("local_commits", {})) != {
        "agents_vault",
        "user_vault",
    }:
        raise ReviewError("dirty snapshot manifest shape mismatch")
    for label, state_key in (("agents", "agents_vault"), ("user", "user_vault")):
        actual_entries = manifest["vaults"][state_key]
        expected_entries = pre[state_key]["dirty_entries"]
        if len(actual_entries) != len(expected_entries):
            raise ReviewError("dirty snapshot entry count mismatch")
        for index, (actual, expected) in enumerate(zip(actual_entries, expected_entries)):
            if {
                "path": actual.get("path"),
                "git_blob_oid": actual.get("git_blob_oid"),
                "mode": actual.get("mode"),
            } != expected:
                raise ReviewError("dirty snapshot identity mismatch")
            if expected["mode"] is None and expected["git_blob_oid"] is None:
                deleted_not_required = (
                    actual.get("materialization_status") == "not_required"
                    and actual.get("materialization_reason") is None
                )
                deleted_deferred = (
                    actual.get("materialization_status") == "deferred"
                    and isinstance(actual.get("materialization_reason"), str)
                    and bool(actual["materialization_reason"])
                )
                if (
                    actual.get("snapshot") is not None
                    or actual.get("sha256") is not None
                    or not (deleted_not_required or deleted_deferred)
                ):
                    raise ReviewError("deleted dirty entry unexpectedly has snapshot bytes")
                continue
            status = actual.get("materialization_status")
            if status == "deferred":
                if (
                    actual.get("snapshot") is not None
                    or actual.get("sha256") is not None
                    or not isinstance(actual.get("materialization_reason"), str)
                    or not actual["materialization_reason"]
                ):
                    raise ReviewError("deferred dirty entry has invalid materialization evidence")
                continue
            if (
                status != "available"
                or actual.get("materialization_reason") is not None
                or expected["mode"] not in {"100644", "100755"}
            ):
                raise ReviewError("dirty snapshot materialization status is invalid")
            expected_relative = f"dirty-snapshots/{label}/{index:04d}.blob"
            if actual.get("snapshot") != expected_relative:
                raise ReviewError("dirty snapshot path mismatch")
            snapshot_bytes = read_regular_beneath(
                manifest_path.parent, PurePosixPath(expected_relative)
            )
            if hashlib.sha256(snapshot_bytes).hexdigest() != actual.get("sha256"):
                raise ReviewError("dirty snapshot content digest mismatch")
        actual_commits = manifest["local_commits"][state_key]
        expected_commits = pre[state_key].get("local_commits", [])
        if len(actual_commits) != len(expected_commits):
            raise ReviewError("local commit snapshot count mismatch")
        for index, (actual, expected) in enumerate(
            zip(actual_commits, expected_commits)
        ):
            actual_identity = {
                key: actual.get(key)
                for key in (
                    "commit", "parents", "tree", "message", "changed_paths",
                )
            }
            if actual_identity != expected:
                raise ReviewError("local commit snapshot identity mismatch")
            status = actual.get("materialization_status")
            if status == "blocked":
                if (
                    actual.get("snapshot") is not None
                    or actual.get("sha256") is not None
                    or not isinstance(actual.get("materialization_reason"), str)
                    or not actual["materialization_reason"]
                ):
                    raise ReviewError("blocked local commit has invalid materialization evidence")
                continue
            if status != "available" or actual.get("materialization_reason") is not None:
                raise ReviewError("local commit materialization status is invalid")
            expected_relative = f"commit-snapshots/{label}/{index:04d}.patch"
            if actual.get("snapshot") != expected_relative:
                raise ReviewError("local commit snapshot path mismatch")
            snapshot_bytes = read_regular_beneath(
                manifest_path.parent, PurePosixPath(expected_relative)
            )
            digest = hashlib.sha256(snapshot_bytes).hexdigest()
            if digest != actual.get("sha256") or digest != actual.get("patch_sha256"):
                raise ReviewError("local commit patch digest mismatch")
    return manifest


def normalize_own_only_residuals(
    review: dict[str, object],
    context: dict[str, object],
    pre: dict[str, object],
    materialization: dict[str, object],
    mode_floor: Optional[dict[str, str]] = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Complete deferred residual structure from sealed snapshot identity.

    The model still owns the core-quality and publication-mode decisions.  This
    projection prevents a large dirty-path set from becoming a model copying
    task after it has already selected ``own_only`` or ``blocked`` with a
    deferred residual review.  Supplied reasons are retained only for exact
    captured paths. ``mode_floor`` is derived from bounded-input metrics and
    never weakens the selected publication mode.
    """
    result = deepcopy(review)
    receipt: dict[str, object] = {}
    generic_reason = (
        "pre-existing dirty path excluded unchanged because "
        "publication_mode_hint requires deferred residual handling"
    )
    for key in ("agents_vault", "user_vault"):
        manifest = result.get(key)
        state = pre.get(key)
        if not isinstance(manifest, dict) or not isinstance(state, dict):
            raise ReviewError("own-only normalization input is malformed")
        declared_mode = manifest.get("publication_mode")
        floor = (mode_floor or {}).get(key, "sweep")
        should_normalize = (
            (
                declared_mode == "own_only"
                and manifest.get("core_review_status") == "quality_ok"
                and manifest.get("review_or_validation_status") == "quality_ok"
                and manifest.get("residual_review_status") == "deferred"
            )
            or (
                floor in {"own_only", "blocked"}
                and declared_mode == "blocked"
                and manifest.get("residual_review_status") in {"deferred", "blocked"}
            )
        )
        if not should_normalize:
            receipt[key] = {
                "normalized": False,
                "dirty_path_count": 0,
                "mode": declared_mode,
                "mode_floor": floor,
            }
            continue

        dirty_paths = state.get("dirty_paths")
        if (
            not isinstance(dirty_paths, list)
            or any(not isinstance(path, str) or not path for path in dirty_paths)
            or len(dirty_paths) != len(set(dirty_paths))
        ):
            raise ReviewError("captured dirty paths are not unique strings")
        ordered_paths = sorted(dirty_paths)
        dirty_set = set(ordered_paths)

        supplied_lists: dict[str, list[str]] = {}
        for field in ("excluded_paths", "unrelated_dirty_paths"):
            supplied = manifest.get(field)
            if (
                not isinstance(supplied, list)
                or any(not isinstance(path, str) for path in supplied)
                or len(supplied) != len(set(supplied))
                or not set(supplied).issubset(dirty_set)
            ):
                raise ReviewError(
                    f"own-only {field} contains duplicate or foreign paths"
                )
            supplied_lists[field] = supplied

        supplied_deferred = manifest.get("deferred_cleanup")
        if not isinstance(supplied_deferred, list):
            raise ReviewError("own-only deferred cleanup is not a list")
        reasons: dict[str, str] = {}
        for item in supplied_deferred:
            if not isinstance(item, dict):
                raise ReviewError("own-only deferred cleanup entry is malformed")
            path = item.get("path")
            reason = item.get("reason")
            if (
                not isinstance(path, str)
                or path not in dirty_set
                or path in reasons
                or not isinstance(reason, str)
                or not reason
            ):
                raise ReviewError(
                    "own-only deferred cleanup contains duplicate or foreign paths"
                )
            reasons[path] = reason

        materialized_vaults = materialization.get("vaults")
        if not isinstance(materialized_vaults, dict):
            raise ReviewError("own-only materialization manifest is malformed")
        vault_entries = materialized_vaults.get(key)
        if not isinstance(vault_entries, list) or len(vault_entries) != len(
            ordered_paths
        ):
            raise ReviewError("own-only materialized residual count mismatch")
        materialized_by_path: dict[str, dict[str, object]] = {}
        for entry in vault_entries:
            if not isinstance(entry, dict) or entry.get("path") in materialized_by_path:
                raise ReviewError("own-only materialized residual identity is invalid")
            path = entry.get("path")
            if not isinstance(path, str) or path not in dirty_set:
                raise ReviewError("own-only materialized residual path is foreign")
            materialized_by_path[path] = entry

        original_reason_count = len(reasons)
        for path in ordered_paths:
            if path in reasons:
                continue
            entry = materialized_by_path[path]
            materialization_reason = entry.get("materialization_reason")
            reasons[path] = (
                materialization_reason
                if isinstance(materialization_reason, str) and materialization_reason
                else generic_reason
            )

        manifest["excluded_paths"] = ordered_paths
        manifest["unrelated_dirty_paths"] = ordered_paths
        manifest["deferred_cleanup"] = [
            {"path": path, "reason": reasons[path]} for path in ordered_paths
        ]
        receipt[key] = {
            "normalized": True,
            "mode": declared_mode,
            "mode_floor": floor,
            "dirty_path_count": len(ordered_paths),
            "supplied_excluded_count": len(supplied_lists["excluded_paths"]),
            "supplied_unrelated_count": len(
                supplied_lists["unrelated_dirty_paths"]
            ),
            "supplied_reason_count": original_reason_count,
            "filled_reason_count": len(ordered_paths) - original_reason_count,
        }
    return result, receipt


def normalize_sealed_local_history(
    review: dict[str, object],
    pre: dict[str, object],
    materialization: dict[str, object],
    mode_floor: dict[str, str],
) -> tuple[dict[str, object], dict[str, object]]:
    """Restore exact local-ahead commit identity omitted from the projection.

    ``approved_existing_commits`` is a copy-only binding, not a reviewer
    judgment.  When a bounded projection omitted ``local_commits`` or a
    nested ``changed_paths`` array, the reviewer can still choose the required
    ``own_only``/``blocked`` mode without reproducing an unbounded history.
    The complete version-4 snapshot is the sole source for the restored
    identity; the final validator continues to compare it with the pre-state.
    """
    result = deepcopy(review)
    receipt: dict[str, object] = {}
    materialized_commits = materialization.get("local_commits")
    if not isinstance(materialized_commits, dict):
        raise ReviewError("local history materialization manifest is malformed")
    for key in ("agents_vault", "user_vault"):
        manifest = result.get(key)
        state = pre.get(key)
        floor = mode_floor.get(key)
        if not isinstance(manifest, dict) or not isinstance(state, dict):
            raise ReviewError("local history normalization input is malformed")
        declared_mode = manifest.get("publication_mode")
        source = state.get("local_commits", [])
        materialized = materialized_commits.get(key)
        if (
            not isinstance(source, list)
            or not isinstance(materialized, list)
            or len(source) != len(materialized)
        ):
            raise ReviewError("local history normalization count mismatch")

        should_normalize = (
            floor in {"own_only", "blocked"}
            and declared_mode in {"own_only", "blocked"}
        )
        supplied = manifest.get("approved_existing_commits")
        if not isinstance(supplied, list):
            raise ReviewError("approved existing commits are not a list")
        if not should_normalize:
            receipt[key] = {
                "normalized": False,
                "mode": declared_mode,
                "mode_floor": floor,
                "supplied_count": len(supplied),
                "restored_count": 0,
            }
            continue

        expected: list[dict[str, object]] = []
        for commit, material in zip(source, materialized):
            if not isinstance(commit, dict) or not isinstance(material, dict):
                raise ReviewError("local history normalization entry is malformed")
            expected.append({**commit, "patch_sha256": material.get("patch_sha256")})
        manifest["approved_existing_commits"] = expected
        receipt[key] = {
            "normalized": supplied != expected,
            "mode": declared_mode,
            "mode_floor": floor,
            "supplied_count": len(supplied),
            "restored_count": len(expected),
            "source": "sealed_version4_snapshot",
        }
    return result, receipt


def normalize_sealed_validation_evidence(
    review: dict[str, object],
    context: dict[str, object],
    pre: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Project copy-only validation identity from the sealed runner context.

    The reviewer still owns both validation judgments (``file_guard`` and
    ``secret_scan``).  The four fields below are not judgments: they are exact
    copies of runner-sealed identity that the deterministic validator already
    requires.  Canonicalizing them prevents a model from confusing the mode
    hint's review-state digest with the reviewed diff snapshot digest without
    weakening any publication guard.
    """
    result = deepcopy(review)
    runtime = context.get("runtime")
    if not isinstance(runtime, dict):
        raise ReviewError("validation evidence runtime context is malformed")
    gitleaks_version = runtime.get("gitleaks_version")
    if not isinstance(gitleaks_version, str) or not gitleaks_version:
        raise ReviewError("sealed gitleaks version is malformed")

    receipt: dict[str, object] = {}
    for key in ("agents_vault", "user_vault"):
        manifest = result.get(key)
        state = pre.get(key)
        if not isinstance(manifest, dict) or not isinstance(state, dict):
            raise ReviewError("validation evidence normalization input is malformed")
        evidence = manifest.get("validation_evidence")
        if not isinstance(evidence, dict):
            raise ReviewError("validation evidence is not an object")

        diff_snapshot = state.get("diff_snapshot_sha256")
        history_snapshot = state.get("history_snapshot_sha256")
        for label, value in (
            ("diff snapshot", diff_snapshot),
            ("history snapshot", history_snapshot),
        ):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise ReviewError(f"sealed {label} digest is malformed")

        expected = {
            "secret_scan_tool": "gitleaks",
            "secret_scan_tool_version": gitleaks_version,
            "reviewed_snapshot_sha256": diff_snapshot,
            "reviewed_history_sha256": history_snapshot,
        }
        corrected_fields = sorted(
            field for field, value in expected.items() if evidence.get(field) != value
        )
        evidence.update(expected)
        receipt[key] = {
            "normalized": bool(corrected_fields),
            "corrected_field_count": len(corrected_fields),
            "corrected_fields": corrected_fields,
            "canonical_values": expected,
        }
    return result, receipt


def write_exclusive_json(path: Path, value: object) -> bytes:
    """Create one immutable runner-owned JSON result beside its raw input."""
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    directory_fd = os.open(path.parent, directory_flags)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ReviewError("could not write normalized publication review")
            view = view[written:]
        os.fsync(descriptor)
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)
    return content


def canonicalize_own_only_main(argv: list[str]) -> int:
    """Write a sealed structural projection while preserving the raw review."""
    if len(argv) != 9:
        print(
            "usage: validate-publication-review.py --canonicalize-own-only "
            "RAW_REVIEW CONTEXT PRE_STATE OUTPUT RECEIPT METRICS METRICS_SHA",
            file=sys.stderr,
        )
        return 64
    try:
        raw_path = Path(argv[2])
        context_path = Path(argv[3])
        pre_path = Path(argv[4])
        output_path = Path(argv[5])
        receipt_path = Path(argv[6])
        metrics_path = Path(argv[7])
        metrics_digest = argv[8]
        if not (
            raw_path.parent == output_path.parent == receipt_path.parent
            and len({raw_path.name, output_path.name, receipt_path.name}) == 3
        ):
            raise ReviewError("review normalization outputs are not sibling paths")
        raw_bytes = read_regular_nofollow(raw_path)
        context_bytes = read_regular_nofollow(context_path)
        pre_bytes = read_regular_nofollow(pre_path)
        raw = json.loads(raw_bytes)
        context = json.loads(context_bytes)
        pre = json.loads(pre_bytes)
        context_digest = hashlib.sha256(context_bytes).hexdigest()
        if raw.get("publication_context_sha256") != context_digest:
            raise ReviewError("raw review context digest mismatch")
        if pre != context.get("pre_collection_state"):
            raise ReviewError("normalization pre-state differs from reviewed context")
        mode_floor = validate_review_input_contract(
            metrics_path, metrics_digest, context_path
        )
        materialization = validate_dirty_snapshots(context, pre)
        normalized, history_normalization = normalize_sealed_local_history(
            raw, pre, materialization, mode_floor
        )
        normalized, residual_normalization = normalize_own_only_residuals(
            normalized, context, pre, materialization, mode_floor
        )
        normalized, evidence_normalization = normalize_sealed_validation_evidence(
            normalized, context, pre
        )
        normalized_bytes = (
            json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        receipt = {
            "version": 2,
            "raw_review_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "canonical_review_sha256": hashlib.sha256(normalized_bytes).hexdigest(),
            "publication_context_sha256": context_digest,
            "local_history": history_normalization,
            "vaults": residual_normalization,
            "validation_evidence": evidence_normalization,
        }
        actual_bytes = write_exclusive_json(output_path, normalized)
        if actual_bytes != normalized_bytes:
            raise ReviewError("normalized publication review encoding drifted")
        write_exclusive_json(receipt_path, receipt)
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ReviewError,
    ) as exc:
        print(f"publication review normalization failed:{exc}", file=sys.stderr)
        return 75
    return 0


def relative_target(root: str, target: str) -> str:
    """Return a normalized repo-relative planned target."""
    root_path = Path(root).resolve()
    raw_target = Path(target)
    if not raw_target.is_absolute():
        raise ReviewError("planned target is not absolute")
    target_path = raw_target.resolve()
    try:
        relative = target_path.relative_to(root_path)
    except ValueError as exc:
        raise ReviewError("planned target escapes its Vault") from exc
    if ".." in relative.parts or not relative.parts:
        raise ReviewError("invalid planned target")
    return str(relative)


def validate_carried_commit_result(
    context: dict[str, object],
    pre: dict[str, object],
    plan: dict[str, object],
) -> set[str]:
    """Bind earlier same-run progress to the new per-Vault review snapshot."""
    if (
        "carried_commit_result" not in context
        and "carried_commit_result_file" not in context
        and "carried_commit_result_sha256" not in context
    ):
        return set()
    carry_path = context.get("carried_commit_result_file")
    if not isinstance(carry_path, str) or not carry_path:
        raise ReviewError("carried commit result path is missing")
    carried_bytes = read_regular_nofollow(Path(carry_path))
    try:
        carried = json.loads(carried_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("carried commit result is unreadable") from exc
    if carried != context.get("carried_commit_result"):
        raise ReviewError("carried commit result differs from the reviewed context")
    carried_digest = context.get("carried_commit_result_sha256")
    if carried is None:
        if carried_digest is not None:
            raise ReviewError("empty carried progress unexpectedly has a digest")
        return set()
    if (
        not isinstance(carried, dict)
        or hashlib.sha256(carried_bytes).hexdigest() != carried_digest
        or carried.get("outcome") != "partial_publication"
        or carried.get("phase") != "local_commit"
        or carried.get("daily_pipeline_status") != "complete"
        or not isinstance(carried.get("resumable_state"), dict)
    ):
        raise ReviewError("carried commit result is not resumable same-run progress")

    completed: set[str] = set()
    definitions = (
        ("agents_vault", "advisory_path", "advisory_target", "agents_vault_root"),
        ("user_vault", "summary_path", "summary_target", "user_vault_root"),
    )
    for key, result_path_key, plan_key, root_key in definitions:
        result_path = carried.get(result_path_key)
        hinted = context["publication_mode_hint"][key].get(
            "artifact_already_committed"
        )
        if not isinstance(result_path, str):
            if result_path is not None or hinted is not False:
                raise ReviewError("carried Vault hint disagrees with publication progress")
            continue
        if hinted is not True or plan.get(plan_key) != result_path:
            raise ReviewError("carried artifact target differs from the new plan")
        claimed = carried.get(key)
        resumable = carried["resumable_state"].get(key)
        state = pre.get(key)
        if not all(isinstance(value, dict) for value in (claimed, resumable, state)):
            raise ReviewError("carried Vault state is malformed")
        commits = claimed.get("commit_hashes")
        if (
            claimed.get("commit_status") != "complete"
            or not isinstance(commits, list)
            or not commits
            or any(
                not isinstance(commit, str)
                or re.fullmatch(r"[0-9a-f]{40}", commit) is None
                for commit in commits
            )
            or not same_semantic_vault_state(resumable, state)
            or claimed.get("local_head") != state.get("local_head")
            or claimed.get("post_dirty_digest") != state.get("dirty_digest")
        ):
            raise ReviewError("carried Vault no longer matches its resumable state")
        relative = relative_target(str(context["runtime"][root_key]), result_path)
        captured_commits = {
            commit.get("commit"): commit for commit in state.get("local_commits", [])
        }
        if any(commit not in captured_commits for commit in commits) or not any(
            relative in captured_commits[commit].get("changed_paths", [])
            for commit in commits
        ):
            raise ReviewError("carried artifact commit is absent from local-only history")
        completed.add(key)
    return completed


def validate_manifest(
    manifest: dict[str, object],
    state: dict[str, object],
    repo_root: str,
    task_id: str,
    artifact: dict[str, str],
    evidence_target: Optional[str],
    gitleaks_version: str,
    mode_hint: dict[str, object],
    materialized_dirty: Optional[list[dict[str, object]]] = None,
    materialized_commits: Optional[list[dict[str, object]]] = None,
    carried: bool = False,
    mode_floor: str = "sweep",
) -> None:
    """Bind one layered review manifest to state and one planned artifact."""
    if manifest["repo_root"] != repo_root or manifest["task_id"] != task_id:
        raise ReviewError("Task Change Manifest identity mismatch")
    if state["repo_root"] != repo_root:
        raise ReviewError("pre-publication repository identity mismatch")
    declared_mode = manifest.get("publication_mode")
    if declared_mode not in {"sweep", "own_only", "blocked"}:
        raise ReviewError("publication mode is missing or invalid")
    if declared_mode == "sweep" and any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in state.get("dirty_paths", [])
    ):
        raise ReviewError("sweep scope contains a forbidden .obsidian path")
    local_commits = state.get("local_commits", [])
    history_capture_status = state.get("history_capture_status", "available")
    if (
        state["history_relation"] == "equal" and local_commits
    ) or (
        state["history_relation"] == "local_ahead"
        and history_capture_status == "available"
        and not local_commits
    ):
        raise ReviewError("pre-publication local history metadata is inconsistent")
    if materialized_commits is None:
        materialized_commits = [
            {**entry, "materialization_status": "available"}
            for entry in state.get("local_commits", [])
        ]
    if len(materialized_commits) != len(local_commits):
        raise ReviewError("local commit materialization count mismatch")
    expected_approved_commits = [
        {**commit, "patch_sha256": materialized.get("patch_sha256")}
        # Python 3.9 is the deployed macOS runtime. The explicit count check
        # above provides the same fail-closed guarantee as zip(strict=True).
        for commit, materialized in zip(local_commits, materialized_commits)
    ]
    if manifest["approved_existing_commits"] != expected_approved_commits:
        raise ReviewError("reviewed local-only commits do not match pre-state")
    artifact_target = relative_target(repo_root, artifact["target_path"])
    dirty_paths = sorted(state["dirty_paths"])
    initial_paths = sorted(set(dirty_paths) | {artifact_target})
    existing_paths = sorted(
        {
            path
            for commit in local_commits
            for path in commit["changed_paths"]
        }
    )
    if declared_mode == "sweep" and any(
        path == ".obsidian" or path.startswith(".obsidian/")
        for path in [*initial_paths, *existing_paths]
    ):
        raise ReviewError("sweep scope contains a forbidden .obsidian path")
    if artifact_target == ".obsidian" or artifact_target.startswith(".obsidian/"):
        raise ReviewError("artifact target is a forbidden .obsidian path")
    expected_evidence = None
    if evidence_target is not None:
        expected_evidence = relative_target(repo_root, evidence_target)
        if (
            expected_evidence == ".obsidian"
            or expected_evidence.startswith(".obsidian/")
        ):
            raise ReviewError("evidence target is a forbidden .obsidian path")
    if manifest["approved_diff_snapshot_sha256"] != state["diff_snapshot_sha256"]:
        raise ReviewError("approved diff snapshot digest mismatch")
    reviews = manifest["reviewed_artifacts"]
    if reviews != [
        {
            "role": artifact["role"],
            "source_sha256": artifact["source_sha256"],
            "target_path": artifact_target,
        }
    ]:
        raise ReviewError("reviewed artifact binding mismatch")
    evidence = manifest["validation_evidence"]
    expected_validation_evidence = {
        "secret_scan_tool": "gitleaks",
        "secret_scan_tool_version": gitleaks_version,
        "reviewed_snapshot_sha256": state["diff_snapshot_sha256"],
        "reviewed_history_sha256": state["history_snapshot_sha256"],
    }
    if any(evidence.get(key) != value for key, value in expected_validation_evidence.items()) or evidence.get(
        "file_guard"
    ) not in {"passed", "blocked"} or evidence.get("secret_scan") not in {
        "passed", "blocked"
    }:
        raise ReviewError("typed validation evidence mismatch")
    mode = declared_mode
    required_mode = mode_hint["required_mode"]
    if mode_hint.get("artifact_already_committed", False) is not carried:
        raise ReviewError("carried artifact hint is inconsistent")
    if carried and (mode != "own_only" or required_mode != "own_only"):
        raise ReviewError("carried artifact must remain in own_only mode")
    if mode_floor not in REVIEW_MODE_RANK:
        raise ReviewError("review input mode floor is invalid")
    effective_required_mode = max(
        (required_mode, mode_floor),
        key=lambda candidate: REVIEW_MODE_RANK.get(candidate, 99),
    )
    if (
        mode not in REVIEW_MODE_RANK
        or required_mode not in REVIEW_MODE_RANK
        or REVIEW_MODE_RANK[mode] < REVIEW_MODE_RANK[effective_required_mode]
    ):
        raise ReviewError("review mode weakens the deterministic mode hint")
    if materialized_dirty is None:
        materialized_dirty = [
            {
                **entry,
                "materialization_status": (
                    "not_required" if entry.get("mode") is None else "available"
                ),
            }
            for entry in state.get("dirty_entries", [])
        ]
    unavailable_dirty = [
        entry for entry in materialized_dirty
        if entry.get("materialization_status") == "deferred"
    ]
    unavailable_commits = [
        entry for entry in materialized_commits
        if entry.get("materialization_status") != "available"
    ]
    if unavailable_commits and mode != "blocked":
        raise ReviewError("unreviewable local-only history requires blocked mode")
    if unavailable_dirty and mode == "sweep":
        raise ReviewError("unreviewable dirty residual cannot use sweep mode")
    if manifest["core_review_status"] == "quality_ok" and manifest[
        "review_or_validation_status"
    ] != "quality_ok":
        raise ReviewError("core review status fields disagree")
    deferred = manifest["deferred_cleanup"]
    deferred_paths = [item["path"] for item in deferred]
    if len(deferred_paths) != len(set(deferred_paths)):
        raise ReviewError("deferred cleanup paths are duplicated")
    grouped = [
        path
        for group in manifest["commit_groups"]
        for path in group["paths"]
    ]
    if len(grouped) != len(set(grouped)):
        raise ReviewError("commit groups contain duplicate paths")
    finalization = manifest["evidence_finalization"]
    expected_finalization = (
        None
        if expected_evidence is None or mode == "blocked"
        else {"target_path": expected_evidence, "template": "daily_publication_v1"}
    )
    if finalization != expected_finalization:
        raise ReviewError("evidence finalization binding mismatch")

    if mode == "sweep":
        expected_owned = set(initial_paths) | set(existing_paths)
        if expected_evidence is not None:
            expected_owned.add(expected_evidence)
        if any(
            path == ".obsidian" or path.startswith(".obsidian/")
            for path in expected_owned
        ):
            raise ReviewError("sweep scope contains a forbidden .obsidian path")
        if sorted(manifest["owned_paths"]) != sorted(expected_owned):
            raise ReviewError("sweep owned paths do not cover the exact scope")
        if (
            manifest["excluded_paths"]
            or manifest["unrelated_dirty_paths"]
            or deferred
            or manifest["residual_review_status"] != "quality_ok"
            or manifest["core_review_status"] != "quality_ok"
            or evidence["file_guard"] != "passed"
            or evidence["secret_scan"] != "passed"
            or manifest["commit_required"] is not True
        ):
            raise ReviewError("sweep review contains a deferred or blocked scope")
        if any(
            entry["mode"] not in {"100644", "100755", None}
            for entry in state["dirty_entries"]
        ) or manifest["approved_dirty_entries"] != state["dirty_entries"]:
            raise ReviewError("sweep dirty blobs/modes do not match pre-state")
        if sorted(grouped) != initial_paths:
            raise ReviewError("sweep groups do not partition dirty paths and artifact")
    elif mode == "own_only":
        expected_owned = {artifact_target}
        if expected_evidence is not None:
            expected_owned.add(expected_evidence)
        if sorted(manifest["owned_paths"]) != sorted(expected_owned):
            raise ReviewError("own_only contains a non-owned path")
        expected_commit_required = not carried
        expected_grouped = [] if carried else [artifact_target]
        if (
            manifest["approved_dirty_entries"]
            or sorted(manifest["excluded_paths"]) != dirty_paths
            or sorted(manifest["unrelated_dirty_paths"]) != dirty_paths
            or sorted(deferred_paths) != dirty_paths
            or manifest["residual_review_status"] != "deferred"
            or manifest["core_review_status"] != "quality_ok"
            or evidence["file_guard"] != "passed"
            or evidence["secret_scan"] != "passed"
            or manifest["commit_required"] is not expected_commit_required
            or grouped != expected_grouped
        ):
            raise ReviewError("own_only manifest does not defer the exact residual scope")
    else:
        expected_owned = {artifact_target}
        if (
            sorted(manifest["owned_paths"]) != sorted(expected_owned)
            or manifest["approved_dirty_entries"]
            or sorted(manifest["excluded_paths"]) != dirty_paths
            or sorted(manifest["unrelated_dirty_paths"]) != dirty_paths
            or sorted(deferred_paths) != dirty_paths
            or manifest["commit_required"] is not False
            or manifest["commit_groups"]
            or manifest["residual_review_status"] == "quality_ok"
        ):
            raise ReviewError(
                "blocked manifest does not preserve the exact non-actionable scope"
            )
        if manifest["core_review_status"] == "blocked" and manifest[
            "review_or_validation_status"
        ] != "blocked":
            raise ReviewError("blocked core status fields disagree")


def validate_root_contract(review: dict[str, Any]) -> None:
    """Bind root outcome and next action to the two per-Vault modes."""
    modes = {
        review["agents_vault"]["publication_mode"],
        review["user_vault"]["publication_mode"],
    }
    if "blocked" in modes:
        if not isinstance(review.get("next_action"), str) or not review[
            "next_action"
        ]:
            raise ReviewError("blocked Vault requires a concrete next action")
    elif review.get("next_action") is not None:
        raise ReviewError("successful per-Vault review has an unexpected next action")
    if review["outcome"] == "approved" and modes == {"blocked"}:
        raise ReviewError("approved review cannot block both Vaults")
    if review["outcome"] == "blocked" and modes != {"blocked"}:
        raise ReviewError("root blocked outcome requires both Vaults blocked")


def main(argv: list[str]) -> int:
    """Validate the approved review and return a fail-closed status."""
    if len(argv) > 1 and argv[1] == "--canonicalize-own-only":
        return canonicalize_own_only_main(argv)
    if len(argv) != 9:
        print(
            "usage: validate-publication-review.py REVIEW CONTEXT PRE_STATE PLAN AUTH_SHA REVIEW_SHA METRICS METRICS_SHA",
            file=sys.stderr,
        )
        return 64
    try:
        review_bytes = read_regular_nofollow(Path(argv[1]))
        if hashlib.sha256(review_bytes).hexdigest() != argv[6]:
            raise ReviewError("publication review file digest mismatch")
        review = json.loads(review_bytes)
        context = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        pre = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        plan = json.loads(Path(argv[4]).read_text(encoding="utf-8"))
        if pre != context["pre_collection_state"] or plan != context["artifact_plan"]:
            raise ReviewError("mutable publication inputs differ from reviewed context")
        if review.get("outcome") not in {"approved", "blocked"}:
            raise ReviewError("publication review outcome is invalid")
        if review["publication_context_sha256"] != sha256(Path(argv[2])):
            raise ReviewError("publication context digest mismatch")
        if context["authorization_task_sha256"] != argv[5]:
            raise ReviewError("authorization evidence digest mismatch")
        if sha256_regular_nofollow(Path(context["authorization_task"])) != argv[5]:
            raise ReviewError("authorization snapshot digest mismatch")
        mode_floor = validate_review_input_contract(
            Path(argv[7]), argv[8], Path(argv[2])
        )
        materialization = validate_dirty_snapshots(context, pre)
        carried_vaults = validate_carried_commit_result(context, pre, plan)
        manifest = context["publication_manifest"]["artifact_manifest"]
        gitleaks_version = context["runtime"]["gitleaks_version"]
        validate_manifest(
            review["agents_vault"],
            pre["agents_vault"],
            context["runtime"]["agents_vault_root"],
            context["authorization_task_id"],
            {
                "role": manifest["advisory"]["role"],
                "source_sha256": manifest["advisory"]["sha256"],
                "target_path": plan["advisory_target"],
            },
            context["standing_task"],
            gitleaks_version,
            context["publication_mode_hint"]["agents_vault"],
            materialization["vaults"]["agents_vault"],
            materialization["local_commits"]["agents_vault"],
            "agents_vault" in carried_vaults,
            mode_floor["agents_vault"],
        )
        validate_manifest(
            review["user_vault"],
            pre["user_vault"],
            context["runtime"]["user_vault_root"],
            context["authorization_task_id"],
            {
                "role": manifest["summary"]["role"],
                "source_sha256": manifest["summary"]["sha256"],
                "target_path": plan["summary_target"],
            },
            None,
            gitleaks_version,
            context["publication_mode_hint"]["user_vault"],
            materialization["vaults"]["user_vault"],
            materialization["local_commits"]["user_vault"],
            "user_vault" in carried_vaults,
            mode_floor["user_vault"],
        )
        validate_root_contract(review)
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ReviewError,
    ) as exc:
        print(f"publication review validation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

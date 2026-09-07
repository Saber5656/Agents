#!/usr/bin/env python3
"""Validate a generated result against the tracked canonical schema subset."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from typing import Any


class CanonicalValidationError(RuntimeError):
    """Raised when a result violates the canonical contract."""


MAX_CANONICAL_FILE_BYTES = 16 * 1024 * 1024
HEX_OBJECT_ID = re.compile(r"^[0-9a-f]{40}$")


SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema", "$defs", "$ref", "type", "additionalProperties", "required",
        "properties", "const", "enum", "minLength", "pattern", "items",
        "minItems", "maxItems", "uniqueItems", "allOf", "anyOf", "oneOf",
        "if", "then", "else",
    }
)


def validate_schema_keywords(value: Any) -> None:
    """Reject canonical keywords this dependency-free validator cannot enforce."""
    if isinstance(value, list):
        for item in value:
            validate_schema_keywords(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key not in SUPPORTED_KEYWORDS:
            raise CanonicalValidationError(f"unsupported canonical keyword: {key}")
        if key in {"properties", "$defs"} and isinstance(item, dict):
            for child_schema in item.values():
                validate_schema_keywords(child_schema)
        elif key not in {"required", "enum"}:
            validate_schema_keywords(item)


def matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise CanonicalValidationError(f"unsupported schema type: {expected}")


def resolves(schema_root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/$defs/"):
        raise CanonicalValidationError(f"unsupported schema reference: {reference}")
    name = reference.removeprefix("#/$defs/")
    try:
        target = schema_root["$defs"][name]
    except (KeyError, TypeError) as exc:
        raise CanonicalValidationError(f"unresolved schema reference: {reference}") from exc
    if not isinstance(target, dict):
        raise CanonicalValidationError(f"schema reference is not an object: {reference}")
    return target


def validate(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if schema is root:
        validate_schema_keywords(root)
    if "$ref" in schema:
        validate(value, resolves(root, schema["$ref"]), root, path)

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(matches_type(value, choice) for choice in choices):
            raise CanonicalValidationError(f"{path}: type mismatch")
    if "const" in schema and value != schema["const"]:
        raise CanonicalValidationError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise CanonicalValidationError(f"{path}: enum mismatch")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise CanonicalValidationError(f"{path}: string is too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise CanonicalValidationError(f"{path}: pattern mismatch")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise CanonicalValidationError(f"{path}: array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise CanonicalValidationError(f"{path}: array has too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise CanonicalValidationError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate(item, item_schema, root, f"{path}[{index}]")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise CanonicalValidationError(f"{path}: missing required properties")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise CanonicalValidationError(f"{path}: additional properties are forbidden")
        for name, child_schema in properties.items():
            if name in value:
                validate(value[name], child_schema, root, f"{path}.{name}")

    for branch in schema.get("allOf", []):
        validate(value, branch, root, path)
    if "anyOf" in schema:
        successes = sum(is_valid(value, branch, root, path) for branch in schema["anyOf"])
        if successes == 0:
            raise CanonicalValidationError(f"{path}: no anyOf branch matched")
    if "oneOf" in schema:
        successes = sum(is_valid(value, branch, root, path) for branch in schema["oneOf"])
        if successes != 1:
            raise CanonicalValidationError(f"{path}: expected exactly one oneOf match")
    if "if" in schema:
        branch = schema.get("then") if is_valid(value, schema["if"], root, path) else schema.get("else")
        if branch is not None:
            validate(value, branch, root, path)


def is_valid(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> bool:
    try:
        validate(value, schema, root, path)
    except CanonicalValidationError:
        return False
    return True


def stable_json_file(path: str) -> Any:
    """Read one regular JSON file through a stable no-follow descriptor snapshot."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise CanonicalValidationError("canonical input is not a regular file")
        if before.st_size > MAX_CANONICAL_FILE_BYTES:
            raise CanonicalValidationError("canonical input exceeds the size limit")
        content = bytearray()
        while chunk := os.read(descriptor, min(1024 * 1024, MAX_CANONICAL_FILE_BYTES + 1)):
            content.extend(chunk)
            if len(content) > MAX_CANONICAL_FILE_BYTES:
                raise CanonicalValidationError("canonical input exceeds the size limit")
        after = os.fstat(descriptor)
        identity = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mode,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if identity(before) != identity(after) or len(content) != before.st_size:
            raise CanonicalValidationError("canonical input changed while it was read")
        return json.loads(bytes(content).decode("utf-8"))
    finally:
        os.close(descriptor)


def valid_object_id(value: Any) -> bool:
    """Return whether a value is one canonical SHA-1 Git object id."""
    return isinstance(value, str) and HEX_OBJECT_ID.fullmatch(value) is not None


def validate_terminal_semantics(result: Any) -> str:
    """Enforce cross-field terminal invariants that JSON Schema cannot express."""
    if not isinstance(result, dict):
        raise CanonicalValidationError("terminal result root must be an object")
    agents = result.get("agents_vault")
    user = result.get("user_vault")
    mode_map = result.get("publication_mode")
    deferred_map = result.get("deferred_cleanup")
    if not all(isinstance(value, dict) for value in (agents, user, mode_map, deferred_map)):
        raise CanonicalValidationError("terminal result maps are malformed")
    vaults = {"agents_vault": agents, "user_vault": user}
    for key, vault in vaults.items():
        hashes = vault.get("commit_hashes")
        if not isinstance(hashes, list) or not all(valid_object_id(item) for item in hashes):
            raise CanonicalValidationError(f"{key}: commit hashes are invalid")
        if vault.get("commit_status") == "complete":
            if not hashes:
                raise CanonicalValidationError(f"{key}: complete commit has no object id")
        elif hashes:
            raise CanonicalValidationError(f"{key}: uncommitted result contains object ids")
        if vault.get("publication_mode") == "blocked" and hashes:
            raise CanonicalValidationError(f"{key}: blocked result contains object ids")
        for field in ("local_head", "remote_head"):
            value = vault.get(field)
            if value is not None and not valid_object_id(value):
                raise CanonicalValidationError(f"{key}: {field} is invalid")
        if mode_map.get(key) != vault.get("publication_mode"):
            raise CanonicalValidationError(f"{key}: publication mode maps disagree")
        if deferred_map.get(key) != vault.get("deferred_cleanup"):
            raise CanonicalValidationError(f"{key}: deferred cleanup maps disagree")

    outcome = result.get("outcome")
    if outcome == "success":
        if (
            result.get("daily_pipeline_status") != "complete"
            or not isinstance(result.get("summary_path"), str)
            or not result["summary_path"]
            or not isinstance(result.get("advisory_path"), str)
            or not result["advisory_path"]
            or not valid_object_id(result.get("evidence_finalization_commit"))
            or result.get("next_action") is not None
        ):
            raise CanonicalValidationError("success terminal fields are inconsistent")
        for key, vault in vaults.items():
            if (
                vault.get("commit_status") != "complete"
                or vault.get("push_status") != "complete"
                or not vault.get("commit_hashes")
                or vault.get("publication_mode") not in {"sweep", "own_only"}
                or not valid_object_id(vault.get("local_head"))
                or vault.get("local_head") != vault.get("remote_head")
                or (vault.get("publication_mode") == "sweep" and vault.get("clean") is not True)
            ):
                raise CanonicalValidationError(f"{key}: success publication is incomplete")
        return "success\t0"
    if outcome == "blocked":
        if (
            not isinstance(result.get("next_action"), str)
            or not result["next_action"]
            or result.get("evidence_finalization_commit") is not None
        ):
            raise CanonicalValidationError("blocked terminal fields are inconsistent")
        for key, vault in vaults.items():
            if (
                vault.get("commit_hashes")
                or vault.get("commit_status") not in {"failed", "not_started", "not_required"}
                or vault.get("push_status") not in {"failed", "not_started", "not_required"}
            ):
                raise CanonicalValidationError(f"{key}: blocked publication made progress")
        return "blocked\t75"
    if outcome == "partial_publication":
        if not isinstance(result.get("next_action"), str) or not result["next_action"]:
            raise CanonicalValidationError("partial publication requires a next action")
        progressed = any(
            bool(vault.get("commit_hashes"))
            or vault.get("push_status") == "complete"
            or vault.get("local_head") != vault.get("remote_head")
            or vault.get("clean") is False
            for vault in vaults.values()
        )
        if not progressed:
            raise CanonicalValidationError("partial publication contains no progress")
        return "partial_publication\t75"
    raise CanonicalValidationError("terminal outcome is invalid")


def main(argv: list[str]) -> int:
    terminal_mode = len(argv) > 1 and argv[1] == "--terminal-status"
    if len(argv) != (4 if terminal_mode else 3):
        print(
            "usage: validate-canonical-result.py [--terminal-status] SCHEMA RESULT",
            file=sys.stderr,
        )
        return 64
    schema_path, result_path = argv[-2:]
    try:
        schema = stable_json_file(schema_path)
        result = stable_json_file(result_path)
        if not isinstance(schema, dict):
            raise CanonicalValidationError("schema root must be an object")
        validate(result, schema, schema)
        if terminal_mode:
            print(validate_terminal_semantics(result))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        CanonicalValidationError,
    ) as exc:
        if terminal_mode:
            print("invalid_result\t65")
            return 0
        print(f"canonical result validation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

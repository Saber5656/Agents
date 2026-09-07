#!/usr/bin/env python3
"""Create a Codex-compatible projection of a fail-closed result schema.

The tracked schema remains the canonical contract used by prompts and deterministic
validators.  Codex receives only the structural subset supported by Structured
Outputs; state-dependent invariants remain enforced after generation by the
phase-specific deterministic validator.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


UNSUPPORTED = frozenset({"allOf", "if", "then", "else", "uniqueItems"})
REJECTED = frozenset({"not", "dependentRequired", "dependentSchemas"})


class SchemaProjectionError(RuntimeError):
    """Raised when a safe compatible projection cannot be produced."""


def reject_unsupported(value: Any) -> None:
    """Scan the complete canonical tree before dropping any composition branch."""
    if isinstance(value, list):
        for item in value:
            reject_unsupported(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key in REJECTED:
            raise SchemaProjectionError(f"unsupported canonical keyword: {key}")
        if key in {"properties", "$defs"} and isinstance(item, dict):
            for child_schema in item.values():
                reject_unsupported(child_schema)
        else:
            reject_unsupported(item)


def project(value: Any) -> Any:
    if isinstance(value, list):
        return [project(item) for item in value]
    if not isinstance(value, dict):
        return value

    projected: dict[str, Any] = {}
    for key, item in value.items():
        if key in REJECTED:
            raise SchemaProjectionError(f"unsupported canonical keyword: {key}")
        if key in UNSUPPORTED:
            continue
        target_key = "anyOf" if key == "oneOf" else key
        if target_key in projected:
            raise SchemaProjectionError(f"schema contains conflicting {target_key}")
        projected[target_key] = project(item)
    if "const" in projected and "type" not in projected:
        constant = projected["const"]
        if constant is None:
            projected["type"] = "null"
        elif isinstance(constant, bool):
            projected["type"] = "boolean"
        elif isinstance(constant, int):
            projected["type"] = "integer"
        elif isinstance(constant, float):
            projected["type"] = "number"
        elif isinstance(constant, str):
            projected["type"] = "string"
        else:
            raise SchemaProjectionError("unsupported const value type")
    return projected


def referenced_defs(schema: dict[str, Any]) -> set[str]:
    defs = schema.get("$defs", {})
    if not isinstance(defs, dict):
        raise SchemaProjectionError("$defs must be an object")

    found: set[str] = set()

    def visit(value: Any, *, include_defs: bool = False) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.removeprefix("#/$defs/")
            if name not in defs:
                raise SchemaProjectionError(f"unresolved local definition: {name}")
            if name not in found:
                found.add(name)
                visit(defs[name], include_defs=True)
        for key, item in value.items():
            if key == "$defs" and not include_defs:
                continue
            visit(item)

    visit(schema)
    return found


def compatible_schema(source: dict[str, Any]) -> dict[str, Any]:
    if source.get("type") != "object" or source.get("additionalProperties") is not False:
        raise SchemaProjectionError("root schema must be a closed object")
    reject_unsupported(source)
    result = project(source)
    defs = result.get("$defs")
    if defs is not None:
        keep = referenced_defs(result)
        result["$defs"] = {name: defs[name] for name in defs if name in keep}
        if not result["$defs"]:
            del result["$defs"]
    encoded = json.dumps(result, sort_keys=True)
    if any(f'"{key}"' in encoded for key in (*UNSUPPORTED, "oneOf")):
        raise SchemaProjectionError("unsupported schema keyword survived projection")
    return result


def write_atomic(destination: Path, schema: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(schema, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: prepare-codex-output-schema.py SOURCE DESTINATION", file=sys.stderr)
        return 64
    source = Path(argv[1])
    destination = Path(argv[2])
    try:
        if source.resolve() == destination.resolve():
            raise SchemaProjectionError("source and destination must differ")
        document = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise SchemaProjectionError("schema document must be an object")
        write_atomic(destination, compatible_schema(document))
    except (OSError, json.JSONDecodeError, SchemaProjectionError) as exc:
        print(f"schema projection failed: {exc}", file=sys.stderr)
        return 65
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

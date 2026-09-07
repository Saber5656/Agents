#!/usr/bin/env python3
"""Build a bounded Codex publication/evidence review request.

The complete digest-bound context remains on disk for deterministic validators.
Only the model-facing envelope is projected here so a large residual Git
snapshot cannot consume the Codex request limit before review starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Optional


# Keep a conservative margin below the Codex turn/start limit (1 MiB).  The
# request is measured both as Unicode characters and UTF-8 bytes because the
# API and the shell can apply different accounting at this boundary.
MAX_REQUEST_CHARS = 900_000
MAX_REQUEST_BYTES = 900_000
MAX_INLINE_ITEMS = 128
MAX_INLINE_ARRAY_CHARS = 48_000
MAX_SAMPLE_ITEMS = 8
MAX_SAMPLE_TEXT_CHARS = 256
MAX_COMMIT_MESSAGE_CHARS = 512
MAX_CONTEXT_BYTES = 32 * 1024 * 1024

RESIDUAL_ARRAY_KEYS = frozenset(
    {
        "dirty_lines",
        "dirty_paths",
        "dirty_entries",
        "dirty_metadata",
        "staged_paths",
        "approved_dirty_entries",
        "excluded_paths",
        "unrelated_dirty_paths",
        "deferred_cleanup",
        "deferred_paths",
        "owned_paths",
        "commit_groups",
        "changed_paths",
    }
)
HISTORY_ARRAY_KEYS = frozenset({"local_commits", "approved_existing_commits"})


class ReviewContextError(RuntimeError):
    """Raised when a safe bounded review request cannot be prepared."""


def read_regular(path: Path, maximum: int = MAX_CONTEXT_BYTES) -> bytes:
    """Read one regular non-symlink file without following replacement paths."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ReviewContextError(f"input file is not a bounded regular file: {path}")
        content = bytearray()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > maximum:
                raise ReviewContextError(f"input file exceeds size limit: {path}")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ReviewContextError(f"input file changed while it was read: {path}")
        return bytes(content)
    finally:
        os.close(descriptor)


def canonical_digest(value: object) -> str:
    """Digest a JSON value without depending on presentation whitespace."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bounded_text(value: str, limit: int = MAX_SAMPLE_TEXT_CHARS) -> object:
    """Clip untrusted review text while retaining an integrity pointer."""
    if len(value) <= limit:
        return value
    half = max(1, (limit - 80) // 2)
    return {
        "truncated": True,
        "chars": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "prefix": value[:half],
        "suffix": value[-half:],
    }


def sample_value(value: object, key: Optional[str] = None) -> object:
    """Create a small, inert sample for a summarized array."""
    if isinstance(value, str):
        limit = MAX_COMMIT_MESSAGE_CHARS if key == "message" else MAX_SAMPLE_TEXT_CHARS
        return bounded_text(value, limit)
    if isinstance(value, list):
        return [sample_value(item) for item in value[:2]]
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for child_key in sorted(value):
            if child_key == "index_entries":
                continue
            result[child_key] = sample_value(value[child_key], child_key)
        return result
    return value


class ProjectionState:
    """Accumulate omitted fields while projecting one review envelope."""

    def __init__(self) -> None:
        self.omitted_fields: list[str] = []
        self.residual_vaults: set[str] = set()
        self.history_vaults: set[str] = set()

    def omit(self, path: str, key: str, vault: Optional[str]) -> None:
        self.omitted_fields.append(path)
        # A commit's changed_paths is part of the local-ahead history identity,
        # not an ordinary dirty residual.  Treating it as residual would allow
        # an omitted ancestor boundary to fall back only to own_only.
        history_identity = key in HISTORY_ARRAY_KEYS or (
            key == "changed_paths"
            and (".local_commits[" in path or ".approved_existing_commits[" in path)
        )
        if (
            key in RESIDUAL_ARRAY_KEYS
            and not history_identity
            and vault in {"agents_vault", "user_vault"}
        ):
            self.residual_vaults.add(vault)
        if history_identity and vault in {"agents_vault", "user_vault"}:
            self.history_vaults.add(vault)

    def mode_floor(self) -> dict[str, str]:
        """Return the strongest publication mode implied by omissions."""
        floors = {"agents_vault": "sweep", "user_vault": "sweep"}
        for vault in self.history_vaults:
            floors[vault] = "blocked"
        for vault in self.residual_vaults:
            if floors[vault] == "sweep":
                floors[vault] = "own_only"
        return floors


def vault_from_path(path: str) -> Optional[str]:
    """Extract a Vault name from a projected JSON path, if present."""
    for candidate in ("agents_vault", "user_vault"):
        if f".{candidate}" in path or path.endswith(f".{candidate}"):
            return candidate
    return None


def summarize_array(
    value: list[object],
    path: str,
    key: str,
    state: ProjectionState,
    force: bool,
    include_samples: bool = True,
) -> object:
    """Keep small arrays exact and replace large arrays with a digest summary."""
    encoded_size = len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    # Empty arrays carry no residual/history evidence and remain exact even
    # when a larger sibling forced the projection into a bounded pass. This
    # avoids turning an empty local-ahead history into a false blocked mode.
    if not value:
        return []
    if not force and len(value) <= MAX_INLINE_ITEMS and encoded_size <= MAX_INLINE_ARRAY_CHARS:
        return [
            project_value(item, f"{path}[{index}]", state, force)
            for index, item in enumerate(value)
        ]
    state.omit(path, key, vault_from_path(path))
    summary: dict[str, object] = {
        "omitted": True,
        "reason": "review_input_budget",
        "count": len(value),
        "sha256": canonical_digest(value),
    }
    if include_samples and value:
        head = value[: MAX_SAMPLE_ITEMS // 2]
        tail = value[-(MAX_SAMPLE_ITEMS - len(head)) :]
        summary["sample"] = [
            sample_value(item, key) for item in [*head, *tail]
        ]
    return summary


def project_value(
    value: object,
    path: str,
    state: ProjectionState,
    force: int,
) -> object:
    """Project known large structures while preserving scalar identity fields."""
    if isinstance(value, dict):
        projected: dict[str, object] = {}
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key == "index_entries":
                state.omit(child_path, key, vault_from_path(child_path))
                continue
            if key == "pre_collection_state" and path.endswith("publication_manifest"):
                projected[key] = {
                    "$ref": "publication_context.pre_collection_state",
                    "sha256": canonical_digest(child),
                }
                continue
            if key == "resumable_state" and path.endswith("carried_commit_result"):
                # The complete carried result remains in the digest-bound file;
                # its resumable bytes are not needed by the model-facing review.
                state.omitted_fields.append(child_path)
                continue
            if isinstance(child, str) and key == "message":
                if len(child) > MAX_COMMIT_MESSAGE_CHARS and (
                    ".local_commits[" in child_path
                    or ".approved_existing_commits[" in child_path
                ):
                    # The review result schema requires a string message.  A
                    # bounded object would be schema-invalid, so mark the
                    # enclosing history as omitted and let the canonicalizer
                    # restore the exact sealed commit identity.
                    state.omit(child_path, "local_commits", vault_from_path(child_path))
                projected[key] = bounded_text(child, MAX_COMMIT_MESSAGE_CHARS)
                continue
            if isinstance(child, list):
                if key in RESIDUAL_ARRAY_KEYS or key in HISTORY_ARRAY_KEYS:
                    projected[key] = summarize_array(
                        child,
                        child_path,
                        key,
                        state,
                        force > 0,
                        include_samples=force == 0,
                    )
                elif force > 1 and len(child) > 0:
                    projected[key] = summarize_array(
                        child,
                        child_path,
                        key,
                        state,
                        True,
                        include_samples=False,
                    )
                else:
                    projected[key] = project_value(child, child_path, state, force)
            else:
                projected[key] = project_value(child, child_path, state, force)
        return projected
    if isinstance(value, list):
        if force > 1 and value:
            return summarize_array(value, path, path.rsplit(".", 1)[-1], state, True, False)
        return [
            project_value(item, f"{path}[{index}]", state, force)
            for index, item in enumerate(value)
        ]
    if isinstance(value, str) and len(value) > MAX_SAMPLE_TEXT_CHARS:
        # Only long free-form strings should be clipped. Paths, URLs, and
        # digests in normal runtime state are bounded by their producers.
        if force > 1 or path.endswith(".message") or path.endswith(".reason"):
            return bounded_text(value)
    return value


def build_projection(envelope: dict[str, object], force: int) -> tuple[dict[str, object], ProjectionState]:
    """Build one deterministic projection and annotate its omission contract."""
    state = ProjectionState()
    projected = project_value(envelope, "", state, force)
    assert isinstance(projected, dict)
    non_index_omissions = [
        field for field in state.omitted_fields if not field.endswith(".index_entries")
    ]
    projection_mode = (
        "bounded_residuals_v1"
        if state.residual_vaults or non_index_omissions
        else "inline_residuals_v1"
    )
    if force > 1:
        projection_mode = "bounded_all_arrays_v1"
    projected["review_input_projection"] = {
        "version": 2,
        "mode": projection_mode,
        "max_request_chars": MAX_REQUEST_CHARS,
        "max_request_bytes": MAX_REQUEST_BYTES,
        "omitted_fields": sorted(set(state.omitted_fields)),
        "residual_review_budget_vaults": sorted(state.residual_vaults),
        "history_review_budget_vaults": sorted(state.history_vaults),
        "mode_floor": state.mode_floor(),
        "residual_review_instruction": (
            "When a residual field is omitted, do not approve sweep from the projection; "
            "use own_only with a concrete deferred reason. If local-ahead history is "
            "omitted, use blocked for that Vault because its ancestor cannot be safely pushed."
        ),
    }
    return projected, state


def render_json(value: object) -> str:
    """Render stable human-readable JSON for the model-facing request."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def make_request(prompt: str, projection: dict[str, object]) -> tuple[str, int, int, int]:
    """Return request text and its character/byte/component sizes."""
    context = render_json(projection)
    request = f"{prompt}\n\nRuntime context JSON:\n{context}"
    return request, len(request), len(request.encode("utf-8")), len(context)


def write_exclusive(path: Path, content: bytes) -> None:
    """Write one private immutable output without following a symlink."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ReviewContextError(f"could not write review output: {path}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare(
    envelope_path: Path,
    prompt_path: Path,
    context_output: Path,
    request_output: Path,
    metrics_output: Path,
) -> None:
    """Prepare bounded context, request, and auditable size metrics."""
    envelope_bytes = read_regular(envelope_path)
    prompt_bytes = read_regular(prompt_path, 512 * 1024)
    try:
        envelope = json.loads(envelope_bytes)
        prompt = prompt_bytes.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewContextError(f"review input is not valid UTF-8/JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise ReviewContextError("review envelope root must be an object")
    context_digest = envelope.get("publication_context_sha256")
    if (
        not isinstance(context_digest, str)
        or len(context_digest) != 64
        or any(character not in "0123456789abcdef" for character in context_digest)
    ):
        raise ReviewContextError("publication context digest is missing or invalid")

    selected: Optional[tuple[dict[str, object], ProjectionState, str, int, int, int]] = None
    for force in (0, 1, 2):
        projection, state = build_projection(envelope, force)
        request, request_chars, request_bytes, context_chars = make_request(prompt, projection)
        if request_chars <= MAX_REQUEST_CHARS and request_bytes <= MAX_REQUEST_BYTES:
            selected = (projection, state, request, request_chars, request_bytes, context_chars)
            break
        selected = (projection, state, request, request_chars, request_bytes, context_chars)
    if selected is None:
        raise ReviewContextError("could not build a review projection")
    projection, state, request, request_chars, request_bytes, context_chars = selected
    if request_chars > MAX_REQUEST_CHARS or request_bytes > MAX_REQUEST_BYTES:
        metrics = {
            "version": 1,
            "status": "input_too_large",
            "publication_context_projection": envelope.get(
                "publication_context_projection"
            ),
            "publication_context_sha256": context_digest,
            "max_request_chars": MAX_REQUEST_CHARS,
            "max_request_bytes": MAX_REQUEST_BYTES,
            "request_chars": request_chars,
            "request_bytes": request_bytes,
            "prompt_chars": len(prompt),
            "projection_chars": context_chars,
            "projection_bytes": len(render_json(projection).encode("utf-8")),
            "source_envelope_sha256": hashlib.sha256(envelope_bytes).hexdigest(),
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "omitted_fields": sorted(set(state.omitted_fields)),
            "residual_review_budget_vaults": sorted(state.residual_vaults),
            "history_review_budget_vaults": sorted(state.history_vaults),
            "mode_floor": state.mode_floor(),
        }
        write_exclusive(
            metrics_output,
            (json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        raise ReviewContextError(
            "publication review input too large after bounded projection: "
            f"actual_chars={request_chars} max_chars={MAX_REQUEST_CHARS} "
            f"actual_bytes={request_bytes} max_bytes={MAX_REQUEST_BYTES}"
        )

    projection_text = render_json(projection)
    metrics = {
        "version": 1,
        "status": "ready",
        "publication_context_projection": envelope.get(
            "publication_context_projection"
        ),
        "publication_context_sha256": context_digest,
        "projection_mode": projection["review_input_projection"]["mode"],
        "max_request_chars": MAX_REQUEST_CHARS,
        "max_request_bytes": MAX_REQUEST_BYTES,
        "request_chars": request_chars,
        "request_bytes": request_bytes,
        "prompt_chars": len(prompt),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "projection_chars": context_chars,
        "projection_bytes": len(projection_text.encode("utf-8")),
        "source_envelope_sha256": hashlib.sha256(envelope_bytes).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "projection_sha256": hashlib.sha256(projection_text.encode("utf-8")).hexdigest(),
        "request_sha256": hashlib.sha256(request.encode("utf-8")).hexdigest(),
        "omitted_fields": sorted(set(state.omitted_fields)),
        "residual_review_budget_vaults": sorted(state.residual_vaults),
        "history_review_budget_vaults": sorted(state.history_vaults),
        "mode_floor": state.mode_floor(),
    }
    write_exclusive(context_output, projection_text.encode("utf-8"))
    write_exclusive(request_output, request.encode("utf-8"))
    write_exclusive(
        metrics_output,
        (json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def main(argv: list[str]) -> int:
    """CLI entrypoint."""
    if len(argv) != 6:
        print(
            "usage: prepare-publication-review-context.py ENVELOPE PROMPT "
            "CONTEXT_OUTPUT REQUEST_OUTPUT METRICS_OUTPUT",
            file=sys.stderr,
        )
        return 64
    try:
        prepare(*(Path(value) for value in argv[1:]))
    except (OSError, ReviewContextError, TypeError, ValueError) as exc:
        print(f"review input preparation failed:{exc}", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

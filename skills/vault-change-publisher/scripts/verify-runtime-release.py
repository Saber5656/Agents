#!/usr/bin/env python3
"""Fail closed when the automation runtime is not the reviewed release."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any


EXIT_USAGE = 66
EXIT_MISMATCH = 78
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_FILES = 256
MAX_PATH_LENGTH = 512
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
ALLOWED_KEYS = {"schema_version", "source_commit", "files"}
FILE_KEYS = {"path", "mode", "sha256"}
REQUIRED_RUNTIME_FILES = frozenset(
    {
        "atomic_file_ops.py",
        "automation-result.schema.json",
        "capture-vault-state.py",
        "collect-public-sources.py",
        "collection-result.schema.json",
        "commit-push-publication-evidence.py",
        "commit-reviewed-publication.py",
        "daily-it-news.collect.prompt.md",
        "daily-it-news.evidence-review.prompt.md",
        "daily-it-news.review.prompt.md",
        "determine-publication-modes.py",
        "evidence-review-result.schema.json",
        "evidence_hunk.py",
        "fetch-vault-main.py",
        "git_diff_digest.py",
        "gitleaks-default.toml",
        "install-verified-artifacts.py",
        "interpret-automation-result.sh",
        "isolated_git_transport.py",
        "it-news-sources.json",
        "prepare-codex-output-schema.py",
        "prepare-publication-evidence.py",
        "prepare-publication-review-context.py",
        "publication-commit-result.schema.json",
        "publication-review-result.schema.json",
        "push-committed-heads.py",
        "resolve-runtime-context.py",
        "run-daily-it-news-vulnerability-check.sh",
        "run-pinned-review.py",
        "send-it-news-discord-notification.py",
        "stage-dirty-review-inputs.py",
        "stage-standing-task.py",
        "trusted_gitleaks.py",
        "validate-canonical-result.py",
        "validate-collection-result.py",
        "validate-publication-review.py",
        "verify-runtime-release.py",
    }
)


class ReleaseVerificationError(ValueError):
    """A malformed manifest or a runtime mismatch."""


def fail(message: str, *, code: int = EXIT_MISMATCH) -> int:
    """Write a short, non-secret diagnostic and return a shell status."""
    print(f"runtime release verification failed: {message}", file=sys.stderr)
    return code


def load_json(manifest_path: Path) -> dict[str, Any]:
    """Load one regular, bounded JSON manifest with duplicate-key rejection."""
    try:
        metadata = manifest_path.lstat()
    except OSError as exc:
        raise ReleaseVerificationError("manifest_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseVerificationError("manifest_not_regular")
    if metadata.st_size > MAX_MANIFEST_BYTES:
        raise ReleaseVerificationError("manifest_too_large")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseVerificationError("manifest_duplicate_key")
            result[key] = value
        return result

    try:
        return json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseVerificationError("manifest_invalid_json") from exc


def validated_relative_path(value: Any) -> str:
    """Accept only a normalized, relative POSIX path."""
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_LENGTH:
        raise ReleaseVerificationError("manifest_invalid_path")
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise ReleaseVerificationError("manifest_invalid_path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseVerificationError("manifest_invalid_path")
    normalized = path.as_posix()
    if normalized != value:
        raise ReleaseVerificationError("manifest_noncanonical_path")
    return normalized


def validated_manifest(manifest: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Validate the manifest shape and return its source identity and entries."""
    if not isinstance(manifest, dict) or set(manifest) != ALLOWED_KEYS:
        raise ReleaseVerificationError("manifest_schema_mismatch")
    if manifest.get("schema_version") != 1:
        raise ReleaseVerificationError("manifest_schema_version_unsupported")
    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str) or not COMMIT_PATTERN.fullmatch(source_commit):
        raise ReleaseVerificationError("manifest_invalid_source_commit")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ReleaseVerificationError("manifest_invalid_files")

    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != FILE_KEYS:
            raise ReleaseVerificationError("manifest_file_schema_mismatch")
        path = validated_relative_path(entry.get("path"))
        if path in seen:
            raise ReleaseVerificationError("manifest_duplicate_path")
        seen.add(path)
        mode = entry.get("mode")
        if mode not in {"100644", "100755"}:
            raise ReleaseVerificationError("manifest_invalid_mode")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise ReleaseVerificationError("manifest_invalid_sha256")
        entries.append({"path": path, "mode": mode, "sha256": digest})
    if seen != REQUIRED_RUNTIME_FILES:
        raise ReleaseVerificationError("manifest_runtime_file_set_mismatch")
    return source_commit, entries


def digest_file(path: Path) -> str:
    """Hash a regular, non-symlink file without following runtime links."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseVerificationError("runtime_file_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseVerificationError("runtime_file_not_regular")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, UnicodeError) as exc:
        raise ReleaseVerificationError("runtime_file_unreadable") from exc
    return digest.hexdigest()


def verify(manifest_path: Path, workdir: Path) -> tuple[str, int]:
    """Verify each manifest entry against a bounded runtime directory."""
    if not workdir.is_dir() or workdir.is_symlink():
        raise ReleaseVerificationError("runtime_root_unavailable")
    source_commit, entries = validated_manifest(load_json(manifest_path))
    workdir_resolved = workdir.resolve()
    for entry in entries:
        relative = PurePosixPath(entry["path"])
        target = workdir / Path(*relative.parts)
        try:
            target_resolved = target.resolve(strict=True)
        except OSError as exc:
            raise ReleaseVerificationError("runtime_file_unavailable") from exc
        if target_resolved != workdir_resolved / Path(*relative.parts):
            raise ReleaseVerificationError("runtime_file_symlink_escape")
        try:
            mode = stat.S_IMODE(target.lstat().st_mode)
        except OSError as exc:
            raise ReleaseVerificationError("runtime_file_unavailable") from exc
        expected_mode = int(entry["mode"][2:], 8)
        if mode != expected_mode:
            raise ReleaseVerificationError("runtime_file_mode_mismatch")
        if digest_file(target) != entry["sha256"]:
            raise ReleaseVerificationError("runtime_file_digest_mismatch")
    return source_commit, len(entries)


def main(argv: list[str]) -> int:
    """CLI entry point: verifier MANIFEST WORKDIR."""
    if len(argv) != 3:
        return fail("usage", code=EXIT_USAGE)
    try:
        source_commit, count = verify(Path(argv[1]), Path(argv[2]))
    except ReleaseVerificationError as exc:
        return fail(str(exc))
    print(f"runtime release verified: source_commit={source_commit};files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

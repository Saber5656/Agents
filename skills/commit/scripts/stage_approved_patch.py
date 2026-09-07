#!/usr/bin/env python3
"""Stage an approved git patch non-interactively.

This helper is intentionally small: it validates patch paths against an
approved scope, then applies the patch to the index only. It exists to avoid
interactive `git add -p` sessions and Task-Index/Kanban line-delete hacks.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


def normalize_repo_path(value: str) -> str:
    path = value.strip().strip('"')
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    path = posixpath.normpath(path)
    if path in {"", "."} or path.startswith("../") or path == ".." or path.startswith("/"):
        raise ValueError(f"unsafe patch path: {value}")
    return path


def path_matches_scope(path: str, scope: str) -> bool:
    scope = normalize_repo_path(scope)
    return path == scope or path.startswith(scope.rstrip("/") + "/")


def diff_git_paths(line: str) -> list[str]:
    raw = line.removeprefix("diff --git ").strip()
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    if len(parts) == 2:
        return [normalize_repo_path(part) for part in parts]

    if raw.startswith("a/"):
        marker_index = raw.rfind(" b/")
        if marker_index != -1:
            return [
                normalize_repo_path(raw[:marker_index]),
                normalize_repo_path(raw[marker_index + 1 :]),
            ]

    return [
        normalize_repo_path(part)
        for part in raw.split()
        if part.startswith(("a/", "b/"))
    ]


def patch_paths(patch_text: str) -> list[str]:
    paths: set[str] = set()
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            paths.update(diff_git_paths(line))
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            raw = line[4:].split("\t", 1)[0].strip()
            if raw == "/dev/null":
                continue
            paths.add(normalize_repo_path(raw))
    return sorted(paths)


def validate_patch_paths(paths: list[str], owned_paths: list[str], excluded_paths: list[str]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if owned_paths and not any(path_matches_scope(path, owned) for owned in owned_paths):
            errors.append(f"patch path outside approved owned_paths: {path}")
        if any(path_matches_scope(path, excluded) for excluded in excluded_paths):
            errors.append(f"patch path is explicitly excluded: {path}")
    return errors


def run_git(repo: Path, args: list[str], *, patch_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args, str(patch_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage an approved patch into the git index.")
    parser.add_argument("--repo", required=True, help="Git repository root")
    parser.add_argument("--patch", required=True, help="Patch file to apply to the index")
    parser.add_argument("--owned-path", action="append", default=[], help="Approved path scope; repeatable")
    parser.add_argument("--excluded-path", action="append", default=[], help="Forbidden path scope; repeatable")
    parser.add_argument("--check", action="store_true", help="Validate only; do not stage")
    parser.add_argument("--unidiff-zero", action="store_true", help="Allow zero-context hunks")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    patch_path = Path(args.patch).expanduser().resolve()
    try:
        patch_text = patch_path.read_text(encoding="utf-8")
        paths = patch_paths(patch_text)
        if not paths:
            raise ValueError("patch contains no file paths")
        path_errors = validate_patch_paths(paths, args.owned_path, args.excluded_path)
        if path_errors:
            emit({"result": "blocked", "reason": "path_scope_mismatch", "errors": path_errors, "paths": paths})
            return 2
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        emit({"result": "blocked", "reason": str(exc)})
        return 2

    git_args = ["apply", "--cached", "--check"]
    if args.unidiff_zero:
        git_args.append("--unidiff-zero")
    check = run_git(repo, git_args, patch_path=patch_path)
    if check.returncode != 0:
        emit(
            {
                "result": "blocked",
                "reason": "git_apply_check_failed",
                "paths": paths,
                "stderr": check.stderr.strip(),
            }
        )
        return 1

    if args.check:
        emit({"result": "check_ok", "paths": paths})
        return 0

    git_args = ["apply", "--cached"]
    if args.unidiff_zero:
        git_args.append("--unidiff-zero")
    apply = run_git(repo, git_args, patch_path=patch_path)
    if apply.returncode != 0:
        emit(
            {
                "result": "blocked",
                "reason": "git_apply_cached_failed",
                "paths": paths,
                "stderr": apply.stderr.strip(),
            }
        )
        return 1

    emit({"result": "staged", "paths": paths})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

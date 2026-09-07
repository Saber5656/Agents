#!/usr/bin/env python3
"""Fetch and safely merge managed local Git repositories."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


REFERENCES_DIR = Path(__file__).resolve().parents[1] / "references"
DEFAULT_REPO_TABLE = REFERENCES_DIR / "managed-repositories.md"
LOCAL_REPO_TABLE = REFERENCES_DIR / "managed-repositories.local.md"


@dataclass
class ManagedRepo:
    name: str
    path: Path
    repo_kind: str
    remote: str
    default_branch: str
    include: bool
    management_source: str
    notes: str


@dataclass
class RepoResult:
    repo: str
    path: str
    branch: str = ""
    upstream: str = ""
    dirty: bool = False
    ahead: int | None = None
    behind: int | None = None
    fetch_status: str = "skipped"
    merge_status: str = "skipped"
    reason: str = ""
    before_head: str = ""
    after_head: str = ""


class GitError(RuntimeError):
    def __init__(self, args: list[str], cwd: Path, output: str):
        super().__init__(output.strip() or "git command failed")
        self.args_list = args
        self.cwd = cwd
        self.output = output


def run_git(repo: Path, args: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(args, repo, proc.stdout)
    return proc.stdout.strip()


def split_markdown_row(line: str) -> list[str]:
    return [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1"}


def parse_managed_repositories(path: Path) -> list[ManagedRepo]:
    text = path.read_text(encoding="utf-8")
    rows: list[ManagedRepo] = []
    headers: list[str] | None = None
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = split_markdown_row(line)
        if not cells or set(cells[0]) <= {"-"}:
            continue
        if cells[0] == "name":
            headers = cells
            continue
        if headers is None or len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells))
        rows.append(
            ManagedRepo(
                name=row["name"],
                path=Path(row["path"]).expanduser(),
                repo_kind=row["repo_kind"],
                remote=row["remote"],
                default_branch=row["default_branch"],
                include=parse_bool(row["include"]),
                management_source=row["management_source"],
                notes=row["notes"],
            )
        )
    return [repo for repo in rows if repo.include]


def default_repo_table() -> Path:
    return LOCAL_REPO_TABLE if LOCAL_REPO_TABLE.exists() else DEFAULT_REPO_TABLE


def is_git_repo(path: Path) -> bool:
    try:
        run_git(path, ["rev-parse", "--git-dir"])
        return True
    except GitError:
        return False


def current_branch(repo: Path) -> str:
    return run_git(repo, ["branch", "--show-current"])


def current_head(repo: Path) -> str:
    return run_git(repo, ["rev-parse", "--short", "HEAD"])


def upstream_ref(repo: Path) -> str:
    return run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])


def remote_ref_exists(repo: Path, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def resolve_upstream(repo: ManagedRepo) -> str:
    try:
        return upstream_ref(repo.path)
    except GitError:
        fallback = f"{repo.remote}/{repo.default_branch}"
        if remote_ref_exists(repo.path, fallback):
            return fallback
        return ""


def dirty_state(repo: Path) -> bool:
    status = run_git(repo, ["status", "--porcelain"])
    return bool(status)


def ahead_behind(repo: Path, upstream: str) -> tuple[int, int]:
    counts = run_git(repo, ["rev-list", "--left-right", "--count", f"HEAD...{upstream}"])
    left, right = counts.split()
    return int(left), int(right)


def has_unmerged_paths(repo: Path) -> bool:
    output = run_git(repo, ["diff", "--name-only", "--diff-filter=U"])
    return bool(output)


def fetch(repo: Path) -> None:
    run_git(repo, ["fetch", "--all", "--prune"])


def merge_upstream(repo: Path, upstream: str) -> None:
    try:
        run_git(repo, ["merge", "--no-edit", upstream])
    except GitError as exc:
        # Leave shorthand pull safe: do not keep conflict state behind.
        run_git(repo, ["merge", "--abort"], check=False)
        raise exc


def inspect_repo(repo: ManagedRepo) -> RepoResult:
    result = RepoResult(repo=repo.name, path=str(repo.path))
    if not repo.path.exists():
        result.reason = "path_missing"
        result.fetch_status = "skipped"
        result.merge_status = "blocked"
        return result
    if not is_git_repo(repo.path):
        result.reason = "not_git_repo"
        result.fetch_status = "skipped"
        result.merge_status = "blocked"
        return result
    result.branch = current_branch(repo.path)
    result.before_head = current_head(repo.path)
    result.dirty = dirty_state(repo.path)
    if has_unmerged_paths(repo.path):
        result.reason = "unmerged_paths"
        result.fetch_status = "skipped"
        result.merge_status = "blocked"
        result.after_head = result.before_head
        return result
    try:
        result.upstream = resolve_upstream(repo)
    except GitError:
        result.reason = "upstream_missing"
        result.fetch_status = "skipped"
        result.merge_status = "blocked"
    return result


def process_repo(repo: ManagedRepo, *, execute: bool) -> RepoResult:
    result = inspect_repo(repo)
    if result.merge_status == "blocked" and result.reason in {"path_missing", "not_git_repo", "unmerged_paths"}:
        return result

    if execute:
        try:
            fetch(repo.path)
            result.fetch_status = "success"
        except GitError as exc:
            result.fetch_status = "failed"
            result.merge_status = "blocked"
            result.reason = f"fetch_failed: {exc}"
            result.after_head = current_head(repo.path)
            return result
    else:
        result.fetch_status = "dry_run"

    if not result.upstream:
        result.upstream = resolve_upstream(repo)
    if not result.upstream:
        result.merge_status = "blocked"
        result.reason = "upstream_missing"
        result.after_head = result.before_head
        return result

    try:
        result.ahead, result.behind = ahead_behind(repo.path, result.upstream)
    except GitError as exc:
        result.merge_status = "blocked"
        result.reason = f"ahead_behind_failed: {exc}"
        result.after_head = current_head(repo.path)
        return result

    if result.behind == 0:
        result.merge_status = "not_needed"
        result.after_head = current_head(repo.path)
        return result

    if result.dirty:
        result.merge_status = "blocked"
        result.reason = "dirty_worktree"
        result.after_head = current_head(repo.path)
        return result

    if not execute:
        result.merge_status = "dry_run"
        result.reason = "remote_updates_available"
        result.after_head = result.before_head
        return result

    try:
        merge_upstream(repo.path, result.upstream)
        result.merge_status = "merged"
        result.after_head = current_head(repo.path)
    except GitError as exc:
        result.merge_status = "blocked"
        result.reason = f"merge_conflict_or_failure: {exc}"
        result.after_head = current_head(repo.path)

    return result


def render_table(results: Iterable[RepoResult]) -> str:
    lines = [
        "| Repo | Path | Branch | Upstream | Dirty | Ahead | Behind | Fetch | Merge | Reason |",
        "|---|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for result in results:
        lines.append(
            "| {repo} | `{path}` | `{branch}` | `{upstream}` | {dirty} | {ahead} | {behind} | {fetch} | {merge} | {reason} |".format(
                repo=result.repo,
                path=result.path,
                branch=result.branch,
                upstream=result.upstream,
                dirty=str(result.dirty).lower(),
                ahead="" if result.ahead is None else result.ahead,
                behind="" if result.behind is None else result.behind,
                fetch=result.fetch_status,
                merge=result.merge_status,
                reason=result.reason,
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Inspect and report without fetch or merge.")
    mode.add_argument("--execute", action="store_true", help="Fetch and merge according to safe policy.")
    parser.add_argument("--repo-table", type=Path, default=default_repo_table())
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown table.")
    args = parser.parse_args(argv)

    repos = parse_managed_repositories(args.repo_table)
    results = [process_repo(repo, execute=args.execute) for repo in repos]

    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        print(render_table(results))

    failures = [result for result in results if result.fetch_status == "failed" or result.merge_status == "failed"]
    blockers = [result for result in results if result.merge_status == "blocked"]
    if failures:
        return 1
    if blockers:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

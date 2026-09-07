#!/usr/bin/env python3
"""Resolve dirty/diverged managed repos by commit-first (or stash) then merge.

This is the companion to the `pull` skill. `pull` deliberately blocks a repo
whose worktree is dirty while remote updates exist (reason ``dirty_worktree``).
`merge` picks those up: it first preserves the local work (commit-first by
default, or ``--stash``), then merges the upstream with ``--no-edit``. On a
merge conflict it aborts and reports, never auto-resolving.

Boundaries identical to `pull`: no push, no force, no ``reset --hard``, no
delete/clean, no conflict auto-resolution.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable


REFERENCES_DIR = Path(__file__).resolve().parents[1] / "references"
DEFAULT_REPO_TABLE = REFERENCES_DIR / "managed-repositories.md"
LOCAL_REPO_TABLE = REFERENCES_DIR / "managed-repositories.local.md"
DEFAULT_COMMIT_MESSAGE = "chore(merge): save local changes before merge"


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
    local_change: str = "none"
    commit_hash: str = ""
    conflict_files: list[str] = field(default_factory=list)
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


def conflict_paths(repo: Path) -> list[str]:
    output = run_git(repo, ["diff", "--name-only", "--diff-filter=U"], check=False)
    return [line for line in output.splitlines() if line]


def fetch(repo: Path) -> None:
    run_git(repo, ["fetch", "--all", "--prune"])


def commit_all(repo: Path, message: str) -> str:
    run_git(repo, ["add", "-A"])
    run_git(repo, ["commit", "--no-verify", "-m", message])
    return current_head(repo)


def stash_all(repo: Path, message: str) -> None:
    run_git(repo, ["stash", "push", "--include-untracked", "-m", message])


class MergeConflict(RuntimeError):
    def __init__(self, files: list[str], output: str):
        super().__init__(output.strip() or "merge conflict")
        self.files = files
        self.output = output


def merge_upstream(repo: Path, upstream: str) -> None:
    """Merge upstream; on conflict capture files, abort, and raise MergeConflict."""
    try:
        run_git(repo, ["merge", "--no-edit", upstream])
    except GitError as exc:
        files = conflict_paths(repo)
        run_git(repo, ["merge", "--abort"], check=False)
        raise MergeConflict(files, exc.output)


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


def process_repo(
    repo: ManagedRepo,
    *,
    execute: bool,
    use_stash: bool = False,
    commit_message: str = DEFAULT_COMMIT_MESSAGE,
) -> RepoResult:
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

    if not execute:
        result.merge_status = "dry_run"
        if result.dirty:
            result.local_change = "stash" if use_stash else "commit"
            result.reason = "remote_updates_available_dirty"
        else:
            result.reason = "remote_updates_available"
        result.after_head = result.before_head
        return result

    # Preserve local work before merging a dirty worktree.
    if result.dirty:
        try:
            if use_stash:
                stash_all(repo.path, commit_message)
                result.local_change = "stashed"
            else:
                result.commit_hash = commit_all(repo.path, commit_message)
                result.local_change = "committed"
        except GitError as exc:
            result.merge_status = "blocked"
            result.reason = f"preserve_failed: {exc}"
            result.after_head = current_head(repo.path)
            return result

    try:
        merge_upstream(repo.path, result.upstream)
        result.merge_status = "merged"
        result.after_head = current_head(repo.path)
    except MergeConflict as exc:
        result.merge_status = "conflict_aborted"
        result.conflict_files = exc.files
        result.reason = "merge_conflict"
        result.after_head = current_head(repo.path)
        return result

    # Restore stashed work after a clean merge (commit-first needs nothing here).
    if result.local_change == "stashed":
        try:
            run_git(repo.path, ["stash", "pop"])
            result.local_change = "stash_restored"
        except GitError:
            result.local_change = "stash_pop_conflict"
            result.reason = "stash_pop_conflict"
            result.conflict_files = conflict_paths(repo.path)

    return result


def render_table(results: Iterable[RepoResult]) -> str:
    lines = [
        "| Repo | Branch | Upstream | Dirty | Ahead | Behind | Fetch | Local | Merge | Reason |",
        "|---|---|---|---:|---:|---:|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            "| {repo} | `{branch}` | `{upstream}` | {dirty} | {ahead} | {behind} | {fetch} | {local} | {merge} | {reason} |".format(
                repo=result.repo,
                branch=result.branch,
                upstream=result.upstream,
                dirty=str(result.dirty).lower(),
                ahead="" if result.ahead is None else result.ahead,
                behind="" if result.behind is None else result.behind,
                fetch=result.fetch_status,
                local=result.local_change,
                merge=result.merge_status,
                reason=result.reason,
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Inspect and report without preserving or merging.")
    mode.add_argument("--execute", action="store_true", help="Commit-first (or stash) then merge per safe policy.")
    parser.add_argument("--stash", action="store_true", help="Stash local changes instead of committing them.")
    parser.add_argument("--message", default=DEFAULT_COMMIT_MESSAGE, help="Commit/stash message for preserved local work.")
    parser.add_argument("--repo-table", type=Path, default=default_repo_table())
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown table.")
    args = parser.parse_args(argv)

    repos = parse_managed_repositories(args.repo_table)
    results = [
        process_repo(repo, execute=args.execute, use_stash=args.stash, commit_message=args.message)
        for repo in repos
    ]

    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        print(render_table(results))

    failures = [r for r in results if r.fetch_status == "failed" or r.merge_status == "failed"]
    conflicts = [r for r in results if r.merge_status == "conflict_aborted" or r.local_change == "stash_pop_conflict"]
    blockers = [r for r in results if r.merge_status == "blocked"]
    if failures:
        return 1
    if conflicts:
        return 3
    if blockers:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

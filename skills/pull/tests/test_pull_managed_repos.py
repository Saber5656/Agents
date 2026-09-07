from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from skills.pull.scripts.pull_managed_repos import parse_managed_repositories, process_repo


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class PullManagedReposTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def init_repo_pair(self) -> tuple[Path, Path]:
        remote = self.root / "remote.git"
        subprocess.check_call(["git", "init", "--bare", str(remote)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        seed = self.root / "seed"
        subprocess.check_call(["git", "clone", str(remote), str(seed)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git(seed, "config", "user.email", "test@example.com")
        git(seed, "config", "user.name", "Test User")
        git(seed, "switch", "-c", "main")
        write(seed / "README.md", "initial\n")
        git(seed, "add", "README.md")
        git(seed, "commit", "-m", "initial")
        git(seed, "push", "origin", "main")
        git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

        local = self.root / "local"
        subprocess.check_call(["git", "clone", str(remote), str(local)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git(local, "config", "user.email", "test@example.com")
        git(local, "config", "user.name", "Test User")
        return remote, local

    def make_repo(self, path: Path) -> str:
        return (
            "| name | path | repo_kind | remote | default_branch | include | management_source | notes |\n"
            "|---|---|---|---|---|---|---|---|\n"
            f"| local | `{path}` | source | origin | main | true | test | temp repo |\n"
        )

    def commit_remote_update(self, remote: Path, filename: str = "remote.txt", text: str = "remote\n") -> None:
        work = self.root / f"remote-work-{filename}"
        subprocess.check_call(["git", "clone", str(remote), str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git(work, "config", "user.email", "test@example.com")
        git(work, "config", "user.name", "Test User")
        write(work / filename, text)
        git(work, "add", filename)
        git(work, "commit", "-m", f"add {filename}")
        git(work, "push", "origin", "main")

    def test_parse_managed_repositories(self) -> None:
        _, local = self.init_repo_pair()
        table = self.root / "repos.md"
        write(table, self.make_repo(local))
        repos = parse_managed_repositories(table)
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0].name, "local")
        self.assertEqual(repos[0].path, local)

    def test_dry_run_does_not_fetch_or_merge(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote)
        repo = parse_managed_repositories_from_text(self.make_repo(local))[0]
        result = process_repo(repo, execute=False)
        self.assertEqual(result.fetch_status, "dry_run")
        self.assertEqual(result.merge_status, "not_needed")
        self.assertFalse((local / "remote.txt").exists())

    def test_execute_fast_forward_clean_repo(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote)
        repo = parse_managed_repositories_from_text(self.make_repo(local))[0]
        result = process_repo(repo, execute=True)
        self.assertEqual(result.fetch_status, "success")
        self.assertEqual(result.merge_status, "merged")
        self.assertTrue((local / "remote.txt").exists())

    def test_dirty_repo_with_remote_update_blocks_merge(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote)
        write(local / "local.txt", "dirty\n")
        repo = parse_managed_repositories_from_text(self.make_repo(local))[0]
        result = process_repo(repo, execute=True)
        self.assertEqual(result.fetch_status, "success")
        self.assertEqual(result.merge_status, "blocked")
        self.assertEqual(result.reason, "dirty_worktree")
        self.assertFalse((local / "remote.txt").exists())

    def test_diverged_clean_repo_merges_without_push(self) -> None:
        remote, local = self.init_repo_pair()
        write(local / "local.txt", "local\n")
        git(local, "add", "local.txt")
        git(local, "commit", "-m", "add local")
        self.commit_remote_update(remote)
        repo = parse_managed_repositories_from_text(self.make_repo(local))[0]
        result = process_repo(repo, execute=True)
        self.assertEqual(result.merge_status, "merged")
        self.assertTrue((local / "remote.txt").exists())
        self.assertTrue((local / "local.txt").exists())

    def test_branch_without_upstream_falls_back_to_origin_main(self) -> None:
        remote, local = self.init_repo_pair()
        git(local, "switch", "-c", "codex-test-branch")
        self.commit_remote_update(remote)
        repo = parse_managed_repositories_from_text(self.make_repo(local))[0]
        result = process_repo(repo, execute=True)
        self.assertEqual(result.upstream, "origin/main")
        self.assertEqual(result.merge_status, "merged")
        self.assertTrue((local / "remote.txt").exists())


def parse_managed_repositories_from_text(text: str):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "repos.md"
        write(path, text)
        return parse_managed_repositories(path)


if __name__ == "__main__":
    unittest.main()

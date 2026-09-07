from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from skills.merge.scripts.merge_managed_repos import parse_managed_repositories, process_repo


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class MergeManagedReposTest(unittest.TestCase):
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

    def repo_from(self, local: Path):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repos.md"
            write(path, self.make_repo(local))
            return parse_managed_repositories(path)[0]

    def test_clean_behind_repo_merges(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote)
        result = process_repo(self.repo_from(local), execute=True)
        self.assertEqual(result.merge_status, "merged")
        self.assertEqual(result.local_change, "none")
        self.assertTrue((local / "remote.txt").exists())

    def test_behind_zero_is_not_needed(self) -> None:
        _, local = self.init_repo_pair()
        result = process_repo(self.repo_from(local), execute=True)
        self.assertEqual(result.merge_status, "not_needed")

    def test_dirty_repo_commit_first_then_merge(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote)
        write(local / "local.txt", "dirty work\n")
        result = process_repo(self.repo_from(local), execute=True)
        self.assertEqual(result.local_change, "committed")
        self.assertTrue(result.commit_hash)
        self.assertEqual(result.merge_status, "merged")
        self.assertTrue((local / "remote.txt").exists())
        self.assertTrue((local / "local.txt").exists())
        # Local work is preserved as a real commit, worktree is clean after merge.
        self.assertEqual(git(local, "status", "--porcelain"), "")

    def test_dirty_repo_stash_then_merge_and_restore(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote)
        write(local / "local.txt", "dirty work\n")
        result = process_repo(self.repo_from(local), execute=True, use_stash=True)
        self.assertEqual(result.local_change, "stash_restored")
        self.assertEqual(result.merge_status, "merged")
        self.assertTrue((local / "remote.txt").exists())
        self.assertTrue((local / "local.txt").exists())

    def test_merge_conflict_aborts_and_reports(self) -> None:
        remote, local = self.init_repo_pair()
        # Remote and local both change README.md -> conflicting merge.
        self.commit_remote_update(remote, filename="README.md", text="remote side\n")
        write(local / "README.md", "local side\n")
        result = process_repo(self.repo_from(local), execute=True)
        self.assertEqual(result.local_change, "committed")
        self.assertEqual(result.merge_status, "conflict_aborted")
        self.assertEqual(result.reason, "merge_conflict")
        self.assertIn("README.md", result.conflict_files)
        # Aborted: no in-progress merge, no unmerged paths left behind.
        self.assertEqual(git(local, "diff", "--name-only", "--diff-filter=U"), "")

    def test_dry_run_does_not_commit_or_merge(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote)
        write(local / "local.txt", "dirty\n")
        result = process_repo(self.repo_from(local), execute=False)
        self.assertEqual(result.fetch_status, "dry_run")
        # Dry run does not fetch, so the un-fetched remote update is invisible
        # (behind 0 against the stale upstream ref); this mirrors the pull skill.
        self.assertIn(result.merge_status, {"dry_run", "not_needed"})
        self.assertEqual(result.local_change, "none")
        self.assertFalse((local / "remote.txt").exists())
        # Still dirty, nothing committed.
        self.assertNotEqual(git(local, "status", "--porcelain"), "")

    def test_unmerged_paths_block_before_fetch(self) -> None:
        remote, local = self.init_repo_pair()
        self.commit_remote_update(remote, filename="README.md", text="remote side\n")
        write(local / "README.md", "local side\n")
        git(local, "add", "README.md")
        git(local, "commit", "-m", "local readme")
        git(local, "fetch", "origin")
        # Force an in-progress conflicted merge state.
        subprocess.run(["git", "-C", str(local), "merge", "origin/main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = process_repo(self.repo_from(local), execute=True)
        self.assertEqual(result.merge_status, "blocked")
        self.assertEqual(result.reason, "unmerged_paths")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stage_approved_patch.py"


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


class StageApprovedPatchTest(unittest.TestCase):
    def init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        self.assertEqual(run(["git", "init"], cwd=repo).returncode, 0)
        self.assertEqual(run(["git", "config", "user.email", "test@example.com"], cwd=repo).returncode, 0)
        self.assertEqual(run(["git", "config", "user.name", "Test User"], cwd=repo).returncode, 0)
        lines = [f"line {index}\n" for index in range(1, 25)]
        (repo / "notes.md").write_text("".join(lines), encoding="utf-8")
        self.assertEqual(run(["git", "add", "notes.md"], cwd=repo).returncode, 0)
        self.assertEqual(run(["git", "commit", "-m", "init"], cwd=repo).returncode, 0)
        return repo

    def second_hunk_patch(self, diff_text: str) -> str:
        lines = diff_text.splitlines()
        header: list[str] = []
        hunks: list[list[str]] = []
        current: list[str] | None = None
        for line in lines:
            if line.startswith("@@ "):
                current = [line]
                hunks.append(current)
            elif current is None:
                header.append(line)
            else:
                current.append(line)
        self.assertGreaterEqual(len(hunks), 2)
        return "\n".join([*header, *hunks[1], ""]) + "\n"

    def test_stages_only_approved_hunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.init_repo(Path(tmp))
            path = repo / "notes.md"
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[1] = "line 2 unrelated"
            lines[20] = "line 21 approved"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            diff = run(["git", "diff", "--unified=0", "--", "notes.md"], cwd=repo)
            patch = repo / "approved.patch"
            patch.write_text(self.second_hunk_patch(diff.stdout), encoding="utf-8")

            completed = run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo",
                    str(repo),
                    "--patch",
                    str(patch),
                    "--owned-path",
                    "notes.md",
                    "--unidiff-zero",
                ]
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(json.loads(completed.stdout)["result"], "staged")
            staged = run(["git", "diff", "--cached", "--unified=0"], cwd=repo).stdout
            unstaged = run(["git", "diff", "--unified=0"], cwd=repo).stdout
            self.assertIn("line 21 approved", staged)
            self.assertNotIn("line 2 unrelated", staged)
            self.assertIn("line 2 unrelated", unstaged)
            self.assertNotIn("line 21 approved", unstaged)

    def test_blocks_patch_outside_owned_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.init_repo(Path(tmp))
            (repo / "other.md").write_text("owned elsewhere\n", encoding="utf-8")
            patch = repo / "outside.patch"
            patch.write_text(
                "diff --git a/other.md b/other.md\n"
                "new file mode 100644\n"
                "index 0000000..1111111\n"
                "--- /dev/null\n"
                "+++ b/other.md\n"
                "@@ -0,0 +1 @@\n"
                "+owned elsewhere\n",
                encoding="utf-8",
            )

            completed = run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo",
                    str(repo),
                    "--patch",
                    str(patch),
                    "--owned-path",
                    "notes.md",
                ]
            )

            self.assertEqual(completed.returncode, 2)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["result"], "blocked")
            self.assertEqual(payload["reason"], "path_scope_mismatch")
            self.assertIn("other.md", payload["paths"])

    def test_accepts_git_diff_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.init_repo(Path(tmp))
            path = repo / "file name.txt"
            path.write_text("before\n", encoding="utf-8")
            self.assertEqual(run(["git", "add", "file name.txt"], cwd=repo).returncode, 0)
            self.assertEqual(run(["git", "commit", "-m", "add spaced path"], cwd=repo).returncode, 0)
            path.write_text("after\n", encoding="utf-8")
            diff = run(["git", "diff", "--", "file name.txt"], cwd=repo)
            patch = repo / "approved.patch"
            patch.write_text(diff.stdout, encoding="utf-8")

            completed = run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo",
                    str(repo),
                    "--patch",
                    str(patch),
                    "--owned-path",
                    "file name.txt",
                ]
            )

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertEqual(json.loads(completed.stdout)["result"], "staged")
            staged = run(["git", "diff", "--cached", "--", "file name.txt"], cwd=repo).stdout
            self.assertIn("after", staged)

    def test_alternate_index_splits_same_file_mixed_changes_without_touching_primary_index(self) -> None:
        """A task patch can be committed while an unrelated same-file stage survives."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.init_repo(Path(tmp))
            path = repo / "notes.md"
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[1] = "line 2 unrelated staged"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertEqual(run(["git", "add", "notes.md"], cwd=repo).returncode, 0)
            primary_index = (repo / ".git" / "index").read_bytes()
            lines[20] = "line 21 task"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            patch = repo / "task.patch"
            patch.write_text(run(["git", "diff", "--unified=0", "--", "notes.md"], cwd=repo).stdout, encoding="utf-8")

            alternate = repo / "task.index"
            # read-tree must populate the alternate index, rather than replacing
            # the primary index that contains the unrelated staged purpose.
            env = dict(os.environ, GIT_INDEX_FILE=str(alternate))
            prepared = subprocess.run(["git", "read-tree", "HEAD"], cwd=repo, env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            completed = run(
                [sys.executable, str(SCRIPT), "--repo", str(repo), "--patch", str(patch),
                 "--owned-path", "notes.md", "--unidiff-zero", "--index-file", str(alternate)]
            )
            self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
            committed = subprocess.run(
                ["git", "commit", "-m", "task purpose"], cwd=repo, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(0, committed.returncode, committed.stderr)
            self.assertEqual(primary_index, (repo / ".git" / "index").read_bytes())
            content = run(["git", "show", "HEAD:notes.md"], cwd=repo).stdout
            self.assertIn("line 21 task", content)
            self.assertNotIn("line 2 unrelated staged", content)
            self.assertIn("line 2 unrelated staged", path.read_text(encoding="utf-8"))
            reverted = Path(tmp) / "reverted"
            self.assertEqual(0, run(["git", "worktree", "add", "--detach", str(reverted), "HEAD"], cwd=repo).returncode)
            self.assertEqual(0, run(["git", "revert", "--no-edit", "HEAD"], cwd=reverted).returncode)
            reverted_content = (reverted / "notes.md").read_text(encoding="utf-8")
            self.assertNotIn("line 21 task", reverted_content)
            self.assertIn("line 2\n", reverted_content)
            self.assertEqual(0, run(["git", "worktree", "remove", str(reverted)], cwd=repo).returncode)

    def test_empty_patch_and_conflict_are_blocked_without_index_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.init_repo(Path(tmp))
            before = (repo / ".git" / "index").read_bytes()
            empty = repo / "empty.patch"
            empty.write_text("", encoding="utf-8")
            blocked = run([sys.executable, str(SCRIPT), "--repo", str(repo), "--patch", str(empty)])
            self.assertEqual(2, blocked.returncode)
            self.assertEqual(before, (repo / ".git" / "index").read_bytes())
            conflict = repo / "conflict.patch"
            conflict.write_text(
                "diff --git a/notes.md b/notes.md\n--- a/notes.md\n+++ b/notes.md\n"
                "@@ -1 +1 @@\n-base\n+wrong base\n", encoding="utf-8"
            )
            blocked = run([sys.executable, str(SCRIPT), "--repo", str(repo), "--patch", str(conflict),
                           "--owned-path", "notes.md"])
            self.assertEqual(1, blocked.returncode)
            self.assertEqual(before, (repo / ".git" / "index").read_bytes())

    def test_rename_and_binary_patches_are_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.init_repo(Path(tmp))
            old = repo / "old.txt"
            old.write_text("rename me\n", encoding="utf-8")
            self.assertEqual(run(["git", "add", "old.txt"], cwd=repo).returncode, 0)
            self.assertEqual(run(["git", "commit", "-m", "add rename source"], cwd=repo).returncode, 0)
            old.rename(repo / "new.txt")
            patch = repo / "rename.patch"
            self.assertEqual(run(["git", "add", "-A"], cwd=repo).returncode, 0)
            patch.write_text(run(["git", "diff", "--cached", "--binary"], cwd=repo).stdout, encoding="utf-8")
            alternate = repo / "rename.index"
            env = dict(os.environ, GIT_INDEX_FILE=str(alternate))
            prepared = subprocess.run(["git", "read-tree", "HEAD"], cwd=repo, env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            completed = run([sys.executable, str(SCRIPT), "--repo", str(repo), "--patch", str(patch),
                             "--owned-path", "old.txt", "--owned-path", "new.txt", "--index-file", str(alternate)])
            self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
            committed = subprocess.run(["git", "commit", "-m", "rename"], cwd=repo, env=env,
                                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(0, committed.returncode, committed.stderr)
            self.assertEqual("rename me\n", run(["git", "show", "HEAD:new.txt"], cwd=repo).stdout)
            self.assertNotEqual(0, run(["git", "cat-file", "-e", "HEAD:old.txt"], cwd=repo).returncode)

            binary_repo = Path(tmp) / "binary-repo"
            binary_repo.mkdir()
            self.assertEqual(run(["git", "init"], cwd=binary_repo).returncode, 0)
            self.assertEqual(run(["git", "config", "user.email", "test@example.com"], cwd=binary_repo).returncode, 0)
            self.assertEqual(run(["git", "config", "user.name", "Test User"], cwd=binary_repo).returncode, 0)
            binary = binary_repo / "blob.bin"
            binary.write_bytes(b"before\x00\xff")
            self.assertEqual(run(["git", "add", "blob.bin"], cwd=binary_repo).returncode, 0)
            self.assertEqual(run(["git", "commit", "-m", "add binary"], cwd=binary_repo).returncode, 0)
            binary.write_bytes(b"after\x00\x00\xfe")
            patch.write_text(run(["git", "diff", "--binary"], cwd=binary_repo).stdout, encoding="utf-8")
            completed = run([sys.executable, str(SCRIPT), "--repo", str(binary_repo), "--patch", str(patch),
                             "--owned-path", "blob.bin"])
            self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
            self.assertIn("blob.bin", run(["git", "diff", "--cached", "--name-only"], cwd=binary_repo).stdout)

    def test_excluded_path_is_blocked_before_git_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.init_repo(Path(tmp))
            path = repo / "notes.md"
            path.write_text("secret-looking value\n", encoding="utf-8")
            patch = repo / "private.patch"
            patch.write_text(run(["git", "diff", "--", "notes.md"], cwd=repo).stdout, encoding="utf-8")
            completed = run([sys.executable, str(SCRIPT), "--repo", str(repo), "--patch", str(patch),
                             "--owned-path", "notes.md", "--excluded-path", "notes.md"])
            self.assertEqual(2, completed.returncode)
            self.assertEqual("", run(["git", "diff", "--cached"], cwd=repo).stdout)


if __name__ == "__main__":
    unittest.main()

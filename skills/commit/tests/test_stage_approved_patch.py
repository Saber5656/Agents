from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()

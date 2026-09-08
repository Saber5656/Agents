from __future__ import annotations

import unittest
from pathlib import Path
import subprocess
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
RETIRED_FLAT_ROOT_CONTRACT = (
    "Retired flat-skills local setup contract; successor coverage is in "
    "tests/test_installation.py and docs/skill-installation.md"
)


class PublicationHygieneTest(unittest.TestCase):
    def local_setup_script(self) -> str:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        local_setup = readme.split("## Local setup", 1)[1]
        return local_setup.split("```bash", 1)[1].split("```", 1)[0].strip()

    def assert_ignored(self, path: str) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(0, result.returncode, path)

    def assert_publishable(self, path: str) -> None:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", path],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(1, result.returncode, path)

    def assert_local_symlink_boundary(self, path: str) -> None:
        candidate = REPO_ROOT / path
        self.assertTrue(candidate.is_symlink(), path)
        tracked = subprocess.run(
            ["git", "ls-files", "--stage", "--", path],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, tracked.returncode, path)
        self.assertIn("120000", tracked.stdout, path)

    def test_private_and_machine_local_files_are_ignored(self) -> None:
        for path in {
            ".env",
            ".env.production",
            "secrets.json",
            "secrets.local.json",
            "credentials.private.pem",
            ".credentials/signing.key",
            "skill/config.local.json",
            "skill/config.private.yaml",
            "skill/tests/fixtures/example.pem",
            ".claude/settings.json",
            ".codex/config.toml",
            ".system/openai-docs/SKILL.md",
            ".workspace/eval/result.json",
            "skill/__pycache__/module.cpython-314.pyc",
        }:
            with self.subTest(path=path):
                self.assert_ignored(path)

    def test_public_examples_and_fixtures_remain_publishable(self) -> None:
        for path in {
            ".env.example",
            "secrets.example.json",
            "example.pem",
            "skill/tests/fixtures/test.key",
            "skill/references/public-key.pem",
            "skill/config.example.yaml",
            "skill/tests/fixtures/.claude/settings.json",
            "skill/tests/fixtures/.codex/config.toml",
            "skill/tests/fixtures/as/example.json",
            "skill/tests/fixtures/kanary/example.json",
        }:
            with self.subTest(path=path):
                self.assert_publishable(path)

    def test_root_local_state_directories_remain_ignored(self) -> None:
        for path in {
            ".claude/settings.json",
            ".codex/config.toml",
            ".codex-work/session.json",
            ".system/openai-docs/SKILL.md",
            ".workspace/eval/result.json",
            "as/example.json",
        }:
            with self.subTest(path=path):
                self.assert_ignored(path)
        self.assert_local_symlink_boundary("kanary")

    def test_root_source_names_are_not_silently_ignored(self) -> None:
        for path in {
            "../config.local.yaml",
            "../docs/feature.private.test.ts",
        }:
            with self.subTest(path=path):
                self.assert_publishable(path)

    @unittest.skip(RETIRED_FLAT_ROOT_CONTRACT)
    def test_local_setup_creates_parents_and_skill_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            repo = Path(temp_dir) / "skills"
            repo.mkdir()

            result = subprocess.run(
                ["bash", "-c", self.local_setup_script()],
                env={"HOME": str(home), "SKILLS_REPO_ROOT": str(repo)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(repo.resolve(), (home / ".claude/skills").resolve())
            self.assertEqual(repo.resolve(), (home / ".codex/skills").resolve())

    @unittest.skip(RETIRED_FLAT_ROOT_CONTRACT)
    def test_local_setup_refuses_to_replace_a_real_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            repo = Path(temp_dir) / "skills"
            existing = home / ".claude/skills"
            existing.mkdir(parents=True)
            repo.mkdir()

            result = subprocess.run(
                ["bash", "-c", self.local_setup_script()],
                env={"HOME": str(home), "SKILLS_REPO_ROOT": str(repo)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("Refusing to replace existing directory", result.stderr)
            self.assertTrue(existing.is_dir())
            self.assertFalse((existing / "skills").exists())

    @unittest.skip(RETIRED_FLAT_ROOT_CONTRACT)
    def test_local_setup_updates_existing_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "home"
            repo = Path(temp_dir) / "skills"
            previous = Path(temp_dir) / "previous-skills"
            repo.mkdir()
            previous.mkdir()
            (home / ".claude").mkdir(parents=True)
            (home / ".codex").mkdir(parents=True)
            (home / ".claude/skills").symlink_to(previous)
            (home / ".codex/skills").symlink_to(previous)

            result = subprocess.run(
                ["bash", "-c", self.local_setup_script()],
                env={"HOME": str(home), "SKILLS_REPO_ROOT": str(repo)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(repo.resolve(), (home / ".claude/skills").resolve())
            self.assertEqual(repo.resolve(), (home / ".codex/skills").resolve())

    def test_readme_documents_the_current_nested_skill_root(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("skills/<skill-name>/", readme)
        self.assertIn("$AGENTS_ROOT", readme)
        self.assertIn("preserving installation", readme)
        self.assertNotIn("## Local setup", readme)


if __name__ == "__main__":
    unittest.main()

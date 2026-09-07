from __future__ import annotations

import json
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]


class PullSkillMetadataTest(unittest.TestCase):
    def test_skill_description_triggers_pull_phrases(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ["プルして", "pullして", "全リポジトリをプルして", "fetch and merge"]:
            self.assertIn(phrase, text)

    def test_skill_documents_safety_boundaries(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ["push | forbidden", "`git reset --hard` | forbidden", "conflict auto-resolution | forbidden"]:
            self.assertIn(phrase, text)

    def test_evals_cover_positive_and_negative_cases(self) -> None:
        evals = json.loads((SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        prompts = [case["prompt"] for case in evals["evals"]]
        self.assertIn("プルして", prompts)
        self.assertIn("pushして", prompts)
        self.assertIn("fetch だけして", prompts)
        self.assertGreaterEqual(len(prompts), 8)

    def test_managed_repositories_include_expected_repos(self) -> None:
        text = (SKILL_DIR / "references" / "managed-repositories.md").read_text(encoding="utf-8")
        for name in ["shared-task-vault", "personal-vault", "dotfiles", "skills-repo"]:
            self.assertIn(name, text)
        self.assertIn("managed-repositories.local.md", text)


if __name__ == "__main__":
    unittest.main()

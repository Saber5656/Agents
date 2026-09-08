from __future__ import annotations

import json
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]


class MergeSkillMetadataTest(unittest.TestCase):
    def test_skill_description_triggers_merge_phrases(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in [
            "Merge or reconcile branches",
            "requested local checkout",
            "use pr-merge-gate for GitHub PR merging",
        ]:
            self.assertIn(phrase, text)

    def test_skill_documents_safety_boundaries(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in [
            "Never reset, clean, force push, bypass hooks",
            "preserving both intended behaviors",
            "This local operation alone does not publish",
        ]:
            self.assertIn(phrase, text)

    def test_skill_documents_commit_first_default(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Commit only when requested/already authorized", text)
        self.assertIn("Resolve ordinary conflicts by preserving both intended behaviors", text)
        self.assertIn("do not sweep dirty state into a checkpoint", text)

    def test_skill_rejects_github_pr_merge_context(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ["GitHub PR merge", "explicit PR URL/number", "pr-merge-gate"]:
            self.assertIn(phrase, text)
        self.assertIn("do not consume it as a request to update every local repository", text)
        self.assertIn("Never reset, clean, force push", text)
        self.assertIn("This local operation alone does not publish", text)

        evals = json.loads((SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        bare_url = next(case for case in evals["evals"] if case["id"] == 9)
        self.assertIn("no local git merge", bare_url["expected_output"])
        self.assertIn("Routes the bare GitHub PR URL to pr-merge-gate", bare_url["expected_output"])

    def test_evals_cover_positive_and_negative_cases(self) -> None:
        evals = json.loads((SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        prompts = [case["prompt"] for case in evals["evals"]]
        self.assertIn("マージして", prompts)
        self.assertIn("pushして", prompts)
        self.assertIn("プルして", prompts)
        self.assertGreaterEqual(len(prompts), 8)

    def test_managed_repositories_include_expected_repos(self) -> None:
        text = (SKILL_DIR / "references" / "managed-repositories.md").read_text(encoding="utf-8")
        for name in ["shared-task-vault", "personal-vault", "dotfiles", "skills-repo"]:
            self.assertIn(name, text)
        self.assertIn("managed-repositories.local.md", text)


if __name__ == "__main__":
    unittest.main()

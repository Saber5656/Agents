from __future__ import annotations

import json
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]


class MergeSkillMetadataTest(unittest.TestCase):
    def test_skill_description_triggers_merge_phrases(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ["マージして", "mergeして", "commit してからマージして", "pull がブロックした"]:
            self.assertIn(phrase, text)

    def test_skill_documents_safety_boundaries(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ["push | forbidden", "`git reset --hard` | forbidden", "conflict auto-resolution | forbidden"]:
            self.assertIn(phrase, text)

    def test_skill_documents_commit_first_default(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("commit-first", text)
        self.assertIn("conflict_aborted", text)

    def test_skill_rejects_github_pr_merge_context(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ["GitHub PR merge", "unsupported_context", "mixed_context", "pr-merge-gate"]:
            self.assertIn(phrase, text)
        self.assertIn("git merge`を実行しない", text)
        self.assertIn("local側を候補化せず", text)
        self.assertIn("fetch、commit、stash、`git merge`を含む全mutationを実行しない", text)
        self.assertIn("別のtask/processを要求し直すのは", text)
        self.assertIn("required_handoff: typed_saihai_merge_envelope", text)
        self.assertIn("完全なtyped Saihai envelope", text)

        evals = json.loads((SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        bare_url = next(case for case in evals["evals"] if case["id"] == 9)
        self.assertIn("no local git merge or PR handoff", bare_url["expected_output"])
        self.assertIn("typed_saihai_merge_envelope", bare_url["expected_output"])

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

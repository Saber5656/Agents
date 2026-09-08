from __future__ import annotations

import json
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]


class PrMergeGateSkillTest(unittest.TestCase):
    def test_required_frontmatter(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for field in [
            "name: pr-merge-gate",
            "description:",
            "user-invocable:",
            "category:",
            "status:",
            "updated:",
        ]:
            self.assertIn(field, text)

    def test_adapter_is_thin_and_fail_closed(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in [
            "thin adapter, not a policy engine",
            "policy_merge_ready",
            "valid_unconsumed",
            "saihai_merge_contract_unavailable",
            "authorization_consumed",
            "github_identity_changed",
            "merge_result_uncertain",
        ]:
            self.assertIn(phrase, text)

    def test_direct_merge_fallback_is_forbidden(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Use `python3 -m harness.delivery merge", text)
        self.assertIn("Do not bypass protection", text)
        self.assertIn("Do not bypass protection or claim atomic base/merge-queue guarantees", text)

    def test_uncertain_result_allows_read_only_reconciliation_only(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in [
            "On a lost response, inspect the same PR",
            "instead of issuing blind duplicate mutations",
            "Read back MERGED and merge SHA",
        ]:
            self.assertIn(phrase, text)

    def test_conflict_repair_is_not_a_merge_gate_or_readiness_bypass(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in [
            "conflict repair",
            "base/head change",
            "invalidates the prior envelope",
            "fresh validation, review, and current CI",
            "never performs conflict repair",
            "does not authorize merge",
        ]:
            self.assertIn(phrase, text)
        self.assertIn("policy_merge_ready", text)
        self.assertIn("Never run `gh pr merge`", text)

    def test_contract_route_and_executor_invocation_failures_are_distinct(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("fixed Saihai-owned validator route cannot be resolved", text)
        self.assertIn("return `saihai_merge_contract_unavailable`", text)
        self.assertIn("inability to invoke the fixed executor at the atomic handoff", text)
        self.assertIn("`saihai_merge_executor_unavailable`. Neither state permits fallback", text)
        self.assertIn(
            "trusted Saihai contract or fixed validator route cannot be resolved | `saihai_merge_contract_unavailable`",
            text,
        )
        self.assertIn(
            "resolved fixed executor cannot be invoked at atomic handoff | `saihai_merge_executor_unavailable`",
            text,
        )
        self.assertNotIn("contract/validator/executor unavailable", text)

    def test_artifact_boundary_and_redaction_are_fail_closed(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in [
            "trusted directory",
            "open the artifact exactly once",
            "beneath-root and no-follow semantics",
            "same opened descriptor",
            "Never validate a lexical",
            "reject symlink components",
            "component/final-entry swaps",
            "contract-defined maximum size",
            "bearer-sensitive",
            "authorization_reference",
            "merge_gate_artifact_invalid",
        ]:
            self.assertIn(phrase, text)
        self.assertRegex(text, r"non-regular\s+files")
        self.assertNotIn("authorization_id: <opaque id>\nauthorization_status", text)

    def test_evals_cover_positive_and_adversarial_cases(self) -> None:
        data = json.loads((SKILL_DIR / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertEqual(data["skill_name"], "pr-merge-gate")
        self.assertGreaterEqual(len(data["evals"]), 19)
        prompts = "\n".join(item["prompt"] for item in data["evals"])
        for phrase in ["CLEAN", "期限切れ", "2回目", "gh pr merge", "dirty", "timeout", "result integrity", "symlink", "swap"]:
            self.assertIn(phrase, prompts)

    def test_trigger_eval_has_balanced_near_misses(self) -> None:
        data = json.loads((SKILL_DIR / "evals" / "trigger-eval.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data), 24)
        self.assertGreaterEqual(sum(1 for item in data if item["should_trigger"]), 12)
        self.assertGreaterEqual(sum(1 for item in data if not item["should_trigger"]), 12)


if __name__ == "__main__":
    unittest.main()

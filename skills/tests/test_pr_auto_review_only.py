import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT = (ROOT / "pr" / "SKILL.md").read_text(encoding="utf-8")
README_TEXT = (ROOT / "pr" / "README.md").read_text(encoding="utf-8")
EVALS = json.loads((ROOT / "pr" / "evals" / "evals.json").read_text(encoding="utf-8"))


class PrAutomaticReviewOnlyTests(unittest.TestCase):
    def test_public_contract_has_no_manual_codex_trigger(self) -> None:
        public_contract = f"{SKILL_TEXT}\n{README_TEXT}"
        for forbidden in (
            "@codex review",
            "gh pr comment",
            "review_trigger_fallback",
        ):
            self.assertNotIn(forbidden, public_contract)

    def test_repository_automation_owns_normal_review_trigger(self) -> None:
        normalized_skill = " ".join(SKILL_TEXT.split())
        self.assertIn("Repository configuration owns the normal review trigger", normalized_skill)
        self.assertIn("never posts a comment-based fallback", normalized_skill)
        self.assertIn(
            "No PR comment was used to trigger Codex review",
            normalized_skill,
        )

    def test_missing_review_is_resumable_without_comment_retrigger(self) -> None:
        self.assertIn("review_pending", SKILL_TEXT)
        self.assertIn("review_timeout", SKILL_TEXT)
        self.assertIn("without posting a trigger comment", SKILL_TEXT)
        self.assertIn("do not post a trigger comment", SKILL_TEXT)

    def test_explicit_fallback_request_is_refused_by_eval(self) -> None:
        fallback_eval = next(item for item in EVALS["evals"] if item["id"] == 16)
        self.assertIn("@codex review", fallback_eval["prompt"])
        self.assertIn("does not post", fallback_eval["expected_output"])
        expectation_text = "\n".join(fallback_eval["expectations"])
        self.assertIn("Does not post the requested @codex review fallback comment", expectation_text)
        self.assertIn("without retriggering review", expectation_text)

    def test_codex_review_requests_still_use_automatic_observation(self) -> None:
        expected_markers = {
            1: "Never posts a manual Codex review-trigger comment",
            2: "Does not post a manual Codex review-trigger comment",
            7: "Does not post a manual Codex review-trigger comment",
            15: "Never posts a manual Codex review-trigger comment",
            16: "Does not post the requested @codex review fallback comment",
        }
        for eval_id, marker in expected_markers.items():
            item = next(entry for entry in EVALS["evals"] if entry["id"] == eval_id)
            contract = f"{item['expected_output']}\n" + "\n".join(item["expectations"])
            self.assertIn(marker, contract)

    def test_direct_reviewer_request_compatibility_is_preserved(self) -> None:
        self.assertIn("Direct reviewer requests remain optional compatibility behavior", SKILL_TEXT)
        reviewer_eval = next(item for item in EVALS["evals"] if item["id"] == 7)
        self.assertIn("reviewer-request failure", reviewer_eval["expected_output"])
        self.assertIn(
            "Does not require pending reviewRequests as the success gate",
            reviewer_eval["expectations"],
        )

    def test_codex_work_monitor_registration_is_explicit(self) -> None:
        self.assertIn("Codex Work PR monitor registration", SKILL_TEXT)
        self.assertIn("pr_monitor_registration: unverified", SKILL_TEXT)
        self.assertIn("continue until merged", SKILL_TEXT)
        self.assertIn("automatic-merge toggle controls merge behavior only", SKILL_TEXT)
        monitor_eval = next(item for item in EVALS["evals"] if item["id"] == 24)
        self.assertIn(
            "Reports pr_monitor_registration: unverified",
            " ".join(monitor_eval["expectations"]),
        )


if __name__ == "__main__":
    unittest.main()

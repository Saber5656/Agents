from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT = (REPO_ROOT / "gh-deliver-remaining-issues" / "SKILL.md").read_text(
    encoding="utf-8"
)
CONTRACT_TEXT = (
    REPO_ROOT
    / "gh-deliver-remaining-issues"
    / "references"
    / "execution-contract.md"
).read_text(encoding="utf-8")


class GhDeliverContractTests(unittest.TestCase):
    def test_catalog_mapping_is_retained_in_skill_and_contract(self) -> None:
        for text in (SKILL_TEXT, CONTRACT_TEXT):
            self.assertIn("catalog_env = {}", text)
            self.assertIn("environ=catalog_env", text)
            self.assertIn('catalog_result["status"] == "loaded"', text)
            self.assertIn('catalog_env["AGENTS_VAULT_ROOT"]', text)
            self.assertNotIn("environ={}", text)
            self.assertNotIn("returned catalog values", text)

    def test_publication_and_review_flags_have_bound_provenance(self) -> None:
        for field in (
            "repository_restrictions_provenance",
            "required_provenance",
            "ready_pr_provenance",
            "stacked_provenance",
            "labels_provenance",
        ):
            self.assertIn(field, CONTRACT_TEXT)

    def test_explicit_organization_assignments_are_scoped_and_routed(self) -> None:
        self.assertIn("organization assignments explicitly supplied by the user", CONTRACT_TEXT)
        self.assertIn("preserve both provenance objects and route the conflict", CONTRACT_TEXT)

if __name__ == "__main__":
    unittest.main()

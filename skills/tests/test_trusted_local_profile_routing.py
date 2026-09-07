from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
USAGE_RUN = (
    "python3.11 scripts/saihai.py usage run --request /absolute/request.json "
    "--authorization /absolute/authority.json --state-root /absolute/private-state"
)
USAGE_ADVANCE = (
    "python3.11 scripts/saihai.py usage advance --authorization /absolute/authority.json "
    "--state-root /absolute/private-state"
)

ROUTED_FILES = (
    "gh-deliver-remaining-issues/SKILL.md",
    "gh-deliver-remaining-issues/references/execution-contract.md",
    "pr/SKILL.md",
    "pr/references/publication-safety-contract.md",
    "push/SKILL.md",
    "pr-merge-gate/SKILL.md",
    "commit/SKILL.md",
    "pr-review-fix-policy/SKILL.md",
)


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class TrustedLocalProfileRoutingTests(unittest.TestCase):
    def test_publication_contracts_declare_both_profiles(self) -> None:
        for relative_path in ROUTED_FILES:
            with self.subTest(path=relative_path):
                text = read(relative_path)
                self.assertIn("trusted_local_v1", text)
                self.assertIn("legacy_managed", text)
                self.assertIn("host_publication_adapter", text)

    def test_normal_route_uses_the_host_usage_entrypoint(self) -> None:
        for relative_path in ROUTED_FILES:
            with self.subTest(path=relative_path):
                self.assertIn(USAGE_RUN, read(relative_path))

    def test_bounded_continuation_is_explicit_at_execution_boundaries(self) -> None:
        for relative_path in (
            "gh-deliver-remaining-issues/references/execution-contract.md",
            "pr/SKILL.md",
            "pr/references/publication-safety-contract.md",
            "pr-merge-gate/SKILL.md",
        ):
            with self.subTest(path=relative_path):
                self.assertIn(USAGE_ADVANCE, read(relative_path))

    def test_legacy_prerequisites_are_not_normal_route_prerequisites(self) -> None:
        text = read("pr/SKILL.md")
        self.assertIn("root-owned broker/attestation is not a prerequisite", text)
        self.assertIn("`legacy_managed` route", text)
        self.assertIn("never switch profiles implicitly", text)

    def test_conflict_resolution_targets_are_marker_free(self) -> None:
        conflict_files = (
            "gh-deliver-remaining-issues/SKILL.md",
            "gh-deliver-remaining-issues/evals/evals.json",
            "gh-deliver-remaining-issues/references/execution-contract.md",
            "pr/SKILL.md",
            "pr/evals/evals.json",
        )
        for relative_path in conflict_files:
            with self.subTest(path=relative_path):
                text = read(relative_path)
                self.assertNotIn("<<<<<<<", text)
                self.assertNotIn("=======", text)
                self.assertNotIn(">>>>>>>", text)


if __name__ == "__main__":
    unittest.main()

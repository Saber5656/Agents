from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_NAME = "english" + "-coach"


class EnglishCoachRetirementTest(unittest.TestCase):
    def test_legacy_skill_is_absent(self) -> None:
        legacy_dir = REPO_ROOT / LEGACY_NAME
        self.assertFalse(any(path.is_file() for path in legacy_dir.rglob("*")))

    def test_active_skill_contracts_do_not_reference_legacy_name(self) -> None:
        active_files = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "TEMPLATE.md",
            *sorted(REPO_ROOT.glob("*/SKILL.md")),
            *sorted(REPO_ROOT.glob("*/evals/evals.json")),
        ]
        failures = []
        for path in active_files:
            if LEGACY_NAME in path.read_text(encoding="utf-8"):
                failures.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual([], failures)

    def test_shared_regression_suite_remains_present(self) -> None:
        test_files = sorted((REPO_ROOT / "tests").glob("test_*.py"))
        self.assertGreaterEqual(len(test_files), 2)


if __name__ == "__main__":
    unittest.main()

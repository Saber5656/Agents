from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_NAME = "security" + "-professor"


class SecurityProfessorRetirementTest(unittest.TestCase):
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

    def test_commit_eval_routes_security_work_to_tech_security(self) -> None:
        evals = json.loads(
            (REPO_ROOT / "commit/evals/evals.json").read_text(encoding="utf-8")
        )
        case = next(item for item in evals["evals"] if item["id"] == 4)
        contract = f"{case['prompt']}\n{case['expected_output']}\n{case.get('assertions', {})}"
        self.assertIn("tech-security", contract)
        self.assertNotIn(LEGACY_NAME, contract)

    def test_skill_updater_does_not_select_a_security_review_provider(self) -> None:
        contract = (REPO_ROOT / "skill-updater/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("caller-supplied Saihai task context", contract)
        self.assertIn("provider が指定されていない場合は独自に選ばず", contract)
        self.assertNotIn("`tech-security`", contract)


if __name__ == "__main__":
    unittest.main()

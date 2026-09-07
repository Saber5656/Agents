from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
def active_contract_files() -> list[Path]:
    """Return public skill contracts, excluding historical/reference/test prose."""
    return [
        REPO_ROOT / "README.md",
        REPO_ROOT / "TEMPLATE.md",
        *sorted(REPO_ROOT.glob("*/SKILL.md")),
        *sorted(REPO_ROOT.glob("*/evals/evals.json")),
    ]


class ControlPlaneRemovalTest(unittest.TestCase):
    def test_legacy_skill_artifacts_are_absent(self) -> None:
        legacy_dir = REPO_ROOT / ("configure" + "-organization")
        self.assertFalse(any(path.is_file() for path in legacy_dir.rglob("*")))

    def test_active_repository_has_no_legacy_control_references(self) -> None:
        forbidden_markers = [
            "configure" + "-organization",
            "configure" + "_organization",
            "Agent" + "-Teams-Viewer",
        ]
        failures: dict[str, list[str]] = {}

        for path in active_contract_files():
            text = path.read_text(encoding="utf-8")
            matches = [marker for marker in forbidden_markers if marker in text]
            if matches:
                failures[str(path.relative_to(REPO_ROOT))] = matches

        self.assertEqual({}, failures)

    def test_workflow_contracts_require_explicit_saihai_inputs(self) -> None:
        expected_contracts = {
            "README.md": [
                "caller-supplied Saihai task context",
                "typed artifact",
                "do not select roles",
            ],
            "TEMPLATE.md": [
                "caller-supplied Saihai task context",
                "typed artifact",
                "独自に決めたりしない",
            ],
            "secretary-ai/SKILL.md": [
                "caller-supplied Saihai task context",
                "task handoff draft",
                "選択しない",
            ],
            "git-workspace-prep/SKILL.md": [
                "caller-supplied Saihai task context",
                "Branch Plan",
                "このスキル内で決めず",
            ],
            "merge/SKILL.md": [
                "caller-supplied Saihai task context",
                "task_scope_missing",
                "このスキル内で決めず",
            ],
            "pull/SKILL.md": [
                "caller-supplied Saihai task context",
                "task_scope_missing",
                "このスキル内で決めず",
            ],
            "push/SKILL.md": [
                "caller-supplied Saihai task context",
                "Publication Manifest",
                "独自に決めない",
            ],
            "commit/SKILL.md": [
                "caller-supplied Saihai task context",
                "Task Change Manifest",
                "Publication Manifest",
                "独自に決めない",
            ],
        }

        for relative_path, required_markers in expected_contracts.items():
            with self.subTest(path=relative_path):
                text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
                for marker in required_markers:
                    self.assertIn(marker, text)

    def test_migrated_evals_return_missing_context_to_the_caller(self) -> None:
        secretary_evals = json.loads(
            (REPO_ROOT / "secretary-ai/evals/evals.json").read_text(encoding="utf-8")
        )
        commit_evals = json.loads(
            (REPO_ROOT / "commit/evals/evals.json").read_text(encoding="utf-8")
        )

        secretary_case = next(case for case in secretary_evals["evals"] if case["id"] == 3)
        commit_case = next(case for case in commit_evals["evals"] if case["id"] == 8)

        for case in [secretary_case, commit_case]:
            self.assertIn("caller-supplied Saihai task context", case["expected_output"])
        self.assertIn("呼び出し元へ返す", secretary_case["expected_output"])
        self.assertIn("caller へ差し戻し", commit_case["expected_output"])


if __name__ == "__main__":
    unittest.main()

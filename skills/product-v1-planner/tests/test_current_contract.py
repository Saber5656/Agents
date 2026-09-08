from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_planner_uses_explicit_apply_request_without_legacy_manifest_gate():
    skill = (ROOT / "SKILL.md").read_text()

    assert "mode: proposal | audit | apply" in skill
    assert "explicit write request" in skill
    assert "Decision Manifest" in skill  # historical status is documented
    assert "現在の実行に必要な入力ではない" in skill
    assert "Complete planning units are recorded in the current task system (#67)" in skill
    assert "Issueization (#69) owns derived Issue creation" in skill
    assert "retain the prior statement and rationale" in skill
    assert "approved-apply" not in skill


def test_planning_contract_preserves_scope_and_evidence_boundaries():
    contract = (ROOT / "references" / "planning-contract.md").read_text()

    assert "提案と監査はread-only" in contract
    assert "ユーザーまたはcallerが対象、目的、許容範囲を明示" in contract
    assert "absolute path、root外、symlink経由" in contract
    assert "product code、commit、push、PR、merge、release、公開操作はこのskillの責務外" in contract


def test_readme_exposes_current_apply_mode():
    readme = (ROOT / "README.md").read_text()

    assert "`apply`:" in readme
    assert "approved-apply" not in readme

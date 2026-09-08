import json
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


def test_sandbox_boundary_uses_explicit_apply_and_issueization_owner():
    """The sandbox note must not revive the retired approval/Issue gate."""
    skill = (ROOT / "SKILL.md").read_text()

    assert "Approved apply" not in skill
    assert "individually approved Issue operations" not in skill
    assert "apply is explicitly requested" in skill
    assert "Issueization owns derived Issue creation" in skill


def test_legacy_decision_manifest_is_not_an_active_planner_dependency():
    """Historical validator artifacts remain visible but are not active inputs."""
    skill = (ROOT / "SKILL.md").read_text()
    contract = (ROOT / "references" / "planning-contract.md").read_text()
    readme = (ROOT / "README.md").read_text()
    active_docs = "\n".join((skill, contract, readme))

    assert "validate_decision_manifest.py" not in active_docs
    assert "decision-manifest.schema.json" not in active_docs
    legacy_validator = ROOT / "scripts" / "validate_decision_manifest.py"
    assert legacy_validator.exists()
    assert (ROOT / "references" / "decision-manifest.schema.json").exists()

    active_scripts = [
        path for path in (ROOT / "scripts").glob("*.py")
        if path.name != legacy_validator.name
    ]
    assert all("validate_decision_manifest" not in path.read_text() for path in active_scripts)


def test_runtime_batch_fixture_has_all_bounded_acceptance_cases():
    """Keep the post-install execution fixture concrete and non-fabricated."""
    fixture_root = ROOT / "evals" / "files" / "runtime-batch"
    batch = json.loads((fixture_root / "batch.json").read_text())
    case_ids = {case["id"] for case in batch["cases"]}
    assert case_ids == {
        "three-feature-decomposition",
        "requirement-correction",
        "ordinary-goal-nonactivation",
        "bounded-critique",
        "explicit-article-rewrite",
    }
    assert len(batch["cases"]) == 5
    assert (fixture_root / "acceptance.md").exists()
    for case in batch["cases"]:
        assert (fixture_root / case["input"]).exists()
        assert case["expected_artifacts"]
        assert case["must_record"]
    assert "fabricated provider/model success string" in (
        fixture_root / "acceptance.md"
    ).read_text()

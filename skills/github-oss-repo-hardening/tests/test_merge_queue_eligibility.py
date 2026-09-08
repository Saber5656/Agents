"""Offline merge-queue eligibility and workflow-precondition fixtures."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/merge_queue_eligibility.py"
spec = importlib.util.spec_from_file_location("merge_queue_eligibility", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "repository,expected",
    [
        ({"owner_type": "User", "visibility": "public"}, "unsupported"),
        ({"owner_type": "Organization", "visibility": "public"}, "supported"),
        ({"owner_type": "Organization", "visibility": "private", "enterprise_cloud": True}, "supported"),
        ({"owner_type": "Organization", "visibility": "private", "enterprise_cloud": False}, "unsupported"),
        ({"owner_type": "Organization", "visibility": "private"}, "unknown"),
        ({"owner_type": "Organization", "visibility": "internal", "enterprise_cloud": True}, "unsupported"),
        ({"owner_type": "Organization", "visibility": "unknown", "enterprise_cloud": True}, "unknown"),
    ],
)
def test_eligibility_is_explicit_and_fails_closed(repository, expected):
    result = module.evaluate_eligibility(repository)
    assert result["status"] == expected
    assert result["reason"]
    assert result["can_propose_activation"] is (expected == "supported")


def test_eligibility_does_not_infer_from_auth_or_rulesets():
    result = module.evaluate_eligibility(
        {
            "owner_type": "User",
            "visibility": "public",
            "authenticated": True,
            "admin": True,
            "rulesets_readable": True,
        }
    )
    assert result["status"] == "unsupported"
    assert "credential" not in result["reason"].lower()


def test_required_checks_and_merge_group_are_separate_from_eligibility():
    eligibility = module.evaluate_eligibility({"owner_type": "Organization", "visibility": "public"})
    workflow = module.evaluate_workflow_requirements(
        [
            {"name": "ci", "on": ["pull_request"]},
            {"name": "docs", "on": ["push", "merge_group"]},
        ],
        ["ci", "docs"],
    )
    assert eligibility["status"] == "supported"
    assert workflow["status"] == "incomplete"
    assert workflow["missing_merge_group"] == ["ci"]
    assert workflow["required_checks"] == ["ci", "docs"]


def test_unknown_workflow_evidence_does_not_enable_activation():
    result = module.evaluate_workflow_requirements(None, ["ci"])
    assert result["status"] == "unknown"
    assert result["can_propose_activation"] is False


def test_duplicate_workflow_identity_is_unknown():
    result = module.evaluate_workflow_requirements(
        [{"name": "ci", "on": ["merge_group"]}, {"name": "ci", "on": ["merge_group"]}],
        ["ci"],
    )
    assert result["status"] == "unknown"
    assert result["can_propose_activation"] is False


def test_cli_is_read_only_json_input(tmp_path, capsys, monkeypatch):
    input_path = tmp_path / "repo.json"
    input_path.write_text(json.dumps({"owner_type": "User", "visibility": "public"}))
    monkeypatch.setattr(module.sys, "argv", ["eligibility", "--input", str(input_path)])
    assert module.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "unsupported"
    assert input_path.read_text() == '{"owner_type": "User", "visibility": "public"}'

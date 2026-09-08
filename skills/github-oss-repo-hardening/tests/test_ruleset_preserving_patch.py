"""Preserving ruleset patch fixtures; no GitHub calls are made."""
import importlib.util
from argparse import Namespace
from copy import deepcopy
from pathlib import Path

import pytest
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/apply-default-branch-ruleset.py"
spec = importlib.util.spec_from_file_location("ruleset_helper", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def args(**overrides):
    values = dict(
        ruleset_name="protect-main-branch-of-OSS",
        required_check=[],
        require_signed_commits=False,
        code_scanning_tool=[],
        code_scanning_alerts_threshold="errors",
        code_scanning_security_threshold="high_or_higher",
        enable_copilot_review=False,
        copilot_review_drafts=False,
        copilot_review_on_push=False,
    )
    values.update(overrides)
    return Namespace(**values)


def existing_ruleset():
    return {
        "id": 42,
        "name": "protect-main-branch-of-OSS",
        "target": "branch",
        "enforcement": "evaluate",
        "bypass_actors": [{"actor_id": 7, "actor_type": "Integration", "bypass_mode": "always"}],
        "conditions": {"ref_name": {"include": ["main", "release/*"], "exclude": ["release/legacy"]}},
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "ci/legacy", "integration_id": 123}],
                    "strict_required_status_checks_policy": False,
                    "do_not_enforce_on_create": False,
                    "unrelated_parameter": "keep",
                },
                "unrelated_rule_key": "keep",
            },
            {"type": "unknown_future_rule", "parameters": {"future": True}},
        ],
        "source_type": "Organization",
        "source": "upstream-org",
        "inherited": True,
        "updated_at": "2026-09-08T00:00:00Z",
    }


def test_patch_preserves_unknown_rules_checks_bypass_conditions_enforcement_and_origin():
    before = existing_ruleset()
    desired = helper.build_payload(args(required_check=["ci/new"]))
    patched = helper.merge_existing_ruleset(before, desired)

    assert patched["enforcement"] == before["enforcement"]
    assert patched["bypass_actors"] == before["bypass_actors"]
    assert patched["conditions"] == before["conditions"]
    assert "source_type" not in patched
    assert "source" not in patched
    assert "inherited" not in patched
    assert {rule["type"] for rule in patched["rules"]} == {"required_status_checks", "unknown_future_rule", "deletion", "non_fast_forward", "required_linear_history", "pull_request"}
    checks = next(rule for rule in patched["rules"] if rule["type"] == "required_status_checks")["parameters"]["required_status_checks"]
    assert checks == [{"context": "ci/legacy", "integration_id": 123}, {"context": "ci/new"}]
    preserved = next(rule for rule in patched["rules"] if rule["type"] == "unknown_future_rule")
    assert preserved == {"type": "unknown_future_rule", "parameters": {"future": True}}
    assert next(rule for rule in patched["rules"] if rule["type"] == "required_status_checks")["unrelated_rule_key"] == "keep"


def test_preimage_changes_are_detected_before_mutation():
    before = existing_ruleset()
    after = deepcopy(before)
    after["rules"].append({"type": "later_unrelated_rule"})
    assert helper.ruleset_preimage_hash(before) != helper.ruleset_preimage_hash(after)

    timestamp_only = deepcopy(before)
    timestamp_only["updated_at"] = "2026-09-08T01:00:00Z"
    assert helper.ruleset_preimage_hash(before) == helper.ruleset_preimage_hash(timestamp_only)
    assert "updated_at" not in helper.ruleset_preimage(timestamp_only)


def test_lost_response_readback_reconciles_without_retry():
    expected = existing_ruleset()
    assert helper.reconcile_mutation_readback(expected, expected) == "applied"
    changed = deepcopy(expected)
    changed["enforcement"] = "disabled"
    assert helper.reconcile_mutation_readback(changed, expected) == "ambiguous"


def test_partial_batch_can_prepare_scoped_restore_and_refuses_later_drift():
    before = existing_ruleset()
    desired = helper.build_payload(args(required_check=["ci/new"]))
    after = helper.merge_existing_ruleset(before, desired)

    # A second target can fail after this target applied. The recovery caller
    # receives only this target's reviewed write body for a scoped restore.
    restore = helper.prepare_scoped_restore(after, before, after)
    assert restore == helper._payload_view(before)

    later_change = deepcopy(after)
    later_change["conditions"]["ref_name"]["include"].append("release/*")
    with pytest.raises(helper.RollbackDrift):
        helper.prepare_scoped_restore(later_change, before, after)

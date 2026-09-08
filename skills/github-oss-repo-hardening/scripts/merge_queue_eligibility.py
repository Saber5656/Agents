#!/usr/bin/env python3
"""Classify GitHub merge-queue eligibility without mutating repository settings.

The classifier intentionally separates product eligibility from authentication,
permission, required-check identity, and workflow evidence. It accepts observed
metadata from a read-only adapter or a fixture and emits fixed JSON diagnostics.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNKNOWN = "unknown"
INCOMPLETE = "incomplete"


def _result(status: str, reason: str, *, can_propose_activation: bool = False, **details: Any) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "can_propose_activation": can_propose_activation,
        **details,
    }


def evaluate_eligibility(repository: Mapping[str, Any]) -> dict[str, Any]:
    """Classify the product/ownership gate from observed repository metadata.

    GitHub documents merge queues for public organization-owned repositories or
    private organization-owned repositories using GitHub Enterprise Cloud.
    Missing or malformed evidence is unknown and never queue-ready.
    """
    if not isinstance(repository, Mapping):
        return _result(UNKNOWN, "repository metadata is missing or malformed")

    owner_type = repository.get("owner_type")
    visibility = repository.get("visibility")
    enterprise_cloud = repository.get("enterprise_cloud")

    if owner_type not in {"User", "Organization"}:
        return _result(UNKNOWN, "owner type is missing or unsupported")
    if visibility not in {"public", "private", "internal"}:
        return _result(UNKNOWN, "repository visibility is missing or unsupported")
    if owner_type == "User":
        return _result(UNSUPPORTED, "personal-account repositories are not eligible for merge queues")
    if visibility == "public":
        return _result(SUPPORTED, "public organization-owned repository is eligible", can_propose_activation=True)
    if visibility == "internal":
        return _result(UNSUPPORTED, "internal repositories are not eligible for merge queues")
    if enterprise_cloud is True:
        return _result(
            SUPPORTED,
            "private organization-owned repository on GitHub Enterprise Cloud is eligible",
            can_propose_activation=True,
        )
    if enterprise_cloud is False:
        return _result(UNSUPPORTED, "private organization-owned repository requires GitHub Enterprise Cloud")
    return _result(UNKNOWN, "private-repository product plan evidence is missing")


def _events(value: Any) -> set[str] | None:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not all(isinstance(event, str) for event in value):
            return None
        return set(value)
    if isinstance(value, Mapping):
        return {str(event) for event in value}
    return None


def _workflow_identity(workflow: Mapping[str, Any]) -> str | None:
    """Return only the observed job/check-run name.

    A workflow display name or path identifies a producer configuration, not a
    status check identity. Accepting either as a fallback can produce a false
    queue-readiness result.
    """
    value = workflow.get("check_name")
    return value if isinstance(value, str) and value else None


def _pending(required_check: str, missing: list[str], reason: str) -> dict[str, Any]:
    return {"required_check": required_check, "missing": missing, "reason": reason}


def _global_pending(missing: list[str], reason: str) -> dict[str, Any]:
    return {"missing": missing, "reason": reason}


def evaluate_workflow_requirements(
    workflows: Sequence[Mapping[str, Any]] | None,
    required_checks: Sequence[str] | None,
) -> dict[str, Any]:
    """Check exact required-check identities and their merge_group trigger.

    This is deliberately a separate result from :func:`evaluate_eligibility`.
    A supported repository with missing or ambiguous workflow evidence remains
    incomplete and cannot produce an activation proposal.
    """
    if workflows is None or required_checks is None:
        checks = list(required_checks) if isinstance(required_checks, Sequence) and not isinstance(required_checks, (str, bytes, bytearray)) else []
        missing = []
        if required_checks is None:
            missing.append("required_checks")
        if workflows is None:
            missing.append("workflows")
        return _result(
            UNKNOWN,
            "required-check or workflow evidence is missing",
            required_checks=checks,
            missing_workflows=checks,
            missing_merge_group=[],
            observed_checks=[],
            pending_evidence=[
                _global_pending(missing, "required-check or workflow evidence is missing")
            ] + [
                _pending(
                    check,
                    ["check_name", "producer", "head", "on"],
                    "observed job/check-run evidence is missing",
                )
                for check in checks
                if isinstance(check, str)
            ],
        )
    if not isinstance(workflows, Sequence) or isinstance(workflows, (str, bytes, bytearray)):
        return _result(
            UNKNOWN,
            "workflow evidence is malformed",
            required_checks=[],
            missing_workflows=[],
            missing_merge_group=[],
            observed_checks=[],
            pending_evidence=[_global_pending(["workflows"], "workflow evidence is malformed")],
        )
    if not isinstance(required_checks, Sequence) or isinstance(required_checks, (str, bytes, bytearray)):
        return _result(
            UNKNOWN,
            "required-check evidence is malformed",
            required_checks=[],
            missing_workflows=[],
            missing_merge_group=[],
            observed_checks=[],
            pending_evidence=[_global_pending(["required_checks"], "required-check evidence is malformed")],
        )
    checks = [check for check in required_checks if isinstance(check, str) and check]
    if len(checks) != len(required_checks):
        return _result(
            UNKNOWN,
            "required-check identities are missing or malformed",
            required_checks=checks,
            missing_workflows=[],
            missing_merge_group=[],
            observed_checks=[],
            pending_evidence=[_global_pending(["exact_required_check_identity"], "required-check identities are missing or malformed")],
        )
    if not checks:
        return _result(
            INCOMPLETE,
            "at least one exact required check is needed before queue activation",
            required_checks=[],
            missing_workflows=[],
            missing_merge_group=[],
            observed_checks=[],
            pending_evidence=[_global_pending(["required_checks"], "at least one exact required check is needed")],
        )
    if len(set(checks)) != len(checks):
        return _result(
            UNKNOWN,
            "required-check identities are duplicated",
            required_checks=checks,
            missing_workflows=[],
            missing_merge_group=[],
            observed_checks=[],
            pending_evidence=[_global_pending(["unique_required_checks"], "required checks must be unique")],
        )

    by_identity: dict[str, list[Mapping[str, Any]]] = {}
    for workflow in workflows:
        if not isinstance(workflow, Mapping):
            return _result(
                UNKNOWN,
                "workflow evidence is malformed",
                required_checks=checks,
                missing_workflows=[],
                missing_merge_group=[],
                observed_checks=[],
                pending_evidence=[
                    _global_pending(
                        ["check_name", "producer", "head", "on"],
                        "workflow item is not a job/check-run observation",
                    )
                ],
            )
        identity = _workflow_identity(workflow)
        if identity is not None:
            by_identity.setdefault(identity, []).append(workflow)
    missing_workflows = [check for check in checks if check not in by_identity]
    missing_merge_group: list[str] = []
    observed_checks: list[dict[str, Any]] = []
    pending_evidence: list[dict[str, Any]] = [
        _pending(check, ["check_name", "producer", "head", "on"], "no observed job/check-run has this exact check_name")
        for check in missing_workflows
    ]
    malformed = False
    for check in checks:
        observations = by_identity.get(check, [])
        if not observations:
            continue
        if len(observations) != 1:
            malformed = True
            pending_evidence.append(
                _pending(
                    check,
                    ["unique_observation"],
                    "multiple job/check-run observations have this exact check_name",
                )
            )
            continue
        workflow = observations[0]
        missing: list[str] = []
        for field in ("producer", "head"):
            value = workflow.get(field)
            if not isinstance(value, str) or not value:
                missing.append(field)
        events = _events(workflow.get("on"))
        if events is None:
            missing.append("on")
        if missing:
            malformed = True
            pending_evidence.append(
                _pending(check, missing, "job/check-run evidence is incomplete")
            )
            continue
        if "merge_group" not in events:
            missing_merge_group.append(check)
            pending_evidence.append(_pending(check, ["merge_group"], "required check does not report on merge_group"))
        observed_checks.append(
            {
                "required_check": check,
                "check_name": workflow["check_name"],
                "producer": workflow["producer"],
                "head": workflow["head"],
                "merge_group": "merge_group" in events,
            }
        )
    if malformed:
        return _result(
            UNKNOWN,
            "required job/check-run evidence is missing or ambiguous",
            required_checks=checks,
            missing_workflows=missing_workflows,
            missing_merge_group=missing_merge_group,
            observed_checks=observed_checks,
            pending_evidence=pending_evidence,
        )
    if missing_workflows or missing_merge_group:
        return _result(
            INCOMPLETE,
            "required checks and merge_group workflow triggers are not all ready",
            required_checks=checks,
            missing_workflows=missing_workflows,
            missing_merge_group=missing_merge_group,
            observed_checks=observed_checks,
            pending_evidence=pending_evidence,
        )
    return _result(
        "complete",
        "required check identities and merge_group triggers are present",
        can_propose_activation=True,
        required_checks=checks,
        missing_workflows=[],
        missing_merge_group=[],
        observed_checks=observed_checks,
        pending_evidence=[],
    )


def load_input(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify merge-queue eligibility without mutation.")
    parser.add_argument("--input", required=True, type=Path, help="JSON file containing observed repository metadata")
    args = parser.parse_args()
    try:
        data = load_input(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    if "repository" in data:
        output = {
            "eligibility": evaluate_eligibility(data.get("repository")),
            "workflow": evaluate_workflow_requirements(data.get("workflows"), data.get("required_checks")),
        }
    else:
        output = evaluate_eligibility(data)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

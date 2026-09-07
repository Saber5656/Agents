#!/usr/bin/env python3
"""Fail-closed semantic validator for product-v1-planner Decision Manifests."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DECISION_CATEGORIES = {
    "v1_scope", "requirement", "architecture", "api", "schema",
    "permission", "security", "compatibility", "dependency", "defer",
}
ACTIONS = {"create_doc", "update_doc", "create_issue", "update_issue"}
ARTIFACT_KEYS = {"artifact_id", "media_type", "resolver", "locator", "exact_bytes_sha256"}
ISSUE_TARGET = re.compile(r"^(?P<repository>[^/\s]+/[^/#\s]+)#(?P<number>new|[1-9][0-9]*)$")


def canonical_bundle_digest(proposal: dict) -> str:
    preimage = {key: proposal[key] for key in (
        "bundle_id", "repository", "base_sha", "evidence_binding", "generated_at",
        "source_manifest_digest", "artifacts",
    )}
    preimage["artifacts"] = sorted(preimage["artifacts"], key=lambda item: item["artifact_id"])
    encoded = json.dumps(preimage, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_artifact_payloads(proposal: dict, repository_root: Path) -> list[str]:
    errors: list[str] = []
    root = repository_root.resolve()
    artifacts = proposal.get("artifacts")
    if not isinstance(artifacts, list):
        return errors
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            continue
        if artifact.get("resolver") != "repository_relative_file":
            errors.append(f"proposal.artifacts[{index}] resolver is unsupported")
            continue
        locator_value = artifact.get("locator")
        if not isinstance(locator_value, str) or not locator_value or "\x00" in locator_value:
            errors.append(f"proposal.artifacts[{index}] locator must be a safe string")
            continue
        locator = Path(locator_value)
        if locator.is_absolute():
            errors.append(f"proposal.artifacts[{index}] locator must be repository-relative")
            continue
        try:
            target = (root / locator).resolve()
        except (OSError, ValueError) as error:
            errors.append(f"proposal.artifacts[{index}] locator is invalid: {error}")
            continue
        try:
            target.relative_to(root)
        except ValueError:
            errors.append(f"proposal.artifacts[{index}] locator escapes repository.root")
            continue
        try:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as error:
            errors.append(f"proposal.artifacts[{index}] payload is unreadable: {error}")
            continue
        if actual != artifact.get("exact_bytes_sha256"):
            errors.append(f"proposal.artifacts[{index}] payload digest mismatch")
    return errors


def classify_target_state(mutation: dict, current_state: str, current_digest: str | None) -> str:
    """Return apply, idempotent_skip, or stale_target for an immediate pre-write observation."""
    if current_state == "present" and current_digest == mutation.get("proposed_after_digest"):
        return "idempotent_skip"
    expected = mutation.get("expected_before") or {}
    if current_state == expected.get("state") and current_digest == expected.get("digest"):
        return "apply"
    return "stale_target"


def resolve_repository_path(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or "\x00" in value or Path(value).is_absolute():
        return None
    try:
        target = (root.resolve() / value).resolve()
    except (OSError, ValueError):
        return None
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def validate_runtime_state(data: dict) -> list[str]:
    """Validate mutable repository state immediately before approved apply."""
    errors: list[str] = []
    repository = data.get("repository")
    mutations = data.get("mutations")
    if not isinstance(repository, dict) or not isinstance(mutations, list):
        return errors
    root_value = repository.get("root")
    if not isinstance(root_value, str) or not Path(root_value).is_absolute():
        return errors
    root = Path(root_value).resolve()
    try:
        actual_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        errors.append("repository.root must be a readable Git worktree")
    else:
        if actual_head != repository.get("base_sha"):
            errors.append("repository HEAD does not match repository.base_sha")

    for index, mutation in enumerate(mutations):
        if not isinstance(mutation, dict) or mutation.get("action") not in {"create_doc", "update_doc"}:
            continue
        target = resolve_repository_path(root, mutation.get("exact_target"))
        if target is None:
            continue
        try:
            if target.is_file():
                current_state = "present"
                current_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            elif target.exists():
                errors.append(f"mutations[{index}] doc target is not a regular file")
                continue
            else:
                current_state = "absent"
                current_digest = None
        except OSError as error:
            errors.append(f"mutations[{index}] doc target is unreadable: {error}")
            continue
        if classify_target_state(mutation, current_state, current_digest) == "stale_target":
            errors.append(f"mutations[{index}] target precondition is stale")
    return errors


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate(data: object) -> list[str]:
    errors: list[str] = []
    require(isinstance(data, dict), "manifest must be an object", errors)
    if not isinstance(data, dict):
        return errors

    require(data.get("schema_version") == "1", "schema_version must be '1'", errors)
    require(data.get("mode") == "approved-apply", "mode must be approved-apply", errors)
    require(bool(data.get("manifest_id")), "manifest_id is required", errors)

    repo = data.get("repository")
    require(isinstance(repo, dict), "repository is required", errors)
    if isinstance(repo, dict):
        root_value = repo.get("root")
        require(isinstance(root_value, str) and bool(root_value) and "\x00" not in root_value and Path(root_value).is_absolute(), "repository.root must be a safe absolute string", errors)
        require(repo.get("owner_repo") is None or bool(re.fullmatch(r"[^/\s]+/[^/\s]+", str(repo.get("owner_repo")))), "repository.owner_repo must be owner/repo or null", errors)
        require(bool(SHA40.fullmatch(str(repo.get("base_sha", "")))), "repository.base_sha must be a full SHA", errors)
        require("observed_at" in repo, "repository.observed_at is required", errors)
        issue_digest = repo.get("issue_snapshot_digest")
        require(issue_digest is None or bool(SHA64.fullmatch(str(issue_digest))), "issue_snapshot_digest must be null or sha256", errors)

    proposal = data.get("proposal")
    require(isinstance(proposal, dict), "proposal is required", errors)
    if isinstance(proposal, dict):
        require(bool(proposal.get("bundle_id")), "proposal.bundle_id is required", errors)
        require(proposal.get("evidence_binding") == "bound_repository", "proposal must be bound_repository", errors)
        require(bool(proposal.get("repository")), "proposal.repository is required", errors)
        require(bool(SHA40.fullmatch(str(proposal.get("base_sha", "")))), "proposal.base_sha must be a full SHA", errors)
        require(bool(proposal.get("generated_at")), "proposal.generated_at is required", errors)
        for key in ("bundle_digest", "source_manifest_digest"):
            require(bool(SHA64.fullmatch(str(proposal.get(key, "")))), f"proposal.{key} must be sha256", errors)
        artifacts = proposal.get("artifacts")
        require(isinstance(artifacts, list) and bool(artifacts), "proposal.artifacts must be a non-empty array", errors)
        if isinstance(artifacts, list):
            artifact_ids: list[str] = []
            artifact_ids_valid = True
            for index, artifact in enumerate(artifacts):
                require(isinstance(artifact, dict), f"proposal.artifacts[{index}] must be an object", errors)
                if not isinstance(artifact, dict):
                    continue
                require(set(artifact) == ARTIFACT_KEYS, f"proposal.artifacts[{index}] has unexpected or missing properties", errors)
                artifact_id = artifact.get("artifact_id")
                if isinstance(artifact_id, str) and artifact_id:
                    artifact_ids.append(artifact_id)
                else:
                    artifact_ids_valid = False
                for key in ("artifact_id", "media_type", "resolver", "locator"):
                    value = artifact.get(key)
                    require(isinstance(value, str) and bool(value) and "\x00" not in value, f"proposal.artifacts[{index}].{key} must be a safe non-empty string", errors)
                require(artifact.get("resolver") == "repository_relative_file", f"proposal.artifacts[{index}].resolver is unsupported", errors)
                require(bool(SHA64.fullmatch(str(artifact.get("exact_bytes_sha256", "")))), f"proposal.artifacts[{index}].exact_bytes_sha256 must be sha256", errors)
            if artifact_ids_valid:
                require(len(set(artifact_ids)) == len(artifact_ids), "proposal artifact IDs must be unique", errors)
        required = {"bundle_id", "repository", "base_sha", "evidence_binding", "generated_at", "source_manifest_digest", "artifacts"}
        if required <= proposal.keys() and isinstance(artifacts, list) and all(isinstance(item, dict) and isinstance(item.get("artifact_id"), str) and item.get("artifact_id") for item in artifacts):
            require(proposal.get("bundle_digest") == canonical_bundle_digest(proposal), "proposal.bundle_digest does not match canonical preimage", errors)
        if isinstance(repo, dict):
            require(proposal.get("base_sha") == repo.get("base_sha"), "proposal.base_sha must match repository.base_sha", errors)
            expected_identity = repo.get("owner_repo") or (f"local:{repo.get('root')}" if repo.get("root") else None)
            require(proposal.get("repository") == expected_identity, "proposal.repository must match the apply repository identity", errors)

    approval = data.get("approval")
    require(isinstance(approval, dict), "approval is required", errors)
    approved_decisions: set[str] = set()
    approved_mutations: set[str] = set()
    if isinstance(approval, dict):
        owner = approval.get("owner")
        require(isinstance(owner, str) and owner in {"caller", "user"}, "approval.owner must be caller or user", errors)
        require(bool(approval.get("approved_at")), "approval.approved_at is required", errors)
        source = approval.get("source")
        source_kind = source.get("kind") if isinstance(source, dict) else None
        require(isinstance(source_kind, str) and source_kind in {"explicit_user_instruction", "typed_caller_context"} and bool(source.get("locator")), "approval.source must be trusted and traceable", errors)
        decision_values = approval.get("approved_decision_ids")
        mutation_values = approval.get("approved_mutation_ids")
        decision_ids_valid = isinstance(decision_values, list) and all(isinstance(item, str) and item for item in decision_values)
        mutation_ids_valid = isinstance(mutation_values, list) and all(isinstance(item, str) and item for item in mutation_values)
        require(decision_ids_valid, "approved decision IDs must be a string array", errors)
        require(mutation_ids_valid, "approved mutation IDs must be a string array", errors)
        if decision_ids_valid:
            approved_decisions = set(decision_values)
            require(len(approved_decisions) == len(decision_values), "approved decision IDs must be unique", errors)
        if mutation_ids_valid:
            approved_mutations = set(mutation_values)
            require(len(approved_mutations) == len(mutation_values), "approved mutation IDs must be unique", errors)

    decisions = data.get("decisions")
    require(isinstance(decisions, list), "decisions must be an array", errors)
    decision_ids: set[str] = set()
    if isinstance(decisions, list):
        for index, decision in enumerate(decisions):
            require(isinstance(decision, dict), f"decisions[{index}] must be an object", errors)
            if not isinstance(decision, dict):
                continue
            did = decision.get("id")
            require(isinstance(did, str) and bool(did), f"decisions[{index}].id is required", errors)
            if isinstance(did, str):
                require(did not in decision_ids, f"duplicate decision id: {did}", errors)
                decision_ids.add(did)
            category = decision.get("category")
            require(isinstance(category, str) and category in DECISION_CATEGORIES, f"decisions[{index}].category is invalid", errors)
            for key in ("statement", "rationale", "source"):
                require(bool(decision.get(key)), f"decisions[{index}].{key} is required", errors)
            require(decision.get("approved") is True, f"decisions[{index}] must be approved", errors)
        require(decision_ids == approved_decisions, "approval decision IDs must exactly match decision objects", errors)

    constraints = data.get("constraints")
    require(isinstance(constraints, dict), "constraints are required", errors)
    exact_paths: set[str] = set()
    github_allowed: set[str] = set()
    if isinstance(constraints, dict):
        exact_values = constraints.get("exact_paths")
        excluded_values = constraints.get("excluded_paths")
        github_values = constraints.get("github_actions_allowed")
        exact_valid = isinstance(exact_values, list) and all(isinstance(item, str) and item for item in exact_values)
        excluded_valid = isinstance(excluded_values, list) and all(isinstance(item, str) and item for item in excluded_values)
        github_valid = isinstance(github_values, list) and all(isinstance(item, str) for item in github_values)
        if exact_valid:
            exact_paths = set(exact_values)
        if excluded_valid:
            excluded_paths = set(excluded_values)
        if github_valid:
            github_allowed = set(github_values)
        require(exact_valid and len(exact_paths) == len(exact_values), "constraints.exact_paths must be a unique string array", errors)
        require(excluded_valid and len(excluded_paths) == len(excluded_values), "constraints.excluded_paths must be a unique string array", errors)
        require(github_valid and len(github_allowed) == len(github_values), "constraints.github_actions_allowed must be a unique string array", errors)
        require(constraints.get("no_product_code") is True, "no_product_code must be true", errors)
        require(constraints.get("no_commit_push_pr_merge_release") is True, "no_commit_push_pr_merge_release must be true", errors)

    mutations = data.get("mutations")
    require(isinstance(mutations, list), "mutations must be an array", errors)
    mutation_ids: set[str] = set()
    if isinstance(mutations, list):
        for index, mutation in enumerate(mutations):
            require(isinstance(mutation, dict), f"mutations[{index}] must be an object", errors)
            if not isinstance(mutation, dict):
                continue
            mid = mutation.get("id")
            action = mutation.get("action")
            target = mutation.get("exact_target")
            require(isinstance(mid, str) and bool(mid), f"mutations[{index}].id is required", errors)
            if isinstance(mid, str):
                require(mid not in mutation_ids, f"duplicate mutation id: {mid}", errors)
                mutation_ids.add(mid)
            require(isinstance(action, str) and action in ACTIONS, f"mutations[{index}].action is invalid", errors)
            require(mutation.get("allowed") is True, f"mutations[{index}] must be individually allowed", errors)
            require(isinstance(target, str) and bool(target) and "*" not in target, f"mutations[{index}].exact_target must be exact", errors)
            decision_values = mutation.get("decision_ids")
            decision_values_valid = isinstance(decision_values, list) and all(isinstance(item, str) and item for item in decision_values)
            refs = set(decision_values) if decision_values_valid else set()
            require(decision_values_valid, f"mutations[{index}].decision_ids must be a string array", errors)
            if decision_values_valid:
                require(len(refs) == len(decision_values), f"mutations[{index}].decision_ids must be unique", errors)
            require(bool(refs) and refs <= decision_ids, f"mutations[{index}] must reference approved decisions", errors)
            require(bool(SHA64.fullmatch(str(mutation.get("proposed_after_digest", "")))), f"mutations[{index}].proposed_after_digest must be sha256", errors)
            before = mutation.get("expected_before")
            require(isinstance(before, dict), f"mutations[{index}].expected_before must be an object", errors)
            state = before.get("state") if isinstance(before, dict) else None
            before_digest = before.get("digest") if isinstance(before, dict) else None
            require(isinstance(state, str) and state in {"absent", "present"}, f"mutations[{index}].expected_before.state is invalid", errors)
            require(before_digest is None or bool(SHA64.fullmatch(str(before_digest))), f"mutations[{index}].expected_before.digest must be null or sha256", errors)
            require((state == "absent" and before_digest is None) or (state == "present" and bool(SHA64.fullmatch(str(before_digest)))), f"mutations[{index}].expected_before state/digest mismatch", errors)
            if isinstance(action, str) and action in {"create_doc", "update_doc"}:
                require(target in exact_paths, f"doc target is outside constraints.exact_paths: {target}", errors)
                require(target not in excluded_paths, f"doc target is explicitly excluded: {target}", errors)
                root = Path(str(repo.get("root"))) if isinstance(repo, dict) and repo.get("root") else Path(".")
                require(resolve_repository_path(root, target) is not None, f"doc target escapes repository.root: {target}", errors)
                artifacts = proposal.get("artifacts", []) if isinstance(proposal, dict) else []
                payload_artifact_id = mutation.get("payload_artifact_id")
                require(isinstance(payload_artifact_id, str) and bool(payload_artifact_id), f"mutations[{index}].payload_artifact_id is required for doc mutations", errors)
                payload_bound = any(
                    isinstance(artifact, dict)
                    and artifact.get("resolver") == "repository_relative_file"
                    and artifact.get("artifact_id") == payload_artifact_id
                    and artifact.get("exact_bytes_sha256") == mutation.get("proposed_after_digest")
                    for artifact in (artifacts if isinstance(artifacts, list) else [])
                )
                require(payload_bound, f"mutations[{index}] doc payload is not bound to a proposal artifact", errors)
                require(action != "create_doc" or state == "absent", "create_doc requires an absent precondition", errors)
                require(action != "update_doc" or state == "present", "update_doc requires a present digest precondition", errors)
            elif isinstance(action, str) and action in {"create_issue", "update_issue"}:
                require(action in github_allowed, f"GitHub action lacks distinct authorization: {action}", errors)
                require(bool(repo.get("issue_snapshot_digest")) if isinstance(repo, dict) else False, f"{action} requires a bound issue_snapshot_digest", errors)
                observed_at = repo.get("observed_at") if isinstance(repo, dict) else None
                try:
                    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if isinstance(observed_at, str) else None
                except ValueError:
                    observed = None
                require(observed is not None and observed.tzinfo is not None and observed.utcoffset() is not None, f"{action} requires a timezone-aware repository.observed_at", errors)
                match = ISSUE_TARGET.fullmatch(target) if isinstance(target, str) else None
                require(bool(match) and match.group("repository") == repo.get("owner_repo"), f"{action} target must belong to repository.owner_repo", errors)
                require(action != "create_issue" or state == "absent", "create_issue requires an absent precondition", errors)
                require(action != "update_issue" or state == "present", "update_issue requires a present digest precondition", errors)
        require(mutation_ids == approved_mutations, "approval mutation IDs must exactly match mutation objects", errors)

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_decision_manifest.py <manifest.json>", file=sys.stderr)
        return 2
    try:
        data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    structural_errors = validate(data)
    payload_errors: list[str] = []
    if not structural_errors and isinstance(data, dict) and isinstance(data.get("proposal"), dict) and isinstance(data.get("repository"), dict):
        payload_errors = validate_artifact_payloads(data["proposal"], Path(data["repository"]["root"]))
    runtime_errors = validate_runtime_state(data) if isinstance(data, dict) and not structural_errors and not payload_errors else []
    errors = payload_errors + structural_errors + runtime_errors
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

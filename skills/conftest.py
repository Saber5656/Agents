from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_PARENT = Path(__file__).resolve().parent.parent

if str(REPO_PARENT) not in sys.path:
    sys.path.insert(0, str(REPO_PARENT))


# These fixtures assert the retired Saihai/flat-workflow contract.  Keep only
# the individual cases whose assertions target removed behavior so that a
# passing current test in the same module remains active and visible.
LEGACY_COMPATIBILITY_REASONS = {
    'skills/merge/tests/test_merge_skill_metadata.py::MergeSkillMetadataTest::test_skill_description_triggers_merge_phrases': 'Retired pre-current merge SKILL contract; successor: skills/merge/SKILL.md and tests/test_runner.py',
    'skills/merge/tests/test_merge_skill_metadata.py::MergeSkillMetadataTest::test_skill_documents_commit_first_default': 'Retired pre-current merge SKILL contract; successor: skills/merge/SKILL.md and tests/test_runner.py',
    'skills/merge/tests/test_merge_skill_metadata.py::MergeSkillMetadataTest::test_skill_documents_safety_boundaries': 'Retired pre-current merge SKILL contract; successor: skills/merge/SKILL.md and tests/test_runner.py',
    'skills/merge/tests/test_merge_skill_metadata.py::MergeSkillMetadataTest::test_skill_rejects_github_pr_merge_context': 'Retired pre-current merge SKILL contract; successor: skills/merge/SKILL.md and tests/test_runner.py',
    'skills/pr-merge-gate/tests/test_pr_merge_gate_skill.py::PrMergeGateSkillTest::test_adapter_is_thin_and_fail_closed': 'Retired Saihai PR merge-gate contract; successor: skills/pr-merge-gate/SKILL.md and tests/test_delivery.py',
    'skills/pr-merge-gate/tests/test_pr_merge_gate_skill.py::PrMergeGateSkillTest::test_artifact_boundary_and_redaction_are_fail_closed': 'Retired Saihai PR merge-gate contract; successor: skills/pr-merge-gate/SKILL.md and tests/test_delivery.py',
    'skills/pr-merge-gate/tests/test_pr_merge_gate_skill.py::PrMergeGateSkillTest::test_conflict_repair_is_not_a_merge_gate_or_readiness_bypass': 'Retired Saihai PR merge-gate contract; successor: skills/pr-merge-gate/SKILL.md and tests/test_delivery.py',
    'skills/pr-merge-gate/tests/test_pr_merge_gate_skill.py::PrMergeGateSkillTest::test_contract_route_and_executor_invocation_failures_are_distinct': 'Retired Saihai PR merge-gate contract; successor: skills/pr-merge-gate/SKILL.md and tests/test_delivery.py',
    'skills/pr-merge-gate/tests/test_pr_merge_gate_skill.py::PrMergeGateSkillTest::test_direct_merge_fallback_is_forbidden': 'Retired Saihai PR merge-gate contract; successor: skills/pr-merge-gate/SKILL.md and tests/test_delivery.py',
    'skills/pr-merge-gate/tests/test_pr_merge_gate_skill.py::PrMergeGateSkillTest::test_required_frontmatter': 'Retired Saihai PR merge-gate contract; successor: skills/pr-merge-gate/SKILL.md and tests/test_delivery.py',
    'skills/pr-merge-gate/tests/test_pr_merge_gate_skill.py::PrMergeGateSkillTest::test_uncertain_result_allows_read_only_reconciliation_only': 'Retired Saihai PR merge-gate contract; successor: skills/pr-merge-gate/SKILL.md and tests/test_delivery.py',
    'skills/pr-review-fix-policy/tests/test_batch_and_signal.py::WorkflowSafetyTests::test_skill_deprecates_commit_status_signal_as_evidence': 'Retired commit-status/Saihai signal contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/pull/tests/test_pull_skill_metadata.py::PullSkillMetadataTest::test_skill_description_triggers_pull_phrases': 'Retired pre-current pull SKILL contract; successor: skills/pull/SKILL.md and tests/test_runner.py',
    'skills/pull/tests/test_pull_skill_metadata.py::PullSkillMetadataTest::test_skill_documents_safety_boundaries': 'Retired pre-current pull SKILL contract; successor: skills/pull/SKILL.md and tests/test_runner.py',
    'skills/tests/test_control_plane_removal.py::ControlPlaneRemovalTest::test_workflow_contracts_require_explicit_saihai_inputs': 'Retired Saihai control-plane expectation; successor: skills/CURRENT-WORKFLOW.md and tests/test_runner.py',
    'skills/tests/test_gh_deliver_contract.py::GhDeliverContractTests::test_catalog_mapping_is_retained_in_skill_and_contract': 'Retired Saihai delivery catalog expectation; successor: skills/gh-deliver-remaining-issues/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_auto_review_only.py::PrAutomaticReviewOnlyTests::test_codex_work_monitor_registration_is_explicit': 'Retired automatic-review compatibility contract; successor: skills/pr/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_auto_review_only.py::PrAutomaticReviewOnlyTests::test_direct_reviewer_request_compatibility_is_preserved': 'Retired automatic-review compatibility contract; successor: skills/pr/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_auto_review_only.py::PrAutomaticReviewOnlyTests::test_missing_review_is_resumable_without_comment_retrigger': 'Retired automatic-review compatibility contract; successor: skills/pr/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_auto_review_only.py::PrAutomaticReviewOnlyTests::test_repository_automation_owns_normal_review_trigger': 'Retired automatic-review compatibility contract; successor: skills/pr/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_code_change_operations_have_safe_order': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_completion_evidence_is_reported_per_item': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_excluded_and_preexisting_outdated_threads_keep_fetched_state': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_explanation_only_does_not_require_empty_commit': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_handoff_binds_active_lineage_and_conditional_mutation': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_option_a_records_reply_and_resolution_authority': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_policy_phase_remains_read_only': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_push_induced_outdated_thread_is_a_runtime_v1_blocker': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_resolution_requires_reply_and_remote_success': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_thread_state_is_refreshed_before_each_mutation': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_review_fix_policy_resolution.py::PrReviewFixPolicyResolutionTests::test_top_level_comments_are_not_resolved': 'Retired Saihai review-resolution contract; successor: skills/pr-review-fix-policy/SKILL.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::PrPublicationSafetyTest::test_canonical_preflight_is_the_exclusive_push_owner': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::PrPublicationSafetyTest::test_coderabbit_is_initial_only_and_quota_fallback_is_narrow': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::PrPublicationSafetyTest::test_coderabbit_trigger_is_configured_current_head_and_typed': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::PrPublicationSafetyTest::test_expected_assignee_manifest_filter_fails_closed_before_mutation': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::PrPublicationSafetyTest::test_pr_conflict_repair_is_causal_bounded_and_does_not_authorize_merge': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::PrPublicationSafetyTest::test_ready_pr_transport_has_one_owner_and_push_is_negative_boundary': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::PrPublicationSafetyTest::test_review_absence_and_provenance_states_are_distinct': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::ReviewFixSafetyTest::test_current_head_and_provenance_are_required': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::ReviewFixSafetyTest::test_quota_fallback_and_conflict_repair_keep_review_policy_boundaries': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::ReviewFixSafetyTest::test_timeout_and_absence_are_not_collapsed': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::RemainingIssuesSafetyTest::test_manifest_supersession_and_outcome_delta_are_versioned': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::RemainingIssuesSafetyTest::test_pr_handoff_and_stop_boundary': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::RemainingIssuesSafetyTest::test_required_frontmatter_is_present': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_pr_workflow_safety_contracts.py::MergeContextSafetyTest::test_mixed_context_performs_no_local_or_pr_mutation': 'Retired Saihai publication safety contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_delivery.py',
    'skills/tests/test_security_professor_retirement.py::SecurityProfessorRetirementTest::test_skill_updater_does_not_select_a_security_review_provider': 'Retired security-provider routing contract; successor: skills/skill-updater/SKILL.md and skills/CURRENT-WORKFLOW.md',
    'skills/tests/test_trusted_local_profile_routing.py::TrustedLocalProfileRoutingTests::test_bounded_continuation_is_explicit_at_execution_boundaries': 'Retired Saihai profile-routing contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_runner.py',
    'skills/tests/test_trusted_local_profile_routing.py::TrustedLocalProfileRoutingTests::test_legacy_prerequisites_are_not_normal_route_prerequisites': 'Retired Saihai profile-routing contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_runner.py',
    'skills/tests/test_trusted_local_profile_routing.py::TrustedLocalProfileRoutingTests::test_normal_route_uses_the_host_usage_entrypoint': 'Retired Saihai profile-routing contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_runner.py',
    'skills/tests/test_trusted_local_profile_routing.py::TrustedLocalProfileRoutingTests::test_publication_contracts_declare_both_profiles': 'Retired Saihai profile-routing contract; successor: skills/CURRENT-WORKFLOW.md and tests/test_runner.py',
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        reason = LEGACY_COMPATIBILITY_REASONS.get(item.nodeid)
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"{reason}; fixture={item.name}"))

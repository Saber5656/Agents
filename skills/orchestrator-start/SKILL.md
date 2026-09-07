---
name: orchestrator-start
description: Handle an explicit request to start the current Agents execution service; never reactivate a retired harness from ordinary work or a skill-name invocation alone.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# orchestrator-start

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `skills/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Explicit service start

Ordinary actionable requests execute under current policy without an activation envelope. This skill name does not itself authorize re-enabling the retired TAKT/Saihai runtime, hooks, credentials or persistent configuration.

Read the requested outcome and current service installation/state. Start the current Agents service only if installation/start is explicitly requested or already included in the session scope. Use the documented current service CLI after its configuration, roots and subscription-only provider path are verified. Reuse an existing matching service/task instead of starting duplicate workers. Record actual service/process identity and recovery evidence.

If the user specifically requests the retired harness, explain the concrete difference and required configuration before changing anything; do not infer that request from a general orchestration prompt. Missing current service implementation is an implementation task or exact unresolved dependency, not a reason to run retired scripts.

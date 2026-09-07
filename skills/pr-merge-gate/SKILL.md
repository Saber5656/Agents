---
name: pr-merge-gate
description: Verify and merge an authorized GitHub PR against its current checks and reviewed changes, then synchronize main; not for local branch merges.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# pr-merge-gate

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `skills/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Verify and merge

Normal assigned development includes merge unless the user has narrowed it. A read-only PR inspection request alone does not authorize mutation. Use the current request as authorization; do not invent a manifest or approval-chain prerequisite.

Read the exact PR/base/head, required checks from native rules and branch protection, current check results and all unresolved current review threads. Failed or unavailable check discovery is incomplete. Evaluate and repair accepted findings; an old review of a different diff is not evidence for the current change. Re-read immediately before merge; changed head/base requires reconciliation and affected validation.

Use `python3 -m harness.delivery merge --repo owner/repo --pr N --head FULL_SHA --base FULL_SHA` from the canonical Agents checkout, or equivalent supported API checks. The operation pins expected head and respects native protection. Do not bypass protection or claim atomic base/merge-queue guarantees the API does not provide. An UNKNOWN mergeability state is recoverable pending evaluation.

Read back MERGED and merge SHA. On a lost response, inspect the same PR instead of issuing blind duplicate mutations. Fast-forward the designated clean canonical main to the verified remote branch containing that SHA using `harness.delivery sync`; preserve dirty/divergent/detached state with an exact blocker. Dependents wait for verified sync; independent units continue. Save context and reconcile/organize the actual matching App task before declaring normal delivery complete.

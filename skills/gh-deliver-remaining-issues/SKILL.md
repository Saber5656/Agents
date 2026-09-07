---
name: gh-deliver-remaining-issues
description: Implement remaining GitHub Issues through validation, review remediation, PRs, merge, main synchronization and chat organization, grouping independent work safely.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# gh-deliver-remaining-issues

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Deliver the existing backlog

1. Read current common policy, latest user requirements, Agents Vault context, GitHub Issues/PRs, git status/worktrees and prior work. Prefer current source facts over old backlog counts. Preserve existing work and avoid duplicate PRs or workers.
2. Track every Issue and acceptance criterion in the local task system. Group related Issues into useful work units/PRs; retain many-to-many links and individual outcomes. Keep dependencies, scope corrections and missing verification visible.
3. Prepare isolated worktrees at verified immutable bases. Parallelize only independent scopes, APIs and resources. Astra owns decisions; choose Luna for bounded routine delegation with explicit model/effort. Give children only necessary context, prohibit redelegation and prefer completion notifications. Do not repeatedly inspect unchanged state.
4. Execute TDD and pre-commit review, repair accepted findings automatically and verify actual outcomes. Reuse unaffected evidence. Delegated completion text alone is not proof of acceptance.
5. Use `commit`, `pr` and `pr-merge-gate` with existing Git/GitHub capabilities. Continue normal delivery through verified merge, canonical main synchronization and actual chat organization. A later explicit PR-only instruction narrows this endpoint.
6. New unrelated discoveries are captured locally, including before the local store is ready (durably in Vault and later imported). Workers do not issueize or implement them. A separately invoked batch agent owns issueization. Pending follow-ups never stop the originating unit.
7. Preserve progress before transitions. App closure is not cancellation. Run through the supported background service when installed, reconcile unknown processes/external outcomes before retry and retain UI-only operations pending until the App is available. Do not claim persistence merely because this chat is still running.

Finish by comparing every targeted Issue, parent and integration acceptance against actual PRs/commits, checks, installed versions and real App/CLI evidence. Keep mock/test/installed/merged/usable separate. Do not close real-integration requirements on fixtures alone. No arbitrary attempt/time threshold abandons the whole task; use durable backoff/recovery. Extra billing and concrete security violations halt only the affected operation, with evidence.

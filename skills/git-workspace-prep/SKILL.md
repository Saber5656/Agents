---
name: git-workspace-prep
description: Create or resume an isolated Git worktree for an assigned task while preserving existing work and verifying the immutable base.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# git-workspace-prep

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Prepare or resume

Inspect repository identity, origin fetch/push URLs, branch, worktrees, current changes and the latest remote base. Choose a task branch/worktree using the assignment; routine naming does not require a user decision. Pin the full immutable base SHA in the task record.

For a new task, use `harness.delivery.prepare_worktree` or equivalent `git worktree add -b` at the selected SHA. Verify actual cwd, branch and HEAD before starting the writer. For a resume, reuse the matching task/path/branch, verify ancestry and ownership of changes since base, and retain issue-owned commits and dirty work. Do not require a resumed HEAD to equal the original base.

Only one active writer may own a worktree/ref/resource. A separate worktree does not prove semantic independence. Existing conflicting ownership, an unknown external writer or missing base is an explicit queued reconciliation state. Do not reset, stash, delete or repurpose another checkout. Source tasks publish through PRs, never default-branch push. For explicitly requested visible App tasks use `codex-worktree-thread`; never prepare the same worktree twice through two interfaces.

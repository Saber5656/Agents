---
name: merge
description: Merge or reconcile branches in the requested local checkout while preserving intent; use pr-merge-gate for GitHub PR merging.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# merge

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Local merge

Identify whether the request means a local branch merge or a GitHub PR merge. Route an explicit PR URL/number to `pr-merge-gate`; do not consume it as a request to update every local repository.

For local work, inspect target branch, selected source commit, index, dirty files and existing commits before mutation. Preserve unrelated changes. Commit only when requested/already authorized, with `commit` validation and review; do not sweep dirty state into a checkpoint merely to make merge convenient.

Prefer a fast-forward when applicable. A requested history-preserving merge may create a merge commit. Resolve ordinary conflicts by preserving both intended behaviors and verifying the affected code. Ask only when a real specification choice cannot be established. Never reset, clean, force push, bypass hooks or silently change repositories. This local operation alone does not publish. Record source/result SHA, conflict decisions and checks.

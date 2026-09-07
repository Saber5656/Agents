---
name: commit
description: Commit reviewed task-owned changes in minimal reversible units when the user requests commit or an authorized delivery reaches commit.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# commit

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `skills/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Commit

1. Inspect the current branch, staged/unstaged/untracked changes and task scope. A natural-language request is sufficient context; do not ask for an internal artifact.
2. Group changes by one independently reversible intent. Keep behavior and its regression test together. Do not sweep unrelated work into the commit.
3. Use TDD for behavior changes: meaningful failing test, minimal implementation, passing focused checks. For documentation-only changes, explain why TDD is inapplicable and check references/consistency.
4. Review every intended commit before creating it, including correctness, scope, regressions, test adequacy, personal paths and secrets. Self-review is allowed unless independent review was requested. Fix accepted findings and recheck affected behavior without another permission question.
5. Stage explicit paths or hunks (`scripts/stage_approved_patch.py` can assist after inspecting its current interface). Inspect `git diff --cached` and ensure it is the reviewed change. Never use a whole-tree add for mixed work.
6. Commit using Conventional Commits. Do not bypass hooks, amend published history, or fabricate co-author/model provenance. Record exact commit, checks and review in Vault and the task.

A failing hook or inseparable unrelated hunk requires preservation and diagnosis, not a reset. This operation alone does not push or merge. Continue any broader already-authorized delivery afterward.

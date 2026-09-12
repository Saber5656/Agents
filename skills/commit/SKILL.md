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

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Commit

An assigned implementation or fix, including a one-line instruction edit, normally ends with a reviewed local commit unless the user explicitly requests uncommitted changes. Unrelated dirty files, staged hunks or unpublished commits alone do not justify stopping before commit. Isolate the task-owned change and finish its local commit; unresolved push permission or publication scope affects push, not that commit. Preserve concrete inseparable conflicts or failed validation with evidence, rather than treating all mixed work as blocked.

1. Inspect the current branch, staged/unstaged/untracked changes and task scope. A natural-language request is sufficient context; do not ask for an internal artifact.
2. Group changes by one independently reversible intent. Keep behavior and its regression test together. Do not sweep unrelated work into the commit.
3. Use TDD for behavior changes: meaningful failing test, minimal implementation, passing focused checks. For documentation-only changes, explain why TDD is inapplicable and check references/consistency.
4. Review every intended commit before creating it, including correctness, scope, regressions, test adequacy, personal paths and secrets. Self-review is allowed unless independent review was requested. Fix accepted findings and recheck affected behavior without another permission question.
5. Stage explicit paths or task-only patches and inspect `git diff --cached`; never use a whole-tree add for mixed work. If unrelated changes are already staged, capture the original index state, initialize an alternate index from `HEAD`, apply only the task patch there, and commit with the same `GIT_INDEX_FILE`. Then reconcile the committed task delta into the original index after verifying its captured preimage still matches. Preserve unrelated staged and unstaged changes, and ensure the new commit does not leave a staged reversal of the task. Reconcile concurrent index edits instead of overwriting them.
6. Commit using Conventional Commits. Do not bypass hooks, amend published history, or fabricate co-author/model provenance. Record exact commit, checks and review in Vault and the task.

Before reporting completion, read back the commit and its task-owned paths/hunks, and verify the intended unrelated staged/unstaged state is preserved. A pending push is reported separately from a completed local commit.

A failing hook or inseparable unrelated hunk requires preservation and diagnosis, not a reset. This operation alone does not push or merge. Continue any broader already-authorized delivery afterward.

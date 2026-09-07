---
name: pr
description: Publish or update a GitHub pull request from authorized local changes, verify English public content and current checks, and continue the requested delivery.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# pr

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Publish a PR

1. Confirm task branch, immutable base/head, origin destination, owned commits, validation and pre-commit review. Use `commit` for remaining task-owned changes. Group related Issues when useful, tracking acceptance separately.
2. Write a concise English title and body explaining the problem, behavior, validation and material limitations. Run privacy checks on body, diff and unpublished commits before transmission. Keep private context in Vault. Use `Closes #N` only for fully satisfied acceptance; otherwise `Refs #N`.
3. Search for an existing PR by repository/head/base before creation, including after a lost response. Reuse the same PR. Never create a second PR merely because a local receipt is missing. Push the reviewed task head and read back the remote SHA.
4. Create the requested PR through authenticated `gh` or supported connector. Preserve existing assignees/labels unless changes are requested or clearly part of the task. Read back actual number, URL, base/head and English body. Use a body file or structured arguments to preserve newlines safely.
5. Observe required current-head CI and existing review findings. Do not manually trigger additional review bots by default. Evaluate findings, fix accepted in-scope problems, run affected checks, push and recheck the original findings. Do not ask routine repair permission or stop after a numeric review count.
6. Continue to `pr-merge-gate`, main synchronization and chat organization under the normal delivery scope. Respect a later explicit PR-only restriction. Keep PR-created, checked, merged, installed and actually usable distinct.

An uncertain external create remains reconciliation-only until the existing PR can be confirmed or definitive noncreation is established. Do not use a missing legacy runtime as a reason to stop this workflow.

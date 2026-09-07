---
name: pull
description: Fetch and safely synchronize the currently requested local repository or explicitly selected repository set; do not broaden a short pull request to unrelated checkouts.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# pull

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Local pull

Resolve the requested checkout(s), remote, current branch/upstream and dirty/index state. A short request uses the current repository. Only an explicit all-repositories request expands the set, using discovered configuration and showing the actual scope in the record.

Fetch using existing credentials. For a clean branch with an unambiguous upstream, fast-forward to the verified remote commit. If dirty, diverged, detached or missing its intended upstream, preserve the state and record the concrete reconciliation needed. Do not automatically commit, stash, reset or delete local work. Already-authorized repairs can proceed separately with evidence.

This skill does not push, create PRs, merge GitHub PRs or alter protection. Post-PR canonical synchronization is owned by the delivery workflow and must verify the merge ancestry. Record per-repository actual outcome; one blocked checkout does not hide the others.

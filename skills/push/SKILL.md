---
name: push
description: Push an already validated and reviewed task branch when requested; PR delivery uses the PR workflow and existing GitHub authentication.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# push

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Push

Resolve the intended repository and exact fetch/push destinations from the current assignment and origin. Verify any URL rewrite and upstream before sending data. Freeze the selected commit SHA and compare all unpublished commits and paths against the task scope; inspect their contents and public metadata for personal paths and secrets.

Use the existing Git credentials to push the task branch without force.
The first push requires `git ls-remote --heads origin TASK_BRANCH`: an absent ref
can be created; an existing ref must be reconciled against the local task's
recorded branch/PR ownership and expected SHA before any mutation. An upstream
setting or a possible fast-forward does not prove ownership. Preserve a ref
owned by another task and select a non-colliding task branch. The shared
`GitHub.push_branch` helper rejects an existing different remote SHA; an
identical SHA is an idempotent readback, not permission to claim another task's PR.

A repository-specific policy may explicitly authorize a reviewed coordinator to
push its default branch; for Agents, follow
`policies/repository-delivery.md` and keep workers on task branches. Without
that explicit authorization, do not push the default branch. Never use an
obsolete repository whitelist, bypass hooks/protection or publish unrelated
commits. Read back the remote SHA and verify it equals the intended commit. If
the response is lost, query the ref before repeating. Divergence is preserved
and reconciled, not force-pushed away.

Do not ask again for already-authorized push. If PR creation is in scope, continue through `pr`; a successful push alone does not mean the task is complete. A genuinely new destination or missing publication authorization requires that concrete user decision.

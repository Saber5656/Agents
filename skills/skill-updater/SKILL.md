---
name: skill-updater
description: Improve an existing skill with a clear behavior brief, preserving installation, measured regression evidence, and actual consuming-surface verification.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---

# skill-updater

## Current execution contract

Read `$AGENTS_ROOT/COMMON-AGENTS.md` and `$SKILLS_ROOT/CURRENT-WORKFLOW.md` for the active workflow. The user's current scope and explicit restrictions take precedence. Retired Saihai intake, authority/manifest artifacts, fixed roles and approval gates are not prerequisites. Existing `references/` material that describes those mechanisms is historical reference, not an executable prerequisite. Do not start a retired runtime or install hooks/configuration as an incidental step.

Use existing changes and verified results. Preserve unrelated files, index entries and commits. Record original requirements, corrections, decisions, raw available evidence and limits in Agents Vault; link them from the local task. Separate new unrelated discoveries from repairs needed to finish the assigned work. Capture discoveries locally for the separate issueization batch; repair accepted in-scope defects autonomously.

## Update an existing skill

Resolve `$SKILLS_ROOT` and actual installed copies first. Record a short brief: current problem, desired behavior/output, behavior to preserve and objective checks. If those are already in the request, proceed without asking again.

Inspect current skill, references, helper scripts and existing tests. Preserve user customization and distinguish vendor packages. For executable behavior use meaningful TDD. For instruction-only changes, say why code TDD does not apply and evaluate the actual instruction behavior plus references. Review every change before commit under current common policy.

Use at least three relevant scenarios with baseline and changed outcomes; record expected/observed results and limits honestly. Store evals/gradings with `expectations` entries containing `text`, `passed`, `evidence`. Use the installed `skill-creator/scripts/aggregate_benchmark.py` where compatible to aggregate actual results. Deterministic file checks and model executions must be labeled separately; never fabricate model runs, timing or token evidence.

Apply scoped edits, fix accepted findings, run affected checks and one integrated validation per feature unit. Record complete available evidence privately in Agents Vault. Use preserving deployment with exact preimages (see `$AGENTS_ROOT/docs/skill-installation.md`), verify the effective digest from the consuming surface and retain rollback. Source edits alone remain deployment-pending. Report unresolved environmental/integration checks without claiming completion.

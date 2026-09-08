# pr-merge-gate

Fail-closed adapter for inspecting and merging an explicitly selected GitHub
PR, then synchronizing the verified result to `main`.

Use the current request and repository policy as authorization. Inspect the
exact repository, base/head, required checks, and current review threads. Fix
accepted findings and re-read the PR before merging. A missing or changing
head, unavailable checks, unresolved review, or uncertain mutation remains
pending and is reconciled by read-only inspection. Never infer readiness from
`mergeable`, `CLEAN`, comments, or a retired historical manifest.

The operation uses the supported `harness.delivery` or equivalent API, keeps
native branch protection and hooks active, and never force pushes or bypasses
repository rules. Afterward read back the merged commit and synchronized
remote `main`. See [SKILL.md](SKILL.md) for the current workflow and output
contract.

# product-v1-planner

One-repository v1 planning with a hard boundary between proposals and approved canonical changes.

## Modes

- `proposal`: draft DESIGN, ISSUE_PLAN, Issues, coverage, and decisions without mutation. A concept-only request is allowed but is labeled `unbound_concept` and cannot be applied.
- `audit`: check current docs and Issues for complete, executable v1 coverage.
- `approved-apply`: apply exact approved docs/Issue mutations only after fresh digest validation.

It does not implement product code, dispatch Issues, commit, push, open PRs, merge, or release.

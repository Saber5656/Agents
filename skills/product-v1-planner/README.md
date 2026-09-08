# product-v1-planner

One-repository v1 planning with a hard boundary between proposals and explicitly requested canonical changes.

## Modes

- `proposal`: draft DESIGN, ISSUE_PLAN, Issues, coverage, and decisions without mutation. A concept-only request is allowed but is labeled `unbound_concept` and cannot be applied.
- `audit`: check current docs and Issues for complete, executable v1 coverage.
- `apply`: apply exact requested local changes only after fresh context and target validation. Complete work units go to the current task system; Issueization owns derived GitHub Issue creation.

It does not implement product code, dispatch Issues, commit, push, open PRs, merge, or release.

# pr-merge-gate

Thin, fail-closed adapter for a Saihai-authorized GitHub PR merge under
`usage-first development operations`.

It activates for an explicit `/pr-merge-gate` or typed Saihai envelope request, then accepts execution only
with a trusted versioned contract reference, finalized manifest digest, and matching unexpired one-shot authorization. Saihai validates policy readiness and atomically consumes the authorization
while merging; for normal-risk work, required current-head CI is the quality gate and external review is not
required. Elevated-risk review is checked only when the finalized policy requires it. This skill never recomputes
policy and never falls back to direct `gh pr merge` or connector
mutation.

Opaque references are preferred. Any required adapter read uses one no-follow, beneath-root file descriptor
from validation through hash/read; bearer-sensitive authorization values remain redacted.
After an uncertain result, only the contract-fixed read-only Saihai reconciliation route may run; the mutation
executor is never retried with either the same or a new authorization.

Generic “merge” requests, PR URLs alone, local repository merges, GitHub `mergeable`/`CLEAN`, review
acknowledgements, and comments are not authorization. A valid finalized Saihai envelope may authorize autonomous
merge after required CI; no extra bot-review approval is added by this adapter.

See [SKILL.md](SKILL.md) for the envelope, typed blockers, replay rules, and output contract.

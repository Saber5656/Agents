# Runtime batch fixture

This fixture is an input and acceptance contract for one bounded, post-install
execution batch. It is not a model score and contains no precomputed success
result. Run each request with the current skill named in `batch.json`, write
the requested artifacts to a disposable output directory, and evaluate them
against `acceptance.md`.

Before running the audit case, make a disposable Git snapshot of
`fixture-repository`, then replace `<runtime-batch-root>` and
`<fixture-commit-sha>` in `requirement-correction-input.json` with its absolute
path and full commit SHA. The placeholder is intentionally not a claim about a
real Git commit.

The batch covers five independent behavior boundaries:

1. Decompose one product into three executable feature work units.
2. Correct a requirement while retaining the previous statement and rationale.
3. Keep an ordinary goal-shaped prompt inactive.
4. Critique a proposal without mutating a repository or publishing it.
5. Rewrite an explicitly supplied article draft while preserving factual TODOs.

Use a disposable directory outside the repository and record the executor,
model, source revision, installed skill digest, commands, raw outputs, and
pass/fail/blocked result. A missing artifact is a failed or blocked case; it
must not be replaced by a fabricated success JSON.

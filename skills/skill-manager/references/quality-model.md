# Skill Portfolio Quality Model

Report dimensions separately:

| Metric | Meaning |
|---|---|
| `contract_valid_rate` | skills with valid applicable metadata/schema/reference contracts |
| `responsibility_defined_rate` | skills whose owned and non-owned responsibility can be identified |
| `collision_free_rate` | skills without unresolved trigger-collision candidates |
| `duplicate_free_rate` | skills without unresolved semantic-duplicate candidates |
| `eval_coverage_rate` | skills with behavior evals |
| `assertion_coverage_rate` | evals with objective expectations/assertions |
| `benchmark_verified_rate` | skills whose benchmark is bound to the current contract digest |
| `routing_eval_coverage_rate` | skills with positive and near-miss negative routing tests |
| `least_privilege_rate` | skills whose declared tools fit owned responsibility |
| `control_plane_boundary_rate` | utility skills that do not invent organization authority |
| `new_debt_count` | findings introduced since approved baseline |
| `stale_evidence_count` | eval/benchmark evidence older than the current contract |

Do not publish a single composite score that can average away a blocker. If an evaluated contract digest is absent, benchmark freshness is `unverified`, not `fresh`.

## Duplicate reasoning rubric

Compare user intent, inputs, canonical source, decision authority, output artifact, mutations, completion gate, and downstream owner. Require substantial overlap across owned responsibility, not shared vocabulary. Label composition, alternative mode, pipeline stage, and platform-specific implementation separately.

## Single responsibility rubric

A skill may orchestrate several steps when they form one stable outcome and authority boundary. Flag it when unrelated outcomes, different approval owners, or independent mutation authorities are bundled such that one cannot change/review/revert the behavior independently.

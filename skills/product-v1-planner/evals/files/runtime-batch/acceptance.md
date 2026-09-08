# Runtime batch acceptance

The executor must run the five cases from `batch.json` in one bounded batch
using disposable output. Evaluate the artifacts, not a model's statement that
it followed the skill.

## Pass conditions

### three-feature-decomposition

- Exactly three separately executable feature units are present: capture,
  review/correction, and local export.
- Each unit has scope, acceptance criteria, validation, dependencies or an
  explicit none, and a non-goal.
- Bidirectional coverage maps every `REQ-*` to a unit and every unit to a
  requirement; one whole-product check proves the chain capture -> review ->
  export.
- No canonical file or Issue is mutated because the input is an unbound
  proposal.

### requirement-correction

- The old statement remains in the decision ledger with its rationale.
- The new scope is a linked `accepted` entry owned by `caller`; it is not
  silently replaced in place.
- The audit reports the stale Issue/document representation and does not edit
  either fixture file.

### ordinary-goal-nonactivation

- No persistent goal is activated and no worker/tool/file mutation occurs.
- The result states that this is an ordinary one-turn request with no durable
  Done contract, rather than inventing one.

### bounded-critique

- The critique identifies discarded provenance, unconditional publication,
  weak validation, and retry risk with evidence, impact, and bounded options.
- Unresolved product/privacy/reliability decisions remain questions; no
  repository or external publication mutation occurs.

### explicit-article-rewrite

- The revised draft is based only on the supplied draft and metadata.
- Unsupported claims (“every”, “proved”, “ready for everyone”) are softened or
  marked as TODOs; the exact missing command, environment, and source links
  remain visible.
- The output records executor/model/source material and says it was not
  published. No frontmatter or external publication is added unless requested.

## Failure classification

Missing output, a fabricated provider/model success string, or a fixture-only
claim without the named artifact is `blocked` or `failed`, never `passed`.
Textual contract tests and this fixture are supporting evidence; they do not
prove live App/CLI behavior or external publication.

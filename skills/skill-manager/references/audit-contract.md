# Skill Portfolio Audit Contract

## Snapshot identity

Bind every audit to an immutable repository revision and a canonical scope list. Compute `scope_digest` over sorted repository-relative paths and content digests. If source files change during the audit, return `audit_incomplete`; never combine observations from different revisions.

## Provenance profiles

Do not treat the same skill copied into plugin caches, system directories, symlinked install roots, and task worktrees as independent source skills. Record source identity, canonical repository, revision, and copy type.

Profile resolution order:

1. explicit caller mapping;
2. tracked repository provenance policy;
3. verifiable upstream package metadata;
4. `unclassified`.

## Deterministic findings

- YAML/frontmatter cannot be parsed.
- required portable or profile-specific field is missing.
- directory name and `name` disagree.
- duplicate active names exist in the same canonical source set.
- invalid status/category/profile enum.
- referenced local file, script, test, fixture, or eval input does not exist.
- eval JSON is invalid, names another skill, or repeats IDs.
- read-only contract declares obvious write/mutation tools without an explained evidence-output boundary.
- active contracts reference a removed/deprecated capability without migration evidence.
- benchmark claims freshness without a matching evaluated contract digest.

## Semantic review candidates

Semantic candidates require human/agent reasoning and must not be hard-coded as automatic deletion:

- duplicate user intent and output artifact;
- trigger collision or routing ambiguity;
- mixed responsibilities or control-plane ownership;
- merge/split/retirement opportunity;
- assertions that do not discriminate skill behavior;
- benchmark methodology that hides failure or variance.

## Baseline comparison

Finding identity is `rule_id + canonical sorted skill IDs + normalized evidence locator`. Compare current and previous reports:

- absent→present: `new`;
- present→present: `existing`;
- present→absent: `improved`;
- evidence cannot be compared: `unverified`.

Initial audits use `report_only`. After an approved baseline, `new_blockers` may fail CI only for deterministic new blockers. Semantic candidates require an approved policy before becoming blocking.

## Retirement evidence

A retirement proposal includes consumers, active references, docs, roles, scripts, tests, evals, migration target, capability parity, order of operations, rollback, and explicit approval owner. Reference count zero alone is insufficient.

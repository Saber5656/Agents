# Product V1 Planning Contract

## Source precedence

1. Explicit approved human/caller decisions and a valid Decision Manifest.
2. Existing approved repo-local canonical docs.
3. Repository implementation, tests, policies, and immutable history as factual evidence.
4. GitHub Issues/PRs as derived execution state.
5. New proposal text, which has no authority until approved.

When sources conflict, preserve the conflict and route it to the declared owner. Never convert prevalence or an existing implementation accident into product approval.

## Proposal bundle

```yaml
bundle_id: "<stable id>"
repository: "<owner/name or null>"
base_sha: "<immutable SHA or null>"
evidence_binding: bound_repository | unbound_concept
generated_at: "<timestamp>"
source_manifest_digest: "<sha256>"
artifacts:
  - artifact_id: "design_draft"
    media_type: "text/markdown"
    resolver: "repository_relative_file"
    locator: "<path or stable content ref>"
    exact_bytes_sha256: "<sha256>"
bundle_digest: "<canonical digest>"
```

`unbound_concept` is allowed only for noncanonical proposal output. It carries assumptions and unresolved decisions but no claims about current repository or GitHub state. It cannot be used as an approved-apply source until regenerated against one immutable repository/Issue snapshot and approved through a fresh Decision Manifest.

Every artifact descriptor is mandatory and binds the referenced payload by its exact-byte SHA-256; a locator is descriptive and is never sufficient evidence by itself. Approved apply supports only `repository_relative_file`: resolve each locator beneath `repository.root`, reject absolute paths, escapes, symlinks that resolve outside the root, missing/unreadable files, and every unsupported resolver. Sort artifacts by `artifact_id` and reject duplicate IDs.

The `bundle_digest` preimage is exactly the UTF-8 JSON serialization (sorted keys, separators `,` and `:`, `ensure_ascii=false`) of an object containing only `bundle_id`, `repository`, `base_sha`, `evidence_binding`, `generated_at`, `source_manifest_digest`, and the artifact descriptor array sorted by `artifact_id`. Exclude `bundle_digest` itself. Do not dereference or normalize locators while hashing: the independently verified `exact_bytes_sha256` binds the bytes. Any artifact byte change requires a new artifact digest, bundle digest, approval, and Decision Manifest.

For approved apply, proposal and apply target identities must match exactly: `proposal.base_sha == repository.base_sha`; when `repository.owner_repo` is present, `proposal.repository == repository.owner_repo`; otherwise `proposal.repository == "local:" + repository.root`. Do not translate aliases or silently rebind a proposal to another checkout.

Immediately before apply, resolve and hash every artifact's current exact bytes and compare them with `exact_bytes_sha256`; only after all payloads match may the bundle digest be recomputed. A locator payload mismatch or unresolved locator is `stale_decision_manifest`, even when the descriptor JSON and bundle digest are unchanged.

## Decision Ledger

Every material decision has an ID, category, statement, status, source, alternatives, rationale, impacted requirements/paths/Issues, and owner. Allowed categories include `v1_scope`, `requirement`, `architecture`, `api`, `schema`, `permission`, `security`, `compatibility`, `dependency`, and `defer`.

`proposed` does not authorize mutation. Only entries named by a valid approved Decision Manifest may be applied.

## DESIGN draft

Use only sections that fit the product, but cover:

- product outcome, users, v1 completion, scope, and non-goals;
- system/domain model, states, invariants, interfaces, and data ownership;
- permissions, privacy/security, failures/recovery, observability, and compatibility where relevant;
- validation strategy and unresolved decisions;
- traceable requirement IDs for the coverage map.

## ISSUE_PLAN draft

Include:

- v1 completion statement;
- Issue inventory with stable draft IDs;
- requirement-to-Issue and Issue-to-requirement coverage;
- dependency DAG and safe sequencing/waves;
- whole-product validation and integration gates;
- explicit v2/deferred scope and known unknowns.

Each Issue draft must be independently understandable and contain:

1. Title
2. Summary
3. Context and design references
4. Scope
5. Detailed requirements
6. Acceptance criteria
7. Validation
8. Dependencies
9. Non-goals

## Audit findings

```yaml
finding_id: "V1-..."
classification: missing_coverage | orphan_issue | contradiction | duplicate | stale_derived_state | weak_acceptance | weak_validation | dependency_gap | unresolved_decision
severity: blocker | high | medium | low
evidence: []
impact: "<why v1 completion or implementation is at risk>"
recommended_disposition: "<proposal only>"
decision_owner: caller | user
```

## Apply semantics

- Exact targets only; no glob or directory-wide authority.
- Immediately before approved apply, require the checkout's actual `HEAD` to equal `repository.base_sha`; a moved or unreadable worktree is stale and authorizes no mutation.
- Each mutation has its own `allowed: true`, action, target, approved decision IDs, explicit expected-before state, and proposed after digest.
- Every doc target and constraint path is repository-relative and resolves beneath `repository.root`. A doc mutation names `payload_artifact_id`; that proposal artifact is the immutable source payload and its exact-byte digest must equal the mutation's proposed-after digest. The artifact locator is not the destination path.
- Use `create_doc` only with `{state: absent, digest: null}` and `update_doc` only with `{state: present, digest: <sha256>}`. Apply must compare current existence and exact bytes immediately before writing.
- If a target already has the proposed-after digest, record an idempotent skip. Any other state/digest mismatch is `stale_target`; do not write that mutation or any later derived Issue mutation.
- Docs apply before derived Issue sync.
- A docs mutation cannot authorize `create_issue` or `update_issue`.
- Issue create and Issue update are distinct permissions.
- Issue targets must belong to `repository.owner_repo`, and Issue writes require a non-null timezone-aware `observed_at` plus the bound Issue snapshot digest.
- If base, source, proposal, target, or Issue snapshot is stale, do not write anything after the stale point.
- Validation is not a transferable write capability. The apply executor must re-open the parent/target without following symlinks, repeat containment and before-state checks at the write boundary, write through a same-directory temporary file, and atomically replace the destination. Abort on any inode/path change; never separate validation from a later unsafe write.
- Never implement product code, commit, push, create a PR, merge, or release.

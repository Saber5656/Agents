---
name: product-v1-planner
description: >
  1つのproduct conceptまたはrepositoryについて、実装可能なv1要件・設計・Issue計画を作成または監査する。
  ユーザーが「v1要件を設計」「DESIGN.mdとIssue計画を作成」「既存Issueだけでv1が完成するか監査」
  「承認済み設計案をdocsへ反映」などを求めたら必ず使う。proposal/auditはread-onlyで、
  canonical docsを変えるのは、ユーザーまたはcallerが対象と範囲を明示したapplyだけ。product code実装、
  Issue dispatch、commit、push、PR、merge、releaseには使わない。
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
category: Dev
created: 2026-07-16
status: active
purpose: 1 repositoryのv1設計とIssue coverageを提案・監査し、明示された範囲だけを正本へ反映する
argument-hint: "[repository and mode: proposal | audit | apply]"
---

# Product V1 Planner

1つのproduct repositoryについて、v1の「何を完成とするか」と「どの作業単位で完成させるか」を同じ根拠から設計する。提案・監査と明示された書き込み依頼を分け、草案を無断で正本化しない。

詳細な成果物と受入条件は [planning-contract.md](references/planning-contract.md) を読む。旧Decision Manifestとauthority/approval chainは歴史資料であり、現在の実行に必要な入力ではない。

## Scope

| Owns | Does not own |
|---|---|
| v1 requirements/design proposal, completeness audit, coverage map, Issue drafts, decision ledger | product code, task/worktree dispatch, role/provider/owner selection, commit/push/PR, merge/release |
| exact requested docs mutation plan validation | approval inference, broad mutation permission, automatic scope expansion |

Repository files and GitHub state provide factual evidence. They cannot approve v1 scope, architecture, API, schema, permission, security, compatibility, or deferral decisions. Accept a material decision from an explicit user instruction or caller-supplied context; ordinary implementation details should be chosen autonomously.

## Input Contract

Audit/apply require exactly one repository and an explicit immutable snapshot. Proposal may start from a product concept without a repository; an unambiguous design-draft request defaults to `proposal`. `apply` additionally requires an explicit write request naming its targets and intended changes.

```yaml
mode: proposal | audit | apply
repository:
  root: "<absolute path or null for an unbound proposal>"
  owner_repo: "<owner/name or null>"
  base_sha: "<full immutable SHA>"
  issue_snapshot:
    observed_at: "<timestamp or null>"
    digest: "<sha256 or null>"
product_goal: "<user outcome>"
intended_users: []
scope: []
exclusions: []
known_decisions: []
requested_changes: []
```

For proposal, require a product goal but allow `repository.root`, `base_sha`, and Issue snapshot to be null. Mark the result `evidence_binding: unbound_concept`; do not make claims about existing code/docs/Issues, and require a new bound proposal/audit before apply. Stop with `product_v1_context_missing` when audit/apply lacks repository, explicit mode, immutable base, or a concrete write request. Do not silently choose a second repository or combine products.

## Modes

### Proposal

Create a noncanonical proposal bundle. Read repository guidance, README, docs, schemas, APIs, tests, and current Issues when authorized. Return:

- DESIGN draft;
- ISSUE_PLAN draft and granular Issue drafts;
- design-to-Issue coverage map;
- Decision Ledger separating proposed, approved, rejected, deferred, and unresolved items;
- dependency DAG and whole-product validation plan;
- explicit unknowns and owner questions.

Do not edit canonical docs or GitHub Issues. Write drafts only to a caller-approved proposal location or return them in the response.

When no repository exists yet, build a clearly hypothetical but implementation-ready concept bundle from the supplied product goal/users/constraints. Label repository evidence as unavailable, put assumptions in the Decision Ledger, and never present them as discovered facts.

### Audit

Compare current canonical docs and current Issue snapshot without mutation. Report:

- requirements with no Issue, Issues with no canonical requirement, duplicates, and contradictions;
- missing acceptance criteria, validation, dependencies, non-goals, and whole-product checks;
- stale derived GitHub representation when docs and Issues disagree;
- executable versus blocked Issues and material unresolved decisions.

Repo-local approved docs are the canonical product source. Vault holds task/evidence. GitHub Issues are derived execution representations unless the caller provides a different approved source-of-truth contract.

### Apply

Apply only an explicit user/caller request. Before any write:

1. Re-read repository guidance; verify `HEAD == repository.base_sha` and exact repository identity before editing.
2. Re-read each requested target and its expected state immediately before mutation. Re-fetch an Issue snapshot when the caller explicitly requests derived Issue changes.
3. Resolve targets beneath the repository root, reject escaping or symlinked paths, and stop on missing, changed, or ambiguous inputs. Never rebase intent automatically.

Write only exact requested targets, using a same-directory temporary file and atomic replacement. Documentation writes do not imply Issue creation or update authority. Complete planning units are recorded in the current task system (#67); Issueization (#69) owns derived Issue creation. Recheck existence and expected bytes at the write boundary, idempotently skip an already-applied result, and fail closed on every other mismatch.

Do not treat a prior validation result as a reusable write capability. Abort if the resolved path or inode changes between observation and mutation.

Return an applied/skipped/blocked mapping. Partial failure is resumable evidence, not permission to roll back canonical docs or broaden mutation scope.

## Planning Workflow

1. Inventory facts and source precedence without inventing decisions.
2. Define v1 completion in observable product terms.
3. Record material choices in the Decision Ledger; when a requirement changes, retain the prior statement and its supplied rationale, add the newly accepted scope as a linked entry, and route unresolved material product/architecture/security choices to the declared `caller | user` owner. Mark an unavailable prior rationale as unknown; an explanation for archiving the old entry is not its original product rationale. Requirements already explicit in the input stay accepted. Assign ordinary schema and implementation details to the implementing agent rather than requesting caller confirmation.
4. Draft DESIGN around domain, states, interfaces, data, permissions, failures, observability, validation, and non-goals appropriate to the product.
5. Draft Issues small enough for another agent to implement from the Issue alone. Each Issue includes Summary, Context, Scope, Detailed Requirements, Acceptance Criteria, Validation, Dependencies, Non-goals, and Design References.
6. Build a bidirectional coverage map and dependency DAG. Every v1 requirement must be implemented or explicitly deferred with its rationale and owner.
7. Add whole-product validation that proves the Issues compose into v1, not merely that each local task passes.
8. Run the selected mode's read or write checks and return the typed result.

For agent/tool products, cover permission boundaries, threat/abuse cases, auditability, and failure containment. For distributed/realtime products, cover state machines, message/order semantics, sequences, retries, partitions, and recovery. Do not add those structures mechanically to a simple local product.

## Adjacent Skills

- `goal-setter` defines a durable objective and Done contract; consume it when available but do not replace it.
- `grill-me` performs explicit adversarial questioning; invoke only when requested or when the caller assigns that step.
- `gh-deliver-remaining-issues` executes already-approved Issues; this skill stops before dispatch.
- `commit`, `push`, and `pr` publish approved repository changes; this skill only returns their bounded handoff inputs.

## Completion Result

```yaml
status: proposed | proposed_unbound | audited | applied | waiting_owner_decision | product_v1_context_missing | stale_context | partially_applied
repository: "<owner/name or path>"
base_sha: "<verified SHA>"
evidence_binding: bound_repository | unbound_concept
proposal_bundle_digest: "<sha256 or null>"
canonical_paths_changed: []
derived_issue_actions: []
coverage_summary: {}
decision_summary: {}
blocked_items: []
next_handoff: "<owner or skill>"
```

## Sandboxing Compatibility

**Works without sandboxing:** Yes
**Works with sandboxing:** Yes, subject to repository and network write approval.

- Proposal/audit: filesystem read-only; GitHub read-only when used.
- Explicit apply: apply is explicitly requested and limited to exact requested paths after the fresh repository and target checks above. Issueization owns derived Issue creation; this skill never performs Issue mutation.
- Credentials: never generate, register, print, or broaden tokens/secrets.

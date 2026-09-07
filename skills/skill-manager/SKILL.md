---
name: skill-manager
description: >
  skills全体のread-onlyメタQAを行い、重複、責任の混在・所有競合、trigger衝突、metadata/reference/eval/test/benchmarkの劣化、
  least-privilegeやcontrol-plane境界違反を監視する。ユーザーが「skillを棚卸し」「重複skillを整理」
  「責任単一になっているか」「skills全体の品質を監査」「廃止・統合候補を確認」などを求めたら必ず使う。
  このskill自身は削除・統合・rename・description変更・廃止を実行せず、根拠付きproposalを
  skill-updater、skill-creator、または人間ownerへ渡す。単一skillの具体的な修正依頼だけならskill-updaterを使う。
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash
category: Dev
created: 2026-07-16
status: active
purpose: skill portfolio全体の重複・責務・trigger・品質をread-onlyで継続監査する
argument-hint: "[audit manifest or scope; mode: full | changed]"
---

# Skill Manager

`skill-manager`はskill portfolioの検査役である。作成は`skill-creator`、既存skillの変更は`skill-updater`、削除・rename・正本変更は人間承認に残し、このskillは監査証拠とhandoffだけを作る。

決定論的検査は `scripts/scan_skill_portfolio.py` を使う。監査契約は [audit-contract.md](references/audit-contract.md)、指標とsemantic QAは [quality-model.md](references/quality-model.md) を読む。

## Input Contract

Caller-supplied `Skill Portfolio Audit Manifest`を要求する。

```yaml
audit_id: "<stable id>"
repository_root: "<absolute tracked source root>"
revision_sha: "<immutable git SHA>"
scope:
  include: []
  exclude: []
profile_source:
  default: repo_native | upstream_compatible | unclassified
  skills: {}
previous_report: null
mode: full | changed
fail_policy: report_only | new_blockers
```

`revision_sha`と監査対象scopeはcleanなtracked treeへ一致させる。scannerはdirty scopeやHEAD不一致を拒否する。JSON/Markdown evidence pathは監査repository外を明示し、同一pathを使わない。

Scopeやsource rootが不明なときは、`.system/`、plugin cache、`.workspace/`、別worktree、symlink copyを勝手に同一母集団へ混ぜず、`skill_portfolio_context_missing`を返す。

## Portfolio Workflow

1. Verify repository root, immutable revision, scope, exclusions, and provenance profiles.
2. Run the deterministic scanner for inventory, metadata, references, eval/test structure, and hard contract checks. `changed` mode requires a previous report and classifies current findings as `new | existing`; disappeared findings are counted as improved.
3. Normalize each skill into responsibility, non-responsibility, trigger, input, output, mutation authority, owner boundary, tests/evals, and source provenance.
4. Review semantic duplicate, trigger collision, responsibility overlap, split/merge, and retirement candidates. These are proposals, never automatic mutations.
5. Compare with the previous audit when supplied. Separate `new`, `existing`, `improved`, and `unverified` findings.
6. Produce machine-readable JSON and human-readable Markdown with evidence, confidence, severity, impact, proposed disposition, and handler.
7. Route approved work: existing skill changes to `skill-updater`, new capability to `skill-creator`, ownership/canonical/delete/rename decisions to the human repository owner.

## Quality Areas

| Area | What to inspect |
|---|---|
| Inventory/provenance | tracked source vs upstream/plugin/cache/worktree copy; active/draft/deprecated status |
| Duplicate QA | same name, same trigger+artifact+workflow, redundant wrappers; exclude intentional composition/pipelines |
| Single responsibility | unrelated responsibilities, control-plane ownership, creator/updater/manager boundary |
| Trigger QA | under-trigger, over-trigger, adjacent collision, explicit negative boundary |
| Contract QA | frontmatter/profile, name/path, allowed tools, sandbox, reference/script/test paths, typed handoffs |
| Quality QA | eval schema and assertions, test coverage, benchmark freshness, stale docs, SKILL.md size |
| Retirement QA | active consumers, role/reference/eval/test dependencies, migration prerequisites, rollback |

Profiles matter:

- `repo_native`: enforce the repository TEMPLATE and local policy.
- `upstream_compatible`: enforce only the portable Codex skill contract; local metadata gaps are informational.
- `unclassified`: report provenance uncertainty instead of misclassifying it as noncompliance.

## Decision Rules

- Same name/path mismatch, invalid JSON, duplicate eval IDs, broken referenced files, or active references to a removed capability are deterministic findings.
- Similar wording, shared keywords, or “no references found” do not prove duplication or safe deletion.
- A parent workflow composing leaf skills is not a duplicate merely because it mentions the same actions.
- Pipelines with different sources, decisions, or outputs may be adjacent without overlapping responsibility.
- A semantic finding must show both overlapping owned responsibility and the user-visible consequence before proposing merge/split/retire.
- Never choose organization role, provider, approval owner, routing, or publication owner. Report missing/conflicting ownership.
- Do not compress blockers into one average quality score. Keep hard gates and per-area metrics visible.

## Output Contract

Write `portfolio-audit.json` and `portfolio-audit.md` to a caller-approved evidence path, not inside an audited skill directory unless that path is explicitly excluded.

```yaml
audit:
  id: "<audit id>"
  revision_sha: "<sha>"
  scanned_at: "<timestamp>"
  scope_digest: "<sha256>"
  previous_audit_id: null
inventory: []
findings:
  - finding_id: "SM-..."
    rule_id: "<stable rule>"
    category: duplicate | responsibility | trigger | contract | quality | retirement | provenance
    severity: blocker | high | medium | low
    confidence: certain | probable | possible
    skills: []
    evidence: []
    impact: "<why it matters>"
    proposed_disposition: "<proposal only>"
    handler: human | skill-updater | skill-creator
    approval_required: true
    baseline_state: new | existing | improved | unverified
summary:
  hard_gate_pass: false
  metrics: {}
  handoffs: []
```

Use exit code `0` for a valid report with no policy-blocking new finding, `1` for new blockers under an approved `new_blockers` policy, and `2` when the audit itself is invalid/incomplete.

## Mutation Boundary

This skill must not edit, rename, delete, merge, deprecate, or create skills. It must not rewrite descriptions, references, tests, evals, catalog files, or Git history. It must not automatically create Issues or PRs. An approved finding becomes a bounded handoff to the lifecycle owner; the receiving workflow revalidates current revision and scope.

## Completion Result

```yaml
status: audit_complete | audit_incomplete | waiting_human_decision | skill_portfolio_context_missing
revision_sha: "<sha>"
inventory_count: 0
new_blockers: 0
existing_findings: 0
improved_findings: 0
unverified_findings: 0
report_json: "<path>"
report_markdown: "<path>"
handoffs: []
```

## Sandboxing Compatibility

**Works without sandboxing:** Yes
**Works with sandboxing:** Yes

- Audited trees: read-only.
- Evidence output: caller-approved path only.
- Network: none unless the manifest explicitly includes remote evidence; remote state remains read-only.

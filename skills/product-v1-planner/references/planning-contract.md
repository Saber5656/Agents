# Product V1 Planning Contract

この契約は、product-v1-plannerが作る設計・作業単位・受入条件の形式を定める。提案と監査はread-onlyで、正本への反映はユーザーまたはcallerが明示した範囲だけで行う。旧Decision Manifest、authority/manifest、固定role、approval chainは履歴資料であり、現在の入力や書き込み権限ではない。

## Source precedence

1. 明示されたユーザーまたはcallerの決定・制約
2. 既存のrepo-local canonical docsと現在のpolicy
3. repositoryの実装、tests、immutable history（事実の根拠）
4. GitHub Issues/PRs（派生した実行状態）
5. 新しい提案文（採用されるまで提案）

根拠が競合する場合は競合を保持し、materialなproduct/architecture/securityの選択だけをownerへ返す。普段の実装詳細は自律的に選ぶ。

## Proposal and audit output

提案はcanonical docsやGitHub Issuesを変更せず、次を返す。

- DESIGN draft: outcome、対象ユーザー、v1 completion、scope、non-goals、domain、states、interfaces、failures、validation
- ISSUE_PLAN draft: work unitごとのSummary、Context、Scope、Detailed Requirements、Acceptance Criteria、Validation、Dependencies、Non-goals、Design References
- requirement-to-work-unitとwork-unit-to-requirementのcoverage map
- dependency DAG、安全な順序、whole-product validation
- Decision Ledger（proposed、accepted、rejected、deferred、unresolvedを区別）
- 事実、仮定、未確認事項、ownerへ返すmaterialな質問

三機能のような複数要求では、各機能を少なくとも一つの観測可能なacceptanceへ対応付け、全体を確認する統合チェックを一つ置く。作業単位は別のworkerが単独で実行できる粒度にする。

Repositoryに結び付かないconcept proposalは `evidence_binding: unbound_concept` と明示し、既存コード・docs・Issuesについて主張しない。repositoryのbound proposal/auditなしにapplyへ使わない。

## Apply contract

`apply`は、ユーザーまたはcallerが対象、目的、許容範囲を明示した場合だけ使う。開始時にrepository root、identity、immutable base SHA、対象のexpected stateを再確認する。Issue snapshotが必要な派生更新は、明示された場合だけ再読する。

- 対象はrepository root配下の明示されたファイルまたは明示されたtask recordに限る
- absolute path、root外、symlink経由、missing、unexpected digest、base移動はfail closed
- docsの書き込みはIssue作成・更新を許可しない
- 同じafter stateならskipし、それ以外の差分は停止して再評価する
- ファイルは同一ディレクトリのtemporary fileからatomic replaceし、観測と書き込みの間にpath/inodeが変われば停止する
- planningはcomplete unitsを現在のtask system（#67）へ記録できるが、派生GitHub Issueの作成はIssueization（#69）の責務とする
- product code、commit、push、PR、merge、release、公開操作はこのskillの責務外

部分失敗は証跡として保持し、rollbackや範囲拡大の理由にしない。結果は `applied`、`skipped`、`blocked` を対象ごとに返す。

## Decision Ledger

各material decisionは次の項目を持つ。

```yaml
id: "D-..."
category: v1_scope | requirement | architecture | api | schema | permission | security | compatibility | dependency | defer
statement: "..."
status: proposed | accepted | rejected | deferred | unresolved
source: "user | caller | repo evidence | open question"
rationale: "..."
impacted_requirements: []
owner: "caller | user | worker"
```

`accepted`は根拠とscopeを記録する状態で、旧式のmanifestや固定authorityを意味しない。要件変更では過去のstatementとrationaleを上書きせず、元のentryにリンクした新しいscope entryを追加する。unresolvedなmaterial choiceがある場合だけownerへ質問し、低リスクな実装詳細で作業を止めない。

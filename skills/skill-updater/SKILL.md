---
name: skill-updater
description: 既存スキルの修正・改善・最適化・挙動変更・description更新を行うときに必ず使う。ユーザーが「スキルを直したい」「既存スキルを改善したい」「SKILL.mdを更新したい」「このスキルの出力が不満」「トリガーを調整したい」「テストして改善したい」と言った場合は、明示的に skill-updater と言っていなくてもこのスキルを使う。usage-first development operationsに沿って、要件が明確なら再確認で停止せず修正し、修正後は eval / benchmark と統合検証が完了するまで完了宣言しない。
user-invocable: true
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
category: Dev
created: 2026-05-15
status: active
purpose: 既存スキルの改善要求を整理し、usage-first方針に沿って修正・テスト・ベンチマークまで完了させる
argument-hint: "[対象スキル名または改善内容]"
---

# Skill Updater

既存スキルを修正するときの専用ワークフロー。`skill-creator` と同じく eval / benchmark を必須にしつつ、修正前の改善ブリーフを明文化する。
通常のスキル変更では、レビュー・承認待ちを追加せず、focused validationを変更挙動ごとに行い、feature unitとして一度だけintegrated full validationを実行する。
permission expansion、authentication secret、data-loss riskだけは、指定されたproviderによる一度の限定review対象とする。通常の内部reviewとPR bot reviewを二重に起動しない。

## usage-first development operations

- IssueとPRは一対一でなくてもよい。関連するIssueを一つのfeature unitとして扱う場合は、全IssueをPR本文へリンクし、identity・scope・完了判定はPRごとに保持する。
- 通常の変更は、エージェントreview、CodeRabbit、ユーザー承認を待たず、必要なfocused validationとfeature unit単位のintegrated full validationを完了したら次の作業へ進む。
- レビューを使う場合は、initial review → 妥当なblocking findingの修正 → focused validation → 初回findingだけ再確認の一回限りとし、修正後にPR botを再起動しない。minor/improvementはfollow-up issueへ送る。
- CodeRabbitとChatGPTの通常併用は禁止する。ChatGPTはauthenticated quota-only receiptがある場合だけ、既存routeをinitial reviewの代替として一度利用できる。
- コンフリクトは意図を保持して自動修復し、影響範囲をfocused validationで確認する。要件・スコープ・設計の選択が必要な場合だけ確認する。
- 同じ未解決原因への再試行だけをboundedに行う。過去の記録不足、環境不足、累積レビュー数を理由に通常作業を永久停止しない。
- commit自体はレビュー対象にせず、目的・判断・検証・制約・リンクを記録する。Vaultの記録主体が親taskである場合は、そのhandoffを成果として扱う。

## When I Activate

- ユーザーが既存スキルの修正、改善、最適化、挙動変更、トリガー調整、description 更新に言及したとき
- ユーザーが特定スキルの出力、保存内容、判断、運用フローに不満や違和感を示したとき
- ユーザーが「このスキルをこう直したい」「前の挙動が微妙」「テストして改善して」と言ったとき
- 新規スキルをゼロから作る場合は `skill-creator` を使う。既存スキルの変更が含まれる場合は、このスキルを併用する

## Core Rule

既存スキルを修正するときは、次の3点を改善ブリーフへ明文化する。ユーザーの発言に含まれている場合は再質問せず、その理解を短く示して編集を始める。

| 確認項目 | 確認すること |
|---|---|
| 不満・改善点 | 既存スキルのどの挙動、出力、判断、トリガー、運用に不満があるか |
| 改善方針 | その課題をどのように直したいか。禁止したい挙動、増やしたい判断、残したい既存挙動は何か |
| 出力イメージ | 修正後にどのような最終アウトプット、保存内容、応答、ファイル、レビュー結果を期待しているか |

3点が不足し、要件・スコープ・設計の選択が成果物を変える場合だけ、A/B/Cで確認する。単なる作業順序、レビュー有無、検証の詳細は安全な既定値で進める。

## Workflow

1. **対象スキルの特定**
   - `~/dev/skills/<skill-name>/SKILL.md` を確認する。
   - 類似スキルや関連 README がある場合は必要最小限だけ読む。

2. **現状挙動の把握**
   - 現在の description、トリガー条件、出力形式、禁止事項、テスト資産を確認する。
   - 既存の eval / benchmark が `skills/.workspace/<skill-name>/` にあるか確認する。

3. **改善ブリーフの作成**
   - 修正前に次の形で短く整理する。

```markdown
## 改善ブリーフ

| 項目 | 内容 |
|---|---|
| 対象スキル |  |
| 現在の不満・改善点 |  |
| 望む改善方針 |  |
| 修正後の出力イメージ |  |
| 維持すべき既存挙動 |  |
| テストで確認すること |  |
```

4. **テスト設計**
   - 修正前または旧仕様相当を baseline とし、修正後と比較できる eval を2〜3件以上作る。既存evalがある場合は今回の方針境界を追加し、旧仕様の必須review/approvalを成功条件として残さない。
   - eval は `skills/.workspace/<skill-name>/evals/evals.json` に保存する。
   - 各 eval に客観的なアサーションを置く。主観評価だけで終わらせない。

5. **修正**
   - 既存の責務とスタイルを尊重し、必要最小限の差分で `SKILL.md` や関連 README / references を更新する。
   - description を変更する場合は、トリガーすべきケースと対象外ケースが誤解されないか確認する。

6. **テスト実行**
   - `skill-creator` の eval / benchmark 形式に従い、`skills/.workspace/<skill-name>/iteration-N/` に結果を保存する。
   - `with_skill` と baseline（`old_skill` または `without_skill` / 旧仕様相当）を比較する。
   - 各実行に `grading.json` を作成し、`expectations` は `text`, `passed`, `evidence` を使う。
   - `scripts.aggregate_benchmark` で `benchmark.json` と `benchmark.md` を生成する。
   - 可能なら `eval-viewer/generate_review.py --static` で `review.html` も生成する。

7. **条件付きレビュー**
   - 通常の変更は、レビュー待ちを完了ゲートにせず、要件・回帰リスク・テスト妥当性をfocused/integrated validationで確認する。
   - permission expansion、authentication secret、data-loss risk、または明示されたreview policyに該当する場合だけ、caller-supplied Saihai task contextまたはtyped artifactが指定するproviderによる一度の限定reviewを使う。provider が指定されていない場合は独自に選ばず、そのリスクの実装を開始せずcontext不足として返す。
   - 条件付きreviewでvalid blocking findingが出た場合は、承認済みscope内で修正し、focused validation後に初回findingだけ再確認する。新しいPR bot reviewや二重の内部reviewは起動しない。minor/improvementはfollow-up issueへ記録する。

8. **ドキュメント更新**
   - 設定済みの task/evidence vault または親taskが指定した記録主体に、改善ブリーフ、修正内容、テスト結果、残課題、制約を記録する。
   - 記録主体を親taskへhandoffした場合は、対象path、検証結果、未実行の検証、digestを明示し、このskill自身が別Vaultへ重複記録しない。

## Completion Criteria

次のすべてが終わるまで完了宣言しない。

| ゲート | 必須条件 |
|---|---|
| 擦り合わせ | 不満・改善方針・出力イメージが明文化されている |
| 修正 | 対象スキルの差分が要件に対応している |
| テスト | eval / grading / benchmark が作成されている |
| 条件付きレビュー | 高リスクまたは明示policyの場合だけ、指定providerの一度のreviewと初回finding再確認を完了している。通常変更は`not_required` |
| 統合検証 | feature unitの変更をまとめたintegrated full validationを一度実行している |
| 記録 | 設定済みのtask/evidence vaultへ保存、または親taskへ完全なhandoffを行っている |

## Output Format

完了報告は次の内容を簡潔に含める。

```markdown
## skill-updater 実施結果

| 項目 | 内容 |
|---|---|
| 対象スキル |  |
| 修正した不満・改善点 |  |
| 変更ファイル |  |
| Benchmark / integrated validation |  |
| 判定 |  |

成果物:
- evals: ...
- benchmark: ...
- review: `not_required` または条件付きreviewの証跡 ...
- Vault: 親taskへhandoff済み / ...
```

## Related Skills

- `skill-creator`: 新規スキル作成、eval / benchmark 基盤、description 最適化に使う。

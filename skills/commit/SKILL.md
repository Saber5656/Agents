---
name: commit
description: >
  gitの未コミット変更を分析し、適切なコミットメッセージを自動生成して実行するスキル。
  ユーザーが「コミットして」「commit して」「変更をコミット」「差分をコミット」と言ったとき、
  または /commit を実行したときに必ず使うこと。
  approved_scope / approved_diff_snapshot 付き Task Change Manifest または
  Publication Manifest が渡された場合も必ず使うこと。
  通常の変更はレビュー承認を待たずに、検証済みのスコープから自動でコミットする。混在する変更（複数の関心事が混ざっている場合）は適切なスコープに自動分割し、
  アトミックなコミットを複数作成する。
user-invocable: true
allowed-tools: Bash, Read, Skill
category: Dev
created: 2026-03-01
updated: 2026-06-16
status: active
purpose: task-owned validated diffだけをConventional Commitsで自動コミットし、scope外差分と高リスク未確認を混ぜない
argument-hint: "[追加のコンテキスト or コミット方針]"
---

# Git Auto Commit

git の未コミット変更を解析し、Task Change Manifest の `approved_scope` / `approved_diff_snapshot` に含まれる task-owned diff だけを、**Conventional Commits** 形式のコミットメッセージでコミットする。
変更が複数の関心事にまたがる場合は、自動的に分割してアトミックなコミットを作成する。通常の変更は
`usage-first development operations` に従い、focused validationが済めばcommitできる。レビューは
permission expansion、authentication secret、data-loss risk、または明示的なpolicyがある場合だけ一度
行い、PR botとの重複レビューはしない。

このスキルは repo 全体を clean にする責務を持たない。別タスク由来の dirty diff は `unrelated_dirty_paths` / `excluded_diffs` として記録し、この task の commit 対象へ混ぜない。

## Task Scope Precondition

このスキルは、repo 全体の dirty state を任意に保存するためのものではない。
`/commit`、`コミットして`、`変更をコミット` のような人間起点の依頼でも、まず task record と task-owned scope を確認する。

外部フロー上の起票、承認、routing、publication 判断はこのスキルでは決めない。
呼び出し元から明示的に渡された caller-supplied Saihai task context、`Task Change Manifest`、
`Publication Manifest` のいずれかを入力として扱う。このスキルは typed artifact を検証して commit だけを実行し、
role、approval owner、review provider、routing、publication ownership を独自に決めない。

次の全項目が確認できるまで、`git status`、`git add`、`git commit` を含む git 操作に進まない。

| Check | Required |
|---|---|
| `task_record_present` | Yes |
| `task_scope_confirmed` | Yes |
| `validation_status` | Yes for behavior changes; explicit non-impact evidence is sufficient for docs-only changes |
| `review_or_validation_status` | Conditional; required only when an elevated-risk review was actually dispatched |
| `publication_manifest_present` | Yes, when invoked by a publication flow |
| `task_change_manifest_present` | Yes |
| `approved_scope_present` | Yes |
| `approved_diff_snapshot_present` | Yes |

task record または task scope がない場合は、このスキルを実行せず `task_scope_missing` として停止する。
`approved_scope` または `approved_diff_snapshot` がない commit handoff は `unscoped_commit_forbidden` として拒否し、git 操作へ進まない。

## Execution profile boundary

Publication handoffs must carry exactly one typed `execution_profile`: `trusted_local_v1` or `legacy_managed`.
The profile controls external publication, not the local scope/snapshot checks owned by this skill. Under
`trusted_local_v1`, the host may run this skill inside
`python3.11 scripts/saihai.py usage run --request /absolute/request.json --authorization /absolute/authority.json --state-root /absolute/private-state`;
after this local commit/validation result, `host_publication_adapter` and bounded `usage advance` own branch push,
PR, CI, and merge. Existing host authentication is used; this skill does not require, create, copy, or repair a
root-owned broker, signer, or attestation. Under `legacy_managed`, preserve the explicitly selected legacy
publication/runtime contract. A missing or malformed profile is `publication_execution_profile_missing` for a
publication handoff; never switch profiles implicitly.

## When I Activate

- ✅ Task Change Manifest に approved scope / snapshot / commit_required があるとき
- ✅ Publication Manifest から commit handoff を受け取り、commit_required が記録されているとき
- ✅ `/commit` や「コミットして」が task context と scope を持つ作業として確定しているとき
- ✅ 変更をまとめて保存・記録したいタスクが task record 済みのとき
- ❌ ユーザーが `git push` や PR の作成を求めているとき（別の操作）
- ❌ ユーザーが「コミットメッセージだけ教えて」と言っているとき（実行はしない）

## What I Do

1. Task Change Manifest と現在 diff を照合する
2. 変更の全体像を把握する（staged/unstaged/untracked）
3. 最小関心事ごとにグループ化し、コミット単位を決める
4. permission expansion、authentication secret、data-loss riskがある場合だけ、task context指定の一度の
   security review contractを確認する。通常の変更はsecret scan/focused validationの結果で進める
5. 確認フェーズを挟まず、各グループを順番にステージ・コミットする
6. commit hash、対象ファイル、snapshot 照合結果、validation、必要なsecurity review、未コミット残差分、
   コミット不要判断を task record に記録する

---

## Step 1: Task Change Manifest の照合

task context から受け取った Task Change Manifest を最初に確認する。

| Field | Required | 内容 |
|---|---:|---|
| `repo_root` | Yes | commit 対象 repo |
| `task_id` | Yes | 親 task |
| `owned_paths` | Yes | この task が所有する path |
| `excluded_paths` | Yes | scope 外、別タスク、生成物など |
| `approved_diff_snapshot` | Yes | validation対象として固定した task-owned diff |
| `execution_profile` | When invoked by publication flow | `trusted_local_v1` or `legacy_managed`; immutable and provenance-bound |
| `reviewed_artifacts` | Yes | validation evidence。conditional reviewを実施した場合だけreview evidenceも含める |
| `commit_required` | Yes | `true` |
| `commit_hashes` | Later | commit 後に記録 |
| `unrelated_dirty_paths` | When applicable | repo に残る別タスク差分 |

現在の diff と `approved_diff_snapshot` を照合する。`approved` は人間またはreviewerの承認を意味せず、
commit対象として固定されたscopeを意味する。

| 判定 | 動作 |
|---|---|
| snapshot と現在の task-owned diff が一致 | 対象 diff だけ stage / commit する |
| 同一ファイルに unrelated hunk が混在し、approved hunk だけ stage 可能 | `scripts/stage_approved_patch.py` で approved patch だけを非対話 stage し、除外 hunk を `excluded_diffs` に記録する |
| snapshot と現在 diff がずれており、安全に approved hunk だけ stage できない | `scope_mismatch` として停止し、差分ずれを task record に記録する |
| approved snapshot がない | `unscoped_commit_forbidden` として停止する |

scope mismatch では、勝手に新しい差分を混ぜてコミットしない。

---

## Step 2: 変更状況の把握

```bash
git status
git diff --staged
git diff
```

未追跡ファイルがある場合は、コミット対象に含めるか判定する。
通常確認は挟まず、差分の意図、task record の scope、既存変更との関連から自動分類する。
scope 外、生成物、一時ファイル、別タスク由来と判断した未追跡ファイルはコミット対象から除外し、`excluded_diffs` に理由を記録する。
repo に残る unrelated dirty diff は `unrelated_dirty_paths` に記録するが、この task の commit 完了を妨げない。

---

## Step 3: 変更の分析とグループ化

差分を読んで、各変更ファイルと hunk の**変更の意図**を把握する。
コミット単位は「関連する塊」ではなく、**最小の意味ある関心事**にする。

次の単位が異なる場合は、同じディレクトリ、同じスキル、同じファイル内であっても原則として分ける：

| 分割軸 | 分ける例 |
|---|---|
| 目的 | 挙動変更と説明文整理 |
| 対象 | 複数スキル、複数モジュール、複数 policy |
| 変更種別 | 実装、テスト、eval、ドキュメント、設定 |
| リスク | security-sensitive 変更と通常の文言修正 |
| レビュー線 | 別エージェントまたは別チームのレビューが必要な変更 |

以下の観点でグループを作る：

| グループの基準 | 例 |
|--------------|-----|
| 同じ機能・モジュールの同じ目的の変更 | auth 実装と対応テスト |
| 同じ種類の修正 | 複数ファイルのタイポ修正 |
| 設定・ビルド系の変更 | package.json, tsconfig.json |
| テストとその対象コード | feature.py + test_feature.py |
| ドキュメントの更新 | README, docs/ |

**重要**: テストコードは、対応するソースコードと同じコミットにまとめるのが理想的。
テストだけが単独で意味を持つ場合（テスト修正のみ）は `test:` として分離する。

**重要**: 複数スキルや複数モジュールの変更を、単に「同じリポジトリの変更」として一括コミットしない。
同じ横断目的が明確で、分けると履歴の理解や revert が難しくなる場合だけ、横断コミットを許可する。

**重要**: 同一ファイル内に複数の関心事が混在する場合は、可能な限り hunk 単位で stage して分割する。
hunk 分割が技術的に困難な場合でも、scope 外または別タスク由来の hunk を混ぜてはならない。
非対話 hunk staging が必要な場合は、approved hunk の patch file を作り、`scripts/stage_approved_patch.py --repo <repo> --patch <patch> --owned-path <path>` で index にだけ適用する。
approved scope 内の関心事だけなら最小の妥協コミットにまとめ、理由を task record に記録できる。
scope 外 hunk を分離できない場合は `scope_mismatch` として停止する。

---

## Conditional Security Commit Review

通常のcommitにはreview承認を要求しない。permission expansion、authentication secret、data-loss risk、
または明示的なsecurity policyに該当する場合だけ、統合変更セットに対して一度の `Security Commit Review`
contractを適用する。PR botや別の通常reviewと同じ目的で重複させない。

review providerの決定はcaller-supplied Saihai task contextに記録された方針に従う。スキル側はproviderを
選択・推測・fallbackせず、該当する場合だけ、ステージ予定のファイル、差分概要、security-sensitiveな
変更点、除外予定差分に対して次のfieldsが揃っていることを検証する。

| Field | Required | 内容 |
|---|---:|---|
| `max_priority` | Yes | `P0` / `P1` / `P2` / `P3` / `none` |
| `findings` | Yes | 各リスクの priority、対象ファイル、根拠、推奨対応 |
| `commit_blocking` | Yes | `true` は `P0` 検出時のみ |
| `verdict` | Yes | `security_clear` / `security_notes` / `security_blocked` / `security_insufficient_input` |

該当レビューを実施した場合、その出力が欠けている、Priorityが不正、`commit_blocking`と`max_priority`が
矛盾する、または`verdict: security_insufficient_input`の場合は、コミットせず`security_review_invalid`と
してtask recordに記録する。レビュー対象外の通常変更では、このcontractを欠くこと自体をblockerにしない。

### Security Stop Rule

| Priority | commit 動作 |
|---|---|
| `P0` / data-loss | コミットを停止し、対象差分、検知理由、推奨対応を人間の要件判断へ返す |
| `P1` / `P2` / `P3` | task recordに記録し、scope内で修正可能ならfocused validation後に継続する |
| `none` | コミットを継続する |

高リスクの要件・設計選択が不要なら、通常の「実行しますか？」確認に戻さない。validなblocking findingは
一度だけ修正・focused validation・元のfinding再確認を行い、minor/improvementはfollow-up issueにする。

---

## Step 4: コミットメッセージの生成

### Conventional Commits 形式

```
<type>(<scope>): <subject>

[body]

[footer]
```

### type の選び方

| type | 使う場面 |
|------|---------|
| `feat` | 新機能の追加 |
| `fix` | バグ修正 |
| `docs` | ドキュメントのみの変更 |
| `style` | コードの意味に影響しない変更（フォーマット、空白など） |
| `refactor` | バグ修正でも機能追加でもないコード変更 |
| `perf` | パフォーマンス改善 |
| `test` | テストの追加・修正 |
| `build` | ビルドシステムや依存関係の変更 |
| `ci` | CI設定の変更 |
| `chore` | その他のメンテナンス作業 |

### scope の付け方

変更が影響するモジュール・機能・ディレクトリ名を使う。
例: `feat(auth):`, `fix(user):`, `docs(api):`, `build(deps):`

スコープが明確でない場合（プロジェクト横断的な変更など）は省略してよい。

### subject の書き方

- 現在形・命令形で書く（"add" not "added", "fix" not "fixes"）
- 先頭は小文字
- 末尾にピリオドなし
- 50文字以内に収める

### body（任意）

変更の**why**（なぜ）を説明する。whatはコードを読めばわかるので不要。
破壊的変更がある場合は `BREAKING CHANGE:` を記載。

---

## Step 5: Autonomous Commit Policy

人間起点の `/commit` や「コミットして」では、コミット前の計画提示と確認質問を挟まない。
task context、validated scope、diff snapshot、focused validation、および該当時のSecurity Commit Reviewの
P0判定をもとに、スキルが自動でコミット単位とメッセージを決めて実行する。通常の変更ではagentやbotの
review/approvalを待たない。

`approved_scope` なしの unscoped commit は禁止する。repo 全体の dirty state をそのまま commit 対象にしてはならない。

Publication Manifest からの handoff では、次を満たす場合に review / validation OK と manifest 検証をコミット承認として扱う。

| 条件 | Required |
|---|---|
| task record がある | Yes |
| validation status が `focused_ok` 相当 | Yes |
| Git Publication Manifest がある | Yes |
| publication flow が `commit_required: true` を検証している | Yes |
| Task Change Manifest がある | Yes |
| Approved Scope と対象ファイルが明示されている | Yes |
| Approved Diff Snapshot がある | Yes |
| 現在 diff が snapshot と一致、または approved hunk だけ明示 stage 可能 | Yes |
| `Security Commit Review` が必要な場合は存在する | Yes |
| 必要なsecurity reviewの`max_priority`が`P0`ではない | Yes |

unrelated diff、untracked file、scope 外の変更、別タスク由来の変更は自動で混ぜず、対象ファイルまたは hunk だけを明示 stage する。
判断した分類、除外差分、unrelated dirty paths、security notes は task record に記録する。

次の場合だけ自動コミットを停止する。

| 停止条件 | 対応 |
|---|---|
| `approved_scope` / `approved_diff_snapshot` がない | `unscoped_commit_forbidden` として差し戻す |
| 現在 diff と approved snapshot が不一致で、approved hunk だけ安全に stage できない | `scope_mismatch` として差し戻す |
| 必要なSecurity Commit Review が `P0` を検出 | ユーザー確認へ戻す |
| 必要なsecurity review contract が欠けている / 不正 / 情報不足 | `security_review_invalid` として差し戻す |
| merge / rebase / conflict 中 | 状態と次アクションを記録する |
| pre-commit hook 失敗 | 原因を調査し、必要なら修正担当へ差し戻す |
| git 権限や環境エラー | エラー内容を記録する |

P1以下のsecurity note、未追跡ファイルの自動除外、scope外差分の自動除外、repoに残るunrelated dirty diffでは、
ユーザー確認に戻さない。full validationは個々のcommitではなく統合変更セットで一度実施する。

---

## Step 6: コミットの実行

各グループを順番に処理する：

```bash
# グループ1のファイルをステージ
git add <files>

# コミット（heredocでメッセージを渡す）
git commit -m "$(cat <<'EOF'
feat(auth): add JWT authentication

JWTベースの認証を実装。セッション管理をステートレスにすることで
スケーラビリティを向上させる。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

**重要**:
- `--no-verify` は絶対に使わない。pre-commitフックが失敗した場合は原因を調査して修正する
- `git add .` や `git add -A` は避け、ファイルを明示的に指定する
- 共有ファイルで unrelated hunk が混在する場合は、`scripts/stage_approved_patch.py` または同等に明示的な方法で approved hunk だけを非対話 stage する
- コミットメッセージは heredoc 形式で渡す（特殊文字のエスケープ問題を避けるため）

## Step 7: task record への記録

コミット後、task record の Completion / Execution Log / Vault Updates のいずれかに次を記録する。

| Field | Required |
|---|---|
| `commit_hashes` | commit した場合は Yes |
| `committed_files` | Yes |
| `committed_diff_matches_snapshot` | Yes |
| `commit_message` | Yes |
| `security_review` | When elevated-risk review applies |
| `security_max_priority` | When elevated-risk review applies |
| `unrelated_dirty_paths` | scope 外 dirty diff が残る場合 |
| `excluded_diffs` | scope 外差分がある場合 |
| `scope_mismatch_reason` | snapshot 不一致で停止した場合 |
| `unscoped_commit_stop_reason` | approved scope / snapshot 不足で停止した場合 |
| `security_stop_reason` | P0 または review 不備で停止した場合 |
| `commit_not_required_reason` | commit 不要判断の場合 |
| `next_step` | publication flow に戻す、または publication_not_required を記録する |
| `execution_profile` | When publication flow | selected profile and host/legacy handoff status |

Publication Manifest から呼ばれた場合、commit 完了後は caller に commit result を返す。
commit hash と `committed_diff_matches_snapshot: true` 記録前に push / PR / `done` へ進めない。記録するのは
purpose、decision、validation、limitations、linksを中心とし、commit自体への追加reviewは要求しない。

---

## 分割コミットの例

### Before（混在した変更）

```
modified: src/auth.py        ← JWT認証を追加
modified: src/user.py        ← displayNameのタイポを修正
modified: tests/test_auth.py ← 認証テストを追加
modified: README.md          ← インストール手順を更新
```

### After（アトミックなコミット）

```
1. feat(auth): add JWT authentication
   → src/auth.py, tests/test_auth.py

2. fix(user): correct typo in displayName
   → src/user.py

3. docs: update installation instructions
   → README.md
```

---

## Edge Cases

**ステージ済みの変更がある場合**
`git diff --staged` でステージ済みの変更を確認し、ユーザーが意図的にステージしたものとして扱う。
ただし、unstaged の変更も分析し、同じ最小関心事なら同じコミットへ含める。別関心事なら分けるか除外理由を記録する。

**変更がない場合**
`git status` を確認し、変更がなければその旨を伝えて終了する。

**マージ中・リベース中**
`git status` で状態を確認し、マージ/リベース中の場合は適切なアドバイスを提供する。

**大量のファイル変更**
ファイル数が多い場合（10ファイル超）は、まず diff の概要を確認してから詳細を読む。

---

## Sandboxing Compatibility

**Works without sandboxing:** ✅ Yes
**Works with sandboxing:** ⚠️ 要注意（git コマンドはリポジトリの書き込みが必要）

- **Filesystem**: リポジトリ内の読み書き
- **Network**: None
- **Configuration**: git が設定されていること

## Related Tools

- `push`: commit 後の branch push 可否判定と実行
- Saihai caller: task context / typed artifact / policy decision を明示的に供給する control plane

- **simplify skill**: コミット前にコードを整理したい場合
- **`/code-review` skill**: PR作成前にコードをレビューしたい場合

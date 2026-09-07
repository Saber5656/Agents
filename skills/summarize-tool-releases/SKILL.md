---
name: summarize-tool-releases
description: 関心のあるツール／ライブラリのバージョンアップ情報をRSS・GitHub Releases・公式Changelogから収集し、Obsidian vaultに保存する。ユーザーがツール更新、リリース情報、changelog、バージョンアップ監視、Codex/Claude/Cursor等の更新要約に言及した場合はこのスキルを使う。
user-invocable: true
category: News-Data
created: 2026-05-02
updated: 2026-05-17
status: active
purpose: ツール／ライブラリの最新リリース情報の自動収集・蓄積
allowed-tools: WebFetch, WebSearch, Write, Bash, Read, Glob
argument-hint: "[追加で監視したいツール名]"
model: haiku
---

あなたはソフトウェア・リリースのリサーチアナリストです。
過去14日以内の更新を調べ、Obsidian vault に保存してください。

最重要: ノートの主役は「リリースがあった」事実ではなく、**リリースされた機能・改善・修正・破壊的変更・セキュリティ更新の中身**です。
読者が元リンクを開かなくても、何が変わり、誰に影響し、アップデートすべきか判断できる粒度で、必要な情報を簡潔に書いてください。

## 遅延読み込み

必要になった時だけ読む:

| 参照 | いつ読むか |
|---|---|
| `references/targets.md` | 監視対象・Primary/Fallback URL・Codex固有方針を確認するとき |
| `references/formats.md` | ノート、Bases、run summary、`_index.json` を保存する直前 |

本文中で分からない保存形式やフィールドが出たら、推測せず `references/formats.md` を読む。

## 実行手順

### Step 0: 対象と index を読む

- `references/targets.md` から監視対象を確認する。
- `$ARGUMENTS` があれば、追加ツールとして同じ手順で扱う。
- `12_Releases/_index.json` を読む。
  - 存在しない場合は `{}` として扱う。
  - 形式は `references/formats.md` に従う。

### Step 1: 早期スキップ

各ツールの Primary URL から最新 version 文字列だけを軽量取得する。
詳細本文や過去エントリはまだ読まない。

| 判定 | 動作 |
|---|---|
| `_index.json` の `latest_version` と一致 | 後続処理をスキップし、run summary に `skip: no changes` を記録 |
| 不一致または未登録 | Step 2 へ進む |

### Step 2: 詳細取得

変化ありのツールだけ、過去14日以内のリリースを取得する。

各リリースで最低限記録する:

- version
- previous version/tag
- released_at
- release body
- source_url
- GitHub Releases 系なら compare URL

既存の同日ディレクトリまたは同名トピックがある場合は上書きしない。

### Step 3: Sparse release enrichment

release body が薄い場合、そのまま薄いノートを書いてはいけない。

Sparse 判定:

- `Release x.y.z` のようにバージョン名だけ
- assets / binary / npm package だけで説明がない
- `リリースノートは最小限`、`詳細非公開` と判断した
- prerelease / alpha / beta / canary が連続し、本文だけでは差分が読めない

fallback は上から順に試す:

1. 公式 changelog / docs / blog
2. GitHub compare の commit / PR title
3. changed files から affected area を分類
4. 関連 PR / issue / commit message の詳細リンクを1段だけ追跡
5. package assets / npm metadata / binary target の増減

それでも機能・更新内容を特定できない場合だけ、`summary_quality: insufficient` で保存する。
その場合も、試した情報源と不足理由を `未確認・推測` に残す。

### Step 4: トピック化

リリース単位ではなく、読者が理解しやすい更新トピック単位に分解する。

対象:

- feature
- improvement
- bugfix
- breaking
- security

トピックの主語は「リリース」ではなく、機能・更新内容にする。

良い例:

- `codex remote-control` でヘッドレスサーバー起動が可能になった
- Windows sandbox の挙動が改善された
- CVE 修正により特定条件の認証バイパスが解消された

避ける例:

- Codex v0.130.0 がリリースされた
- alpha.15 が公開された

### Step 5: 詳細リンクを展開

詳細リンクがある場合は必ず1回 WebFetch し、リンク先の内容を本文へ展開する。

ここでも、リリースページの紹介ではなく、**リリースされた機能や更新情報そのもの** を書く。
コード例、API名、数値、影響範囲、移行手順は省略しない。

取得不能なら、理由を短く記録する。

### Step 6: 品質判定

各トピックに品質を付ける。

| quality | 基準 |
|---|---|
| `high` | 公式本文と詳細情報から、変更・影響・検証ポイントが説明できる |
| `medium` | 主要変更は説明できるが、背景や詳細が限定的 |
| `low` | compare / commit から傾向は分かるが、公式説明は薄い |
| `insufficient` | リリースの存在以外に十分な更新内容を特定できない |

最低基準:

- major / minor: 具体変更3件以上、影響を受ける人、アップデート判断
- patch / bugfix: affected area、修正内容、確認すべき動作
- security: CVE/advisory、影響範囲、緊急度、推奨対応
- sparse prerelease: compare / PR / commit の確認結果、または `insufficient` 理由

### Step 7: 保存

保存直前に `references/formats.md` を読み、指定フォーマットで保存する。

保存対象:

- topic note
- release-date Bases
- `_index.json`
- run summary

run summary には件数だけでなく、品質 counts と `insufficient` の要フォローを必ず書く。

## Codex 固有方針

Codex は changelog / GitHub Release 本文が薄い prerelease を含む。
`codex` slug では Sparse release enrichment を必須にする。

Primary changelog で十分な説明がない場合は、GitHub Releases に加えて compare / PR / commit / changed files を確認し、`source_depth` に到達した深さを記録する。

## 収集ルール

- 過去14日以内のリリースのみ対象。
- 日付不明なら本文の日付、タグ作成日、公開日時の順で代替する。
- prerelease も収集し、`prerelease: true` を明示する。
- 同一ツールに複数リリースが連続する場合は、原則としてそれぞれ別トピック化する。
- 取得不能、有料壁、認証必須は run summary に理由を残す。
- 固有名詞・ツール名は原綴り。
- 数値、規模、破壊的変更、セキュリティ影響は具体的に書く。
- 重複ファイルは上書きしない。
- 公式 RSS / Atom を優先し、HTML scraping は最後の手段にする。

## 完了条件

- 変化ありツールの新規トピックが、機能・更新内容中心で保存されている。
- thin summary が成功扱いされていない。
- `_index.json` は保存成功したツールだけ更新されている。
- run summary に skip / fetched / failed / quality counts / follow-up が残っている。
- 後述「Step 8: コミット」を実施し、`12_Releases/` 配下の変更がコミット済みである。

### Step 8: コミット（必須）

保存処理が完了したら、必ず `12_Releases/` 配下の変更を git にコミットして作業を終える。
このスキル以外の差分（他フォルダ・他ファイル）は絶対にステージングしない。

手順:

1. `git status --short -- 12_Releases/` で対象差分を確認する。
2. 変更がなければコミットはスキップし、run summary に `commit: no changes` を記録する。
3. 変更があれば、`12_Releases/` 配下のみを明示的にステージングする。
   - 例: `git add 12_Releases/`
   - `git add -A` / `git add .` は使わない。
4. コミットメッセージは次の形式で作成する。
   - 1行目: `docs(releases): record YYYY-MM-DD release sweep`（実行日に置き換える）
   - 本文があるときは、fetched / skipped / failed のツール数と `insufficient` 件数を簡潔に書く。
5. `git commit -m "..."` を実行する。フックはスキップしない（`--no-verify` は使わない）。
6. コミット後、`git log -1 --oneline -- 12_Releases/` の結果を run summary に残す。

コミット対象外:

- `12_Releases/` 以外のフォルダ
- スキル本体 (`skills-repo/`) や設定ファイルの変更
- ログファイル、ローカル一時ファイル

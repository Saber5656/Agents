# スキル作成テンプレート集

> 新しいスキルを作成する際のコピー&ペーストテンプレート

スキルの種類に応じて3種類のテンプレートを用意しています。

---

## Frontmatter フィールド一覧

| フィールド | 必須 | 説明 |
|-----------|:----:|------|
| `name` | ✅ | スキルID（ディレクトリ名と一致させる） |
| `description` | ✅ | Claude Code の自動検出トリガー。「いつ使うか」を明確に書く（TRIGGER/DO NOT TRIGGER 形式推奨） |
| `user-invocable` | ✅ | `true` = `/スキル名` で直接呼び出し可、`false` = 自動トリガーのみ |
| `allowed-tools` | ✅ | 使用するツールのみ列挙（例: `Read, Grep, Bash, Write`） |
| `category` | ✅ | Obsidian 管理用カテゴリ（後述） |
| `created` | ✅ | 作成日 `YYYY-MM-DD` |
| `updated` | — | 最終更新日 `YYYY-MM-DD` |
| `status` | ✅ | `active` / `draft` / `deprecated` |
| `purpose` | ✅ | 一行の目的説明（日本語可） |
| `argument-hint` | — | 引数ありスキルのみ（例: `"[追加メモ]"`） |

### カテゴリ一覧

| カテゴリ | 用途 |
|---------|------|
| `Dev` | 開発支援・コードレビュー・フレームワーク検証 |
| `Obsidian` | Obsidian vault 操作 |
| `Security` | セキュリティレビュー・監査 |
| `Utility` | 汎用ユーティリティ（ドキュメント生成など） |
| `Operation` | セッション操作・実行制御（context リセット、プロセス制御など） |
| `News-Data` | ニュース・リリース情報の収集と要約 |

---

## ディレクトリ構成

```
skills/
├── TEMPLATE.md          ← このファイル
├── my-skill/
│   ├── SKILL.md         ← メイン定義（必須）
│   └── README.md        ← クイックリファレンス（推奨）
└── my-skill-with-refs/  ← 長いスキルは references/ に分割
    ├── SKILL.md
    ├── README.md
    └── references/
        ├── patterns.md
        └── examples.md
```

### 命名規則

| 種別 | パターン | 例 |
|------|---------|-----|
| 汎用スキル | `kebab-case` | `code-reviewer` |
| Obsidian 関連 | `obsidian-*` | `obsidian-markdown` |
| ツール連携 | `tool-name` そのまま | `defuddle` |

---

## Template 1: ユーザー呼び出しスキル（基本形）

`/スキル名` で明示的に呼び出す。特定の作業を開始する際のワークフローを定義する。

### SKILL.md

```markdown
---
name: my-skill
description: >
  （スキルの目的と呼び出しタイミングを説明する）
  ユーザーが `/my-skill` と入力したとき、または〇〇したいときに使う。
user-invocable: true
allowed-tools: Read, Grep
category: Dev
created: YYYY-MM-DD
status: active
purpose: （一行で目的を記述）
argument-hint: "[オプション引数]"
---

# My Skill

（このスキルが何をするかを1〜2文で説明する）

## When I Activate

- ✅ ユーザーが `/my-skill` を実行したとき
- ✅ 〇〇ファイルを編集しているとき
- ✅ ユーザーが「〇〇」と言及したとき
- ❌ 〇〇の場合は対象外

## What I Do

1. 〇〇を確認する
2. 〇〇を実行する
3. 結果を〇〇の形式で出力する

## Examples

### Example 1: 〇〇のケース

```language
// ユーザーの入力:
（例）

// 出力:
（例）
```

## Sandboxing Compatibility

**Works without sandboxing:** ✅ Yes
**Works with sandboxing:** ✅ Yes

- **Filesystem**: Read-only
- **Network**: None
- **Configuration**: None required

## Best Practices

1. （実践上のポイント1）
2. （実践上のポイント2）
3. （実践上のポイント3）

## Related Tools

- **〇〇 skill**: （関連スキルと用途）
```

### README.md

```markdown
# My Skill

> （一行のキャッチコピー）

## Quick Example

```language
// スキルの動作を示す最小限の例
```

## What It Does

- ✅ 機能1
- ✅ 機能2
- ✅ 機能3

## Triggers

- `/my-skill` で直接呼び出し
- 〇〇を検知したとき

See [SKILL.md](SKILL.md) for full documentation.
```

---

## Template 2: 自動トリガースキル

コードのインポート文やファイルパターンを検知して自動的に適用される。
`description` に TRIGGER/DO NOT TRIGGER を明記する。

### SKILL.md

```markdown
---
name: my-framework-skill
description: >
  TRIGGER when: コードに `my-framework` がインポートされている、または `.mf` ファイルを編集するとき、
  ユーザーが「〇〇」と言及したとき。
  DO NOT TRIGGER when: 他のフレームワークを使用している場合、または一般的なプログラミングタスク。
user-invocable: false
allowed-tools: Read, Grep, Glob
category: Dev
created: YYYY-MM-DD
status: active
purpose: 〇〇フレームワーク使用時にベストプラクティスを自動適用する
---

# My Framework スキル

〇〇フレームワーク使用時に規約の遵守と最適化を自動チェックする。

## When I Activate

- ✅ `my-framework` がインポートされているファイルを編集するとき
- ✅ `app/` ディレクトリ内のファイルが変更されたとき
- ✅ ユーザーが「〇〇」「〇〇」と言及したとき
- ❌ 他のフレームワークを使用している場合は不要

## What I Check

### チェック項目 1: 〇〇

**問題のあるコード:**
```typescript
// 避けるべき書き方
const bad = doSomethingBad()
```

**推奨する書き方:**
```typescript
// 推奨する書き方
const good = doSomethingGood()
```

**理由**: 〇〇のためにこの書き方を推奨する。

### チェック項目 2: 〇〇

（同様に続ける）

## Detection Logic

```javascript
// 検出パターン
const patterns = {
  violation1: /badPattern/i,
  violation2: /anotherBadPattern/,
}
```

## Relationship with @architect

**Me (Skill):** リアルタイムの規約チェック
**@architect (Sub-Agent):** 全体アーキテクチャの詳細レビュー

### Workflow
1. スキルが規約違反をリアルタイムで検出
2. 深いレビューが必要な場合はユーザーが **@architect** を呼び出す

## Sandboxing Compatibility

**Works without sandboxing:** ✅ Yes
**Works with sandboxing:** ✅ Yes

- **Filesystem**: Read-only（`app/` ディレクトリ）
- **Network**: None
- **Configuration**: None required

## Best Practices

1. （実践上のポイント1）
2. （実践上のポイント2）

## Related Tools

- **@architect sub-agent**: 全体設計レビュー
- **〇〇 skill**: 関連する別のスキル
```

---

## Template 3: 複合ワークフロースキル

複数ステップの作業手順、外部ツール連携、またはローカル運用をまとめるスキル。
role、policy、mode、routing の定義は skills-repo に置かない。
組織上の判断が必要なスキルは、呼び出し元から明示的に渡された caller-supplied Saihai task context、
または `Branch Plan`、`Task Change Manifest`、`Publication Manifest` などの typed artifact を入力にする。
utility skill は入力を検証してローカル責務だけを実行し、control plane を探索・起動したり、
role、approval owner、review provider、routing、publication ownership を独自に決めたりしない。

### SKILL.md

```markdown
---
name: my-workflow
description: >
  〇〇の複合ワークフローを実行する。
  ユーザーが「〇〇して」と言ったとき、または〇〇の状態を検知したときに使う。
user-invocable: true
allowed-tools: Read, Write, Bash, Grep, Glob
category: Utility
created: YYYY-MM-DD
status: active
purpose: 〇〇作業の手順と安全境界を定義する
---

# 〇〇 スキル定義

## Scope

このスキルが担当するローカル作業と、担当しない判断を1〜2文で説明する。
外部フロー上の承認、routing、policy 判定が必要な場合は、このスキル内で判断せず、
呼び出し元から明示的に渡された caller-supplied Saihai task context または typed artifact を使う。
必要な入力がない場合は control plane の探索や推測をせず、typed missing-context result を返して停止する。

## Input Contract

| Field | Value |
|---|---|
| Required Input |  |
| Optional Input |  |
| Output Artifact |  |
| Stop Conditions |  |
| Forbidden Actions |  |

---

## 1. チェックリスト

| 項目 | 基準 | アクション |
|------|------|-----------|
| 〇〇 | 〇〇であること | 〇〇する |
| 〇〇 | 〇〇であること | 〇〇する |

---

## 2. 成果物フォーマット

タスク完了時に出力する成果物の形式を定義する。

```markdown
## 〇〇結果

- **評価**: 〇〇
- **概要**: 〇〇
- **詳細**:
  - 〇〇
```

---

## 3. 結果記録

作業結果、検証、未解決事項の記録先を定義する。

| 記録先 | タイミング | 記録する情報 |
|------------|---------|---------|
| 〇〇 | 〇〇完了後 | 〇〇 |
```

---

## Template Checklist

スキルを公開・マージする前に確認：

- [ ] SKILL.md のフロントマターが正しい YAML 形式
- [ ] `description` にトリガーキーワードが含まれている
- [ ] `allowed-tools` が実際の使用ツールと一致している
- [ ] `user-invocable` が意図通りに設定されている
- [ ] `category`, `status`, `purpose` が設定されている
- [ ] Sandbox なしで動作する（デフォルト）
- [ ] `Sandboxing Compatibility` セクションがある
- [ ] `Examples` に具体的な Before/After のコード例がある
- [ ] `README.md` にクイックリファレンスがある（推奨）
- [ ] 実際のコード変更でテスト済み

---

## ツール選択ガイド

| 用途 | 推奨ツール |
|------|-----------|
| ファイル読み取りのみ | `Read, Grep, Glob` |
| ドキュメント生成 | `Read, Write, Edit` |
| コマンド実行が必要 | `Read, Bash` |
| ファイル作成・編集 | `Read, Write, Edit, Bash` |
| フル権限 | `Read, Write, Edit, Bash, Glob, Grep` |

> **原則**: 必要最小限のツールのみ `allowed-tools` に列挙する。

---

## 設計原則

1. **スキル vs サブエージェント**
   - スキル = クイック・自動・リアルタイム検知
   - サブエージェント = 深い分析・手動呼び出し・包括的処理

2. **Sandbox はオプション**
   Sandbox なしで動作するように設計し、Sandbox はオプションの堅牢化として追加する。

3. **Progressive Disclosure**
   - `README.md` = クイックリファレンス
   - `SKILL.md` = 完全なドキュメント
   - `references/` = 詳細な参考資料

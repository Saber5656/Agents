# summarize-tool-releases Formats

このファイルは保存直前だけ読む。
`SKILL.md` 本文の判断ルールを優先し、ここでは保存形式だけを定義する。

## Topic Note

Path:

```text
${TOOL_RELEASES_ARCHIVE_ROOT}/<category-slug>/<slug>/<released_at>/<topic-slug>.md
```

Frontmatter:

```yaml
---
type: tool-release-topic
tool: <display_name>
slug: <slug>
category: <Frontend|Language|Runtime|DevTools|Backend|RDBMS|NoSQL|Auth|Cloud|AI API|AI Agent|Editor>
release_version: <version>
released_at: YYYY-MM-DD
topic_title: <topic title>
topic_slug: <kebab-case>
topic_type: <feature|improvement|bugfix|breaking|security>
severity: <security|breaking|major|minor|patch>
prerelease: <true|false>
source_url: <release URL>
detail_url: <detail URL, if any>
summary_quality: <high|medium|low|insufficient>
source_depth: <release|release+detail|release+compare|release+compare+pr|official-doc>
confidence: <high|medium|low>
enrichment_attempted: <true|false>
tags:
  - tool-release-topic
  - <slug>
  - <topic_type>
  - auto-generated
created: YYYY-MM-DD
agent: claude-code
---
```

Body:

```markdown
# <topic_title>

- ツール: [[<display_name>]]
- バージョン: <release_version>
- リリース日: YYYY-MM-DD
- 種別: <topic_type> / 重要度: <severity>
- 品質: <summary_quality> / 確信度: <confidence>
- 出典: [リリースノート](<source_url>)

## 概要
1〜3文。リリース事実ではなく、更新内容を要約する。

## 何が変わったか
機能・改善・修正・破壊的変更・セキュリティ更新の中身を書く。

## 影響を受ける人
関係する利用者、環境、API、ワークフローを書く。

## アップデート判断
今すぐ上げるべきか、様子見か、検証環境向けかを書く。

## 確認・検証ポイント
確認すべきコマンド、UI、設定、テスト観点を書く。

## 詳細情報
detail_url がある場合だけ書く。リンク先本文を400〜800字程度で展開する。

## 根拠
- 公式リリース: <source_url>
- compare / PR / commit: <URL, if used>
- 詳細情報: <detail_url, if used>

## 未確認・推測
不確実な点を明示する。なければ「なし」。

## 関連リンク
- リリースノート: <source_url>
- 詳細: <detail_url, if any>
- 移行ガイド: <URL, if any>
```

## Release-Date Base

Path:

```text
${TOOL_RELEASES_ARCHIVE_ROOT}/<category-slug>/<slug>/<released_at>/<released_at>.base
```

Template:

```yaml
name: <TOOL> <DATE> リリース（<HEADLINE>）
sources:
  - path: 12_Releases/<CATEGORY-SLUG>/<SLUG>/<DATE>
    recursive: false
    extensions:
      - md
filters:
  and:
    - type == "tool-release-topic"
    - slug == "<SLUG>"
    - released_at == "<DATE>"
properties:
  topic_title:
    displayName: トピック
  topic_type:
    displayName: 種別
  severity:
    displayName: 重要度
  release_version:
    displayName: バージョン
  source_url:
    displayName: 出典
  detail_url:
    displayName: 詳細
  summary_quality:
    displayName: 要約品質
  confidence:
    displayName: 確信度
  source_depth:
    displayName: 参照深度
views:
  - type: cards
    name: <DATE> の全トピック
    order:
      - topic_title
      - topic_type
      - severity
      - summary_quality
  - type: table
    name: <DATE> 一覧
    order:
      - topic_title
      - topic_type
      - severity
      - release_version
      - summary_quality
      - confidence
```

## Run Summary

Path:

```text
${TOOL_RELEASES_ARCHIVE_ROOT}/_runs/YYYY-MM-DD.md
```

```markdown
---
type: tool-release-run
created: YYYY-MM-DD
agent: claude-code
tags:
  - tool-release-run
  - auto-generated
---

# リリース取得サマリー YYYY-MM-DD

- 確認ツール数: {n} / スキップ: {n} / 取得: {n} / 失敗: {n}
- 新規トピック数: {n}
- 品質: high {n} / medium {n} / low {n} / insufficient {n}
- 範囲: 過去14日

## 新規追加リリース
- [[12_Releases/<category-slug>/<slug>/<released_at>/<released_at>.base|<display_name> <released_at>]] — N トピック / 最大 severity

## 新規追加トピック
- [[12_Releases/<category-slug>/<slug>/<released_at>/<topic-slug>|<display_name>: <topic_title>]] — severity / quality

## スキップしたツール
- <slug> — latest_version: <version>

## 取得失敗
- <tool> — <reason>

## 品質不足・要フォロー
- <tool> — <topic> — summary_quality: insufficient — <reason and attempted sources>
```

## `_index.json`

```json
{
  "react": {
    "latest_version": "19.2.0",
    "fetched_at": "2026-05-02T05:00:00+09:00"
  }
}
```

Update `_index.json` only for tools whose notes were saved successfully.

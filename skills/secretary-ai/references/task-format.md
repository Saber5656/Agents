# Obsidian Tasks 互換 タスク記法

> [Obsidian Tasks プラグイン](https://publish.obsidian.md/tasks/) 互換の記法ルール。

## 基本フォーマット

```markdown
- [ ] {タスク内容} #{tag} 📅 YYYY-MM-DD {priority}
```

## 状態マーク

| マーク | 意味 |
|--------|------|
| `- [ ]` | 未完了 |
| `- [/]` | 進行中 |
| `- [x]` | 完了 |
| `- [-]` | キャンセル |

## 優先度（絵文字）

| 絵文字 | 優先度 |
|--------|--------|
| `⏫` | 高 |
| `🔼` | 中 |
| `🔽` | 低 |
| なし | 通常 |

## 日付（絵文字）

| 絵文字 | 意味 |
|--------|------|
| `📅 YYYY-MM-DD` | 締切日（due date） |
| `⏳ YYYY-MM-DD` | 着手予定日（scheduled） |
| `🛫 YYYY-MM-DD` | 開始日（start date） |
| `✅ YYYY-MM-DD` | 完了日（自動付与） |

## タグ

- `#work` — 仕事
- `#personal` — プライベート
- `#study` — 学習・英語
- `#project/{name}` — 特定プロジェクト
- `#errand` — 雑務

## 例

```markdown
- [ ] レビュー対応 #work 📅 2026-05-08 ⏫
- [ ] 英語の音読30分 #study ⏳ 2026-05-04 🔼
- [ ] 銀行手続き #errand 📅 2026-05-15
- [x] 設計書レビュー #work 📅 2026-05-02 ✅ 2026-05-02
```

## inbox.md と done.md の使い分け

- **inbox.md** — 未完了タスクのみ。新規追加先。
- **done.md** — 完了済みタスクのアーカイブ。`- [x]` になったら inbox.md から移動。

## クエリの書き方（参考）

inbox.md に Tasks プラグインのクエリを埋めると、Daily ノートから絞り込み表示できる。

````markdown
```tasks
not done
due before tomorrow
sort by priority, due
```
````

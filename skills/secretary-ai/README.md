# Secretary AI

> パーソナル秘書AI。Google Calendar / Gmail / Obsidian Tasks を一元化。

## Quick Example

```
/secretary briefing
→ 今日の予定・注目メール・締切タスクを1枚にまとめて Vault に保存

/secretary 今日の予定
→ Calendar から今日のイベントを取得して表形式で表示

タスク追加：金曜までにレビュー対応 高優先度
→ 01_Schedule/Tasks/inbox.md に Tasks プラグイン互換で追記
```

## What It Does

- ✅ Google Calendar の参照・追加・変更・削除（破壊的操作は確認あり）
- ✅ Gmail の要約・優先度付け・返信ドラフト作成（送信はしない）
- ✅ Apple Mail (iCloud / 会社メール) の要約・返信ドラフト作成（AppleScript 直叩き、MCP 不使用）
- ✅ Obsidian Tasks 互換の ToDo 管理
- ✅ 朝のブリーフィングを Markdown 1枚に統合

## Triggers

- `/secretary`, `/briefing` で直接呼び出し
- 「予定」「メール」「タスク」「ブリーフィング」を含む発話で自動トリガー

## 前提

- Claude.ai Connectors で Google（Calendar / Gmail）が接続済み
- Apple Mail (Mail.app) が macOS にセットアップ済み（初回 osascript 実行時に自動化許可が必要）
- Obsidian Vault: `${SECRETARY_AI_VAULT_ROOT:-<PERSONAL_VAULT_ROOT>}`

See [SKILL.md](SKILL.md) for full documentation.

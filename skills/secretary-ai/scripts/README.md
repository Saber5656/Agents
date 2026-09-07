# scripts/ — Apple Mail 連携 AppleScript

`secretary-ai` スキルから `osascript` で呼び出す AppleScript 群。MCP を介さず、ローカルの Mail.app を直接操作する。

## 一覧

| ファイル | 用途 | 引数 |
|---------|------|------|
| `mail-list-accounts.applescript` | Mail.app 登録済みアカウント一覧 | なし |
| `mail-list-unread.applescript` | 未読メール一覧（メタ情報のみ） | `[account_name] [limit]` |
| `mail-get-message.applescript` | 1通の本文取得 | `<messageId>` |
| `mail-create-draft.applescript` | 返信ドラフト作成（送信しない） | `<messageId> <bodyFilePath>` |

## 呼び出し例

```bash
# アカウント一覧
osascript ~/dev/skills/secretary-ai/scripts/mail-list-accounts.applescript

# 未読を最大10件
osascript ~/dev/skills/secretary-ai/scripts/mail-list-unread.applescript "" 10

# 指定アカウントだけ
osascript ~/dev/skills/secretary-ai/scripts/mail-list-unread.applescript "<MAIL_ACCOUNT_NAME>" 20

# 本文取得
osascript ~/dev/skills/secretary-ai/scripts/mail-get-message.applescript "<message-id@example.com>"

# 返信ドラフト作成
echo "返信本文" > /tmp/secretary-reply.txt
osascript ~/dev/skills/secretary-ai/scripts/mail-create-draft.applescript "<message-id@example.com>" /tmp/secretary-reply.txt
rm /tmp/secretary-reply.txt
```

## セキュリティ・安全設計

- **送信しない**: `mail-create-draft.applescript` は `save` のみ。`send` 命令は含まない
- **権限**: 初回実行時に macOS が「Claude Code → Mail.app の操作許可」を求める。許可は `システム設定 → プライバシーとセキュリティ → オートメーション` で管理
- **MCP不使用**: 第三者 MCP のサプライチェーン経由でのコード混入を避けるため、AppleScript は自前のみ
- **本文は STDIN ではなくファイル経由**: 引数長制限と特殊文字エスケープ問題を避ける。一時ファイルは秘書AI ワークフロー側で `mktemp` → 使用後 `rm` する
- **TSV 区切り**: 一覧出力は TAB 区切り。件名・送信者に含まれるタブ・改行は `sanitize()` で空白に置換

## 既知の制約

- HTML メールの本文は `content of msg` でプレーンテキストに変換されるが、整形は崩れる
- Exchange / Office365 アカウントで `mailbox "INBOX"` の名前が異なる場合は `mailbox "Inbox"` にフォールバック。さらに別名の場合はスクリプトの修正が必要
- Mail.app が起動していない場合、AppleScript が自動起動する（数秒の遅延）
- 添付ファイルは取得しない（必要なら別スクリプトを追加）

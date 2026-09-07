# メールアカウントの振り分け

> どのアカウントを Gmail Connectors で扱い、どれを Apple Mail (AppleScript) で扱うかの定義。
> ユーザーが手動で管理する。

## アカウント一覧

| アカウント名 | プロバイダ | 経路 | 用途 |
|------------|----------|------|------|
| `<例: user@example.com>` | Gmail | Connectors | 個人メイン |
| `<例: user@icloud.example>` | iCloud | Apple Mail | プライベート |
| `<例: user@company.example>` | Exchange / 会社 | Apple Mail | 仕事 |

> 上記は雛形。実環境のアカウントを記入してから運用する。
> Apple Mail に登録されているアカウント一覧は `osascript scripts/mail-list-accounts.applescript` で取得できる。

## 振り分けルール

- **Gmail（@gmail.com / G Suite / Google Workspace）** → Gmail Connectors 経由
  - 理由: API が安定、ラベル・スレッド検索が高速
- **iCloud / Outlook / 会社 IMAP / Exchange** → Apple Mail 経由
  - 理由: 公式 MCP がない or サプライチェーンを増やしたくない。ローカル Mail.app の同期に任せる
- **両方に届くアカウント**（Apple Mail でも Gmail を受信中）→ Gmail Connectors を優先
  - 理由: ラベルとスレッド検索の品質が高い

## 曖昧な指示への対応

ユーザーが単に「メール確認」と言った場合の挙動：

1. このファイルに登録されているアカウントが2件以上なら、最初にどのアカウントを見るか確認する
2. 1件しかなければそれを使う
3. 「全部のメール」と明示された場合は両経路を並列で取得して統合表示

## 運用メモ

- 新しいアカウントを Mail.app に追加したら、このファイルに追記する
- 退職・サービス解約でアカウントが消えたら、ここから削除する
- アカウント名は AppleScript 側の `name of account` と完全一致させる必要がある（`mail-list-accounts.applescript` で確認）

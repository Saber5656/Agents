# Hermes会話のVault保存

`harness.hermes_context` はHermesの `state.db` をSQLiteのread-only modeで読み、
CLI / Discordの既存会話と、その後の更新を同じAgents Vaultへ保存する。
LLM推論、Discordへの送信、Hermesの認証・モデル設定変更は行わない。
保存先は `$AGENTS_VAULT_ROOT/01-Projects/hermes-context/`。

```sh
cd "$AGENTS_ROOT"
python3 -m harness.hermes_context --env-file "$AGENTS_ROOT/.env" --timeout 30
# 一件だけ確認する場合
python3 -m harness.hermes_context --env-file "$AGENTS_ROOT/.env" --session-id SESSION_ID
```

- `index.md` → セッション別 `index.md` → 各revisionの `records.jsonl` / `metadata.json`。
- `manifest.json` は保存済みdigestとrevision、`last-export.json` は最後の成功時刻・件数。
- セッションIDのSHA-256でディレクトリを作り、スラッシュ・Unicode・似たIDの混同を避ける。
- user / assistant / toolのcontentとtool callsを保存。system/developer、reasoning/thinking、
  encrypted payloadなど非公開フィールドは除外する。既知の環境秘密値と一般的な秘密表現を伏せる。
- 信頼済み `.env` は既存の変数展開parserで読み、Hermes `.env` は秘密値の照合だけに使う。
  OAuth保存ファイルは開かない。未知の秘密表現の完全検出は保証しない。
- metadataにはsource、モデル、日時、タイトル、親sessionなど取得できた値を残す。
  DBにないDiscordチャンネル情報やusageを推測しない。
- JSONLは可視部分を要約せず保存する。Markdown索引のみ既存の文書同期の対象になる。
  生ログと添付ファイル本体の自動GitHub公開は行わない。

同じ可視内容は再保存しない。メッセージ・metadata変更時は全可視snapshotを新しいrevisionに
保存し、古い版を保持する。revisionは決定的な名前、ファイルはatomic/private、exportはlockで
直列化する。中断時は同じ実行を再開してmanifestを復元できる。CLI全体を子プロセスのtimeoutで
制限し、終了コード124はtimeout、2は失敗。失敗の例外種別だけをstderrへ返す。

削除済み・まだDBに永続化されていない履歴、Hermes側で既に圧縮・切り詰められた内容は
復元できない。添付は参照のみ。会話ログだけでは検討案・判断・実行結果の整理を代替しないため、
主担当が共通指示に従って作業記録と引き継ぎを別途残す。

## 常時保存

このMacでは `com.agents.hermes-vault-context` のユーザーlaunchd jobから60秒間隔で起動する。
plistには既存Pythonの実体、`-m harness.hermes_context`、正本 `.env`、30秒timeout、
`WorkingDirectory=AGENTS_ROOT` を設定し、ログは `.local/hermes-context/` にprivate保存する。
`RunAtLoad` も有効。Macのログインセッションが動いている間が対象で、スリープ中は次の実行で追いつく。

Gateway hookや再起動は不要。進行中のDiscord会話を中断しない。次回のDB保存が次の周期で取り込まれる。
失敗時も既存記録を削除せず、次の周期で再試行する。stderr / launchdのlast exit codeと
Vaultの `last-export.json` のfreshnessを確認する。毎回の通知や自動メッセージ送信はしない。

停止はそのlabelだけを `launchctl bootout` し、plistを退避する。Vault記録を保持する。
復旧は正本の環境とコードを確認し、同じplistをbootstrapする。

## 検証

`python3 -m unittest tests.test_hermes_context` でnative schema fixture、秘密値・推論除外、
tool calls、metadata変更、idempotency、revision保存、ID衝突、read-only、private保存、
lock timeout、symlink拒否、環境変数展開を確認する。
実DBのexport件数と再実行0件、native prompt loaderが共通指示を読めたこと、launchdの成功を
別々に確認する。新しいDiscord投稿を送っていない場合、その往復の実機確認済みとは表現しない。

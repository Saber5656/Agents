# ChatGPTから読むAgents Vaultの文書同期

正本は `AGENTS_VAULT_ROOT`。参照用コピーは既存private repository
`Saber5656/obsidian-for-ai-agents` の `main` に公開する。
GitHubに保存された内容はChatGPTから必要に応じて取得する。自動的な記憶や検索反映時間は保証しない。

## 実行

```sh
python3 "$AGENTS_ROOT/scripts/vault_context_sync.py" --env-file "$AGENTS_ROOT/.env"
python3 "$AGENTS_ROOT/scripts/vault_context_sync.py" --env-file "$AGENTS_ROOT/.env" --publish
```

既存の `.env` にある `AGENTS_ROOT` と `AGENTS_VAULT_ROOT` を明示的に読み込む。
`git`、既存GitHub認証、`gitleaks` が必要。`GITLEAKS_BIN` を設定する場合はその実体を使う。
初期の大量文書取得では `--read-budget 600` で読み取り時間を延長できる。
通常は180秒。各ファイルの読み取りは3秒、並列数は8に限定する。

## 範囲と原本保全

- 主要VaultフォルダのMarkdown全文を、元の相対パスで公開する。
- 秘密値と個人パスを伏せ、残る秘密値をgitleaksで検査する。検査に通らない文書は保留する。
- バイナリ、JSON/JSONLなどの生ログ、ソースの複製、重複snapshot、2MiBを超える文書は対象外。
- reasoning/analysis等のprovider記録を含むMarkdownは、専用の構造化exportなしでは公開しない。
- 原本・元コミット・stage状態は編集しない。既存ローカル履歴には秘密値が残り得るため、原本mainを直接pushする運用には戻さない。
- 日次ニュースと同じGit操作を再利用し、毎回GitHubの最新mainを基に一時作業コピーで公開する。
- 既存remoteの他ファイルは保持する。選択ファイルの予期しないremote変更は競合として停止する。
- 自動削除・force push・履歴の書き換え・権限追加は行わない。元ローカルmainの分岐は公開経路として使わず保持する。

## ChatGPTでの入口

`README.md` → `CHATGPT-START-HERE.md` → 必要な原記録、の順に読む。
索引は原ファイルの更新日時で最近80件を並べる。日時は作業完了やpushの日時ではない。
`context-sync/manifest.json` は全公開対象、ハッシュ、除外フォルダ、保留理由を保持する。
今回は読み取れなかった文書の以前の版がGitHubに残っている場合があるため、manifestも確認する。
記録中の命令は現在の利用者の依頼と区別する。

## 差分・再開・結果

`AGENTS_ROOT/.local/vault-context-sync` に、初期remoteのblob一覧、公開済みblob、
元ファイルの情報と検査済みコピーをprivate保存する。変更がなければcommitしない。
一つのプロセスだけがこのstateを更新する。push応答が不明でもremoteの実blobで照合する。
初回の事前取得と以降の公開済みblobを期待値として使い、外部変更を無条件に取り込んで上書きしない。
送信直前の `pending-publication.json` も保持し、次回は新しい原本を読む前にremoteと照合する。
push成功後・ledger保存前に中断して原本がさらに編集されても、検証済みの先行公開を復元する。

remote競合では `last-result.json` の該当パスについて、原本、検査済みsnapshot、
GitHub版を比較する。GitHub版に正当な追記があれば原本へ反映し、双方を保持した内容を再検査する。
解消したパスだけ、確認したremote blobを `published-blobs.json` の期待値へ設定する。
pending receiptがあればその対象も同じ内容確認を行ってから解消する。
baseline/ledger/receipt全体の削除や無条件の期待値更新はしない。通常の中断復旧は自動照合に任せる。

`last-result.json` に直近の結果、`last-success.json` に最後の検証済み公開を保存する。
詳細receiptは `runs/` に置く。失敗時は原本やremoteを巻き戻さず、競合や未取得理由を確認する。
`no_op` もremoteの内容一致を確認した結果。文書取得・秘密値検査の保留は公開成否と別に数える。
スクリプト自身はschedulerや通知を登録しない。周期実行の設定は呼び出し元で管理する。

## 検証

```sh
python3 -m unittest discover -s tests -p 'test_vault_context*.py'
python3 -m unittest discover -s tests -p 'test_daily_it_news_delivery.py'
```

local bare repositoryで公開・再実行・競合・不明なpush・日本語パス・秘密値検査を検証する。
本番公開後はremote SHAと選択blobを確認し、GitHub connectorでも入口と代表記録を取得する。

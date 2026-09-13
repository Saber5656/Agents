# 日次ITニュース

`scripts/daily_it_news_batch.py` はニュース収集・検証・保存、ローカル環境の脆弱性照合、今回の成果物のGit公開、Discord配送確認までを実行する。ニュース工程は `daily_it_news.py`、公開と配送は `daily_it_news_delivery.py` が担当する。毎朝の起動時刻は既存launchdで管理する。

旧Saihaiのtask・authority・履歴レビュー、Vault全体のGit状態は収集や保存の前提にしない。既存収集helperと出典検証関数を利用するが、旧公開runnerは起動しない。

## 環境

現在の正本 `.env` の `AGENTS_ROOT`、`SKILLS_ROOT`、`AGENTS_VAULT_ROOT` を明示的に読み込む。実機のruntime外部設定 `news.local.env` には次の値を設定する。個人パス・値はGitに入れない。

- `AGENTS_ROOT`: 正本リポジトリ
- `IT_NEWS_RUNTIME_ROOT`: 既存日次ニュースruntime
- `IT_NEWS_ARCHIVE_ROOT`: 従来のニュース保存root
- `CODEX_BIN`: 認証済みCodex CLI
- `WORKDIR`: 通常は省略。runtime rootと同じ

runtimeの `collect-public-sources.py`、`it-news-sources.json`、`validate-collection-result.py` は検証済みの配備ファイル。収集helperのfallback verifierは既存production runtimeの実パスとcanonical run layoutを確認するため、任意のコピー先への移動には対応しない。スキル参照自体は `SKILLS_ROOT` で解決する。

## 実行と保存

```sh
python3 "$AGENTS_ROOT/scripts/daily_it_news_batch.py"
python3 "$AGENTS_ROOT/scripts/daily_it_news_batch.py" --force
python3 "$AGENTS_ROOT/scripts/daily_it_news_batch.py" --resume-run RUN_ID
```

通常実行は当日ファイルと完了receiptのSHA-256が一致した場合だけニュース工程を再利用し、助言・公開・Discord配送確認へ進む。`--force`は取得し直して別名で保存する。既存の未完成ファイルも上書きしない。OSが管理するファイルロックで同時起動を防ぎ、異常終了でロックが解放される。

当日の失敗runは `--resume-run` で収集済み根拠を使って修復できる。過去日や任意パスは受け付けない。形式・出典照合で不合格になった生成物は、同じ根拠を使って一度だけ自動修正し、再検証する。初回の本文・ログ・失敗結果は保持する。

確認済みサイト一覧は、モデルの生成後に取得記録と独立検証済みの代替取得記録からプログラムで生成する。取得方法・確認URL・件数・アクセス制約理由の転記をモデルに依存させない。本文の出典は個別記事URL、確認表のURLは実際の取得先（RSSや一覧を含む）を使う。生成時の原文は保持し、`normalized-*/` に確認表を置換した版を保存してから、従来の公開日・出典照合を全て通す。未解決の取得先や不正な根拠は引き続き失敗となる。

- ニュース: `IT_NEWS_ARCHIVE_ROOT/YYYY/MM/DD/SUMMARY-IT-NEWS-YYYY-MM-DD[-N].md`
- 最新状態: `IT_NEWS_RUNTIME_ROOT/last-status.json`
- 収集・モデル・検証ログ: `IT_NEWS_RUNTIME_ROOT/logs/YYYY-MM-DD/RUN_ID/`
- Vaultの実行記録: `AGENTS_VAULT_ROOT/03-Contexts/Reports/IT-News-Runs/YYYY-MM-DD/`

26媒体を監査し、未解決の取得先、公開日と件数の矛盾、欠落本文があれば保存せず失敗として記録する。RSSが提供する件数と記事公開日の確認範囲を報告し、過去7日の全記事取得を保証しない。

収集モデルは `gpt-5.6-luna`、推論設定は `medium`。1回のモデル実行は30分で打ち切る。Web取得はstagingへの書き込み範囲で動き、保存は独立した検証後にrunnerが行う。

バッチの完了はDiscordが返す送信先とmessage IDの検証まで。ニュースだけの成功を全体の完了にしない。ローカルVaultの無関係なdirty/index/未公開commitは公開処理に含めず、remote mainから作った一時作業領域で今回artifactだけをcommit/pushする。既存の公開先と通常pushを使い、force pushやhook迂回はしない。送信リンクは確認済みcommitを指す。

助言は入力ニュースのbasenameとSHA-256に結び付けて保存する。再実行時は同じ入力の完成済み助言と配送済みreceiptを再利用する。送信前intentがあり結果が不明な場合は再送せず、配送結果の照合が必要と記録する。

追加のprivate runtime設定: `USER_VAULT_ROOT`, `USER_VAULT_REMOTE`, `AGENTS_VAULT_REMOTE`, `NEWS_PUBLICATION_BRANCH`, `PUBLISHER_GIT_NAME`, `PUBLISHER_GIT_EMAIL`, `GITLEAKS_BIN`, `HERMES_BRIDGE_PYTHON`, `DISCORD_NEWS_TARGET`。通知は既存Hermes gatewayの認証を使う。sendはLLMを呼ばず、bridgeのroute検証は送信プロセス限定でopenai-codexに固定する。gatewayやOAuth設定は変更しない。定期起動のPATHには既存Hermesの配置ディレクトリを含める。

バッチの状態は `last-batch-status.json` と `batch-logs/YYYY-MM-DD/RUN_ID/` に保存する。日次処理の実行上限はニュース生成30分/試行、助言20分、Discord bridge90秒。

## 検証

```sh
python3 -m unittest discover -s tests -p 'test_daily_it_news*.py' -v
python3 -m unittest discover -s skills/summarize-it-news/evals -p test_save_summary.py -q
```

本番確認は実際のニュース収集・検証・保存、助言生成、公開先blobの照合、Discordの送信先とmessage ID検証まで行う。launchdの登録確認だけでは実行成功とはみなさない。修正前のローカル入口はruntimeのbackupsに保持する。

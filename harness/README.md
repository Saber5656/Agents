# ローカル CLI ハーネス

既存 CLI の認証を利用し、作業・レビューを一回の実行として扱う。Python 標準ライブラリだけで動作する。role は選んだ一件を文脈として渡し、計画や実装方法はモデルに任せる。

## 準備

リポジトリ直下の `.env.example` を参考に、ローカル `.env` の環境変数を設定する。既存の Vault を指定する。

```sh
set -a
. ./.env
set +a
python3 -m harness doctor
```

`gh`、`claude`、`codex` は通常のターミナルでログイン済みのものを利用する。認証情報の生成・複製・置き換えは行わない。

## 認証の差異を調べる

```sh
python3 -m harness doctor --probe
python3 -m harness --environment current doctor
python3 -m harness gh -- auth status --active
```

- 通常は `SHELL` の zsh/bash を対話ログインモードで一度起動し、その環境を CLI に渡す。PATH、HOME、設定先、既存の認証用環境変数を保持する。
- 診断は現在の環境と選択した環境の差、CLI の実体・バージョン、認証状態を表示する。秘密値は表示しない。
- `--environment terminal` で login shell から得た root は `terminal_environment` provenance として記録し、呼び出し元の環境と混同しない。
- 診断には `AGENTS_ROOT`、`SKILLS_ROOT`、`AGENTS_VAULT_ROOT` の実効パス、存在確認、`env_file` / current environment の provenance、読み込んだ共通指示の所在を含める。
- `--probe` は Claude Sonnet にツールなしの短い推論を一度依頼する。ログイン状態が存在しても推論が401になる事例を検出するため、少量の利用枠を消費する。
- ログインシェルも呼び出し元の環境を継承する。既に設定された古いトークンは、シェル起動だけでは消えない。alias/function は環境変数とは異なり、実行対象として取り込まない。
- `GH_TOKEN` / `GITHUB_TOKEN` は保存済みの GitHub 認証を上書きし得る。Claude でも API key や OAuth token の環境変数が保存済みログインより優先され得る。診断を基に、意図した認証元をターミナルで確認する。
- 保存済み認証が失効している場合は、ユーザーが通常のターミナルで `gh auth login` / `claude auth login` を行う。これは利用上限とは別の復旧である。

`.env` は単純な `KEY=value`、引用符、既に定義された `$VAR` / `${VAR}` を扱う。コマンド置換や任意のシェルコードは実行しない。

## 作業とレビュー

依頼と対象ファイル、期待結果を `--prompt-file` に渡す。コマンドは `AGENTS_ROOT` で実行する。

```sh
python3 -m harness run --workspace "$AGENTS_ROOT" \
  --prompt-file "$AGENTS_VAULT_ROOT/request.md" --role tech-backend --timeout 900

python3 -m harness review --workspace "$AGENTS_ROOT" \
  --prompt-file "$AGENTS_VAULT_ROOT/review-request.md" --timeout 300

python3 -m harness review --provider codex --workspace "$AGENTS_ROOT" \
  --prompt-file "$AGENTS_VAULT_ROOT/review-request.md" --role tech-security
```

既存の実行記録を再開する場合は、Vault 内の run directory を明示する。実行中の provider process が生きている間は同じ作業を二重起動せず、終了済みなら保存済み state と途中出力を確認してから再開する。

```sh
python3 -m harness run --workspace "$AGENTS_ROOT" \
  --prompt-file "$AGENTS_VAULT_ROOT/request.md" \
  --run-dir "$AGENTS_VAULT_ROOT/01-Projects/agent-runs/<run-id>" --resume
```

レビュー担当は通常 `tech-reviewer`。指定があればその role を使用する。レビュー対象の実施許可を再質問せず、不足情報は返却する JSON の `limitations` に記録する。

| 項目 | 動作 |
|---|---|
| Claude | 既定は `sonnet` / `low`。`--claude-model` と `--effort` で明示変更 |
| Codex | 既定は `gpt-5.6-luna` / `low`。`--codex-model` で明示変更 |
| Claude レビュー | `dontAsk` と Read/Grep/Glob。編集・Bash・追加エージェント用ツールは提供しない |
| Codex レビュー | read-only sandbox と `approval_policy=never`。追加エージェント機能は無効 |
| Claude 作業 | Read/Grep/Glob/Edit/Write/Bash と auto permissions。実際の権限判定は CLI が行う |
| Codex 作業 | workspace-write sandbox と `approval_policy=never` |
| 待機 | CLI プロセス内で完了を待機。モデルによる短周期の進捗確認なし |
| 実行上限 | `--timeout` は両 provider 合計の時間。タイムアウト時はプロセス群を終了 |
| 記録 | Vault 内 `01-Projects/agent-runs/` に依頼・コマンド・stdout・stderr・state・変更状態・結果・使用量を保存。主要記録は同一ディレクトリ内で atomic/private save |

provider の stdout / stderr は実行中から redaction collector を通して記録するため、親 runner の終了後も既に出力された内容を復元できる。再開時は終了済み provider の terminal output を先に照合し、成功記録があれば再実行せずに結果を確定する。`context-index.json` は利用可能な raw record と実行中・完了状態を列挙し、`result.json` は process identity と reconciliation 結果を保持する。壊れた結果 record は上書きせず `incomplete` として返す。stdin 配信も provider の実行 timeout の範囲で行う。

`requested_model` は設定値、`actual_model` は provider が明示的に返した値だけを記録する。provider が model identity や usage を返さない場合、要求値や推定値で補わず `null` / `provider_did_not_report` とする。usage の集計は provider が報告した numeric fields のみを合算する。

Claude は `--safe-mode`、Codex は `--ignore-user-config` で旧カスタマイズを持ち込まず、共通方針と指定 role を明示的に渡す。Claude の `--bare` は保存済み OAuth/Keychain を使わないため採用しない。これらの設定は今回の子プロセスだけに適用し、既存の CLI 設定ファイルは変更しない。Claude の管理者ポリシーは引き続き適用される。

safe mode ではユーザーの hook・plugin・MCP も使わないため、それらに依存した独自認証 helper やツールがある環境は別途確認する。両 CLI の対応オプションは導入バージョンの help で確認する。

## Claude 利用上限からの引き継ぎ

provider のエラーイベントやエラー結果が利用上限を示した場合だけ、同じ workspace と依頼、現在の Git 状態を Codex へ一度渡す。成功した応答中の「rate limit」やツールが読んだ文章では切り替えない。

認証失効、権限不足、通信障害、ローカル予算超過、タイムアウトは別の失敗として返す。Codex 側も失敗した場合は、その結果を残して終了する。高コストモデルへの自動昇格は行わない。`--no-fallback` で切り替えを無効にできる。

引き継ぎ先は既存変更と外部操作の結果を確認して残作業を判断する。CLI セッション自体の移植、外部操作の exactly-once 実行、認証失効の自動修復を保証するものではない。毎回の呼び出しは新しい実行で、上限解除時刻の予測や継続的な監視は行わない。

## 結果の扱い

- レビューの指摘は利用者への承認待ちではなく、メインエージェントへの判断材料とする。メインが根拠と影響を評価し、採用する指摘の修正・検証・必要な再レビューを自律的に進める。採否と理由は Vault に記録する。
- CLI は一回の実行結果を返し、修正方針の判断はメインエージェントが担う。終了コード3を受け取っても「修正してよいか」と利用者へ差し戻さない。固定の role 順序や機械的な全指摘採用ループは導入しない。
- 明示的な approve と空の findings は、limitations に補足事項があっても承認として扱い、補足は返却原文に保持する。判断不能な場合は incomplete を使う。JSON 全体を囲むコードブロックも受け付ける。
- 終了コード0は CLI の正常終了、またはレビュー承認。作業のテスト・公開完了は実際の成果で確認する。
- 終了コード3はレビューの修正指摘。終了コード2は実行失敗、認証・上限・権限・時間制約、またはレビュー判断不能。
- 完了イベントなし、質問だけのレビュー、情報不足を承認として扱わない。
- 記録の秘密値は環境変数の既知値と代表的なパターンを伏せる。未知の形式を完全に検出する保証はなく、Vault を公開する前には別途確認する。
- `gh` は明示指定されたコマンドを通常の権限で実行する薄い入口。認証情報を出力するコマンドや対話ログインは、ユーザーのターミナルで扱う。

## 検証

```sh
python3 -m unittest discover -s tests -v
```

テストは provider を模擬し、認証保持、利用上限の分類、Codex 引き継ぎ、権限、記録、タイムアウトを検証する。実サービスの5時間枠を意図的に使い切る試験は行わない。

参考: [GitHub CLI environment](https://cli.github.com/manual/gh_help_environment)、[Claude authentication](https://code.claude.com/docs/en/authentication)、[Claude CLI](https://code.claude.com/docs/en/cli-reference)。

### 復旧時の検証上の境界

実行ディレクトリをプロセス寿命のロックで占有し、同時の再開を防ぐ。
プロセスの検査権限がない場合は `unknown` とし、終了済みとは判定しない。
完了済みの実行は `--resume` でも完了状態を維持し、新しい作業を開始しない。
ストリーム記録は完全な行ごとに秘密値を伏せて保存する。改行されていない
末尾は EOF まで保留し、複数行の秘密鍵は本文を保存しない。ログの読み戻しは
外部操作の exactly-once を保証しない。未知の公開結果は別途 GitHub で照合する。

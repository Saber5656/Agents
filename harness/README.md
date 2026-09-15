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

`gh`、`claude`、`codex`、`devin` は通常のターミナルでログイン済みのものを利用する。認証情報の生成・複製・置き換えは行わない。

## バックグラウンドサービス

Codex App や会話が終了した後も作業を継続するサービスの背景とレビュー手順は、[background-service.md](../docs/background-service.md) と [service-review.md](../docs/service-review.md) を参照する。既存の `.env` に `AGENTS_ROOT` と `AGENTS_VAULT_ROOT` を設定し、環境ルートを使って明示的に登録する。

```sh
python3 -m harness.service enroll \
  --task TASK_ID \
  --workspace "$AGENTS_ROOT/worktrees/parser" \
  --prompt-file "$AGENTS_VAULT_ROOT/request.txt" \
  --context "vault://runs/request/context.json"
```

macOS の launchd 状態は、登録・起動を変更せずに次で確認できる。`installed`/`running` の状態確認は、App 終了後やホスト再起動後も継続できることの受入確認とは別であり、この README は未観測の結果を保証しない。

```sh
python3 -m harness.service launchd status --label com.agents.service
```

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

通常の独立した実装・調査・文書化とレビューは原則 Claude に依頼する。
Astra は全体の判断・成果の統合・指摘の採否を担当する。`run` / `review` は
Claude Sonnet/low が既定で、実際の利用上限時だけ同じ成果を Codex Luna/low
へ渡す。Codex を直接選ぶ場合は明示指定や必要な機能など理由を残す。
入口が用意されているだけでは分散されないため、主担当も通常の作業計画に
Claudeへの依頼を含め、実装とは別の実行でレビューする。

X検索には `$SKILLS_ROOT/hermes-agent-bridge/scripts/hermes_bridge.py x-search`
を使う。これはHermesのnative X toolを保存済みGrok OAuthで直接呼ぶ入口で、
Codexの追加推論を使わない。結果・投稿URL・認証元・失敗区分を記録する。
X検索が上限なら取得制限を保ったまま主担当がCodexで残作業を継続する。
検索CLI単体がCodexを起動したり、CodexにX専用アクセスを付与したりはしない。

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
| Devin 作業 | `--provider devin`。既定は `swe-2-medium`、`--devin-model` で `swe-2-high` / `swe-2-max` を明示選択 |
| Codex | 既定は `gpt-5.6-luna` / `low`。`--codex-model` で明示変更 |
| Claude レビュー | `dontAsk` と Read/Grep/Glob。編集・Bash・追加エージェント用ツールは提供しない |
| Codex レビュー | read-only sandbox と `approval_policy=never`。追加エージェント機能は無効 |
| Claude 作業 | Read/Grep/Glob/Edit/Write/Bash と auto permissions。実際の権限判定は CLI が行う |
| Codex 作業 | workspace-write sandbox と `approval_policy=never` |
| 待機 | CLI プロセス内で完了を待機。モデルによる短周期の進捗確認なし |
| 実行上限 | `--timeout` は引き継ぎを含む全 provider 合計の時間。タイムアウト時はプロセス群を終了 |
| 記録 | Vault 内 `01-Projects/agent-runs/` に依頼・コマンド・stdout・stderr・state・変更状態・結果・使用量を保存。主要記録は同一ディレクトリ内で atomic/private save |

provider の stdout / stderr は実行中から redaction collector を通して記録するため、親 runner の終了後も既に出力された内容を復元できる。再開時は終了済み provider の terminal output を先に照合し、成功記録があれば再実行せずに結果を確定する。`context-index.json` は利用可能な raw record と実行中・完了状態を列挙し、`result.json` は process identity と reconciliation 結果を保持する。壊れた結果 record は上書きせず `incomplete` として返す。stdin 配信も provider の実行 timeout の範囲で行う。

`sonnet` などのClaude公式aliasと対応する実モデルの違いは `match_type=provider_alias` として記録する。

`requested_model` は設定値、`actual_model` は provider が明示的に返した値だけを記録する。provider が model identity や usage を返さない場合、要求値や推定値で補わず `null` / `provider_did_not_report` とする。usage の集計は provider が報告した numeric fields のみを合算する。

Claude は `--safe-mode`、Codex は `--ignore-user-config` で旧カスタマイズを持ち込まず、共通方針と指定 role を明示的に渡す。Claude の `--bare` は保存済み OAuth/Keychain を使わないため採用しない。これらの設定は今回の子プロセスだけに適用し、既存の CLI 設定ファイルは変更しない。Claude の管理者ポリシーは引き続き適用される。

safe mode ではユーザーの hook・plugin・MCP も使わないため、それらに依存した独自認証 helper やツールがある環境は別途確認する。両 CLI の対応オプションは導入バージョンの help で確認する。

## Devin CLI（SWE-2）で作業する

```sh
python3 -m harness run --provider devin --devin-model swe-2-medium \
  --workspace "$AGENTS_ROOT" --prompt-file "$AGENTS_VAULT_ROOT/request.md" --timeout 900

python3 -m harness.service enroll --provider devin --model swe-2-medium \
  --task TASK_ID --workspace "$AGENTS_ROOT/worktrees/parser" \
  --prompt-file "$AGENTS_VAULT_ROOT/request.md" --context "vault://request"
```

Devinの既存ログインを利用する。モデルの選択はSWE-2の3種類に限定し、他モデルや
API keyへの自動切替は行わない。SWE-2の推論強度はモデル名末尾のmedium/high/maxで
決まり、Claude/Codex向けの `--effort` はDevinには適用しない。
通常の既定は引き続きClaude、Claudeの実際の上限時だけCodexへ引き継ぐ。

Devinはstdinから依頼を受け取らないため、`harness/devin.py` が一時ファイルと
`--prompt-file` を使って渡す。`--export` のATIF出力から最終応答、実モデル、
報告されたトークン数を読み取る。標準出力の文章や終了コード0だけでは成功にしない。
一時ファイルはprivateに作成し、永続ログはrunnerの秘密値除去を通す。

作業はDevinの `--sandbox` を使い、編集・テストをsandboxの対象となる `exec` で
行う。直接のedit/write、追加エージェント、MCP、browser previewは子設定で禁止する。
Devin固有のルール・skill等の自動読込はClaudeのsafe modeと同等には隔離できない。
既存のユーザー設定は書き換えず、子用の設定だけを生成する。
呼び出し元が `--workspace` で作業先を明示することを実行の承認範囲とし、
非対話CLIで確認画面を開けないため `--respect-workspace-trust false` を子に渡す。
この指定はDevinの信頼確認を省略するもので、OS sandboxの解除ではない。
Devinのレビュー専用read-only境界は未対応なので `review --provider devin` は
明示的にエラーにする。レビューとサービスの受入確認にはClaude/Codexを使う。
対応はDevin 3000.10.23のhelp、実際のATIF-v1.7出力と作業試験で確認した。

参考: [Devin CLI flags](https://docs.devin.ai/cli/reference/commands)、
[Devin sandbox](https://docs.devin.ai/cli/sandbox)。

## 使用量と委譲実績を確認する

```sh
python3 -m harness usage
python3 -m harness usage --since 2026-09-15T00:00:00+09:00 --json
python3 -m harness usage --run-dir "$AGENTS_VAULT_ROOT/01-Projects/agent-runs/<run-id>"
python3 -m harness doctor --probe
```

`usage` はVaultの保存済み実行を読み、provider・実モデルごとの実行件数、結果、
入力・出力・キャッシュのトークン数を表示する。CLIの推論や認証取得は行わない。
同じattemptの重複はまとめ、使用量がない記録は「未観測」として数える。
不正な記録やiCloudの未取得ファイルは取得できなかった理由を表示する。
`--run-dir` はVaultのagent-runs内に限定し、サービスの入れ子の実行も対象にできる。

Claudeの `rate_limit_event` があれば、枠の種類、許可/拒否、リセット時刻などの
providerの通知をそのまま表示する。これは記録時点の情報で、残量の割合ではない。
使用トークン数から5時間・週間枠の残量を推計しない。未観測や過去の上限通知を
理由に新しいClaudeの依頼を止めることもない。`doctor --probe` は短いClaude推論を
一度実行し、認証だけでなく実モデル・使用量・利用上限通知を保存する。
実行失敗時もCLIが返した使用量を保持する。

## Claude 利用上限からの引き継ぎ

provider のエラーイベントやエラー結果が利用上限を示した場合だけ、同じ workspace と依頼、現在の Git 状態を Codex へ一度渡す。成功した応答中の「rate limit」やツールが読んだ文章では切り替えない。

認証失効、権限不足、通信障害、ローカル予算超過、タイムアウトは別の失敗として返す。Codex 側も失敗した場合は、その結果を残して終了する。高コストモデルへの自動昇格は行わない。`--no-fallback` で切り替えを無効にできる。

引き継ぎ先は既存変更と外部操作の結果を確認して残作業を判断する。CLI セッション自体の移植、外部操作の exactly-once 実行、認証失効の自動修復を保証するものではない。毎回の呼び出しは新しい実行で、上限解除時刻の予測や継続的な監視は行わない。

## 結果の扱い

Claudeのレビュー・受入確認・Issue下書きは `--json-schema` を指定し、成功した
terminal result の `structured_output` を判定に使う。説明文からJSONらしい断片を
拾って承認へ変換しない。生の応答は引き続き保存する。
仕様は [Claude Code structured output](https://code.claude.com/docs/en/headless#get-structured-output)
を参照し、導入CLIの対応オプションと実応答でも確認する。

- レビューの指摘は利用者への承認待ちではなく、メインエージェントへの判断材料とする。メインが根拠と影響を評価し、採用する指摘の修正・検証・必要な再レビューを自律的に進める。採否と理由は Vault に記録する。
- CLI は一回の実行結果を返し、修正方針の判断はメインエージェントが担う。終了コード3を受け取っても「修正してよいか」と利用者へ差し戻さない。固定の role 順序や機械的な全指摘採用ループは導入しない。
- 明示的な approve と空の findings は、limitations に補足事項があっても承認として扱い、補足は返却原文に保持する。判断不能な場合は incomplete を使う。JSON 全体を囲むコードブロックも受け付ける。
- 終了コード0は CLI の正常終了、またはレビュー承認。作業のテスト・公開完了は実際の成果で確認する。
- 終了コード3はレビューの修正指摘。終了コード2は実行失敗、認証・上限・権限・時間制約、またはレビュー判断不能。
- 完了イベントなし、質問だけのレビュー、情報不足を承認として扱わない。
- 記録の秘密値は環境変数の既知値と代表的なパターンを伏せる。未知の形式を完全に検出する保証はなく、Vault を公開する前には別途確認する。
- `gh` は明示指定されたコマンドを通常の権限で実行する薄い入口。認証情報を出力するコマンドや対話ログインは、ユーザーのターミナルで扱う。

## 検証

モバイルアプリの検証を扱う場合は、共通ルールから参照する[モバイルアプリのテスト環境](../policies/mobile-testing.md)に従う。Simulator / Emulatorでの日常的な確認、実機での機能確認、配布用ビルドでの最終確認を分ける。iPhoneミラーリングは任意の補助機能であり、このCLIがGUI操作を提供・保証するものではない。

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

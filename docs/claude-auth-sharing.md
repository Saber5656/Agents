# Claudeの認証を起動元間で確認する

同じMacユーザーで通常のTerminal.appとCodexからClaudeを使う場合は、両方の新規CLIプロセスが有効な認証を利用できることを確認する。どちらか一方でのログイン成功だけでは、もう一方での利用や認証共有を確認したことにはならない。

## 診断の環境名が意味するもの

- `--environment current`: 呼び出し元の環境を使用する。ローカル`.env`の設定はその上に適用する。
- `--environment terminal`: 呼び出し元の環境を継承するlogin shellから、exportされた環境変数を取得する。Terminal.appを起動する機能ではない。既存Terminalのalias/function、一時的なexport、アプリ固有の実行文脈まで再現するものではない。
- `--context-label`: どこで診断を実行したかを記録する任意の自己申告ラベル。`terminal-app`と入力してもTerminal.appで実行した証明にはならない。

一つの診断は別の起動元との認証共有を検証しない。実際のTerminal.appを操作できない場合は、その側を未確認として残す。

## 同じ手順をそれぞれの起動元で実行する

まず通常Terminal.appを開き、次のコマンドを実行する。認証・推論は新しいCLIプロセスで確認する。

```sh
set -a
. "$HOME/.config/agents/environment.env"
set +a
cd "$AGENTS_ROOT"
python3 -m harness --environment current doctor --context-label terminal-app --probe
```

Codexのコマンド実行からも、同じ既存設定を読み込んで実行する。

```sh
set -a
. "$HOME/.config/agents/environment.env"
set +a
cd "$AGENTS_ROOT"
python3 -m harness --environment current doctor --context-label codex --probe
```

`--probe`はSonnetにツールなしで短い依頼を一回送り、契約利用枠を少量使う。初めに環境だけ調べる場合は省略できるが、推論成功は未確認のまま。各実行が表示する`record`の診断ファイルを比較する。

診断ファイルの次の項目を比較する。

| 項目 | 読み方 |
|---|---|
| `startup.context_label` / `context_label_source` | 起動元の自己申告。指定時のsourceは`self_reported` |
| `startup.capture_method` | 環境変数をどう採取したか。実行アプリの証明ではない |
| `startup.cross_launcher_auth_verification` | 単独診断では常に`not_performed` |
| `claude_context.current` / `selected` | 同じ呼び出しから見た、採取・`.env`適用前後のHOMEと設定先 |
| `expected_config_path` / `config_path_source` | 明示した`CLAUDE_CONFIG_DIR`、またはHOMEから予想する既定設定先。CLIが読んだcredentialの特定ではない |
| `same_home` / `same_claude_config_dir` / `same_expected_config_path` | 上記二環境の文字列比較。片側が未設定・不明なら`null` |
| `tools.claude.current_resolved_executable` / `resolved_executable` | 呼び出し元と選択環境でPATHから解決したCLIの実体 |
| `tools.claude.executables_match` | CLI実体が同じか。片方でも見つからなければ`null` |
| `environment_differences` | 認証用環境変数などの有無と一致。秘密値は含まない |
| `tools.claude.loggedIn` / `auth_status_parse` | 明示的な`true`/`false`と、読取不能の`null`を区別する |
| `claude_inference.status` / `observed_at` | `--probe`で行った推論の結果と観測時刻 |

`auth_status_parse`は正常なら`ok`、JSONを解析できなければ`unparseable`、期待する型でなければ`unexpected_type`。読取不能や明示的な未ログインは正常終了と判定しない。doctor全体の終了コードにはほかのCLIの診断も含むため、Claudeの状態は上記のClaude項目で判断する。

設定先は環境変数からの予想であり、明示した値の`~`・変数・相対パスを勝手に展開しない。文字列の不一致だけで実際の保存先が異なると断定しない。ラベルや同じパスだけで成功としない。診断中のcurrent/selectedは同一呼び出しから見た二つの環境であり、Terminal.app/Codexという別々の実行結果ではない。

HOMEや設定先・CLI実体が異なるときは、それが意図した使い分けかを調べる。設定先が一致しても、実際にどの認証情報を読んだかやKeychainのアクセス状態まではこの診断では確定しない。既存の設定・認証を確認せず削除したり、秘密トークンをコピーしたりしない。

## SSHでは成功し、MacのターミナルやCodexでは未認証になる場合

SSHでMacへ接続して実行するCLIも、Mac上の同じユーザーのプロセスである。
ただし、GUIログインとSSHではKeychainへのアクセス状態が異なることがある。
macOSでKeychainへの保存に失敗すると、Claude Codeは同じ設定ディレクトリの
`.credentials.json`（0600）を予備の保存先に使う。

導入版2.1.296で、Keychainの項目が存在するもののaccess/refresh tokenが空で、
予備ファイルに有効な認証が残る事例を確認した。この版はKeychainからJSONを読めると
予備ファイルを参照しないため、GUI側だけ未認証になり得る。
`loggedIn: false`だけで認証の失効や再ログインが必要だと断定しない。

診断ではCLIの実体とバージョン、Keychain項目の有無、トークンの有無、期限のみを
比較し、トークンの値を表示・記録しない。Keychain項目が完全に空のOAuth認証だけを
含み、他の認証情報がなく、予備ファイルの認証が利用可能であることを確認できた場合は、
その空項目だけの除去で復旧する場合がある。Keychain全体や有効な項目を削除したり、
予備ファイルを複製したりする一般的な自動修復にはしない。

シェル間でCLIが違う場合も解消する。例えばnative版と古いnpm版が同居すると、
`.zshrc`だけでPATHを設定した環境では対話シェルと非対話login shellで実体が変わる。
意図したCLIのPATH優先順位をlogin shellにも設定し、既存CLIの削除に頼らず確認する。

復旧後は各起動元の新しいプロセスで認証と短い推論を確認する。空項目の除去は
その時点の不整合の修復であり、将来のログイン失効、Keychainのロック、CLIによる
認証保存先の変更を防ぐ保証ではない。SSH側を実行できない場合は未確認と明記する。
無人実行用の認証を別途設計するときは、公式の`claude setup-token`も候補になるが、
本人認証と安全な秘密値の配布が必要で、通常のログイン修復とは分けて扱う。

## 再ログインと遠隔での本人認証

認証状態が読めない結果と、CLIが明示的に未ログインと返した結果を区別する。認証エラー、利用上限、通信失敗、推論timeoutも別の結果として扱う。

再認証が必要な場合は、実際に利用するCLIの実体と設定先を揃え、そのCLIで`claude auth login --claudeai`を起動する。本人が手元のブラウザでCLIの認証URLを開き、承認後にコードが表示されたら、同じ待機中CLIへ入力する。Mac本体のブラウザを操作する必要はない。

- 入力前に元のログイン待機プロセスが生きていることを確認する。
- 待機が終了していたら、ログインを起動し直して新しいURLとコードを使う。古いコードを別セッションへ流用しない。
- 認証コードやトークンはVault、Git、コマンド履歴へ保存しない。対話・実行ツール自身の履歴に入力が残り得る点に注意し、秘密値が記録に残らないとは保証しない。
- 成功後は新しいCLIプロセスで診断と短い推論を確認する。両起動元で使うなら両方で確認する。

認証の削除・自動ログイン・APIキーへの課金切替を診断の一部として行わない。片側の確認ができない場合は、確認できた側の利用可能性と、共有の未確認部分を分けて記録する。

参照: [Claude Code Authentication](https://code.claude.com/docs/en/authentication)、[手動ログインの公式案内](https://support.claude.com/en/articles/14552646-troubleshoot-claude-code-installation-and-authentication)。

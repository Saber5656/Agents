# 各アプリからAgents共通運用を開始する

Agentsは起動元に依存しない指示体系。Devin Desktopで始めればDevin、DiscordでHermesへ
依頼すればHermesが主担当になる。Codexや `python3 -m harness run` を経由する必要はない。
`COMMON-AGENTS.md` が正本で、各アプリには短い読み込み・保存指示を置く。
モデルによる指示遵守の確率性は残る。OS制約や認証・権限を変更する機構ではない。

## 正本と入口

| 起動元 | 常時読み込む入口 | 適用範囲 |
|---|---|---|
| Codex | `~/.codex/AGENTS.md`（既存のCOMMONへのsymlinkは保持） | このユーザーの通常セッション |
| Claude Code | `~/.claude/CLAUDE.md`（同上） | このユーザーの通常セッション |
| Devin Local / CLI | `~/.config/devin/AGENTS.md` | DesktopのDevin LocalとCLI |
| Devin Desktop Cascade | `~/.codeium/windsurf/memories/global_rules.md` | Cascadeの全workspace、6,000文字以内 |
| Hermes CLI / gateway | `~/.hermes/SOUL.md` 内の管理ブロック | 作業ディレクトリに依存しないglobal identity |
| Cursor | `~/.cursor/rules/agents.mdc`（alwaysApply） | 対応版CursorのローカルUser rule |
| その他のAGENTS対応agent | このrepoの `AGENTS.md` | このrepoを開いたセッション |

`~/.config/agents/environment.env` は既存Agents `.env` へのsymlink。
正本の環境変数とVaultの場所を一か所から解決する。グローバルなシェル環境変更は不要。
独自の `CODEX_HOME` / `HERMES_HOME` / XDG設定、別OS、別ユーザーは標準パスとは別の
環境なので、その起動元で実際の指示パスを確認して接続する。

## 導入と更新

既にインストール済みの製品だけを選ぶ。`--agents` は明示指定であり、製品検出は行わない。
未インストールのアプリへの設定だけで動作済みと扱わない。

```sh
cd "$AGENTS_ROOT"
python3 -m harness.instructions --env-file "$AGENTS_ROOT/.env" --agents codex claude devin hermes
python3 -m harness.instructions --env-file "$AGENTS_ROOT/.env" --agents codex claude devin hermes --apply
# Cursorを導入済みの端末では、上記に cursor を追加する。
```

既存のSOULやルールは保持し、`agents:begin` / `agents:end` の範囲だけ更新する。
変更前の内容は `.local/agent-instructions/backups/` にprivate保存する。
壊れたマーカー、想定外のsymlink、正本と異なる環境参照、文字数超過では上書きせず停止する。
他のアプリ設定、認証、モデル、ツール権限は変更しない。
Codex/Claudeが既にCOMMONを直接参照している場合、その参照を保持する。

読込は新しい会話で確認する。起動済みのプロンプトや圧縮済み履歴には遡及適用されない。
DevinのCustomizationsではロード済みルールの所在を確認できる。
Hermes gatewayのhook変更にはgateway再起動が必要。進行中の会話を終了させず、
既存の起動方式に沿ってidleを確認してから反映する。

## コンテキストの継続

共通指示は全起動元へ、既存Vaultの検索と作業中の記録を要求する。
依頼・資料・判断・実行・検証・未解決点を保存し、最終応答から記録へリンクする。
Devin/Cursorの全文保存は、そのセッションから取得可能なvisible recordを対象にする。
アプリが公開していない履歴まで保存できたと主張しない。
Hermesには [会話保存機構](hermes-vault-context.md) を追加し、DiscordとCLIの
ローカル履歴をモデルによる記録とは別に保存する。送信receiptだけで受信会話保存を証明しない。

クラウドDevinや別PCにはこのMacのファイルは届かない。対象端末にもこのrepoと
既存Vaultへの接続・環境設定・ネイティブ入口を用意する。Vaultへの接続がない場合は
その事実と後で取り込む記録を明示し、別の正本を勝手に作らない。
新しいアプリも、その製品が公式に読むglobal ruleからこの正本を参照する。

## 検証・復旧

`python3 -m unittest tests.test_instructions` で既存設定の保持、再実行、競合拒否、
環境/Vault不在、symlink、native文字数制限を確認する。
実機では新規セッションで主担当・指示の参照元・Vaultの場所を読ませ、短い記録を保存して
ファイルを読み戻す。設定配置、モデルの読込、実行結果を別々に記録する。

停止時は自分が設置した管理ブロックだけ除去し、直後に外部変更がない場合に限りbackupを
復元する。既存COMMONリンクや他のユーザールールを削除しない。環境symlinkも利用中の
入口がないことを確認してから外す。Hermes自動保存を止めても既存Vault記録は削除しない。

## 仕様確認元

2026-09-18に公式仕様と導入済みHermesコードを照合。

- [Devin Local](https://docs.devin.ai/desktop/devin-local)
- [Devin Rules / AGENTS](https://docs.devin.ai/cli/extensibility/rules)
- [Cascade global rules](https://docs.devin.ai/desktop/cascade/memories)
- [Cursor user rule files](https://prod.cursor.com/help/customization/rules)
- Hermes: installed `agent/prompt_builder.py` の `load_soul_md` / `build_context_files_prompt`。
  SOULはHERMES_HOMEから読み込み、AGENTSはcwdだけを探索するため、SOULに入口を配置。

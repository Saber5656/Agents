# Hermes Agent Bridge

> Codex / Claude agentsからHermes Agentを汎用I/O・tool bridgeとして呼び出すスキル。

## Quick Examples

```bash
python3 "${SKILLS_REPO_ROOT:-$HOME/dev/skills}/hermes-agent-bridge/scripts/hermes_bridge.py" \
  oneshot \
  --prompt "XでOpenAI Codexの最新動向を検索して要点を3つにして" \
  --toolsets "x-search" \
  --timeout 30
```

```bash
python3 "${SKILLS_REPO_ROOT:-$HOME/dev/skills}/hermes-agent-bridge/scripts/hermes_bridge.py" \
  send \
  --target "discord:#secretary" \
  --message "確認待ち: 明日15:00の予定を作成してよいですか？" \
  --timeout 30 \
  --receipt-dir "$AGENTS_VAULT_ROOT/hermes-receipts"
```

## What It Does

- Hermesを秘書本体ではなくtransport/tool bridgeとして扱う
- Codex / ClaudeからHermes CLIへ安全に依頼する
- 同期応答と非同期応答の違いを明確にする
- Discord返信でCodexを再開するにはMCP/event polling/runtimeが必要だと明示する
- Secretary-AIとの責務境界を守る
- `oneshot --timeout SECONDS` で呼び出しをboundedにし、timeout・実行ファイル欠落を構造化結果で返す
- `oneshot`、`send`、`list-targets` は既定30秒で終了し、`--timeout`で調整できる
- `--receipt-dir DIR` を指定すると、request/state/result/stdout/stderrをprivate receiptへ保存する
- 実行時はHermes configのprimary/fallbackを読み取り検証し、実コマンドへ`--provider openai-codex`を束縛する。未確認configは停止する
- receiptのprompt・stdout・stderrはredactし、同じ`--request-id`の再実行も既存attemptを保持する

## Triggers

- `/hermes-agent-bridge`
- Hermes Agent、Discord bridge、X検索、Hermesへのパイプ、応答回収に関する相談

See [SKILL.md](SKILL.md) for full documentation.

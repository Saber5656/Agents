# Hermes Agent Bridge

> Codex / Claude agentsからHermes Agentを汎用I/O・tool bridgeとして呼び出すスキル。

## Quick Examples

```bash
python3 "${SKILLS_REPO_ROOT:-$HOME/dev/skills}/hermes-agent-bridge/scripts/hermes_bridge.py" \
  oneshot \
  --prompt "XでOpenAI Codexの最新動向を検索して要点を3つにして" \
  --toolsets "x-search"
```

```bash
python3 "${SKILLS_REPO_ROOT:-$HOME/dev/skills}/hermes-agent-bridge/scripts/hermes_bridge.py" \
  send \
  --target "discord:#secretary" \
  --message "確認待ち: 明日15:00の予定を作成してよいですか？"
```

## What It Does

- Hermesを秘書本体ではなくtransport/tool bridgeとして扱う
- Codex / ClaudeからHermes CLIへ安全に依頼する
- 同期応答と非同期応答の違いを明確にする
- Discord返信でCodexを再開するにはMCP/event polling/runtimeが必要だと明示する
- Secretary-AIとの責務境界を守る

## Triggers

- `/hermes-agent-bridge`
- Hermes Agent、Discord bridge、X検索、Hermesへのパイプ、応答回収に関する相談

See [SKILL.md](SKILL.md) for full documentation.

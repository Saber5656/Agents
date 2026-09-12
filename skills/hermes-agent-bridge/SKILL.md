---
name: hermes-agent-bridge
description: >
  Codex / Claude / other local agents need to call Hermes Agent as a generic
  I/O and tool bridge. Use when the user says Hermes, Hermes Agent, Discord
  bridge, X search through Hermes, agent-to-Hermes pipe, Hermes response
  return, or asks whether Codex/Claude can send work to Hermes and receive the
  result. This skill must keep domain responsibility in the calling agent
  such as secretary-ai; Hermes is treated as transport/tool execution, not as
  the secretary or primary domain owner.
user-invocable: true
allowed-tools: Read, Grep, Bash
category: Utility
created: 2026-05-20
status: active
purpose: Codex / Claude agentsからHermes Agentを汎用I/O・tool bridgeとして呼び出す
argument-hint: "[x-search|oneshot|send|mcp|response-design ...]"
---

# Hermes Agent Bridge

Codex / Claude / local agentsからHermes Agentを呼び出すための汎用bridge。
このスキルはSecretary-AI、調査エージェント、開発エージェントなどの責務を引き受けず、Hermesが持つDiscord I/O、X検索、gateway、MCP、tool実行面へ安全に接続する。

## When I Activate

- ユーザーが「Hermes」「Hermes Agent」「Discord bridge」「X検索をHermes経由」「Hermesへのパイプ」と言及したとき
- Codex / Claude からHermesに依頼を送りたいとき
- HermesからCodex / Claudeへ応答を戻せるか確認したいとき
- Discordへの通知や、Hermes gateway上の会話をagent workflowに接続したいとき
- 秘書AIのI/OとしてHermesを使うが、秘書判断は`secretary-ai`に残すとき

対象外:

- Hermes Agent本体のOAuth/token/Discord gateway設定を変更する作業
- `secretary-ai`の予定・メール・タスク判断そのもの
- 認証情報、token、`.env`の中身を表示する作業
- 外部公開endpointやcallback receiverを新設する作業

## Responsibility Split

| Actor | Responsibility |
|---|---|
| Calling agent | 目的、ドメイン判断、完了条件、ユーザーへの説明 |
| This skill | Hermes呼び出し方法、request envelope、応答回収方式、制約確認 |
| Hermes Agent | Discord/gateway送信、X検索などHermes側tool実行、Hermes session管理 |
| Secretary-AI | 秘書判断、予定・メール・タスクの意味解釈、確認要否 |
| Vault | 重要な判断、実施内容、応答結果、未解決事項の正本 |

Hermesはtransport/tool bridgeであり、秘書や組織タスクのownerではない。

## Choose A Response Pattern

| Pattern | Use when | Codex/Claudeに応答が戻るか |
|---|---|---|
| `x-search` | Xの投稿・会話を検索し、出典付きで戻す。通常のX検索の第一候補 | Yes。Hermesのnative X toolをGrok OAuthに固定して呼ぶ |
| `oneshot` | X検索以外のHermes tool結果をその場で欲しい。例: Web調査、軽い外部tool実行 | Yes。`hermes -z` / `hermes chat -q` のstdoutで戻る |
| `send` | Discord/Slack等へ通知だけ送る | No。配送結果だけ戻る。人間の返信は別途取得が必要 |
| `mcp` | gateway会話履歴、live events、messages_send/readをagent側から扱いたい | Yes, if MCP client/runtime is configured and polling/waiting is running |
| `async-callback` | Hermes/Discord側の後続返信でCodexを再開したい | Skill alone is not enough. receiver, polling automation, or MCP event loop is required |

重要な結論:

- Codex側のskillだけでHermesへ依頼を送ることはできる。
- Hermesの処理結果を同期コマンドのstdoutとして受け取ることもできる。
- ただし、Discord上の後続返信やHermes gatewayからのpushでCodexを自動再開するには、Codex側skillだけでは足りない。
- 自動再開には、`hermes mcp serve`をCodex/ClaudeのMCP clientへ接続する、またはCodex automation/外部runtimeがeventsをpollしてCodex/Claude bridgeを起動する必要がある。

## Request Envelope

Hermesへ渡す前に、呼び出し元はこの形に整理する。

```markdown
## Hermes Bridge Request

| Field | Value |
|---|---|
| request_id |  |
| caller | Codex / Claude / secretary-ai / other |
| intent |  |
| hermes_pattern | x-search / oneshot / send / mcp / async-callback |
| target | cli / discord:#channel / mcp-session / other |
| required_toolsets |  |
| prompt_or_message |  |
| expected_response | final_text / delivery_status / event / conversation_history |
| timeout |  |
| safe_to_run | true / false |
| vault_log_required | true / false |
```

Do not include tokens, passwords, OAuth secrets, or private config values in the envelope.

## Runtime prerequisites

Load the established `$AGENTS_ROOT` / `$SKILLS_ROOT` / `$AGENTS_VAULT_ROOT`
from the trusted local environment. Use a Python interpreter with PyYAML
installed from `requirements.txt` beside this skill. The existing Agents
skill-tools virtual environment can be reused when available; verify its
interpreter and dependency before invocation. Missing PyYAML or an unreadable
Hermes route configuration is a concrete incomplete prerequisite, never proof
of subscription routing. Do not replace the user's Python environment.

A verified subscription inference route does not authorize extra-billed tools
or message delivery. The caller must check the specific tool's charge source
and existing user authorization before invocation; ordinary subscribed token
usage is permitted. Record all available context and redacted receipts in
Agents Vault. A `safe_to_run` field alone is not an authorization or safety check.

## Pattern: X Search Through Grok OAuth

通常のX検索にはこの入口を使う。CodexやClaudeの追加推論でHermesを操作せず、
既存Hermesの `x_search` toolを直接呼び、Grokの契約枠へ処理を分散する。

```bash
python3 "$SKILLS_ROOT/hermes-agent-bridge/scripts/hermes_bridge.py" \
  x-search \
  --query "XでOpenAI Codexの公式投稿を検索し、日付と投稿URL付きで3件返して" \
  --timeout 120 \
  --receipt-dir "$AGENTS_VAULT_ROOT/01-Projects/agent-runs/hermes-x-search"
```

- `hermes` 実行ファイルから既存の仮想環境とnative toolを解決する。別のHermes環境を作らない。
- workerは保存済みGrok OAuthを解決し、公式endpointと認証元を確認する。native toolのcredential resolverをそのプロセス内だけで固定し、Hermesの `.env` に `XAI_API_KEY` が存在してもAPIキーへ代替しない。OAuth自体の通常の更新は既存resolverが行う。
- Hermesの主推論モデル設定・gateway設定を変更しない。`hermes -z` の自動承認モードは使わず、検索toolだけを実行する。
- JSONの `tool_result` に結果、出典、実モデル、`credential_source: xai-oauth` が入る。providerが使用量を返さない場合は推定しない。
- 利用上限は終了コード75と `usage_limit`、認証失敗は `auth_error` として返す。上限時の `codex_handoff` を受け取った呼び出し元は、同じ目的と取得済みの資料を保持してCodexで残作業を継続する。X専用検索の上限そのものはCodexへ切り替えても解消しないため、公開Webで代替できる範囲と取得制限を明示し、架空のX検索結果を作らない。このCLI単体は追加のCodex推論を起動しない。

## Pattern: Oneshot Hermes Tool Call

Use this when the caller needs Hermes to execute a tool and return the result immediately.

Preferred command:

```bash
python3 "$SKILLS_ROOT/hermes-agent-bridge/scripts/hermes_bridge.py" \
  oneshot \
  --prompt "指定した公開資料を調べて要点を3つにして" \
  --provider openai-codex --model gpt-5.6-luna \
  --toolsets "search" \
  --timeout 30
```

Underlying command reference (not a substitute for the wrapper route check, timeout or receipt):

```bash
hermes -z "指定した公開資料を調べて要点を3つにして" --provider openai-codex --model gpt-5.6-luna --toolsets "search"
```

Rules:

- Treat stdout as Hermes' answer.
- Keep prompts bounded and include expected output format.
- Set `--timeout` for a bounded call. Timeout returns structured JSON with `error: "timeout"` and exit code 124; a missing `hermes` executable returns `error: "executable_not_found"` and exit code 127. No alternate provider or model is selected by this wrapper.
- `send` and `list-targets` are bounded too (30 seconds by default). Pass `--receipt-dir` to persist private request/state/result/stdout/stderr artifacts and `--request-id` when a caller needs a stable receipt identity. `resume --receipt PATH` only reads a receipt; it never reruns Hermes.
- Before process creation, the wrapper resolves and verifies the Hermes config primary/fallback route, then binds the actual command to the `openai-codex` OAuth subscription provider. API-key route variables, fallback route variables, other providers, and unavailable or malformed config are rejected; there is no paid-route fallback. An omitted route is allowed only when the resolved config primary is `openai-codex`.
- Receipt prompt/command and stdout/stderr are redacted before persistence. Request IDs are filesystem-safe, receipt files are private and atomic, and repeated IDs create a new attempt while retaining earlier records.
- Do not use `--yolo`.
- Installed `hermes -z` internally bypasses interactive approval. Use only an explicitly authorized, restricted toolset; for X search always use `x-search` above. Its native toolset identifier is `x_search`, not `x-search`.
- If the command needs network or privileged local access and the current runtime blocks it, ask for approval through the current host environment.

## Pattern: Send Message To Discord Or Another Gateway

Use this when the caller only needs to announce something.

```bash
python3 "$SKILLS_ROOT/hermes-agent-bridge/scripts/hermes_bridge.py" \
  send \
  --target "discord:#secretary" \
  --message "予定確認待ち: 明日15:00のMTGを作成してよいですか？"
```

Raw command:

```bash
hermes send --to "discord:#secretary" "予定確認待ち: 明日15:00のMTGを作成してよいですか？"
```

This returns delivery status, not the user's later reply.
If a reply must resume Codex/Claude work, use MCP/event polling or a separate runtime.

## Pattern: MCP Event Bridge

Hermes exposes a bridge surface through:

```bash
hermes mcp serve
```

The MCP server provides conversation tools such as conversation listing, message reading, event polling/waiting, message sending, and channel listing.

Use this when an agent runtime can configure Hermes as an MCP server. The useful surface is:

| Capability | Purpose |
|---|---|
| `messages_send` | send to `discord:#channel` or another target |
| `events_poll` / `events_wait` | get later gateway events and user replies |
| `messages_read` | read conversation history |
| `channels_list` | discover send targets |

If the current Codex session has no Hermes MCP tool loaded, this skill can only document or run CLI fallback commands. It cannot magically receive push events.

## Pattern: Async Callback Or Wake-Up

Use this for "ask the user on Discord, then continue when they answer".

Minimum architecture:

1. Calling agent sends a request with a stable `request_id`.
2. Hermes posts the question to Discord and records the target conversation.
3. A runtime waits for the reply by one of:
   - `hermes mcp serve` + MCP `events_wait`
   - periodic polling of Hermes sessions/events
   - a custom callback receiver that can resume Claude/Codex
4. Runtime resumes the correct agent session with the answer and original `request_id`.

Skill-only limitation:

- A skill runs only while the agent is active.
- A skill cannot passively listen after the current turn ends.
- Therefore "Hermes pushes a Discord reply back into Codex" requires a runtime, automation, or MCP event loop outside this SKILL.md.

## Secretary-AI Boundary

When used by `secretary-ai`:

- This skill handles Hermes transport only.
- `secretary-ai` decides whether a calendar event, mail draft, task, or follow-up question is needed.
- Hermes may ask the user through Discord or run X/web search if delegated.
- Hermes must not become the source of truth for secretary decisions.
- Important outcomes are logged back to Vault by the calling workflow.

## Safety Rules

- Never print or copy Hermes OAuth tokens, bot tokens, `.env` values, or credential files.
- Do not modify Hermes gateway, Discord, OAuth, or token settings unless the user explicitly asks within the already authorized task scope.
- Do not use `--yolo` from this skill.
- For external side effects, separate "prepare message" from "send message" when user confirmation is required.
- If the caller asks for a long-running listener, explain that an automation/MCP runtime is required.

## Quick Decision Guide

| User asks | Use |
|---|---|
| "HermesでX検索して結果を戻して" | `x-search` |
| "Discordへアナウンスして" | `send` |
| "Discordで質問して返答を待って続きやって" | `async-callback` design; requires runtime |
| "Hermesの会話履歴を読んで" | `mcp` if configured; otherwise inspect Hermes sessions with explicit approval/context |
| "秘書AIからDiscordに聞き返して" | `secretary-ai` decides content, this skill transports |

## Validation Checklist

- Hermesをtransport/tool bridgeとして扱っている。
- 秘書判断や組織タスク判断をこのskillに移していない。
- 応答が同期stdoutで戻るケースと、非同期runtimeが必要なケースを分けている。
- tokenやcredentialを表示していない。
- request_id、caller、pattern、target、expected_responseが明確になっている。

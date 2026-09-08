---
name: create-hook
description: >-
  Create Codex lifecycle hooks. Use when you want to create or update
  .codex/hooks.json, add a hook command, or automate behavior around Codex
  events.
---
# Creating Codex Hooks

Use this skill when a user wants Codex to run a command around a lifecycle event. Gather the scope, event, behavior, filtering, and failure policy, then edit the selected Codex hook files directly. Do not silently change a user's existing configuration for a general question.

## Choose the scope

- **Project:** `.codex/hooks.json` and scripts kept with the repository. Project configuration is loaded only after the project is trusted.
- **User:** `~/.codex/hooks.json` and a script in a user-owned path. Change this only when the user selected user-wide behavior.

Read and preserve unrelated existing entries. A hook can run with the permissions of its process, so inspect every command, path, and secret source before saving it. Never suggest a hook-trust bypass or an API-key fallback to make a hook work.

## Choose the event

Use the narrowest PascalCase Codex event:

- `SessionStart` / `SessionEnd` for session setup or teardown.
- `UserPromptSubmit` for prompt auditing or additional context.
- `PreToolUse` / `PostToolUse` for tool checks and post-run context.
- `PreCompact` / `PostCompact` for compaction bookkeeping.
- `SubagentStart` / `SubagentStop` for subagent lifecycle work.
- `Stop`, `Interrupt`, and `PermissionRequest` for turn or approval handling.

Matchers are event-specific. They are useful for `PreToolUse` tool names and `SessionStart` sources such as `startup` or `resume`; `Stop`, `Interrupt`, and `UserPromptSubmit` do not use a matcher. Confirm the current event support in the [Codex hooks guide](https://learn.chatgpt.com/docs/hooks) before relying on a less common event.

## Hook file format

Codex reads a JSON object from `.codex/hooks.json` or `~/.codex/hooks.json`; the top-level object contains `hooks` and no legacy wrapper field. If lifecycle hooks are disabled in the selected config, enable `[features].hooks = true`. Each event contains matcher groups, and each group contains `hooks` handlers:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "python3 .codex/hooks/session_start.py",
            "statusMessage": "Loading session notes",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Command handlers receive event JSON on stdin and write supported JSON on stdout. For example, `SessionStart` can return additional context, while `PreToolUse` can return a `hookSpecificOutput` with `permissionDecision: "deny"` and a reason. Exit code `2` blocks where the event supports blocking; other failures follow the event's failure behavior, so state the intended fail-open or fail-closed policy in the hook and test it.

The current CLI parses prompt handlers but skips them at runtime. Use a command handler when behavior must actually run or be deterministic. Use only the PascalCase Codex event names listed above.

## Implementation workflow

1. Inspect the selected existing `hooks.json` and keep unrelated entries.
2. Pick one supported PascalCase event and the simplest matcher that meets the need.
3. Create a command script with a valid shebang, executable permissions, bounded work, and no embedded secrets.
4. Verify each executable with `command -v` or an absolute path. Read JSON from stdin and emit only fields supported by that event.
5. Validate the JSON and run a disposable `codex --strict-config` or `codex debug prompt-input` check before testing the real event.
6. Trigger the real event in a trusted disposable project, inspect the output, and report any unsupported or untested behavior.

The CLI does not provide a `codex hooks` management command. Use the JSON file and the documented CLI/config validation paths instead.

## Safety and limits

Hook configuration is local executable configuration, not a general policy file. Preserve the user's existing files, use the smallest edit, and avoid network or credential access unless the user explicitly requested it. Do not claim a hook was tested from a static parse check alone. See the [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) for config-layer and trust behavior and the [hooks guide](https://learn.chatgpt.com/docs/hooks) for current event details.

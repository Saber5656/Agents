---
name: create-subagent
disable-model-invocation: true
description: >-
  Create a Codex custom agent role. Use when you want a specialized subagent,
  a reviewer/debugger role, or an entry under .codex/config.toml [agents].
---
# Creating Codex Custom Agents

Use this skill when a user wants a named role that Codex can choose or spawn. Gather the role name, description, instructions, model/effort defaults, and scope. Add the smallest role declaration to the selected Codex config layer, and keep the role's detailed instructions in a separate TOML config file.

## Locations and format

- **Project role:** `.codex/config.toml` and a role TOML file checked into the repository.
- **User role:** `~/.codex/config.toml` and a role TOML file under the user's Codex configuration directory.

Project config is loaded only for a trusted project. User-wide edits require the user to have selected that scope. Inspect and preserve unrelated config keys; a general request to explain custom agents does not authorize writing either file.

Declare a role in `.codex/config.toml` with `[agents.<name>]`:

```toml
[agents.code-reviewer]
description = "Reviews changes for correctness, security, and maintainability."
config_file = "agents/code-reviewer.toml"
```

The `config_file` path is resolved relative to the `config.toml` that declares the role. The referenced file is a TOML config layer. It can set supported session defaults such as `model`, `model_reasoning_effort`, `sandbox_mode`, and `developer_instructions`:

```toml
model = "gpt-5.6-luna"
model_reasoning_effort = "low"
sandbox_mode = "read-only"
developer_instructions = """
Do not delegate again unless the user explicitly requested it. Review only the requested files. Report concrete findings with evidence and run the smallest relevant check.
"""
```

Use only keys accepted by the current [Codex configuration schema](https://learn.chatgpt.com/docs/config-schema.json). The role `description` is the short selection/spawn guidance; `developer_instructions` is the longer role behavior. Role defaults do not create a hard isolation boundary from the parent session, so use sandbox and approval settings deliberately.

## Workflow

Follow the current `COMMON-AGENTS.md` and user instructions when available. Do not restore retired intake, authority manifests, fixed roles, or approval gates.

1. Inspect the selected `.codex/config.toml` or `~/.codex/config.toml` and preserve unrelated tables.
2. Choose a unique role name and a concrete description that says when the role is useful.
3. Write the role TOML with focused instructions, explicit output expectations, and appropriate model/sandbox defaults.
4. Add or update `[agents.<name>]` with `description` and `config_file`; keep paths relative to the declaring config when possible.
5. Validate TOML with `codex --strict-config` and a disposable `codex debug prompt-input` load check. Test the role in a trusted fixture before relying on it.
6. Report what was loaded and what remains untested. Do not claim that a static TOML parse proves a spawned role behaved correctly.

## Guidance and policy boundaries

Use `AGENTS.md` for repository or directory guidance shared by all roles. Use Codex execpolicy/rules or sandbox/approval configuration for command enforcement. Do not copy another provider's agent frontmatter, Claude agent metadata, or a Markdown file as a Codex custom role.

No helper skill should silently change model providers, authentication, API routes, or user configuration. Never bypass hook trust or approval controls to make a custom role load.

See the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) for current `agents` keys and project trust behavior.

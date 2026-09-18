# Agents repository entrypoint

Read `COMMON-AGENTS.md` and `skills/CURRENT-WORKFLOW.md` in full before working.
They are the canonical operating instructions for every agent using this repository.
Use the native agent that received the user's request as the lead.

Resolve the existing environment through `~/.config/agents/environment.env` when
`AGENTS_ROOT`, `SKILLS_ROOT`, or `AGENTS_VAULT_ROOT` is unset. Never invent a new Vault.
A checkout on another machine needs its own explicit environment setup; see
`docs/cross-agent-instructions.md`. Record available visible context in that Vault.

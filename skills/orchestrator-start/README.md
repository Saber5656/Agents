# Orchestrator Start

Explicit one-shot activation for the TAKT-based agent orchestrator.

## Quick Use

```text
/orchestrator-start 現在の差分を Claude にレビューさせて、typed report を返して
```

## What It Does

- Treats explicit invocation as approval to start orchestration.
- Builds an activation envelope for the harness control plane.
- Keeps normal prompts in draft/proposed state unless this skill is invoked.
- Does not approve secrets, destructive operations, publication, or scope expansion.

## Trigger

Use only with explicit invocation:

- `/orchestrator-start`
- `$orchestrator-start`
- `[$orchestrator-start](...)`
- `orchestrator-startして`

See [SKILL.md](SKILL.md) for the full contract.

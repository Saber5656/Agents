# Orchestrator Start

Start or inspect the current Agents execution service when the user has
explicitly requested that operation.

## Quick Use

```text
/orchestrator-start 現在の Agents サービスの状態を確認して、未起動なら起動して
```

## What It Does

- Reads the current service configuration and existing task/process state.
- Reuses an existing matching service and saved task instead of duplicating workers.
- Starts the current service only when the requested operation is authorized.
- Records actual execution and recovery evidence in Agents Vault.
- Ordinary actionable requests proceed under current policy without an activation envelope.
- The skill name alone never enables the retired TAKT/Saihai runtime or installs hooks.

## Trigger

These invocations select the skill; the request determines whether to inspect
or start the current service:

- `/orchestrator-start`
- `$orchestrator-start`
- `[$orchestrator-start](...)`
- `orchestrator-startして`

See [SKILL.md](SKILL.md) for the current contract and
[background-service.md](../../docs/background-service.md) for service operation.

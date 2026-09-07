---
name: orchestrator-start
description: >
  Explicit one-shot activation skill for the TAKT-based agent orchestrator.
  Use this skill only when the user explicitly invokes `/orchestrator-start`,
  `$orchestrator-start`, `[$orchestrator-start](...)`, or says
  `orchestrator-startして`. Do not auto-trigger for ordinary prompts,
  vague requests, brainstorming, or normal goal discussion. When invoked,
  treat the current request as user-approved for starting orchestration within
  the preapproved bounded scope, then create or update the typed activation
  envelope needed by the harness control plane.
user-invocable: true
allowed-tools: Read, Grep, Bash, Write, Edit
category: Operation
created: 2026-06-26
status: active
purpose: 明示 invocation された場合だけ TAKT 型オーケストレーターの goal activation gate を approved 扱いにする
argument-hint: "[実行したい依頼]"
---

# Orchestrator Start

このスキルは、通常プロンプトでは開始しない TAKT 型オーケストレーションを、ユーザーが明示的に指定した場合だけ一発始動する。

通常の prompt は draft / proposed までに留める。`/orchestrator-start` または `$orchestrator-start` が明示された場合だけ、goal activation gate を `approved` として扱い、harness control plane に渡す activation envelope を作る。

## Activation Rule

Use this skill only for explicit invocation:

- `/orchestrator-start <request>`
- `$orchestrator-start <request>`
- `[$orchestrator-start](...) <request>`
- `orchestrator-startして <request>`

Do not use it merely because a request sounds actionable. The point of this skill is to make approval explicit without adding another confirmation turn.

## What This Skill Approves

When explicitly invoked, treat the user as approving:

1. Starting orchestration for the supplied request.
2. Moving the goal candidate from `draft` / `proposed` to `approved`.
3. Creating a workflow run if deterministic selection succeeds.
4. Enqueuing the first workflow step.
5. Using preapproved bounded provider transport when the selected workflow requires it.

## What This Skill Does Not Approve

Do not treat this skill as approval for:

- secrets, credentials, auth tokens, private keys, or unrelated personal data transmission
- unbounded repository, Vault, home directory, or transcript dumps
- destructive operations
- production, billing, auth, security-boundary, or public-release changes beyond existing policy
- persistent provider/model registry changes
- new external service integrations
- publication actions such as push, PR, deploy, or release unless the workflow has a separate publication gate
- bypassing the Codex host sandbox or external-provider safety reviewer

If the request needs one of these, return `activation_status: blocked` with a concrete `approval_required_reason`.

## Required Flow

1. Parse the explicit invocation and extract the actual work request.
2. Build or update a typed intake candidate.
3. Produce a typed classification candidate.
4. Run or request deterministic workflow selection.
5. If workflow selection succeeds and no policy trigger blocks it, mark the activation envelope as `approved`.
6. If workflow selection is ambiguous, unsupported, or policy-expanding, mark it `blocked` or `waiting_human`.
7. Record the decision and evidence path in the configured task/evidence vault when working in this organization environment.

## Activation Envelope

Use this shape when emitting or writing activation state:

```yaml
activation_version: "1"
activation_source: "orchestrator-start"
activation_status: approved
approved_by: human_explicit_skill_invocation
approved_at: "<ISO-8601 timestamp>"
task_id: "<task id or provisional id>"
request_id: "<stable request id>"
goal_state_transition:
  from: proposed
  to: approved
workflow_selection:
  status: selected
  workflow_id: "<workflow id>"
  initial_step: "<step id>"
  optional_expansions: []
policy:
  bounded_provider_transport: allowed
  destructive_operations: not_approved
  publication: separate_gate_required
context_scope:
  mode: bounded_refs
  refs: []
next_action: create_workflow_run
```

For blocked cases:

```yaml
activation_version: "1"
activation_source: "orchestrator-start"
activation_status: blocked
approved_by: human_explicit_skill_invocation
task_id: "<task id or provisional id>"
request_id: "<stable request id>"
workflow_selection:
  status: blocked
  candidates: []
approval_required_reason: "<specific reason>"
next_action: ask_human
```

## Decision Rules

| Condition | Result |
|---|---|
| Explicit invocation + deterministic workflow selected + within bounded scope | `approved` |
| Explicit invocation + missing required classification field | `blocked` with missing fields |
| Explicit invocation + multiple plausible workflows | `waiting_human` or TPM refinement |
| Explicit invocation + forbidden context or secret risk | `blocked` |
| Explicit invocation + destructive/publication action | `blocked` unless separate gate exists |
| No explicit invocation | do not use this skill |

## Relationship To Goal Setter

`goal-setter` defines durable objectives and Done criteria. This skill does not replace it.

Use `goal-setter` when the user wants to formulate a long-running goal. Use `orchestrator-start` when the user intentionally wants the current request to cross the activation gate without an additional confirmation turn.

## Sandboxing Compatibility

**Works without sandboxing:** Yes, if the harness runner is installed and allowed by local policy.

**Works with sandboxing:** Partially. This skill can create local activation artifacts in writable state roots. It cannot bypass host restrictions for external provider calls. Provider execution should be performed by the user-owned harness runner, not by the front-door agent directly.

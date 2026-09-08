# Service review coordinator

`harness.service_review.decide_findings(spec, review, *, runner=None)` gives the
primary agent a durable decision record for a completed read-only code review.
The coordinator does not edit the worktree, create GitHub Issues, or treat a
provider success string as a decision.

`spec` must identify the source task and job and must provide existing
`agents_root` and `vault_root` directories:

```python
spec = {
    "task": {"id": "task_123", "purpose": "ship parser"},
    "job": {"id": "job_123", "workspace": "/work/repository", "repository": "org/repo"},
    "agents_root": "/work/agents",
    "vault_root": "/work/vault",
}
review = {
    "findings": [{"severity": "high", "file": "src/parser.py:10", "issue": "..."}],
    "evidence_links": ["vault://review/receipt.json"],
}
result = decide_findings(spec, review)
```

Each finding receives a deterministic `finding-...` ID. The configured review
runner must return JSON with exactly one decision for every ID:

```json
{
  "decisions": [
    {
      "finding_id": "finding-...",
      "decision": "adopt|reject|separate",
      "reason": "why this follows from the task and evidence",
      "evidence": ["vault://review/receipt.json"]
    }
  ]
}
```

Missing, duplicate, unknown, or malformed decisions return `status:
incomplete` and preserve the request, provider output, usage, and process
identity under `AGENTS_VAULT_ROOT/service-review/<input-digest>/`. The same
completed input returns the saved result without calling the provider again;
an incomplete result can be retried with the same input. The stored result
contains `adopted_findings`, `rejected_findings`, and `separate_task_ids`, so
the parent work unit can pass only adopted findings to its correction step.

`separate` creates a local TaskStore follow-up using
`source_task_id=<spec.task.id>` and a deterministic
`source_event_key=review:<source-task-id>:<finding-id>`. Repeating the same
completed input reuses that task. No GitHub Issue or remote write is made.

The default provider path validates a real `codex login status` containing a
ChatGPT subscription and rejects API-key or API-base routes before starting a
provider. It invokes Codex `gpt-6-astra` with `high` reasoning, `read-only`
sandboxing, and `multi_agent`, `apps`, and `plugins` disabled. A caller-supplied
runner receives the same configuration in a request object and must remain
read-only. Provider communication errors and invalid JSON remain incomplete;
they are not converted into adopt decisions.

The coordinator evaluates finding disposition only. It does not decide whether
the original implementation is correct, merge code, create a PR, or claim that
acceptance is complete. The primary agent must apply adopted findings,
re-review the resulting evidence, and keep UI-only or deployment work pending
when the supported surface is unavailable.

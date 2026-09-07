# Issueization batch

Workers record discoveries in the local `TaskStore`; they do not create GitHub Issues. The separately invoked batch in `harness.issueize` claims eligible local tasks, asks the authenticated Codex subscription surface for an English draft, creates or reuses the Issue, reads it back, and only then links the exact Issue identity to the task.

Run one repository at a time from a shell that has the existing Agents roots configured:

```sh
python3 -m harness.issueize \
  --repository Saber5656/Agents \
  --owner issue-batch-$(date +%Y%m%d) \
  --limit 20
```

The draft agent is fixed to `gpt-5.6-luna` with `low` effort, read-only sandboxing, no extra agents, and a verified `codex login status` showing a ChatGPT subscription. API keys (including `CODEX_API_KEY`) and custom API/base URL routes are rejected before the provider process starts; the batch never promotes to a higher model or routes inference to a separately billed API. A live draft is accepted only after Codex emits a successful `turn.completed` event.

Each task has a stable public marker, `<!-- agents-local-task:TASK_ID -->`. The batch uses a paginated, non-search Issue listing to reconcile that marker before every create or retry. A returned Issue is read back and must match the repository, number, exact GitHub URL, and marker before `TaskStore.link_issue(..., verified=True, readback=...)` can mark the task `issued`.

The process lifetime lock is derived from the canonical TaskStore database path and covers candidate selection, claim reconciliation, drafting, remote mutation, readback, and local linking. It closes the overlap window that a lease alone cannot cover, even when callers choose different receipt directories. Receipts under `AGENTS_VAULT_ROOT/01-Projects/issueization/receipts/` transition through `prepare`, `creating`, `linking`, and `linked`; an uncertain remote result is stored as `ambiguous` and remains reconciliation-only when the listing is temporarily stale. A later marker match is read back against the exact saved title and body before linking. Corrupt receipts are preserved and produce an incomplete reconciliation record without creating an Issue. Agent prompts, stdout, stderr, and reported usage are written as redacted private Vault artifacts; no model is inferred when Codex does not report one.

GitHub's Issue API does not provide an atomic idempotency key for this workflow. A lost response followed by an incomplete or stale remote listing therefore cannot provide a mathematical exactly-once guarantee. The durable receipt, stable marker, lifetime lock, and no-create reconciliation phase minimize that uncertainty and prevent the batch from blindly duplicating a live Issue.

No automatic issueization is activated by importing this module. The real repository and task scope must be selected explicitly at invocation time. Tests use fake TaskStore-adjacent fixtures and remote adapters; they do not create real Issues or consume a provider request.

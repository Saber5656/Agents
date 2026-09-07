# Local task store

`harness.tasks` is the durable local source of truth for work discovered by
workers and coordinators. It uses only Python's standard library and SQLite.
The default database is `$AGENTS_ROOT/.local/tasks.sqlite3`; an explicit
`--db` path can be used for an isolated fixture or another local store.

Both `AGENTS_ROOT` and `AGENTS_VAULT_ROOT` must be set to existing directories.
The store never invents a Vault directory. Evidence is stored as links (for
example `vault://runs/2026-09-08/task.json`) so the full context remains in
Agents Vault while the local record remains small and queryable.

## Worker API

```python
from harness.tasks import TaskStore

with TaskStore() as store:
    task = store.create_task(
        purpose="Fix the parser",
        source="request:42",
        repository="org/repo",
        assignee="worker-a",
        priority="P0",
        evidence_links=["vault://runs/42/result.json"],
        acceptance_evidence=["tests/test_parser.py passes"],
    )

    follow_up = store.record_discovery(
        originating_task=task["id"],
        discovery_key="review:event-17",
        purpose="Handle an unrelated improvement",
        expected_result="A regression test covers the edge case",
        repository="org/repo",
        evidence_links=["vault://runs/42/review.json"],
    )
```

`record_discovery` is idempotent for `(originating_task, discovery_key)`. A
retry returns the same task and merges new evidence and acceptance records.
It only captures a follow-up locally; it does not call GitHub or start work.

Execution is updated with optimistic concurrency:

```python
current = store.get_task(task["id"])
current = store.update_task(
    task["id"], expected_version=current["version"], execution_status="verified"
)
```

A stale `expected_version` raises `ConflictError`, so a worker cannot silently
overwrite a newer result. Execution status and issueization state are separate;
linking an Issue does not mark execution complete.

## Separate issueization batch API

The issueization batch owns external Issue creation. It claims work durably,
then records the outcome after its agent has read back the remote Issue:

```python
claim = store.claim_issueization(follow_up["id"], "issue-batch-2026-09-08")
try:
    # The separate batch agent creates or reconciles an Issue here.
    store.link_issue(
        follow_up["id"], "org/repo", 123,
        "https://github.com/org/repo/issues/123",
        claim_token=claim["claim_token"],
    )
except TimeoutError as error:
    store.mark_issueization_ambiguous(
        follow_up["id"], claim["claim_token"], str(error)
    )
```

Issueization states are `unissued`, `claimed`, `ambiguous`, `retry`, and
`issued`. A claim has an owner, token, expiry, attempt count, and diagnostic.
An ambiguous remote result is retained and can be reconciled before a retry;
the local task remains available when authentication, network, rate-limit, or
agent-output failures occur. `import_existing_issue` and `import_backlog`
link existing Issues without creating them.

Tasks can have many Issue and PR links. `link_work_unit` joins any number of
tasks, Issues, and PRs under one work unit while each task retains its own
acceptance and completion evidence (`add_acceptance_evidence` and
`add_completion_evidence`). `completion_report()` remains false when a linked
unit, acceptance result, or completion result is missing. Requirements are
also durable records: use
`create_requirement`, `link_requirement_task`, and `add_requirement_revision`
to preserve all original requirements and later scope corrections through a
partial completion or coordinator restart.

## CLI

```sh
python3 -m harness.tasks create --purpose "Investigate follow-up" --json
python3 -m harness.tasks list --issueization unissued
python3 -m harness.tasks show TASK_ID --json
python3 -m harness.tasks import /tmp/agents-issues-20260908.json \
  --repository Saber5656/Agents --json
```

The CLI has no GitHub write operation. The `import` command only records links
to Issues represented by the supplied JSON and is safe to rerun.

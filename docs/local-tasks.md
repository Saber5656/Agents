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

Collection arguments such as `evidence_links`, `acceptance_evidence`,
`completion_evidence`, `dependencies`, and work-unit link IDs must be passed as
sequences. Scalar strings, bytes, and mappings raise `TypeError` instead of
being split into characters or keys; pass a one-item list when recording one
value.

`record_discovery` is idempotent for `(originating_task, discovery_key)`. A
retry returns the same task and merges new evidence and acceptance records.
`RequirementLedger.record_followup()` uses this same local discovery boundary,
so a follow-up is visible to a later issueization batch as an `unissued` task
even if its private sidecar write was interrupted. It does not call GitHub or
start work.

Execution is updated with optimistic concurrency:

```python
current = store.get_task(task["id"])
current = store.update_task(
    task["id"], expected_version=current["version"], execution_status="verified"
)
```

A stale `expected_version` raises `ConflictError`, so a worker cannot silently
overwrite a newer result. Execution status and issueization state are separate;
linking an Issue does not mark execution complete. Updating `expected_result`
adds a revision, and a purpose update must remain nonblank. Duplicate
acceptance capture preserves an existing verified record.

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
        verified=True,
    )
except TimeoutError as error:
    store.mark_issueization_ambiguous(
        follow_up["id"], claim["claim_token"], str(error)
    )
```

`verified=True` is an explicit caller assertion that the batch read the remote
record back; the URL must exactly be
`https://github.com/{repository}/issues/{issue_id}`. A
`readback={"repository": ..., "issue_id": ..., "url": ...}` object can carry
the checked values and is validated when supplied. While a task is actively
`claimed`, `claim_token` is mandatory for `link_issue`; an ambiguous task has
no active token and may be linked only after the batch has read back the
existing remote Issue. The store never performs the remote read or Issue
creation itself.

Issueization states are `unissued`, `claimed`, `ambiguous`, `retry`, and
`issued`. A claim has an owner, token, expiry, attempt count, and diagnostic.
An expired claim is surfaced by `list_issueization_candidates()` with
`reconciliation_required=True` and cannot be reclaimed by expiry alone. The
batch must reconcile the remote outcome first (using `reconcile_expired_claim`)
and must hold its process lifetime flock around external create/reconcile work
so two live batches cannot create concurrently. An ambiguous remote result is
retained and cannot be claimed directly: after a remote marker is found, link
that existing Issue with verified readback; when no marker is found, call
`retry_issueization()` and then claim the returned `retry` task. This prevents
an uncertain remote create from being replayed blindly;
the local task remains available when authentication, network, rate-limit, or
agent-output failures occur. `import_existing_issue` and `import_backlog`
link existing Issues without creating them. Re-importing an existing Issue
merges newly discovered acceptance criteria while retaining prior verified
criteria.

Tasks can have many Issue and PR links. `link_work_unit` joins any number of
tasks, Issues, and PRs under one work unit while each task retains its own
acceptance and completion evidence (`add_acceptance_evidence` with
`verified=True`, and `add_completion_evidence`). `completion_report()` remains
false when a linked unit, unverified acceptance result, acceptance result, or
completion result is missing. For a requirement with acceptance criteria,
every criterion must match a verified acceptance record on one of its linked
tasks. Requirements are also durable records: use
`create_requirement`, `link_requirement_task`, and `add_requirement_revision`
to preserve all original requirements and later scope corrections through a
partial completion or coordinator restart.

For coordinator handoffs, `harness.context.RequirementLedger` wraps these
TaskStore calls. Requirement dependencies are durable TaskStore rows and the
sidecar retains the latest scope correction, selected unit, and a visible
follow-up projection. A handoff always returns all requirements, so selecting
one child cannot discard the remaining work. Follow-ups contain no Issue
mutation path and are left for the separate issueization batch.

The store keeps the SQLite file and sidecars private. An explicit database
parent may be mode `0755`, but group- or world-writable parent directories are
rejected because SQLite must not follow a pathname that another local user can
replace.

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

# Task lifecycle

`harness.lifecycle` keeps the local record for one work unit and coordinates
three reversible boundaries: creating or handing off a Codex App chat,
archiving the completed chat, and disposing of its merged Git worktree. The
module uses only the Python standard library. A caller supplies the supported
App backend adapter; a local record is never treated as proof that a remote
operation succeeded.

## Work-unit record

Create one record for a work unit even when it contains several local tasks or
Issues:

```python
from harness.lifecycle import LifecycleStore

store = LifecycleStore(vault / "01-Projects" / "task-lifecycle.json",
                       vault_root=vault)
unit = store.register(
    "wu-parser", purpose="Parser maintenance", repository="owner/repo",
    project_id="project-id", host_id="host-id", base_oid=full_base_oid,
    worktree=worktree, repository_path=canonical_checkout,
    branch="feat/parser", task_ids=[task_id], issue_ids=["owner/repo#14"],
    context_path=vault / "context.json",
)
```

The immutable portion includes the repository, project/host, full base OID,
worktree, branch, and linked task/Issue/PR references. Registry updates are
private, atomic replacements under a process-lifetime `flock`; a corrupt
registry is preserved and reported for reconciliation.

## App chat creation and handoff

`ChatLifecycle.create()` adds a stable
`<!-- agents-work-unit:ID -->` marker and saves a `creating` receipt before
calling the injected backend. A returned `threadId` is accepted only after a
readback proves the marker, host, exact worktree/cwd, immutable base, and task
scope. A `clientThreadId` is stored as `pending` and is never passed to a
ready-thread API.

If the create response is lost or malformed, the record becomes `ambiguous`.
The next call paginates the backend's thread listing and reconciles the marker
and client ID. A temporarily stale listing keeps the record ambiguous; it does
not issue another create request. This avoids duplicate chats when the remote
side effect happened but its response did not arrive. The adapter must expose
the supported App operation and must not be replaced by an inferred CLI
equivalent.

`handoff()` reads the current ready thread. If its current turn is still
running, it forks with the latest requirements and the existing task, Issue,
repository, base, and worktree scope. Otherwise it sends the update to the
ready thread. New ready IDs are read back before replacing the stored ID;
client-only or uncertain handoffs remain pending/ambiguous for reconciliation.

## Archive and restore

Before archive, call `set_delivery()` with verified merge, main synchronization,
and context-save results. The context path must exist inside the configured
Vault. The archive operation reads the actual ready thread, rejects a running
turn or a different task scope, archives it, and reads it back as archived.
An uncertain archive is recorded and can be retried through readback without
blindly repeating a different operation. `unarchive()` restores the same ready
thread reference and verifies the remote state. The local task and Issue/PR/
commit/context references remain in the registry.

## Worktree cleanup

`cleanup()` is available only after merge, main synchronization, and context
save. It verifies the canonical checkout and target worktree from `git
worktree list`, a clean status including ignored files, no local-only commits,
no active writer, no other worktree using the branch, and no other unfinished
work unit referencing the path or branch. It calls `git worktree remove` and
then `git branch -d`; both operations are non-force operations.

Each step is recorded in a private receipt under
`AGENTS_VAULT_ROOT/01-Projects/task-lifecycle/cleanup/`. If the process ends
after worktree removal and before branch deletion, the receipt resumes at the
branch step. A missing branch after a successful removal is accepted as an
idempotent completed state. Dirty files, active locks, local-only commits,
dependent use, or an unknown Git state stop cleanup while preserving data.

The lifecycle lock is derived from the canonical worktree path and lives under
the Vault's task-lifecycle lock directory. Writers that participate in cleanup
must hold the same lock for their process lifetime. Real user worktrees and
live chats are not removed by the tests; isolated fake backend and Git
fixtures cover the reversible and negative paths.

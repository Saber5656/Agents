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

Projectless Codex tasks pass `project_id=None`; the value is retained as
`None` and no project is inferred.

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
An existing ready thread ID remains usable while its current turn is running;
running describes the current turn, while the ID identifies the ready App
thread. Archive still rejects a running current turn.

## Archive and restore

Before archive, call `set_delivery()` with verified merge, main synchronization,
and context-save results. The context path must exist inside the configured
Vault. The archive operation reads the actual ready thread, rejects a running
turn or a different task scope, archives it, and reads it back as archived.
An uncertain archive is recorded and can be retried through readback without
blindly repeating a different operation. `unarchive()` restores the same ready
thread reference and verifies the remote state. Its in-flight receipt is
durable, so a lost response is reconciled from readback; message sends use the
same durable handoff marker. The local task and Issue/PR/commit/context
references remain in the registry.

## Worktree cleanup

`cleanup()` is available only after merge, main synchronization, and context
save. It verifies the canonical checkout and target worktree from `git
worktree list`, a clean status including ignored files, no local-only commits,
no active writer, no other worktree using the branch, and no other unfinished
work unit referencing the path or branch. It calls `git worktree remove` and
then `git branch -d`; both operations are non-force operations. Cleanup
requires an injected writer guard to prove the process-lifetime writer lock is
inactive; an absent or failed guard is unknown and stops deletion.

Each step is recorded in a private receipt under
`AGENTS_VAULT_ROOT/01-Projects/task-lifecycle/cleanup/`. If the process ends
after worktree removal and before branch deletion, the receipt resumes at the
branch step. A prepared receipt whose worktree is already absent is promoted
to `worktree_removed`, covering interruption between Git removal and receipt
update. Receipt identity includes repository, path, branch, and full HEAD; a
changed branch HEAD stops cleanup. Main synchronization reads
`git ls-remote origin refs/heads/main` and checks local reachability instead of
trusting a stale `origin/main` tracking ref. A missing branch after a
successful removal is accepted as an idempotent completed state. Dirty files,
active locks, local-only commits, dependent use, or an unknown Git state stop
cleanup while preserving data.

The lifecycle lock is derived from the resolved worktree path and lives at
`<lock_root>/workspace/<sha256(resolved_worktree)>.lock`. By default
`lock_root` is the Vault's task-lifecycle lock directory; a service caller can
pass its explicit service `lock_root` to `ChatLifecycle` or `cleanup()` so the
cleanup process and worker use the same process-lifetime lock. Writers that
participate in cleanup must hold this exact lock for their process lifetime;
one inactive probe without the lock does not establish safety. A missing branch
after a recorded worktree removal is treated as the completed branch step only
when the receipt identity matches; a recreated branch is checked for its exact
recorded HEAD and remains protected if it differs. The real Git fixture uses a
private bare remote and three worktrees, and verifies dirty/unpushed peer data
survives cleanup and restart recovery.

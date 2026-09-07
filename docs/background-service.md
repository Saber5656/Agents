# Background execution service

`harness.service` keeps scheduled work in a private SQLite database and runs
bounded concurrent attempts after the Codex App or conversation is gone. The default
database is `$AGENTS_ROOT/.local/service.sqlite3`; the database, WAL/SHM files,
service logs, and workspace lock directory are private. The service requires
the existing `AGENTS_ROOT` and `AGENTS_VAULT_ROOT` roots and reads
`$AGENTS_ROOT/.env` without executing shell expressions.

## Enroll and run

Enrollment is explicit and records the original prompt and context before a
worker starts:

```sh
python3 -m harness.service enroll \
  --task TASK_ID \
  --workspace "$AGENTS_ROOT/worktrees/parser" \
  --prompt-file request.txt \
  --context "vault://runs/request/context.json"
```

The Python API is the equivalent when a prompt file has already been read:

```python
job = store.enroll(
    task_id, workspace, original_prompt, current_context,
    model="gpt-5.6-luna", effort="low", timeout=900,
)
store.record_update(job["id"], "Accepted scope correction", ["vault://runs/update.json"])
```

The default model is `gpt-5.6-luna` with `low` effort. `timeout` applies to
one attempt; it is not a whole-task timeout. Failures remain retryable with
durable exponential backoff and no fixed whole-task retry count. A successful
provider result moves the job to `needs_verification`. It does not mark the
task complete. Call `verify` with completion evidence only after all task
acceptance records have been explicitly verified.

```sh
python3 -m harness.service run-once --json
python3 -m harness.service verify JOB_ID --evidence 'vault://runs/verified.json; merge; main-sync'
python3 -m harness.service run --poll 30
```

The service only runs tasks whose dependencies are complete and verified with
acceptance and completion evidence that includes a `main-sync` or `merged`
stage marker. A worker success enters a separate read-only verification stage;
the task is complete only after every acceptance record is verified and the
verification evidence proves merge and main synchronization. It never creates
GitHub Issues. Pending UI operations remain in task context for a later
supported UI reconciliation.

The explicit `verify` evidence must identify both the merge and main
synchronization stages; a provider success string or acceptance text alone is
insufficient.

## Restart, locks, and idle work

Only the scheduler owner performs recovery. A newly constructed `ServiceStore`
does not mutate live work. It records a process identity with every attempt;
an alive or uninspectable process remains occupied, while a definitely dead
attempt enters `reconciling` under both locks. A read-only receipt reconciler
must run before a retry. If the receipt cannot prove that resuming is safe, the
job remains `needs_verification` for the verification agent or explicit
operator evidence. An expired lease alone is not evidence that a live worker
stopped, and the batch layer must hold its process-lifetime flock around
external create/reconcile operations to prevent overlapping creates.

Each attempt acquires a workspace lock and a separate global resource lock.
This excludes two resources in one workspace and the same resource in
different workspaces. The scheduler uses a bounded worker pool; one configured
coordinator slot is reserved from the worker capacity, and idle waits use an
event rather than busy polling.

The idle loop waits through a stop event rather than polling in a busy loop.
The scheduler database retains attempts, updates, errors, and the next retry
time across process restarts.

## Authentication boundary

The production worker requires `codex login status` to succeed and rejects
API-key or API-base-url routes, including `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL`. It has no
paid-inference fallback. Tests may inject an executor and an auth check; that
does not change the production guard.

## launchd

Generate a reviewable plist without installing it:

```sh
python3 -m harness.service launchd generate --label com.example.agents
```

The plist uses the absolute `sys.executable`, a configured database path, the
Agents root as working directory, `RunAtLoad`, and `KeepAlive`. It includes
only a safe explicit `PATH` containing the resolved `codex` directory and
system directories, so launchd does not depend on an interactive shell.
Prompts, contexts, tokens, and API keys are never written into the plist. On
macOS, `install` preserves a differing existing plist in a private timestamped
backup and is idempotent; `start` checks status first. An operator can
explicitly call these methods after review; this implementation does not
activate a real LaunchAgent by itself.

App-quit and host-reboot continuation still require a live acceptance run on
the supported host. The subprocess and restart fixtures here prove durable
local recovery and lock behavior only.

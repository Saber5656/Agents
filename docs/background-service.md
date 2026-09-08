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
task complete. The service then uses a separate reserved verification slot for an
actual read-only Codex Luna/low review of the acceptance criteria, saved result
and artifacts, workspace state, and public merge/main synchronization. Review
findings are sent to the Astra/high read-only review coordinator. An adopted
finding is recorded as a repair instruction and returns the job to `retry`;
rejected findings retain their rationale, and separated findings create only a
local follow-up while the original verification continues. Call `verify` with
completion evidence only after all task acceptance records have been explicitly
verified.

A concrete cost or security boundary moves a job to `held`, which is excluded
from ready work. An operator must call `resume_held(job_id, checker)` with an
explicit read-only checker returning `{"safe": true}` after the held operation
has been addressed. A failed or unavailable checker keeps the job held; the
original attempt and all recheck diagnostics remain in the service history.

```sh
python3 -m harness.service run-once --json
python3 -m harness.service resume-held JOB_ID --recheck /path/to/safety-recheck.json --json
python3 -m harness.service verify JOB_ID --evidence /path/to/acceptance-review.json
python3 -m harness.service run --poll 30
```

The service only runs tasks whose dependencies are complete and verified with
acceptance and completion evidence that includes a `main-sync` or `merged`
stage marker, or a valid structured service acceptance receipt. A worker
success enters a separate read-only verification stage;
the task is complete only after every acceptance record is verified and the
verification evidence proves merge and main synchronization. It never creates
GitHub Issues. Pending UI operations remain in task context for a later
supported UI reconciliation.

The explicit `verify` evidence file must contain the structured acceptance
review, one verified observation for every exact criterion, and a publication
object. The service reads back the actual commit and synchronized main before
completion; a provider success string or acceptance text alone is
insufficient. The resulting private acceptance receipt is valid dependency
completion evidence when the dependent task is later scheduled.

When a worker returns a structured `publication_proposal`, the host owns the
delivery boundary. It re-reads the task worktree `HEAD`, selected-file
preimage, and selected diff, and rejects a proposal whose self-reported values
do not match. The verifier must also return a structured `publication_review`
with every finding accounted for and applied-fix evidence for adopted findings;
the worker's own approval text cannot authorize publication. The host then
calls `publish_scoped`, observes the remote `main` commit through GitHub, and
records verification only after that readback. If CI was requested, the host
queries check runs for the published SHA; a pending or failed observation keeps
the job in verification. Workers receive no additional `.git` write access.

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

Each attempt has a stable directory under the job run directory (`attempt-N`),
and the latest recorded updates are passed into that attempt. Only a dead attempt
is resumed after read-only receipt and surviving provider inspection; a live or
unknown runner/provider remains occupied. Each attempt acquires a workspace
lock. An explicit shared resource also acquires a global resource lock; the
default resource does not serialize unrelated workspaces. The scheduler uses
bounded worker and reserved verification pools, replenishes free slots as
futures complete, and idle waits use an event rather than busy polling.

The idle loop waits through a stop event rather than polling in a busy loop.
The scheduler database retains attempts, updates, errors, verification and
reconciliation states, and the next retry time across process restarts. Database
busy timeout follows the configured `timeout`; an explicitly supplied database
path whose file or immediate parent is a symlink is rejected, and all database
ancestors are checked for trusted ownership and writable modes before SQLite
opens the file.

## Authentication boundary

The production worker requires `codex login status` to succeed and rejects
API-key or API-base-url routes, including `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and
`CODEX_API_KEY`. It has no paid-inference fallback. Tests may inject an
executor and an auth check; that does not change the production guard.

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
macOS, `install` rejects symlink targets, preserves the exact bytes of a
differing existing plist in a private timestamped backup, and uses a synced
atomic replacement with restoration on failure; it is idempotent. `start`
checks status first. An operator can explicitly call these methods after
review; this implementation does not activate a real LaunchAgent by itself.

App-quit and host-reboot continuation still require a live acceptance run on
the supported host. The subprocess and restart fixtures here prove durable
local recovery and lock behavior only.

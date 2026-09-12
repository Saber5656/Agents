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
    provider="claude", model="gpt-5.6-luna", effort="low", timeout=900,
)
store.record_update(job["id"], "Accepted scope correction", ["vault://runs/update.json"])
```

## Provider routing (Claude first, Codex on quota only)

A new enrollment defaults to `provider="claude"`: the ordinary worker and the
acceptance verifier both run Claude `sonnet`/`low` first, so the separate
Codex subscription is spent only when Claude cannot serve the request. The
persisted `model` column keeps its original meaning as the configured Codex
model (`gpt-5.6-luna` by default); when Claude is primary that value becomes
the automatic fallback model instead of a second explicit selection. The
underlying `harness.runner` fallback is quota-only: it switches provider only
on an explicit `usage_limit` classification from the provider's own control
records, never on an ordinary failure or an authentication error, and never
promotes to a higher-cost model. Falling back once from Claude to Codex does
not fall back again.

Passing `provider="codex"` preserves the original single-provider semantics
exactly: the job runs only the configured Codex `model`, with no Claude
attempt and no automatic fallback. This is how an explicitly selected Codex
model keeps its original behavior. A job enrolled before this column existed
is migrated to `provider="codex"` on the next restart, so an already
scheduled or resumed job keeps running the provider it started with instead
of silently moving onto the new default; only a fresh enrollment adopts the
Claude-first default. A direct `default_executor(spec)` call that omits the
`provider` key (an older integration) also keeps the original Codex-only
routing; only `run_once`/the `Scheduler` supply the persisted job's
`provider` explicitly.

The worker's pre-attempt authentication guard mirrors this choice: a
`provider="claude"` job checks `claude auth status --json` for the actual
Claude Pro/Max subscription route, not merely `loggedIn: true` (a saved API
key also reports that): `authMethod` must be `"claude.ai"`, `apiProvider`
must be `"firstParty"`, and `subscriptionType` must be a truthy plan name. A
`provider="codex"` job keeps the original `codex login status`
ChatGPT-subscription check. Both routes still reject the existing separately
billed API-route environment variables (`OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `CODEX_API_KEY`) plus the
Bedrock/Vertex/Foundry paid-route variables
(`CLAUDE_CODE_USE_BEDROCK`, `AWS_BEARER_TOKEN_BEDROCK`,
`CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_VERTEX_PROJECT_ID`,
`CLAUDE_CODE_USE_FOUNDRY`, `ANTHROPIC_FOUNDRY_API_KEY`) before any provider
runs. An authentication failure is a distinct, non-quota outcome: it is never
classified as `usage_limit` and never triggers the Codex fallback; it
surfaces as an ordinary retryable failure (or, for a held job's
`inference_api_route` recheck, a still-blocked hold) so an operator can fix
the login instead of the job silently burning the other subscription. When a
Claude-primary worker attempt actually falls back inside `harness.runner`,
the Codex subscription login is verified before that Codex turn spends any
inference, using the same guard as the ordinary Codex path.
The guard is attached to that job's executor and preserves its streaming
records; it never replaces a shared module function used by other workers.

The acceptance verifier (`default_verifier`) follows the identical policy
independently of the worker's own provider: it always attempts Claude
`sonnet`/`low` first and falls back to Codex `gpt-5.6-luna`/`low` only when
Claude's own turn is classified `usage_limit` and the Codex subscription
login itself verifies; both provider turns share one overall time budget
(`min(job.timeout, 300)` seconds total, not per provider), so a fallback
attempt that would exceed the shared deadline is not started. Any other
verifier failure, including an authentication failure, stops immediately
with no fallback attempt. Each verification attempt's Vault record stores
its own `<index>-<provider>-command.json`/`-stdout.jsonl`/`-stderr.txt`/
`-process-state.json`/`-outcome.json`, and the returned/bound verdict
records the selected `provider`, requested `model`/`requested_model`, and
the `actual_model` and `usage` reported by the winning attempt (unreported
values stay null). In-progress fallback state takes precedence over an older
completed attempt; a quota response is never cached as a malformed verdict.
The acceptance verdict schema, criterion binding, caching and
publication-review boundaries described below are unchanged by which
provider produced the turn.

`timeout` applies to
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
explicit read-only checker returning a result bound to the exact `job_id` and
current `hold_reason`, with `{"safe": true}`, after the held operation has
been addressed. A generic safety acknowledgement is rejected. A failed or
unavailable checker keeps the job held; the original attempt and all recheck
diagnostics remain in the service history. An `inference_api_route` hold also
runs the configured subscription authentication guard again before the job is
made runnable.

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

The production worker requires a subscription login for its provider to
succeed: `claude auth status --json` (`loggedIn: true`, `authMethod:
"claude.ai"`, `apiProvider: "firstParty"`, and a truthy `subscriptionType`,
rejecting a saved API key) for the default `provider="claude"` job, or `codex
login status` for an explicit `provider="codex"` job. Either route rejects
API-key or API-base-url routes, including `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`,
`CODEX_API_KEY`, and the Bedrock/Vertex/Foundry paid-route variables. There
is no paid-inference fallback; the only automatic provider switch is the
quota-only Claude→Codex fallback described above, and that switch still
requires the Codex subscription login to succeed once it is attempted. Tests
may inject an executor and an auth check; that does not change the
production guard.

## launchd

Generate a reviewable plist without installing it:

```sh
python3 -m harness.service launchd generate --label com.example.agents
```

The plist uses the absolute `sys.executable`, a configured database path, the
Agents root as working directory, `RunAtLoad`, and `KeepAlive`. It includes
only a safe explicit `PATH` containing the resolved `claude` and `codex`
directories (whichever are actually installed) and system directories, so a
headless launchd process can discover both the Claude-first worker/verifier
and its Codex quota fallback without depending on an interactive shell.
Prompts, contexts, tokens, and API keys are never written into the plist. On
macOS, `install` rejects symlink targets, preserves the exact bytes of a
differing existing plist in a private timestamped backup, and uses a synced
atomic replacement with restoration on failure; it is idempotent. `start`
checks status first. An operator can explicitly call these methods after
review; this implementation does not activate a real LaunchAgent by itself.

App-quit and host-reboot continuation still require a live acceptance run on
the supported host. The subprocess and restart fixtures here prove durable
local recovery and lock behavior only.

The supported service entrypoint exposes its concurrency policy explicitly;
the defaults remain two total slots with one reserved coordinator/verifier
slot. A host that has validated capacity can choose the worker count without
changing the service code:

```sh
python3 -m harness.service --db "$AGENTS_ROOT/.local/service.sqlite3" run \
  --max-workers 3 --coordinator-reserved 1
```

## Explicit RequirementLedger enrollment

The coordinator can connect one already selected requirement to one already
linked TaskStore task with `enroll-selected`. This command has no backlog or
discovery mode: it refuses an unselected requirement, an unlinked task, an
unknown work unit, a missing Vault record, or a worktree whose branch does not
descend from the supplied immutable base.

```sh
python3 -m harness.service enroll-selected \
  --ledger "$AGENTS_ROOT/.local/requirements.json" \
  --requirement REQ_ID --task TASK_ID --work-unit UNIT_ID \
  --workspace "$AGENTS_ROOT/worktrees/parser" \
  --repository Saber5656/Agents --repository-path "$AGENTS_ROOT" \
  --branch task/parser --immutable-base BASE_SHA \
  --vault-reference vault://execution-01/parser/criteria.json \
  --criteria "criterion one" "criterion two" --json
```

The same operation is available as `ServiceStore.enroll_selected(...)`. It
stores the requirement/task/work-unit identity, repository/worktree binding,
immutable base, Vault reference, and criteria in the service SQLite row. A
repeat call returns the existing job, including after restart, and preserves
the original prompt and update history. Requirement and task dependencies
remain pending until their linked tasks have verified acceptance and completion
evidence; the service does not auto-create implementation tasks or GitHub
Issues. The worker receives a short selection prompt plus the Vault reference
and criteria, rather than a copied requirement narrative. An existing
work-unit may carry many tasks, Issues, and PRs through TaskStore and is never
silently created by enrollment.

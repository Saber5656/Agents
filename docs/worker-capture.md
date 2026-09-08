# Worker discovery capture

Run mode can attach a worker to an existing local TaskStore task with
`--task-id`. The runner loads the trusted `.env` supplied by the caller and
uses `AGENTS_ROOT/.local/tasks.sqlite3` unless an explicit `--task-db` is
provided. An explicit database must remain beneath `AGENTS_ROOT/.local`.
Review mode has no capture write path.

```sh
set -a; . "$AGENTS_ROOT/.env"; set +a
python3 -m harness.runner --environment current run \
  --provider codex --codex-model gpt-5.6-luna --effort low \
  --workspace /absolute/task-worktree \
  --prompt-file /absolute/request.md \
  --task-id task_... \
  --task-db "$AGENTS_ROOT/.local/tasks.sqlite3"
```

For a Codex run with capture enabled, the command grants `--add-dir` only for
the explicit TaskStore `.local` directory and the configured Agents Vault. It
does not grant a `.git` directory, arbitrary paths, or a guessed Vault. The
worker still runs with `workspace-write`, the normal repository checks, and
the configured subscription model. The `gpt-5.6-luna`/low combination is the
default; a coordinator must explicitly choose another supported model.

The worker prompt identifies the originating task and keeps two responsibilities
separate. A review defect required to finish that task is repaired and tested
as part of the assigned work. An unrelated improvement is neither implemented
nor sent to GitHub by the worker. Instead, the worker may return this envelope
in its final response:

```json
{
  "agents_worker_capture": {
    "originating_task_id": "task_...",
    "discoveries": [
      {
        "event_key": "review-20260908-1",
        "purpose": "Describe the unrelated improvement",
        "expected_result": "State an observable result",
        "repository": "Saber5656/Agents",
        "evidence_links": ["vault://..."],
        "acceptance_evidence": ["A concrete check"]
      }
    ]
  }
}
```

The host runner validates the envelope and calls
`TaskStore.record_discovery` with the originating task ID. The unique pair of
originating task and event key makes a retry idempotent while TaskStore merges
new evidence and retains corrected expected-result revisions. A malformed
envelope changes the run result to `incomplete`; it is never reported as a
successful capture. A response without an envelope records `capture_status:
none` and leaves the assigned run result unchanged.

Capture has no GitHub client and never creates, edits, or starts a discovered
task. The separate issueization batch decides when an unissued local record is
published. A worker can therefore finish its original assignment and its
available tests/delivery steps while unrelated discoveries remain local.

Run receipts retain the original prompt, command, model, workspace, Vault and
capture database paths, provider output, and TaskStore result IDs. On resume,
terminal provider output is reconciled into the local store before completion
is reported. Replaying the same event does not duplicate the discovery. A
changed originating task or capture location cannot reuse an earlier receipt;
malformed capture remains incomplete without starting another provider.

A PID with a different recorded start identity proves the old process has
terminated. A changed command alone, permission failure, or unavailable identity
remains unknown, preserving the original process until reconciliation is possible.

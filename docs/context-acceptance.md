# Requirements and visible context

`harness.context` supplies two small coordinator boundaries that do not start
providers or contact GitHub.

`RequirementLedger` uses the existing `TaskStore` requirement, revision,
acceptance, and completion APIs. Requirement dependencies are stored in the
same SQLite transaction as the requirement, while the sidecar keeps the
selection and presentation details. If the sidecar write is interrupted after
the SQLite commit, `handoff()` restores dependencies from the TaskStore.
`handoff()` returns every requirement with its latest text and revisions, even
when only one child was selected. A scope correction is therefore visible
after a coordinator restart.

`record_followup()` registers a deterministic local discovery through the
TaskStore and mirrors it in the sidecar. Repeating the call for the same
originating task and purpose reuses the same discovery task and merges
evidence. A sidecar interruption remains recoverable from the TaskStore, and
the issueization batch can see the task as `unissued`; no GitHub call or Issue
creation occurs here.

`VaultContext` requires an existing Vault root and an explicit run id. It writes
private JSONL chunks and an atomic `context-index.json`. The index points to all
available chunks and records whether the source was complete or had upstream
compaction/truncation. Visible export removes reasoning and analysis records,
encrypted payloads, and known secret values. A missing or unwritable Vault is a
failure; no guessed fallback directory is created.

The module does not claim that unavailable provider history can be reconstructed.
The runner remains responsible for live stdout/stderr capture and process
recovery; this module stores and indexes records already made available to it.

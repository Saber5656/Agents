# Requirements and visible context

`harness.context` supplies two small coordinator boundaries that do not start
providers or contact GitHub.

`RequirementLedger` uses the existing `TaskStore` requirement, revision,
acceptance, and completion APIs. Its explicitly selected sidecar adds
requirement dependencies and local-only follow-ups. `handoff()` returns every
requirement with its latest text and revisions, even when only one child was
selected. A scope correction is therefore visible after a coordinator restart.
Follow-ups are recorded locally with evidence and are intentionally not turned
into an Issue or added to the assigned implementation.

`VaultContext` requires an existing Vault root and an explicit run id. It writes
private JSONL chunks and an atomic `context-index.json`. The index points to all
available chunks and records whether the source was complete or had upstream
compaction/truncation. Visible export removes reasoning and analysis records,
encrypted payloads, and known secret values. A missing or unwritable Vault is a
failure; no guessed fallback directory is created.

The module does not claim that unavailable provider history can be reconstructed.
The runner remains responsible for live stdout/stderr capture and process
recovery; this module stores and indexes records already made available to it.

# Start the shared Agents workflow from each native app

The app receiving the user's request remains the lead. A task started in Devin
Desktop is led by Devin; a Discord request to Hermes is led by Hermes. Neither
needs to start Codex or `python3 -m harness run`. `COMMON-AGENTS.md` is the
canonical policy, with short loading and recording instructions in each app.
These instructions guide model behavior; they do not change OS restrictions,
authentication or permissions, and compliance still requires verification.

## Canonical policy and entrypoints

| App | Persistent entrypoint | Scope |
|---|---|---|
| Codex | `~/.codex/AGENTS.md` (preserve its existing COMMON symlink) | This user's normal sessions |
| Claude Code | `~/.claude/CLAUDE.md` (same) | This user's normal sessions |
| Devin Local / CLI | `~/.config/devin/AGENTS.md` | Desktop Devin Local and CLI |
| Devin Desktop Cascade | `~/.codeium/windsurf/memories/global_rules.md` | Cascade workspaces; 6,000-character limit |
| Hermes CLI / gateway | Managed block in `~/.hermes/SOUL.md` | Global identity, independent of working directory |
| Cursor | `~/.cursor/rules/agents.mdc` (`alwaysApply`) | Local user rules in supported Cursor versions |
| Other AGENTS-compatible agents | This repository's `AGENTS.md` | Sessions opened in this repository |

`~/.config/agents/environment.env` is a symlink to the existing Agents `.env`,
resolving canonical roots and Vault location in one place. Global shell changes
are unnecessary. Custom `CODEX_HOME`, `HERMES_HOME`, XDG settings, another OS or
another user need their actual entrypoint checked separately.

## Language and documentation conflicts

The shared policy and generated bootstrap use English for conversation,
progress, questions, handoffs and new titles even when input is Japanese.
[Communication policy](../policies/communication.md) preserves explicitly
requested artifact languages, existing localized artifacts and original
records, and defines concise explanations and reporting.

When descriptive documentation and existing code disagree, inspect code, tests
and observed behavior, follow established code conventions and report each
observed inconsistency in the final reply. Explicit requirements, security rules,
invariants and intended migrations still apply. Existing code is not assumed
correct merely because it exists. Record the evidence and disposition.

Changing COMMON alone is insufficient if a generated bootstrap or personal
skill still requires Japanese. Update the bootstrap and affected user-owned
skills together. Check installed copies through the preserving
[skill installation workflow](skill-installation.md). Additional GUI/account
rules, project instructions and scheduled prompts need separate inspection.
Vendor packages and historical source records are not bulk-translated.

## Install and update

Select only products already installed. `--agents` is an explicit selection,
not product detection. Writing a rule does not establish that an app uses it.

```sh
cd "$AGENTS_ROOT"
python3 -m harness.instructions --env-file "$AGENTS_ROOT/.env" --agents codex claude devin hermes
python3 -m harness.instructions --env-file "$AGENTS_ROOT/.env" --agents codex claude devin hermes --apply
# Add cursor when Cursor is installed on this machine.
```

The installer preserves existing SOUL and rule content, replacing only the
`agents:begin` / `agents:end` block. Private preimages are retained in
`.local/agent-instructions/backups/`. Broken markers, unexpected symlinks,
conflicting environment references and native size-limit violations stop the
operation before overwrite. Other app settings, credentials, models and tool
permissions are unchanged. Existing Codex/Claude links to COMMON are retained.

Verify loading in fresh conversations. Already loaded prompts and compacted
history are not retroactively updated. Devin Customizations can show loaded
rule locations. Hermes gateway hook changes require a gateway restart; apply
such changes through its existing startup method after checking for idle work,
without terminating an active conversation. This language rollout changes
instructions, not hooks, and does not itself restart the gateway.

## Context continuity

Every entrypoint directs the agent to consult the existing Vault and record work
as it proceeds. Preserve requests, references, decisions, execution, validation
and unresolved work, and link the record in the final response. For Devin and
Cursor, full preservation covers visible records actually available in that
session; do not claim inaccessible application history was saved.

The [Hermes conversation exporter](hermes-vault-context.md) records local Discord
and CLI history separately from model-authored notes. An outbound receipt does
not establish that an inbound conversation was archived.

A cloud Devin session or another computer does not receive this Mac's files
automatically. Configure repository access, the existing Vault, environment roots
and native entrypoints there. If Vault access is unavailable, state that and
retain records for later import; do not create another canonical store. Connect
new apps through their officially supported global rule entrypoints.

## Verification and recovery

`python3 -m unittest tests.test_instructions` checks preservation, repeatability,
conflict refusal, missing roots/Vault, symlinks, native size limits and migration
from Japanese managed rules while preserving original user text.

In fresh native sessions, check the lead agent, loaded policy and Vault location,
then save and read back a short record. Test English and Japanese inputs, a
clarification/error, an explicitly requested Japanese artifact and a handoff.
Conversation should remain English while original sources and requested artifact
languages are preserved. Record configuration installation, model loading and
observed behavior separately. GUI, CLI, cloud and mobile coverage are distinct.
A missing provider or authentication failure remains unverified, not passed.

For rollback, remove only the managed block installed by this workflow or
restore its preimage only after checking for later edits. Preserve existing
COMMON symlinks and unrelated user rules. Remove the environment symlink only
after confirming no entrypoints use it. Stopping Hermes archival must not delete
existing Vault records.

If Claude is used both in Terminal.app and from Codex, verify valid authentication
from a fresh CLI process in each context. `--environment terminal doctor` names
an environment-capture method and does not itself prove execution inside
Terminal.app. See [Claude authentication across launchers](claude-auth-sharing.md).

## Reference checks

The following specifications and installed Hermes code were checked on
2026-09-18; this date is historical evidence, not a claim of current testing:

- [Devin Local](https://docs.devin.ai/desktop/devin-local)
- [Devin Rules / AGENTS](https://docs.devin.ai/cli/extensibility/rules)
- [Cascade global rules](https://docs.devin.ai/desktop/cascade/memories)
- [Cursor user rule files](https://prod.cursor.com/help/customization/rules)
- Installed Hermes `agent/prompt_builder.py`: `load_soul_md` and
  `build_context_files_prompt`. SOUL loads from HERMES_HOME; AGENTS discovery was
  limited to cwd, so the global entrypoint was placed in SOUL.

## Delegate from Codex to Cursor

The official CLI can be called through `harness run --provider cursor` without
opening Cursor Desktop. See the [harness README](../harness/README.md) for initial
user authentication, explicit model selection, Vault records and execution scope.
This can coexist with the shared instructions for native sessions.

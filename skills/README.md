# Skills in Agents

User-managed source skills live under `skills/<skill-name>/` in the Agents
repository. `$AGENTS_ROOT`, `$SKILLS_ROOT`, and `$AGENTS_VAULT_ROOT` come from the
ignored local `.env`; `.env.example` documents the portable variables.

Each skill contains `SKILL.md` and optional `agents/`, `references/`, `scripts/`,
`evals/`, and `tests/` directories. Vendor-owned `.system/` and plugin packages,
local evaluation output, and installed-only user skills retain separate
provenance. Do not overwrite an installed-only skill or local customization.

Follow [COMMON-AGENTS.md](../COMMON-AGENTS.md) and the current [policies](../policies/).
Retired Saihai intake, authority, manifests, fixed role chains, and approval gates
are not prerequisites. Historical references are evidence only. Normal assigned
work includes validation, pre-commit review, accepted finding remediation, and
when requested publication, merge, main synchronization and chat organization.
New unrelated discoveries are captured locally for the separate issueization
batch; they do not expand the assigned implementation or block its completion.

See [preserving installation](../docs/skill-installation.md) for inventory,
file-specific deployment, effective digest read-back, and preimage rollback.
Never point every consuming surface at a replacement root without first
reconciling existing copies and preserving local edits. App reload and real
behavioral verification are distinct from source edits and filesystem checks.

Reusable examples must use relative paths or environment variables. Private
context, credentials, personal paths and runtime artifacts stay outside tracked
files. Inspect selected diffs and unpublished commits before push. Keep each
commit independently understandable and reversible, with appropriate tests and
pre-commit review. Never bypass hooks or repository protection.

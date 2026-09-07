# Managed Local Repositories

This file is the operational copy used by the `merge` skill script.
The public copy must not contain personal paths, account names, or private
repository locations.

For a real machine, create an ignored local override at:

```text
skills/merge/references/managed-repositories.local.md
```

The script prefers that local file when it exists. Keep the same table schema
and set `include` to `true` only in the local override.

## Policy

| Field | Value |
|---|---|
| trigger | `マージして` resolves repos that `pull` blocked: commit-first (or stash) then safe merge |
| push | forbidden |
| reset_hard | forbidden |
| delete_or_clean | forbidden |
| dirty_repo_with_remote_updates | commit local work first, then merge |
| conflict | abort merge and report (no auto-resolution) |

## Public Template

| name | path | repo_kind | remote | default_branch | include | management_source | notes |
|---|---|---|---|---|---|---|---|
| shared-task-vault | `<AGENTS_VAULT_ROOT>` | vault | origin | main | false | local-only | Shared task/evidence vault. Real path belongs in `managed-repositories.local.md`. |
| personal-vault | `<PERSONAL_VAULT_ROOT>` | vault | origin | main | false | local-only | Personal notes vault. Real path belongs in `managed-repositories.local.md`. |
| dotfiles | `<DOTFILES_ROOT>` | source | origin | main | false | local-only | Local runtime and CLI configuration. Real path belongs in `managed-repositories.local.md`. |
| skills-repo | `<SKILLS_REPO_ROOT>` | skills | origin | main | false | local-only | Skill source repository. Real path belongs in `managed-repositories.local.md`. |

## Exclusion Rule

Do not add a local Git repository here just because it exists on disk.
It must be intentionally managed by the AI-agent workflow or explicitly approved by the user.
Keep this table in sync with the `pull` skill copy.

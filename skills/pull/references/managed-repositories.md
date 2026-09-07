# Managed Local Repositories

This file is the operational copy used by the `pull` skill script.
The public copy must not contain personal paths, account names, or private
repository locations.

For a real machine, create an ignored local override at:

```text
skills/pull/references/managed-repositories.local.md
```

The script prefers that local file when it exists. Keep the same table schema
and set `include` to `true` only in the local override.

## Policy

| Field | Value |
|---|---|
| trigger | `プルして` means fetch + safe merge for every included repository |
| push | forbidden |
| reset_hard | forbidden |
| delete_or_clean | forbidden |
| dirty_repo_with_remote_updates | block merge |
| conflict | abort merge and report |
| vault_gitdir_layout | Vault worktree may live in a synced folder while `.git` is a `gitdir:` pointer to external metadata outside the synced folder. |
| vault_canonical_writer | Choose one canonical writer host for periodic vault snapshot commits. |
| vault_non_writer_alignment | On non-writer devices, after fetch, if `git diff --quiet <upstream> -- .` succeeds, align HEAD/index with `git reset --mixed <upstream>` only. |
| vault_forbidden_recovery | Do not use `git reset --hard`, deletion, or duplicate snapshot commits to clear metadata-lag dirty state. |

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

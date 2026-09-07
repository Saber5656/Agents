# Push Repo Profiles

This file describes repository kinds for the `push` skill.
Default branch push permission is not decided here. The canonical allowlist is `main-push-repos.md`.

## Default Policy

| Field | Default |
|---|---|
| `repo_kind` | `other` |
| `auto_push` | `working_branch_only` |
| `default_branch_push` | `deny_unless_listed_in_main_push_repos` |
| `allowed_remotes` | `origin` |
| `allowed_working_branches` | any non-default, non-protected branch |
| `blocked_branches` | `release/*`, `production/*`, `staging` |
| `default_branch_deny_patterns` | see `default-branch-deny-patterns.md` |

Repositories not listed in `main-push-repos.md` use this branch policy:

| Branch kind | Auto push |
|---|---|
| default branch | false |
| working branch | true |

## Profiles

### source

Use for product or project source code repositories.
Repositories under `${DEV_REPO_ROOT}/*` use this profile by default.

| Field | Value |
|---|---|
| `auto_push` | `working_branch_only` |
| `default_branch_push` | `deny_unless_listed_in_main_push_repos` |
| `allowed_working_branches` | any non-default, non-protected branch |
| `blocked_branches` | `release/*`, `production/*`, `staging` |
| `workspace_policy` | task-specific git worktree required for `${DEV_REPO_ROOT}/*` |

## Path Pattern Defaults

| path_pattern | repo_kind | default_branch_push | working_branch_push | workspace_policy | notes |
|---|---|---|---|---|---|
| `${DEV_REPO_ROOT}/*` | `source` | `deny_by_default_branch_deny_pattern` | `automated_when_preflight_passes` | `task_worktree_required` | Do not reuse an arbitrary current non-default branch. Start work from the planned task branch and git worktree. |

### vault

Use for context-management vault repositories.
Vault repositories still need explicit listing in `main-push-repos.md` before default branch push is allowed.
When listed, task work should still start in a task-specific branch / worktree and be integrated to the default branch at publication time.

| Field | Value |
|---|---|
| `auto_push` | `working_branch_or_whitelisted_default` |
| `default_branch_push` | `deny_unless_listed_in_main_push_repos` |
| `allowed_working_branches` | any non-default, non-protected branch |
| `blocked_branches` | `release/*`, `production/*`, `staging` |

### skills

Use for skills repositories.
When listed in `main-push-repos.md`, default branch push is allowed after task branch integration.
Routine skill edits still use task-specific worktrees unless the task is explicitly read-only or emergency policy records `branch_action: none`.

| Field | Value |
|---|---|
| `auto_push` | `working_branch_or_whitelisted_default` |
| `default_branch_push` | `deny_unless_listed_in_main_push_repos` |
| `allowed_working_branches` | any non-default, non-protected branch |
| `blocked_branches` | `release/*`, `production/*`, `staging` |

## Known Repository Defaults

| repo_root | repo_kind | default_branch_push | notes |
|---|---|---|---|
| `${SKILLS_REPO}` | `skills` | `allow_when_listed_in_main_push_repos` | Start task work in a task-specific worktree branch, then integrate into `main` and push `main`. |
| `${DOTFILES_ROOT}` | `other` | `allow_when_listed_in_main_push_repos` | Start task work in a task-specific worktree branch, then integrate into `main` and push `main`. |
| `${AGENTS_VAULT_ROOT}` | `vault` | `allow_when_listed_in_main_push_repos` | Start task work in a task-specific worktree branch, then integrate into `main` and push `main`. |
| `${USER_VAULT_ROOT}` | `vault` | `allow_when_listed_in_main_push_repos` | Start task work in a task-specific worktree branch, then integrate into `main` and push `main`. |

Add source-code repositories to `main-push-repos.md` only when default branch push is intentionally allowed.

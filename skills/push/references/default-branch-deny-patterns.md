# Default Branch Deny Patterns

This file records explicit path patterns where default branch push must be denied by policy.
It complements `main-push-repos.md`, which is the allowlist for default branch push.

If a repository matches both this file and `main-push-repos.md`, stop and record a policy conflict instead of pushing.
Resolve the conflict only after a human explicitly updates the policy.

## Deny Patterns

| path_pattern | repo_kind | denied_branches | working_branch_push | workspace_policy | reason |
|---|---|---|---|---|---|
| `${DEV_REPO_ROOT}/*` | `source` | `main`, `master`, branch resolved from `origin/HEAD` | automated when task-owned paths are clean, unrelated dirty paths are recorded, remote is allowed, and branch is non-protected; eligible first publication may create upstream with `git push -u origin <branch>` | task-specific git worktree required | Product / project repositories under `~/dev` must not auto-push default branches. Work starts in a task-specific local branch and git worktree. |

## Default-Branch Push Repositories

These repositories are intentionally allowed to push their default branch when listed in `main-push-repos.md`.
They are not exempt from task branch / task worktree switching.
Task work should start in a task-specific branch / worktree and be integrated to the default branch before default branch push.

| repo_root | default_branch_publication_flow |
|---|---|
| `${SKILLS_REPO}` | task worktree branch -> merge to `main` -> push `main` |
| `${DOTFILES_ROOT}` | task worktree branch -> merge to `main` -> push `main` |
| `${AGENTS_VAULT_ROOT}` | task worktree branch -> merge to `main` -> push `main` |
| `${USER_VAULT_ROOT}` | task worktree branch -> merge to `main` -> push `main` |

# Main Push Repositories

This file is the canonical whitelist for default branch push permission in the `push` skill.

If a repository is not listed here, default branch push is denied regardless of `repo_kind`.
Working branch push remains allowed when task-owned paths are clean, unrelated dirty paths are recorded, the remote is allowed, and the branch is not protected. Existing upstream uses plain `git push`; eligible first publication of a task-owned working branch may create upstream with `git push -u origin <branch>`.
Explicit default-branch deny patterns are recorded in `default-branch-deny-patterns.md`.
If a repository appears to match both files, stop and record a policy conflict instead of pushing.

## Policy

| Branch kind | Auto push |
|---|---|
| default branch listed here | true |
| default branch not listed here | false |
| working branch | true |
| protected branch such as `release/*`, `production/*`, `staging` | false |

Default branch means `main`, `master`, or the branch resolved from `origin/HEAD`.

Repositories listed here permit default branch push in `Branch Plan`.
This is permission to push the default branch after a task branch has been integrated, not permission to do normal task work on the default branch and not an exemption from task worktree isolation.
The task planning layer should normally provide a task-specific branch / worktree for these repos and set publication flow to `merge_to_main_and_push`.

## Alias Resolution

Whitelist rows may use public `${...}` aliases so this file does not publish
machine-specific paths. The `push` skill must resolve aliases before comparing
rows with `git rev-parse --show-toplevel`.

Resolution order:

1. Environment variables.
2. Ignored `.directory-path.local.md` values when available.
3. Deterministic local fallback for aliases that can be inferred from the
   current skill repository, such as `${SKILLS_REPO}`.

Unresolved aliases are configuration errors. Do not compare the literal alias
text to the repository root; stop with `blocked_reason:
whitelist_alias_unresolved`.

## Whitelist

| repo_root | default_branch | remote | reason |
|---|---|---|---|
| `${SKILLS_REPO}` | `main` | `origin` | Skills repository; default branch push is allowed after task worktree branch integration. |
| `${DOTFILES_ROOT}` | `main` | `origin` | Dotfiles repository; default branch push is allowed after task worktree branch integration. |
| `${AGENTS_VAULT_ROOT}` | `main` | `origin` | Shared context-management vault; default branch push is allowed after task worktree branch integration. |
| `${USER_VAULT_ROOT}` | `main` | `origin` | Personal context-management vault; default branch push is allowed after task worktree branch integration. |

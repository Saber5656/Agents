# GitHub Guards

The `push` skill is a local safety gate. Source repositories should also use GitHub-side guards for default branches.

## Recommended Source Repo Settings

- Protect the default branch with GitHub rulesets or branch protection.
- Require pull requests before merging, especially for repositories under `${DEV_REPO_ROOT}/*`.
- Disallow force pushes.
- Restrict bypass permissions where possible.
- Keep automatic push allowed for working branches such as `codex/*`, `feature/*`, and `fix/*`.

## Publication Flow

| Repo group | Flow |
|---|---|
| `${DEV_REPO_ROOT}/*` | task worktree branch -> push working branch -> create PR -> merge through GitHub |
| `main-push-repos.md` listed repos | task worktree branch -> merge to default branch locally or through approved integration step -> push default branch |

The `push` skill does not configure these settings. It records the local policy decision and stops unsafe pushes before calling `git push`.

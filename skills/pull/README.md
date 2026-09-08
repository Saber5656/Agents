# pull

> Managed local repository fetch + safe merge workflow.

## Quick Use

```bash
python3 skills/pull/scripts/pull_managed_repos.py --dry-run
python3 skills/pull/scripts/pull_managed_repos.py --execute
# Limit the operation to one configured repository.
python3 skills/pull/scripts/pull_managed_repos.py --execute --repo-name skills-repo
```

## Trigger

- `プルして`
- `pullして`
- `全リポジトリをプルして`
- `管理 repo を最新化して`

## Safety

- No push.
- No force.
- No `git reset --hard`.
- No deletion or cleanup.
- Dirty repos with remote updates are blocked before merge.
- Merge conflicts are aborted and reported.
- Detached, dirty, divergent, missing-upstream, and remote-failure states are
  reported with the local checkout preserved. A named repository operation does
  not touch other configured repositories.

See [SKILL.md](SKILL.md) for full workflow details.

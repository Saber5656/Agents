# pull

> Managed local repository fetch + safe merge workflow.

## Quick Use

```bash
python3 skills/pull/scripts/pull_managed_repos.py --dry-run
python3 skills/pull/scripts/pull_managed_repos.py --execute
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

See [SKILL.md](SKILL.md) for full workflow details.

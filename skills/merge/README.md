# merge

> Resolve dirty/diverged managed repos: commit-first (or stash) then safe merge.

Companion to the [`pull`](../pull/) skill. `pull` blocks dirty repos with remote
updates; `merge` resolves them.

## Quick Use

```bash
python3 skills/merge/scripts/merge_managed_repos.py --dry-run
python3 skills/merge/scripts/merge_managed_repos.py --execute
python3 skills/merge/scripts/merge_managed_repos.py --execute --stash
```

## Trigger

- `マージして`
- `mergeして`
- `dirty な repo をマージして`
- `commit してからマージして`
- `pull がブロックした repo をマージして`

## Safety

- No push.
- No force.
- No `git reset --hard`.
- No deletion or cleanup.
- Local work is preserved commit-first (default) or by stash before merging.
- Merge conflicts are aborted and reported, never auto-resolved.
- Normal-risk local integration does not wait for an agent/bot review; focused validation and the integrated change
  set's full validation are the quality gates.
- Check concrete permission, secret-handling, and data-loss operations against the current authorization;
  perform review and revalidation according to the change impact, without a fixed review-count limit.
- GitHub PR URLs, PR-number merge requests, merge queues, and auto-merge are explicit negative triggers.
- Mixed local/PR requests fail closed with no fetch, commit, stash, or local merge. An explicit PR request with
  repository, base, head, and PR identity may be handed to `pr-merge-gate`; missing identity or scope remains pending.
- GitHub PR merge is routed to `pr-merge-gate` when the current request contains
  the required repository, base/head and PR identity. A bare PR URL/number is
  inspected for missing identity and remains pending until the request scope is
  clear; no legacy envelope is required.

See [SKILL.md](SKILL.md) for full workflow details.

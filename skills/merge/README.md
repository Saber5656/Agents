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
- Permission expansion, authentication secrets, and data-loss risk receive at most one limited review.
- GitHub PR URLs, PR-number merge requests, merge queues, and auto-merge are explicit negative triggers.
- Mixed local/PR requests fail closed with no fetch, commit, stash, or local merge. A complete profile-bound PR
  handoff may be handed to `pr-merge-gate`; otherwise ask only for the missing identity/scope/authorization choice.
- GitHub PR merge is routed to `pr-merge-gate` only when a complete `trusted_local_v1` host authority/report or
  explicitly selected `legacy_managed` Saihai envelope carries the required identity; a bare PR URL/number returns
  the handoff requirement without local merge.

See [SKILL.md](SKILL.md) for full workflow details.

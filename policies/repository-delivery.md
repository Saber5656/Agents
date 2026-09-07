# Agents repository delivery

This policy applies to `Saber5656/Agents`. It records the user direction of
2026-09-08 and takes precedence over generic skill defaults for this repository.

- Split changes into the smallest independently reversible units of intent.
- Review every unit before committing. Reproduce and repair adopted findings,
  run affected checks, and record every finding disposition and evidence in Vault.
- The coordinating agent integrates reviewed worker commits into clean `main`
  and pushes directly to the verified existing origin without force. Preserve
  concurrent work; reconcile remote advancement before pushing. Read back the
  exact remote commit and check applicable CI after publication.
- A PR is optional here. If used, inspect current agent and human reviews before
  merging. Do not ignore outstanding findings. Fix accepted findings and record
  the evidence for rejected findings before resolving them and merging.
- Direct push does not remove review, testing, public-content inspection,
  deployment, actual acceptance, main synchronization or chat cleanup duties.
- This is repository-specific authorization. It does not authorize default-branch
  writes elsewhere, bypass protection, alter credentials or rewrite public history.

Workers keep isolated worktrees and return reviewed commits with tests and
finding dispositions. A worker does not compete with the coordinator to push
`main`. Existing PR and commit evidence stays linked to each Issue even when
later repairs are delivered directly.

# Preserving Git delivery

`harness.delivery` provides local primitives for the current Agents workflow.
It does not require a retired runtime, role chain, manifest or approval gate.
User-authorized delivery still requires validation and pre-commit review of the
actual selected changes. Follow [the Agents repository delivery policy](../policies/repository-delivery.md):
reviewed minimal commits normally go directly to main under the coordinating
agent. Never force push or bypass hooks or protection.

`prepare_worktree(repo, path, branch, full_base_oid)` creates an isolated task
checkout without touching a dirty primary checkout. Repeated setup reuses only
the matching path/branch whose HEAD descends from that base. An existing branch
without a matching worktree requires explicit reconciliation. This helper is not
a scheduler or a writer lock; the task service owns concurrent worker exclusion.

For a mixed same-file change, preserve the primary index by creating an alternate
index from `HEAD`, applying the reviewed patch with
`skills/commit/scripts/stage_approved_patch.py --index-file`, and running the
commit with that same `GIT_INDEX_FILE`. This lets one purpose be committed while
the other staged/unstaged purpose remains byte-for-byte in the original checkout.

`public_text` rejects detected secrets and personal home paths before public
transmission. With `english=True`, Japanese/CJK text requires English rewriting.
This is a conservative language check, not a complete language/secret classifier.
Inspect unpublished commit content and generated bodies, including edits/comments,
through the same check. Full private evidence belongs in Agents Vault.

```sh
python3 -m harness.delivery check-public pr-body.md --english
python3 -m harness.delivery merge --repo owner/repository --pr 123 \
  --branch <observed-base-branch> --merge-method merge \
  --head <reviewed-head-oid> --base <observed-base-oid>
python3 -m harness.delivery sync --repo "$AGENTS_ROOT" \
  --remote <verified-origin-url> --merge-sha <verified-merge-oid>
```

Merge requires live discovery of rules and branch protection, current successful
checks, current head/base, and no unresolved current review thread or change
request. Failed/unavailable discovery is incomplete. All review-thread pages are
read twice around the final PR observation; a changed thread set requires a fresh
review. The target branch name is verified even when branch OIDs match. Required
check producer IDs are retained and matched against paginated authenticated
check-run metadata for the reviewed head. A previously merged matching PR is
read back without another mutation.
The GitHub operation pins the expected head and respects native protection;
select `merge`, `squash` or `rebase` only when that method is enabled by the
repository policy.
GitHub has no atomic base-OID condition on this operation. Do not claim a merge
queue, atomic base pin or atomic review-thread lock. Native conversation
resolution protection is necessary for server-side enforcement against a review
arriving after the last read; local reobservation alone cannot eliminate that race. A pending remote outcome must be reconciled before retry.

Main sync verifies origin, requires the intended clean attached branch, fetches
and checks that remote main contains the merge, then fast-forwards. Dirty,
divergent, detached and wrong-branch checkouts are retained with a specific
blocker. Repeating sync after the same readback is a no-op and creates no extra
commit. Dependents must wait for this observed synchronization; independent work
can continue. Archival is a separate supported App action after durable context
save and actual merge/sync.

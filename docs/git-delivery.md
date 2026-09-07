# Preserving Git delivery

`harness.delivery` provides local primitives for the current Agents workflow.
It does not require a retired runtime, role chain, manifest or approval gate.
User-authorized delivery still requires validation and pre-commit review of the
actual selected changes. Never push main, force push, bypass hooks or protection.

`prepare_worktree(repo, path, branch, full_base_oid)` creates an isolated task
checkout without touching a dirty primary checkout. Repeated setup reuses only
the matching path/branch whose HEAD descends from that base. An existing branch
without a matching worktree requires explicit reconciliation. This helper is not
a scheduler or a writer lock; the task service owns concurrent worker exclusion.

`public_text` rejects detected secrets and personal home paths before public
transmission. With `english=True`, Japanese/CJK text requires English rewriting.
This is a conservative language check, not a complete language/secret classifier.
Inspect unpublished commit content and generated bodies, including edits/comments,
through the same check. Full private evidence belongs in Agents Vault.

```sh
python3 -m harness.delivery check-public pr-body.md --english
python3 -m harness.delivery merge --repo owner/repository --pr 123 \
  --head <reviewed-head-oid> --base <observed-base-oid>
python3 -m harness.delivery sync --repo "$AGENTS_ROOT" \
  --remote <verified-origin-url> --merge-sha <verified-merge-oid>
```

Merge requires live discovery of rules and branch protection, current successful
checks, current head/base, and no unresolved current review thread or change
request. Failed/unavailable discovery is incomplete. All review-thread pages are
read. A previously merged matching PR is read back without another mutation.
The GitHub operation pins the expected head and respects native protection;
GitHub has no atomic base-OID condition on this operation. Do not claim a merge
queue or atomic base pin. A pending remote outcome must be reconciled before retry.

Main sync verifies origin, requires the intended clean attached branch, fetches
and checks that remote main contains the merge, then fast-forwards. Dirty,
divergent, detached and wrong-branch checkouts are retained. Dependents must wait
for this observed synchronization; independent work can continue. Archival is a
separate supported App action after durable context save and actual merge/sync.

# Skill provenance and preserving installation

The canonical user-managed source is `$SKILLS_ROOT` within `$AGENTS_ROOT/skills`.
Set all three roots in the ignored local `.env`; no command creates a replacement
Vault. Installed-only skills remain separate until their provenance is reconciled.
Vendor packages are read-only inventory inputs, never deployment destinations.

```sh
python3 -m harness.installation inventory --installed "$HOME/.agents/skills" \
  --installed "$HOME/.codex/skills"
```

Supply actual vendor skill roots with repeatable `--vendor` arguments. Inventory
reports exact SKILL.md digests, resolved paths, canonical/installed-only status
through the source field, and vendor ownership. Aliases resolving to the same
file are counted once. Inventory is filesystem evidence, not proof that an App
has reloaded a skill. A source-only edit remains deployment-pending until the
consuming surface reads the intended content.

Inspect and reconcile a differing file before deploying. Select one relative
file and pass the installed digest from inventory (or a separately measured
sha256 for auxiliary files). `absent` means the selected file must not exist.

```sh
python3 -m harness.installation deploy --skill skill-manager \
  --target "$HOME/.agents/skills" --file SKILL.md --expected <inspected-sha256>
python3 -m harness.installation rollback <private-receipt-path>
```

Each selected file is replaced atomically only after retaining its preimage and
receipt in Agents Vault. Read-back verifies the effective digest. Unselected
files, directories and other skills are untouched. Existing file permissions are
preserved. Selected symlinks and path escapes are rejected. Installation roots
may themselves be known symlinks; deploying onto the source returns
`already_effective`. A package with several changed files needs one explicitly
reviewed operation per file; this is not a whole-package atomic transaction.

Rollback verifies both the current installed digest and saved preimage. Later
user edits prevent rollback. A crash after replacement but before final receipt
persistence can be rolled back from the prepared receipt. The Vault lock
serializes this tool's operations; unrelated editors do not honor that lock,
so pause editing the selected file during installation. A filesystem read-back
alone is not an App refresh or behavioral acceptance result.

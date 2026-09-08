---
name: update-cursor-settings
description: >-
  Modify settings for an explicitly identified Cursor or VS Code editor in its
  product-specific settings.json. Use only when the user names that editor;
  report an absent installation instead of creating a guessed settings path.
---
# Update Cursor or VS Code editor settings

This skill applies to a named Cursor or VS Code editor. It does not modify
Codex CLI `config.toml`, Codex App GUI preferences, or a generic file called
`settings.json`.

## Resolve the product before the path

The target product must be explicit. Resolve the installation and inspect the
path before writing. Do not treat a missing file as permission to create a new
product configuration.

| Product | macOS | Linux | Windows |
| --- | --- | --- | --- |
| VS Code | `~/Library/Application Support/Code/User/settings.json` | `~/.config/Code/User/settings.json` | `%APPDATA%\\Code\\User\\settings.json` |
| Cursor | `~/Library/Application Support/Cursor/User/settings.json` | `~/.config/Cursor/User/settings.json` | `%APPDATA%\\Cursor\\User\\settings.json` |

Codex CLI requests belong to `update-cli-config` and use
`$CODEX_HOME/config.toml`. Codex App requests require the supported App/UI
surface; do not substitute an editor JSON file.

If the named product or its settings path is absent, report the exact product
and path checked, then stop without creating or editing anything.

## Safe edit procedure

Read the file bytes, mode, symlink target, and SHA-256 preimage first. Parse the
editor's JSON-with-comments format with a parser that preserves comments and
ordering, or use the editor's own supported settings command. Change only the
requested key and retain unrelated keys and comments. Do not replace the file
with a newly generated JSON document when the parser cannot preserve its
format.

Reject malformed JSONC, an unsupported option, or a value with the wrong type
before writing. Recheck the preimage immediately before the write; an external
change is a conflict and must remain intact. After writing, parse and read back
the requested value and verify unrelated content. Restore the preimage only
when the current digest is the digest produced by this operation; otherwise
refuse rollback and report the conflict.

Use a disposable product-specific fixture to exercise modification,
preservation, malformed input, unknown-key rejection, conflict, and rollback.
Do not claim that fixture success proves the real editor consumed the value;
report that effective application separately.

---
name: update-cli-config
description: >-
  View or modify the user-managed Codex CLI configuration at the resolved
  CODEX_HOME/config.toml. Use when the user explicitly asks to change a
  Codex CLI setting or feature flag. Do not use this skill for Cursor, VS Code,
  or Codex App settings.
---
# Update Codex CLI configuration

This skill is for the Codex CLI product. It does not read or write
`~/.cursor/cli-config.json`, editor `settings.json`, or Codex App GUI state.

## Resolve the product and file

Use the real Codex CLI identity before editing:

```sh
command -v codex
codex --version
codex --help
```

The persistent CLI file is `$CODEX_HOME/config.toml`; when `CODEX_HOME` is
unset it is `~/.codex/config.toml`. Resolve symlinks for inspection, retain the
logical path in the receipt, and never replace a symlink with a regular file.
If the requested product is Cursor, VS Code, or Codex App, route to the
product-specific skill or supported App surface. Do not infer a product from a
setting name.

## Supported edit surfaces

Read and save the exact preimage before any edit. Prefer a product-supported
CLI command:

```sh
codex features list
codex features enable <known-feature>
codex features disable <known-feature>
```

Run those commands with the resolved `CODEX_HOME`; they reject unknown feature
flags and keep unrelated TOML sections. A `-c key=value` option is a
per-process override and must not be described as a persistent edit.

Some Codex subcommands tolerate unknown top-level TOML keys when loading the
file. A successful `codex features list` therefore does not prove that an
arbitrary key is supported. Compare a requested persistent key with the
current CLI's documented schema before invoking a writer;
otherwise reject it as unsupported and leave the file unchanged.

For another persistent key, first confirm that the current CLI accepts the key
and value from its own help/schema. Presence in an existing local file is not
evidence of support: the loader may silently ignore that key. If no
supported write surface preserves the TOML structure, stop with an actionable
unsupported-setting report. Do not invent a JSON adapter or rewrite the whole
file. Never edit `model`, auth, cached state, or provider credentials as a
side-effect of another request.

## Validation, conflict, and rollback

1. Read bytes, mode, symlink target, and a SHA-256 preimage.
2. Parse the TOML and validate the requested key/value before invoking the
   writer. Malformed input, an unknown key/feature, or an invalid value must
   leave the file unchanged.
3. Recheck the preimage immediately before the write. If it changed, report a
   conflict and preserve the external edit.
4. After the writer returns, read back the effective value and verify unrelated
   sections remain unchanged.
5. Keep the preimage and restore it only after confirming the current digest is
   the digest produced by this operation. If it has drifted, refuse rollback
   and report the conflict.

When a fixture is used, set `CODEX_HOME` to a disposable directory and invoke
the real `codex features` command. Never use a fixture path as the user's
default or create `~/.codex` as a replacement.

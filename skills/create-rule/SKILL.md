---
name: create-rule
description: >-
  Create persistent Codex guidance. Use when you want to add or update an
  AGENTS.md instruction file, project conventions, or command execution rules.
---
# Creating Codex Guidance

Use this skill when a user wants instructions that Codex should apply across turns or files. First determine the guidance scope and the concrete behavior it should teach. Write the smallest applicable `AGENTS.md` file, or update the nearest existing one while preserving unrelated guidance.

## Persistent guidance locations

Codex reads `AGENTS.md` guidance from the user or project instruction hierarchy. Use `~/.codex/AGENTS.md` for user-wide defaults, a repository-root `AGENTS.md` for shared project conventions, and a deeper `AGENTS.md` only for a narrower directory. `AGENTS.override.md` takes precedence at the same scope for an intentional temporary override; do not create one unless the user asked for an override. Keep instructions concise, actionable, and explicit about validation.

Do not create another provider's rule files, YAML `globs`/`alwaysApply` frontmatter, or `RULE.md`. Those files are not the Codex guidance mechanism.

## Separate guidance from execution policy

`AGENTS.md` teaches the agent how to reason and work. It does not create a hard command allowlist or sandbox boundary. If the user needs command enforcement, inspect the current Codex execpolicy/rules configuration and explain the resulting approval behavior separately. Do not describe execpolicy as an `AGENTS.md` feature, and do not weaken an existing restrictive rule while adding guidance.

## Workflow

Follow the current `COMMON-AGENTS.md` and user instructions when available. Do not restore retired intake, authority manifests, fixed roles, or approval gates.

1. Inspect the current directory and parent instruction files before editing.
2. Decide whether the request is repository-wide, directory-specific, or user-wide. Ask only when the scope cannot be inferred.
3. State the behavior in direct, testable language. Include required checks, source-of-truth paths, and stop conditions when they matter.
4. Add the narrowest `AGENTS.md` at the selected scope. Preserve existing content and avoid duplicating parent guidance.
5. Review the resulting instruction hierarchy from the target directory. Validate Markdown and run a disposable Codex load check when the change affects startup behavior.
6. Report the path changed and any command enforcement that remains a separate configuration task.

## Example

A project convention belongs in a repository file:

```text
AGENTS.md
```

```markdown
# Project guidance

Run `python -m pytest` after changing Python behavior. Keep generated files under `build/` and do not edit them by hand.
```

A file-specific convention belongs in the nearest directory's `AGENTS.md`; express the scope in prose and directory placement instead of a `globs` field.

## Safety and limits

Read-only questions must not cause a persistent config edit. Preserve user-owned configuration and never invent a path for a missing Vault or policy file. For config keys and command rules, consult the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference); for agent-readable guidance, follow the repository's `AGENTS.md` hierarchy.

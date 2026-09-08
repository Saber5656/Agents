---
name: create-skill
description: Explain Codex SKILL.md structure or route an explicitly requested new skill to skill-creator as the single implementation owner. Existing skill changes belong to skill-updater.
disable-model-invocation: true
---

# Codex skill structure and creation entrypoint

Follow the current `COMMON-AGENTS.md` and the user's request. This entrypoint
explains the file format and selects one implementation owner; it does not
start a second authoring, evaluation, or packaging workflow.

- For a new reusable skill, use `$SKILLS_ROOT/skill-creator/SKILL.md` as the
  single owner. Pass the existing request, chosen name/output directory and
  acceptance criteria. If that owner is already active for this intent,
  continue its existing artifact instead of creating another skill/package.
- For an existing skill change, use `$SKILLS_ROOT/skill-updater/SKILL.md`.
- For a question about skill structure, explain the format without writing
  files or invoking an authoring workflow.

A skill directory contains `SKILL.md` with YAML `name` and `description`,
followed by instructions. Keep executable examples in `scripts/`, detailed
material in `references/`, and test inputs/results separate from the delivered
package. Preserve the user's supplied wording and existing customization.

Resolve the configured `$SKILLS_ROOT` and actual consuming roots before any
write. Read the existing inventory and symlink targets; do not assume a Cursor
path is a Codex installation or create a replacement source root. User-managed
source changes go through review, meaningful evaluation and preserving
installation as described in `$AGENTS_ROOT/docs/skill-installation.md`.
A disposable fixture stays in its explicitly selected fixture directory.

Infer already supplied requirements from context. Ask only for missing
material choices, not for a second approval of routine implementation,
validation or already-authorized delivery. A request to explain a file format
is not authorization to create a persistent skill or change product settings.

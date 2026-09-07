# skills

This repository contains reusable utility skills. Organization routing and policy
ownership live outside the utility-skill layer.

Workflow skills that need organization decisions consume an explicit,
caller-supplied Saihai task context or a typed artifact such as a `Branch Plan`,
`Task Change Manifest`, or `Publication Manifest`. They validate and execute that
input; they do not select roles, approval owners, review providers, routing, or
publication ownership. If required context is missing, the skill stops and returns
a typed missing-context result to the caller.

## Repository layout

Each skill lives directly under the repository root:

```text
<skill-name>/
├── SKILL.md
├── agents/       # optional UI metadata
├── references/   # optional reusable guidance
├── scripts/      # optional deterministic helpers
├── evals/        # optional behavior scenarios
└── tests/        # optional automated checks
```

Bundled `.system/` skills, `.workspace/` evaluation output, caches, and local
runtime configuration are not part of the user-managed public skill source.

## Local setup

Use the clean public clone as the local skill root:

```bash
SKILLS_REPO_ROOT="${SKILLS_REPO_ROOT:-$HOME/dev/skills}"
mkdir -p "$HOME/.claude" "$HOME/.codex"

for SKILLS_LINK in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
  if [ -e "$SKILLS_LINK" ] && [ ! -L "$SKILLS_LINK" ]; then
    printf 'Refusing to replace existing directory: %s\n' "$SKILLS_LINK" >&2
    exit 1
  fi
  ln -sfn "$SKILLS_REPO_ROOT" "$SKILLS_LINK"
done
```

If the command reports an existing directory, back up or remove that directory
manually, then rerun the setup. The setup never deletes existing skill files.

## Public and private data boundary

Track reusable defaults, examples, templates, and tests. Keep machine-specific or
private values in ignored files such as `.env`, `*.local.*`, or `*.private.*`.
Never commit personal absolute paths, personal email addresses, Vault names, account names,
tokens, credentials, private keys, certificates, or real contact data.

Before publication, inspect the complete tracked diff and history for local paths,
personal identifiers, credential material, and secret-adjacent files. A clean scan
does not make an exposed credential safe; revoke or rotate any credential that may
have entered Git history.

## Development

Create or update one feature unit at a time. A feature unit may include a
coordinated set of related skills when their contracts must change together;
keep supporting tests and evals with the behavior they verify, and run focused
validation for each behavior plus one integrated validation for the feature unit
before committing.
Use task-specific branches/worktrees and publish changes through pull requests;
do not push directly to the default branch.

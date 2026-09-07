# GitHub OSS Repo Hardening

Public-first GitHub OSS repository hardening workflow.

## What It Does

- Guides repository setup after creation.
- Helps decide OSS-ready product and repository names before public hardening.
- Separates manual UI, read-only audit, dry-run, and approved mutation.
- Covers Rulesets, merge queue, Actions permissions, fork PRs, Dependabot, CodeQL, secret scanning, CODEOWNERS, and release safety.
- Treats token handling as high risk.
- Provides a dry-run/apply helper for standard default-branch Rulesets.
- Guides individual-account Advanced Security setup through GitHub UI instead of requiring strong `gh` mutation permissions.

## Quick Use

Ask for public OSS repository hardening or invoke the skill directly, then provide `OWNER/REPO` when needed.

Ruleset dry-run:

```bash
python3 scripts/apply-default-branch-ruleset.py --repo OWNER/REPO \
  --payload-out /PRIVATE/DIR/ruleset.json \
  --context-out /PRIVATE/DIR/context.private.json
```

Key Actions baseline:

- Restrict allowed actions to owner/local plus explicitly selected external actions.
- Require full-length SHA pinning where supported.
- Set default workflow token permissions to read repository contents and packages only.
- Require approval for all external contributor fork PR workflow runs.
- Keep GitHub Actions PR creation/approval disabled by default.
- When merge queue is enabled, make required workflows run on both `pull_request` and `merge_group`.

Merge queue required check trigger:

```yaml
on:
  pull_request:
    branches: [main]
  merge_group:
    branches: [main]
```

See [SKILL.md](SKILL.md) for the full workflow.

Advanced Security UI baseline:

- Enable Dependency graph, Dependabot alerts, Dependabot security updates, malware alerts, Secret Protection, and Push protection.
- Use a valid `.github/dependabot.yml`; do not keep the empty `package-ecosystem: ""` starter template.
- Start CodeQL with Default setup and Default query suite.
- Do not add CodeQL/code scanning to ruleset required gates until the first successful run is visible.

For apply, pass the reviewed `--payload-in` and `--context-in` in the same executor
with the existing confirmation flags. See the [credential context contract](references/gh-credential-context.md)
for private evidence, drift checks and presence-only limitations.

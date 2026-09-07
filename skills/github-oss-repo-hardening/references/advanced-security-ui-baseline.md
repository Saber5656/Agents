# Advanced Security UI Baseline

Use this reference when hardening a public-first OSS repository owned by an individual account.

Do not default to `gh api` mutation for Advanced Security settings. Many of these settings require strong repository administration permissions. For individual-account OSS setup, prefer GitHub UI guidance and record the final settings.

## UI Path

```text
Repository -> Settings -> Security and quality -> Advanced Security
```

GitHub UI labels change over time. Match by feature meaning, not exact text only.

## Recommended Settings

| Section | Setting | Baseline | Reason |
|---|---|---|---|
| Private vulnerability reporting | Private vulnerability reporting | Enable | Gives reporters a non-public path for sensitive issues. |
| Dependency graph | Dependency graph | Enable | Required foundation for dependency visibility and Dependabot features. |
| Dependency graph | Automatic dependency submission | Disable initially | Avoid extra moving parts until a build-time dependency ecosystem needs it. |
| Dependabot | Dependabot alerts | Enable | Alerts maintainers to vulnerable dependencies. |
| Dependabot | Dependabot malware alerts | Enable | Alerts when malware is detected in dependencies. |
| Dependabot | Dependabot security updates | Enable | Allows Dependabot to open PRs for vulnerable dependency patches. |
| Dependabot | Grouped security updates | Enable | Reduces alert-fix PR noise by grouping compatible security updates. |
| Dependabot | Dependabot version updates | Enable only with valid `.github/dependabot.yml` | The UI template with empty `package-ecosystem: ""` is invalid. |
| Code scanning | CodeQL analysis | Default setup first | Low-maintenance baseline. Use Advanced setup only when the repository needs custom build/query behavior. |
| Code scanning | CodeQL query suite | Default | Start with lower noise; strengthen after alert triage is understood. |
| Code scanning | Check runs failure threshold | Security: High or higher; Standard: Only errors | Sensible early OSS default that avoids noisy merge blocking. |
| Code scanning | Ruleset required gate | Not initially | Add only after CodeQL has run successfully and check names are stable. |
| Code scanning | Copilot Autofix | Optional | Helpful if available, but it does not replace review or threat modeling. |
| Secret Protection | Secret Protection | Enable | Enables GitHub native secret protection surface for the repository. |
| Secret Protection | Push protection | Enable | Blocks supported secrets before they enter repository history. |

## Dependabot Version Updates

Do not keep GitHub's empty starter template:

```yaml
version: 2
updates:
  - package-ecosystem: ""
    directory: "/"
    schedule:
      interval: "weekly"
```

For a repository that only has GitHub Actions workflows and no application package manifest yet, use:

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "09:00"
      timezone: "Asia/Tokyo"
    open-pull-requests-limit: 2
    commit-message:
      prefix: "chore"
      include: "scope"
```

Add package-manager blocks only after manifests exist.

Examples:

```yaml
  - package-ecosystem: "npm"
    directory: "/"
    schedule:
      interval: "weekly"
```

```yaml
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
```

## CodeQL Default Setup

Use CodeQL default setup first.

Baseline:

| Item | Value |
|---|---|
| Setup type | Default |
| Languages | GitHub-detected supported languages only |
| Query suite | Default |
| Runner | GitHub-hosted default |
| Private registry access | Off unless needed |
| Required ruleset gate | Defer until at least one successful run |

If the repository has no CodeQL-supported application code yet, enabling CodeQL may have little immediate effect. That is acceptable; revisit after the implementation language is present.

## What Not To Automate By Default

Do not advise strong `gh` mutation for personal-account OSS setup unless the user explicitly chooses that trade-off.

Avoid:

- Asking the user to store a broad PAT in `gh auth login`.
- Running Advanced Security mutations with a saved broad credential.
- Creating long-lived tokens for one-time repository setup.
- Treating UI settings as if they had a reusable ruleset-style import/export file.

If automation becomes necessary, keep it as a separate explicit decision:

1. Prefer manual UI for one-off setup.
2. If repeated setup becomes painful, use a short-lived fine-grained PAT selected to one repository.
3. For many repositories, consider a GitHub App or organization-level code security configurations instead of a personal PAT.

## Verification Prompt

After the user finishes the UI setup, ask for the visible state and compare it with this table.

Minimum acceptable public-first baseline:

| Feature | Expected |
|---|---|
| Dependency graph | Enabled |
| Dependabot alerts | Enabled |
| Dependabot security updates | Enabled |
| Dependabot malware alerts | Enabled if available |
| Grouped security updates | Enabled or explicitly deferred |
| Dependabot version updates | Valid `.github/dependabot.yml` exists or intentionally deferred |
| Secret Protection | Enabled |
| Push protection | Enabled |
| CodeQL | Default setup enabled or intentionally deferred until code exists |
| Private vulnerability reporting | Enabled or intentionally deferred |

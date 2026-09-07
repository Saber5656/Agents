# Public-first GitHub OSS Repository Hardening Checklist

Use this checklist after a repository is created, or before a repository is made visible to contributors.

## Status Values

| Status | Meaning |
|---|---|
| `done` | Verified in the repository |
| `pending` | Not done yet |
| `blocked` | Cannot proceed without user decision, permission, or upstream GitHub support |
| `manual` | Must be done or confirmed by the user in the GitHub UI |
| `n/a` | Not applicable to this repository |

## Phase 0: Product And Repository Identity

| Task | Desired State | Verification |
|---|---|---|
| Confirm product name | OSS-ready name is chosen before broad public setup | README draft / naming decision note |
| Confirm repository name | Repo name matches product name or intentionally differs | GitHub repo settings / `git remote -v` |
| Check naming shape | Prefer lowercase one-word, package-friendly, pronounceable name | name review |
| Avoid weak naming patterns | Avoid crowded `ai-*`, `gpt-*`, `agent-*`, `*-tube`, and major-platform-adjacent names unless intentionally justified | name review |
| Check basic collisions | GitHub, npm/PyPI when relevant, web search, and domain if needed are checked before adoption | search/audit notes |
| Record naming caveat | No trademark/legal safety claim is made without proper review | decision note |
| Confirm target repository | Exactly one `OWNER/REPO` is selected | `git remote -v` or `gh repo view OWNER/REPO` |
| Confirm owner type | User or organization ownership is known | `gh repo view --json owner` |
| Confirm visibility | Public-first if OSS development can be public | `gh repo view --json visibility,isPrivate` |
| Confirm default branch | Default branch is `main` unless project deliberately uses another name | `gh repo view --json defaultBranchRef` |
| Confirm release posture | README says pre-alpha / experimental / no release yet | README content |
| Confirm branch policy | Short-lived branches open PRs to protected `main`; no permanent `developer` branch by default | README / CONTRIBUTING |

## Phase 1: Baseline Files

| File | Required Content |
|---|---|
| `README.md` | Project status, no production use, no release/package guarantee, basic contribution path |
| `LICENSE` | Chosen OSS license |
| `SECURITY.md` | Vulnerability reporting path, supported versions, response policy, pre-alpha caveat |
| `CONTRIBUTING.md` | Fork/branch/PR workflow, no permanent `developer` branch by default, no secrets in PRs, dependency and workflow change rules |
| `.github/CODEOWNERS` | Owner rules for `.github/CODEOWNERS`, `.github/workflows/**`, package manifests, lock files, release files |
| `.github/pull_request_template.md` | Purpose, tests, dependency changes, workflow changes, secret impact |
| `.github/ISSUE_TEMPLATE/*` | Bug and feature templates; security reports should point to `SECURITY.md` |

CODEOWNERS must protect itself. At minimum:

```text
.github/CODEOWNERS @OWNER
.github/workflows/** @OWNER
package.json @OWNER
package-lock.json @OWNER
pnpm-lock.yaml @OWNER
yarn.lock @OWNER
requirements*.txt @OWNER
pyproject.toml @OWNER
poetry.lock @OWNER
Cargo.toml @OWNER
Cargo.lock @OWNER
go.mod @OWNER
go.sum @OWNER
Dockerfile @OWNER
.github/dependabot.yml @OWNER
```

Replace `@OWNER` with a GitHub user or team that has write access.

## Phase 2: Local Secret Hygiene

| Task | Desired State | Verification |
|---|---|---|
| Ignore local secrets | `.env`, private keys, local config are ignored | `.gitignore` |
| Search current tree | No likely secret in working tree | local secret scan or targeted `rg` |
| Search history | No likely secret in commit history | history secret scan |
| Handle findings | Rotate/revoke leaked credentials, then clean history if needed | provider console and git history |

Never treat deletion from git as enough. If a token was committed, revoke or rotate it.

## Phase 3: Actions Hardening

| Task | Desired State | Verification |
|---|---|---|
| Actions permissions | `Allow OWNER, and select non-OWNER, actions and reusable workflows` | GitHub UI or `gh api repos/OWNER/REPO/actions/permissions` |
| GitHub-owned actions | Allowed | GitHub Actions settings |
| Marketplace verified creators | Not allowed initially | GitHub Actions settings |
| Specified external actions | Empty until a reviewed action is needed | GitHub Actions settings |
| Full-length SHA pinning | Required if supported by the selected policy | GitHub Actions settings |
| Artifact and log retention | `30 days` initially | GitHub Actions settings |
| Cache retention | `7 days` initially | GitHub Actions settings |
| Cache size limit | `10 GB` initially | GitHub Actions settings |
| Fork PR approval | All external contributors require maintainer approval | GitHub Actions settings |
| Default token permission | Read repository contents and packages permissions | GitHub UI or `gh api repos/OWNER/REPO/actions/permissions/workflow` |
| Workflow permissions | Every workflow has explicit `permissions:` | inspect `.github/workflows/*.yml` |
| Merge queue workflow trigger | Required checks include `merge_group` when merge queue is enabled | inspect `.github/workflows/*.yml` |
| PR creation by Actions | Disabled unless explicitly needed | GitHub Actions settings |
| `pull_request_target` | Not used by default | `rg "pull_request_target" .github/workflows` |
| Third-party actions | Full-length SHA pinning or documented exception | workflow review |
| Self-hosted runner | Not used for public fork PRs | workflow `runs-on` review |

Prefer GitHub-hosted runners for public-first OSS. Treat self-hosted runners as a separate threat model.

## Phase 4: Security Features

| Task | Desired State | Verification |
|---|---|---|
| Advanced Security mutation policy | Personal-account setup uses GitHub UI first; no strong `gh` mutation by default | user decision / setup note |
| Dependency graph | Enabled | GitHub Advanced Security UI |
| Automatic dependency submission | Disabled initially unless build-time dependency submission is needed | GitHub Advanced Security UI |
| Dependabot alerts | Enabled | GitHub Advanced Security UI |
| Dependabot malware alerts | Enabled if available | GitHub Advanced Security UI |
| Dependabot security updates | Enabled where available | GitHub Advanced Security UI |
| Grouped security updates | Enabled, or intentionally deferred | GitHub Advanced Security UI |
| Dependabot version updates | Valid `.github/dependabot.yml` exists; no empty `package-ecosystem: ""` | file review |
| Initial Dependabot ecosystem | `github-actions` only until real package manifests exist | `.github/dependabot.yml` |
| CodeQL default setup | Enabled if language supported or implementation language will be added soon | Code scanning settings |
| CodeQL query suite | `Default` initially | Code scanning settings |
| CodeQL required gate | Not required until at least one successful run and stable check names | ruleset review |
| Secret Protection | Enabled/available for public repository | GitHub Advanced Security UI |
| Push protection | Enabled/available for user/repository | GitHub Advanced Security UI |
| Private vulnerability reporting | Enabled when useful for OSS | GitHub Advanced Security UI |
| Copilot Autofix | Optional; not treated as a review replacement | Code scanning settings |

Some settings vary by plan, owner type, language, and repository state. Mark uncertain items as `blocked` or `manual`, not `done`.

## Phase 5: Main Ruleset

Initial solo-maintainer target:

| Rule | Initial Recommendation |
|---|---|
| Target | `~DEFAULT_BRANCH` |
| Enforcement | Start with payload review; activate after baseline checks |
| Bypass actors | Empty |
| Require pull request | Yes |
| Required approving reviews | `0` for solo maintainers |
| Code owner review | Off initially unless another maintainer can approve |
| Review thread resolution | On |
| Dismiss stale reviews | On |
| Required status checks | Add after CI check names are stable |
| Merge queue | Optional after required checks are stable; recommended when multiple PRs may merge close together |
| Required signatures | Add after local commit signing is verified |
| Linear history | On if using squash/rebase merge |
| Non-fast-forward | On |
| Deletion protection | On |

Do not block the only maintainer by requiring approvals or code owner review that nobody else can provide.

Merge queue setup checklist:

| Task | Desired State | Verification |
|---|---|---|
| Stable required checks | Required checks have passed at least once before queue enforcement | PR checks / branch protection |
| GitHub Actions trigger | Required workflows run on both `pull_request` and `merge_group` | workflow YAML |
| Queue target | Queue targets a concrete default branch or repository-level ruleset, not a wildcard branch protection pattern | GitHub branch protection / ruleset UI |
| Build concurrency | Start small for solo / small OSS, usually `1` | merge queue settings |
| Queue dry run | A PR can be added to the queue and is tested as `main + queued PRs` before merge | PR timeline / checks |

For GitHub Actions, required workflows should include:

```yaml
on:
  pull_request:
    branches: [main]
  merge_group:
    branches: [main]
```

For third-party CI, the CI must report required statuses for merge queue temporary branches
or merge group webhooks, including branches with the `gh-readonly-queue/{base_branch}` prefix.

## Phase 6: Release / Package Safety

| Task | Desired State | Verification |
|---|---|---|
| Release posture | No formal release until explicit `v0.1.0` gate | README and GitHub releases |
| CD trigger | Production deploy/release is gated by tag, GitHub Release, or environment approval, not every `main` merge | workflow review |
| Package publish | No publish token stored | `gh secret list --repo OWNER/REPO` and package settings |
| Tags | No release tags unless intentional | `git tag` and GitHub releases |
| Artifact policy | CI artifacts do not include secrets or private notes | workflow review |
| Attestation/SBOM | Planned before package/release automation | release design doc |

## Phase 7: Final Verification

| Check | Expected Result |
|---|---|
| Direct push to `main` | Rejected |
| Branch -> PR -> checks -> merge | Works |
| Merge queue, if enabled | `Add to merge queue` runs required `merge_group` checks and merges only after they pass |
| Fork PR from external user | Requires approval before risky workflows |
| Workflow change PR | Flagged for owner review |
| Dependency manifest change | Flagged for owner review |
| Secret list | Empty unless explicitly documented |
| Repository audit record | Saved without token values |

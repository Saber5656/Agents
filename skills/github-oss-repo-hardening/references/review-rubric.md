# Review Rubric

Use this rubric when reviewing a repository hardening plan, a generated checklist, or gh automation proposal.

## Repository Scope

| Check | Pass Criteria |
|---|---|
| Product name readiness | OSS-ready product/repository name is decided before heavy public setup, or the risk of delaying naming is explicit |
| Naming shape | The plan prefers compact, pronounceable, package-friendly one-word names when the user wants a brandable OSS project |
| Naming collision check | GitHub and relevant package registry exact-name checks are planned before final adoption |
| Naming confusion risk | The plan avoids overly generic AI terms, `tube`-style platform mimicry, and names too close to major platforms unless justified |
| Target repo clarity | Exactly one `OWNER/REPO` is selected |
| Visibility alignment | The plan matches public-first OSS, or explicitly handles private mismatch |
| Owner model | User/org ownership implications are noted |
| Release posture | Public repo is not confused with release/package publication |
| Branch posture | Solo/small OSS uses short-lived branches to protected `main`; a permanent `developer` branch is not introduced without a concrete staging/integration reason |

## Security Posture

| Check | Pass Criteria |
|---|---|
| Secrets | No token values printed, stored, or requested unnecessarily |
| Actions token | `GITHUB_TOKEN` is read-only by default and least privilege per workflow |
| Fork PRs | External workflow execution is reviewed; no automatic approval |
| Dangerous events | `pull_request_target` is banned or threat-reviewed |
| Runners | Self-hosted runners are not used for public fork PRs |
| Third-party actions | SHA pinning or documented exception |
| CODEOWNERS | `.github/CODEOWNERS`, workflows, dependency files, release files are owned |
| Ruleset | Direct push, force push, and deletion are blocked without trapping solo maintainer |

## Automation Safety

| Check | Pass Criteria |
|---|---|
| Read vs write | Read-only commands are separated from mutations |
| Approval | Every mutation has explicit approval |
| Dry-run | Endpoint, command, permission, change, rollback are shown |
| Auth source | Broad stored auth is not silently used |
| Token audit | The plan does not claim `gh auth status` proves fine-grained permissions |
| Allowlist | Raw `gh api` is limited to known endpoints |

## Sequencing

| Check | Pass Criteria |
|---|---|
| Baseline first | README/LICENSE/SECURITY/CONTRIBUTING/CODEOWNERS before heavy rules |
| CI before required checks | Required checks are added only after check names are stable |
| Signing before required signatures | Required signatures only after verified signing works |
| Release controls | Publish tokens are absent until release design exists |
| CD controls | Deployment/release is gated by explicit tag, GitHub Release, or environment approval rather than raw `main` merge |
| Verification | Direct push rejection and PR path are tested |

## Common Failure Modes

- Required approving review count blocks solo maintainer.
- CODEOWNER review is enabled before there is another valid reviewer.
- Required checks reference a workflow name that has not run yet.
- `gh api` mutation uses broad saved credential without the user noticing.
- Secret scanning is marked done without checking actual plan/visibility support.
- The skill outputs a long essay but no next action.

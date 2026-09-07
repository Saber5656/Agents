# GitHub Actions Hardening Baseline

Use this baseline before adding workflows to a public-first OSS repository.

The goal is to make future workflows opt into trust and write access explicitly.

## Repository Settings

Path:

```text
Repository -> Settings -> Actions -> General
```

## Actions Permissions

| UI item | Baseline value | Reason |
|---|---|---|
| Actions permissions | `Allow OWNER, and select non-OWNER, actions and reusable workflows` | Avoid unrestricted third-party action use while still allowing repository-owned actions and selected external actions. |
| Allow actions created by GitHub | On | Needed for common GitHub-owned actions such as checkout/setup actions. |
| Allow Marketplace actions by verified creators | Off | Verified creator is useful but still too broad for initial supply-chain posture. |
| Allow specified actions and reusable workflows | Empty initially | Add exact allowlist entries only when a workflow requires them. |
| Require actions to be pinned to a full-length commit SHA | On, when the UI supports it with the selected policy | GitHub documents full-length SHA pinning as the strongest immutability control for third-party actions. |

## Retention

| UI item | Baseline value | Reason |
|---|---|---|
| Artifact and log retention | `30 days` | Keeps enough debugging history while reducing retained output exposure. |
| Cache retention | `7 days` | Good early-project default; increase only if CI cache misses become a real bottleneck. |
| Cache size limit | `10 GB` | Keep default unless cache pressure appears. |

## Fork Pull Request Workflows

| UI item | Baseline value | Reason |
|---|---|---|
| Approval for running fork pull request workflows from contributors | `Require approval for all external contributors` | Strongest posture for public pre-alpha repositories; every outside contributor workflow run gets maintainer review first. |

## Workflow Permissions

| UI item | Baseline value | Reason |
|---|---|---|
| Workflow permissions | `Read repository contents and packages permissions` | Repository-level least privilege default for `GITHUB_TOKEN`. |
| Allow GitHub Actions to create and approve pull requests | Off | Avoid workflow approval loops and unexpected write paths. |

## Workflow Authoring Rules

When workflows are added later:

- Add top-level or job-level `permissions:` explicitly.
- Keep default permissions read-only; add write scopes only for the job that needs them.
- If a workflow reports a required check for a branch protected by merge queue,
  trigger it on both `pull_request` and `merge_group`.
- Do not use `pull_request_target` without a dedicated threat review.
- Do not use self-hosted runners for public fork pull requests.
- Pin third-party actions to a full-length commit SHA.
- Treat every workflow file change as security-sensitive.

Merge queue required check trigger:

```yaml
on:
  pull_request:
    branches: [main]
  merge_group:
    branches: [main]
```

`merge_group` is separate from `pull_request` and `push`. Without it, GitHub
Actions required checks may pass on a PR but never report on the queue's
temporary merge group, blocking the queue.

## When To Relax

| Relaxation | Only after |
|---|---|
| Allow Marketplace actions by verified creators | The project has a workflow review process and accepts broader third-party dependency risk. |
| Add specified third-party actions | The action is reviewed, pinned to full SHA, and has a clear owner/use case. |
| Enable merge queue as a required path | Required checks are stable and the workflows report on `merge_group`. |
| Reduce fork PR approval strictness | CI is mature, no secrets/write tokens are exposed to fork PRs, and maintainer review overhead is too high. |
| Increase artifact/log retention | Release/debugging needs justify longer retention. |
| Enable PR creation by Actions | A specific bot workflow needs it and uses least-privilege permissions with branch/ruleset safeguards. |

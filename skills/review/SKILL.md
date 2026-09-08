---
name: review
description: >-
  Run one explicit, read-only code review through the local review harness and
  return evidence-backed findings to the main agent.
disable-model-invocation: true
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---
# Review

Use this entrypoint when the user explicitly requests a review. The local
`python -m harness.runner review` command is the single review owner. Do not
ask which bot to start, launch a subagent, edit the workspace, run mutation
commands, or post a review result.

The caller chooses exactly one provider, model, effort, workspace, prompt, and
role. A normal review uses `tech-reviewer`; a security-scoped review uses
`tech-security`. Provider fallback is a caller choice and each attempt remains
separately identified. Do not silently replace the requested model or scope.

The worker may read the selected workspace through the provider's read-only
surface. Its terminal output must be one JSON object:

```json
{
  "verdict": "approve|request_changes|incomplete",
  "findings": [
    {
      "severity": "high|medium|low",
      "file": "path:line",
      "issue": "impact and rationale",
      "evidence": ["observed diff or command result"]
    }
  ],
  "limitations": []
}
```

An approval requires an empty findings list. A finding requires severity,
location, issue, and non-empty evidence. Empty, malformed, question-only, or
provider-success text is `incomplete`. The main agent decides whether a
finding is adopted, rejected, or separated and performs any repair.

The source copies under `.agents/skills` or another installed surface may be
older or locally customized. Reconcile and preserve those copies before
installation; source changes alone do not claim installed behavior.

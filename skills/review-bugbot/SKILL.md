---
name: review-bugbot
description: >-
  Run a bug-focused read-only review through the local harness without a
  separate bot or agent.
disable-model-invocation: true
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---
# Bug-focused review

This is a scope preset for an explicitly requested bug review. It uses the
same single local review owner as `/review`, with role `tech-reviewer`; it does
not invoke a historical Bugbot subagent or an external review service.

Run one provider/model/effort selected by the caller through
`python -m harness.runner review`. The review process is read-only: no edit,
write, shell mutation, network publication, or extra agent is allowed. Return
the current review JSON schema with `verdict`, `findings`, and `limitations`.
Each finding must include `severity`, `file`, `issue`, and non-empty `evidence`.
The main agent owns disposition and repair.

If the provider returns a question, malformed JSON, missing evidence, or an
unverified success string, report `incomplete` and retain the raw bounded
record. Do not treat an installed-only legacy skill or bot name as proof that
the provider exists.

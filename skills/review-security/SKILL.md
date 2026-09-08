---
name: review-security
description: >-
  Run a security-scoped read-only review through the local harness with the
  explicit tech-security role and evidence-backed results.
disable-model-invocation: true
user-invocable: true
category: Security
status: active
updated: 2026-09-08
---
# Security review

This is a scope preset for an explicitly requested security review. Use the
single local `python -m harness.runner review` owner with role `tech-security`.
The caller selects exactly one provider, model, effort, workspace, and prompt;
do not start a security subagent, historical Saihai route, or external bot.

The provider receives a read-only surface. Do not edit files, execute mutation
commands, change credentials/configuration, publish findings, or start extra
agents. Return the shared JSON result with `verdict`, `findings`, and
`limitations`; every finding has `severity`, `file`, `issue`, and non-empty
`evidence`. Missing, malformed, question-only, or provider-success-only output
is `incomplete`, never approval.

The main agent owns finding disposition and any repair. Installed copies are
consuming surfaces with their own provenance; a canonical source edit remains
deployment-pending until preserving installation and effective read-back are
performed.

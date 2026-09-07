# Current workflow for user-managed skills

`COMMON-AGENTS.md` and the current user request define the operating policy.
Legacy Saihai intake, authority/manifests, fixed roles and approval gates are
retired. Unchanged historical references may explain earlier designs but must
not be loaded as active prerequisites or used to reactivate old infrastructure.

Use existing authenticated App/CLI capabilities. Load the canonical local `.env`
when necessary; roots are `AGENTS_ROOT`, `SKILLS_ROOT`, `AGENTS_VAULT_ROOT`.
Never guess a replacement Vault. Keep full available requirements, corrections,
context, decisions and raw evidence privately in Vault, with links from durable
local tasks. Redact secrets and state missing/truncated history explicitly.

For ordinary development, the endpoint is validation, pre-commit review,
accepted finding remediation, minimal commits, branch push/PR, verified merge,
main synchronization and matching chat organization. A task-specific explicit
restriction can narrow it. Do not repeat routine permission questions. A
read-only inspection alone is not publication authorization.

Code behavior changes use TDD. Documentation-only changes explain why code TDD
is inapplicable and use relevant consistency/behavior checks. Every commit is
reviewed; self-review is allowed unless independent review was requested.
Review findings are judged by the main agent. Accepted in-scope defects are
fixed and verified automatically; unrelated discoveries are captured locally
for a separately invoked issueization agent, never silently implemented.

Use one active writer per worktree/ref/shared resource. Pin immutable bases and
reuse matching existing work. Preserve unrelated staged/dirty files and commits.
The local task system keeps execution state separate from issueization status,
with many-to-many work-unit/Issue/PR links and individual acceptance evidence.
A pending unrelated follow-up does not block its originating task.

The current background service must persist before transitions, reconcile old
processes and uncertain remote effects before retry, and recover automatically
when runnable. Worker timeout, quota wait and backoff are not whole-task
abandonment. UI operations remain explicitly pending when the supported App is
unavailable. Never claim App-independent execution from a foreground run alone.

Halt an operation that incurs separate charges beyond subscribed-agent tokens
or concretely violates security. Record the exact action/evidence without secret
values, preserve work and continue independent eligible tasks. Do not turn an
ordinary failing test, repair choice or timeout into a generic approval gate.
Do not switch to paid API inference, provision paid services, disclose secrets,
expand access outside the assignment or bypass repository protection.

GitHub titles/bodies are English and checked for private paths/secrets before
create, edit or comment. Use `Refs` until the actual acceptance is met; distinguish
mock success, tested, installed, merged and real usability. No direct main push,
force push, history rewrite, hook bypass or automatic destruction of other work.

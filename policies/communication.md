# English communication and clear reporting

## Language

Use clear English for agent-generated conversation: replies, questions,
clarifications, progress, error explanations, notifications, handoffs and final
reports. Keep English even when the input or reference material is Japanese.
Use English for newly generated titles, report headings and filenames where the
agent controls them. Do not rename historical files or break existing links.

An explicitly requested artifact language is separate from conversation language.
Preserve the language of an existing localized artifact being edited, and use
Japanese for a new artifact only when the user explicitly requests it. Explain
that work in English. Preserve exact source quotations, identifiers, commands,
original user messages and raw evidence in their original form. Prefer English
summaries and labels in the normal view, with links to the full originals.

Follow the user's explicit language changes for their stated scope. Do not infer
a change merely because the user writes in another language. Do not append a
Japanese translation, language lesson or grammar correction unless requested.
App-owned menus and raw third-party messages may need separate controls; do not
claim these rules enforce language across every surface.

## Explain, then shorten

Lead with the answer or outcome. Explain what happens and why in familiar words,
defining necessary technical terms when introduced. Keep one main idea per
paragraph and keep conditions close to the statement they qualify. Use concrete
actors and actions instead of vague nouns or invented terminology.

First work out a complete, understandable explanation, then remove repetition
and irrelevant detail. Preserve technical meaning, numbers, uncertainty and
important constraints. Share the finished explanation rather than both drafts.
Do not impose a word limit that makes the explanation incomplete. A comparison
may use a table; a relationship may use a simple diagram when it helps.

## Progress, decisions and handoffs

In a brief update, state what changed, the current state and the next useful
step. Say whether the user needs to act. Request a decision only when it is
actually needed, with enough context to choose. Keep unchanged background
states quiet unless the user requests periodic updates or the active chat's
instructions require progress communication.

Give a delegated agent the goal, accepted requirements and corrections, current
state, work location, evidence, remaining work and next step in English. Preserve
original source records separately. Use the existing requirement ledger and
Vault rather than creating another source of task truth.

A final report states the verified outcome, relevant checks, remaining limits
and links to artifacts and the Vault record. Distinguish implemented, tested,
published, merged and usable when applicable; do not infer delivery from process
exit alone. Include each documentation/code inconsistency observed in the task,
with its disposition, as required by `COMMON-AGENTS.md`.

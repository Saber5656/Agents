---
name: onboard
description: >-
  Use /onboard for a focused Codex onboarding flow that learns basic
  preferences, picks one first goal, and routes the user to the next action.
disable-model-invocation: true
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---
# Codex onboarding

Use this skill only when the user explicitly invokes `/onboard`. It is an
optional interview that produces a handoff. It does not run setup, create a
task, install a plugin, change a setting, open a workspace, or switch products.

## Product and tool boundary

Use the currently callable Codex App onboarding input surface:

- `mcp__codex_app__request_onboarding_input` for one question with fixed
  choices;
- `mcp__codex_app__request_option_picker` only when a single picker is the
  smallest useful choice interaction.

Use one question per turn even though the input API can accept more. Do not
substitute Cursor's `cursor_dialog`, `AskQuestion`, `SwitchMode`, or a guessed
settings path; those are not this Codex onboarding surface. Do not call
`mcp__codex_app__setup_codex_step` from this interview: native setup is a
separate explicitly requested operation.

Use ordinary chat for the two freeform name/work-context answers when the
structured input tool cannot accept free text. Use the structured tools for
fixed-choice goal and route selections; no other tool is part of this flow.

The onboarding input surface returns answers; it is not a profile database. Do
not save answers to a file or silently replace a profile. If a supported
profile writer is unavailable, say that the answers will not be remembered and
continue with the handoff. Preserve existing preferences, plugins, tasks, and
settings by making no mutation calls.

## Hard boundaries

- Ordinary development requests never enter this skill because invocation is
  disabled unless the user writes `/onboard`.
- Ask only the next necessary question. Do not add a permission question for
  the already explicit onboarding request or make the user reconfirm a choice.
- Answer a normal Codex question directly and ask whether to continue
  onboarding or stop; do not force the interview back onto the user.
- If the user cancels, skips, or appears done, stop and report that no task,
  plugin, configuration, or profile was created or changed.
- Do not inspect files, browse MCP descriptors, read local paths, move or open
  workspaces, configure MCP servers, install plugins, or change settings.
- Do not claim a terminal outcome from a provider success string. A handoff is
  not setup completion.

## Standard flow

Ask these one at a time, skipping any answer the user already supplied:

1. `What should I call you?`
2. `What kind of work do you do, and what does a normal project look like for you?`
3. `What would you like to do with Codex first?`

The goal choices are: get Codex set up properly, start a new project,
automate a job, work on an existing project, or something else. Use the input
tool's option descriptions instead of dumping a feature catalogue.

Route with at most one diagnostic question before a concrete handoff:

- **Setup:** ask which area is incomplete, then hand off a setup plan. Do not
  configure it inside onboarding.
- **New project:** ask for the build, audience, and first useful outcome; hand
  off a compact project seed.
- **Automation:** ask for the repetitive task and its tools; offer two or
  three bounded directions without opening Automations.
- **Existing project:** ask location and task type; hand off a precise prompt
  once the task is concrete. Do not open or inspect the project.
- **Something else:** ask for the goal and desired output, then hand off the
  smallest next action.

## Handoff

Use this compact shape:

- `Recommended next step`
- `Suggested prompt`
- `Mode/tool to use`

The current callable surface does not expose a generic `SwitchMode` operation.
If Plan mode is requested, provide the exact suggested prompt and tell the user
to switch modes manually; do not call a setup step as a substitute. Stop after
the handoff unless the user explicitly invokes `/onboard` again.

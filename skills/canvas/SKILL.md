---
name: canvas
description: >-
  Create or update an interactive Canvas artifact for the Cursor IDE when the
  user explicitly requests that supported surface. Keep Cursor Canvas,
  Codex CLI/App artifacts, and Obsidian JSON Canvas as separate products.
user-invocable: true
category: Dev
status: active
updated: 2026-09-08
---
# Cursor Canvas

This skill describes the installed Cursor IDE Canvas surface. It is not a
general "Codex Canvas" capability. The verified local SDK is
`cursor/canvas`, with declarations under `~/.cursor/skills-cursor/canvas/sdk/`.
The installed skill documents its file surface as a single `.canvas.tsx` file in Cursor's managed
workspace directory:

```text
~/.cursor/projects/<workspace>/canvases/<name>.canvas.tsx
```

Resolve `<workspace>` from the actual Cursor workspace before writing. If the
directory cannot be identified, preserve the artifact elsewhere and report
that the Cursor renderer is unavailable; do not invent a managed path or create
a replacement Cursor project.

## Product boundary

- A request for a Cursor IDE Canvas or `.canvas.tsx` may use this skill.
- A request for an Obsidian `.canvas` file routes to `json-canvas`, which writes
  JSON Canvas `nodes` and `edges` rather than React/TSX.
- Codex CLI currently exposes `codex app [PATH]` and configuration flags, but
  its local CLI surface does not expose a verified Canvas renderer or
  `cursor/canvas` runtime. Codex App presence is not proof of Canvas support.
  Do not claim Codex Canvas support, invent a `~/.codex` Canvas path, or use
  the Codex App/browser as an unverified substitute for Cursor's renderer.
- If the named Cursor surface is unavailable or locked, keep the artifact and
  report the exact unverified checks. Do not bypass the GUI constraint with
  another application.

## Decide whether to create one

Use Canvas when the user wants a standalone interactive visual artifact such
as an exploration, table, chart, repeatable tool, or analytical view. Skip it
for a specific external-tool deliverable, a targeted code fix, a one-off
answer, or an existing artifact edit that belongs in that artifact's own tool.

## Authoring rules

1. Write exactly one `.canvas.tsx` file directly in the resolved managed
   `canvases` directory. Do not create helper files, style files, or support
   modules.
2. Import only from `cursor/canvas`; use the declarations in its local SDK for
   component and hook names and prop shapes. Default-export the top-level
   component.
3. Embed non-secret fixture data inline. Do not use `fetch`, network calls, or
   hidden external state.
4. Use `useHostTheme()` tokens and built-in components. Avoid gradients,
   hardcoded colors, box shadows, emoji decoration, and repetitive card walls.
5. Do not render placeholder or empty canvases. Omit sections that have no
   data; if the entire requested artifact lacks real input, report what is
   missing instead of fabricating content.
6. Label charts and tables with the metric, units, series, source, and time
   range needed to understand them without the chat.

## Verification

Before presenting a Cursor Canvas, perform the static self-check for one file,
allowed imports, inline data, no network, and the visual hierarchy rules. Then
open it through the supported Cursor Canvas surface and record the host's
TypeScript diagnostic. Exercise each requested control and read back the
visible result, including error/loading boundaries when they are part of the
artifact. A source file or metadata presence is not evidence that it rendered.

If the Cursor host cannot be opened, static checks are only partial validation.
Record the artifact path used for the fixture, the SDK/source digest, the
renderer availability result, and every unverified interaction. Do not claim
the artifact is usable until the consuming surface has displayed it.

When a Canvas is actually available to the user, link the exact absolute
`.canvas.tsx` path in the response. Do not provide a guessed link.

# Work signals design

## Goal and selected direction

A local, read-only view helps the user see current work and decisions without
rereading conversations. Existing task records and Agents Vault remain the
sources of truth. No second task database, remote exposure or job controls.

Product Design 0.1.56 user-context preflight, get-context and ideate were used.
Three visual directions were shown: a light overview, a light master-detail
view and a dark grouped overview. The user selected the third displayed image,
Work Signals, and requested only the minimum necessary data initially.
A revised dark single-column mock was generated and shown before implementation.
It is the visual target. The source image and detailed selection record are in
`$AGENTS_VAULT_ROOT/03-Contexts/2026-10-30-english-agent-workflow/dashboard/`.

## Initial screen

- Work signals title and one short purpose sentence.
- Refresh control and one observation-age indicator.
- Pending user decisions, only when explicitly recorded. Otherwise a quiet
  “No decisions waiting” line; unavailable data must not imply none exist.
- Recently updated active work: title, recorded state, next action, record age
  and a disclosure for details. Show at most three rows with a reveal-more action.
- Recent results and all records/sources start collapsed.

An old running record is not proof that an agent is active. Separate the time
the source was checked from the time the source record changed. Use a 24-hour
record-age threshold for the default current-work view. Keep older and unknown
records visible under all records; never silently delete them or alter their
stored state. Unknown dates, future dates and failed reads remain explicit.

## Visual specification

Use a graphite page, off-white text, muted blue-gray secondary text, mint for
recorded progress and amber for warnings. The desktop content width is about
1200 CSS pixels, centered with generous outer margins. Use a system sans-serif
font, a 36–46 pixel title and 16 pixel body text. Separate rows with quiet rules;
avoid nested cards, charts, decorative imagery and fake progress percentages.
The source contains only text, separators and ordinary outline icons. Use a
licensed icon library, not custom drawings. No raster assets are needed at runtime.

At narrow widths stack task metadata and keep every action reachable without
horizontal scrolling. Use semantic buttons/disclosures, visible focus, text
status labels, sufficient contrast and reduced-motion support. Evidence opens
as escaped plain text or a validated external HTTPS link. Original source text
is available in explicit details; normal labels and summaries remain English.

## Interaction and failures

Refresh on opening, manually, periodically while visible and on return after
sleep. Serialize reads and keep the last successful view when a refresh fails,
with a clear warning and its original observation time. Show loading and empty
states honestly. A failed check does not prove that no tasks or decisions exist.
Do not treat process exit or a recent fetch as task completion or worker health.

The first release runs on this Mac only. Phone access requires a separately
designed authenticated delivery route and is outside this implementation.

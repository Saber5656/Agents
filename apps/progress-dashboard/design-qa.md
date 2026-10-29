# Work signals design QA

final result: passed

## Evidence and comparison conditions

- Source visual truth: `$AGENTS_VAULT_ROOT/03-Contexts/2026-10-30-english-agent-workflow/dashboard/design-target.png` (selected option 3, revised to minimal data).
- Implementation: local Python server at `http://127.0.0.1:8766/`, built React client. Screenshots in the same private Vault directory: `desktop-before.png`, `desktop.png`, `mobile.png`.
- Desktop source and implementation are both 1487 × 1058 pixels; CSS viewport 1487 × 1058, device scale factor 1. No density scaling or browser chrome. Both have one active task and collapsed optional sections.
- Dynamic task age and next action differ deliberately: the source is a concept; the implementation uses real recorded data. No sample progress has been substituted.
- Source and actual implementation were opened together in the same comparison input, first before and then after fixes. A final paired comparison used the post-review build. This is a direct visual comparison, not a code-only inference.
- Mobile web viewport: 390 × 844 CSS/pixels, scale 1, touch emulation. This is browser emulation, not a physical-device or native-app test.
- Focused crops were unnecessary: the single-column screen contains only large text and simple controls, all legible at full size. No dense imagery or tables.

## Findings and comparison history

1. Initial comparison: P2 content width/spacing/type drift. The draft's 1120px content area and smaller heading/footer compressed the intended hierarchy and shifted alignment. Increased content width to 1200px, adjusted header top spacing, title to 46px, section title to 28px, task row padding to 24px, and footer to 16px.
2. Post-fix paired desktop comparison: major alignment, hierarchy and section rhythm now match the selected source. Remaining differences in antialiasing, icon stroke and generated texture are minor or intentional. No actionable P0/P1/P2 visual mismatch remains.
3. Independent source review and rendered mobile check: source buttons measured 30px tall. Added 44px minimum; actual rendered height is 44px. Removed routine age announcements from the clock. These repairs do not alter the collapsed default layout. Final desktop comparison and mobile checks passed.
4. Freshness review: saved active rows could remain active after 24 hours during failures. A failing regression test now passes; the UI clock recomputes age without changing source records. This fixes an implementation inconsistency with the design, not a change to the selected requirement.

## Required fidelity surfaces

- Fonts/typography: system sans-serif has the target's clear hierarchy, appropriate weights and readable 16–20px body/labels. Title is 46px desktop and 36px narrow. Line breaks remain natural. Small differences from the generated font and image antialiasing are acceptable P3 polish.
- Spacing/layout: 1200px centered desktop column, generous margins, quiet separators and opt-in details preserve the chosen density. Narrow view stacks metadata/actions without hiding controls; document width is exactly 390px at 390px viewport, including expanded source text.
- Colors/tokens: graphite background, off-white primary text, muted blue-gray secondary text, mint recorded progress and amber stale/error states. Flat CSS background intentionally avoids the generated mock's incidental texture. Focus styling is defined; no status depends on color alone.
- Image quality/assets: no photographic or decorative assets in the source. Standard refresh/caret/status icons use Phosphor React, with crisp library vectors. No custom SVG art or raster approximation of text.
- Copy/content: standalone English labels match the target. Real task states, next action and timestamps replace concept values. Original evidence stays behind explicit disclosure. No invented percentage, heartbeat or completion.

## Interaction and state checks

Verified in Chrome: real initial load; refresh; task details open/close; Vault text read/hide; recent verified results; all records and title/state search; old-record labels; offline refresh failure with retained data and unknown decisions; restored refresh; narrow viewport and expanded evidence without overflow; post-fix 44px source buttons; clock has no aria-live; fresh final navigation has no console errors or warnings. Loading state was observed during navigation. Unit tests cover source failures, missing states, concurrency, timeouts and stale-age expiry. Backend tests cover read-only database/WAL preservation and access/path boundaries.

The in-app browser initially loaded localhost but later timed out. Chrome with the explicit loopback address rendered reliably. This is a local browser delivery limitation, not application success inferred from HTTP status.

## Review and remaining limits

Claude CLI performed independent source review using sonnet/low (reported claude-sonnet-5-5), then a targeted follow-up approved the repairs and CI job. Parent directly inspected code and rendered behavior for the reviewer’s stated coverage limits. Evidence and finding dispositions are in the private Vault.

No physical phone, screen-reader session or remote access was tested. The first release is local-only and does not install startup persistence. The underlying store has no worker heartbeat and does not represent every native app chat.

## Implementation checklist

- [x] Selected design recorded before implementation.
- [x] Paired source/render comparison and visual repairs completed.
- [x] Primary interactions and responsive behavior checked.
- [x] Independent findings disposed and accepted repairs verified.
- [x] No actionable P0/P1/P2 design findings remain.

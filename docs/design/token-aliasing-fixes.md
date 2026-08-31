# Token-aliasing + typography-specificity repair (2026-08-31)

Merge-blocking review on `feature/ui-onboarding-activation-tour`. Scope: stylesheets
+ the contrast guard only (`ui/src/styles/**`, `ui/src/test/cssCascade.js`,
`ui/src/test/contrastRatio.js`, `ui/src/styles/__tests__/console-contrast.test.jsx`).
Did not touch `ui/src/components/onboarding/**`, `useTour.js`, `TourSpotlight.jsx`,
`ui/src/app/destinations.jsx`, `ui/src/components/console/useLaunchScenario.js`, or
`EalConsole.jsx` — all owned by concurrent agents.

## C1 (BLOCKER) — the 8 unaliased `--c-*` foregrounds

**Root cause.** `cortex-console.css`'s `.theme-console` block aliased the
*surface* tokens (`--c-void` etc.) and *text* tokens (`--c-text` etc.) onto
the real light/dark token layer, but left 8 *foreground accent* tokens as a
single hardcoded hex each, tuned only against the old permanently-dark void:
`--c-action`, `--c-action-bright`, `--c-action-deep`, `--c-signal`,
`--c-detected`, `--c-missed`, `--c-pending`, `--c-stitched`.

**Why the review's suggested fix ("alias to `--ac`/`--crit`/`--warn`/`--info`")
doesn't actually clear AA.** Those tokens are tuned as *fill* colors paired
with an on-accent text color (`--ac-tx` etc.), not as small text sitting
directly on `--s0..--s3`. Measured as plain foreground text in light theme:
`--ac` 2.8–3.1:1, `--ac-str` 1.9–2.1:1, `--warn` 2.3–2.7:1, `--info`
3.3–3.8:1 — all still short of the 4.5:1 text floor. Only `--crit`
(6.6–7.6:1) happens to already clear it, so `--c-missed` is the one token
that really can alias directly onto an existing dual-theme token.

**Fix implemented.** Added a new, *additive* token block to
`cortex-tokens.css` (NOT part of the 25-token contract per block — a
separate `:root` / `[data-theme="dark"]` pair below it, explicitly called
out in a comment so it's never mistaken for the designer's 25 and never
"reconciled" into that count): `--ink-action`, `--ink-action-bright`,
`--ink-action-deep`, `--ink-signal` (also serves `--ink-stitched`),
`--ink-detected`, `--ink-missed` (`= var(--crit)`), `--ink-pending`. DARK
values are the original hardcoded hex, unchanged, except `--ink-missed`
(see table). `cortex-console.css`'s 8 `--c-*` foregrounds now alias onto
these. Semantic distinction preserved: detected/missed/pending stay three
different hues (green/red/amber) in both themes; action and detected also
stay distinguishable within light theme (forest-green vs teal-green).

**Blast radius closed:** 175 `color:` declarations in `cortex-console.css`
alone (verified via `grep -rE "(^|[^-])color:\s*var\(--c-<token>\)" ui/src/styles/`:
signal 72, missed 40, pending 38, detected 31, action-bright 13, action 5,
action-deep 0, stitched 0 — the last two are never used as `color:`,
border/decoration only, so they were held to the 3:1 UI-component floor,
not 4.5:1).

### Measured contrast — before / after, both themes

Worst case across all four surfaces (`--s0..--s3`), flat and composited
into the token's own real `*-soft` background at its real alpha (`.detected`/
`.missed`/`.pending`/`.action` use `.14`, `.signal` uses `.12`).
`action-deep` and `stitched` are never used as `color:` (border/decoration
only), so they're held to 3:1, not 4.5:1.

| token | light before (flat) | light after (flat / composited) | dark before (flat) | dark after (flat / composited) |
|---|---:|---:|---:|---:|
| `--c-action` | 1.99 | 5.91 / 4.87 | 8.83 | unchanged |
| `--c-action-bright` | 1.57 | 5.91 / 4.85 | 11.18 | unchanged |
| `--c-action-deep` (border only, 3:1 floor) | n/a | 3.81 | n/a | unchanged |
| `--c-signal` | 1.94 | 5.72 / 4.79 | 9.06 | unchanged |
| `--c-detected` | 1.72 | 5.90 / 4.84 | 10.24 | unchanged |
| `--c-missed` | 2.50 | 6.61 / 5.18 | 5.48 (worst-comp 4.46, <4.5) | 5.39 / 4.46 → **now `var(--crit)`: 5.39/4.84** |
| `--c-pending` | 1.83 | 6.00 / 4.92 | 9.61 | unchanged |
| `--c-stitched` (border/decoration only, unused as `color:`) | n/a | 5.72 (3:1 floor) | n/a | unchanged |

`--c-missed` dark needed a real change, not just "unchanged": the
*original* hardcoded dark value (`#F97066`), once actually measured
against every surface + its own chip alpha (not only `--s2`, which is what
the review's own spot-check used), fell to 4.46:1 composited against
`--s3` — just under 4.5. Re-pointing it at `--crit` (dark: `#FF6A4D`, a
same-family salmon-red) clears 4.84:1 there while keeping every other
surface comfortably above floor.

### RED → GREEN evidence

New fixtures (`CHIP_FIXTURES`, `LINK_FIXTURES`, `STATUS_VALUE_FIXTURES` in
`console-contrast.test.jsx`) were run against the **unfixed** 8 hardcoded
hex values (temporarily reverted `--c-*` back to their pre-repair literals,
`--ink-*` untouched) before being restored:

```
FAIL detection-state chip: Detected  — #4FD1A1 on #E0F4ED (composited over .ttpb-stat, --s2) → 1.67:1
FAIL detection-state chip: Missed    — #F97066 on #F8E7E4                                    → 2.33:1
FAIL detection-state chip: Pending   — #F5A524 on #F8EEDB                                    → 1.77:1
FAIL link:                            #00C0E8 on #F1F3F2 (page bg)                           → 1.94:1
FAIL status value: signal             #00C0E8 on #FFFFFF (.telemetry, --c-surface)           → 2.16:1
FAIL status value: detected           #4FD1A1 on #FFFFFF                                     → 1.91:1
FAIL status value: pending            #F5A524 on #FFFFFF                                     → 2.04:1
→ 7 failed | 7 passed | 22 skipped (36)  [dark-theme half of the 7 new fixture pairs still passed]
```

After restoring the fix: `console-contrast.test.jsx` — **36/36 passed**
(18 fixtures × 2 themes: 5 title + 2 body + 4 tour-overlay + 3 chip + 1
link + 3 status).

## T1 — `.theme-console .mono` discarded font-size/letter-spacing

`.theme-console code, .theme-console kbd, .theme-console .mono` was one
(0,2,0) rule. Any destination rule styling an element that *also* carries
class `mono` in JSX (e.g. `.ttpb-stat__value { font-size: 20px }` on
`<div className="ttpb-stat__value mono">`) is bare (0,1,0) and lost.

**Fix.** Split into two rules: `code, kbd` keep the original
family+size+tracking (native elements, nothing competes with them);
`.mono` now carries only `font-family`. Removed the now-redundant local
override this exact bug had already been patched around once,
`destinations/adapters.css`'s `.theme-console .tools-destination .mono {
font-family: var(--font-mono); }` (0,3,0) — byte-identical to the new root
behavior.

**RED → GREEN** (`mono-specificity.test.jsx`, new file): `.ttpb-stat__value`
resolved `font-size="0.92em"` (RED) before the split, `"20px"` (GREEN)
after. A regression fixture also proves the root rule alone (without the
removed local override) still resolves `.mono`'s `font-family` correctly
inside `.tools-destination`.

## T2 — h1/h2/h3 typography half still winning over destination titles

`.theme-console h1,h2,h3` fixed `color` (by making the *winning* value
theme-correct everywhere — a legitimate move only because every heading
legitimately wants the same text color) but stayed (0,1,1) for
`font-family`/`font-weight`/`letter-spacing`/`line-height`/`font-size`,
which four destinations *do* want different values for. All four still
lost every one of those properties:

- `destinations/eal.css` `.eal-console__title` (h2) — rendered 20px/500 (Funnel Display) instead of the authored 22px/800 Montserrat
- `destinations/uctc.css` `.uctc__pagehead-title` (h1) — 32px instead of 25px
- `destinations/ttps.css` `.ttpb-detail__title` (h3) — 16px/500 instead of 17px/800
- `destinations/ttps.css` `.ttpb-modal__title` (h3) — weight 500 instead of 700

**Fix.** Prefixed each with `.theme-console` (`.theme-console
.eal-console__title` etc.), raising them to (0,2,0) — unconditional win,
same technique already proven elsewhere in this codebase
(`adapters.css`/`agents.css`/`ttp-detail.css`/58 of `uctc.css`'s own
rules), rather than lowering the shared rule (which would strip
typography from every plain heading that has *no* destination override).
Also expanded `eal.css`'s `font: 800 22px/1.15 'Montserrat'` shorthand into
longhand sub-properties — jsdom's CSSOM does not expand a shorthand-only
declaration into `font-size`/`font-weight` sub-properties for
`getPropertyValue`, so a shorthand-only rule was invisible to the cascade
resolver even after the specificity fix; longhand also matches the other
three destinations' existing convention. Removed the dead, stale-commented
duplicate `.eal-console__title { margin: 0; font-size: 18px; }` in
`cortex-console.css` (its comment's claim that EalConsole doesn't render
inside `.theme-console` is false — `AppShell.jsx` wraps the whole console).

**RED → GREEN** (`title-typography.test.jsx`, new file, 4 fixtures): all 4
failed pre-fix (resolved 20px/25px→32px/16px/weight-500 respectively vs.
expected authored values), all 4 pass post-fix.

## I1 — the guard's looser-than-reality bar + missing coverage

Two problems, both in `console-contrast.test.jsx`:

1. `largeText` was an *authored assumption* per fixture (the EAL title's
   comment: "800-weight 22px clears the WCAG bold-large threshold") rather
   than derived from what actually resolved — which, per T2, was 20px/500,
   not large text at all. **Fix:** added `isLargeText(el)` +
   `parsePx(value)`, deriving large-text status from the same resolved
   cascade (`resolveProperty` for `font-size`/`font-weight`) this file
   already trusts for color/background. All 5 title fixtures now compute
   `largeText` instead of asserting it.
2. Because the shared h1/h2/h3 rule wins `color` unconditionally (by
   design), every title fixture passed for that one reason regardless of
   which rule won typography — a 6th destination would have added zero
   real coverage, and the 175-declaration blast radius from C1 (chips,
   links, status values) had **zero** fixtures anywhere in this guard.
   **Fix:** added `CHIP_FIXTURES` (detected/missed/pending, composited
   into their real translucent `*-soft` background via a new
   `compositeOverAncestor()` helper — `contrastRatio.js::parseColor`
   deliberately refuses to score a translucent color directly),
   `LINK_FIXTURES` (`.theme-console a`), and `STATUS_VALUE_FIXTURES`
   (`.tel-value--signal/detected/pending` on real `.telemetry` markup) —
   18 fixtures × 2 themes = 36 tests total (up from 22).

## I2 — `@media` blocks flattened, not skipped

`cssCascade.js::collectRules()` fell into the generic `else if
(rule.cssRules)` branch for *any* container rule lacking `selectorText` —
true of `CSSMediaRule` as much as anything else — and collected its
children unconditionally, contradicting the module doc's explicit "no
@media/@supports/@layer evaluation" claim. Real instance:
`destinations/ttps.css`'s unconditional `.ttpb-detail { position: sticky;
...; max-height: calc(100vh - 48px); }` followed by an `@media
(max-width: 980px) { .ttpb-detail { position: static; ...; max-height:
none } }` block whose own comment says it's "placed last so it wins the
cascade... at equal specificity" *if its condition holds* — which the old
walker ignored, always applying it.

**Fix.** Skip any container rule carrying `conditionText` (the JS-visible
signal both `CSSMediaRule` and `CSSSupportsRule` define; this app uses
neither `@supports` nor `@layer` for theme-relevant rules per the existing
doc comment).

**RED → GREEN** (`ui/src/test/__tests__/cssCascade.test.js`, new file):
`resolveProperty(el, 'position')` on `.ttpb-detail` resolved `"static"`
(RED, the always-applied media block) before the fix, `"sticky"` (GREEN,
the unconditional rule) after.

## I5 — re-verified under the pinned toolchain

`npm ci` ran clean (251 packages, 0 vulnerabilities). Confirmed
`npx vitest --version` → `3.2.7` and `npx vite --version` → `7.3.6`
(matching `package-lock.json`'s pins, not the previously-installed 1.6.1).
Re-ran the full suite and the build under the pinned versions — both
green, same 76 files / 782 tests, same chunked bundle output (no
regression from the earlier vitest 1.x run this task started under).

## Out of scope / deliberately not touched

- The eager-vs-lazy destination CSS split, duplicate `.btn`/`.tenant-mgr__title`
  definitions, missing `ErrorBoundary` around lazy mounts, and the 33
  no-fallback `var()` references — all named explicitly out of scope by
  the review.
- `.eal-console__eyebrow` and other non-h1/h2/h3 rules in `eal.css` still
  use the `font:` shorthand; left alone since T2 only named the four
  h1/h2/h3 title rules and nothing else collides with them.
- Concurrent-agent files (onboarding components, `useTour.js`,
  `TourSpotlight.jsx`, `destinations.jsx`, `useLaunchScenario.js`,
  `EalConsole.jsx`) were not read for editing purposes and not modified.

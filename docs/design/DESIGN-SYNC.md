# Design ↔ development sync

This file is the seam between the **design track** (Claude Design, where the
console is mocked as HTML/CSS/JS prototypes) and the **development track**
(this repo). It records what was synced, from where, and what keeps the two
from drifting apart silently.

## Sync manifest

| | |
|---|---|
| Design project | `Cortex POV Engine UI Redesign` |
| Primary artifact | `POVengine Console.dc.html` (+ `support.js`) |
| Design system | PANW Executive Design System `panw-leadership-14ba87ae-5d5e-4097-b277-dd8179b582e0` |
| Repo | `hankthebldr/cortex-pov-engine` · branch `main` · path `ui/src` |
| Design-side last sync | 2026-09-14T18:14:52Z · tree `a4130a9fe88a` |
| Implemented in | `feat/povengine-console-design-parity` |

## What is vendored, and what that means

`ui/src/styles/ds/` holds the design system's token CSS **copied verbatim**
from the bundle above. It is the upstream source of truth for every brand
value in this console — do not hand-edit it. To take a new DS version: replace
those files wholesale, run the drift guard, and fix whatever it reports.

```
ui/src/styles/ds/
  colors.css       brand hues, neutral ramps, semantic aliases
  typography.css   families, weights, type scale, tracking
  spacing.css      4px spacing scale, radii, shadows, layout geometry
  gradients.css    the brand gradient system
  fonts.css        the Montserrat @import + the Helvetica Neue substitution note
```

`ui/src/styles/cortex-tokens.css` is this console's own contract. It does not
import the DS files — a console needs dark surfaces, alpha-composited chip
fills and measured 4.5:1 text variants that a presentation system has no
reason to define. What it *does* do is take every brand hue from the DS
unchanged. `src/styles/__tests__/ds-drift.test.js` asserts exactly that: if
`ds/colors.css` changes `--panw-orange` and `cortex-tokens.css` does not
follow, the suite fails and names both values.

Brand assets are copied, not referenced:

```
ui/public/assets/cortex-green.png   Cortex product mark, on-dark set
ui/public/assets/cortex-mono.png    Cortex product mark, on-light set
ui/public/assets/panw-reversed.png  PANW master lockup, reversed (see delta 3)
ui/public/icons/*.png               15 DS line icons, one per rail destination
```

The two Cortex marks and the PANW lockup were already in the repo and are
byte-identical to the design bundle's, so only the icon set was new. The header
selects between the Cortex pair on theme — the wrong one is invisible against
its own header, which is why it is selected rather than fixed.

No brand mark in this console is drawn, traced or reconstructed — the DS
forbids it, and every mark above is the real embedded asset.

## The contract that changed

The previous token contract made **Cortex green the primary accent** and
reserved **PANW orange for warn/gap signal**. This pass **inverts** that:

```
PANW orange  #FA582D   the one dominant brand color — chrome only
                       (active rail item, accent rules, primary buttons,
                       focus, title accent bar)
Cortex green #00CC66   status only — live, confirmed, observed, pass,
                       FULL coverage. Never chrome.
sub-brand hues         categorical coding colors (plane tints, series).
                       Fills, borders and dots. Never `color:` ink.
```

This is the brand's own one-dominant-color rule (`ds/colors.css` sets
`--color-accent: var(--panw-orange)`). The practical argument is narrower: a
console that paints its chrome green has spent the only color that carries a
verdict, and a green "CONFIRMED" pill stops reading as a result.

Dark is the console default. Light is retained as a complete, independent,
AA-clean token set — see the measured contrast table at the top of
`cortex-tokens.css`.

## Screen map

Where each design surface landed in the code.

| Design surface | Rail group | Implementation |
|---|---|---|
| Overview & Readiness | Start here | `components/console/OverviewView.jsx` |
| Setup Wizard (5 steps) | Start here | `components/console/SetupWizardView.jsx` |
| Components | 1 · Scope | `components/console/ComponentsView.jsx` |
| Tenant (4 tabs) | 1 · Scope | `components/console/TenantView.jsx` (Binding tab mounts `TenantManager.jsx`) |
| Agents | 1 · Scope | `components/console/TargetsView.jsx` |
| Library | 2 · Compose | `components/console/OperationsView.jsx` |
| CLI Items | 2 · Compose | `components/console/CliItemsView.jsx` |
| Composer node canvas | 2 · Compose | `components/console/ComposerView.jsx` · `ComposerCanvas.jsx` |
| Packages | 2 · Compose | `components/console/ToolAdapterCatalog.jsx` |
| Data Streams | 2 · Compose | `components/console/DataStreamsView.jsx` |
| TTP Cards | 2 · Compose | `components/console/TtpBrowserView.jsx` |
| UC / TC Index | 2 · Compose | `components/console/UcTcIndexView.jsx` |
| Launch Gate | 3 · Preflight | `components/console/ReadinessView.jsx` |
| Runs (list → detail → topology) | 5 · Observe | `app/destinations.jsx::RunsSurface` · `RunDetailView.jsx` · `InflightView.jsx` |
| Tenant Validation | 6 · Prove | `components/console/TenantValidationView.jsx` |
| Coverage | 6 · Prove | `components/console/CoverageView.jsx` |
| Proof & Export | 6 · Prove | `components/console/EvidenceView.jsx` |
| Shell chrome | — | `AppShell.jsx` · `ConsoleHeader.jsx` · `DestinationNav.jsx` · `FlowBar.jsx` · `EvidenceCollection.jsx` |
| Object pop-out | — | `components/console/ObjectSheet.jsx` + `sheets.js` |
| Coverage cross-tab + analytics sources | — | `components/console/PlaneCoverageMatrix.jsx` |
| Launch gate checks | — | `components/console/LaunchGate.jsx` |
| Workflow switcher / execution timeline | — | `WorkflowSwitcher.jsx` · `ExecutionTimeline.jsx` |
| Run topology / techniques | — | `RunTopology.jsx` · `RunTechniques.jsx` |
| Compose sibling strip | — | `components/console/ComposeTabs.jsx` |
| Seed catalogs | — | `components/console/povdata/` |
| Flow model / CTA rule | — | `app/povflow.js` |
| Tokens / theme | — | `styles/cortex-tokens.css` ← `styles/ds/` |

## IA decisions carried over from the design thread

These were argued out in the design conversation and are contracts, not
preferences. Changing one means changing it on both tracks.

- **The rail *is* the phase model.** Groups are `Start here` → `1 · Scope` →
  `2 · Compose` → `3 · Preflight` → `5 · Observe` → `6 · Prove`. The old
  separate phase bar is gone: two wayfinding systems that could disagree were
  the redesign's original complaint.
- **Phase 4 (Launch) has no rail entry**, and neither does Preflight-as-a-page
  beyond the Launch Gate. Both are *actions*, not destinations.
- **Overview is phase-less** (flow index `-1`). It is the front door; showing
  Scope and Compose as already ticked on the app's entry page was a real bug.
- **Data Streams and TTP Cards live in Compose, not Observe/Prove.** A stream
  is a composition input and a TTP card is authored content; neither is proof
  output.
- **One tenant per instance.** The instance is deployed once for a POV and dies
  with the lab, so a tenant list was modelling something that cannot happen.
- **Every flow CTA reads `Next: <destination as the rail names it>`**, with
  `Export report` as the single terminal action. Launch Gate keeps its own
  `Launch chain` button because that is an action, not navigation.
- **Detection objects are categorical; the verdict carries the color.**

## What is NOT at parity yet

Recorded plainly so the next sync does not rediscover these as bugs, and does
not assume they were done.

**1. The Composer canvas has no swimlane bands.** The design bands the canvas by
launch area (LAUNCH · ENDPOINT · CLOUD · NETWORK · DATA STREAMS · ANALYTICS ·
PROOF), keyed to the same `LANE_CATALOG` the Runs topology uses, so that
dragging a node into another band *retargets* it and its ingestion badge
changes with it. This repo's Design lens lays the chain out as a vertical
spine (`composerLayout.js::layoutChain`), and converting it to horizontal
swimlanes means replacing the layout engine, the stitch-overlay geometry that
rides its coordinates, and the tests that pin both. `COMPOSER_LANES` and
`LANE_BANDS` are already vendored in `povdata/corpus.js` for that work.
Partially covered today: the execution timeline shows each step's lane, and
the Runs topology bands by the same doors, so the vocabulary is consistent
even though the canvas does not yet draw it.

**2. The evidence collection's group toggles do not reach an export.** The
panel tallies what is included and what that weighs, and `Export collection`
navigates to Proof & Export rather than emitting a filtered bundle. The
selection is real; the plumbing from it to `downloadReportBundle` is not.

**3. The PANW master lockup is not on the Overview page.** The design puts the
reversed lockup there under an "internal tooling for Cortex Domain Consulting,
not a customer-facing product" framing. `NOTICE` states the opposite — that
this is an independent project, NOT an official PANW product, with no
affiliation claimed — and `ShellRedesign.test.jsx` guards against flying the
vendor mark. Those two framings are mutually exclusive, and choosing between
them is an affiliation and trademark decision rather than a design one, so it
is left to the repo owner. The asset is present at
`ui/public/assets/panw-reversed.png` if the framing changes. The Cortex product
mark IS used, in the header, theme-swapped — Cortex is named nominatively as
the platform under test, which NOTICE already covers.

**4. Surfaces without a SimCore endpoint read from seed catalogs.** Setup
wizard, Components, CLI items, Tenant Validation and the evidence collection
have no API behind them yet. They read `povdata/`, whose shape is what those
endpoints should return.

## Other deltas from the prototype

Deliberate, and not gaps.

- **Light theme is retained.** The prototype is dark-only. Dark is the default
  here, and light is a complete, independent, AA-clean token set — the measured
  contrast floors are at the top of `cortex-tokens.css`.
- **The soft chip fills are opaque.** The prototype authors them as `rgba()`
  over black. A translucent fill is a different colour on `--s0` than on `--s3`,
  so "what is the contrast of this label on its chip" has no single answer, and
  the contrast harness cannot score one at all. They are pre-composited over
  `--s1`, which is the surface those chips actually sit on.
- **Tenant registration survives.** The prototype's Tenant page is read-only.
  Binding a tenant is a real operation that has to happen once, so the existing
  wizard is the fourth tab rather than being deleted.
- **`environments`, `eal` and the legacy `readiness` id stay routable** but
  unlisted. Their content moved into Components and Data Streams; the routes
  remain so existing deep links resolve instead of silently falling back to the
  default destination, which would look like data loss.
- The prototype declares a **1200px minimum width** and scrolls horizontally
  below it, rather than reflowing its own chrome. That is carried over.
- Canvas node positions are per-session in the prototype. Here they persist per
  workflow in `localStorage`; nothing is written back to the server yet.

## Guards

Four suites hold this seam together. Each exists because the corresponding
mistake was actually made:

| Suite | What it catches |
|---|---|
| `styles/__tests__/ds-drift.test.js` | a DS refresh moving a brand hue without the console following, and a revert of the accent inversion |
| `styles/__tests__/cortex-tokens.test.js` | the contract itself — surfaces, the fill/ink split, opaque chip fills, and `--cortex-success` drifting back onto the accent |
| `app/__tests__/everyDestinationRenders.test.jsx` | one destination's markup swallowing or leaking into another's |
| `console/povdata/__tests__/povdata.test.js` | a lifted function referencing a symbol that is no longer in scope, and the topology being drawn rather than derived |
| `console/__tests__/navOrderMatchesPhases.test.js` | the rail and the flow bar disagreeing about which phase owns a destination |

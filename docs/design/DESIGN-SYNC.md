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
ui/public/brand/cortex.png          Cortex product mark, on-dark (green) set
ui/public/brand/panw-reversed.png   PANW master lockup, reversed for dark
ui/public/icons/*.png               15 DS line icons, one per rail destination
```

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
| Tenant (4 tabs) | 1 · Scope | `components/console/TenantManager.jsx` |
| Agents | 1 · Scope | `components/console/TargetsView.jsx` |
| Library | 2 · Compose | `components/console/OperationsView.jsx` |
| CLI Items | 2 · Compose | `components/console/CliItemsView.jsx` |
| Composer node canvas | 2 · Compose | `components/console/ComposerView.jsx` · `ComposerCanvas.jsx` |
| Packages | 2 · Compose | `components/console/ToolAdapterCatalog.jsx` |
| Data Streams | 2 · Compose | `components/console/DataStreamsView.jsx` |
| TTP Cards | 2 · Compose | `components/console/TtpBrowserView.jsx` |
| UC / TC Index | 2 · Compose | `components/console/UcTcIndexView.jsx` |
| Launch Gate | 3 · Preflight | `components/console/ReadinessView.jsx` |
| Runs (list → detail → topology) | 5 · Observe | `components/console/RunDetailView.jsx` · `InflightView.jsx` |
| Tenant Validation | 6 · Prove | `components/console/TenantValidationView.jsx` |
| Coverage | 6 · Prove | `components/console/CoverageView.jsx` |
| Proof & Export | 6 · Prove | `components/console/EvidenceView.jsx` |
| Shell chrome | — | `AppShell.jsx` · `ConsoleHeader.jsx` · `DestinationNav.jsx` · `FlowBar.jsx` |
| Object pop-out | — | `components/console/ObjectSheet.jsx` |
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

## Known deltas from the prototype

Recorded so the next sync does not "rediscover" them as bugs.

- The prototype is demo-data only. Surfaces with a real SimCore API behind them
  (Library, Composer, Runs, Coverage, Readiness) are bound to it; surfaces the
  backend does not serve yet (Setup wizard, Components, CLI items, Tenant
  Validation, the evidence collection) read from seed catalogs under
  `components/console/povdata/`, which are marked as such and are the exact
  shape the API should eventually return.
- The prototype declares a **1200px minimum width** and scrolls horizontally
  below it, rather than reflowing its own chrome. That is carried over.
- Canvas node positions are per-session in the prototype. Here they persist per
  workflow in `localStorage`; nothing is written back to the server yet.

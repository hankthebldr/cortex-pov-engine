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
| Implemented in | `feat/povengine-console-design-parity` (parity pass) · `feat/console-minimal-guided-setup` (minimal pass) |

> **The development track is now AHEAD of the design track.** The minimal pass
> (2026-10, below) collapsed the 17-surface rail to a task rail, added a
> Get started checklist, narrowed the colour rule and removed every seed-data
> surface. The Claude Design prototype still shows the 17-surface IA and the
> seed numbers. Until the prototype is updated to match, treat this file — not
> the prototype — as the source of truth for IA and colour, and port the
> sections below back into the design project on its next sync.

## Minimal pass (2026-10)

Driven by DC feedback on a live install: the console was overwhelming, did not
say what the next task was, used colour without a purpose, and showed numbers
that were not this instance's (a tenant called Acme Financial, a jumpbox that
answered every poll, twelve components) while the header — reading real state
— contradicted them.

```
Rail                         Tabs inside the task
────────────────────────     ──────────────────────────────────────────────
Get started   (home)         —   setup checklist on real state
Simulate      #/library      Scenarios · Build a chain · Launch gate
Runs                         —
Results       #/proof        Report · Coverage
Catalog       #/ttps         TTP cards · Packages · Data streams · UC/TC index
── Manage
Agents · Tenant
```

- **Get started** (`GetStartedView.jsx`, model in `setupProgress.js`) is the
  default destination: install an agent → connect the tenant → data collector
  (optional) → Broker VM (optional) → first simulation. Agent, tenant and run
  state are read from SimCore; collector and Broker VM have no SimCore API, so
  the DC confirms them and the page says "you confirmed", never "verified". A
  failed first run is not "done" — the step says it stopped early and links to
  the run. The suggested first run hands off to the guided launch with the
  active online agent preselected as the target.
- **Colour rule.** Orange = the single primary action on a screen. Green =
  healthy / done / detected. Amber = a real warning; red = a real failure
  (dark `--crit` moved off salmon, which read as brand orange). Everything
  else is neutral, and "selected" reads as brightness, not hue. Implemented at
  the token layer (`--ac-str/-ink/-soft/-line` neutral, `--ac-brand` holds the
  hex) plus `styles/povengine-minimal.css`, loaded last.
- **Chrome.** Flow bar removed. The acknowledged safety notice is a slim
  neutral line (the unacknowledged consent gate is unchanged and still loud).
  The degraded-health band is one neutral line. Theater moved into ⌘K. The
  evidence-collection pill (seed POV name) left the header. Phase eyebrows and
  accent bars left every page. The first-run tour no longer auto-starts.
- **Seed data removed.** Components, CLI items, the setup/scope wizard and
  Tenant validation pages are retired (their routes redirect). The Tenant page
  shows the real registry. The Launch Gate's canned "will this chain reach the
  target" block, the Coverage planes × doors matrix, the run-detail topology
  and techniques tabs and the composer's seed workflow switcher are gone.
  `povdata/` is vocabulary only (lane and plane names, tints, band geometry),
  guarded by `povdata.test.js`.

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

> Superseded in part by the minimal pass above: orange is now the primary
> action only, not "chrome". Selection, rules and eyebrows are neutral.

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

Where each surface lives in the code after the minimal pass.

| Surface | Rail / tab | Implementation |
|---|---|---|
| Get started | Get started | `components/console/GetStartedView.jsx` · `setupProgress.js` · `useSetupProgress.js` |
| Scenarios (Library) | Simulate › Scenarios | `components/console/OperationsView.jsx` |
| Composer node canvas | Simulate › Build a chain | `components/console/ComposerView.jsx` · `ComposerCanvas.jsx` |
| Launch Gate | Simulate › Launch gate | `components/console/ReadinessView.jsx` |
| Guided launch | (from Get started / ⌘K) | `app/destinations.jsx::GuidedPovFlow` · `LaunchView.jsx` |
| Runs (list → detail) | Runs | `app/destinations.jsx::RunsSurface` · `RunDetailView.jsx` · `InflightView.jsx` |
| Report | Results › Report | `components/console/EvidenceView.jsx` |
| Coverage | Results › Coverage | `components/console/CoverageView.jsx` |
| TTP Cards | Catalog › TTP cards | `components/console/TtpBrowserView.jsx` |
| Packages | Catalog › Packages | `components/console/ToolAdapterCatalog.jsx` |
| Data Streams | Catalog › Data streams | `components/console/DataStreamsView.jsx` |
| UC / TC Index | Catalog › UC / TC index | `components/console/UcTcIndexView.jsx` |
| Agents | Manage | `components/console/TargetsView.jsx` |
| Tenant | Manage | `components/console/TenantView.jsx` → `TenantManager.jsx` |
| About POVengine | (link from Get started) | `components/console/OverviewView.jsx` |
| Shell chrome | — | `AppShell.jsx` · `ConsoleHeader.jsx` · `DestinationNav.jsx` · `SectionTabs.jsx` |
| Execution timeline / lanes | — | `ExecutionTimeline.jsx` · `composerLanes.js` |
| Lane and plane vocabulary | — | `components/console/povdata/` |
| Tokens / theme | — | `styles/cortex-tokens.css` ← `styles/ds/` · `styles/povengine-minimal.css` |

## IA decisions carried over from the design thread

These were argued out in the design conversation and are contracts, not
preferences. Changing one means changing it on both tracks.

- **The rail is a task list** (minimal pass). Five tasks plus Manage; every
  other page is a tab inside one task (`SECTIONS` in `app/destinations.jsx`).
  This replaced "the rail is the phase model", which was correct as a model
  but answered "what does this contain" rather than "what do I do next".
- **A destination id is a route, not a label.** `library` is labelled
  Simulate, `ttps` Catalog, `proof` Results, `adapters` Packages. Retired pages
  keep their ids and redirect.
- **One tenant per instance.** The instance is deployed once for a POV and dies
  with the lab, so a tenant list was modelling something that cannot happen.
- **Detection objects are categorical; the verdict carries the color.**

## What is NOT at parity yet

Recorded plainly so the next sync does not rediscover these as bugs, and does
not assume they were done.

**1. The Composer swimlanes are a lens, not the only layout.** The design
bands the canvas by launch door (LAUNCH · ENDPOINT · CLOUD · NETWORK · DATA
STREAMS · ANALYTICS · PROOF), keyed to the same `LANE_CATALOG` the Runs
topology uses, so that dragging a node into another band retargets it and its
door badge changes with it. That now exists as the **Lanes** lens beside
Design and Run: `composerLanes.js` is the one lane derivation (`laneOf`) the
canvas, the execution timeline and the Runs topology all share; `layoutLanes`
puts execution order on x and door on y; a drag that ends in another band
calls `setStepLane`, which records the lane on the draft (`laneOverrides`,
persisted as `composer_lanes` beside `composer_layout`) and re-badges the
card. What remains deliberately different from the design: the Design lens
keeps the vertical spine and the stitch overlay that rides its coordinates,
because that is what the stitch geometry and its tests were built on; and a
lane drag writes through to the step's data ONLY for the two channel-backed
doors (DATA STREAMS ⇄ `channel: eal`, ENDPOINT ⇄ `channel: agent`). Dragging
a NDR step into CLOUD records the intent and moves the badge but does not
rewrite its plane — the plane is what its expected detections were authored
against, and silently changing it on a drag would be a claim the readout later
paid for. Guards: `composerLanes.test.js`, the Lanes block in
`ComposerCanvas.test.jsx`.

**2. Collector and Broker VM setup cannot be verified.** SimCore has no
endpoint that can see a tenant-side collector or Broker VM, so Get started
takes the DC's confirmation and labels it as such. The EAL collector preflight
(`POST /api/eal/campaigns/{id}/collectors/preflight`) is campaign-scoped; a
campaign-free variant would let these steps become verified.

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

**4. Seed-only design surfaces are not implemented.** The prototype's
Components inventory, CLI items, scoping wizard, Tenant validation sheets,
evidence collection, planes × doors matrix and run topology have no SimCore
data behind them. Rather than render seed numbers as facts, the minimal pass
removed them. Each can come back when an endpoint exists to back it.

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
- **Tenant registration is the Tenant page.** The prototype's Tenant page is
  read-only and seeded; here it is the real registry and registration form.
- **`environments`, `eal`, `guided` and the legacy `readiness` id stay
  routable** but unlisted, so existing deep links resolve.
- The prototype declares a **1200px minimum width** and scrolls horizontally
  below it, rather than reflowing its own chrome. That is carried over.
- Canvas node positions are per-session in the prototype. Here they persist per
  workflow in `localStorage`; nothing is written back to the server yet.

## Guards

Each exists because the corresponding mistake was actually made:

| Suite | What it catches |
|---|---|
| `styles/__tests__/ds-drift.test.js` | a DS refresh moving a brand hue without the console following |
| `styles/__tests__/cortex-tokens.test.js` | the contract — surfaces, opaque chip fills, and any accent role other than the primary action turning orange again |
| `app/__tests__/everyDestinationRenders.test.jsx` | the rail drifting from the five tasks + Manage; one page's markup leaking into another's |
| `app/__tests__/mastheadEyebrow.test.jsx` | the retired phase vocabulary reappearing on a page |
| `console/__tests__/navTaskRail.test.js` | an off-rail page whose parent is not on the rail; a tab pointing at nothing; an e2e route id that no longer resolves |
| `console/povdata/__tests__/povdata.test.js` | `povdata/` growing record-shaped (seed) exports again |
| `components/__tests__/setupProgress.test.js` · `GetStartedView.test.jsx` | a setup step marked done without real state, a confirmed step presented as verified, a failed run counted as done |

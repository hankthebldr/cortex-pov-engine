# Design ↔ development sync

This file is the seam between the **design track** (Claude Design, where the
console is mocked as HTML/CSS/JS prototypes) and the **development track**
(this repo). It records what was synced, from where, and what keeps the two
from drifting apart silently.

## Sync manifest

| | |
|---|---|
| Design project | `Cortex POV Engine UI Redesign` — moved from Claude Design to a claude.ai canvas on 2026-11-12: https://claude.ai/artifact/UtUX2baFiCdFAsrkQi5tcb |
| Primary artifact | `POVengine Console.dc.html` (+ `support.js`) |
| Design system | PANW Executive Design System `panw-leadership-14ba87ae-5d5e-4097-b277-dd8179b582e0` |
| Repo | `hankthebldr/cortex-pov-engine` · branch `main` · path `ui/src` |
| Design-side last sync | 2026-11-13 · canvas version `1794626399-70f5` ("v33": v26 + changes C1–C10) |
| Implemented in | `feat/povengine-console-design-parity` (parity pass) · `feat/console-minimal-guided-setup` (minimal pass) · v1.2.0 parity pass (below) · design v26 (2026-11-13, PRs #250–#260) · design v33 C1–C10 + Launch gate MVP (2026-11-13, PRs #286–#308, below) |
| Last parity audit | 2026-11-12, console at `dev@6029122d`: https://claude.ai/artifact/UeVHFu4TKUCMz4tc4GRgig |
| Current handoff | **Design v33 → `ui/src`, 2026-11-13:** the canvas's "CHANGES SINCE v26" notes (C1–C10) and the owner's v33 handoff, recorded below. v26's own handoff: `docs/design/2026-11-13-v26-design-to-code.md` |

> **One design source, one review surface (2026-11-13).** The canvas above is
> the only design source; the other console-design sessions were archived so
> nothing drifts beside it. The code on `dev` ships what the next section
> lists, and the owner reviews a `:8888` container rebuilt from `dev`. Where
> the owner's live review overrode the canvas, the override is listed below
> and the canvas is the side that has to catch up.

## Design v33 — changes C1–C10 and the Launch gate MVP (2026-11-13)

The canvas's "CHANGES SINCE v26" notes (C1–C10) and the owner's v33 handoff
("Launch gate MVP first; then navigation, dashboards, deep-dive thesis,
component explanations, library completeness; ANALYTICS not orange"), as
built. All into `dev`.

| Change | What the code does | PR |
|---|---|---|
| C1 · New POV run on the Launch gate · v33 MVP | The Launch gate (`preflight`) is the one page a run starts from: What runs › Readiness › Where it runs (agents inline with their state; an offline one listed, not choosable; the offline bundle) › What it will do › an authorisation tick that starts unticked › one **Run**, disabled with its reasons listed. Push: Run saves the bundle and records the run (Staged); no separate download. `guided` and `readiness` redirect to it, and every launch link goes straight there. | #299, #307 |
| C2 · Runs · Compare | Runs header: primary **Run** (the verb set has no "New run") beside Compare runs. Compare picks 2–3 finished runs; only a read-back run may say Detected / Not seen; others say "Ran — not read back" / "Did not run". | #301 |
| C2b · Lab target + WAITING runs | **Not built.** The v33 MVP keeps push runs Staged and draws no lab target. The UC/TC page reads an unreported push run as "Waiting" rather than "ran". | — |
| C3 · VerdictReason | Run detail: a "Test case" line (PENDING + "nothing executed" / "detectors soaking" + the TC + the action sentence) and the Cortex Engine line under the chain. | #291 |
| C4 · verb fixes | Already on `dev` before this pass. | — |
| C5 · Catalog | TTP cards filter by **Subdomain** and Tactic (owner override, below). UC/TC: four tiles, verdict as icon + word, a Runs rollup. | #292, #305 |
| C6 · UC/TC tabs | Index · Mechanisms · Coverage (the five-stage proof path) · Gaps (one verb per row), all from SimCore; mechanism tenant state and door checks read "Can't check" until an API reports them. | #305 |
| C7 · component explanations | Every component page opens with What it is · Why the POV needs it · What it unlocks · Where it lives, corrected where the canvas copy claimed more than the code does. | #302 |
| C8 · Dashboards | A rail item under Run & observe: Detection source × Use case, MITRE ATT&CK (the heatmap ported onto `/api/mitre/coverage`), Planes × doors; detected only from a read-back run. | #308 |
| C9 · navigation | Header Back / Forward (⌘[ / ⌘]) over the router's own history, and a clickable breadcrumb journey › page › object. | #303 |
| C10 · deep-dive thesis + verbs | Scenario, TTP and detection sheets open with "Why this matters" (Thesis · Why we simulate it · What proves it · What it needs) from the documents' own fields; actions are verbs with icons, navigation is links. | #304 |
| v33 · library completeness + stepper | Every Scenarios card shows UC/TC · Source (its doors) · MITRE; an **Unmapped** filter with its count (205 corpus scenarios: 8 unmapped, all for underived detection sources). The extended library, the 362 staged pairs under `drafts/detection-library/`, is listed read-only (`GET /api/scenarios/staged`): not launchable, not coverage. A Scenarios › Build a chain › Launch gate › Run stepper carries the scenario / run id between the four pages. | #310 |
| v33 · ANALYTICS tint | The ANALYTICS plane is the DS ink-400, not PANW orange. | #300 |

### Owner overrides of the canvas (the canvas should follow these)

Decided by the owner in live review on 2026-11-13; the code follows them.

- **Colour.** The warning colour is the DS gold-600 (`#C7AA47`), muted from
  Strata yellow (#296). Greens come in shades: one selected state everywhere
  (deep wash `--ac-soft` + matte line `--gr-matte`), and one primary button in
  both themes, the deep matte green `--btn` `#0A6231` with white text. Secondary
  buttons and the error badge are greyscale; the badge's "!" alone carries the
  tone.
- **TTP cards.** No run-status line and no Status or Platform filter (#292);
  Subdomain and Tactic only. No platform chips on cards.
- **Favorites.** A ★ tab first in the Composer palette; a scenario earns the ★
  by measurement (lab-ready, every step real telemetry, a Correlation, ≥ 4
  detection types), not from a hand-kept list (#286).
- **Composer.** Start / Stop are the chain's anchors (not "Launch" / "Proof"),
  above the signal-path lanes rather than as lanes (#278).
- **Words.** No "shelf" or "staged" for tools anywhere in the console (#281);
  Packages is a plain catalog (#279).
- **Launch.** One page and one Run (the v33 MVP above); C2b's lab target and
  WAITING state are not built.

### Not built, or found and left (follow-ups)

- C2b (lab target, WAITING runs) — superseded by the MVP; revisit with the owner.
- UC/TC Index keeps the use-case rail + test-case table rather than the
  canvas's one-row-per-use-case table, because Composer and TTP cards deep-link
  `?uc=` / `?tc=` into it.
- The TTP card does not list ABIOC or Prevention detections (96 cards carry
  ABIOCs, 3 carry Prevention).
- At 375 px the nav rail draws over the lower half of the scenario sheet.
- AIRS (`orange-500`) and DLP (`orange-900`) plane tints are still in the
  orange family; only ANALYTICS was named.
- Mechanism tenant state (C6) has no SimCore API yet; it reads "Can't check".

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
Agents · Tenant · Firewall
```

- **Get started** (`GetStartedView.jsx`, model in `setupProgress.js`) is the
  default destination: install an agent → connect the tenant → data collector
  (optional) → Broker VM (optional) → first simulation. Agent, tenant and run
  state are read from SimCore; collector and Broker VM have no SimCore API, so
  the DC confirms them and the page says "you confirmed", never "verified". A
  failed first run is not "done" — the step says it stopped early and links to
  the run. The suggested first run hands off to the guided launch with the
  active online agent preselected as the target.
- **Colour rule (minimal pass; replaced by v26 §9.1 — see "The contract
  today" below).** Orange = the single primary action on a screen. Green =
  healthy / done / detected. Amber = a real warning; red = a real failure
  (dark `--crit` moved off salmon, which read as brand orange). Everything
  else is neutral, and "selected" reads as brightness, not hue.
- **Chrome.** Flow bar removed. The acknowledged safety notice is a slim
  neutral line (the unacknowledged consent gate is unchanged and still loud).
  The degraded-health band is one neutral line. Theater moved into ⌘K. The
  evidence-collection pill (seed POV name) left the header. Phase eyebrows and
  accent bars left every page. The first-run tour no longer auto-starts.
- **Seed data removed.** Components, CLI items, the setup/scope wizard and
  Tenant validation pages are retired (their routes redirect). The Tenant page
  shows the real registry. The Launch Gate's canned "will this chain reach the
  target" block, the Coverage planes × doors matrix and the composer's seed
  workflow switcher are gone. The run-detail Topology and Techniques tabs left
  as seed views and came back on live run records (e1e819de, 2026-11-02),
  banded by signal path (34907748, 2026-11-10).
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

## The contract today (design v26 §9.1, 2026-11-13)

Cortex green (`--ac`, `--br`; `#00CC66` in dark, the deep `#0A6231` fill in
light so its white label clears 7.5:1) is the primary action, the selected
state and the brand: the one filled button on a screen, the active rail bar,
the active tab line, a selected item's soft fill and line. Green also means
healthy / done / detected (`--pos`), so a status is never colour alone — it
carries its glyph and word (`kit/StatusIcon`). The warning colour (`--warn`)
is the DS **gold-600** (`#C7AA47`), muted from Strata yellow to an accent by
Henry on 2026-11-13; coral (`--crit`) is down / missing / broken /
destructive. Greens come in shades (2026-11-13): bright `--ac` is the one
primary button; every selected state — tab, chip, segmented button, the
active rail item — is the deep wash `--ac-soft` with the matte line
`--gr-matte` (DS panw-green-700); `--gr-mid` (green-500) and `--gr-pale`
(green-200) are the secondary greens.
PANW orange is the NGFW family tint (`--family-ngfw`) and the categorical
plane tints, nothing else; `--ac-brand` and the `--orange` aliases are
retired. Implemented in `cortex-tokens.css` and `povengine-minimal.css`
(loaded last); the shared constructs are `components/console/kit` with
`styles/povengine-kit.css`. `ds-drift.test.js` pins `--br` and `--pos-str` to
the DS's `--panw-green`, `--warn-str` to `--gold-600`, the three greens to
`--panw-green-700` / `--green-500` / `--green-200`, and `--family-ngfw` to
its `--panw-orange`; the DS
itself still names orange its presentation accent, and the console
deliberately does not follow it.

## The contract that changed (2026-09 parity pass — history)

> Superseded twice: by the minimal pass (orange = primary action only,
> selection neutral) and then by v26 §9.1 above (green = primary and
> selected).

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

Where each surface lives in the code after design v26 (2026-11-13). The rail
is three journeys plus Reference; everything else is a component page, a tab
or a link.

| Surface | Rail / tab | Implementation |
|---|---|---|
| Get started | Onboard & connect › Get started | `components/console/GetStartedView.jsx` · `setupProgress.js` · `useSetupProgress.js` (each step points at its component's Configure) |
| Components hub | Onboard & connect › Components | `components/console/ComponentsView.jsx` · `componentModel.js` · `app/componentRoutes.js` |
| POVengine | Components › POVengine (API tab generated from OpenAPI) | `component/PovengineComponent.jsx` |
| Agents | Components › Agents (Fleet tab is the former Agents page) | `component/AgentsComponent.jsx` → `TargetsView.jsx` |
| Lab | Components › Agents (`#/environments`) | `components/console/LabView.jsx` |
| Tenant | Components › Tenant (Read-back & integrations tab) | `component/TenantComponent.jsx` → `TenantManager.jsx` · `CollectorTargetsPanel.jsx` |
| HTTP collector | Components › HTTP collector | `component/HttpCollectorComponent.jsx` |
| Broker VM · XDR Collector · Cortex Engine | Components › each ("you confirmed" / Unknown: no SimCore API) | `component/ConfirmedComponents.jsx` |
| Firewall | Components › Firewall (Setup · Sources tabs) | `component/FirewallComponent.jsx` |
| Component page shape + Configure frame | — | `component/ComponentPage.jsx` · `ConfigureFrame.jsx` · `checks.js` |
| Component About lines (v33 C7: What it is · Why the POV needs it · What it unlocks · Where it lives) | every component page, every tab | `components/console/componentAbout.js` (copy, keyed by component id) → `ComponentPage.jsx` |
| Scenarios (Library) | Compose & simulate › Scenarios | `components/console/OperationsView.jsx` · `ScenarioGrid.jsx` · `ScenarioInspector.jsx` (the scenario sheet) |
| Composer node canvas | Compose & simulate › Build a chain | `components/console/ComposerView.jsx` · `ComposerCanvas.jsx` |
| Launch Gate | Compose & simulate › Launch gate | `components/console/ReadinessView.jsx` |
| Guided launch | (from Scenarios › Run / ⌘K) | `app/destinations.jsx::GuidedPovFlow` · `LaunchView.jsx` |
| Runs (list → detail) | Run & observe › Runs | `app/destinations.jsx::RunsSurface` · `RunDetailView.jsx` · `RunLifecycleStrip.jsx` · `RunChain.jsx` |
| Broken step troubleshooting sheet | Runs › a run › a Broken card | `components/console/BrokenStepSheet.jsx` · `brokenStep.js` |
| Runs · Compare | Runs (`?compare=1`) | `components/console/MultiRunCompare.jsx` |
| Results (Report) | Run & observe › Results ("Result of" bar) | `app/destinations.jsx::ProofSurface` · `ResultOfBar.jsx` · `EvidenceView.jsx` |
| Dashboards (v33 C8: Detection source × Use case · MITRE ATT&CK · Planes × doors, plus PANW stack · Competitive; state from run read-back only) | Run & observe › Dashboards (route `#/coverage`, `?tab=`) | `components/console/CoverageView.jsx` · `dashboardModel.js` (read-back state, grids, ATT&CK matrix) |
| TTP Cards | Reference › Catalog › TTP cards | `components/console/TtpBrowserView.jsx` |
| Packages | Catalog › Packages | `components/console/ToolAdapterCatalog.jsx` |
| Data Streams | Catalog › Data streams | `components/console/DataStreamsView.jsx` |
| UC / TC Index | Catalog › UC / TC index | `components/console/UcTcIndexView.jsx` |
| "Your chain" tray | Scenarios and Catalog pages | `components/console/ChainTray.jsx` · `context/ChainTrayContext.jsx` |
| Chain stage (editable · live · preview) | Composer Lanes lens, Run detail, scenario sheet | `components/console/ChainStage.jsx` · `chainStage.js` |
| "Why this matters" (design v33 C10) | First on the scenario sheet, a TTP card, and an opened detection | `components/console/WhyThisMatters.jsx` · `sheetThesis.js` (every line read off the document; a missing part says "Not written for this … yet.") |
| Traffic / EAL | (from Scenarios, `#/eal`) | `components/EalConsole.jsx` |
| About POVengine | (link from Get started) | `components/console/OverviewView.jsx` |
| Shell chrome | — | `AppShell.jsx` · `ConsoleHeader.jsx` · `DestinationNav.jsx` · `SectionTabs.jsx` · `CommandPalette.jsx` |
| Shared constructs | — | `components/console/kit/` (StatusIcon · ErrorBadge · Button · Toast · PageIcon · Icon). Button's `verb` draws the verb's icon (`VERB_ICONS`, design v33 C10); an action that only navigates is a link, not a Button |
| Header Back / Forward and breadcrumb (design v33 C9) | header, beside the brand | `HistoryNav.jsx` · `Breadcrumb.jsx` · `app/navHistory.js` (the router's own history) · `app/breadcrumb.js` |
| Shared constructs | — | `components/console/kit/` (StatusIcon · ErrorBadge · Button · Toast · PageIcon) |
| Lanes | — | `composerLanes.js` (`ExecutionTimeline.jsx` and `ConsoleStepper.jsx` have no importers; do not model them) |
| Lane and plane vocabulary | — | `components/console/povdata/` |
| Tokens / theme | — | `styles/cortex-tokens.css` ← `styles/ds/` · `styles/povengine-kit.css` · `styles/povengine-minimal.css` |

Retired routes redirect: `setup`, `scope` → Get started; `validation` →
Results; `agents` / `tenants` / `firewall` → the component page's Fleet /
Read-back / Sources tab; `readiness` is an alias of the Launch gate.

All eight slices of the v26 handoff are in `dev` (PRs #250, #252, #253, #254,
#257, #258, #259, #260, plus the Broken step sheet).

## IA decisions carried over from the design thread

These were argued out in the design conversation and are contracts, not
preferences. Changing one means changing it on both tracks.

- **The rail is three journeys** (design v26 §1): Onboard & connect · Compose
  & simulate · Run & observe, plus Reference. Components expands into the
  eight component pages. This replaced the minimal pass's "five tasks plus
  Manage", which replaced "the rail is the phase model"; each answered "what
  do I do next" better than the one before.
- **A destination id is a route, not a label.** `library` is labelled
  Simulate, `ttps` Catalog, `proof` Results, `adapters` Packages. Retired pages
  keep their ids and redirect.
- **One tenant per instance.** The instance is deployed once for a POV and dies
  with the lab, so a tenant list was modelling something that cannot happen.
- **Detection objects are categorical; the verdict carries the color.**

## v1.2.0 parity pass (2026-11-12)

The canvas moved from Claude Design to claude.ai as "Cortex POV Engine UI
Redesign" (page "POVengine Console"). An audit compared it with the console at
`dev@6029122d`, screen by screen with screenshots of both, token by token,
plus an accessibility and token-adherence review of `ui/src`:
https://claude.ai/artifact/UeVHFu4TKUCMz4tc4GRgig

- **Already level:** the `_ds` token files, the 15 rail icons and the PANW
  lockup are byte-identical; the canvas had the task rail, the tab strip, Get
  started, Firewall and the immersive Composer.
- **Design owed, superseded:** the audit's list (adopt the minimal colour rule,
  retire Components, redraw the newer screens, strip seed data) was approved on
  2026-11-12 and overtaken the next day by the v26 handoff, which keeps the
  seed-data rule but reverses the colour rule and brings Components back (§9 of
  the handoff). Components the canvas still did not draw at the audit: New POV
  run, TTP cards and UC / TC index as their own pages, Runs · Compare, Lab, and
  the VerdictReason chip.
- **Code owed, fixed in this pass:**

  | PR | Fix |
  |---|---|
  | #240 | Abort on Runs threw (no `onAbort`); the run page had no Abort; the confirm dialog mounted outside `.theme-console` and rendered unstyled |
  | #245 | The `--cortex-*` aliases were declared on `:root` only, so they kept light values inside the dark shell (Storyline, Causality, Composer badges); Causality's dark styling followed the OS, not the console theme; `test/cssCascade.js` resolved custom properties where no browser does |
  | #242 | Command palette: hover selected the last row and Enter ran it; the search box had no name |
  | #244 | The skip link changed page instead of focusing the workspace |
  | #246 | h1s that disagreed with their tab or registry label; "Tools & Payloads ▸" on the Launch gate; three h1s on New POV run |
  | #247 | Tenant form, launch selects and the TTP editor textarea had no programmatic labels |
  | #243 | Agent cards now show capabilities and whether steps run as their declared service accounts (the canvas drew this; the code dropped it) |

- **Deferred, recorded so the next pass does not rediscover them:** orange used
  as text and selection shown by hue in about 40 places (needs the canvas
  colour decision first); three plane palettes and plane tints under 3:1 on
  white; 40 font sizes, 43 radii and 52% of spacing off the 4px grid; three
  focus colours, `role="tab"` without arrow keys, dialogs without focus traps;
  the PENDING verdict chip's `--warn-str` text (about 1.5:1 on light).

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

**4. Seed-only design surfaces are not implemented.** Components came back in
v26 on real state only (§9.2): where SimCore has no API it says "you confirmed"
or Unknown. The prototype's CLI items, scoping wizard, Tenant validation sheets,
evidence collection and planes × doors matrix have no SimCore data behind
them. Rather than render seed numbers as facts, the minimal pass removed them.
Each can come back when an endpoint exists to back it. (The run topology was on
this list; it came back on live run records, see the Minimal pass section.)

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
| `app/__tests__/everyDestinationRenders.test.jsx` | the rail drifting from the three journeys; one page's markup leaking into another's |
| `app/__tests__/mastheadEyebrow.test.jsx` | the retired phase vocabulary reappearing on a page |
| `console/__tests__/navTaskRail.test.js` | an off-rail page whose parent is not on the rail; a tab pointing at nothing; an e2e route id that no longer resolves |
| `console/povdata/__tests__/povdata.test.js` | `povdata/` growing record-shaped (seed) exports again |
| `components/__tests__/setupProgress.test.js` · `GetStartedView.test.jsx` | a setup step marked done without real state, a confirmed step presented as verified, a failed run counted as done |

<!-- Received from the design track (Cowork) on 2026-11-13 and committed
     unchanged below the line. It travels design → code; the other files in
     this folder travel code → design. The prototype snapshot it names
     ("POVengine-Console.v26.dc.html") is not committed: read it from the
     canvas https://claude.ai/artifact/UtUX2baFiCdFAsrkQi5tcb (page
     "POVengine Console"). Implementation status: docs/design/DESIGN-SYNC.md. -->

# Handoff Spec: POVengine Console — design v26 → `ui/src`

| | |
|---|---|
| Date | 2026-11-13 |
| Design source | Claude Design canvas "Cortex POV Engine UI Redesign", Version 26 (`claude.ai/code/artifact/e1d6a26d-95af-4eed-ab44-c6ec5fb7cb0c`) |
| Source snapshot in this folder | `POVengine-Console.v26.dc.html` (markup + logic of the prototype; read it, do not ship it) |
| Target | `ui/src` on `dev` (release 1.2.0), React 18 + plain CSS, tokens in `styles/` |
| Supersedes | The 2026-09-14 design sync in `docs/design/DESIGN-SYNC.md`. The design track is no longer behind the code: v26 was rebuilt from `dev` and then moved past it. |
| Author | Design track (Cowork). Not visually verified in a browser — logic was smoke-tested only. |

## 0. Read this first

1. **The prototype's numbers are samples.** Every tenant name, host, run id, count, uptime and status in the canvas is illustrative. The minimal pass removed seed data from the console and that rule stands: port structure, states and copy, never values. Anything the console cannot read from SimCore is shown as "you confirmed" or "Unknown", never as verified.
2. **Repo rules are unchanged.** Never commit to `main`. POVengine never writes to Cortex or the firewall. "Authored is not proven"; tenant-verified stays 0 until a run is read back.
3. **Three decisions in this design change rules already written in the repo.** They are listed in §9 with the owner's direction. Do not silently keep the old rule or silently adopt the new one — implement as §9 says and update `DESIGN-SYNC.md` in the same PR.
4. **Not in scope:** run plans (concurrent / scheduled / orchestrated runs). Nothing in v26 designs them. Per-agent queueing stays as it is.

## 1. Overview

The console is reorganised around the three jobs a Domain Consultant does, in order. The rail is those three jobs; every page belongs to exactly one.

```
1  Onboard & connect      Get started · Components ▸ (8 components, expandable)
2  Compose & simulate     Scenarios · Build a chain · Launch gate
3  Run & observe          Runs · Results
   Reference              Catalog (TTP cards · Packages · Data streams · UC/TC index)
```

What is new relative to `dev`:

| Area | `dev` today | v26 |
|---|---|---|
| Rail | Get started / Simulate / Runs / Results / Catalog + Manage (Agents, Tenant, Firewall) | Three numbered journeys + Reference. Simulate's tabs become rail items. Manage becomes **Components**, an expandable submenu of all eight components with a status dot each. |
| Components | Agents, Tenant, Firewall pages; collector and Broker VM only as Get started steps | One page shape for all eight: POVengine, Agents, Tenant, HTTP collector, Broker VM, Firewall, XDR Collector, Cortex Engine |
| Setup | Per-component, different shapes | One **Configure** checklist frame for every component (§4.3) |
| Health | Mixed | Service rows with status + uptime; each row drills into the step that fixes it (§4.2) |
| Runs → Results | Hard to tell what ran and whether results exist | Run lifecycle strip and an explicit read-back step (§4.6) |
| Scenario detail | Text | Leads with the chain graph, same stage as the Composer (§4.4) |
| Header | Evidence pill removed | Instance status (this POVengine's uptime + live KPIs), Running pill only while a run is live, ⌘K search |

## 2. Layout

Desktop console, designed at 1440 wide; must hold at 1280. No mobile target.

- Shell: fixed header (48px), left rail (232px), content column max-width 1180px with 28px side padding.
- Page header: icon tile + title (one size, §3), one-line lede, primary actions right-aligned on the same row.
- Cards: `--s1` surface, 1px `--bd` border, radius 10px, padding 14–16px. Lists inside a card separate rows with a 1px top border, not gaps.
- Component page: header → tab strip (Overview · Setup · one management tab) → two-column body on Overview (Services left, Configuration right), stacking below 1100px.
- Configure: step list (260px) left, current step right.
- Deep-dive sheet: right-side panel, 560px, over a scrim. Search palette: centred, 520px, 90px from top.

## 3. Design tokens

Map to existing variables in `styles/cortex-tokens.css`; do not add hex values in components.

| Token | Value (dark) | Usage |
|---|---|---|
| `--ac` / `--br` | `#00CC66` | Primary action, selected state, brand. See §9.1 |
| `--ac-tx` | `#0B1F14` | Text on a primary button |
| `--ac-soft` / `--ac-line` | `rgba(0,204,102,.12)` / `.42` | Selected fill / selected border |
| `--pos` (+ `-soft`) | `#00CC66` | Up, done, detected |
| `--warn` | `#FFCB06` (soft `rgba(255,203,6,.13)`) | Degraded, changed. The only yellow |
| `--crit` (+ `-soft`) | `#FDAC96` / `rgba(253,172,150,.12)` | Down, missing, broken, destructive confirm |
| `--info` | `#00C0E8` | Informational rows only |
| `--s0…--s3` | `#000` `#141414` `#1F1F1F` `#2B2B2B` | Page, card, raised/hover, chip |
| `--bd` / `--bd2` | `#333` / `#4A4A4A` | Border / hover border |
| `--tx` / `--tx2` / `--tx3` | `#FFF` / `#C7C7C7` / `#9A9A9A` | Primary / secondary / tertiary text |
| Title | Montserrat 700 26/1.2 | Every page `h1` (run name on Run detail: 23px) |
| Body | Helvetica Neue 400–600, 11.5–13px | Everything else |
| Mono | JetBrains Mono | Ids, commands, counts, lane labels |

No orange outlines or accents anywhere. Orange survives only as the NGFW family tint (`#FA582D`) on firewall-sourced cards.

## 4. Components

### 4.1 Shared constructs (build these once)

| Component | Variants | Notes |
|---|---|---|
| **StatusIcon** | Up `✓` · Degraded `!` · Down `✕` · Off `–` · Unknown `?` | 16px (rows) / 20px (list) circle, soft fill + glyph. Always icon + one word; never colour alone. `role="img"` with the word as `aria-label`. |
| **ErrorBadge** | warn · crit | `!` + short error name (max ~24 chars). Is a button; opens Configure at the fixing step. |
| **Button** | primary (filled `--ac`) · secondary (1px `--bd2`) · destructive (filled `--crit`) | One primary per view. Fixed verb set, §5. |
| **PageIcon** | 30px tile / 14–16px inline | The rail's icon set, reused on page titles, component rows, Get started steps, sheet headers and search results. Same neutral-line treatment as `.pov-rail__icon`. Two icons are currently shared by two destinations each (Firewall/Scenarios, POVengine/Components) — add dedicated ones if the DS has them. |
| **ChainStage** | editable (Composer) · live (Run detail) · preview (Scenario sheet) | One component, three modes. Lanes, cards and edges come from `composerLanes.js` / `composerLayout.js` / `composerGraph.js`. Live adds a state pip + word per card; preview has no state and is scaled to fit the sheet. This replaces `RunTopologyView`'s separate drawing. |
| **ScenarioCard / TtpCard** | — | Same anatomy: header (id, technique), title, tag row, footer. Body opens the deep-dive sheet; footer carries the verb — Scenario: **Run** + **Customize**; TTP card: **Add to chain**. |
| **Toast** | — | Bottom-centre, `role="status"`, 2.4s. Every action that completes in place confirms through it. |
| **Sheet** | per object kind | Kind icon + kind label + id, name, status, tab strip, action row (max two actions, first is primary). |

### 4.2 Component page (all eight)

| Block | Content | Behaviour |
|---|---|---|
| Header | PageIcon, name, StatusIcon + word, **Configure**, **Test** | Test shows "Testing…" then "Passed" or the failure |
| Issue line | One sentence, only when something is wrong | — |
| Services | One row per service: StatusIcon, name, since/uptime, drill-in label | Whole row is a button. Up → "View ›" (where it is configured). Degraded/Down → "Troubleshoot ›" (the Setup step that fixes it). |
| Configuration | One row per required item: OK · Missing · Changed · Can't check | Failing items sort first. Row opens the fixing step ("Fix ›") or the page that shows it ("View ›"). |
| Tabs | Overview · Setup · one management tab | Management tab per component: POVengine → **API**, Agents → **Fleet**, Tenant → **Read-back & integrations**, HTTP collector → **Collector targets**, Firewall → **Sources**. The others have none. |

**No polling history.** Overview shows current status and uptime of named services and current configuration errors. Do not add poll timelines, "last polled" strips or sparkline history.

Service and configuration rows per component, and the step each one drills into, are the `SERVICES`, `MANAGE`, `SERVICE_TARGET` and `CONFIG_TARGET` tables in the source snapshot. **The drill-in mapping is the design track's reading — verify each against the real step before shipping.**

### 4.3 Configure (one frame, every component)

A checklist. Each step: title, one-paragraph why, where it happens (product path), the fields to set, an optional command (copyable), and who can verify it.

| Verifier | Shown as | Test button |
|---|---|---|
| `pov` | "POVengine tests this step." | Yes |
| `cortex` | "Cortex shows the result — Test reads it back." | Yes |
| `you` | "POVengine cannot see this step — it takes your word." | No; step is ticked by the user and reads "you confirmed" |

Steps (titles are final copy; order is product order):

| Component | Steps |
|---|---|
| POVengine | Prepare the host · Set the master key · Name the host · Start POVengine · Check health |
| Agents | Create an enrollment token · Check the host can reach POVengine · *then per OS (Linux / macOS / Windows):* Run the installer · Confirm the service · Check the service accounts (Windows: Know the identity limit) · Confirm it is online |
| Tenant | Create a read-only API key in Cortex · Add it to POVengine · Test the connection · Check datasets and clock |
| HTTP collector | Add the collector in Cortex · Choose format and dataset · Generate the key · Add it to POVengine · Send a test event · Confirm it arrived |
| Broker VM | Download the image · Deploy and set the network · Open the web console · Register with a token · Activate the Syslog Collector · Check agents can reach it |
| Firewall | Register read-only · Create the POV test rule · *then per path:* **Broker VM (syslog, CEF):** Set the syslog host name format · Add a syslog server profile · Forward the rule's logs · Match the broker's format · Confirm logs arrive — **Strata Logging Service:** Add the firewall in Cortex · Turn on cloud logging · Install the certificate · Forward the rule's logs · Confirm logs arrive |
| XDR Collector | Create the installer · Install on the host · Create a collector profile · Apply it with a policy · Confirm data arrives |
| Cortex Engine | Create the engine in Cortex · Install it on a host · Run the integrations on it |

Full step copy, fields, commands and the per-step detail panels (`STEP_INFO`, including the jumpbox host-permission commands) are in the snapshot's `SETUP` and `STEP_INFO` tables. **Lower confidence:** XDR Collector and Cortex Engine steps (installer docs could not be fetched), and the host-permission commands (generic Linux practice). Check both against product docs and `deployCommands.js`.

Existing code to fold in rather than duplicate: `GetStartedView.jsx` + `setupProgress.js`, `deployCommands.js`, `DeployAgentKubernetes.jsx`, `firewall/CheckStep.jsx`, `CollectorTargetsPanel.jsx`, `TenantManager.jsx`.

### 4.4 Compose & simulate

| Surface | Spec |
|---|---|
| **Scenarios** | Card grid with filters: plane, component/source, use case / test case, status. Card footer: **Run** (→ Launch gate with the scenario loaded) and **Customize** (→ Build a chain with the scenario's chain loaded). |
| **Scenario sheet** | Opens with ChainStage in preview mode, then tabs. Same two actions as the card. |
| **Build a chain** | Existing Composer. Palette tabs: Scenarios · TTP cards · Data streams · Packages. "Add to chain" from any card or sheet anywhere in the console drops the item into the chain tray and confirms by toast. |
| **Launch gate** | Unchanged in purpose. The canned reach block removed in the minimal pass stays removed. |
| **Catalog** | Reference only: what scenarios are made of. Every item still offers "Add to chain", so Catalog is never a dead end. |

Catalog vs Scenarios vs Build a chain, in one line each: Scenarios are things you can run now; Build a chain is where you assemble or alter one; Catalog is the parts list.

### 4.5 Header

| Element | Spec |
|---|---|
| Instance status | StatusIcon + word + uptime of **this POVengine instance**, then up to four live KPIs. Not POV-specific. Opens the POVengine component page. |
| Running pill | Rendered only while a run is live. Opens that run's detail. |
| Search (⌘K) | Extend `CommandPalette.jsx`: pages, components, scenarios, TTP cards; each result shows PageIcon, label, kind. |

### 4.6 Run & observe

Run lifecycle, shown as a five-stage strip on each run: **Launched → Running → Finished / Failed / Aborted → Read back → Results.**

| Phase | Row action | Meaning |
|---|---|---|
| running | Open (live detail) | Steps report as they execute |
| nothing | — | Ended before any step ran |
| unread | **Read back** (primary) | Finished; tenant not yet queried. No detection claims shown — steps say only "Ran" / "Did not run" |
| reading | "Reading…" disabled | — |
| ready | **Open results** | Read back; detection states are real |

- Run detail draws the chain with ChainStage (live). Card states: Detected · Broken · Waiting / Not seen · Ran — not read back · Did not run. A lane header opens its component page.
- A **Broken** step opens a troubleshooting sheet: likely causes in the order to check, each line opening the place that fixes it.
- **Abort run:** button → inline confirm ("Stop this run on the agent?" · Keep running · Abort now). On confirm the run reads Aborted everywhere and the header pill disappears. Wire to the existing abort path (PR #240) and `ConfirmDialog.jsx`.
- **Results** always opens with a "Result of" bar naming the run it belongs to, with a switcher across read-back runs. Results never appear without a run.

## 5. States and interactions

**Verbs — closed set.** Configure · Test · Run · Customize · Add to chain · Read back · Open results · Abort run. Do not ship "wizard", "probe", "setup", "enroll", "bind" as button labels.

| Element | State | Behaviour |
|---|---|---|
| Primary button | hover | `filter: brightness(1.08)` |
| Secondary button / card | hover | border `--bd` → `--bd2` |
| Row button | hover | background `--s2` |
| Test | in progress / done | "Testing…" (disabled) → "Passed", or ErrorBadge with the failure |
| Read back | in progress | "Reading…" disabled; row keeps its place |
| Rail › Components | collapsed / expanded | Chevron toggles; children show a status dot; group shows "N issues" when any component has one |
| Tab | selected | `--ac-soft` fill, `--ac-line` border (see §9.1) |
| Any redirect of a retired route | — | `setup`, `scope` → Get started; `validation` → Results; `tenants` / `agents` / `firewall` → the matching component page |

## 6. Responsive behaviour

| Width | Changes |
|---|---|
| ≥1280 | Default |
| 1100–1279 | Component Overview columns stack; scenario grid drops to two columns |
| <1100 | Not a target. Rail may collapse to icons; nothing else is designed |

ChainStage scrolls horizontally inside its own box at every width; it never scales below 0.62.

## 7. Edge cases

- **Empty:** Component never configured → status Off, one line "Configure it to unlock its scenarios.", Configure as the only primary. No runs → Runs shows a single line and a Run link to Scenarios. No read-back runs → Results explains that a run must be read back first and links to Runs.
- **Cannot check:** any item SimCore has no API for renders Unknown / "Can't check" in `--tx3`, never green.
- **Long text:** component summaries and card subtitles truncate to one line with ellipsis; scenario titles wrap to two lines (reserve 36px); ids never truncate.
- **Loading:** keep `DestinationLoading`; rows keep their height while a Test or Read back is in flight.
- **Errors:** route through the existing `SurfaceNotice`; in-row failures use ErrorBadge.
- **Scenario without an authored chain:** the prototype draws a generic chain from step count and source lane. In code, draw the scenario's real steps; if a scenario truly has no chain data, show the stage's empty state rather than a synthetic one.

## 8. Motion and accessibility

Motion: none beyond hover transitions (≤120ms, ease-out) and the toast (fade 150ms). Respect `prefers-reduced-motion`.

- Every interactive row is a real `<button>`; no click handlers on `div`.
- Status is never colour-only: icon + word, word exposed as `aria-label`.
- Tabs use `aria-pressed` / `aria-current="step"` as in the prototype; Configure's step list is an ordered list.
- Toast is `role="status"`, `aria-live="polite"`; the abort confirm and search palette trap focus and close on Esc; search is `role="dialog"` with a labelled input.
- Keep ⌘K, ⌘L, ⌘E, ⌘/.
- Contrast: `--tx3` on `--s1` is the floor for text; do not use `--bd2` for text.

## 9. Decisions that change rules already in the repo

| # | Repo today | v26 (owner's direction) | Do |
|---|---|---|---|
| 9.1 | `povengine-minimal.css`: orange = the single primary action; "selected" reads as brightness, not hue | Cortex green is the primary action and selected colour; no orange accents; yellow only for warnings | Switch the primary-action token to green and update the colour rule comment + `DESIGN-SYNC.md`. Because green now means both "primary" and "healthy", health must always carry its icon and word. Exact green is open (§10.2). |
| 9.2 | Minimal pass retired the Components page (it was seed data) | Components returns as the hub for eight components | Rebuild it on real state only. Where SimCore has no API (Broker VM, XDR Collector, Cortex Engine, parts of HTTP collector), show "you confirmed" / Unknown. Do not reintroduce seed rows. |
| 9.3 | Minimal pass removed the run-detail topology tab | Run detail leads with the chain stage | Bring it back only as ChainStage fed by the run's real steps and read-back state. |

## 10. Open items (not blocking; do not guess)

1. **Run plans** — concurrent / scheduled / orchestrated runs. Not designed. Out of scope.
2. **Which green** — `#00CC66` (used here, already the token) or the logo's `#41B864`. Keep `#00CC66` unless told otherwise.
3. **Icons** — two shared icons (§4.1).
4. **UC/TC ids on scenarios** in the prototype are placeholders; bind to the real index.
5. **API tab** — the prototype groups 156 routes into 22 groups. The grouping is the design track's; generate it from the router instead.
6. **"Apply for me"** — the design never has POVengine change Cortex or firewall config. Steps marked `pov` on the firewall mean *check*, not *write*. Keep it that way.

## 11. Suggested build order

Each row is one reviewable PR; earlier rows unblock later ones.

| # | Slice | Touches |
|---|---|---|
| 1 | Tokens + colour rule (§9.1), StatusIcon, ErrorBadge, Button verbs, Toast, PageIcon | `styles/`, shared components |
| 2 | Rail: three journeys, Components submenu, redirects | `ConsoleRail.jsx`, `destinations.jsx`, `useConsoleRouter.js` |
| 3 | Component page shape + Configure frame; migrate Agents, Tenant, Firewall | `TenantView`, `FirewallView`, Agents, new shared frame |
| 4 | Remaining components: POVengine (+ API tab), HTTP collector, Broker VM, XDR Collector, Cortex Engine; Get started points at Configure | `GetStartedView`, `setupProgress.js`, `CollectorTargetsPanel` |
| 5 | ChainStage extraction (editable / live / preview); Scenario sheet preview | `Composer*`, `RunTopologyView`, `ScenarioInspector` |
| 6 | Scenario / TTP card anatomy + filters + "Add to chain" everywhere | `ScenarioGrid`, `TtpBrowserView`, `ComposerPalette` |
| 7 | Run lifecycle strip, Read back, Results "Result of" bar, troubleshooting sheet, abort confirm | `RunList`, `RunDetailView`, `EvidenceView`, `RunCasePanel` |
| 8 | Header instance status, Running pill, search results with icons | `ConsoleHeader`, `CommandPalette` |

Definition of done for the whole handoff: no button without an action; every status has icon + word; no seed values on screen; `povdata.test.js` and the DS drift guard pass; `DESIGN-SYNC.md` sync manifest updated to this handoff.

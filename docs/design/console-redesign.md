# CortexSim Console — Frontend Redesign Direction

Author: DC2 GTM NAM Cortex · Status: **direction** (awaiting review)
Starting point: Enclave (`~/Github/Github_desktop/local-ai-platform`)

## Who this is for

Palo Alto Networks **Domain Consultants running POVs in customer labs.**
They are:

- Operators, not casual users. They live in terminals, SOC consoles, and IaC.
- Working against a clock — the POV timeline is short and the demo has to land.
- Often in low-light customer rooms or at odd hours. Light-themed business UI
  is the wrong register.
- Telling a *story* — the POV isn't "we detected X," it's "the attacker did
  A → B → C and Cortex saw the whole story." The UI has to reinforce that.

## What's wrong with today's UI (honest inventory)

| # | Issue                                                              | Impact                                                                 |
|---|--------------------------------------------------------------------|------------------------------------------------------------------------|
| 1 | Light theme (`#F4F6F8` canvas)                                      | Clashes with SOC-console mental model; reads as "business app"         |
| 2 | Three-mode toggle (MITRE / Deploy / Runs) hides in-flight state    | DC loses visibility into the live run while exploring other views       |
| 3 | Vertically stacked scenario detail → launch                        | Critical CTA falls below the fold once scenario metadata expands        |
| 4 | Custom invented CortexLogo SVG                                      | Not PANW-brand-correct; feels hobbyist                                  |
| 5 | Results validation hidden behind a mode                             | The detection-validation flow is the POV's deliverable — should be central|
| 6 | MITRE heatmap is view-only                                          | Missed opportunity — it should be a filter surface for scenarios        |
| 7 | No attack-timeline visual                                           | The "5 alerts → 1 incident" XSIAM narrative has no visual representation|
| 8 | No always-visible run telemetry                                    | DC can't see "what is executing right now" without clicking Runs        |
| 9 | Generic Inter font                                                  | Does not differentiate Cortex from any other enterprise SaaS            |

## Aesthetic direction — "Mission Ops Console"

Enclave's terminal-adjacent infra-tool sensibility, pushed toward the
formality of a launch console. Three words: **precise · operator · narrative.**

Not a marketing page. Not a dashboard. A **console**.

### Visual DNA

- **Deep near-black navy field** — Cortex navy taken dark (`#0A1420`)
- **Thin hairlines** (`#1F2E46`) instead of cards-with-shadows. Shadows are rare
  and reserved for elevated modal surfaces.
- **Tabular numerals everywhere** a number appears (MTTD, elapsed, coverage %)
- **Monospaced labels** for identifiers — `SIM-MP-004`, `T1552.001`, `www-data`
- **Cortex teal** (`#00C0E8`) as the **active signal color** — selection, progress,
  live-state. Never decoration. Used sparingly and with intent.
- **Detection status** color-coded per plane: teal (EDR/XDR), cyan-green (CDR),
  amber (analytics-in-progress), soft green (confirmed detected), soft red (missed).
- **Subtle CRT grid overlay** with 40px spacing behind hero surfaces (from
  Enclave) — atmosphere, not chrome.
- **No rounded-pill everything.** 4px radius on chips, 2px on inputs, 6px on
  modal surfaces. Hard edges read operator; rounded reads friendly.

### Typography

Intentional choices, not generic. Three typefaces:

| Role           | Typeface             | Why                                                 |
|----------------|----------------------|-----------------------------------------------------|
| Display/hero   | **Funnel Display**   | Condensed editorial-tech; evokes mission-briefing   |
| UI / body      | **Archivo**          | Excellent at 11–14px data density; characterful     |
| Mono / labels  | **JetBrains Mono**   | Operator standard; tabular by default               |
| Narrative body | **Fraunces** (light) | When the POV story is rendered as prose, it's editorial — a deliberate typographic shift that signals "this is the document you hand the customer" |

All free on Google Fonts. `font-feature-settings: 'tnum', 'ss01'` applied to
every numeric or ID-bearing element.

### Motion

Restrained. Motion is used to express **state change**, not to delight.

- Scenario selection: drawer slides in from right (240ms, cubic-bezier ease-out)
- Detection signal arrives: the corresponding plane dot fades from ○ → ◐ → ● over 600ms
- XSIAM stitching: a hairline **physically draws** between two alert nodes on the
  narrative timeline over 800ms. This is the POV money shot.
- Step transitions: elapsed counter increments with a subtle glyph swap
- No scroll-driven parallax. No bouncing. No confetti.

### The Hero Interaction — Attack Narrative Timeline

The one thing someone will remember. A horizontal flow renders mid-run.

- Each TTP step is a node on a left→right path
- Each node shows: TID · technique · expected-plane set (small column of dots, one
  per Cortex product)
- As a detection arrives in the corresponding plane, that dot pulses teal then
  fills solid
- When XSIAM stitches two events, an animated hairline is drawn between the
  two nodes — literally materializing "5 alerts → 1 incident" on screen
- Elapsed timer and MTTD-per-step appear under each node in mono

This is also the DC's screenshot artifact — it goes straight into the POV
debrief deck with minimal editing.

## Layout system

A full-height shell with **four persistent regions**:

```
┌────────────────────────────────────────────────────────────────────┐
│ HEADER 56px                                                        │
│ Cortex wordmark | env pill | ⌘K trigger | user                     │
├────────────────────────────────────────────────────────────────────┤
│ TELEMETRY STRIP 40px  (always visible; shows live state)           │
│ SIM-MP-004 · step 3/5 · 2m:17s · 4/12 detected · [ABORT]           │
├──────────────┬─────────────────────────────────────┬───────────────┤
│ LEFT RAIL    │ WORKSPACE                           │ INSPECTOR     │
│ 240px        │ fluid                               │ 420px, slides │
│ collapsible  │                                     │ in on select  │
│              │ Operations · In-Flight · Evidence   │               │
│              │ · Lab · Coverage (tabs)             │               │
│              │                                     │               │
│              │                                     │               │
├──────────────┴─────────────────────────────────────┴───────────────┤
│ COMMAND STRIP 32px — shortcut hints + last-event ticker            │
└────────────────────────────────────────────────────────────────────┘
```

- **Header**: Cortex wordmark (real typographic treatment, not invented SVG),
  environment pill showing connected lab + sensor health, `⌘K` command-palette
  trigger, user avatar.
- **Telemetry strip**: always visible when a run is active. Disappears when
  idle. Contains active scenario chip, live step, elapsed, detection tally, and
  a destructive-styled `ABORT` button.
- **Left rail**: plane filter chips, pinned scenarios, search. Collapsible to
  56px icon rail.
- **Workspace**: five tabs — `Operations · In-Flight · Evidence · Lab · Coverage`.
  The current modal-switch design becomes proper tabs.
- **Inspector**: slides in from right when a scenario is selected. Contains
  metadata, expected-detection matrix, and the Launch CTA *pinned to the top*
  so it's never below the fold.
- **Command strip**: `⌘K search · ⌘L launch · ⌘E export · ⌘/ help` hints plus a
  ticker showing the last detection event (`▸ 12:41:07Z · T1552.001 detected · XDR BIOC`).

## Tabs — what each one does

- **Operations** — scenario browse/launch (the default landing; replaces the
  existing ScenarioBrowser + LaunchPanel stack). Grid of scenario cards.
  Selecting a card opens the Inspector drawer.
- **In-Flight** — the Attack Narrative Timeline for the active run. If no run
  is active, shows the last completed run's timeline (ready to screenshot).
- **Evidence** — detection scorecard + MTTD histogram + POV report export.
  Replaces ResultsViewer. Validation of observed detections happens here and
  has a dedicated, central surface — not a hidden mode.
- **Lab** — IaC generator (renamed from "Deploy" — clearer for DC vocabulary).
  Same InfraGenerator component, just relocated.
- **Coverage** — MITRE ATT&CK heatmap. Upgrade: clicking a technique filters
  scenarios in the Operations tab. Bidirectional.

## Command palette (⌘K)

Primary navigation. Overlays the console. Does:

- Fuzzy-search scenarios by name / scenario ID / TID / threat actor
- Jump to any tab
- `launch <scenario-id>` — one-shot launch with default identity and mode
- `export` — regenerate POV report for last run
- `cleanup` — trigger cleanup on active run

This is what makes the UI fast for DCs who know what they want. The rail and
grid are for exploration; ⌘K is for the 10th time they launch SIM-MP-004.

## Color tokens (full set)

```css
/* Base field */
--c-void:            #050A14;  /* full-bleed background */
--c-surface:         #0A1420;  /* panel / workspace */
--c-surface-raised:  #101A28;  /* cards / elevated */
--c-surface-modal:   #18243A;  /* modals / drawers */
--c-hairline:        #1F2E46;  /* dividers */
--c-hairline-strong: #2B3E5E;  /* focused divider */

/* Signal (Cortex teal — the active color) */
--c-signal:          #00C0E8;
--c-signal-soft:     rgba(0, 192, 232, 0.12);
--c-signal-glow:     rgba(0, 192, 232, 0.45);

/* Detection states */
--c-detected:        #4FD1A1;  /* confirmed */
--c-missed:          #F97066;  /* missed */
--c-pending:         #F5A524;  /* in-flight */
--c-stitched:        #00C0E8;  /* XSIAM stitch line */

/* Text */
--c-text:            #E8EEF5;
--c-text-secondary:  #8FA3BE;
--c-text-muted:      #5A6B84;
--c-text-disabled:   #3D4A60;

/* Accents (use sparingly) */
--c-panw-orange:     #FA582D;  /* PANW parent brand — reserved for PANW wordmark only */
--c-accent-violet:   #8E7CFF;  /* reserved for XSIAM-specific features */
```

Rule: if a color is not in this token list, it does not appear in the UI.

## Typography tokens

```css
--font-display:   'Funnel Display', ui-serif, Georgia, serif;
--font-ui:        'Archivo', ui-sans-serif, system-ui, sans-serif;
--font-mono:      'JetBrains Mono', ui-monospace, 'SF Mono', monospace;
--font-narrative: 'Fraunces', ui-serif, Georgia, serif;

--fs-display-xl:  48px;  /* hero narrative headline */
--fs-display-lg:  32px;
--fs-h1:          20px;
--fs-h2:          16px;
--fs-body:        13px;  /* data-dense operator UI */
--fs-body-lg:     15px;  /* narrative prose in Evidence */
--fs-label:       11px;  /* mono labels / chip text */
--fs-tiny:        10px;  /* telemetry strip numerics */

--tracking-mono:  0.04em;  /* mono always tracked */
--tracking-tight: -0.01em;
```

## Migration plan (from today's UI)

Low-risk, incremental — nothing breaks at any step.

1. **Ship tokens in parallel.** `ui/src/styles/cortex-console.css` lives
   alongside `cortex-theme.css`. Scoped under `.theme-console` class on root.
   No existing component changes.
2. **Build `AppShell` wrapper.** New component providing the 4-region layout
   under `.theme-console`. Wraps existing `App.jsx` as a content slot.
3. **Move modes → tabs.** Replace the three header buttons
   (MITRE/Deploy/Runs) with proper tabs in the workspace region. Same
   components render inside.
4. **Inspector drawer.** Extract scenario detail + launch into a right-drawer
   component with pinned CTA. Existing `LaunchPanel` + `UCTCMapper` slot in.
5. **Telemetry strip.** New component that subscribes to active run state.
   Derives everything from existing `getRuns()` endpoint + a new
   `GET /api/runs/{id}/progress` endpoint (if not already present).
6. **Attack Narrative Timeline.** New component — this is net-new visualization
   work. Uses SVG + CSS animations. Data comes from the scenario's steps +
   observed detections per step (already in the Result schema per
   `core/models.py`).
7. **Command palette.** New component at `<CommandPalette />`. Keyboard-triggered
   via `cmdk` library or hand-built.
8. **Typography cut-over.** Swap font imports in `index.html`. Review every
   component for font-family references.
9. **Deprecate light theme.** Once dark console ships, remove the old
   `cortex-theme.css` or keep it as a `.theme-light` fallback.

## What's in this package

- `console-redesign.md` — this document
- `mockup-console.html` — self-contained, openable-in-browser static mockup
  showing Operations, In-Flight, Evidence tabs, telemetry strip, Inspector
  drawer, and ⌘K palette. Not wired to an API — visual-fidelity preview.
- `cortex-console.css` — drop-in token set for the React app (copy to
  `ui/src/styles/cortex-console.css` when ready to wire up)

## Non-goals (intentional)

- **Not a marketing site.** No hero pitch, no feature grid, no pricing. Operator
  console only.
- **Not mobile-first.** DCs run this on a laptop + external monitor. Min width
  is 1280px. Below that: "open this on a workstation."
- **Not multi-tenant.** One DC, one lab at a time. No org/team switcher.
- **Not themed.** Dark only — this is an operator tool. If sunlight is an issue,
  close the blinds.

## Open questions for the DC team

1. **Cortex wordmark asset** — can we get the official SVG or does the mockup's
   typographic treatment suffice?
2. **Sensor health pill data source** — is there a `/api/health` aggregate that
   includes XDR agent heartbeat + CDR ingestion status, or does that need a new
   endpoint?
3. **Attack narrative persistence** — after a run completes, does the timeline
   need to be preserved as an artifact (PNG/SVG export) for the POV package?
4. **Command-palette scope** — should ⌘K also invoke actions on the Lab tab
   (generate bundle), or is that too broad?

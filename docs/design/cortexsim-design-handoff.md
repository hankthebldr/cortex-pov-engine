# CortexSim — Design System & Strategy Handoff

> **Audience:** a designer / Claude Design / Figma to take the redesign from
> direction to finished comps. Self-contained: everything below is real,
> sourced from the live `.theme-console` token system and the v2 redesign
> decisions (see `console-redesign-v2.md`). Hex values are production tokens.
>
> **Product in one line:** CortexSim is a detection-validation engine for Palo
> Alto Networks Domain Consultants — it fires controlled, high-fidelity attack
> signals into a customer's Cortex (XSIAM/XDR) tenant to prove detections work.
> It is an *operator console for a POV*, not a dashboard.

---

## 1 · Design principles

1. **Operator-first, not analyst-first.** The user is running a live exercise
   in front of (or for) a customer. Optimize for "what do I do next" and
   "can the room read this on a projector," not dense data exploration.
2. **The workflow IS the navigation.** A POV is linear: pick where it runs →
   pick what to run → fire → watch → prove it. Nav mirrors that exactly.
3. **One accent means "act."** Cortex green is reserved for the primary action
   / current step / launch. Teal is information. Color is a signal, not décor.
4. **Dark, calm, high-contrast.** Deep navy base; content floats on subtly
   raised surfaces. Motion is purposeful (state changes, stitch arcs), never
   ambient.
5. **Evidence is the product.** Everything funnels to an exportable POV
   artifact (coverage %, MTTD, ATT&CK Navigator layer, narrative).

---

## 2 · Information architecture

Primary navigation is a **numbered stepper** (the DC's journey). Secondary
tools live under **More ▾**.

```
① TARGETS  ›  ② LIBRARY  ›  ③ LAUNCH  ›  ④ LIVE  ›  ⑤ EVIDENCE      More ▾
```

| Step | Purpose | Primary objects |
|---|---|---|
| **① Targets** | Choose *where* the simulation runs | pull agents · push bundle · IaC lab environments |
| **② Library** | Browse / filter the 58-scenario catalog; arm one | scenario cards (plane, MITRE, detections) |
| **③ Launch** | Arm scenario × target → fire | mode (auto from target), identity, launch CTA |
| **④ Live** | Watch the attack unfold | attack-narrative timeline, telemetry, detections firing |
| **⑤ Evidence** | Validate detections, export POV | scorecard, MTTD, coverage %, report bundle |
| **More ▾** | Secondary surfaces | ATT&CK Coverage · Environments (IaC) · TTP authoring · Adapter registry · Competitive · PANW Stack |

**Persistent left rail (collapsible):** plane filter, pinned scenarios, quick
target switch. Collapses to a 56px icon strip; expanded is 240px.

**Global chrome:** top header (brand, environment/health pills, ⌘K command
palette, theme/theater toggle); bottom command strip (live ticker, key hints).
A **telemetry strip** appears between header and workspace only while a run is
active.

---

## 3 · Design tokens

### 3.1 Color

**Base / surfaces (dark navy field)**
| Token | Hex | Use |
|---|---|---|
| `--c-void` | `#050A14` | app background, button text on accent |
| `--c-surface` | `#0A1420` | header, base panels |
| `--c-surface-raised` | `#101A28` | cards, raised panels |
| `--c-surface-modal` | `#18243A` | modals, palettes, toasts |
| `--c-hairline` | `#1F2E46` | default borders |
| `--c-hairline-strong` | `#2B3E5E` | emphasized borders, dividers |

**Action — Cortex green (PRIMARY accent)**
| Token | Hex | Use |
|---|---|---|
| `--c-action` | `#6CC24A` | primary buttons, current step, brand mark, launch |
| `--c-action-bright` | `#84D85F` | hover, glow |
| `--c-action-deep` | `#4FA838` | pressed, deep gradient stop |
| `--c-action-soft` | `rgba(108,194,74,.14)` | tints, selected-row fill |
| `--c-action-glow` | `rgba(108,194,74,.45)` | focus glow / shadow |

> ⚠️ **Exact brand hex pending.** `#6CC24A` is a strong working Cortex green;
> confirm against the official Palo Alto Cortex palette and replace the five
> `--c-action*` tokens if it differs. Nothing else needs to change.

**Signal — Cortex teal (SECONDARY / info)**
| Token | Hex | Use |
|---|---|---|
| `--c-signal` | `#00C0E8` | links, info, stitch lines, secondary highlight |
| `--c-signal-soft` | `rgba(0,192,232,.12)` | info tints |
| `--c-signal-glow` | `rgba(0,192,232,.45)` | stitch glow |

**Detection states**
| Token | Hex | Meaning |
|---|---|---|
| `--c-detected` | `#4FD1A1` | detection fired / observed |
| `--c-missed` | `#F97066` | expected but not detected |
| `--c-pending` | `#F5A524` | in-flight / awaiting validation |

**Text**
| Token | Hex |
|---|---|
| `--c-text` | `#E8EEF5` |
| `--c-text-secondary` | `#8FA3BE` |
| `--c-text-muted` | `#5A6B84` |
| `--c-text-disabled` | `#3D4A60` |

**Brand accents (sparingly):** `--c-panw-orange #FA582D`, `--c-accent-violet #8E7CFF`.

### 3.2 Typography

| Role | Family | Token |
|---|---|---|
| Display / hero | **Funnel Display** (fallback serif) | `--font-display` |
| UI / body | **Archivo** (fallback system-ui) | `--font-ui` |
| Mono / data / IDs | **JetBrains Mono** | `--font-mono` |
| Narrative / report prose | **Fraunces** (serif) | `--font-narrative` |

Scale: display-xl 48 · display-lg 32 · h1 20 · h2 16 · body-lg 15 · body 13 ·
label 11 · tiny 10. Line-height: tight 1.2 · body 1.5 · narrative 1.6.
Tracking: mono `0.04em` (uppercase labels), tight `-0.01em` (headings).
Numerics use `font-feature-settings: 'tnum'` (tabular) for aligned metrics.

### 3.3 Spacing, radius, elevation, motion

- **Spacing scale (px):** 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 (`--s-1`…`--s-12`).
- **Radius:** chip 4 · input 2 · modal 6. (Sharp, instrument-like — not rounded/friendly.)
- **Elevation:** one modal shadow `0 16px 48px -8px rgba(0,0,0,.6)` + hairline ring;
  accent glow `0 0 24px -4px <accent-glow>` for focus/active only.
- **Motion:** ease-out `cubic-bezier(.22,1,.36,1)`, ease-in-out `cubic-bezier(.65,0,.35,1)`;
  durations fast 120ms · base 240ms · stitch 800ms. Respect `prefers-reduced-motion`.

### 3.4 Layout dimensions

| Token | Value | Region |
|---|---|---|
| `--h-header` | 56px | top header |
| `--h-telemetry` | 40px | active-run strip |
| `--h-commandstrip` | 32px | bottom strip |
| `--w-rail` | 240px | left rail (expanded) |
| `--w-rail-collapsed` | 56px | left rail (collapsed) |
| `--w-inspector` | 420px | scenario inspector drawer (OVERLAY, not in-flow) |

---

## 4 · Layout system

**Fixed-viewport shell** (no full-page scroll — only the active view scrolls).

```
┌────────────────────────────────────────────────────────────┐  ← 100dvh, overflow hidden
│ HEADER  (56px fixed)                                         │
├────────────────────────────────────────────────────────────┤
│ TELEMETRY STRIP (40px, only when a run is active)            │
├──────────┬─────────────────────────────────────────────────┤
│  RAIL    │  STEPPER (numbered nav)                          │
│ 240/56px │ ┌─────────────────────────────────────────────┐ │
│ (collap- │ │  VIEW  — the only internally-scrolling region │ │  ← overflow-y:auto
│  sible)  │ │        (min-height:0 so it bounds correctly)  │ │
│          │ └─────────────────────────────────────────────┘ │
├──────────┴─────────────────────────────────────────────────┤
│ COMMAND STRIP (32px fixed): live ticker · ⌘K · hints        │
└────────────────────────────────────────────────────────────┘
```

CSS contract (already implemented):
- Shell: `display:grid; grid-template-rows: header / [telemetry] / 1fr / commandstrip; height:100dvh; overflow:hidden`.
- Workspace: `display:grid; grid-template-columns: var(--w-rail) 1fr; min-height:0`.
- View: `overflow-y:auto; min-height:0` (the bounded scroll region).
- **Empty-state rule:** when a filtered grid is short, the view must fill its
  height gracefully (content top-aligned on a bounded surface) — never leave a
  raw "unbound" gap. Use `align-content:start` + a full-height view background.
- **Inspector drawer is an overlay** (`position:fixed/absolute`, right-anchored,
  420px) — it must NOT take layout width (that caused ~396px horizontal overflow).

**Responsive breakpoints**
- ≥1600px: scenario grid 4–6 cols; rail expanded.
- 1200–1600px: 3 cols.
- 1000–1200px: 2 cols; header pills may wrap; rail can auto-collapse.
- <1000px (not a primary target — DC laptops are ≥1280): rail collapses, stepper
  becomes scrollable horizontally, single-col grid.

---

## 5 · Component specifications

### 5.1 Stepper (primary nav)
- Numbered chips `① label`, connected by `›` separators; **current step** = green
  fill on the number + green label + subtle underline; **completed** steps =
  muted-green number; **future** = muted. Badges: live "LIVE" pill (teal,
  pulsing), counts (e.g. Library "58"). Right-aligned `More ▾` dropdown.
- Height ~44px, sits at top of the view column (inside `.main`, above `.view`).

### 5.2 Collapsible rail
- Header row with a `◀ / ▶` toggle (persist state in `localStorage`).
- Expanded (240): section titles ("Detection Planes", "Pinned"), plane rows
  `CODE · name · count`, active row = green left-border + `--c-action-soft` fill.
- Collapsed (56): icon/code-only; tooltip on hover; counts as superscript dot.
- Transition width 240↔56 at `--dur-base`.

### 5.3 Targets cards (① Targets) — 3 columns
- **Pull agent:** status dot (live = green pulse / stale = amber / unknown =
  grey), mono id, host·os, `live|stale` pill. Selected = green ring + "✓ selected
  · pull mode".
- **Push bundle:** always-ready card, teal dot, "Offline bundle", explainer.
  Selected = "✓ selected · push mode".
- **IaC lab:** provisioned environment id + modules; empty state has a green
  "Provision environment ▸" CTA → Environments.
- Empty agent state shows the literal `cortexsim-agent --server <url> --id …`
  command so the DC knows how to register one.

### 5.4 Scenario card (② Library)
- Header: mono `SIM-XXX-000` (teal) + pin toggle. Title (h2). Plane chip +
  detection-type chips (Analytics/BIOC/IOC). Footer: MITRE technique, "Anchor:
  Unit42", run-count + last-run, and a row of plane-dots (detection coverage
  preview). Hover = raise + green hairline. Click = open inspector / arm.
- Grid: `repeat(auto-fill, minmax(320px, 1fr))`, gap 16, `align-content:start`.

### 5.5 Launch panel (③ Launch) — 2 columns
- Left: "Armed scenario" summary card (name, id·plane·technique, description,
  "Change scenario"). Right: "Target" (selected target + auto mode pill) +
  identity `<select>` + (pull: beacon id / push: bundle-format `<select>`) +
  big green **"Launch run ▸"** (and for push, a secondary "↓ Download bundle").
  Result banner (success green / error red) below.
- Gate states: no scenario → "② No scenario armed" with "Go to Library ▸"; no
  target → inline "Pick a target ▸".

### 5.6 Buttons / chips / pills
- `.btn`: 32px, mono uppercase 11px, hairline border, transparent; hover → green
  border+text. `.btn--primary`: green fill, void text, weight 600; hover bright +
  glow. `.btn--danger`: red on hover. `.btn--lg`: 40px. `.btn--xs`: 24px.
- Chips: 4px radius, soft-tint bg per semantic (signal/detected/missed/pending).
- Status dots: 8px; live = green + pulse, stale = amber, ready = teal, unknown = grey.

### 5.7 Attack-narrative timeline (④ Live) — the hero
- Vertical/horizontal step nodes connected by animated **stitch arcs** (teal,
  800ms draw) showing cross-plane correlation. Node states: pending (amber
  pulse) → fired (green, glow) → missed (red). Theater mode enlarges nodes and
  hides synthetic-flag chrome for projector readability.

### 5.8 Evidence scorecard (⑤ Evidence)
- Per-detection rows grouped by step: plane chip, description, ✅/❌ state, MTTD.
- Top: coverage % ring (green fill), MTTD avg/min/max, by-type bars. Export
  menu → markdown report · matrix CSV · Navigator layer · full bundle (.tar.gz).

---

## 6 · States & interaction patterns

- **Loading:** skeleton/ghost cards ("polling beacons…"), never spinners-only.
- **Empty:** every empty state names the next action with a CTA (see Targets).
- **Selected/armed:** green ring + soft-green fill + explicit "✓ selected" text.
- **Live run:** telemetry strip appears; Live step gets pulsing "LIVE" badge;
  ticker animates in command strip.
- **Command palette (⌘K):** fuzzy scenario search + jump-to-step + actions.
  Filter palette (⌘F), quick-launch (⌘L), export (⌘E), help (⌘/).
- **Toasts:** bottom-right, mono uppercase, semantic color, 3–4s.

---

## 7 · Accessibility

- Skip link to workspace; ARIA landmarks; `role=tablist/tab` on stepper.
- `aria-live` on telemetry + ticker; `role=progressbar` with `aria-valuenow` on
  coverage. Focus-visible green outlines. `prefers-reduced-motion` disables
  stitch/pulse animations. Min contrast: body text on surface ≥ 7:1; green on
  void ≥ 4.5:1 (verify `#6CC24A` on `#050A14` ≈ 7.8:1 ✓).

---

## 8 · Cortex brand alignment

- Lead with **Cortex green**; keep the deep-navy "security ops" field.
- Wordmark: `cortex` muted + `sim` in green (`.brand__wordmark em`). PANW orange
  appears only in the corner PANW mark — never competes with green.
- Tone: precise, instrument-grade, confident. Sharp radii, tabular numerics,
  mono for all machine identifiers (IDs, hosts, techniques).

---

## 9 · Handoff deliverables requested from design

1. High-fidelity comps for the 5 steps + More surfaces at 1440 and 1920.
2. Final **Cortex green** hex + any secondary brand values → map to `--c-action*`.
3. Stepper, rail (expanded/collapsed), and Targets cards as a small component set.
4. Timeline (Live) hero treatment + theater-mode variant.
5. Empty / loading / error states for each step.
6. Icon set (line, 1.5px, matching mono weight) for planes + step numbers.

> Implementation note: this maps 1:1 onto the existing `.theme-console` token
> layer in `ui/src/styles/cortex-console.css`. A designer can change values
> there and the live app updates — the system is already token-driven.

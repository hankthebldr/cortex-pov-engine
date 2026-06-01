# CortexSim — Design Spec (evolve-v2 baseline)

> Read `00-README.md` first. Token detail lives in `../cortexsim-design-handoff.md §3`.
> This doc defines the problem, the users, the locked IA, and per-screen
> requirements with **Keep / Fix / Elevate** guidance.

---

## 1 · Users & jobs-to-be-done

**Primary user — the Domain Consultant (DC).** A pre-sales / POV engineer at
Palo Alto Networks, technical, time-boxed, often presenting live to a customer's
security team. Runs many POVs across many tenants.

Jobs, in order of a POV:
1. **"Where can I safely run this?"** — stand up or pick a target (a jumpbox
   beacon, an offline bundle, or a provisioned lab).
2. **"What should I run?"** — find the right scenario(s) for the customer's
   detections, by plane / ATT&CK technique / use case.
3. **"Fire it."** — launch against the chosen target without fumbling transport
   details.
4. **"Is it working?"** — watch the attack unfold and see detections fire in
   near-real-time, ideally on a projector.
5. **"Prove it."** — validate which detections fired, capture MTTD, and export a
   customer-ready POV report.

**Secondary viewers:** the customer's security architects in the room (read the
big picture on a screen) and the DC's own SE team (consume the exported report).

**Design implication:** optimize for *legibility of the next action* and
*demo-readability*, not dense analyst exploration.

---

## 2 · Design principles

1. **The workflow IS the navigation.** Linear POV journey → numbered stepper.
2. **One accent means "act."** Cortex green = primary action / current step /
   launch. Teal = information. Color is signal, not décor.
3. **Operator-first, projector-ready.** High contrast, calm dark field, theater
   mode for briefings.
4. **Evidence is the product.** Everything funnels to the exportable POV artifact.
5. **Honest about state.** Live/stale/empty/loading states are explicit and
   always name the next action.

---

## 3 · Information architecture (LOCKED — evolve, don't replace)

Primary nav = a numbered **stepper**; secondary tools under **More ▾**.

```
① TARGETS  ›  ② LIBRARY  ›  ③ LAUNCH  ›  ④ LIVE  ›  ⑤ EVIDENCE      More ▾
```

| Step | id (code) | Purpose |
|---|---|---|
| ① Targets | `targets` | choose *where* it runs: pull agents · push bundle · IaC labs |
| ② Library | `operations` | browse/filter the 58-scenario catalog; arm one |
| ③ Launch | `launch` | armed scenario × target → fire (mode auto-set) |
| ④ Live | `inflight` | attack-narrative timeline, detections firing |
| ⑤ Evidence | `evidence` | scorecard, MTTD, coverage %, export POV |
| More ▾ | `coverage`,`lab` | ATT&CK Coverage · Environments (IaC) |

Persistent **collapsible left rail** (240 ↔ 56px): plane filter, pinned
scenarios. Global chrome: top **header** (brand, env/health pills, ⌘K, theater
toggle), **telemetry strip** (only during a live run), bottom **command strip**
(ticker + key hints).

> **Naming fix for the designer:** the inner views still carry old headings —
> `operations` view says "Operations" (should read **Library**) and `lab` says
> "Lab" (should read **Environments**). Align inner titles to the stepper labels.

---

## 4 · Design tokens (summary — full set in `../cortexsim-design-handoff.md §3`)

- **Action / Cortex green (primary):** `--c-action #6CC24A` · bright `#84D85F` ·
  deep `#4FA838` · soft `rgba(108,194,74,.14)` · glow `rgba(108,194,74,.45)`.
- **Signal / teal (secondary, info):** `--c-signal #00C0E8`.
- **States:** detected `#4FD1A1` · missed `#F97066` · pending `#F5A524`.
- **Base (dark navy):** void `#050A14` · surface `#0A1420` · raised `#101A28` ·
  modal `#18243A` · hairline `#1F2E46` / strong `#2B3E5E`.
- **Text:** `#E8EEF5` / `#8FA3BE` / `#5A6B84` / disabled `#3D4A60`.
- **Type:** Funnel Display (display) · Archivo (UI) · JetBrains Mono (data/IDs) ·
  Fraunces (narrative). Scale 48/32/20/16/15/13/11/10. Tabular numerics.
- **Space:** 4–48 (8-step). **Radius:** chip 4 / input 2 / modal 6 (sharp,
  instrument-like). **Motion:** ease-out `cubic-bezier(.22,1,.36,1)`; 120/240/800ms.
- **Layout:** header 56 · telemetry 40 · command-strip 32 · rail 240/56 ·
  inspector 420.

---

## 5 · Per-screen requirements + Keep / Fix / Elevate

> Screenshots + component lists in `02-screen-inventory.md`; data per screen in
> `03-api-data-map.md`.

### ① Targets
- **Requirement:** present all three execution paths; selecting one sets the
  launch target and auto-derives mode. Show beacon liveness (live/stale).
- **Keep:** three-column model; explicit "✓ selected · pull/push mode"; the
  empty-agent state that shows the literal `cortexsim-agent` command.
- **Fix:** push & IaC columns feel sparse vs. the agents column — balance.
- **Elevate:** make this a confident "control room" landing; richer agent cards
  (last-seen, OS, run count), inline "register agent" affordance, lab cards with
  a topology thumbnail.

### ② Library
- **Requirement:** browse/filter 58 scenarios by plane, ATT&CK technique, use
  case, run-history; arm one for launch; pin favorites.
- **Keep:** responsive card grid; plane/tech/history filters; pin; the rich
  scenario inspector for power users.
- **Fix:** rename heading to "Library"; the inspector drawer (420px overlay) and
  the new ③ Launch step are now **redundant launch paths** — converge them
  (inspector = detail/preview, Launch step = the act).
- **Elevate:** scenario card hierarchy (technique + detection-count more
  prominent), better empty/zero-result filter states, clearer "armed" affordance
  on the card itself.

### ③ Launch
- **Requirement:** compose armed scenario × selected target → fire; mode auto-set
  from target; identity + (pull: beacon / push: format) controls; primary CTA.
- **Keep:** the two-card "Armed scenario × Target" model; gate states ("No
  scenario armed → Go to Library").
- **Fix:** large empty right-hand whitespace; the launch result/confirmation is
  thin.
- **Elevate:** a pre-flight summary ("you are about to run N steps as identity X
  against target Y"), post-launch transition straight into ④ Live.

### ④ Live
- **Requirement:** show the attack unfolding step-by-step with detections firing;
  agent stdout stream; pause/clear; theater mode.
- **Keep:** the **attack-narrative timeline** is the hero — per-step nodes with
  technique IDs and plane chips, animated stitch arcs for cross-plane
  correlation; event stream with INFO/STEP/DETECT/WARN/ERROR filters.
- **Fix:** empty/"waiting for agent" state dominates when no run is active —
  needs a stronger idle/empty design.
- **Elevate:** this is the **demo centerpiece** — make stitch arcs and
  detection-fire moments feel alive (the "wow" for the room). Bigger theater mode.

### ⑤ Evidence
- **Requirement:** per-detection scorecard, coverage %, MTTD (median/avg/min/max),
  XSIAM stitch count, pending count; validate detections; compare runs; export.
- **Keep:** the metric scorecards (coverage / MTTD / stitch / pending), THIS-RUN
  vs COMPARE-RUNS toggle, VALIDATE ALL, **Export POV Briefing** (green primary).
- **Fix:** zero-state ("no results yet — ingestion takes 30–120s") is the most
  common first view — make it informative, not barren.
- **Elevate:** the coverage % as a hero metric; per-detection drill-down; the
  exported report preview.

### More ▾ · ATT&CK Coverage
- **Requirement:** full ATT&CK matrix (tactics × techniques) with detected/
  run/not-detected coloring; sub-views: PANW Stack, Advantage, EAL Plugins, Tool
  Adapters, TTP Browser; export Navigator layer.
- **Keep:** the matrix; the sub-tabs (these surface otherwise-latent capability).
- **Fix:** the sub-tabs are buried; matrix density is high.
- **Elevate:** the PANW Stack (product × kill-chain) view is a strong
  architect-facing visual — promote it.

### More ▾ · Environments (IaC)
- **Requirement:** generate a Terraform bundle (provider + region + modules) the
  customer can apply; list prior bundles.
- **Keep:** provider tabs, module multi-select grid with dependency hints.
- **Fix:** rename heading "Lab" → "Environments"; GCP/Azure are present but not
  yet implemented (mark clearly).
- **Elevate:** connect generated environments back to ① Targets as selectable
  lab targets (close the loop).

---

## 6 · Latent capability (exists in code, weakly surfaced)

These components exist and mostly render inside Coverage sub-tabs or are unwired;
the designer should decide whether to promote them into the IA:
TTP authoring (browser + editor), Tool Adapter registry/catalog, Competitive
view, PANW Stack coverage, Multi-run compare, EAL campaign builder/console.

---

## 7 · Constraints & non-goals

- **Constraints:** React + plain CSS token system; FastAPI-served static SPA; no
  SSR; keyboard-first; projector/theater-ready; dark theme is the product
  identity (don't go light).
- **Non-goals:** CortexSim does **not** read alerts back out of Cortex (no live
  detection truth from the tenant — the DC validates manually, or a future XQL
  hook does). Don't design screens that imply a live read-back from Cortex.

---

## 8 · Success criteria for the redesign

1. A new DC can complete Targets → Library → Launch → Live → Evidence without
   instruction.
2. "Where does Launch go?" is never asked — the target model makes it obvious.
3. The Live timeline reads clearly on a projector at 3m distance.
4. Coverage % + MTTD + export read as the unambiguous "outcome" of a POV.
5. The whole app holds one viewport (no full-page scroll); only content panels
   scroll. (Already true in v2 — preserve it.)

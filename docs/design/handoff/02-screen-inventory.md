# CortexSim — Screen & State Inventory (current v2 build)

> Annotated catalogue of every reachable surface, with current-state captures in
> `screens/` (1440×900 @2×). Each entry: what it is, components, states, and the
> data it consumes (see `03-api-data-map.md` for shapes). Keep/Fix/Elevate is in
> `01-design-spec.md §5`.

## Global chrome (present on every screen)

- **Header** (`ConsoleHeader`, 56px) — brand `cortexsim` (green "sim"), "PALO
  ALTO NETWORKS" mark, environment pill (`LOCALHOST` + xdr/cdr/ndr health dots),
  ⌘K search · launch · export triggers, theater toggle.
- **Telemetry strip** (`TelemetryStrip`, 40px) — only while a run is active:
  active scenario id, STEP x/y, ELAPSED, DETECTED n/m, progress bar, ABORT.
  *(In the captures it shows a stale `SIM-EDR-001 · 0/0` from a prior run.)*
- **Stepper** (`ConsoleStepper`) — the numbered primary nav + More ▾.
- **Left rail** (`ConsoleRail`, 240/56px) — Detection Planes filter (+ counts),
  Pinned scenarios; collapse toggle `◀/▶`.
- **Command strip** (`CommandStrip`, 32px) — key hints (⌘K/⌘L/⌘E/⌘/) + live
  ticker (latest run event).
- **Overlays:** Command palette (⌘K), Filter palette (⌘F), Help (⌘/), toasts,
  confirm dialog (abort).

---

## ① Targets — `screens/01-targets.png`
- **Purpose:** choose where the simulation runs.
- **Components:** `TargetsView` → three `target-col`s (Pull agents / Push bundle /
  Lab environments), `target-card`s, `status-dot` (live/stale/ready/unknown).
- **States captured:** agents present (3 live beacons from smoke tests); push
  bundle "always ready"; labs empty → green "Provision environment ▸".
- **Other states:** no agents (shows `cortexsim-agent --server …` command);
  selected target (green ring + "✓ selected · pull/push mode").
- **Data:** `GET /api/agents`, `GET /api/infra/bundles` (10s poll).

## ② Library — `screens/02-library.png`
- **Purpose:** browse/filter/arm scenarios. *(Inner heading still says
  "Operations" — rename to "Library".)*
- **Components:** `OperationsView` → `view-head` + filter chips, `HistoryModeStrip`
  (All / Never run / Already run), `ScenarioGrid` of `scenario-card`s,
  `ScenarioInspector` (420px right overlay drawer), `FilterPalette`.
- **States:** loaded grid (58 cards, responsive 2–6 col); loading; filtered (by
  plane via rail / technique via Coverage); inspector open.
- **Data:** `GET /api/scenarios[?plane=]`, `GET /api/scenario/{id}` (on open),
  run-history rollup from `GET /api/runs`.

## ③ Launch — `screens/03-launch-gate.png`, `screens/03b-launch-armed.png`
- **Purpose:** compose armed scenario × target → fire.
- **Components:** `LaunchView` → gate state (`launch-gate`, green ② glyph + "Go to
  Library") OR armed state: "Armed scenario" card + "Target" card with mode pill,
  identity `<select>`, bundle-format `<select>` (push), `Launch run ▸` (green) +
  `Download bundle`, result banner.
- **States captured:** gate (no scenario armed); armed (SIM-AIACC-001 × Offline
  push bundle, push mode).
- **Other states:** no target selected; launching; success/error result.
- **Data:** `GET /api/scenario/{id}` (armed detail), `GET /api/agents` (pull),
  `POST /api/run`, `GET /api/scenarios/{id}/download` (push bundle).

## ④ Live — `screens/04-live.png`
- **Purpose:** watch the attack unfold; detections fire.
- **Components:** `InflightView` → `NarrativeTimeline` (per-step nodes: technique
  id + plane chips + plane-dots; animated stitch arcs), `EventStream` (AGENT
  STDOUT with INFO/STEP/DETECT/WARN/ERROR filters, PAUSE/CLEAR).
- **States captured:** scenario steps laid out, "waiting for agent…" (no live
  run). 
- **Other states:** active run (nodes animate pending→fired→missed), theater mode
  (enlarged nodes, synthetic chrome hidden).
- **Data:** `GET /api/runs`, run event stream (`useRunEventStream`),
  `GET /api/results/{runId}`.

## ⑤ Evidence — `screens/05-evidence.png`
- **Purpose:** validate detections, capture metrics, export POV.
- **Components:** `EvidenceView` → scorecards (COVERAGE % / MTTD median / XSIAM
  stitch / PENDING), THIS-RUN vs COMPARE-RUNS toggle, VALIDATE ALL, **Export POV
  Briefing** (green), `MttdHistogram`, per-detection results table (TID / plane /
  alert / MTTD / alert-id / status), `DetectionDrawer` drill-down.
- **States captured:** zero-state (0% coverage, "no results yet — ingestion takes
  30–120s").
- **Other states:** populated scorecard; 100% coverage; compare-runs.
- **Data:** `GET /api/results/{runId}`, `PUT /api/results/{id}/validate`,
  report exports (`/api/runs/{id}/report*`, `/report/bundle`).

## More ▾ · ATT&CK Coverage — `screens/06-coverage.png`
- **Purpose:** technique coverage matrix + adjacent analyses.
- **Components:** `CoverageView` → tactic columns × technique cells (colored by
  detected/run/not-detected), sub-tabs **ATT&CK · PANW Stack · Advantage · EAL
  Plugins · Tool Adapters · TTP Browser**, "Navigator layer" export, Refresh.
- **States:** matrix populated (72 techniques · 4 detected · 68 no-run in capture).
- **Data:** `GET /api/mitre/coverage` (`{techniques, by_tactic, summary}`),
  sub-tabs hit EAL/adapter/TTP endpoints.

## More ▾ · Environments (IaC) — `screens/07-environments.png`
- **Purpose:** generate Terraform target environments. *(Inner heading "Lab" →
  rename "Environments".)*
- **Components:** `LabView` → provider tabs (AWS / GCP / Azure), region select,
  module multi-select grid (11 modules: base, edr, cdr, ndr, itdr, cspm, asm,
  tim, cloud_app, content-library, telemetry-replay) with dependency hints,
  Refresh modules.
- **States:** AWS modules listed; GCP/Azure present but not implemented (mark).
- **Data:** `GET /api/infra/modules?provider=`, `POST /api/infra/generate`,
  `GET /api/infra/bundles`.

## Rail collapsed — `screens/08-rail-collapsed.png`
- 56px icon strip (plane codes only); persisted to localStorage; smooth width
  transition.

---

## Latent / unwired components (in repo, not in the primary IA)
`TtpEditorView`, `AdapterRegistryView`, `ToolAdapterCatalog`, `CompetitiveView`,
`StackCoverageView`, `MultiRunCompare`, `EalCampaignBuilder`, `EalConsole`,
`EalRunProgress`. Some render inside Coverage sub-tabs; the designer decides
whether to promote any into the stepper or More menu.

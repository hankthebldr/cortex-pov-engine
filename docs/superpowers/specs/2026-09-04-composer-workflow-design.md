# Simulation Composer — full workflow design

**Status:** design approved in brainstorm 2026-09-04, spec pending user review.
**Owner:** Henry (DC), authored by Claude.
**Scope of this spec:** make the Simulation Composer a fully functional,
end-to-end authoring **and** execution surface for POV chains — persist a
composed draft, edit every field in-product, and launch it through the real
run path — mirroring the interaction model of the `local-ai-platform` Composer
while staying inside CortexSim's no-fake-runs doctrine.

---

## 1. Why

The console can browse, launch, and prove **authored** scenarios, but the
Composer (`ui/src/components/console/ComposerView.jsx`) is half-built:

- The canvas is a static linear chain; there is no spatial graph.
- The inspector is **read-only** — a DC cannot edit a step's command,
  identity, technique, or detections in-product.
- A hand-edited or from-scratch chain **cannot be launched** — SimCore only
  executes scenarios loaded from `scenarios/` at boot, so the only output is a
  YAML file the DC drops into the tree and reloads by hand.

The `local-ai-platform` design prototype
(`docs/design/project/ui_kits/console/Composer.jsx`,
`console-v2/CanvasMode.jsx`) demonstrates the target experience: a tabbed
palette, a zoomable node/edge canvas, a fully editable inspector, per-step
test, a Design/Run lens with a scrub timeline, and run-status overlays. Its
runs are **mocked**. CortexSim will mirror the *interaction model* but drive it
with **real** run data.

## 2. The doctrine this must not break (Gate A5)

1. **Authored is not proven.** `tenant-verified` stays 0. A composed draft that
   has run produces real seeded results, but is never counted as tenant-proven.
2. **A draft is not corpus coverage.** Draft rows are excluded from
   `/api/uctc` evidence, coverage counts, and corpus totals.
3. **No invented data.** The Run lens shows real step status / MTTD from SSE and
   `env.runs`; it never fabricates throughput or timing.
4. **No write path to Cortex.** Unchanged; the Composer generates signal, it
   does not write to a tenant.

## 3. Decisions (locked in brainstorm)

| # | Decision |
|---|----------|
| D1 | **Full path:** author + persist + run, not UI-only. |
| D2 | **Canvas:** graph canvas in the reference's visual language, **spine-constrained** — edges must form a valid causality tree (one parent per step, one root), never a free-form DAG. |
| D3 | **Persistence:** DB `Scenario` rows with `status='draft'`, not disk YAML. |
| D4 | **Bottom region:** keep the honest workstream tabs (Payload / Preflight / Active / History). Drop the chat dock. |
| D5 | **Launch gate:** a draft saves and edits with no refs, but **launch is blocked until it is bound to a real `tc_ref`** resolvable in the FY27 index (plus chain validity). |

## 4. Architecture

### 4.1 Persistence substrate (backend)

Composed chains persist as `Scenario` ORM rows (`core/models.py`) with:

- `status = 'draft'` (the column already documents `active | draft | deprecated`).
- `author = <dc identifier>` (existing column).
- `tags` includes `'composer-draft'` so drafts are trivially identifiable and
  the strict boot loader (which only walks `scenarios/` on disk) never touches
  them.
- `scenario_id` generated as `SIM-DRAFT-<8-char slug>`, unique.

No new columns are required. Drafts live in `data/cortexsim.db` (a mounted
volume), survive restart, and never enter git-tracked `scenarios/`.

**Relaxed draft schema.** `ScenarioSchema` (the strict corpus loader) makes
`uc_ref`, `tc_ref`, `uc_name`, `tc_name`, MITRE tactic/technique names, and
`push/pull_supported` mandatory — a from-scratch draft has none of these. Add
`DraftScenarioSchema` in `core/engine/composer_draft_schema.py`:

- **Required:** `name`, `plane` (must be a known plane), `steps` (≥1,
  each well-formed).
- **Optional:** `uc_ref`, `tc_ref`, `tc_refs`, MITRE fields, KPI/threshold,
  `cgo_anchor`, `cleanup`, `execution_identity`.
- **Hard structural validation, reused from the loader:**
  - each step: non-empty `id` (unique), `name`, `command`; `expected_detections`
    entries carry `plane` + `type` from the six-value vocabulary.
  - the **causality spine**: reuse `_validate_causality_spine`'s rules — exactly
    one root (a step with no `causality.parent_step`), no forward/self
    references, `pivot` in the allowed set.
- Missing mandatory ORM fields are filled with explicit sentinels on persist:
  `uc_ref='UNBOUND'`, `tc_ref='UNBOUND'`, `uc_name='(draft — unbound)'`, etc.,
  and `push_supported`/`pull_supported` derived from step command text the same
  way the push generator classifies targets.

### 4.2 API surface (backend)

New router `core/api/drafts.py`, mounted at `/api/scenarios/drafts`:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/scenarios/drafts` | Validate via `DraftScenarioSchema`, persist a `status='draft'` row, return the draft document + `scenario_id`. |
| `GET` | `/api/scenarios/drafts` | List draft rows (summary shape), optionally `?author=`. |
| `GET` | `/api/scenarios/drafts/{id}` | Full draft document (same shape as `GET /api/scenarios/{id}`). |
| `PUT` | `/api/scenarios/drafts/{id}` | Replace a draft's chain (re-validate). |
| `DELETE` | `/api/scenarios/drafts/{id}` | Remove a draft row (hard delete is fine — it is the DC's own scratch work, and runs reference it by `scenario_id` string, not FK). |

**`list_scenarios` (existing) gains a status guard:** it must exclude
`status='draft'` by default so drafts never leak into the Library, UC/TC
evidence, coverage, or entitlement scoping. A `?status=` query can opt in.
`_evidence_index` in `core/api/uctc.py` and any coverage walk filter to
`status='active'`.

**Launch is unchanged.** Once a draft row exists, `POST /api/runs` with its
`scenario_id` runs through `Orchestrator.launch()` exactly as a shipped
scenario does (it selects the row from the DB, seeds results from the draft's
`expected_detections`, enqueues the step task the beacon pulls). This is the
crux: we add persistence, not a second execution engine.

**Launch gate (the D5 rule)** is enforced authoritatively in the run-launch
path (so the UI cannot be bypassed), and also exposed read-only for the UI to
pre-check via `GET /api/scenarios/drafts/{id}/launchable`. Both compute the same
structured verdict:

- `chain_valid`: every step has a command and ≥1 expected detection.
- `tc_bound`: `validate_index_refs(...)` (advisory return) yields a resolvable
  test case for the draft's `tc_ref`/`tc_refs` with no FK errors.
- Launch of a `status='draft'` row whose `tc_ref == 'UNBOUND'` (or unresolvable)
  is refused with `409 DRAFT_NOT_TC_BOUND`, naming the fix ("bind a test case
  from the UC/TC Index"). Non-draft scenarios are unaffected.

Consent gating (`_check_launch_consent`) and runtime-readiness preflight are
inherited unchanged — a draft wiring a gated adapter or a c2 framework is gated
exactly like any scenario.

### 4.3 Frontend decomposition

`ComposerView.jsx` is 938 lines and read-only; extending it in place would make
it unmaintainable. Split by responsibility (pure modules stay React-free and
unit-tested, per the `runStatus.js` / `healthModel.js` convention):

| Module | Kind | Responsibility |
|--------|------|----------------|
| `composerDraft.js` (extend) | pure | Draft model + immutable edit ops; add `editStep`, `addDetection`, `removeDetection`, `setCausalityParent`; add `draftToApi` / `draftFromApi` mapping for the new endpoints. |
| `composerLayout.js` (new) | pure | Spine → node coordinates + edge list. Deterministic top-down/left-right layout from `causality.parent_step`. |
| `ComposerCanvas.jsx` (new) | view | The zoomable graph: node cards, SVG spine edges, ports, zoom controls, selection, run-status overlay. |
| `ComposerPalette.jsx` (new) | view | Tabbed palette (§4.5). |
| `ComposerInspector.jsx` (new) | view | Editable step config + workflow meta (§4.6). |
| `ComposerView.jsx` (slim) | view | Orchestrates the above; owns draft state, save/load, launch, workstream. |

### 4.4 Canvas & the two lenses

**Design lens** — the graph canvas, in the reference's visual language:

- Node cards tinted by **plane** (CortexSim's analogue of the reference's role
  colour), a title, an id/order kicker, meta (technique · identity), detection
  chips, and a run-status pip.
- Edges are the **causality spine** drawn as SVG paths (reuse the reference's
  `edgePath` curve). START and END anchors bracket the chain.
- Layout comes from `composerLayout.js` — the DC never positions nodes by
  hand-drag into invalid topologies; they add/link/reorder and the layout
  re-derives. Adding a step from the palette links it under the current
  selection (or the last root-line step) as its `parent_step`.
- Zoom controls, node selection, keyboard reorder — mirrored from the reference.

**Run lens** — real data, not mock:

- Painted from `env.activeRun` (live SSE: `step`, `totalSteps`, `detected`,
  per-step status) and terminal runs in `env.runs`.
- A scrub timeline sized by real step boundaries; a per-node overlay shows real
  MTTD where a result was observed, and "not reached" / "no detection" honestly
  where it was not. No fabricated throughput.
- "Inspect a past run on the canvas" deep-links from the Runs view.

### 4.5 Palette taxonomy

The reference's `roles / agents / skills / plugins / mcps`, mapped to real
CortexSim concepts, every group sourced from the API:

| Palette tab | Source | "Add" action |
|-------------|--------|--------------|
| Step kinds | static (things this UI creates) | append a blank command / wait step |
| Scenario library | `env.scenarios` | seed/replace the draft from a scenario |
| TTP cards | `GET /api/ttps` | append a step **pre-bound** to that card's detection (this is how a DC binds a real `tc_ref` to satisfy the launch gate) |
| Tool adapters | `GET /api/tools/adapters` | attach an `adapter_ref` to the selected step |
| Targets | `env.agents` | set the launch agent |
| Staged payloads | shelf (`useShelf`) | inspect / route to Tools & Payloads |

### 4.6 Editable inspector

The core of "fully functional." For the selected step, edit:

- name, command (textarea), identity (from the identity-harness spec set),
  technique, platforms, `causality.parent_step` + `pivot`,
- **expected detections:** add / remove; binding a TTP card fills
  `type` / `ttp_ref` / `detection_id` / `description`.

When no step is selected, show **workflow meta** (editable): name, plane,
`tc_ref` binding (deep-link to the UC/TC Index picker), CGO anchor. Every edit
flows through a pure op in `composerDraft.js`; the YAML view and validation
re-derive from the same model so they can never disagree.

### 4.7 Header actions

`Save draft` (POST/PUT), `Load` (draft picker), `Download YAML` (existing),
`Run preflight` (existing, now also reports the D5 tc-bound gate), `Launch`
(enabled only when saved **and** chain-valid **and** tc-bound).

## 5. Testing

- **Backend:** `DraftScenarioSchema` validation (accepts a minimal draft;
  rejects a broken spine, a duplicate step id, a bad detection type); the CRUD
  routes; the status guard on `list_scenarios` / uctc evidence (a draft row does
  **not** appear in the Library or inflate coverage); the launch gate (a draft
  with `tc_ref='UNBOUND'` is refused `409 DRAFT_NOT_TC_BOUND`; a tc-bound draft
  launches and seeds results).
- **Frontend:** extend `ComposerView.test.jsx` and add unit tests for
  `composerLayout.js`. Preserve every existing test contract in §
  `ComposerView.test.jsx` (empty-state three ways, seeded-from-scenario,
  failed-load honesty, YAML emit). The one contract that changes on purpose:
  "REFUSES to launch an edited draft" becomes "refuses to launch an **unsaved
  or un-tc-bound** draft, and says why."
- **CI:** `refs` job stays green (drafts never enter `scenarios/`, so the strict
  loader is untouched). Backend/ui suites gate as usual.

## 6. Migration & rollout

- No schema migration needed (reuses `status`, `author`, `tags`).
- Drafts are additive; no existing behaviour changes except the `list_scenarios`
  status guard (which only ever hid rows that do not exist yet).
- App route count rises by the drafts router's endpoints; update the counted
  ground truth in `docs/reference/README.md` when the work lands.

## 7. Out of scope

- Writing drafts to disk `scenarios/` (rejected in favour of DB rows).
- A conversational/chat dock (rejected; no real agent backend).
- Free-form DAG topologies (rejected; spine-constrained only).
- Promoting a draft into the shipped corpus — that stays a human PR against
  `scenarios/`, and is explicitly a separate, later concern.

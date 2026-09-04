# Simulation Composer — cross-surface, causality-stitched workflow program

**Status:** design in brainstorm 2026-09-04; spec pending user review.
**Owner:** Henry (DC), authored by Claude.
**Shape:** a three-phase program. Spec the whole architecture now; build Phase 1
first, gate, then Phase 2, then Phase 3.

**North star.** A DC composes one chain that fans out across **NICE — Network,
Identity, Cloud, Endpoint** — e.g. a third-party syslog/analytics detection, an
agent-based endpoint sim, and an EAL sim on a *second* endpoint that triggers
NGFW ingestion — and the platform stitches all of it into **one real causality
instance** because every channel plants the *same* XDM shared entities (5-tuple,
user principal, host, container) inside the correlation window. The Composer is
the surface that authors, coordinates, persists, and launches that run.

**The Composer is additive.** Existing scenario / TTP / EAL / assertion
execution paths stay fully usable on their own. Nothing here replaces them.

---

## 1. Why

The Composer (`ui/src/components/console/ComposerView.jsx`, 938 lines) is
half-built: static linear canvas, **read-only** inspector, and a from-scratch
chain **cannot launch** (SimCore only runs scenarios loaded from `scenarios/` at
boot). The `local-ai-platform` prototype
(`docs/design/project/ui_kits/console/Composer.jsx`, `console-v2/CanvasMode.jsx`)
shows the target *interaction model* (tabbed palette, node/edge canvas, editable
inspector, Design/Run lens, scrub timeline) but its runs are **mocked**.
CortexSim mirrors the interaction model and drives it with **real** data.

Beyond authoring, the deeper gap is **cross-surface orchestration**: today a
multi-plane scenario gets its shared 5-tuple "for free" because it is one
`curl` on one host (`SIM-MP-006`). The moment signal comes from *different
channels on different endpoints*, nothing coordinates the shared entities, and
nothing runs the channels under one timed, correlated run.

## 2. The doctrine this must not break (Gate A5)

1. **Authored is not proven.** `tenant-verified` stays 0. A composed draft that
   runs produces real seeded results, never a tenant-proven claim.
2. **A draft is not corpus coverage.** Draft rows are excluded from `/api/uctc`
   evidence, coverage counts, and corpus totals.
3. **No invented data.** The Run lens renders the **real** causality graph
   (`build_causality_graph`) with its real edge states; a stitch that fell
   outside the window shows **BROKEN**, never quietly "confirmed". No fabricated
   throughput/timing.
4. **No write path to Cortex.** Unchanged.

## 3. Decisions (locked in brainstorm 2026-09-04)

| # | Decision |
|---|----------|
| D1 | Full path: author + persist + run — not UI-only. |
| D2 | Canvas: graph canvas in the reference's visual language, **spine-constrained** (valid causality tree, one root; never a free-form DAG). |
| D3 | Persistence: DB `Scenario` rows with `status='draft'`, not disk YAML. |
| D4 | Bottom region: keep the honest workstream tabs; drop the chat dock. |
| D5 | Launch gate: a draft saves/edits freely, but **launch is blocked until it is bound to a real `tc_ref`** in the FY27 index (plus chain validity). |
| D6 | **NICE (Network · Identity · Cloud · Endpoint)** is the organizing model for surfaces, the palette, and the stitch context. |
| D7 | Phase-3 channels: **agent beacon · EAL emitter · second endpoint/agent** (not push bundle). |
| D8 | The **Stitch Context** generalizes the existing `_entities()` 8-key set, named canonically in XDM, resolved to consistent concrete values at launch. |
| D9 | The Run lens renders the **real** `build_causality_graph` output (nodes + typed edges + EXPECTED/CONFIRMED/BROKEN), not a mock. |
| D10 | Spec all three phases; build Phase 1 first, gate, then 2, then 3. |

## 4. Grounding — what the platform already gives us

Verified in-repo (2026-09-04) and against Cortex XSIAM docs.

### 4.1 Three field vocabularies, one reconciler
- **Raw** vendor-native emitter keys (on the wire to a collector), tagged with a
  `dataset` hint — e.g. NGFW `src`/`dst`/`proto` → `panw_ngfw_traffic_raw`
  (`core/eal_simulator/ngfw_eal_emitter.py:76,143-146`).
- **Normalized** `xdr_data` columns used in scenario `verification_xql` —
  `source_ip`, `action_local_ip`, `actor_effective_user_name`,
  `causality_actor_process_*`, `container_id`.
- **Canonical** `xdm.*` paths in modeling-rule exports
  (`detection_scanner/exports/modeling/*.xql`; schema
  `detection_scanner/schema/ttp-entry.schema.json:650-679`) — the authoritative
  dictionary. Most-used: `xdm.source.ipv4` (41), `xdm.source.user.username`,
  `xdm.target.container.id`, `xdm.source.host.hostname`, `xdm.network.*`.

`core/engine/causality_graph.py::_entities()` (`:268-294`) already coalesces all
three into **eight shared entity keys**: `host, src_ip, dst_ip, src_port,
dst_port, protocol, container_id, account`. **This is the seed of the Stitch
Context.**

### 4.2 NICE → XDM → the eight entity keys

| NICE | Canonical `xdm.*` | Normalized column | Entity key |
|------|-------------------|-------------------|-----------|
| Network | `xdm.source.ipv4` / `xdm.target.ipv4`, `xdm.source.port` / `xdm.target.port`, `xdm.network.application_protocol`, `xdm.network.session_id` | `source_ip`/`action_local_ip`, `dest_ip`/`action_remote_ip`, `src_port`, `dst_port`, `protocol` | `src_ip,dst_ip,src_port,dst_port,protocol` (the 5-tuple) |
| Identity | `xdm.source.user.upn` / `.username` / `.domain` | `actor_effective_user_name`, `causality_actor_primary_username` | `account` |
| Cloud | cloud account/project/resource (entity; **no CGO**), `xdm.target.container.id` | `resource_name`, `container_id` | `container_id` (+ cloud resource, see §7) |
| Endpoint | `xdm.source.host.hostname`, `xdm.source.process.causality_id`, CGO process | `agent_hostname`, `src_host`, `causality_actor_process_image_name` | `host` (+ `causality_id`) |

### 4.3 Two causality mechanisms (both must be authorable)
- **Endpoint process spine** — a **CGO (Causality Group Owner)** root; every
  process/wrapper node stamps the same `causality_id` (= `cgo:{run_id}`),
  mirroring XSIAM's `causality_actor_process_*` anchoring. Authored today via
  `cgo_anchor` + per-step `causality.parent_step`/`pivot`
  (`causality_graph.py:399-586`).
- **Entity-join stitching** — network/identity/cloud events attach by **shared
  entity**, not a process tree (Cortex: **cloud/SaaS causality has no CGO**).
  Authored via `stitching_key` + `correlation_window_seconds` +
  `required_planes_in_incident`, realized as `network_session` /
  `endpoint_network_stitch` / `shared_entity` / `temporal` edges.

### 4.4 The causality graph is real, and has a stitch state machine
`build_causality_graph` (`causality_graph.py`) is a pure deterministic
projection over seeded detections + observations. Nodes: `cgo, process, wrapper,
exposure, alert`. Edges carry `state ∈ {EXPECTED, CONFIRMED, BROKEN}`
(`_edge_state` `:317-330`): both ends observed within the window → CONFIRMED;
observed but outside → **BROKEN** (a demonstrable stitch gap); else EXPECTED.
`_stitch_value` / `_five_tuple` / `_shared_entity` (`:297-314,891-896`) resolve
the join values; `_causality_summary` yields `chain_completeness_pct`,
`stitched_incident`, `broken_stitches`, `dual_control_verdict`.

### 4.5 Two execution lifecycles today (Phase 3 must coordinate)
- Beacon: scenario `Run` → `Orchestrator.launch()` selects the DB row, seeds
  results, enqueues a step task the Go beacon pulls (`orchestrator.py:178-393`).
- EAL: `CampaignExecutor` runs **in SimCore's own process**, POSTs shape-true
  logs to a collector, tracked as a separate `EalCampaignRun` (`core/api/eal.py`,
  `core/eal_simulator/`). Shared entities today reach only identity
  (`analytics_emitter.py::canary_bindings` shares `account`/`principal`).

## 5. Target architecture (all three phases)

### 5.1 Data model additions (designed now, filled in per phase)

**Step gains a channel (back-compat).** `StepSchema` and the draft model add:
- `channel: 'agent' | 'eal' | 'agent@target'` — absent ⇒ `agent` (today's
  behaviour; every existing scenario loads unchanged).
- `target`: which endpoint/agent (Phase 3) or collector (EAL).
- `eal`: `{plugin, params}` when `channel == 'eal'`.
- `entity_bindings`: which Stitch-Context keys this step **plants** vs
  **consumes** (e.g. an NGFW EAL step plants the 5-tuple; the endpoint step
  consumes it). Absent ⇒ inherit all from context.

**Scenario gains a stitch context.** A `stitch_context` block (additive,
optional): the NICE-organized entity set (§4.2 keys), each entry either a
literal or a `resolve` directive (`auto_ip`, `auto_5tuple`, `canary_principal`,
`from_agent`). Persisted on the draft/scenario row. This is the generalization
of `canary_bindings` to all eight keys, XDM-named.

### 5.2 Stitch Context resolver (Phase 2)
`core/engine/stitch_context.py` (new, pure): given a `stitch_context` spec + the
launch target(s), resolve concrete consistent values (one src_ip, one 5-tuple,
one UPN, one host/CI anchor) and return a **binding map** keyed by the eight
entity keys, plus per-channel projections (raw emitter keys, `xdr_data` columns,
`xdm.*`) so each channel plants the value in its own vocabulary. Reuses the
`_entities()` coalescing rules in reverse. Deterministic, unit-tested, no
network.

### 5.3 Multi-channel orchestrator (Phase 3)
A coordinator that, for one composed run, dispatches each step to its channel —
beacon (existing), EAL (`CampaignExecutor`), or a second beacon/target —
injecting the resolved Stitch-Context binding so every channel emits the *same*
entities, ordered/timed to land within `correlation_window_seconds`. Tracked as
one composite run; per-channel sub-results roll up. Approach (merge lifecycles
vs coordinate two under a parent run id) is decided in Phase 3's detailed
design; the Stitch Context + channel-typed steps make either viable.

## 6. Phase 1 — Composer UX + single-channel persist/run (build first)

Everything here ships without any new orchestration substrate. Channel is
implicitly `agent`; a `SIM-MP-006`-style single-host chain already produces
multi-plane telemetry with a natural shared tuple.

### 6.1 Persistence (backend)
- Composed chains persist as `Scenario` rows: `status='draft'`, `author=<dc>`,
  `tags` includes `'composer-draft'`, `scenario_id = SIM-DRAFT-<slug>`. No new
  columns (reuses existing `status`/`author`/`tags`). Drafts live in
  `data/cortexsim.db`; never touch git-tracked `scenarios/`.
- New `DraftScenarioSchema` (`core/engine/composer_draft_schema.py`): requires
  only `name`, `plane`, well-formed `steps`; `uc_ref`/`tc_ref`/MITRE/KPI
  optional. Hard structural validation reused from the loader (step shape,
  detection shape, **causality-spine** rules — one root, no forward refs, valid
  pivot). Missing mandatory ORM fields filled with explicit sentinels
  (`tc_ref='UNBOUND'`, etc.); `push/pull_supported` derived from command text.

### 6.2 API surface (backend)
New router `core/api/drafts.py` at `/api/scenarios/drafts`: `POST` (create),
`GET` (list, `?author=`), `GET/PUT/DELETE /{id}`, and
`GET /{id}/launchable` (the D5 verdict). **Launch is unchanged** — once a draft
row exists, `POST /api/runs` runs it through the existing path.
`list_scenarios` and `_evidence_index` (`core/api/uctc.py`) gain a
`status='active'` guard so drafts never leak into Library / coverage.

### 6.3 Launch gate (D5, enforced server-side)
In the run-launch path (authoritative) and mirrored at `/{id}/launchable`:
`chain_valid` (every step has a command + ≥1 expected detection) **and**
`tc_bound` (`validate_index_refs(...)` resolves the draft's `tc_ref`/`tc_refs`
with no FK errors). A `status='draft'` row with `tc_ref='UNBOUND'` is refused
`409 DRAFT_NOT_TC_BOUND`, naming the fix. Consent + runtime-readiness preflight
inherited unchanged.

### 6.4 Frontend decomposition
`ComposerView.jsx` splits (pure modules stay React-free + unit-tested, per the
`runStatus.js`/`healthModel.js` convention):

| Module | Kind | Responsibility |
|--------|------|----------------|
| `composerDraft.js` (extend) | pure | model + edit ops (`editStep`, `add/removeDetection`, `setCausalityParent`) + `draftToApi`/`draftFromApi`. |
| `composerLayout.js` (new) | pure | spine → node coordinates + edge list. |
| `ComposerCanvas.jsx` (new) | view | zoomable graph: node cards, SVG spine edges, ports, zoom, selection, run-status overlay. |
| `ComposerPalette.jsx` (new) | view | NICE-organized tabbed palette (§6.6). |
| `ComposerInspector.jsx` (new) | view | editable step config + workflow meta. |
| `ComposerView.jsx` (slim) | view | wiring, draft state, save/load, launch, workstream. |

### 6.5 Canvas + two lenses
- **Design lens:** graph canvas in the reference's visual language, tinted by
  **plane** (the NICE analogue of the reference's role colour); edges are the
  spine-constrained causality tree; START/END anchors; layout from
  `composerLayout.js` (the DC adds/links/reorders, never drags into an invalid
  topology).
- **Run lens:** renders the **real** `build_causality_graph` — nodes and typed
  edges with EXPECTED/CONFIRMED/BROKEN, `chain_completeness_pct`, and the
  `broken_stitches` list. Pre-run: EXPECTED. Post-run/reconcile: confirmed or
  broken from real observations. Live status from `env.activeRun` SSE; terminal
  runs from `env.runs`. No mock throughput.

### 6.6 Editable inspector + NICE palette
- Inspector edits: name, command, identity, technique, platforms,
  `causality.parent_step`/`pivot`, expected detections (add/remove; bind a TTP
  card fills `type`/`ttp_ref`/`detection_id`). No step selected ⇒ editable
  workflow meta (name, plane, `tc_ref` picker → UC/TC Index, CGO anchor).
- Palette tabs (reference's roles/agents/skills/plugins/mcps → CortexSim,
  organized by NICE), every group API-sourced: **Step kinds**, **Scenario
  library** (seed/replace), **TTP cards** (`GET /api/ttps`, append a step
  pre-bound to a detection — the path to satisfying the launch gate), **Tool
  adapters** (attach `adapter_ref`), **Targets** (agents, by NICE surface),
  **Staged payloads** (shelf). Phase 3 adds an **EAL emitter** group here.

### 6.7 Header actions
`Save draft`, `Load`, `Download YAML` (existing), `Run preflight` (now also
reports the tc-bound gate), `Launch` (enabled only when saved + chain-valid +
tc-bound).

## 7. Phase 2 — Stitch Context (XDM shared entities)

- Implement `stitch_context` on the draft/scenario + `stitch_context.py`
  resolver (§5.1–5.2). Generalize `canary_bindings` to the eight keys; add a
  Cloud resource key (account/project/resource) since cloud stitches by entity,
  not CGO.
- Composer UI: a **Stitch panel** (NICE-organized) to declare the context and,
  per step, mark each entity **plant** vs **consume**. The canvas draws the
  entity-join edges the context implies (mirrors the graph's
  `endpoint_network_stitch`/`shared_entity`), so the DC *sees* how the chain will
  stitch before launch.
- Launch injects the resolved binding into the (still single-channel) run;
  `verification_xql`/reconciliation confirm the stitch. The Run lens now shows
  the entity-join edges as CONFIRMED/BROKEN — DC-authored, not accidental.

## 8. Phase 3 — Multi-channel orchestrated run

- Channel-typed steps go live (D7: agent · EAL · second endpoint). Palette gains
  the EAL-emitter group; inspector gains channel/target/eal-params/entity-binding
  editors.
- Multi-channel orchestrator (§5.3) dispatches per channel with the shared
  Stitch-Context binding, timed within the window; results roll up under one run.
- Delivers the north-star example: syslog/analytics (EAL) + agent endpoint sim +
  EAL sim on a second endpoint → NGFW ingestion, all stitched on the 5-tuple into
  one causality instance, verified as CONFIRMED edges across ≥N NICE planes under
  one `incident_id`.

## 9. Testing

- **Phase 1 backend:** `DraftScenarioSchema` (accepts minimal draft; rejects
  broken spine / dup step id / bad detection type); CRUD routes; the status guard
  (draft absent from Library + coverage); the launch gate (`UNBOUND` →
  `409 DRAFT_NOT_TC_BOUND`; tc-bound → launches + seeds).
- **Phase 1 frontend:** extend `ComposerView.test.jsx`; unit-test
  `composerLayout.js`. Preserve existing contracts; the one intentional change:
  "REFUSES to launch an edited draft" → "refuses to launch an **unsaved or
  un-tc-bound** draft, and says why".
- **Phase 2:** `stitch_context.py` resolver determinism + per-channel projection;
  UI plant/consume authoring.
- **Phase 3:** orchestrator fan-out + shared-binding injection + window timing;
  the north-star scenario as an integration test (injected transport).
- **CI:** `refs` stays green (drafts never enter `scenarios/`).

## 10. Migration & rollout

- No schema migration in Phase 1 (reuses `status`/`author`/`tags`). Phases 2–3
  add JSON columns (`stitch_context`, step `channel`/`eal`/`entity_bindings`) via
  the repo's idempotent `ADD COLUMN` pattern (as `cgo_anchor` did).
- Additive throughout; the only existing-behaviour change is the `list_scenarios`
  status guard (which only hides rows that do not exist yet).
- Update counted ground truth in `docs/reference/README.md` when each phase
  lands (route count rises with the drafts router, then the orchestrator).

## 11. Out of scope

- Writing drafts to disk `scenarios/` (rejected; DB rows).
- A conversational chat dock (rejected; no real agent backend).
- Free-form DAG topologies (rejected; spine-constrained).
- Push-bundle as an orchestrated stitch channel (D7 excluded it).
- Promoting a draft into the shipped corpus — stays a human PR against
  `scenarios/`, a separate later concern.

# CortexSim — Finalization Implementation Plan (autonomous-executable)

> **As-of:** 2026-07-11 · **Branch:** `ultracode/full-revamp`
>
> **Purpose.** A self-contained execution spec another model (or a fresh
> session) can implement **near-autonomously**. Every task names exact files,
> the change, an acceptance check, effort (`S` <½d · `M` ½–2d · `L` >2d), and
> dependencies. It is the actionable companion to the severity-ranked backlog in
> [`detection-engine-gap-analysis.md`](detection-engine-gap-analysis.md) — that
> doc is the *why/evidence*; this is the *how/do-it*.
>
> **Ground truth to respect (invariants):**
> - The causality graph, storyline, and scorecard are **three pure projections
>   of the seeded-Result denominator**. They MUST delegate coverage/MTTD to
>   `detection_storyline.build_summary` — never recompute — so they can't diverge.
> - New API routers follow the **storyline precedent**: dedicated router under the
>   `/runs` prefix, lazy-import 503 guard, `{ <name>: payload }` envelope,
>   registered once in `core/main.py`. The UI normalizes the builder shape at the
>   client edge (`ui/src/api/*.js`), never in the component.
> - Tests must not stub the thing they claim to test (see TC-01/TC-02).
> - Gates before commit: `make validate` · `cd ui && npm run build && npx vitest run`
>   · `make test-backend` (Docker; host is Py3.14 w/o app deps).

## Where we are (shipped, green)

| Capability | Files | State |
|---|---|---|
| Detection Proof Layer | `core/engine/detection_storyline.py`, `core/api/storyline.py`, `core/engine/efficacy_scorecard.py`, `ui/src/components/DetectionStoryline.jsx` | shipped, tested |
| Causality Graph | `core/engine/causality_graph.py`, `core/api/causality.py`, `ui/src/components/CausalityGraph.jsx`, `ui/src/api/causality.js` | shipped, tested |
| Dual-control POV | `scenarios/multi_plane/mp-007-staged-vuln-exploit-causality.yml` (SIM-MP-007), `docs/reference/exposure-plus-prevention-pov.md`, `docs/reference/causality-graph-methodology.md` | shipped |

Baselines after this work: backend **2150 pass / 0 fail**, UI **318 pass**, corpus **184 pass / 0 fail**.

---

## Thread A — Frontend-design UX pass (LEAD; gated by a `/frontend-design` discussion)

**Hold the `/frontend-design` discussion first** (Task #1). It sets the north-star
below; the UX build tasks (A1–A5) implement it. Do not build UX before the
discussion resolves visual hierarchy + the design-token system.

### North-star: the guided POV journey `Arm → Fire → Prove → Brief`

The console already has a stepper; the product's spine is a *guided narrative*
that currently **evaporates at the climax** (run-complete dumps the operator on
the raw Inflight table). Re-centre everything on reaching the proof:

- **Arm** (Library → Launch): pick + configure a scenario. *Fix:* plane rail must
  show all planes (A3).
- **Fire** (Live): watch execution. *Fix:* forward CTA to Prove.
- **Prove** (NEW first-class step): the **Detection Storyline** (engine-attributed
  live kill-chain) and the **Causality Graph** (endpoint↔network DAG) as a
  timeline↔graph toggle over the same run — the "wow". *Fix:* promote out of `More▾`.
- **Brief** (Export): one-click **CISO efficacy scorecard** walk-out. *Fix:* surface
  it in ExportMenu; disambiguate the three "Export POV" payloads.

### Per-component optimization (thinking → tasks)

| Component | Today | Optimize to | Task |
|---|---|---|---|
| `DetectionStoryline.jsx` / `CausalityGraph.jsx` | orphaned in `More▾`, never auto-reached | first-class **Prove** step; auto-route on run-complete; timeline↔graph toggle | A1 |
| `ExportMenu.jsx` | scorecard reachable only by hand-crafted URL | "Executive scorecard (.md/.html)" entry + header CTA | A2 |
| `PLANE_META` (`AppConsole.jsx`) | 11 hard-coded planes vs 14 corpus dirs | derive rail from live scenario list | A3 |
| `TargetsView.jsx` | legacy self-asserted `--id` installer | mint-enrollment-token front door; demote `--id` to advanced | A4 |
| journey hand-off (`ConsoleStepper`, `InflightView`) | dead-ends at Live | persistent "Next step ▸" CTA through Prove → Brief | A1 |
| evidence deep-link (`onOpenEvidence`) | detection arg **discarded** (`AppConsole.jsx:539,549`) | pass the detection, open EvidenceView with that row pre-selected | A5 |
| sensor-health pill (`AppConsole.jsx:122`) | **fabricated** `{xdr:'healthy',...}` | wire to Phase-9 `/healthcheck` or render muted/"unknown" | A5 |
| empty-state copy, `G T` keybinding dupe | point to non-existent tab; duplicate chord | correct copy; reassign one chord | A5 |

**A1** (M) — Prove step + routing. Files: `ui/src/AppConsole.jsx` (add `prove`
tab that hosts a storyline/graph toggle; change `handleRunComplete` → route to
`prove`), `ui/src/components/console/AppShell.jsx` (promote out of `MORE_ITEMS`),
`InflightView.jsx` (forward CTA), `HelpOverlay`. **Accept:** completing a run lands
on the proof surface without `More▾`; toggle switches timeline↔graph for the same run.

**A2** (S) — scorecard export. Files: `ui/src/components/console/ExportMenu.jsx`
(+ a client wrapper for `GET /api/runs/{id}/report?format=scorecard[-html]`).
**Accept:** menu downloads both formats; correct Content-Type.

**A3** (S) — live plane rail. File: `ui/src/AppConsole.jsx` — replace the static
`PLANE_META` with distinct planes derived from the loaded scenario list.
**Accept:** CSPM/ASM/TIM/EMAIL appear and filter; no static list to drift.

**A4** (M) — enrollment-token UI. File: `ui/src/components/console/TargetsView.jsx`
— "Mint enrollment token" (TTL/max-uses/revoke) calling `POST /api/agents/enroll/tokens`,
generate the token one-liner; demote raw `--id`. **Accept:** DC can mint/revoke a
token and copy a `install?token=…` one-liner.

**A5** (S, bundle) — deep-link evidence (thread the `det` arg + pre-select row in
`EvidenceView.jsx:152` drawer); real/muted sensor health; empty-state copy
(`EvidenceView.jsx:69`, `InflightView.jsx:144`); reassign duplicate `G T`
(`AppConsole.jsx:294/357`). **Accept:** evidence click lands on the right row;
no fabricated green; copy names real steps; unique chords.

---

## Thread B — Test hardening (kill false-greens; cheap, high-trust)

**B1 — TC-01** (S). `tests/api/test_storyline_api.py` stubs the real builder →
false green. Add a **non-stubbed** integration test: seed a run, hit
`GET /api/runs/{id}/storyline`, assert `entries[]` + `coverage_pct` from the real
`build_storyline`. **Accept:** a shape drift in `Result.to_dict()` now fails a test.

**B2 — TC-02** (S). `?format=scorecard`/`scorecard-html` (`core/api/runs.py`) has
zero tests. Add report tests over a seeded run: 200, correct Content-Type
(text/plain vs text/html), coverage numbers matching seeded results.

**B3 — TC-06 parity** (S). One parametrized test feeding a shared row-set
(detected / reviewed-unfired / pending) through `report_generator`,
`efficacy_scorecard`, and `detection_storyline`, asserting **equal** counts —
enforces the "three projections never diverge" invariant.

**B4 — live SSE** (S, TC-04/TC-05). Backend: assert a `result.observed` frame on
`event_bus` during manual-ingest reconcile. UI: mock `EventSource`, dispatch a
`result.observed` frame, assert a storyline row flips Awaiting→Detected.

---

## Thread C — Causality NEXT edges (turn EXPECTED into evidence-backed CONFIRMED)

Extend `core/engine/causality_graph.py` (+ matcher/model support). Keep pure;
delegate counts to `build_summary`.

**C1 — CG-04 temporal** (S). After matching, compute pairwise `observed_at`
deltas; emit `temporal` edges where delta ≤ `scenario.correlation_window_seconds`
(a currently-unused YAML field). Files: `causality_graph.py`.

**C2 — CG-05 shared_entity** (M). Extract host/container_id/account/ip onto
`Result` + `ObservedAlert` (`core/models.py`, `core/connectors/base.py`); emit
`shared_entity` edges on any shared value. Adds a DB column → migration-safe default.

**C3 — CG-07 same_incident** (M). Add `incident_id` to `ObservedAlert`
(`core/connectors/base.py`) + a `group_by_incident` pass in
`core/connectors/matcher.py`; emit `same_incident` edges — the most direct proof
XSIAM stitched N alerts into one incident.

---

## Thread D — Detection library depth (Later; corpus policy)

Enforce the targets table in `detection-engine-gap-analysis.md §Policy`. Highest
leverage: **DL-01** (6–10 ABIOC scenarios across EDR/ITDR/NDR → ABIOC+Analytics
≥15%), then **DL-04** (backfill Execution/PrivEsc/Lateral Movement to ≥5 each),
**DL-05** (per-plane floor of 3; rescue CSPM=1, TIM=2). Author with the
`/new-scenario` skill; every scenario must pass `make validate`.

---

## Execution model — how another model runs this near-autonomously

**Recommended order:** Thread B (fast, buys trust) → Task #1 discussion → Thread A
(UX) → Thread C (causality edges) → Thread D (corpus, ongoing).

**Per-task loop (deterministic):**
1. Read the named files + the neighbouring pattern (storyline for API/UI slices).
2. Make the change; keep new API/UI slices disjoint-new-file where possible;
   for existing-file wiring, mirror the storyline/causality precedent already in
   `main.py` / `AppConsole.jsx`.
3. Write/extend the test named in the task.
4. Gate: `make validate` (if corpus touched) · `cd ui && npm run build && npx vitest run`
   (if UI) · `make test-backend` (if backend). All must be green.
5. Commit at a sensible boundary (named files, no `-A`, no PR): `feat(ux|test|causality): …`.

**Parallelizable as a workflow.** Threads A5, B1–B4, C1 are disjoint and
independently testable — ideal for a fan-out. A ready-to-run script is prepared at
[`docs/workflows/finalize-slice.workflow.js`](../workflows/finalize-slice.workflow.js);
launch with `Workflow({ scriptPath: "docs/workflows/finalize-slice.workflow.js" })`.
It was **authored but not executed** this turn because the Anthropic session/usage
limit was hit mid-run — fire it once the limit resets.

**Guardrails a subagent must not violate:** don't recompute coverage/MTTD (delegate
to `build_summary`); don't let a test stub its own subject; don't hand-edit
`detection_scanner/exports/` (regenerate) or `sources/**` (submodules); keep the
plane rail derived, never re-hardcoded.

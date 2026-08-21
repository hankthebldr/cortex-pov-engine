---
title: "cortex-pov-engine — UC/TC Reconciliation & Detection Coverage"
type: implementation-plan
status: ready-for-execution
repo: cortex-pov-engine
repo-path: /Users/henry/Github/Github_desktop/cortex-pov-engine
things-project: "cortex-pov-engine — Detection Simulation Engine"
things-uuid: MmEL8T1BxKP4MRZ8M2JvGH
area: cortex
period: FY27
owner: Henry Reed
surface: claude-code (BD790i or MBP)
date-created: 2026-07-31
informed-by: "[[OC-I004 UC-TC Index v2.x]]"
---

# Implementation Plan — cortex-pov-engine UC/TC Reconciliation & Detection Coverage

**Handoff target:** Claude Code, working in `/Users/henry/Github/Github_desktop/cortex-pov-engine`.
**Companion data:** `Cortex-UCTC-v2.2-Patch-Pack.xlsx` — tabs 3 (Detection Spec), 5 (Crosswalk Gap),
6 (Build Backlog), 2 (Product/Add-On), 1 (SKU Catalog). Export each to CSV into
`docs/uc_tc_mapping/_v2.2-source/` as the first act of Phase 0.

---

## 0 · Why this plan exists

The engine is in far better shape than the FY27 initiative notes claim — 88 scenarios across 15
planes, 89 TTP cards resolving 753 detection objects, a working XSIAM connector, a real
measurement loop. `OC-I003 CortexSim` says `status: proposed` against "an empty folder." That is
wrong and should be corrected (see §7).

The actual problem is not missing content. It is that **the engine cannot prove what it covers.**

Three defects, in dependency order:

1. **The UC/TC join is broken and silently wrong.** 88 scenarios carry `uc_ref` / `tc_ref` as
   unvalidated free-text. Joined against the v2.2 master index: **53 dangle** (no such TC exists)
   and **21 are false matches** — the string collides but the differentiation tier disagrees,
   meaning `TC-EDR-01` in the engine and `TC-EDR-01` in the index are different test cases. Only
   14 of 88 are even provisional matches. Any coverage number the engine reports today is fiction.
2. **The index in-repo is pinned to v2.0.** `docs/uc_tc_mapping/_v2-source/tc_index.json` is the
   248-TC / 56-UC v2.0 export. Master is now v2.2: 266 TC / 49 UC, with all descriptions
   re-authored and two new UCs (UC-XTI, UC-APB).
3. **Every measurement field the POV report needs is validated then thrown away.**
   `scenario_loader.py` accepts `primary_kpi`, `threshold`, `success_criteria`, `moat_tier`,
   `methodology_family`, `correlation_window_seconds`, `stitching_key`, and per-detection
   `verification_xql` / `kpi_contribution` — and persists none of them. Loader comment:
   `# Not yet persisted to the ORM — validation-only in this pass.` So a run produces
   observed/not-observed and MTTD, but cannot answer "did this test case PASS its threshold."

Fixing (1) without (3) yields an accurate map of an unscoreable system. Fixing (3) without (1)
yields precise scores against the wrong test cases. Do them in the order below.

---

## 1 · Architecture — where the seams are

```
  MASTER INDEX (Google Sheets, v2.2)          ← source of truth for the sales motion
    49 UC · 192+ UCS · 266 TC
          │  (exported, versioned)
          ▼
  docs/uc_tc_mapping/_v2.2-source/*.csv       ← NEW: versioned snapshot in-repo
          │
          ▼
  core/engine/uctc_registry.py                ← NEW: loads the index, validates refs
          │
          ├──────────────┐
          ▼              ▼
  scenarios/**.yml   detection_scanner/ttps/*.json
   uc_ref/tc_ref      panw_mapping.use_cases[]
   (namespace A)         (namespace C)
          │              │
          └──────┬───────┘
                 ▼
        core/engine/scenario_loader.py  →  Scenario ORM
                 │
                 ▼
        core/engine/orchestrator.py     →  Run → Result[]
                 │
                 ▼
        core/connectors/xsiam.py + matcher.py  →  observed_at, mttd_seconds
                 │
                 ▼
        core/engine/report_generator.py →  POV readout
```

**Three UC/TC namespaces exist today and nothing joins them:**

| Namespace | Where | Example | Machine-read? |
|---|---|---|---|
| A · scenario refs | `scenarios/**.yml` | `uc_ref: UCS-EDR-06` / `tc_ref: TC-EDR-06` | yes — `uctc_mapper.py`, unvalidated |
| B · master index | `docs/uc_tc_mapping/` | `TC-IR-01`, `UC-IR`, `UCS-IR-01` | **no** — nothing in `*.py` reads it |
| C · TTP card refs | `detection_scanner/ttps/*.json` | `UC-RANSOM-002` / `TC-RANSOM-002A` | partly — folded into `score_weights` |

**Decision: B is canonical. A becomes a validated FK into B. C is demoted to a card-local
`threat_scenario_id` and stops pretending to be a UC.** Namespace C's IDs are threat-narrative
labels ("RANSOM", "INSIDER", "SUPPLY"), not sales use cases; keeping them named `UC-`/`TC-`
guarantees future confusion.

---

## 2 · Phases

Each phase has a Definition of Done. Do not advance on a red gate.

### Phase 0 — Snapshot and freeze (½ day)

1. Create `docs/uc_tc_mapping/_v2.2-source/` and land four CSVs exported from the patch pack:
   `uc_index_v2.2.csv` (49 rows), `tc_index_v2.2.csv` (266 rows), `detection_spec_v2.2.csv`
   (266 rows), `sku_catalog.csv` (38 rows).
2. Add `docs/uc_tc_mapping/VERSION` containing `2.2` and the source spreadsheet fileId
   `1w-lNpc1uZ3CcrFr89Uo1miuCYMCwie8-1TYSToHm9yM`.
3. Move `_v2-source/` → `_archive/v2.0-source/`. Do not delete — the v2.0 → v2.2 re-key trail
   lives there.
4. Fix the duplicate `TC-IR-02` row in `v2.0-tc-mapping-table.csv` before archiving.

**DoD:** `git diff --stat` shows only additions + one move; `pytest` unchanged (nothing reads
these yet).

---

### Phase 1 — Make the UC/TC join real (2–3 days) ← **the unblocking phase**

**1a. `core/engine/uctc_registry.py` (new).**
Loads `_v2.2-source/*.csv` at boot into frozen dataclasses. Public surface:

```python
@dataclass(frozen=True)
class TestCase:
    tc_id: str; ucs_id: str; uc_id: str; use_case: str; scenario: str
    title: str; description: str
    primary_kpi: str; threshold: str; measurement_method: str
    detection_source: str; expected_signal: str
    moat_classification: str; differentiation_tier: str
    validation_class: str          # DET | HNT | POS | PLT | AUT
    base_platform: str; required_addon: str

class UcTcRegistry:
    def tc(self, tc_id) -> TestCase | None
    def ucs(self, ucs_id) -> list[TestCase]
    def uc(self, uc_id) -> UseCase | None
    def validate_ref(self, uc_ref, tc_ref) -> RefVerdict   # ok | dangling | fk_mismatch
```

Mirror the `ttp_catalog.py` contract exactly: pure read path, rebuilt at boot, never mutates
source. Do not add an ORM table for the index — it is a versioned file artifact, not runtime state.

**1b. Wire validation into `scenario_loader.py`.**
Add checks alongside the existing S-01/S-02/S-09 warnings:

| Code | Condition | Severity |
|---|---|---|
| `S-10` | `tc_ref` not in registry | **ERROR** (was: silent) |
| `S-11` | `uc_ref` not in registry | **ERROR** |
| `S-12` | `tc_ref` resolves but its `ucs_id` ≠ `uc_ref` | **ERROR** |
| `S-13` | `moat_tier` ≠ registry `differentiation_tier` | WARNING |
| `S-14` | scenario exists for a TC whose `validation_class` is POS/PLT/AUT | WARNING |

Gate behind `CORTEXSIM_STRICT_REFS` (default `false` for one release, then flip). Shipping S-10
as a hard error on day one fails 53 of 88 scenarios at boot.

**1c. Author the crosswalk.** For each of the 74 broken refs (tab 5 of the patch pack), one of
three resolutions — record the choice in `docs/uc_tc_mapping/crosswalk-v2.2.csv` with columns
`scenario_id, old_uc_ref, old_tc_ref, new_uc_ref, new_tc_ref, resolution, rationale`:

- **REMAP** — a real v2.2 TC exists for this scenario; point at it. Expect most of `CDR`, `EDR`,
  `ITDR`, `NDR` overflow here.
- **NET-NEW TC** — the scenario proves something the index does not yet carry. Emit a proposed TC
  row into `docs/uc_tc_mapping/proposed-tc-v2.3.csv` for Henry to merge upstream. **The whole
  `AI_ACCESS`, `BROWSER`, `KOI`, `CLOUD_APP`, `AI_SPM` and `ANALYTICS`/multi-plane set (33
  scenarios) almost certainly lands here** — the index has no UC for browser plane, AI access
  brokering, or agentic-supply-chain (KOI). That is an index gap, not an engine defect, and it is
  the most valuable output of this phase.
- **ORPHAN** — the scenario has no sales motion behind it; mark `status: deprecated`.

**1d. Rename namespace C.** In `schema/ttp-entry.schema.json`, rename
`panw_mapping.use_cases[].use_case_id` → `threat_scenario_id` and `test_cases[].test_case_id` →
`threat_step_id`. Migrate all 89 cards with a script. Update `ttp_catalog._parse_entry` and the
`score_weights` keying. This is mechanical and prevents the next person re-conflating them.

**DoD:**
- `crosswalk-v2.2.csv` covers all 88 scenarios, zero blank resolutions.
- With `CORTEXSIM_STRICT_REFS=true`, `python -m core.main` boots with **zero** S-10/S-11/S-12.
- `pytest tests/engine/test_uctc_registry.py` — new, ≥15 cases incl. dangling, FK mismatch, and
  the tier-disagreement case.
- No `use_case_id` string remains outside `_archive/`.

---

### Phase 2 — Persist the measurement contract (2 days)

The v2.0 KPI block is the reason a POV readout cannot be scored. Land it.

**2a. ORM.** Add to `Scenario`: `validation_methodology`, `methodology_family`, `primary_kpi`,
`threshold` (JSON), `success_criteria`, `moat_tier`, `correlation_window_seconds`,
`stitching_key`, `required_planes_in_incident` (JSON). Alembic migration; these are all nullable.

**2b. Add to `Result`:** `verification_xql`, `kpi_contribution` (JSON), `kpi_verdict`
(`pass|fail|pending|not_applicable`), `verified_at`.

**2c. Verification harness — `core/engine/verifier.py` (new).** This is the piece the loader
comment promises and nothing implements. For each `Result` with a `verification_xql`: run it via
`integrations/xsiam/client.start_xql_query()`, compare row count to `expected_rows_min`, set
`kpi_verdict` and `verified_at`. Runs on the same opt-in timer as `auto_reconcile_loop`; reuse
that scheduling pattern rather than inventing a second one.

**2d. Run-level scoring — `Run.tc_verdict`.** A run PASSES its test case when every
`kpi_contribution`-weighted Result passes and the aggregate clears `Scenario.threshold`. Surface
it in `report_generator.py` alongside `methodology_family` and `moat_tier`.

**Blocker to flag, not solve here:** 62 of 266 TCs carry `Qualitative pass` as their threshold and
86 of 107 content artifacts have no MITRE technique. `verifier.py` must treat those as
`not_applicable` and **log them to `docs/uc_tc_mapping/unscoreable-tcs.md`** rather than silently
passing them. Silent pass on an unscoreable TC is worse than no scoring at all.

**DoD:** a scenario with `threshold: {kpi: MTTD, op: "<=", value: 300, unit: s}` produces a
`Run.tc_verdict` of `pass`/`fail` end-to-end against a live or mocked XSIAM tenant;
`GET /api/runs/{id}/report` renders KPI, threshold, verdict, moat tier, methodology family.

---

### Phase 3 — License gating (1–2 days)

New, and it is what makes the engine sellable rather than just demonstrable. A POV must not
propose a scenario the prospect cannot license.

**3a.** Add to `ScenarioSchema`: `required_base_platform: list[str]`, `required_addons: list[str]`.
Populate from tab 2 of the patch pack via the scenario's `tc_ref` → registry → UC.

**3b.** Enum-validate against `sku_catalog.csv` (tab 1). Values are capability names
(`Cloud Runtime`, `Cloud Posture`, `Cloud AppSec`, `ASM`, `EM`, `ITDR`, `Host Insights`,
`Forensics`, `XTH`, `XTI`, `TIM`, `Email Security`, `Endpoint DLP`, `FIM`, `Endpoint Protection`,
`Compute Units`, `Retention`), each carrying a real `PAN-*` part number.

**3c.** `GET /api/scenarios?entitlement=...` — filter the catalog to what a given tenant profile
can actually run. `POST /api/pov/scope` takes a tenant entitlement set and returns the runnable
scenario list plus the "you would need to add X" delta. That delta is a sales artifact, not a
technical one — it is the upsell list, generated from the POV.

**DoD:** every scenario carries a non-empty `required_base_platform`; the scoping endpoint returns
a correct runnable/blocked split for the three canned profiles (NG-SIEM bare, Enterprise, Premium).

---

### Phase 4 — Close the coverage gap (ongoing, P1 first)

`6 · Engine Build Backlog` in the patch pack is the worklist: **99 content artifacts with no
scenario today — 32 of them P1/MOAT.** Ordered `P1 → P2 → P3`, each row carrying its proposed
detection type, target dataset, POV scenario ID, and the authoring gaps it has to close.

Two structural fixes to do *before* bulk authoring, or the work compounds:

- **Split the 6 over-shared simulation payloads** (tab 4, `reuse_flag = SPLIT REQUIRED`).
  `POV-SC-001` backs 21 test cases; 16 detections cannot be independently validated because one
  injection fires all of them.
- **CSPM is one scenario against ~107 posture TCs.** But per the validation-class split most of
  those are POS-class — they need *fixtures*, not detections. Build a fixture harness
  (`scenarios/cspm/fixtures/`) that provisions known-bad Terraform and asserts posture-policy
  evaluation, instead of authoring 107 scenarios. This is the single biggest effort saver in the
  plan.

**DoD per artifact:** card exists in `detection_scanner/ttps/`, passes `scripts/validate.py`,
is bound from a scenario `expected_detections[]`, has a MITRE technique, a measurable threshold,
an `fp_guard`, and a `teardown`.

---

### Phase 5 — Doc truth-up (½ day, do last)

Delete or rewrite; do not leave both. From the survey:

- `docs/uc_tc_mapping/README.md` — **superseded**, describes six `*_uc_tc.yml` files that do not
  exist and a loader validation that does not happen. Rewrite against Phase 1.
- `docs/implementation-plans/README.md` — says "these are plans, not yet built"; all five shipped.
- `detection_scanner/README.md` — says "six entries"; there are 89.
- `docs/strategic-roadmap.md` — progress log two months stale.
- `docs/reference/detection-coverage.md` — pinned to 63 cards / 58 scenarios.
- `docs/tool_registry.md` — 11-tool table superseded by the 69-pack adapter framework. Delete.
- `lab_cortex_analytics_pov/` — orphan legacy lab, rule format incompatible with the card schema,
  referenced by nothing. Move to `_archive/` or delete.
- `docs/specs/2026-05-08-detection-scanner-integration.md` — specs a wiki-sync that was never
  built as described. Delete.

---

## 3 · Sequencing and effort

```
Phase 0  ▓                                   0.5d   snapshot
Phase 1  ▓▓▓▓▓▓                              3d     ← unblocks everything
Phase 2      ▓▓▓▓                            2d     depends on 1a
Phase 3          ▓▓▓                         1.5d   depends on 1a + 2a
Phase 4              ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓        ongoing depends on 1c
Phase 5                              ▓       0.5d
```

Phases 2 and 3 can run in parallel once `uctc_registry.py` exists. Phase 4 must not start before
Phase 1c, or every artifact authored lands against a ref that later moves.

---

## 4 · Non-goals

- Do not build a UI for the crosswalk. It is a CSV reviewed once.
- Do not add an ORM table for the master index. It is a versioned file artifact.
- Do not attempt live two-way sync with the Google Sheet. Export → commit → PR. The index moves on
  a human cadence; a sync loop would be a permanent source of drift.
- Do not fix `analytics_modules` (GAP-11, 143 named-but-untestable modules) here. It is real, it is
  separate, and it will eat the phase.

---

## 5 · Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| The 33 net-new-TC scenarios (browser, KOI, AI access, cloud app, multi-plane) get force-fitted onto existing index UCs | The index absorbs a lie and the false-match problem recurs one layer up | Phase 1c must allow NET-NEW as a first-class resolution and emit `proposed-tc-v2.3.csv`. Resist the tidy answer. |
| `CORTEXSIM_STRICT_REFS` flipped to true before the crosswalk is complete | 53 scenarios fail at boot; engine unusable | Default false; flip only at Phase 1 DoD. |
| Namespace C rename breaks `score_weights` | Silent scoring regression | Migration script + a test asserting per-card weight sums are unchanged pre/post. |
| Index re-bases to v2.3 mid-flight | Crosswalk invalidated | `VERSION` file + the crosswalk keyed on `scenario_id`, not row order. |
| Phase 4 authored against `Qualitative pass` thresholds | 62 unscoreable TCs look green | `verifier.py` emits `not_applicable`, never `pass`; log to `unscoreable-tcs.md`. |

---

## 6 · Things 3 binding

Use `docsync` — the repo name, Things project name, and vault folder are all `cortex-pov-engine`.
Do not call `add_todo` directly; pass the explicit `list_id` for
`MmEL8T1BxKP4MRZ8M2JvGH`. Suggested sections mirroring the phases: `Phase 0 · Snapshot`,
`Phase 1 · UC/TC Join`, `Phase 2 · Measurement`, `Phase 3 · License Gating`,
`Phase 4 · Coverage`, `Phase 5 · Doc Truth-Up`.

Run `board-reconcile` before starting — the board last synced 2026-07-12 and the repo has moved.

---

## 7 · Correction to file upstream

`OC-I003 CortexSim — Cortex Detection Engine` currently reads `status: proposed`, gate
`discovery`, with the note *"`03-cortexsim/` is an empty folder; Phase 1 architecture was logged
to Things but no artifact survives."* That is false. The repo is at 88 scenarios, 89 cards, a
working XSIAM connector, 1596 passing Python tests. Set `status: active`, gate `build`, bind
`repo: cortex-pov-engine`, and retire DEFERRED-6 — it has been blocking scoping for a project that
is already three phases in.

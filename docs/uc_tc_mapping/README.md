# UC/TC Mapping — the master index and how scenarios join to it

The FY27 Use-Case / Test-Case index is the source of truth for the sales motion.
This directory holds the versioned in-repo snapshot of it and the crosswalk that
binds every CortexSim scenario to it.

**Current version: 2.2** — 49 UC · 203 UCS · 266 TC · 38 SKU. See [`VERSION`](VERSION).

## The three namespaces

Three things in this repo have historically been called "UC" or "TC". Only one
of them is canonical.

| | Where | Example | Status |
|---|---|---|---|
| **A · scenario refs** | `scenarios/**.yml` | `uc_ref: UCS-EDR-02` · `tc_ref: TC-EDR-03` | a validated **foreign key into B** |
| **B · master index** | `_v2.2-source/*.csv` | `TC-IR-01` · `UC-IR` · `UCS-IR-01` | **canonical** |
| **C · card-local threat ids** | `detection_scanner/ttps/*.json` | `TS-RANSOM-002` · `TS-MP-010A` | card-local narrative labels — **not** use cases |

Namespace C was renamed in Phase 1d (`use_case_id` → `threat_scenario_id`,
`test_case_id` → `threat_step_id`, values re-prefixed `UC-`/`TC-` → `TS-`)
because ids that *look* like index refs but aren't are how the join silently
broke the first time. A guard test in `tests/engine/test_ttp_catalog.py` fails
if a card id ever carries the `UC-`/`TC-` prefix again.

## The payload join — why `tc_refs` is a list

The index binds one **POV-SC payload** to **many** test cases. `POV-SC-008`
("network-based attack — beaconing, DNS tunneling, SMB/RDP lateral") backs 6
test cases, and the engine carries 12 NDR scenarios that are all instances of
that one payload.

So a scenario evidences a *set* of test cases, not one:

```yaml
uc_ref: UCS-NDR-02                 # the UCS group (or a UC id — both are legal)
tc_ref: TC-NDR-03                  # primary binding, required, back-compatible
tc_refs:                           # the full evidence set
  - TC-NDR-03
  - TC-NDR-04
pov_scenario_id: POV-SC-008        # the index payload this scenario instantiates
```

`tc_refs` defaults to `[tc_ref]` and always contains it, so a scenario that
never declares it behaves exactly as it did before.

## Validation — what the loader enforces

`core/engine/uctc_registry.py` loads `_v2.2-source/` at boot;
`core/engine/scenario_loader.py` validates every scenario against it.

| Code | Condition | Severity |
|---|---|---|
| `S-10` | `tc_ref` not in the index | ERROR |
| `S-11` | `uc_ref` is neither a known UCS group nor a known UC | ERROR |
| `S-12` | `tc_ref` resolves but its parent ≠ `uc_ref` | ERROR |
| `S-13` | `moat_tier` disagrees with the index `differentiation_tier` | WARNING |
| `S-14` | the bound TC is POS/PLT/AUT — a posture/platform assertion, not a detection | WARNING |
| `S-15` | a non-primary `tc_refs[]` entry is not in the index | ERROR |
| `S-16` | `pov_scenario_id` names no payload in the index | WARNING |

`CORTEXSIM_STRICT_REFS` (**default `true`**) decides whether the ERROR codes
reject a scenario at boot or merely log. It is on because the corpus is clean;
set it false only to load a corpus mid-re-key. If the snapshot is absent
entirely the registry reports `unverified` and never rejects, so a stripped
deployment still boots.

## In the product — the index is browsable, not just a file

The snapshot is also served **inside CortexSim** so a DC can answer "what does
this POV prove, and what does it not" without opening a CSV in front of a
customer.

- **API:** `core/api/uctc.py` → `GET /api/uctc/{summary,use-cases,test-cases,coverage,gaps,payloads,by-scenario/{id}}`.
  Read-only by design; authoring stays in this directory and in
  `scripts/uctc_crosswalk_v2.2.py`. Full endpoint table:
  [`../reference/api-and-agent-surface.md`](../reference/api-and-agent-surface.md) §1.14.
- **Console:** the **UC / TC Index** destination under *Analyze*
  (`ui/src/components/console/UcTcIndexView.jsx`, route `#/uctc`). Three modes —
  Index (UC rail → TC table → detail drawer), Coverage (worst-covered UC first),
  and Gaps (unevidenced DET/HNT, P1 first). Deep-linkable:
  `#/uctc?tab=index&uc=UC-EDR&tc=TC-EDR-03`.
- **Licensing:** `core/api/pov.py` → `GET /api/pov/profiles`, `/capabilities`,
  `POST /api/pov/scope` scopes the corpus to a tenant's entitlements and
  generates the upsell list from the same registry.

Two things the surface deliberately does **not** do. It never joins evidence
through `pov_scenario_id` (one payload binds up to 21 test cases, so that would
over-claim wildly), and it never hides `is_scoreable: false` — 57 of the 107
detection-backable rows carry no measurable threshold, so a `pass` verdict is
impossible for them by construction and saying so is the point.

## Two proof mechanisms, not one

DET and HNT rows are proven by an **attack scenario** — a TTP fires and a
detection catches it. POS, PLT and AUT rows are not detections at all: they ask
whether a state *holds*, whether a capability is *present*, or whether an
outcome *occurs inside a budget*. Authoring more scenario YAML cannot answer
any of those, so the engine carries a second artifact type.

| | DET / HNT | POS / PLT / AUT |
|---|---|---|
| artifact | `scenarios/{plane}/*.yml` | `assertions/{pos,plt,aut}/*.yml` |
| ORM | `Scenario` → `Run` → `Result` | `Assertion` → `AssertionRun` → `AssertionCheck` |
| proves | a detection fired | a probe measured a number and it cleared a bar |
| scored by | `verifier.score_run` | `verifier.score_run` (same function) |
| API | `/api/scenarios`, `/api/runs` | `/api/assertions`, `/api/assertions/runs` |

An assertion **cannot be authored unless it can fail**. At load time the
loader builds measurements across the probe's own physical domain, pushes them
through the real evaluator, and rejects the artifact unless the check produces
both a `fail` and a `pass` (`A-17`), and unless the author's declared
`negative_control` really evaluates `fail` (`A-18`). Both are structural —
`CORTEXSIM_STRICT_REFS` does not relax them. `expected_rows_min: 0` on a row
count is rejected with *"this check can never fail and therefore proves
nothing"*. No tenant, an unreachable tenant, a 401/429, a bad dataset or a dry
run all resolve **`pending`**, never `pass` and never a benign
`not_applicable`. Full contract: [`assertions.md`](assertions.md).

If the snapshot is absent from a deployment, every endpoint returns **200 with
`index_loaded: false`** and the console renders an explicit degraded state
rather than a misleading zero.

## Current state

- **162 / 162 scenarios** resolve. Zero S-10/S-11/S-12/S-15.
- **86 of 266** index test cases are evidenced by an attack **scenario**
  (**67 of 107** DET/HNT — the detection-backable subset). This is the number
  `scripts/uctc_crosswalk_v2.2.py --report` and `/api/uctc` report, because both
  walk `Scenario` rows only.
- **18 assertion artifacts** (`assertions/{pos,plt,aut}/*.yml`) bind **18 more
  test cases**, 8 of which no scenario reached. Union across both mechanisms:
  **94 of 266**.
- 100 scenarios carry an `S-13` tier disagreement and 13 an `S-14` posture-class
  binding. Both are advisory and both are real — see
  [`index-gaps-v2.2.md`](index-gaps-v2.2.md).

### Coverage by validation class

The index is not one population. Each `validation_class` is proven by a
different mechanism, and a flat percentage hides that.

| class | total | by scenario | by assertion | union | open | index-scoreable | tenant-verified |
|---|---:|---:|---:|---:|---:|---:|---:|
| DET | 102 | 63 | 0 | 63 | 39 | 49 | 0 |
| HNT | 5 | 4 | 0 | 4 | 1 | 1 | 0 |
| POS | 110 | 18 | 11 | 19 | 91 | 19 | 0 |
| PLT | 43 | 1 | 4 | 5 | 38 | 16 | 0 |
| AUT | 6 | 0 | 3 | 3 | 3 | 6 | 0 |
| **all** | **266** | **86** | **18** | **94** | **172** | **91** | **0** |

Read the last two columns before quoting the union. *index-scoreable* is how
many rows carry a measurable threshold at all — the other 175 are
`Qualitative pass` and `verifier.score_run` clamps them to `not_applicable`,
never `pass`. *tenant-verified* is how many have an `AssertionRun` or `Run`
carrying a real `pass`/`fail` against a live tenant: **zero**, because no
assertion has been executed against a tenant yet.

**Authored is not proven.** A binding means an artifact exists that A-17 proved
can go red. It does not mean anyone watched it do so.

## Files

| File | What it is |
|---|---|
| `_v2.2-source/` | the versioned snapshot — 8 tabs of the v2.2 patch pack |
| `VERSION` | version + source spreadsheet id |
| `crosswalk-v2.2.csv` | scenario → index binding, one row per scenario, with rationale |
| `proposed-tc-v2.3.csv` | 16 test cases the index does not yet carry |
| `index-gaps-v2.2.md` | what the crosswalk surfaced that needs a human decision |
| `assertions.md` | the POS/PLT/AUT proof mechanism — artifact schema, the A-* diagnostics, the verdict taxonomy |
| `v2.0-methodology-master.md` | the F1–F10 methodology families (still current) |
| `_archive/v2.0-source/` | the superseded v2.0 export, kept for the re-key trail |

## Regenerating

The crosswalk is hand-authored in `scripts/uctc_crosswalk_v2.2.py` — string
matching was tried and rejected (8 confident matches out of 161: the index
speaks capability language, the corpus speaks adversary language).

```bash
python3 scripts/uctc_crosswalk_v2.2.py --report   # reconciliation summary
python3 scripts/uctc_crosswalk_v2.2.py --emit     # rewrite the CSV artifacts
python3 scripts/uctc_crosswalk_v2.2.py --apply    # rewrite scenario YAMLs (idempotent)
```

The index moves on a human cadence. Export → commit → PR; there is deliberately
no live sync with the source spreadsheet.

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

## Current state

- **161 / 161 scenarios** resolve. Zero S-10/S-11/S-12/S-15.
- **81 of 266** index test cases are evidenced by the corpus (**62 of 107**
  DET/HNT — the detection-backable subset).
- 101 scenarios carry an `S-13` tier disagreement and 13 an `S-14` posture-class
  binding. Both are advisory and both are real — see
  [`index-gaps-v2.2.md`](index-gaps-v2.2.md).

## Files

| File | What it is |
|---|---|
| `_v2.2-source/` | the versioned snapshot — 8 tabs of the v2.2 patch pack |
| `VERSION` | version + source spreadsheet id |
| `crosswalk-v2.2.csv` | scenario → index binding, one row per scenario, with rationale |
| `proposed-tc-v2.3.csv` | 16 test cases the index does not yet carry |
| `index-gaps-v2.2.md` | what the crosswalk surfaced that needs a human decision |
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

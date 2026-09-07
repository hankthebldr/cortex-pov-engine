# UC/TC Full-Coverage Design — a detection/simulation for every v2.2 test case

**Status:** Draft for review · **Date:** 2026-09-04 · **Owner:** Henry Reed (DC Domain Consulting)
**Companion:** [`docs/uc_tc_mapping/sprint-plan-v2.3.md`](../../uc_tc_mapping/sprint-plan-v2.3.md) (the phased roadmap)
**Decision record:** vault `github-projects/cortex-pov-engine/2026-09-04-cortexsim-uc-tc-full-coverage-sprint-plan-decisions-prioriti.md`

## 1. Problem

The FY27 v2.2 index (`CortexUCTCIndexv2_2FINAL.xlsx`, mirrored at
`docs/uc_tc_mapping/_v2.2-source/`) defines **266 test cases** across 49 use
cases. The goal is a specific, engine-backed detection or simulation for **every**
one of them, and — for the many TCs that fire on data only an external platform
produces — detailed guidance on how a DC stands that up.

This document defines the *method*: a deterministic procedure that assigns every
open TC to exactly one closure mechanism, records that choice per-TC, and expands
the log-simulation surface so that "requires an external platform" collapses, for
every detection that fires on ingested logs, to "requires a shape-true emitter."

## 2. Ground truth (reconciled against the live engine)

The Excel's `Engine Coverage` column is a stale snapshot ("231 NO ENGINE
COVERAGE"). The repo rule is that the counted command wins. The live crosswalk
(`scripts/uctc_crosswalk_v2.2.py --report`, 2026-09-04) is authoritative:

| Validation class | Total | Evidenced (live) | Open |
|---|---:|---:|---:|
| DET (detection) | 102 | 63 | 39 |
| HNT (hunt) | 5 | 4 | 1 |
| POS (posture holds) | 110 | ~2 | ~108 |
| PLT (platform present) | 43 | 4 | 39 |
| AUT (outcome in budget) | 6 | 3 | 3 |
| **All** | **266** | **~90 (union w/ assertions ~94)** | **~172** |

Two invariants carry from the repo's honesty rules (CLAUDE.md Gate A5) and are
load-bearing here:

- **tenant-verified is 0** for all 266. No run has executed against a live Cortex
  tenant. `authored` and `proven` are two numbers, never one.
- **A zero is degraded, not ok.** "No artifact authored" and "authored but
  unproven" and "proven" are three distinct states, surfaced distinctly.

The open work is **not** mostly detections. It is ~150 assertion-shaped rows
(POS/PLT/AUT), most of which depend on an external platform. Only ~40 open rows
are DET/HNT.

## 3. The five closure mechanisms

Every open TC closes through exactly one of these. The mechanism is a property of
the TC recorded in the engine binding, not a guess made at authoring time.

| ID | Mechanism | Closes | External platform | Primary artifact |
|---|---|---|---|---|
| **M1** | Identity-harness scenario | DET/HNT keyed on endpoint/process/network causality | none (we generate the signal) | `scenarios/<plane>/*.yml` + TTP card |
| **M2** | Log-sim emitter → connector / BVM / API | DET/HNT/Correlation/ABIOC/IOC keyed on an ingested data source | **simulated, not provisioned** | EAL analytics emitter + scenario |
| **M3** | IaC fixture + POS assertion | POS rows where a posture state must physically hold | **real (provisioned)** | `infra/modules/**` planted finding + `assertions/pos/*.yml` |
| **M4** | Platform-state XQL assertion | PLT rows (capability present / config state) | read-only probe | `assertions/plt/*.yml` (`xql_rows`/`xql_scalar`/`xql_ratio`) |
| **M5** | Automation-outcome assertion | AUT rows (outcome inside a budget) | read-only probe | `assertions/aut/*.yml` with a `settle` block |

M1 and the controllable half of M2 are the only mechanisms with **no external
gate** — they are the quick-win surface. M3 is the only mechanism that genuinely
needs the real platform (a public S3 bucket must actually exist to be observed);
it is deferred behind LaaB approval (§9).

## 4. The decision procedure (deterministic)

Applied to each open TC, in order. The output is a single mechanism plus a
recorded binding row.

```
1. validation_class picks the family:
     DET | HNT  -> step 2
     POS        -> M3
     PLT        -> M4
     AUT        -> M5

2. For DET/HNT, split by SIGNAL ORIGIN (not by the `platforms` label,
   which ~13 scenarios mislabel — classify on the detection's data source):
     detection keys on endpoint process lineage / local network causality
                                    -> M1   (identity-harness scenario)
     detection keys on an ingested data source
       (cloud audit, IdP/SSO, SaaS, email, K8s audit, NGFW EAL, DNS)
                                    -> M2   (log-sim emitter)

3. For M2, classify CONTROLLABILITY (drives sprint sequencing, §6 of the
   sprint plan — NOT a different artifact):
     we own the emitter AND the data shape (network/endpoint: NGFW EAL,
       DNS, C2, signalbench telemetry)          -> M2-quick
     data source is SaaS/O365/identity-cloud and needs live-tenant field
       confirmation                              -> M2-longterm
```

The tie-breakers, made explicit so two authors reach the same answer:

- **A DET row whose only realistic signal is an ingested log is M2, never M1** —
  do not fake endpoint causality for a cloud-audit detection.
- **A POS row is M3 even when a log could *show* the misconfiguration** — POS
  asserts the state *holds*, which a log stream cannot prove. (A detection that
  *fires on* the misconfiguration event is a separate DET row and may be M2.)
- **`is_scoreable: false` does not change the mechanism** — it changes only the
  terminal verdict clamp (PASS → `not_applicable`; FAIL is never clamped).

## 5. The per-TC binding record

The crosswalk already walks `scenario → adapter_ref → pack → artifact` and emits
engine-binding rows. This design extends the recorded shape so the mechanism
decision is durable and machine-readable. Each open TC gains:

```yaml
tc_id: TC-EDR-07
uc_id: UC-EDR
validation_class: DET
mechanism: M1                 # M1 | M2-quick | M2-longterm | M3 | M4 | M5
data_source: xdr_data         # the XSIAM dataset the detection reads (M2/M4/M5)
external_platform: none       # none | aws | azure | gcp | okta | entra | m365 | ...
artifact_path: scenarios/edr/edr-023-*.yml   # or "" while open
authored: false               # an artifact binds this TC
fails_without_fix: false      # the guard demonstrably reddens without the fix
tenant_verified: false        # a live-tenant run recorded pass/fail — SEPARATE
status: open                  # open | authored | proven | blocked(<dep>)
```

`authored`, `fails_without_fix`, and `tenant_verified` are three independent
booleans. Closure (the sprint scoreboard) counts `authored AND fails_without_fix`.
`tenant_verified` is reported in its own column and never folded in.

## 6. Log-simulation coverage expansion (the scalable lever)

Current state: one streamer spine (`core/eal_simulator/analytics_emitter.py`) plus
~14 data-source emitters (`*_emitter.py`), delivery-accounted (only a 2xx from the
collector counts as delivered), targeting three delivery paths — an HTTP log
collector (connector), an XSIAM Broker VM, or a direct API. This design makes
**data-source and detection-object coverage a measured target**, against the
Cortex "alerts by data source" catalog
(https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-data-source).

- **L0 — coverage matrix (P0).** Enumerate the catalog. For each data source
  produce a row: `data_source · detector_count (Analytics/BIOC/ABIOC) · emitter?
  · KNOWN_DATASETS? · delivery_paths_proven`. This is the authoritative gap list
  and the source for which emitters to build and in what order.
- **L1..Ln — one emitter per uncovered high-value source.** Each build must prove
  its target detection objects fire on shape-true logs delivered through **all
  three paths** (connector, BVM, API), and register its dataset in
  `KNOWN_DATASETS`. Sequencing follows controllability: network/endpoint sources
  (M2-quick) first; SaaS/O365/identity-cloud (M2-longterm) on the robust track.
- **Delivery honesty is preserved.** The existing `delivery.py` accounting and the
  campaign-level `delivery_verdict` stay in force, so an emitter that POSTs into a
  black hole reports `not_delivered`, never a false green.

The metric this phase moves: **`data_sources_covered / catalog_total`** and
**`detection_objects_exercised / catalog_total`**, both reported in the scoreboard.

## 7. External-platform runbooks (the "detailed guidance" deliverable)

For every external platform the corpus touches (AWS, Azure, GCP, Okta/Entra,
M365/Google Workspace, Proofpoint, Kubernetes, on-prem AD), a runbook under
`docs/uc_tc_mapping/external-platforms/<platform>.md`, each giving:

1. **Which TCs it backs** (the binding rows with this `external_platform`).
2. **Mechanism choice** — M2 (emit shape-true logs, no stand-up) vs M3 (provision
   the real platform), with the reason.
3. **The exact stand-up** — for M2, the emitter + collector/BVM/API config and the
   dataset name; for M3, the IaC module and its planted findings.
4. **Pointing CortexSim at it** — credentials path, preflight, the `POST` target.
5. **What proves the detection fired** — the XQL / alert read-back that turns
   `authored` into `proven`.

The runbook is the artifact a DC reads to reproduce coverage in a customer lab. It
is not marketing; it states plainly where a TC is authored-but-unproven.

## 8. The UC/TC mapping sheet write-back

New crosswalk mode `--emit-xlsx` regenerates a machine-readable **"Engine Coverage
v2.3"** sheet keyed by UC/TC ID, written into a *copy* of the workbook
(`docs/uc_tc_mapping/_v2.2-source/CortexUCTCIndex_v2.3_engine-coverage.xlsx`) — the
DC's original is never mutated. Columns are the §5 binding record, one row per TC,
plus the L0 coverage-matrix rows on a second sheet.

Because it is regenerated from live engine state (never hand-edited), it cannot
drift; a CI check (`--emit-xlsx --check`, fail-closed like the existing export
determinism gate) proves the committed sheet matches the tree. This is the
machine-readable twin of the sprint scoreboard and the answer to "map coverage
back to the index."

## 9. Definition of done, dependencies, and gates

- **Done per TC** = `authored AND fails_without_fix`. The negative control is
  mandatory: an assertion that cannot fail does not load (`A-17`/`A-18`), and a
  scenario's guard must redden without the fix (Gate A5).
- **tenant-verified is a separate 0→N column.** It advances only on a live-tenant
  run. Turning it on is P6, gated on tenant access the repo does not yet have.
- **M3 / posture is LaaB-gated.** IaC fixtures are quick to author but "likely not
  usable yet without more approval from the LaaB team." We author the POS
  assertions on schedule; each such TC sits at `status: blocked(laab)` until
  approval, and the scoreboard shows it as an *external* dependency, not as our
  incomplete work.
- **M2-longterm needs live-tenant field confirmation.** SaaS/O365/identity-cloud
  dataset strings and field shapes must be confirmed against a real ingest before
  those emitters are trustworthy (the existing `RUNBOOK` "BIOC XQL dialects drift"
  caveat). Until then they are authored-but-unproven and labelled so.

## 10. Enforcement / ground-truth loop

- `scripts/uctc_crosswalk_v2.2.py --report` stays the counted ground truth for TC
  evidence; `--emit-xlsx` produces the sheet; `--emit-xlsx --check` guards it in
  CI (new sub-check under the existing `refs` / `detection` jobs — added to an
  existing job, not a thirteenth job that reads like a pass when skipped).
- The `assertions.py` structural guards (`A-17`..`A-24`) and scenario loader codes
  (`S-10`..`S-16`) already fail closed; the mapping sheet inherits their verdicts
  rather than re-deriving them.
- The scoreboard doc regenerates from the same command, so the sprint plan's
  numbers and the sheet's numbers are one source.

## 11. Non-goals / YAGNI

- **No new scorer.** `verifier.score_run` already scores scenarios and assertions;
  everything here binds to that. No parallel scoring path.
- **No write path to Cortex.** `CORTEXSIM_XSIAM_ALLOW_WRITE` /
  `..._ALLOW_DESTRUCTIVE` stay default-off. Every read path (preflight, reconcile,
  Tier-2 verify) stays opt-in and flag-gated.
- **No archive-kind payloads.** The shelf's `TA-08` rejection stands; do not "close
  a gap" by declaring archives no consumer can unpack.
- **No editing the DC's original workbook.** `--emit-xlsx` writes a copy.
- **Not counting `tenant_verified` as closure.** Ever.

## 12. Component isolation (what each unit owns)

| Unit | Does | Depends on |
|---|---|---|
| decision procedure (§4) | assigns one mechanism per open TC | index registry + detection card data source |
| binding record (§5) | durable per-TC mechanism/status | crosswalk |
| `--emit-xlsx` (§8) | regenerates the coverage sheet | binding record + live engine state |
| L0 matrix + emitters (§6) | measured log-sim coverage | analytics streamer + delivery accounting |
| runbooks (§7) | DC-facing stand-up guidance | binding record (which TCs, which platform) |
| scoreboard (sprint plan) | human-readable phase progress | crosswalk `--report` |

Each is testable and replaceable without touching the others: the emitters do not
know about the sheet, the sheet does not know about the runbooks, and all three
read the one binding record.

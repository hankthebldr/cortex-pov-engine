# CortexSim alongside the SOC Framework — design

**Status:** approved by Henry 2026-11-11 · branch `docs/docs-socfw-coexistence` (from dev `ef3576b`)
**Related:**
- `docs/superpowers/specs/2026-11-10-pov-content-pack-design.md`. The prerequisite registry
  (`spec/dataset_requirements.yml`) is already on dev.
- `docs/superpowers/specs/2026-11-10-collector-targets-design.md`.
- PR #184 (persistence grouping chains).
- PR #164 (reconcile accepts the tenant credential), merged to dev at `7fd77a8`.
**tenant-verified: 0.** Nothing here has run against a live tenant. Authored is not proven.

## 1. Why

During a POV the DC installs and runs the Palo-Cortex **SOC Framework**
(`Palo-Cortex/secops-framework`, installed with `Palo-Cortex/povkit`). The customer evaluates
it alongside detection, so CortexSim has to treat it as part of the environment. It is not
noise to route around.

The framework is live on deathstar. These facts were read from the tenant on 2026-11-10/11, read-only:

- **Auto Triage seals cases.** A JOB runs every 10 minutes. It closes cases that are still New
  and unstarred, are at least 6 h old by `creation_time`, and have an `aggregated_score` of at
  most 40, writing `resolved_other`. It ignores severity: critical case 51863 was closed this way
  (`SOCAutoTriageScoreFilter.py:17-19, 212-260`, framework 3.31.x).
- **XSIAM reopens a resolved case for a matching new issue only when the case is
  *Resolved - Auto Resolved*, and only within 6 h of its last grouped issue.** A
  `resolved_other` case never reopens, so the later stages of a chain open a new case
  (Cortex docs, "How to resolve a case").
- **Low-severity issues never create or join a case.** CortexSim's NGFW case (IP-keyed,
  `192.168.1.106`) and XDR case (hostname-keyed, `pov-target-01`) did not merge. The beacon is
  the causality root (CGO) for every run on a host: one `actor_causality_id` across runs.

**What success looks like:** a CortexSim chain lands in **one native XSIAM case**. When it
doesn't, the DC can see why. The framework's own work on that case is measurable, and its
absence reads as degraded, never as a detection miss.

## 2. Goals, non-goals, anti-goals

**Goals**

- **G1 Know the response layer.** Before a run, CortexSim knows whether the framework is
  installed, which version and mode, and how Auto Triage is set.
- **G2 Report merge and disposition per run.** Which cases the run's alerts landed in, why
  they split if they did, and what happened to each case.
- **G3 Make CortexSim's signal mergeable.** Every scenario's Medium-and-above detections share
  a native grouping key, and a lint enforces it.
- **G4 Observe the framework.** Report what the SOC Framework did with each attack as evidence,
  never as pass/fail (§10 decision 3), plus one scored scenario for Cortex's own grouping.
- **G5 Declare the dependency.** A missing framework reads as degraded, never as "Cortex missed it".

**Non-goals**

- Writing to the tenant: no starring, no `SOCFWConfig`, no list edits, no pack install (povkit
  owns install). CortexSim stays read-only toward Cortex; `CORTEXSIM_XSIAM_ALLOW_WRITE` stays off.
- Changing or forking the framework. Defects go to its owners.
- Using the framework's modelled analyst-minutes as evidence.
- Fixing the shared CGO across runs on one host. This design reports it (§4.2); it doesn't fix it.

**Anti-goals** (failure even if it "works")

- Disposition or merge class feeding detection verdicts: `detected`/`missed`, MTTD, `tc_verdict`.
- Inferring a closure cause from free text that isn't in a closed, version-pinned vocabulary.
- Claiming "merged" from the `host_incidents` basis, which counts other runs' alerts.
- Reading an unrecognised framework config shape as "absent" or "defaults". It raises, and
  the result is degraded with a code.
- Scoring the framework as FAIL from executor `phase_step` rows, or from the absence of rows.
  The framework writes no row for a skipped action.

## 3. Phasing

| Phase | Delivers | Depends on |
|---|---|---|
| **0 Works alongside** (minimum) | G1, G2 (§4) | nothing open (PR #164 merged) |
| **1 Mergeable signal** (lint only, §10 decision 1) | G3 (§5) | nothing new |
| **2 Framework observations** | G4 (§6) | Phase 0, the xql capability (D-18, granted on deathstar) |
| **3 Declared dependency** | G5 (§7) | the registry on dev |

Each phase is its own branch and Gate A PR. Phase 1 can run in parallel with Phase 0.

## 4. Phase 0 — works alongside

### 4.1 Response-layer probe (read-only, opt-in, metered)

- **Placement.** A new stage `response_layer` on `POST /api/connectors/xsiam_tenant/preflight`,
  after `clock`. It reports; it never blocks the earlier stages. Like every stage it carries
  `queries_issued`.
- **Sources, in order.** Each source is recorded in `evidence.sources_used`.
  1. **Installed packs:** `GET /xsoar/contentpacks/metadata/installed`. This path is povkit's
     observation (`internal/tenant/api.go:26`), not a CortexSim verification. Gives the versions
     of `soc-optimization-unified`, `soc-framework-nist-ir`, `soc-framework-nist-ir-ai` and the
     `soc-*` / `SocFramework*` vendor packs.
  2. **List names:** `GET /xsoar/lists/names` (povkit `api.go:85`). Gives the presence of
     `SOCFWTenantConfig` and `SOCFWConfigModes`.
  3. **List content.** The read path is unverified, and the builder confirms it. With no read
     path, skip to (4) and report `PRESENT_UNREAD`.
  4. **XQL, last 24 h,** over `xsiam_socfw_ir_execution_raw`: counts by `action_taken` ×
     `execution_mode`. An `auto_triage_closed` row with mode `production` proves triage is live
     even when the lists can't be read.
- **New read-class operations** go in `core/integrations/xsiam/operations/packs/`, through the
  existing client, ledger and quota. No write op is added.
- **Output** (shape decided; field names are the builder's):

```yaml
response_layer:
  state: ABSENT | PRESENT_UNREAD | PRESENT
  framework_version: "3.31.4"          # soc-optimization-unified
  packs: [{id, version}]
  mode: pov | star_and_triage | …      # null when unreadable
  auto_triage: {live: true, window_hours: 6, score_threshold: 40,
                source: tenant_config | job_default | xql_evidence}
  ai_close: live | shadow | unknown
  code: RESPONSE_LAYER_OK | RESPONSE_LAYER_ABSENT | RESPONSE_LAYER_UNREAD
        | RESPONSE_LAYER_SHAPE_UNRECOGNIZED
```

- **An unrecognised shape raises** `RESPONSE_LAYER_SHAPE_UNRECOGNIZED`. It is never read as defaults.
- **`ABSENT` is degraded, not informational.** Henry's instruction is that the DC installs and
  operationalizes the framework in every POV, so a POV tenant without it is a setup gap. The
  stage reports `degraded`, and the preflight `overall` follows.
- **The last probe is stored.** The `response_layer` result is persisted with `read_at` on the
  tenant integration record (the builder picks the column). The launch advisory (§4.3) reads it,
  and a result older than 24 h reads as "unknown — run preflight". *(My call.)*
- **Readiness** shows it as a row under the connector ladder, not a fifth rung, with "proves" /
  "does not prove" lines like the existing rungs. It proves the framework is configured; it
  does not prove the framework handled any CortexSim case.
- **`/api/health`** makes zero outbound calls, so it only adds `response_layer` to
  `not_checked[]`, naming the preflight endpoint.

### 4.2 Run case ledger: merge and disposition

- **Input.** The `case_ids` already stamped on matched alerts (`ObservedAlert.incident_id`;
  `connectors/xsiam.py:416`). Only the `alert_incident_ids` basis is used. With
  `host_incidents` the ledger reports `PENDING`, never a merge claim.
- **Read.** One `OP-CASE-SEARCH` per sweep per run, filtered to those case ids. Fields read:
  `status_progress`, `resolve_reason`, `resolve_comment`, `resolved_timestamp`, `starred`,
  `assigned_user_mail`, `issue_ids`, `creation_time`, `modification_time`, `xdr_url`.
- **Persist.** On `Run` as a new nullable JSON column `case_ledger`, with a prod
  `ALTER TABLE runs ADD COLUMN case_ledger JSON`. Same pattern as `cgo_anchor`.
- **Issue read** *(my call, from the builder check)*. In `pov` mode the framework's AI closes
  *issues* live, and a case whose issues are all closed may then show *Auto Resolved*. Reading
  only cases would credit the framework's most consequential action to the platform. So the
  sweep also reads the run's matched issues with `OP-ISSUE-SEARCH`, filtered by `id`, taking
  `status.progress`, `status.resolution_reason` and `status.resolution_comment`. An id-filtered
  read of issue 1500462 on deathstar returned those fields on 2026-11-10.
- **Per-case fields:**
  - `closed_by` uses a closed vocabulary: `open`, `socfw_auto_triage`, `auto_resolved`,
    `other_resolved`. There is no `analyst` value, because CortexSim can't know who closed a case.
    - `socfw_auto_triage` matches the framework's `RESOLVE_COMMENT` prefix. That string lives in
      one module with the framework version it was copied from (`SOCAutoTriageScoreFilter.py:18`),
      and a test pins it.
    - `auto_resolved` means the reason is *Resolved - Auto Resolved*. It names the status, not
      who caused it. When every issue in the case was closed first, the record says so.
    - `other_resolved` covers any other reason.
  - Issues use the same vocabulary, plus `socfw_ai_close` when the framework's close notes carry
    a stable marker. The builder verifies whether such a marker exists; without one, those
    issues are `other_resolved`.
  - `sealed` = resolved, and not *Auto Resolved*.
  - `issues_from_run` and `issues_other` record how many issues in the case CortexSim matched to
    this run and how many it didn't.
- **One `merge_class` per run:**

| `merge_class` | Condition |
|---|---|
| `ONE_CASE` | Every matched Medium+ alert is in one case |
| `SPLIT_SEALED` | More than one case, and an earlier case of the run was sealed (`resolved_timestamp`) before the first alert of a later case |
| `SPLIT_NATIVE` | More than one case, and none was sealed at that point, so native grouping didn't merge them |
| `LOW_ONLY_NO_CASE` | All matched alerts are Low. No case is expected |
| `NO_CASE_YET` | Alerts matched, but none has a case id yet |
| `PENDING` | Fewer than two Medium+ alerts, or the basis isn't `alert_incident_ids` |

  A separate flag, `shared_with_other_runs`, is set when `issues_other > 0`. That covers
  cross-run CGO merges and unrelated issues. It is reported as a count only.
- **Isolation from scoring, with one deliberate exception.** The ledger never touches `Result`,
  outcome, MTTD or a detection-family `tc_verdict`. The exception covers the 22 scenarios that
  score Cross-Source Correlation Rate. That KPI is built from the same case ids, so a seal caused
  by triage would otherwise read as a correlation miss.
  - **The rule** *(my call)*: when `merge_class = SPLIT_SEALED`, `guard_measurement` withholds a
    correlation **FAIL** as `pending`, with the reason "a case was sealed by the response layer
    before the chain finished — the split is not the detection engine's". This follows the
    existing withheld-on-truncation precedent.
  - **What stays unchanged:** a PASS is never touched, and neither is any other KPI family.
  - **Tests:** the invariance test asserts `score_run` is byte-identical for every non-correlation
    family and for every non-`SPLIT_SEALED` correlation run. A second test shows the FAIL being
    withheld.
- **Drift.** Disposition changes after the run: triage closes 6 h later. The reconcile sweep
  re-reads ledgers for runs younger than `CORTEXSIM_CASE_LEDGER_WINDOW_HOURS` (default 24),
  inside the existing per-sweep query cap. `read_at` is always shown.
- **Where sealing actually splits a chain.** Most runs finish in minutes. Inside one run, a split
  can only come from a late match: ABIOC or Analytics alerts match within a 24 h window, so they
  can arrive after a 6 h seal. Across a **paired run**, the block half can land after the report
  half's case was sealed. So `merge_class` is also computed **per pair**, across both halves,
  and exposed through the existing `GET /api/runs/{id}/pair`. *(My call.)*

### 4.3 Launch advisory

When the stored probe (§4.1) says Auto Triage is live, launch preflight adds the advisory
`SOCFW_TRIAGE_MAY_SEAL` *(triggers are my call)*:
- at **warn** level when the run is half of a paired run;
- at **info** level when the scenario expects ABIOC or Analytics detections, whose 24 h match
  window outlives a 6 h seal. That is about half the corpus, which is why it's only info.

The remediation names the three tenant-side options:
1. star the case;
2. `!SOCFWConfig command=triage` with a longer window for the POV;
3. run triage with `dry_run`.

The advisory never blocks a launch, and CortexSim never acts on it.

### 4.4 Surfaces

- **Run view:** a **Case** panel. It shows case links (`xdr_url`), `merge_class` with its reason
  line, and a disposition badge per case, plus a fixed line: *"Tenant disposition is shown for
  context. It is not detection evidence."*
- **Report export (markdown):** the same section, with the same line.
- **MCP:** the existing run-read tool carries `case_ledger`. Exact exposure is the builder's call.

## 5. Phase 1 — mergeable signal

- **5.1 Grouping-key model.** `core/engine/grouping_keys.py` holds the native alert-field
  destinations XSIAM groups on (host, user, file hash, IP, and so on). The list is ported from
  the framework's `tools/correlation_rule_grouping_check.py` `ENTITY_ALERT_FIELDS`. That repo is
  MIT, so the notice is kept in the file header.
- **5.2 Per-scenario key analysis.** For each step's expected detections, derive the set of keys
  and the severity:
  - correlation rules: the destinations in `alert_fields`;
  - BIOC, XDR agent and analytics alerts: host and user, implicitly;
  - EAL-emitted third-party alerts: the emitter's mapped fields.

  The result is a `shared_key` across the Medium+ steps, or `none`.
- **5.3 Lint** with the next free scenario code (S-29 at the time of writing; confirm on dev):
  - **WARN**: two or more Medium+ step-detections share no native key.
  - **WARN**: a step inside `stitching_key` / `required_planes_in_incident` carries only Low
    detections ("Low never forms or joins a case").
  - **ERROR**: a card's correlation rule maps the declared `stitching_key` to a custom
    (non-native) destination.

  It also adds `make check-grouping`, wires it into CI's `refs` job (not a new job), and adds
  the counts to the ground truth.
- **5.3a The data mostly isn't there yet.** Measured on dev `ef3576b`:
  - 0 of 193 cards carry `alert_fields` on any correlation rule;
  - 0 scenario `expected_detections` declare a severity;
  - only the BIOC and ABIOC card objects carry a severity;
  - EAL emitters post raw logs, not alerts, so they have no alert-field map.

  So the lint emits an explicit **`GROUPING_KEY_UNKNOWN`** outcome per step whenever the key or
  severity can't be derived. It never skips silently and never assumes Medium+. The ERROR above
  stays dormant until cards declare `alert_fields`. Phase 1 is the lint only (§10 decision 1).
  The ground truth publishes the unknown count, so the size of the later authoring program is
  visible.
- **5.3b IP and host keys.** An IP-keyed step and a host-keyed step don't count as sharing a key,
  but they aren't a WARN either. They get the separate outcome `BRIDGEABLE_IP_HOST`, which §5.4
  turns into a preflight check. *(My call.)*
- **5.4 Bridge preflight.** For runs that mix IP-keyed alerts (NGFW, EAL) with host-keyed ones
  (XDR), preflight checks that the target's IP is on the XDR endpoint record. If it isn't, it
  warns `UNBRIDGED_IP_HOST`.
- **5.5 Cross-run sharing.** Runs on the same host share the beacon CGO. The ledger flags this
  (§4.2), and the runbook (§7) tells the DC.

## 6. Phase 2 — observing the SOC Framework, plus one grouping scenario

The framework checks are **response-layer observations**, never pass/fail (§10 decision 3).
Each records a measured value, its evidence and a reason code. They are not bound to the UC/TC
index and never counted in coverage, and they appear in the run view and report under their own
heading. They read the framework's datasets through Tier-2 XQL (the xql capability, D-18) and
reuse the assertion engine's probes. Whether that means a new `observation` artifact type or
assertions with a non-scoring flag is the builder's call. Either way, nothing reaches
`tc_verdict`.

The one scored item is SIM-MP-0xx, because it measures *Cortex's* grouping, not the framework.

**Reading rules.** These come from the 2026-11-10 review and are binding:

- Read wrapper rows only, never `phase_step` rows. The executor marks a step `dispatched`
  without reading the result.
- `execution_mode` is `shadow` or `production`. A "did" needs `production` and `has_error = false`.
- A missing row resolves `pending`, not `fail`.
- Scope by the run's `case_ids` from §4.2, never by hostname plus window.

**Observations.** Each one resolves to `observed`, `not_observed` or `pending`, with evidence.
`not_observed` needs positive evidence, exactly as a FAIL would.

| id | Observes | Resolves |
|---|---|---|
| OBS-SOCFW-INGEST | Did the framework take in the attack? | `observed`: every case of the run has a `xsiam_socfw_case_ledger_raw` row within N min. `not_observed`: a case still has no row after the settle window |
| OBS-SOCFW-TRIAGE | Did Auto Triage close the attack's case? | `observed (closed)`: the case is `socfw_auto_triage`. `not_observed (left open)` needs both: the stored probe says triage was live, and the case is still open at `window_hours` + one 10-min cycle + margin. Otherwise `pending` *(my call)*. Reference shape: case 51863 |
| OBS-SOCFW-AI-CLOSE | Did the AI close an issue from a CortexSim attack? This is live in `pov` mode | `observed`: a matched issue is `socfw_ai_close`, with the verdict and confidence from its `ai_close_decision` row. The framework's close guard blocks only `malicious`, so this is the observation a DC most needs to see |
| OBS-SOCFW-CONTAIN | Would containment have fired? | A wrapper row for the expected containment command on the run's incident within budget. "Would have" (`shadow`, the `pov` default) and "did" (`production`) are reported separately |

**Scored scenario.**

| id | Scores | Negative control |
|---|---|---|
| SIM-MP-0xx | An email-to-endpoint chain: EAL Proofpoint TAP plus endpoint, mirroring the framework's `grouping_*` scenarios. It scores on **Cross-Source Correlation Rate = 100 %**, not on `merge_class`, which stays out of verdicts *(my call)* | Alerts split across cases |

## 7. Phase 3 — declared dependency

- **Registry.** Add `xsiam_socfw_ir_execution_raw` and `xsiam_socfw_case_ledger_raw` to
  `spec/dataset_requirements.yml` (on dev). The Phase 2 observations then *derive* the
  prerequisite; it is never hand-authored per observation. With the framework absent, they
  resolve `pending` with `RESPONSE_LAYER_ABSENT`, never `not_observed`.
- **Runbook.** `docs/lab/` gets "Run CortexSim alongside the SOC Framework":
  1. install with povkit, in `pov` mode;
  2. run CortexSim preflight (`response_layer`);
  3. **protect POV cases from Auto Triage**, a required step (§10 decision 2): star the POV's
     cases, or set a POV triage window with `!SOCFWConfig command=triage`;
  4. run;
  5. read the Case panel and the response-layer observations.

  The runbook also says plainly that, in `pov` mode, the framework's AI may close an attack's
  issue live, and that CortexSim reports this but doesn't prevent it.

## 8. Constraints

- **Read-only, always.** It uses the existing `xsiam_tenant` credential. The builder must confirm
  that its role can GET the `/xsoar` paths. A 403 means `PRESENT_UNREAD`, not `ABSENT`.
- **The framework moves daily** (patch bumps on every merge). Every string and shape CortexSim
  reads is pinned to a named framework version in one module. Unrecognised input raises.
- **Quota.** All new reads go through the ledger, the per-sweep caps and the circuit breaker.
- **Timestamps** are compared with the platform's own fields, never wall-clock. Coerce with
  `connectors/base.py::coerce_utc`.

## 9. Calls made in this design, and what's left

**Calls I made** (check these):
- the phase order, and read-only throughout;
- disposition never counts as evidence, **except** that `SPLIT_SEALED` withholds a correlation
  FAIL as pending (§4.2);
- the issue-status read in Phase 0, and the neutral name `auto_resolved`;
- `merge_class` computed per run and per pair;
- the closed vocabularies, and the ledger as a JSON column on `Run`;
- the lint as WARN, plus `GROUPING_KEY_UNKNOWN` and `BRIDGEABLE_IP_HOST`, with the
  custom-destination ERROR dormant until cards carry `alert_fields`;
- `ABSENT` degrades preflight, and the probe result is stored with `read_at`;
- the advisory's warn and info triggers;
- the positive-evidence rule for OBS-SOCFW-TRIAGE, and SIM-MP-0xx scoring on correlation rate;
- Readiness as a row, not a rung.

**Left to the builder:** UI placement and copy, op file naming, XQL text, reason-code spelling,
MCP exposure, and the list-content read path (§4.1 step 3).

## 10. Decisions (Henry, 2026-11-11)

1. **Phase 1 is the lint only.** It reports `GROUPING_KEY_UNKNOWN` honestly. Authoring severity
   and `alert_fields` across the corpus is a separate, later program that this design does not
   schedule.
2. **A DC runbook step protects POV cases from Auto Triage.** The DC stars POV cases, or sets a
   POV triage window with `!SOCFWConfig command=triage`. CortexSim's launch advisory (§4.3)
   points at that runbook step and never acts itself.
3. **Framework-side results are observations only.** Every Phase 2 check about the framework
   reports a measured value and its evidence, never pass/fail. It is not bound to the UC/TC
   index and never counted in coverage (§6).
4. **DCs run the framework in `pov` mode.** Starring, benign AI close and analyst assignment run
   live; Auto Triage is on; containment is recorded in shadow (`SOCFWConfigModes` `pov`). Two
   consequences:
   - The Phase 0 issue read is essential, because the AI closes issues live.
   - Containment observations can only ever show "would have" (shadow rows) unless the DC
     turns a containment action live.

## 11. Verification

- **Unit tests:**
  - a classifier table for `merge_class`, including the sealed-before-next-alert timing edges;
  - a pin test on the framework marker string;
  - the `score_run` invariance test;
  - an unrecognised-shape test that raises.
- **Gate A:** each new guard is shown failing without its fix.
- **Lab, which proves the disposition read rather than a split:** one SIM-EDR-023 run on
  `pov-target-01` with Auto Triage live. Read the ledger at T+0 and T+7 h. Expected:
  - at T+0, `ONE_CASE`, with the case `open`;
  - at T+7 h, the case `socfw_auto_triage` and `sealed`. It qualifies: high severity, score 6, ≤ 40.

  The split paths (`SPLIT_SEALED` within a run or across a pair) are proven by unit tests on the
  classifier until a paired run is spaced past the window in the lab. SIM-EDR-023 finishes in
  minutes, and its cron foothold expires after 45 min, so it can't produce a late alert on its own.

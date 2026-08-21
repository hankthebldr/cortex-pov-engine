# Assertions — the authoring contract for POS / PLT / AUT

A **scenario** proves a detection fired. That covers the index's DET/HNT rows and
nothing else. **140 of the 182 unevidenced test cases are POS, PLT or AUT** —
a posture finding must be *discovered*, a platform capability must be *present*,
an automation outcome must *occur inside a budget*. None of those is a
detection, and authoring 140 more attack scenarios would move the coverage
number without proving anything.

An **assertion** is the artifact for those three classes. One artifact type, one
loader, one scoring path, one verdict vocabulary.

| | Scenario | Assertion |
|---|---|---|
| File | `scenarios/{plane}/*.yml` | `assertions/{pos,plt,aut}/*.yml` |
| Proves | a detection fired | state / capability / outcome |
| Runtime | `Scenario` → `Run` → `Result` | `Assertion` → `AssertionRun` → `AssertionCheck` |
| Index FK | `S-10`..`S-16` | `A-10`..`A-16` — **the same code path** |
| Scored by | `verifier.score_run` | `verifier.score_run` — **the same function** |
| Verdict | `pass \| fail \| pending \| not_applicable` | identical |

---

## 0. The three rules, before anything else

**1 · An assertion that cannot fail does not load.** Every check's threshold is
proven falsifiable *and* satisfiable at load time — the loader constructs
measurements across the probe's own measurement domain, pushes them through the
real evaluator, and rejects the artifact if none of them produces `fail`
(`A-17`). Your declared negative control is separately proven to actually
evaluate `fail` (`A-18`). Neither is relaxable by `CORTEXSIM_STRICT_REFS`.

This is why `expected_rows_min: 0` cannot be authored here. `>= 0` on a row count
is satisfied by every value a count probe can produce, so it is rejected with
*"this check can never fail and therefore proves nothing"*.

**2 · Without a tenant, nothing is green.** No integration, an unreachable
tenant, a quota error, a dry run — all resolve `pending`. `pending` means *"this
claim is unproven and still owed"*. `not_applicable` means *"this claim can never
be scored"* and is reserved for two things only: the index carries no measurable
threshold for the bound test case, or the tenant is not entitled to the product.
**Do not conflate them.** Reporting unproven claims as N/A lets 92 open gaps
disappear into a bucket that reads as benign — the single worst outcome
available here.

**3 · Never bind a test case the engine cannot prove.** If it needs a human, a
product the engine cannot reach, or a write operation, say so and list it. A
binding that exists to move a number is worse than an honest gap.

---

## 1. File layout

```
assertions/
  pos/POS-CSPM-001-planted-misconfig-discovery.yml
  plt/PLT-IR-008-broker-vm-heterogeneous-ingest.yml
  aut/AUT-AEPS-002-autonomous-containment-mttr.yml
```

Any `.yml` / `.yaml` under `assertions/` is loaded, recursively. Filenames
starting with `_` are skipped (so `_schema.yml` and notes are safe). The
directory is optional: a deployment without it boots exactly as before.

`assertion_id` must match `^(POS|PLT|AUT)-[A-Z0-9]+-\d{3}$` and its prefix must
equal `validation_class`.

---

## 2. The complete worked example

`assertions/plt/PLT-IR-008-broker-vm-heterogeneous-ingest.yml`

```yaml
# ─── identity ────────────────────────────────────────────────────────────────
assertion_id: PLT-IR-008
name: Broker VM heterogeneous ingest and normalization
version: "1.0"
status: active                    # active | draft | deprecated

validation_class: PLT             # POS | PLT | AUT — must equal the index row's
kind: state                       # state | outcome  (see §4)
plane: ANALYTICS                  # optional, free text

# ─── index binding — the SAME validated FK scenarios use ─────────────────────
uc_ref: UCS-IR-04
tc_ref: TC-IR-08
tc_refs: [TC-IR-08]               # defaults to [tc_ref]; always contains it
uc_name: "Incident Response"
tc_name: "Validate ingestion and normalization of 3+ heterogeneous sources"

moat_tier: MOAT                   # advisory; A-23 warns if it fights the index
methodology_family: F1
primary_kpi: "Detection Accuracy"
success_criteria: |
  Three structurally dissimilar sources land AND normalize into one queryable
  schema, retrievable by a single canary token in one union query.

# ─── MANDATORY: what this does NOT prove (A-19) ──────────────────────────────
# Rendered beside every verdict, in full, never collapsed. A partial proof that
# hides its edges is a false claim.
scope_limitations: >
  Proves three structurally dissimilar sources land AND normalize into one
  queryable schema. Does NOT prove the records traversed a Broker VM
  specifically — the operator supplies the collector URL, which may be a Broker
  VM HTTP applet, a native cloud collector, or a forwarder.

# ─── what must have happened first (declarative; the engine does not run it) ──
stimulus:
  kind: eal_campaign              # none | scenario_run | eal_campaign | iac_fixture
  campaign_id: heterogeneous-ingest-3src
  detail: >
    Run the k8s_audit, m365_activity and ngfw_eal emitters against the
    operator's collector with the same canary in each record, then supply that
    canary as context.canary below.

# ─── template values the author may use as {{...}} in queries ────────────────
# Builtins are always available: nonce, run_id, lookback_seconds.
context_vars: [canary]

# ─── the checks ──────────────────────────────────────────────────────────────
checks:
  - check_id: chk-01
    title: Each source lands in its own normalized dataset
    probe: xql_distinct
    primary: true                 # this check's measurement is the run's KPI
    weight: 1.0
    params:
      query: |
        dataset in (kubernetes_audit_logs, msft_o365_audit, panw_ngfw_traffic_raw)
        | filter _raw_log contains "{{canary}}"
        | comp count() as records by _dataset
      distinct_key: _dataset
    threshold:
      kpi: sources normalized
      op: ">="                    # <= ≤ >= ≥ < > = == !=
      value: 3
      unit: ""
      source: authored_sharpening # index | authored_sharpening
      rationale: >
        The index scores TC-IR-08 "Qualitative pass". "3+ heterogeneous sources"
        is objectively countable, so the assertion sharpens it to a machine-
        evaluable floor of 3 rather than reporting not_applicable.
    # MANDATORY (A-18): the named condition that turns this red, and the
    # measurement that condition produces. Proven at load to evaluate `fail`.
    negative_control:
      description: >
        Only the k8s and M365 parsers map the canary; the NGFW records land raw
        and never normalize, so the union query spans two datasets, not three.
      measured_value: 2

  - check_id: chk-02
    title: Every source's records are retrievable by the canary
    probe: xql_rows
    params:
      query: |
        dataset in (kubernetes_audit_logs, msft_o365_audit, panw_ngfw_traffic_raw)
        | filter _raw_log contains "{{canary}}"
        | limit 200
    threshold:
      kpi: canary records retrieved
      op: ">="
      value: 3
      unit: rows
      source: authored_sharpening
      rationale: one record per source is the floor for a three-source claim
    negative_control:
      description: the collector accepted the POSTs but nothing reached the lake
      measured_value: 0

tags: [ingestion, normalization, broker-vm]
author: "Henry Reed"
```

Run it:

```bash
# preview — renders the exact queries, touches no tenant
curl -sX POST localhost:8888/api/assertions/PLT-IR-008/run \
  -H 'content-type: application/json' \
  -d '{"dry_run": true, "context": {"canary": "CSIM-a3f19c2b"}}' | jq

# measure — read-only, credential-backed
curl -sX POST localhost:8888/api/assertions/PLT-IR-008/run \
  -H 'content-type: application/json' \
  -d '{"dry_run": false, "integration": "acme-xsiam",
       "context": {"canary": "CSIM-a3f19c2b"}}' | jq
```

---

## 3. Probes

A probe is a **read-only** measurement. It returns a number; the evaluator
compares it to your threshold. No probe may ever write to a tenant.

| Probe | Measures | Domain | Required params |
|---|---|---|---|
| `xql_rows` | how many rows the answer has | `count` | `query` |
| `xql_distinct` | how many DISTINCT values of one field | `count` | `query`, `distinct_key` |
| `xql_scalar` | a number the tenant itself computed | your `domain` | `query`, `field` (+ `aggregate`, `domain`) |
| `xql_ratio` | numerator ÷ platform-defined population, as % | `percent` | `numerator_query`, `population_query` (+ `min_population`) |
| `xql_latency` | outcome_ts − precursor_ts, in the platform's clock | `seconds` | `precursor_query`, `outcome_query`, `precursor_ts_field`, `outcome_ts_field` |

`GET /api/assertions/probes` publishes this live, including each probe's
`failure_conditions`.

### Three probe rules worth internalising

**Do not put the threshold in the query.** A query ending
`| filter sla_seconds <= 300` returns zero rows for a tenant that remediated in
412 s — indistinguishable from a tenant that never remediated at all, and the
customer never learns the number. Return `sla_seconds` with `xql_scalar` and let
the evaluator compare. The readout then says *"412 s against a 300 s budget — the
loop closed, it was late"*, which is a completely different conversation from
*"nothing happened"*.

**Measure latency in the platform's clock.** `xql_latency` subtracts two
timestamps the tenant itself wrote. Never wall-clock: `now() − run.started_at`
folds agent dispatch, emitter POST latency, collector buffering and ingest delay
into the platform's response time and over-reports it by minutes, manufacturing
false red against a platform that met the bar.

**A ratio needs a population the platform defined.** `xql_ratio` takes a
`population_query` and refuses to compute anything when it returns fewer rows
than `min_population` (`POPULATION_EMPTY` → `pending`). Zero of zero is 100 %,
and reporting that green is the purest form of the inflation this substrate
exists to prevent.

### `xql_scalar` domains

`domain` tells the loader what numbers the probe can physically produce, which
is what makes the falsifiability proof honest. Pick the one that matches:

| domain | bounds | use for |
|---|---|---|
| `count` | `[0, ∞)` | rows, records, distinct entities |
| `seconds` | `[0, ∞)` | latency, SLA, dwell |
| `percent` | `[0, 100]` | coverage, accuracy, automation rate |
| `ratio` | `[0, 1]` | normalized scores |

---

## 4. `state` vs `outcome`

`kind: state` — idempotent. Probe standing platform or posture state; runnable
at any time with no causal setup. Almost all POS and most PLT.

`kind: outcome` — a causally-raised condition must have provoked an action
inside a latency budget. Requires a `settle` block:

```yaml
kind: outcome
stimulus:
  kind: scenario_run
  scenario_id: SIM-MP-020
settle:
  ingest_floor_seconds: 120     # nothing is readable before this
  poll_interval_seconds: 15
  max_wait_seconds: 900
  expect_by_seconds: 300        # the bar
```

`A-22` rejects `max_wait_seconds < expect_by_seconds + ingest_floor_seconds` —
an assertion that stops looking before the budget expires manufactures false red.

**Outcome assertions must use `xql_latency` for the timing claim**, because its
precursor query is what makes `fail` legitimate. You may only report `fail` when
you can positively prove the trigger existed and the outcome did not. No
precursor rows → `PRECURSOR_MISSING` → `pending`, because the failure is then a
*detection* failure, not an automation failure, and painting the automation red
for it is wrong.

---

## 5. Thresholds

Structured only. `evaluate_threshold` correctly refuses raw strings, and the
index only carries prose (`"Qualitative pass"`, `"<60 second"`, `">95%"`).

- `source: index` — you transcribed the index's own number. `A-16` rejects this
  when the index row is unscoreable; you may not claim the index set a number it
  did not set.
- `source: authored_sharpening` — you set a number the index left as prose. A
  `rationale` is **required**. Sharpening is legitimate when the thing measured
  is objectively countable ("3+ heterogeneous sources" → `>= 3`). Where no
  defensible objective measure exists, do not author the check.

`A-15` rejects a sharpened bar that is **looser** than the index's. If the index
says `<60 second` you may author `< 30 s`; you may not author `< 90 s`.

### The qualitative clamp — read this before reporting coverage

91 of the index's 110 POS rows and 57 of its 107 DET/HNT rows carry
`Qualitative pass`. For those, per-check `kpi_verdict` stays a real `pass`/`fail`
— you genuinely measured something — but the **run-level** `tc_verdict` clamps
`pass` → `not_applicable` with *"this is evidence, not a scored pass"*.
`fail` is never clamped: you can disprove a qualitative claim, you just cannot
machine-certify that it passed.

So the honest readout for most POS work is *"9 of 9 planted findings discovered
— the index carries no threshold for TC-CSPM-04, so this is evidence, not a
scored pass"*. **Report that split.** A number that hides it overstates what was
proven.

The far larger return is not more probes: propose one measurable threshold for
the POS family in `proposed-tc-v2.3.csv` ("Planted-Finding Discovery Coverage
≥ 100 % within one scan cycle"). Adopting it converts a dozen `not_applicable`s
into real scored passes with **zero** additional engine work.

---

## 6. Verdict matrix

| Condition | taxonomy code | check verdict | run verdict |
|---|---|---|---|
| dry run | `DRY_RUN` | pending | **pending** |
| no tenant integration registered | `NO_TENANT_INTEGRATION` | pending | **pending** |
| tenant unreachable | `TENANT_UNREACHABLE` | pending | **pending** |
| 401 from the tenant | `PROBE_AUTH_FAILED` | pending | **pending** |
| 429 / XQL quota | `PROBE_QUOTA_EXHAUSTED` | pending | **pending** |
| unknown dataset / bad field / unsafe context value | `PROBE_QUERY_FAILED` | pending | **pending** |
| trigger never raised | `PRECURSOR_MISSING` | pending | **pending** |
| ratio population below `min_population` | `POPULATION_EMPTY` | pending | **pending** |
| tenant lacks the required add-on | `NOT_ENTITLED` | not_applicable | **not_applicable** |
| trigger proven, outcome absent | `OUTCOME_ABSENT` | **fail** | **fail** |
| measured, threshold missed | `MEASURED` | **fail** | **fail** |
| measured, threshold cleared | `MEASURED` | **pass** | **pass**, clamped to `not_applicable` when the index TC is unscoreable |

A tenant or transport error is **never** a capability failure. Conflating them
produces false red in a customer readout, which is worse than reporting nothing.

`entitled_addons` on the run body is the tenant's declared profile. Omit it and
the tenant is treated as entitled — silence must never buy a benign verdict.

---

## 7. Diagnostic codes

Structural codes **always** reject. Index-binding codes are gated by
`CORTEXSIM_STRICT_REFS` (default `true`), exactly as `S-10`..`S-15` are for
scenarios — strictness is about whether the index snapshot is trustworthy in
this deployment, never about whether the artifact is sound.

| Code | Condition | Gated by strict-refs |
|---|---|---|
| `A-01` | YAML unparseable / schema invalid | no |
| `A-02` | duplicate `assertion_id` | no |
| `A-03` | duplicate `check_id` within one assertion | no |
| `A-10` | `tc_ref` not in the index | **yes** |
| `A-11` | `uc_ref` is neither a known UCS group nor a known UC | **yes** |
| `A-12` | `tc_ref` resolves but its parent ≠ `uc_ref` | **yes** |
| `A-13` | a non-primary `tc_refs[]` entry is not in the index | **yes** |
| `A-14` | the bound TC's `validation_class` ≠ the assertion's | **yes** |
| `A-15` | the authored threshold is looser than the index's | **yes** |
| `A-16` | `threshold.source: index` on an unscoreable index row | **yes** |
| `A-17` | **the check can never fail, or can never pass** | no |
| `A-18` | `negative_control` missing, out of domain, or does not evaluate `fail` | no |
| `A-19` | `scope_limitations` blank | no |
| `A-20` | unknown probe | no |
| `A-21` | undeclared `{{placeholder}}` in a query | no |
| `A-22` | settle window shorter than the bar it measures | no |
| `A-23` | `moat_tier` disagrees with the index tier | *warning* |
| `A-24` | probe params invalid for that probe | no |

`A-14` is the **inverted** `S-14`. A scenario binding a POS row is a warning
(a detection cannot satisfy it). An assertion binding a DET row is an **error**:
the index expects authored detection content there, and an assertion would claim
credit for work nobody did.

Rejections are visible at `GET /api/assertions` under `rejected[]`. A guard
nobody can see is not a guard.

---

## 8. Templating and the injection guard

Queries may carry `{{name}}` placeholders. Allowed names are the builtins
`nonce`, `run_id`, `lookback_seconds` plus anything you list in `context_vars`.
Anything else is `A-21`.

Values are whitelist-validated (`^[A-Za-z0-9_.:@/\-]{1,200}$`) *before*
substitution. A value that could reshape the query is refused at runtime and the
check resolves `PROBE_QUERY_FAILED` → `pending`, never a silent bad query.

**Use a run-scoped nonce.** A per-assertion token lets yesterday's record prove
today's run. `{{nonce}}` is minted per evaluation and is the difference between
*"a containment happened"* and *"OUR containment happened"*.

---

## 9. API

```
GET  /api/assertions                 ?validation_class=&tc_ref=&kind=&status=&probe=
GET  /api/assertions/probes          the probe contract, with failure conditions
GET  /api/assertions/{id}            full spec + index row + scope limitations
POST /api/assertions/{id}/run        {dry_run, integration, context, trigger_run_id,
                                      entitled_addons, timeframe_seconds, persist}
GET  /api/assertions/runs            ?assertion_id=&limit=
GET  /api/assertions/runs/{run_id}   checks, rendered queries, taxonomy, remediation
```

`dry_run` defaults **true**. Errors are `{"error","code","detail"}` throughout.

---

## 10. Two things this substrate deliberately does not do

**It does not execute stimulus.** An assertion *declares* its trigger; the
operator runs it and supplies the resulting context. Wiring the orchestrator,
the EAL simulator and Terraform into the proof mechanism would couple it to
three subsystems it does not need, and each of those couplings is a separate
decision with its own failure modes.

**It does not move the coverage number.** `core/api/uctc.py` still counts
evidence from `Scenario` rows only. That is on purpose and it is the ordering
the anti-inflation rule demands: the number cannot move before the thing it
counts has been demonstrated to work against a real tenant. When it is widened,
widen it as two fields, not one:

- `authored` — an assertion exists that binds this TC. Not a claim of proof.
- `proven` — an `AssertionRun` exists whose `tc_verdict` is `pass` or `fail`.
  A `pending` or `not_applicable` run does **not** set it.

Because `A-17` guarantees no artifact can be authored that is incapable of
producing `fail`, a test case can only reach `proven` by way of an assertion
that could have gone the other way. That is the whole point.

---

## 11. Checklist before you commit an assertion

- [ ] `scope_limitations` names something real that this does **not** prove.
- [ ] Every check has a `negative_control` describing a failure a customer would
      actually recognise — and its `measured_value` really evaluates `fail`.
- [ ] No threshold is embedded in a query.
- [ ] Latency comes from `xql_latency`, not wall-clock.
- [ ] Ratios declare `min_population`.
- [ ] `threshold.source: authored_sharpening` carries a rationale that survives
      being read aloud to the customer.
- [ ] The assertion loads: `GET /api/assertions` shows it, and `rejected[]` does
      not mention it.
- [ ] You can state, in one sentence, the tenant condition under which this
      assertion goes red. If you cannot, it does not ship.

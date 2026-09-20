# Implementation plan — XSIAM read-back trust + index honesty

Derived from [`audit-2026-09-20.md`](audit-2026-09-20.md). Three sprints,
ordered so that nothing later can produce a number that the earlier sprint
has not made trustworthy. Every sprint is Gate-A shaped: each change ships
with a test that was observed failing before the fix.

```
 sprint 1 — trust the loop        sprint 2 — widen what it proves     sprint 3 — tell the truth about it
 ┌──────────────────────────┐     ┌──────────────────────────────┐    ┌──────────────────────────────┐
 │ 1a paginate + truncation │     │ 2a Detection Accuracy from   │    │ 3a --emit-xlsx walks         │
 │ 1b host scoping          │ ──▶ │    matched/seeded            │ ─▶ │    assertions/ (28, not 6)   │
 │ 1c source-aware matching │     │ 2b get_incidents → correl.   │    │ 3b regenerate CLAUDE/README/ │
 │ 1d preflight shape probe │     │    rate for stitch scenarios │    │    index-gaps from ground-truth│
 └──────────────────────────┘     │ 2c per-detection rule names  │    │ 3c _evidence_index authored/ │
   ceiling: 6 rows, honest          │    on cards (step N-of-1)    │    │    proven split              │
                                    └──────────────────────────────┘    └──────────────────────────────┘
                                      ceiling: 6 → 14 → +18 rows          every surface shows tenant-verified
```

## Sprint 1 — trust the loop (this pass)

Goal: the alert read-back path cannot silently over-claim or under-claim
coverage on a real, shared, busy tenant. No new outbound surface except one
extra 1-row preflight call. No write path.

| id | change | where | failing-first evidence |
|---|---|---|---|
| 1a | Page `get_alerts_multi_events` (`search_from`/`search_to`, ≤100/page, page cap) and read `reply.total_count` / `result_count`. When the tenant holds more than was returned, `PullResult.detail.truncated` is set and the reconcile summary carries a `warning` naming coverage a **floor**. `"9,999+"`-style capped counts parse. | `connectors/xsiam.py`, `connectors/service.py::reconcile_run` | a 150-alert tenant returns 100 observations before the fix; `truncated` absent |
| 1b | Scope alerts to the run's target host before matching. `reconcile_run` resolves `Run.target → Agent.hostname`; `matcher.scope_to_host` drops alerts that name a *different* host, keeps host-less alerts, and reports both counts. An unscoped run says so in the summary. | `connectors/matcher.py`, `connectors/service.py` | an earlier alert from another host wins MTTD before the fix |
| 1c | Capture the alert's `source` into `ObservedAlert.alert_source`; map it and `Result.signal_type` onto one family vocabulary (`bioc · abioc · analytics · correlation · ioc`); when both families are known and disagree the pair is **not** a match, when they agree `matched_on` gains `source`. `_technique_id` → `findall`, so multi-technique strings keep every id. `matching_service_rule_id` joins the rule-id keys. | `connectors/base.py`, `matcher.py`, `xsiam.py` | a BIOC alert satisfies a Correlation expectation before the fix; `"T1059.001, T1003"` yields one id |
| 1d | Preflight `alert_shape` rung: reuse the scope-probe alert or issue one more 1-row call over a 7-day lookback, then report which of the keys the connector reads are present (timestamp, technique, host, source, rule id), the `source` value, and `total_count`. Missing rule-id key → `degraded` (matching rests on technique/name); no technique and no name → `blocked` (nothing can match); no alert in 7 days → `degraded`, shape unverified. | `connectors/preflight.py` | stage absent before the fix |

Definition of done: `pytest tests/connectors` green with the new tests, the
four tests above observed red on the parent commit, `make -n ci` equivalents
for the backend job pass locally, the audit's §4 items 1–4 and 6 closed, and
this file's sprint-1 table marked shipped with the commit shas.

Deliberately **not** in sprint 1: endpoint version (v1 vs v2 path) — the
shape probe reports what the tenant returns, and a config-driven path is a
sprint-2 decision once one real tenant has answered.

## Sprint 2 — widen what the loop can prove

| id | change | lifts |
|---|---|---|
| 2a | `apply_verdicts` passes `matched / seeded` as `measured_value` into `score_run` for scenarios whose `primary_kpi` is Detection Accuracy. Zero extra tenant calls. | machine-PASS-reachable DET/HNT rows 6 → 14 |
| 2b | `get_incidents` read on the `xsiam` connector, a `CorrelationRate` measured value (`incidents / alerts` collapse ratio) for the 33 correlation/stitch/causality scenarios and the five `UC-IR` rows on the `incidents` dataset. | +18 scoreable DET/HNT rows behind it |
| 2c | Cards carry the tenant-facing rule name per detection object so a step with N expected detections needs N distinct alerts, not one alert N times. | closes the residual of audit item 4 |
| 2d | Alerts endpoint path becomes a credential config field with the v1 default; the shape probe records which path answered. | closes audit item 5 |

## Sprint 3 — tell the truth about it

| id | change |
|---|---|
| 3a | `scripts/uctc_crosswalk_v2.2.py --emit-xlsx` derives assertion bindings from `assertions/**` instead of the two hand dicts; regenerate `scoreboard.md`; add a guard test that the count of artifacts on disk equals the count the sheet reports. |
| 3b | Regenerate the coverage tables in `CLAUDE.md`, `docs/uc_tc_mapping/README.md`, `index-gaps-v2.2.md` from `docs/reference/ground-truth.md`; add those three files to `make check-ground-truth`'s drift set. |
| 3c | `core/api/uctc.py::_evidence_index` returns `authored` (scenario or assertion binds) and `proven` (a `Run`/`AssertionRun` with `tc_verdict ∈ {pass, fail}`) as two fields; console Coverage view renders both and never sums them. |
| 3d | Console Coverage view marks `TC-DLP-11` and every other row carrying ≥ 1 `NET-NEW` crosswalk binding as *parked pending v2.3*, so a `UC-DLP` scope stops quoting Browser / AI-Access / Cloud-App evidence as DLP proof. |

## Owner decisions this plan does not make

- Promote or withdraw the 23 `Draft` index rows (`UC-AEPS`, `UC-APB`, `UC-XTI`).
- Merge `proposed-tc-v2.3.csv` (16 rows) so the 20 parked scenarios re-key.
- Re-class the 8 open P1 DET rows that are platform-operations claims
  (`TC-SOAR-01/03`, `TC-ITPA-01`, `TC-MSIAM-01`, `TC-MTH-01`, `TC-SOT-01/03`,
  `TC-BYOML-01`) as AUT/PLT so they can be closed by an assertion.

## Status

| sprint | state | shas |
|---|---|---|
| 1 | shipped (branch `claude/dazzling-ritchie-38r2qr`, awaiting Gate A) | `51deca2` 1a+1c · `e31f4c1` 1b · `8e57eb6` 1d |
| 2 | not started | — |
| 3 | not started | — |

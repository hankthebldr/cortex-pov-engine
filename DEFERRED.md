# DEFERRED — captures that could not reach their canonical home

Per the in-flight / vault-capture durability rule: a vault write that cannot be
made from this surface is logged here verbatim and replayed from the MBP.
Never claim a hung or unreachable write succeeded.

---

## 2026-09-20 · vault-os unreachable from the cloud Claude Code session

**Replay:** create the note below at
`_projects/cortex-pov-engine/WI cortex-pov-engine xsiam-readback-trust.md`
(dest asserted, `informed-by: [[_MOC cortex-pov-engine]]`), then delete this
entry. Frontmatter per the 12-key schema; `work-item` + `plan` tags.

```markdown
---
title: WI cortex-pov-engine xsiam-readback-trust
type: working-note
status: active
area: cortex-pov-engine
period: 2026-Q3
product: Cortex XSIAM
source: claude-code
informed-by: "[[_MOC cortex-pov-engine]]"
tags: [work-item, plan, uctc-index, xsiam]
aliases: [xsiam-readback-trust]
date-created: 2026-09-20
date-modified: 2026-09-20
---

## Charter
**Intent:** Make the XSIAM alert read-back loop trustworthy on a real, shared,
busy tenant, then widen what it can prove against the FY27 v2.2 UC/TC index,
then make every in-tree coverage number agree with the tree.
**Success:** a live-tenant run produces a `pass`/`fail` on at least one
index-scoreable DET row with `tenant-verified > 0`, every coverage table in
CLAUDE.md / README / scoreboard regenerates from one counted source, and the
console never sums authored with proven.
**Constraints:** no write path to Cortex (`CORTEXSIM_XSIAM_ALLOW_WRITE` stays
off); a zero is degraded, not ok; an unrecognised shape raises; authored is not
proven and the two are never one number; topic branches cut from `dev`, PRs
only when asked; commit by name.
**Out of scope:** index-owner decisions (Draft rows, v2.3 merge, re-classing
the 8 platform-ops P1 DET rows); the UI Coverage view until sprint 3.

## Ledger
### 2026-09-20 · claude-code · Fable 5.1
- state: cortex-pov-engine@bdd65ae · branch claude/dazzling-ritchie-38r2qr ·
  audit written `docs/uc_tc_mapping/audit-2026-09-20.md`
- did: audited the index binding. Tree: 177 scenarios, 90/266 by scenario,
  70/107 DET/HNT, 28 assertion artifacts binding 26 TCs (16 net-new), union
  106. Five in-tree docs carry five different answers; the generated
  scoreboard reads assertion bindings from a 6-entry hand dict (reports 95
  authored, AUT 0). Machine-PASS ceiling of the read-back loop is 6 DET/HNT
  rows. Four connector defects found (no paging, no host scope, dead
  detection_id key, one alert satisfies a whole step).
- decided: D1 — sprint order is trust-the-loop → widen-what-it-proves →
  tell-the-truth; nothing in sprint 2/3 may consume a number sprint 1 has not
  made honest. D2 — an unknown alert `source` is unconstrained, never a
  disagreement (a manufactured false negative is worse than an unlabelled
  match). D3 — host-less alerts stay eligible under host scoping; only a
  DIFFERENT host is excluded.
- open: which rule-id field a real tenant returns (`matching_service_rule_id`
  assumed; PANW docs egress-blocked from the cloud session); whether the v1
  `get_alerts_multi_events` path still answers on current tenants.
- next: execute sprint 1 → [WI:xsiam-readback-trust] in Things

### 2026-09-20 · claude-code · Fable 5.1
- state: cortex-pov-engine@8e57eb6 · branch claude/dazzling-ritchie-38r2qr ·
  sprint 1 shipped, full backend suite running, push pending green
- did: plan written (`IMPL-PLAN-xsiam-readback-and-index-honesty.md`, 3
  sprints). Sprint 1: `51deca2` pagination + truncation-as-floor + source
  families + findall + matching_service_rule_id; `e31f4c1` host scoping in
  reconcile_run (Run.target → Agent.hostname) with unscoped warning;
  `8e57eb6` preflight `alert_shape` rung (PF_ALERT_SHAPE_PARTIAL /
  _UNMATCHABLE / _UNKNOWN; empty tenant is degraded, not ready). 16 new
  tests, each observed red on the parent; 335 connector/API tests green.
- decided: D4 — an explicit `limit` filter is always a single page (preflight
  probes must never become a harvest). D5 — a non-200 on page N>1 fails the
  whole pull; page 1 alone would read as the tenant's total.
- open: sprint 2a (Detection Accuracy from matched/seeded) is the cheapest
  lift, 6 → 14 rows, zero tenant calls; vault-os was not attached to this
  session, so this note lives in DEFERRED.md until replayed.
- next: run preflight against the first real tenant and read the
  `alert_shape` rung before any POV → [WI:xsiam-readback-trust] in Things

### 2026-09-21 · claude-code · Fable 5.1
- state: cortex-pov-engine@f08980a · branch claude/dazzling-ritchie-38r2qr ·
  PR #129 open against dev (CI green on c5e5e4c) · sprint 2 shipped on the
  same branch, push pending full-suite green
- did: sprint 2. `30d0808` measures.py (accuracy, correlation rate, the
  honesty guard), `pull_incidents` via one shared `_harvest`, `incident_id`
  off alerts, `alerts_path`/`incidents_path` config, `Result.detection_name`
  + exact rule-name key with consumption. `f08980a` reconcile_run builds the
  pull context, optional host-scoped incidents read for correlation
  scenarios, `score_run_for_run(pull_context=…)`, re-score keeps the last
  measurement, manual /observations scores under `basis: manual`. 23 new
  tests (each observed red first); 361 connector/API tests green.
- decided: D6 — unscoped withholds PASS, truncated withholds FAIL, zero
  matched stays pending (the sweep's attempt cap is the only "gave up").
  D7 — correlation basis order: alert-carried incident ids, then one
  host-scoped get_incidents read, never unscoped. D8 — only alerts taken
  on the exact rule-name key are consumed; technique/name matches are not,
  or every scenario without card names would break.
- open: PR #129 now carries sprint 1 + 2 (harness pins this branch);
  Henry to say split or keep. vault-os still not attached from cloud
  sessions — this ledger is still in DEFERRED.md.
- next: sprint 3a — make `--emit-xlsx` walk assertions/ (28, not 6) →
  [WI:xsiam-readback-trust] in Things
```

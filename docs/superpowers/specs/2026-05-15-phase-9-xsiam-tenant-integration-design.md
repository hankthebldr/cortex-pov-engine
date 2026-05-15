# Phase 9 — XSIAM Tenant Integration Design

> Author: Henry Reed (with Claude Opus 4.7) · 2026-05-15 · Status: draft, awaiting Gate-9-A approval

## 1. Problem

CortexSim's validation loop is open. We fire signals into a customer's Cortex tenant; the DC sits at the console, watches alerts fire, and manually marks `Result` rows as observed in the UI. That works for a live demo. It does not scale to:

- Overnight regression sweeps across all 39 scenarios.
- POV regeneration after a customer tunes a correlation rule.
- "Did the change land?" sanity checks during XSIAM content development.
- Multi-tenant DC workflows (the same scenario fires against five different customer tenants in a week — we have no programmatic way to verify each landed).

Phase 9 closes the loop: CortexSim queries the tenant after every run, matches alerts to scenario steps, and auto-populates the Result rows. The DC moves from clicker-of-checkboxes to reviewer-of-anomalies. MTTD becomes a metric we can trend over time, not a sticky note from one demo.

## 2. Goals & non-goals

**Goals**
- Register one or more XSIAM tenants by URL + API key in SimCore.
- Auto-validate Result rows by querying the tenant after a Run completes.
- Compute MTTD per detection from real `alert.creation_time` − `step.executed_at`.
- Surface unmatched-but-observed alerts as "bonus detections" or "noise" in the POV report.
- Treat tenants as plural and isolated — credentials never leave the registered tenant boundary.

**Non-goals (V1)**
- Programmatic BIOC / correlation rule authoring. PANW's public API surface does not expose CRUD for these resources today. CortexSim continues to ship rule recommendations as POV report artifacts; the customer authors them in the console.
- Real-time alert streaming (websocket / SSE). All polling.
- Cross-tenant aggregation. A Run targets exactly one tenant.
- XSOAR playbook execution. Out of scope; Phase 11+ if it ever comes back.

## 3. API surface (research summary)

Full research write-up was produced by a sub-agent on 2026-05-15 (see Section 11 for source links). Key load-bearing facts that drive the design:

| Fact | Implication |
|---|---|
| Auth is **Standard** (static headers) or **Advanced** (SHA-256-signed nonce/timestamp). | Client must support both. Default to Standard; allow per-tenant override. |
| Tenant base URL is `api-{sub}.xdr.{region}.paloaltonetworks.com`. Region list drifts. | Region is free-text config, not an enum. |
| Alerts: `POST /public_api/v1/alerts/get_alerts` — 100/page, filter array is AND-only, `total_count` capped at `"9,999+"`. | Pagination via *narrower time filters*, not offsets. |
| Incidents: `POST /public_api/v1/incidents/get_incidents`. | Use for stitching scenarios — "did we get ONE incident, not N alerts?" |
| XQL: `start_xql_query` + `get_query_results`. Max 4 concurrent per tenant. Daily quota opaque pre-run, returned post-run in `remaining_quota`. | Bias V1 toward `get_alerts` (no XQL quota cost); use XQL only when raw `xdr_data` joins are needed. |
| **No public BIOC / correlation rule CRUD.** | Hard ceiling on rule-management features. Don't promise them. |
| Insert APIs: `insert_parsed_alerts` (60/req, 600/min), `insert_jsons` (10k IOCs/req). | We *can* push synthetic ledger entries — drives correlation strategy Option C. |
| `cortex-xdr-client` PyPI package: unmaintained. | Do not adopt. Write thin `httpx` async client. |

## 4. Correlation strategy

The research surfaced three options to tie a CortexSim Run to its resulting Cortex alerts. Phase 9 V1 implements **Option A**, and ships **Option B** as a feature-flagged add-on.

### Option A — Time-window + host/identity correlation (V1)

Pre-flight: record `run.started_at`, `run.ended_at`, every `step.executed_at`, the host (`endpoint_id` and/or `host_name`), and the harness identity (`actor_effective_username`).

Post-flight: query `get_alerts` with `creation_time >= run.started_at - 30s`, `creation_time <= run.ended_at + 5min`, then in-memory filter by host/identity. Match alerts to scenario steps by closest-prior-step within a 60s window.

**Why first.** Zero tenant configuration. Works on every plane. Deterministic enough at single-tenant POV scale. Survives the lack of a documented `external_id` on alerts.

**Where it breaks down.** Shared lab hosts running multiple CortexSim runs concurrently. Customer environments where the host has noisy background activity. The 60s match window is a heuristic.

### Option B — Per-run marker IOC (V1 feature flag)

For tenants where Option A noise is unacceptable: at run start, push a unique synthetic IOC (`cortexsim-run-<uuid>.invalid` domain, or a UA fragment like `CortexSim/<uuid>`) via `/indicators/insert_jsons`. The scenario emits the marker on the wire/endpoint. Post-flight: query alerts by the IOC.

**Why feature flag.** Adds an IOC ingestion step per run; only works for planes where we control a network or file artifact (poor fit for ITDR or pure analytics-only stitching); IOC propagation latency is non-zero.

### Option C — Insert-parsed-alerts companion ledger (deferred)

Skipped. The published `insert_parsed_alerts` schema does not expose a tagged `external_id` field — the run UUID has to live inside `alert_name` or `alert_description`, and the alert-to-real-alert join ultimately reduces to time+host (i.e. Option A with extra steps). Not worth the alert-volume pollution in V1.

## 5. Data model

```
┌─────────────────────────────────────────────────────────────────────────┐
│ tenant                                                                  │
│   id              UUID                                                  │
│   name            TEXT (DC-supplied)                                    │
│   base_url        TEXT  (https://api-acme.xdr.us.paloaltonetworks.com)  │
│   auth_mode       ENUM('standard','advanced')                           │
│   api_key_enc     BYTEA   (Fernet-encrypted, key in CORTEXSIM_SECRET)   │
│   api_key_id      TEXT                                                  │
│   region          TEXT                                                  │
│   created_at      TIMESTAMPTZ                                           │
│   last_verified_at TIMESTAMPTZ                                          │
│   last_verified_ok BOOLEAN                                              │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ run  (extends existing model)                                           │
│   ...existing columns...                                                │
│   tenant_id        UUID NULL  (NULL = not validated against a tenant)   │
│   target_hosts     JSONB      (list of {endpoint_id, host_name}         │
│                                supplied by DC at run launch)            │
│   target_identity  TEXT NULL  (optional override)                       │
│   correlation_mode ENUM('time_host','ioc_marker') DEFAULT 'time_host'   │
│   marker_ioc       TEXT NULL   (set if correlation_mode='ioc_marker')   │
│   validated_at     TIMESTAMPTZ NULL                                     │
│   validation_summary JSONB NULL                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ result  (extends existing model)                                        │
│   ...existing columns: id, run_id, step_id, expected_plane, ...         │
│   matched_alert_id  TEXT NULL   (Cortex alert_id that matched)          │
│   matched_alert_payload JSONB NULL  (the alert envelope, frozen)        │
│   matched_via       ENUM('manual','time_host','ioc_marker') NULL        │
│   match_confidence  REAL NULL   (0-1; heuristic)                        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ bonus_alert  (new — alerts observed but not matched to any expected)    │
│   id             UUID                                                   │
│   run_id         UUID                                                   │
│   alert_id       TEXT                                                   │
│   payload        JSONB                                                  │
│   classification ENUM('unmatched','noise','suspected_fp') DEFAULT       │
│                  'unmatched'                                            │
│   note           TEXT NULL                                              │
└─────────────────────────────────────────────────────────────────────────┘
```

## 6. New API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/xsiam/tenants` | Register a tenant. Body: `{name, base_url, auth_mode, api_key, api_key_id, region}`. Encrypts on store. |
| `GET` | `/api/xsiam/tenants` | List registered tenants (redacted). |
| `GET` | `/api/xsiam/tenants/{id}` | Single tenant (redacted). |
| `DELETE` | `/api/xsiam/tenants/{id}` | Remove tenant. |
| `POST` | `/api/xsiam/tenants/{id}/test` | Liveness check: `get_alerts` with `creation_time > now() - 60s, limit 1`. Updates `last_verified_*`. |
| `POST` | `/api/xsiam/tenants/{id}/xql` | Pass-through XQL: `{query, timeframe}`. For ad-hoc DC use. |
| `POST` | `/api/runs/{run_id}/validate/auto` | Trigger automatic validation against the run's `tenant_id`. Idempotent. |
| `GET` | `/api/runs/{run_id}/validation` | Validation result + bonus alerts. |

The existing `POST /api/runs` gains an optional `{tenant_id, target_hosts[], correlation_mode}` block. If omitted, runs in the legacy manual-validation flow.

## 7. New module layout

```
core/integrations/xsiam/
├── __init__.py
├── client.py            # XsiamAsyncClient — httpx + Standard/Advanced auth
├── auth.py              # Standard/Advanced header builders + Fernet store
├── models.py            # Pydantic for alert / incident / query envelopes
├── tenant_store.py      # CRUD against the tenant table
├── correlator.py        # Time/host matching, IOC marker matching
├── validator.py         # Run-level: orchestrates query + correlate + write
└── exceptions.py
core/api/xsiam.py        # FastAPI router
tests/integration/xsiam/
├── test_auth_standard.py
├── test_auth_advanced.py
├── test_correlator_time_host.py
├── test_validator_e2e.py    # uses respx to mock tenant
└── fixtures/                # canned XSIAM responses
```

Why a fresh `core/integrations/xsiam/` namespace rather than slotting it under `core/engine/`: the integration is a *consumer* of the engine's output (the Run + Results), not part of the engine itself. Keep the seam clean.

## 8. UI surface

Three new screens (Phase 7 design language — leverage existing EAL Console patterns):

1. **Tenant Manager** (`/tenants`) — register, test, delete tenants. Shows last-verified timestamp + last error.
2. **Run launcher enhancement** — when a tenant is registered, the launcher gains a "Validate against tenant" checkbox + tenant picker + target-hosts input.
3. **Results validator (auto mode)** — after a run finishes, the existing Results page shows a "Auto-validate" button. Clicking it polls `/api/runs/{id}/validation` and shows match confidence + bonus alerts.

Bonus alerts get a Cortex-amber `#FA582D` chip; matched alerts a Cortex-green `#00E87B` chip. Manual override is always available — the DC can demote a matched alert or promote a bonus alert.

## 9. Phased delivery

### Phase 9-A (Gate-9-A) — Read-only foundation

- Tenant CRUD + encrypted storage.
- `XsiamAsyncClient` with Standard auth only. (Advanced auth deferred to 9-B.)
- `POST /api/xsiam/tenants/{id}/test` working against a real tenant.
- Tenant Manager UI page.
- No correlation, no validation. Just "can we authenticate and read".

**Acceptance:** DC registers a tenant, hits Test, sees a recent alert count come back. No write paths yet.

### Phase 9-B — Time-host correlator

- Run launcher gains tenant + host-target fields.
- `validator.py` implements Option A (time-window + host/identity).
- `bonus_alert` table populated.
- Results UI shows auto-match.

**Acceptance:** Run `SIM-EDR-001` against a real Linux endpoint enrolled in Cortex XDR. Auto-validation matches at least 80% of expected detections to real alerts within 60s. MTTD column populates.

### Phase 9-C — Advanced auth + XQL pass-through

- Advanced-key signature builder + matching test vectors.
- `/api/xsiam/tenants/{id}/xql` pass-through.
- POV report exporter consumes auto-validation data.

**Acceptance:** Switch a registered tenant from Standard to Advanced auth. All Phase 9-B verifications pass with the new auth. DC can fire an ad-hoc XQL from the Tenant Manager and see results.

### Phase 9-D — IOC marker mode

- Per-run synthetic IOC ingestion.
- Per-plane probe insertion (KOI: stamp the run UUID into the agentic-pack archive name; NDR: include the marker in `c2_http_beacon` Host header; AI: include in the `User-Agent`).
- Correlator gains `match_via='ioc_marker'` path.

**Acceptance:** Run `SIM-MP-001` on a shared lab host with deliberate concurrent noise. Auto-validation produces zero cross-talk with the noise run.

### Phase 9-E — Multi-tenant DC workflow + observability

- Multi-tenant select-and-fan-out (one Run definition → N tenant runs).
- Prometheus metrics for `query_cost`, `remaining_quota`, `429_retries`, `auto_match_rate`.
- Configurable rate-limit token bucket.

**Acceptance:** Fire `SIM-EDR-001` across 3 tenants in parallel. All three auto-validate. No tenant exceeds its rate budget.

## 10. Risks (top 5, from research)

1. **Advanced-key signature canonicalization is under-documented.** Concatenation order, timestamp unit (ms vs s), nonce length — all "community knowledge". Validate against a live tenant in Phase 9-C before shipping. Failure mode is silent 401.
2. **`total_count` cap at `"9,999+"`.** A noisy customer tenant blows past 10k alerts in our typical window. The correlator must shard by time/host rather than paginate by offset. Pre-compute the shard plan.
3. **XQL daily quota is opaque pre-run.** A 40-scenario POV that uses XQL per verification can plausibly chew through a Premium tenant's daily budget. V1 biases toward `get_alerts`; XQL only when raw `xdr_data` joins are needed.
4. **No public BIOC / correlation rule CRUD.** We can never close the loop to "auto-author the missing rule" without XSOAR content packs. Document this ceiling clearly in DC training.
5. **Tenant URL region drift.** PANW adds regions ad-hoc. Treat region as free-text the DC types in, validate by attempting `test`, never as an enum.

## 11. Source links

The Section 3 research was compiled from current PANW docs (May 2026). Authoritative pages:

- [Cortex XSIAM REST API root](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-REST-API)
- [Manage API keys](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Manage-API-keys)
- [Get all Alerts](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-REST-API/Get-all-Alerts)
- [Get Alerts Multi-Events v2](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-REST-API/Get-Alerts-Multi-Events-v2)
- [Get Incidents](https://cortex-panw.stoplight.io/docs/cortex-xsiam-1/hurg0x3gwsxyl-get-incidents)
- [Insert Parsed Alerts](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-REST-API/Insert-Parsed-Alerts)
- [Insert Simple Indicators JSON](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-REST-API/Insert-Simple-Indicators-JSON)
- [Start an XQL query](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-REST-API/Start-an-XQL-query)
- [Get XQL query results](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-REST-API/Get-XQL-query-results)
- [pan-cortex-xdr-nodejs reference impl](https://github.com/PaloAltoNetworks/pan-cortex-xdr-nodejs)

## 12. Gate-9-A acceptance checklist

- [ ] Tenant CRUD implemented + tested.
- [ ] `XsiamAsyncClient` Standard-auth happy path verified against a real tenant (Henry-provided).
- [ ] `POST /tenants/{id}/test` returns ok with `last_verified_at` populated.
- [ ] Tenant Manager UI page rendered, registered tenants visible.
- [ ] API keys never logged. Stored Fernet-encrypted.
- [ ] OPSEC review: API key live only in env / Fernet payload, never plaintext on disk or in chat.
- [ ] Reviewer signs off in chat before Phase 9-B starts.

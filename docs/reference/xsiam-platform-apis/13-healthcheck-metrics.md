# Healthcheck & Health Metrics (`healthcheck` + `metrics_*` over XQL)

> The **tenant-operations read surface** for CortexSim — the API mapping of the repo's Phase 9 *XSIAM Tenant Health & Config Integration* track: **opt-in, read-only** health and usage pulls that let a DC answer "is this tenant healthy, is data landing, and at what volume?" without leaving the POV engine. This page assumes the shared contract from [`./01-authentication.md`](./01-authentication.md) and [`./02-conventions.md`](./02-conventions.md) and does **not** re-explain auth, the `{"request_data": {...}}` envelope, the `{"reply": {...}}` wrapper, epoch-millisecond timestamps, or the filter/paging grammar. It composes two primitives: a one-shot `healthcheck` liveness probe, and — the reliable workhorse — curated **XQL over the `metrics_*` dataset family**, whose async start/poll/stream lifecycle lives in [`./07-xql-api.md`](./07-xql-api.md). Authoritative design: [`docs/superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md`](../../superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md).

All calls in this page follow the standard shape:

```
POST https://api-{fqdn}/public_api/v1/{api_name}/{call_name}/
Body: {"request_data": { ... }}
Reply: {"reply": { ... }}
```

---

## 1. `healthcheck` — `/public_api/v1/healthcheck/`  ⚠ verify exact `api_name`/path

**Purpose.** A single, cheap liveness/health probe surfaced by the engine as a live status pill. Confirms the tenant is reachable and reports overall/component health (data ingestion, agents, correlation, license).

> **⚠ Path caveat.** The design doc lists `/public_api/v1/healthcheck` as a bare path; whether it is a `GET` with no body or a `POST` with the standard `{"request_data": {}}` envelope, and whether it nests under an `api_name` segment, is **under-documented — pin against a real tenant** in the env-gated smoke test before relying on it.

| Aspect | Value |
|--------|-------|
| Method | `GET` (per design §4) — ⚠ verify vs. `POST {"request_data": {}}` |
| Request body | `{}` / none |
| Direction | **READ** |
| License gating | **Premium / Enterprise only** — non-Premium tenants **403**. Fall back to `endpoints/get_endpoints` (`limit: 1`) purely as an auth-liveness probe (see [`./05-endpoints-api.md`](./05-endpoints-api.md)). |

### Plausible response shape (⚠ every field "verify")

```json
{
  "reply": {
    "status": "OK",                        // ⚠ verify enum: OK | DEGRADED | ERROR
    "components": {                         // ⚠ verify key + presence
      "data_ingestion":  { "status": "OK",       "lag_seconds": 4 },
      "agents":          { "status": "OK",       "online": 128, "offline": 3 },
      "correlation":     { "status": "OK" },
      "license":         { "status": "OK",       "tier": "enterprise", "expires_at": 1735689600000 }
    },
    "checked_at": 1719792000000             // ⚠ verify field name (epoch-ms)
  }
}
```

> Treat **only `status`** as load-bearing; the `components` breakdown is illustrative until pinned. On a `403` do **not** retry — switch to the `get_endpoints` limit-1 fallback and record the tier limitation.

**Harness relevance.** ✅ Read. Backs `POST /api/xsiam/tenants/{name}/test` and the health pill (`GET …/health`) in the design's `core/integrations/xsiam/client.py::healthcheck()`.

---

## 2. Health metrics via XQL over `metrics_*` — the reliable path

The **primary, dependable** way to pull health/usage numbers is not a bespoke metrics endpoint but **XQL over the `metrics_*` dataset family** (requires *Data Ingestion Monitoring* enabled on the tenant). It reuses the exact async lifecycle documented in [`./07-xql-api.md`](./07-xql-api.md):

`start_xql_query` → `get_query_results` (poll to `SUCCESS`) → branch inline `results.data[]` vs. `results.stream_id`.

> **⚠ Schema is version-specific.** Every dataset name (`metrics_source`, `metrics_ingestion`, `metrics_agents`, …) and field name below is a **plausible placeholder — verify against the target tenant's schema.** The design deliberately keeps the canonical ingestion query as Henry-owned content (`queries.py::INGESTION_HEALTH_XQL`) precisely because these names drift across tenant versions. Bias to **cheap aggregates** — XQL runs on a metered quota (preflight with `get_quota`, [`./07-xql-api.md §4`](./07-xql-api.md#get_quota--public_apiv1xqlget_quota)).

### Example metric queries

Each is a complete XQL string to hand to `start_xql_query` as `request_data.query` (with a `timeframe`). Dataset/field names marked ⚠.

```text
# (a) Ingestion volume + EPS per source over the window — the canonical health query
dataset = metrics_source                                    # ⚠ verify dataset
| filter _time > to_timestamp(1719792000000, "MILLIS")
| comp sum(ingested_bytes) as total_bytes,                  # ⚠ verify metric fields
       sum(event_count)    as events
       by source, vendor, product
| alter eps = events / 3600                                 # ⚠ derive EPS from window length
```

```text
# (b) Last-seen freshness per source — is data still landing, or did a feed go dark?
dataset = metrics_source                                    # ⚠ verify
| comp max(_time) as last_seen by source, vendor, product
| sort desc last_seen
```

```text
# (c) Agent online / offline counts (fleet readiness before a run)
dataset = metrics_agents                                    # ⚠ verify dataset/fields
| comp count_distinct(agent_id) as agents by connection_status
```

```text
# (d) License / quota consumption — ingestion GB vs. entitlement
dataset = metrics_license                                   # ⚠ verify
| filter _time > to_timestamp(1719792000000, "MILLIS")
| comp sum(ingested_bytes) / 1073741824 as ingested_gb by license_tier   # ⚠ verify
```

```text
# (e) Dataset sizes / row counts — spot the heavy hitters in the tenant
dataset = metrics_ingestion                                 # ⚠ verify
| comp sum(row_count) as rows, sum(size_bytes) as bytes by dataset_name  # ⚠ verify
| sort desc bytes
```

```text
# (f) Correlation-rule hit rates — are detection rules actually firing?
dataset = metrics_correlation                               # ⚠ verify dataset name
| filter _time > to_timestamp(1719792000000, "MILLIS")
| comp count() as hits by rule_name, rule_id                # ⚠ verify fields
| sort desc hits
```

```text
# (g) Overall ingestion EPS trend, bucketed (dashboard sparkline source)
dataset = metrics_ingestion                                 # ⚠ verify
| filter _time > to_timestamp(1719792000000, "MILLIS")
| comp sum(event_count) as events by bin(_time, 5m)         # ⚠ verify bin() + field
```

### Output contract (design §8, `INGESTION_HEALTH_XQL`)

The curated ingestion query returns **one row per data source** over the lookback window:

| Field | Type | Meaning |
|-------|------|---------|
| `source` | str | Data source / collector name |
| `vendor` | str | Vendor |
| `product` | str | Product |
| `events` | int | Event count in window |
| `last_seen` | epoch-ms | Most recent event time |

The client (`run_xql(INGESTION_HEALTH_XQL, timeframe)`) shapes this into the `GET /api/xsiam/tenants/{name}/metrics` payload → the Tenant Manager per-source table.

**Harness relevance.** ✅ Read. This is the workhorse of the Phase 9 track; all four XQL calls are read-only (see [`./07-xql-api.md` summary](./07-xql-api.md#harness-relevance-summary)).

---

## 3. Config read (settings / collectors inventory) — ⚠ prefer XQL/health

A DC often also wants a tenant's configuration inventory (enabled settings, data-collector list). There is **no stable, documented public-API config-read surface** across XSIAM versions.

| Want | Recommended path | Avoid |
|------|------------------|-------|
| Which sources/collectors are ingesting | `metrics_*` XQL (query **b** above — sources with recent `last_seen`) | Undocumented `/settings` / `/collectors` REST endpoints |
| Config-change audit trail | `audits/management_logs` ([`./09-audit-api.md`](./09-audit-api.md)) | — |
| Agent fleet inventory | `endpoints/get_endpoints` ([`./05-endpoints-api.md`](./05-endpoints-api.md)) — deferred to design **Slice 2** | — |
| Overall/component health | `healthcheck` (§1) | — |

> **⚠ verify.** Any REST config endpoint you find (settings dumps, collector lists) is **version-dependent and effectively undocumented** — treat it as unsupported. The design explicitly declines a REST config surface: derive config state from `metrics_*` XQL, `healthcheck`, and `audits` instead. Config **write** is out of scope entirely (no-write principle holds).

---

## 4. Harness relevance — ✅ this **is** the Phase 9 track

This page maps directly onto the repo's Phase 9 *Health & Config* track, which **relaxes** the standing "No Cortex API connection" rule to **opt-in, read-only** health/metrics — the no-write and no-detection-readback principles still hold.

| Guardrail | Behaviour |
|-----------|-----------|
| **Read-only** | Only `healthcheck` + XQL `SELECT`-style reads. No config mutation, no rule authoring (the public API exposes no BIOC/correlation CRUD anyway). Zero writes to Cortex. |
| **Opt-in** | Outbound tenant calls happen **only** when an XSIAM tenant credential is registered (`IntegrationCredential(kind="xsiam_tenant")`, Fernet-encrypted via `CredentialStore`). No credential → no calls. |
| **Auto-loops off by default** | The background auto-reconcile loop (`CORTEXSIM_AUTO_RECONCILE`, per `CLAUDE.md`) is **off by default** — it is the only thing that makes unattended outbound calls, and it stays opt-in. Health pulls are otherwise DC-initiated (`POST …/test`, `GET …/metrics`, ad-hoc XQL). |
| **Quota-safe** | Metric queries are cheap aggregates; preflight `get_quota` before wide sweeps so a POV can't exhaust a customer's XQL budget. |
| **SSRF control** | `base_url` is strict-validated (`https://` + PANW tenant FQDN) before the customer API key is ever sent — a typo'd URL is a credential-leak vector, not cosmetics (design §5). |

**Consuming surfaces** (design §7): `POST /api/xsiam/tenants/{name}/test` (healthcheck + `mark_integration_verified`), `GET …/health`, `GET …/metrics`, `POST …/xql` + `GET …/xql/{query_id}`. Implementation seam: `core/integrations/xsiam/{client,queries,auth,config,loader}.py`, thin router `core/api/xsiam.py`.

| Call | Direction | Harness | Why |
|------|-----------|:------:|-----|
| `healthcheck` | read | ✅ | Liveness/health pill; license-gated → `get_endpoints` limit-1 fallback |
| `metrics_*` via XQL | read | ✅ | The reliable ingestion/EPS/agents/license/rule-hit numbers |
| config REST endpoints | — | ❌ | Undocumented/version-dependent — use XQL/health/audits instead |

Legend: ✅ read (in harness) · ❌ not used. All in-scope calls here are strictly read-only.

---

## ⚠ verify — before trusting this page against a live tenant

- **`healthcheck` path/method** — bare `/public_api/v1/healthcheck` vs. `api_name`-nested; `GET` (no body) vs. `POST {"request_data": {}}`.
- **`healthcheck` response envelope + fields** — `status` enum, `components.*` breakdown, `checked_at` name; pin via the smoke test.
- **License gating** — confirm the 403-on-non-Premium behaviour and that `get_endpoints` (limit 1) is a valid auth-liveness fallback.
- **`metrics_*` dataset names** — `metrics_source` / `metrics_ingestion` / `metrics_agents` / `metrics_license` / `metrics_correlation` are placeholders; the real family/`metrics_view` preset is tenant-version-specific.
- **`metrics_*` field names** — `ingested_bytes`, `event_count`, `connection_status`, `row_count`, `rule_name`, etc. all unverified.
- **XQL helper signatures** — `to_timestamp(..,"MILLIS")`, `bin(_time,5m)`, EPS derivation from window length.
- **`timeframe` shape** — `{"relativeTime": <ms>}` vs. `{"from","to"}` (cross-check [`./07-xql-api.md`](./07-xql-api.md)).
- **Config read** — any `/settings` or `/collectors` REST surface is undocumented/version-dependent; do not depend on it.

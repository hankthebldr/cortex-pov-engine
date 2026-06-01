# Phase 9 (Health & Config track) — XSIAM Tenant Health & Config Integration

> Author: Henry Reed (with Claude Opus 4.8) · 2026-06-01 · Status: draft, awaiting spec review
>
> **Supersedes the _detection-validation_ direction** of
> [`2026-05-15-phase-9-xsiam-tenant-integration-design.md`](./2026-05-15-phase-9-xsiam-tenant-integration-design.md)
> (now parked). Reuses that doc's §3 API research and §10 risks. The
> credential-storage foundation that doc assumed has since shipped —
> generalized `Secret` + `IntegrationCredential` — so this spec builds on what
> shipped rather than the bespoke `tenant` table the prior doc proposed.

## 1. Problem

CortexSim generates signals **into** a customer's Cortex tenant but has no
programmatic read-back. During a POV the DC has no in-engine way to answer
operational questions: *Is the tenant healthy? Is data actually landing, and
from which sources? At what volume?* Today that's eyeballed in the XSIAM
Command Center / Data Ingestion Dashboard, out-of-band from the POV engine.

This integration gives the engine a **read-only tenant operations surface**:
register a tenant, confirm it is healthy, and pull **health metrics via XQL**
to gauge POV readiness. It is explicitly **not** detection-scenario validation —
correlating fired alerts to scenario steps and computing MTTD remains parked in
the prior Phase 9 doc.

## 2. Goals & non-goals

**Goals**

- Register one or more XSIAM tenants by base URL + Standard API key, reusing the
  shipped credential layer.
- A liveness/health probe (`/healthcheck`) surfaced as a live status pill.
- **Health metrics via XQL** — run curated metric queries (ingestion volume /
  EPS / last-seen per source) over the tenant's `metrics_*` datasets and render
  them.
- Ad-hoc XQL pass-through for DC developer use.
- Real API calls in production; mocked transport only in CI; an env-gated
  real-tenant smoke test.

**Non-goals (this slice)**

- Detection/alert correlation, MTTD, scenario auto-validation — parked in the
  prior Phase 9 doc.
- Agent fleet **health feature** (`get_endpoints` inventory view) — deferred to
  Slice 2. *(A single `get_endpoints` limit-1 call may still appear in Slice 1
  purely as an auth-liveness fallback when `/healthcheck` is license-gated — that
  is a probe, not the feature.)*
- Any write path to Cortex (no config mutation, no rule authoring — the public
  API exposes no BIOC/correlation CRUD anyway).
- Advanced (signed) auth — Standard only this slice.

## 3. Relationship to the shipped foundation

Reuse, do not rebuild:

- `core/security/credentials.py` `CredentialStore` (Fernet) —
  `put_integration` / `get_integration_secret` / `mark_integration_verified`.
- `core/models.py` `Secret` + `IntegrationCredential` — a tenant **is**
  `IntegrationCredential(kind="xsiam_tenant")`.
- `core/api/credentials.py` generic `/api/credentials/integrations` CRUD
  (**decision B**: tenant CRUD is generic; only health/XQL operations live under
  `/api/xsiam`).
- `httpx` (already a dependency), the `CORTEXSIM_SECRET` boot guard.

## 4. API surface (confirmed, June 2026)

| Endpoint | Method | Use |
|---|---|---|
| `/public_api/v1/healthcheck` | GET | Environment health → `{status}`. **License-gated** (Premium/Enterprise) — fall back to `endpoints/get_endpoints` (limit 1) if it 403s. |
| `/public_api/v1/xql/start_xql_query` | POST | Start an XQL query → returns an execution/query id. |
| `/public_api/v1/xql/get_query_results` | POST | Poll by query id → `{status: PENDING\|SUCCESS, results, remaining_quota}`. Large result sets return a `stream_id`. |

- **Auth (Standard):** headers `x-xdr-auth-id: {api_key_id}` +
  `Authorization: {api_key}`; base
  `https://api-{tenant}.xdr.{region}.paloaltonetworks.com`.
- **Constraints:** max **4 concurrent** XQL queries per tenant; daily quota is
  opaque pre-run, returned post-run in `remaining_quota`.
- **Exact envelopes** (`reply` wrapping, the precise `healthcheck` body, XQL
  `timeframe` shape) are under-documented and **must be pinned against the real
  tenant in the smoke test** before Slice 1 sign-off.
- **Health-metrics source:** the `metrics_*` dataset family / `metrics_view`
  preset; requires Data Ingestion Monitoring enabled on the tenant.

## 5. Architecture & key decisions

- **D1 — Storage:** tenant = `IntegrationCredential(kind="xsiam_tenant")`,
  `config = {base_url, region, auth_mode: "standard", api_key_id}`; the API key
  lives in the backing `Secret`. No new tables.
- **D2 — Router (B):** CRUD via the generic `/api/credentials/integrations`;
  `/api/xsiam` holds only live-tenant operations.
- **D3 — UI:** new `TenantManager.jsx` (the first credential-management screen),
  XSIAM-scoped.
- **XQL lifecycle — async start/poll (mirrors the platform):**
  `POST …/xql` → `{query_id}`; `GET …/xql/{query_id}` → `{status, results}`.
  The UI polls. Healthcheck and the curated metric query may bounded-poll
  server-side (they are fast). This avoids long-held HTTP connections and
  respects the concurrency/quota limits.
- **`base_url` validation is a security control, not cosmetics.** This is the
  first time CortexSim sends a customer API key as a bearer token to a
  DC-typed URL; a typo or malicious value is an SSRF-shaped credential leak.
  `XsiamTenantConfig` enforces `https://` + the PANW tenant FQDN shape.
  *(Exact strictness is a Henry-owned contribution — see §8.)*
- **Test doubling:** `httpx.MockTransport` (zero new dependencies) for unit
  tests; an env-gated (`CORTEXSIM_XSIAM_TEST_TENANT`) real-tenant smoke test for
  genuine API verification.

## 6. Module layout

```
core/integrations/xsiam/
├── __init__.py
├── auth.py            # StandardAuth header builder (x-xdr-auth-id + Authorization)
├── config.py          # XsiamTenantConfig — base_url (SSRF control), auth_mode, region
├── client.py          # XsiamClient: healthcheck(); start_xql_query()/get_query_results();
│                       #   run_xql() = start + bounded poll
├── queries.py         # curated XQL constants — the health-metrics query (Henry-owned)
├── loader.py          # integration name → CredentialStore decrypt → typed XsiamClient
└── exceptions.py      # XsiamConfigError / XsiamAuthError / XsiamApiError /
                       #   XsiamQueryError / XsiamQuotaError
core/api/xsiam.py      # thin FastAPI router (registered in core/main.py)
tests/integration/xsiam/
├── test_auth_standard.py
├── test_config.py
├── test_client_healthcheck.py
├── test_client_xql.py
├── test_api_endpoints.py
├── test_smoke_live.py        # env-gated; skipped without CORTEXSIM_XSIAM_TEST_TENANT
└── fixtures/                 # canned XSIAM responses for MockTransport
```

Why `core/integrations/xsiam/` and not `core/engine/`: the integration is a
*consumer* of tenant state, not part of the scenario engine. Keep the seam clean.

## 7. New API endpoints (`core/api/xsiam.py`)

All responses are structured JSON; errors follow the repo rule
`{error, code, detail}`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/xsiam/tenants/{name}/test` | `healthcheck()`; on success `mark_integration_verified(ok=True)` and return `{ok, status, last_verified_at}`; on 403 use the fallback probe; on failure record the error. |
| `GET` | `/api/xsiam/tenants/{name}/health` | Health-pill payload (last status + on-demand refresh). |
| `GET` | `/api/xsiam/tenants/{name}/metrics` | Runs the curated health-metrics XQL; returns shaped per-source metrics. |
| `POST` | `/api/xsiam/tenants/{name}/xql` | `{query, timeframe}` → `{query_id}`. |
| `GET` | `/api/xsiam/tenants/{name}/xql/{query_id}` | `{status, results, remaining_quota}`. |

`{name}` is the `IntegrationCredential.name` (unique, DC-supplied). Registration,
listing, and deletion are **not** here — they use the generic
`/api/credentials/integrations` endpoints with `kind="xsiam_tenant"`.

## 8. Health-metrics capability + the Henry-owned query

`queries.py` holds curated XQL as named constants, each with a documented output
contract. The ingestion/health-metrics query is **scaffolded but intentionally
left for Henry to write** — the exact `metrics_*` dataset and field names are
tenant-version-specific domain knowledge that beats anything synthesized from the
docs.

```python
# core/integrations/xsiam/queries.py
#
# CONTRACT: INGESTION_HEALTH_XQL returns one row per data source over the
# trailing `lookback` window, with fields:
#   source (str), vendor (str), product (str), events (int), last_seen (epoch_ms)
# ~6-10 lines of XQL. Bias to a cheap aggregate so it is quota-friendly.
INGESTION_HEALTH_XQL = """
// TODO(Henry): finalize against the tenant's metrics schema.
dataset = metrics_source
| ...
"""
```

The client wraps it: `run_xql(INGESTION_HEALTH_XQL, timeframe)` → shape →
`/metrics` endpoint → UI table. Because the query is config-as-content, schema
drift across tenant versions is a content edit, not a code change.

## 9. UI surface

`TenantManager.jsx`, wired into `AppConsole`:

- **Register** form (name, base_url, region, auth_mode=standard, api_key_id,
  api_key) → `PUT /api/credentials/integrations` (kind=`xsiam_tenant`).
- **Tenant list** → `GET /api/credentials/integrations?kind=xsiam_tenant`
  (redacted; shows `preview_tail` + last-verified status).
- **Test** button → `POST /api/xsiam/tenants/{name}/test` → green/red pill +
  timestamp/error.
- **Health metrics** view → `GET …/metrics` rendered as a per-source table
  (events, last-seen, freshness colouring).
- **XQL box** → textarea + Run → `POST …/xql` then poll `GET …/xql/{id}` →
  results table; shows `remaining_quota`.
- Cortex theme tokens; bonus/error chips per existing console patterns.

## 10. Testing

- **Unit (MockTransport, no live tenant):** `test_auth_standard` (header
  correctness); `test_config` (base_url good/bad, auth_mode enum); 
  `test_client_healthcheck` (200 + 403→fallback); `test_client_xql`
  (start → PENDING → SUCCESS, quota surfaced, error paths); `test_api_endpoints`
  (each route, asserts `mark_integration_verified` fires on `/test`).
- **Smoke (env-gated):** `test_smoke_live` hits real `/healthcheck` + a trivial
  XQL against Henry's tenant when `CORTEXSIM_XSIAM_TEST_TENANT` creds are
  present; skipped in CI. This is where the under-documented envelopes get pinned.

## 11. Doc reconciliation

- Banner added to `2026-05-15-phase-9-…` pointing here; its detection-validation
  roadmap marked **parked**.
- Caveat on `CLAUDE.md:9` and `CORTEXSIM_AGENT_CONTEXT.md §10.1`: Phase 9
  (Health & Config) relaxes "No Cortex API connection" for **opt-in, read-only**
  health/metrics. The no-write and no-detection-readback principles still hold.

## 12. Phased delivery

- **Slice 1 (this spec):** substrate + `/healthcheck` + XQL lifecycle +
  health-metrics + pass-through + Tenant Manager.
- **Slice 2:** agent fleet health (`get_endpoints`), config audit trail
  (`audits/management_logs`), a saved-query library.
- **Later:** Advanced (signed) auth; multi-tenant fan-out. Detection-validation
  stays parked unless explicitly revived.

## 13. Risks

1. **Healthcheck license-gating** (403 on non-Premium) → fallback probe path.
2. **XQL quota exhaustion** across a POV → bias to cheap queries, cache metrics,
   surface `remaining_quota`.
3. **Under-documented API envelopes** → pin via the smoke test before sign-off.
4. **`metrics_*` schema drift** across tenant versions → query is Henry-owned
   content, never a hardcoded assumption.
5. **`base_url` mis-entry = key exfiltration** → strict FQDN validation,
   https-only.

## 14. Acceptance / Gate

DC registers a tenant → health pill goes green off `/healthcheck` (or the
fallback) → `/metrics` returns real per-source ingestion metrics from Henry's
tenant → an ad-hoc XQL returns results → the API key is stored Fernet-encrypted
and never logged → zero writes to Cortex. Reviewer signs off in chat before the
implementation plan.

# Harness Design Notes — Consuming the XSIAM Platform APIs from CortexSim

> How the POV engine's **API harness** consumes everything in this vault.
> This page ties the external API surface to the code in the repo
> (`core/integrations/xsiam/`, `core/connectors/`, `core/security/credentials.py`) and to the
> Phase-9 design spec
> [`docs/superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md`](../../superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md).

## 0. Implemented architecture (what shipped)

The harness is built as a declarative **operation catalog** mirroring the Tool Adapter
Framework, driving a gated executor on the existing `XsiamClient`:

| Layer | Path |
|-------|------|
| Operation schema (`op_id`, method, path, `access_class`, consent, `path_params`) | `core/integrations/xsiam/operations/schema.py` |
| Loader (reject-and-log, never raises) | `core/integrations/xsiam/operations/loader.py` |
| Singleton catalog (`catalog.load/find/all/list_for_*`) | `core/integrations/xsiam/operations/catalog.py` |
| 116 operations in category-grouped YAML packs | `core/integrations/xsiam/operations/packs/*.yml` |
| Generic transport `XsiamClient.request(method, path, json, params)` + advanced auth | `core/integrations/xsiam/client.py`, `auth.py` |
| List / detail / **gated executor** endpoints | `core/api/xsiam.py` (`/operations`, `/operations/{id}`, `POST /tenants/{name}/operations/{id}`) |
| Boot load + health component | `core/main.py` (lifespan 2c, `xsiam_operation_catalog`) |
| Master off-switches | `core/config.py` (`CORTEXSIM_XSIAM_ALLOW_WRITE`, `..._DESTRUCTIVE`) |

**Executor contract:** the request body is `{path_params, query, body, dry_run, consent}`.
Reads run live. Write/destructive ops **default to dry-run** (return the composed request,
send nothing); a live mutation requires the matching global flag **and** the matching
consent key (`write_authorized` / `destructive_authorized`). The client sends `body` verbatim
(never invents a payload) and unwraps `reply`. Both auth modes (standard + advanced-signed)
are supported; tenant FQDNs accept both `.xdr.` and `.xsiam.` hosts.

---

## 1. Scope guardrails (non-negotiable)

Per `CLAUDE.md` and the Phase-9 spec, the harness is **read-only and opt-in**:

- ✅ **Reads only:** XQL, `get_incidents`, `get_alerts_multi_events`, `get_endpoints`
  (limit-1 liveness probe), audit logs, `healthcheck`, `metrics_*` via XQL.
- 🚫 **Never** isolates hosts, runs scripts, quarantines files, edits block/allow lists,
  mutates incidents, or authors detections. The public API exposes no BIOC/correlation CRUD
  anyway, and the harness must not touch the action surface even where it exists.
- ⚠ **Never** uses the ingestion/push APIs (`insert_parsed_alerts`, HTTP Log Collector).
  CortexSim puts signal in via **agents + EAL plugins**, not the API. See
  [`12-data-ingestion-api.md`](12-data-ingestion-api.md).
- **Opt-in:** outbound tenant calls happen only when an integration credential is configured
  and (for background loops) an env flag is set. Default posture makes **zero** outbound
  calls.

---

## 2. What already exists (don't rebuild)

The measurement loop shipped a working slice of this. Reuse it:

| Component | Path | Role |
|-----------|------|------|
| `XsiamConnector` | `core/connectors/xsiam.py` | Pulls alerts via `/public_api/v1/alerts/get_alerts_multi_events`; supports **standard + advanced** auth; injectable transport; offline-safe (`PullResult(ok=False)` on any failure) |
| `Connector` base + registry | `core/connectors/base.py` | `ObservedAlert`, `PullResult`, `ConnectorConfig`, `@register_connector` |
| `matcher` | `core/connectors/matcher.py` | Pure function: correlate observed alerts → seeded `Result` rows on technique/detection-id/name within a window → real MTTD |
| `auto_reconcile_loop` | `core/connectors/service.py` | Opt-in background reconcile (`CORTEXSIM_AUTO_RECONCILE`, off by default) |
| `CredentialStore` | `core/security/credentials.py` | Fernet-encrypted vault: `put_integration` / `get_integration_secret` / `mark_integration_verified` |
| `IntegrationCredential` / `Secret` | `core/models.py` | A tenant **is** `IntegrationCredential(kind="xsiam_tenant")`, `config={base_url, region, auth_mode, api_key_id}`, key in the backing `Secret` |
| Credentials CRUD | `core/api/credentials.py` | Generic `/api/credentials/integrations` |

The Phase-9 "Health & Config" track adds an `/api/xsiam` router for **live-tenant operations
only** (health, XQL pass-through, curated metrics) on top of that foundation.

The harness generalizes the connector's HTTP/auth core into a reusable **`XsiamClient`**
(the contract in [`02-conventions.md`](02-conventions.md) §6) that all the read families in
this vault call through.

---

## 3. Client construction recap

- **Base URL:** `https://api-{fqdn}/public_api/v1/{api_name}/{call_name}/` — prefer the exact
  API URL copied from the tenant console over reconstructing it
  ([`00-overview.md`](00-overview.md) §2).
- **Auth:** standard or advanced ([`01-authentication.md`](01-authentication.md)). The
  shipped connector already implements advanced (`sha256(api_key + nonce + timestamp)`); the
  Phase-9 Slice-1 uses standard for simplicity. Prefer **advanced** for anything long-lived.
- **Envelope:** wrap `{"request_data": ...}`, unwrap `{"reply": ...}`
  ([`02-conventions.md`](02-conventions.md)).
- **Transport injectable:** tests never hit the network (existing rule; the connector already
  does this).

> **GET exception:** `/public_api/v1/healthcheck` is a **GET** and is **license-gated**
> (Premium/Enterprise). Everything else in the read surface is POST. If `healthcheck` 403s,
> fall back to a `get_endpoints` limit-1 call purely as an auth-liveness probe
> ([`13-healthcheck-metrics.md`](13-healthcheck-metrics.md)).

---

## 4. Harness capabilities → endpoints map

What a DC wants to ask during a POV, and which endpoint answers it:

| DC question | Endpoint(s) | Doc |
|-------------|-------------|-----|
| Is the tenant alive / healthy? | `healthcheck` (GET) → fallback `get_endpoints` limit 1 | [13](13-healthcheck-metrics.md) |
| Is data landing, from which sources, at what volume/EPS? | XQL over `metrics_*` | [13](13-healthcheck-metrics.md) · [07](07-xql-api.md) |
| Did my seeded detection fire? | `get_alerts_multi_events` (+ `matcher`) or XQL `dataset = alerts` | [04](04-alerts-api.md) · [07](07-xql-api.md) |
| What incidents did the POV generate? | `get_incidents` → `get_incident_extra_data` | [03](03-incidents-api.md) |
| Are the target endpoints connected & on-policy? | `get_endpoints` / `get_endpoint`, `agents_reports` | [05](05-endpoints-api.md) · [09](09-audit-api.md) |
| Who changed detection config mid-POV? | `management_logs` | [09](09-audit-api.md) |
| Arbitrary telemetry / process causality | XQL over `xdr_data` | [07](07-xql-api.md) |
| XQL budget left before a big sweep? | `xql/get_quota` | [07](07-xql-api.md) |

**XQL is the harness's universal read** — most questions can be answered by XQL alone, which
is why [`07-xql-api.md`](07-xql-api.md) is the flagship page. Prefer XQL over narrow REST
calls when the data lives in a dataset (alerts, endpoints/assets, metrics).

---

## 5. Reconciliation flow (detection efficacy, evidence-backed)

The end-to-end loop the harness enables (parked "detection-validation" track — build on the
Health slice first):

```
run launched ──▶ orchestrator seeds Result rows (expected_detections)
                          │
   (bounded poll, opt-in) ▼
        XsiamClient.get_alerts_multi_events(since=run.start, until=now)
                          │  normalize → ObservedAlert[]
                          ▼
        matcher(observed, seeded_results, window)   # pure, testable
                          │  technique / detection_id / name match
                          ▼
        Result.observed_at set ──▶ mttd_seconds = observed_at − executed_at
```

- Poll on a **bounded interval** within a window (e.g. every 30–60s for N minutes), never a
  tight loop. Respect `xql/get_quota` and the **max-4-concurrent-XQL** tenant constraint.
- Every transport/auth/parse failure is **non-fatal** — degrade to "unvalidated", never crash
  a POV run.

---

## 6. Safety, rate, and blast-radius rules

- **Least-privilege key:** a read-only custom role ([`01-authentication.md`](01-authentication.md) §4).
  A leaked harness key must not be able to isolate a customer host.
- **Backoff + jitter** on `429`/`500`, capped retries, global concurrency cap so a POV can't
  DoS a customer tenant ([`02-conventions.md`](02-conventions.md) §4).
- **Quota-aware:** check `get_quota` before large XQL sweeps; XQL is metered.
- **Secrets stay in the vault:** key + key id live only in the Fernet store
  (`CORTEXSIM_MASTER_KEY` / `CORTEXSIM_SECRET` boot guard) — never in scenario YAML, git,
  logs, SSE frames, or push bundles.
- **Opt-in outbound:** `CORTEXSIM_AUTO_RECONCILE` off by default; `/api/xsiam` operations run
  only against a registered, verified tenant.

---

## 7. Build order (recommended)

1. **`XsiamClient`** — generalize the connector's auth/transport/envelope into the
   [`02-conventions.md`](02-conventions.md) §6 contract. Injectable transport; typed errors.
2. **Health slice (Phase-9 Slice 1):** `healthcheck` + `metrics_*` XQL + ad-hoc XQL
   pass-through under `/api/xsiam`. Pin the exact `reply`/`healthcheck`/`timeframe` envelopes
   against a real tenant in the env-gated smoke test.
3. **Endpoint/incident reads:** `get_endpoints`, `get_incidents` for POV readiness views.
4. **Reconciliation (parked track):** wire `get_alerts_multi_events` → `matcher` → MTTD.

## 8. Open items to pin against a live tenant

The vault flags many fields **⚠ verify** because the docs portal is bot-protected. Before the
harness depends on them, confirm against the target tenant's platform version in the
env-gated smoke test:

- Exact `reply` wrapping and the `healthcheck` body shape.
- XQL `timeframe` shape, `query_cost` / `remaining_quota` fields, the 1000-row inline cap and
  `stream_id` branch behavior.
- `metrics_*` dataset and field names (Data Ingestion Monitoring must be enabled).
- Uppercase `AUDIT_*` / `agents_reports` field spellings.
- Any `hash_exceptions` / IOC / distribution paths (least certain — see those pages).

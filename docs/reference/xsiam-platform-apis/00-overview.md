# Cortex XSIAM Platform APIs — Overview

> **Source of truth:** [Cortex XSIAM Platform APIs](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-Platform-APIs/Cortex-XSIAM-Platform-APIs)
> (Palo Alto Networks documentation portal). The interactive reference is mirrored on
> [Stoplight (`cortex-panw.stoplight.io`)](https://cortex-panw.stoplight.io/docs/cortex-xdr/).
>
> **Why this vault exists:** the POV engine's next feature is an **API harness** that lets
> CortexSim make **opt-in, read-only** calls into a registered XSIAM tenant to pull
> incidents, alerts, XQL results, endpoint state, audit logs, and health/metrics — closing
> the detection-efficacy loop with evidence instead of manual checkboxes. This vault is the
> engineering reference the harness is built against. See
> [`99-harness-design-notes.md`](99-harness-design-notes.md) for how it maps onto
> `core/connectors/` and the credential vault.
>
> **Accuracy note:** the PANW docs portal and Stoplight mirror are bot-protected and could
> not be scraped directly from the build environment. Endpoint names, the request/response
> envelope, the base-URL pattern, and the authentication scheme below were verified against
> public client libraries (e.g. `ebarti/cortex-xdr-client`) and PANW LIVEcommunity threads.
> Anything marked **⚠ verify** should be confirmed against the live portal for the target
> tenant's platform version before the harness depends on it.

---

## 1. What "Platform APIs" means

Cortex XSIAM exposes a single REST surface — the **Platform APIs** (historically the
"Cortex XDR public API"). XSIAM is the superset product: it inherits the entire XDR public
API (endpoints, response actions, incidents, alerts, XQL, scripts, audit, distributions)
and adds SIEM-scale capabilities (data ingestion, datasets, correlation, health/metrics).

For our purposes the surface splits into three intents:

| Intent | Examples | Harness relevance |
|--------|----------|-------------------|
| **Read / pull** | `get_incidents`, `get_alerts_multi_events`, XQL, `get_endpoints`, audit logs, `healthcheck` | ✅ **Primary** — this is what the harness does |
| **Ingest / push** | `insert_parsed_alerts`, `insert_cef_alerts`, HTTP Log Collector | ⚠ Out of scope for now (CortexSim generates signal via agents, not the API) |
| **Act / respond** | isolate, scan, `run_script`, quarantine, blocklist | 🚫 Out of scope — the harness never writes to or actions the tenant |

The harness is deliberately confined to the **read/pull** column. See the Phase-1 rule in
`CLAUDE.md` and the Phase-9 relaxation
(`docs/superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md`).

---

## 2. Base URL / FQDN

Every call has the shape:

```
https://api-{fqdn}/public_api/v1/{api_name}/{call_name}/
```

- `{fqdn}` — the tenant's unique host. If the console is at
  `https://mytenant.xsiam.us.paloaltonetworks.com`, the API host is
  `https://api-mytenant.xsiam.us.paloaltonetworks.com`. (XDR-era tenants use `.xdr.`.)
- The exact **API URL** is shown in the tenant console under
  **Settings → Configurations → Integrations → API Keys → Copy URL** — always prefer the
  string the console hands you over reconstructing it, since regional/edge routing can
  differ.
- `{api_name}` — the API family (`endpoints`, `alerts`, `incidents`, `xql`, `scripts`,
  `audits`, `distributions`, …).
- `{call_name}` — the specific operation (`get_endpoints`, `start_xql_query`, …).
- Regions seen in the wild: `us`, `eu`, `apac` (e.g. `.xsiam.eu.paloaltonetworks.com`).
- The trailing slash matters on some calls — keep it.

Almost all requests are `POST` with `Content-Type: application/json`, even read-only ones —
the request body carries the filter. The one notable exception is
`/public_api/v1/healthcheck` (a `GET`, license-gated); see
[`13-healthcheck-metrics.md`](13-healthcheck-metrics.md).

---

## 3. Versioning

`v1` is the only public major version. Field-level additions are made backward-compatibly;
new capabilities appear as new `{call_name}`s rather than a `v2`. XSIAM platform releases
(3.x today) may add fields — treat unknown response fields as forward-compatible and don't
fail closed on them.

---

## 4. Vault map

| File | Contents |
|------|----------|
| [`00-overview.md`](00-overview.md) | This file — surface, base URL, versioning |
| [`01-authentication.md`](01-authentication.md) | Standard + Advanced API keys, header/signature construction, RBAC scoping |
| [`02-conventions.md`](02-conventions.md) | Request/response envelope, filters, pagination, sorting, rate limits, error codes, timestamps |
| [`03-incidents-api.md`](03-incidents-api.md) | `get_incidents`, `get_incident_extra_data`, `update_incident` |
| [`04-alerts-api.md`](04-alerts-api.md) | `get_alerts_multi_events`, `insert_parsed_alerts`, `insert_cef_alerts` |
| [`05-endpoints-api.md`](05-endpoints-api.md) | `get_endpoints`, `get_endpoint`, isolate/unisolate/scan/delete, policy |
| [`06-response-actions-api.md`](06-response-actions-api.md) | file retrieval, quarantine/restore, blocklist/allowlist, action status |
| [`07-xql-api.md`](07-xql-api.md) | `start_xql_query`, `get_query_results`, `get_query_results_stream`, quota |
| [`08-scripts-api.md`](08-scripts-api.md) | scripts library, `run_script`, snippet, execution status/results/files |
| [`09-audit-api.md`](09-audit-api.md) | `get_audit_management_logs`, `get_audit_agent_reports` |
| [`10-threat-intel-iocs-api.md`](10-threat-intel-iocs-api.md) | IOC insert/get/enable/disable/delete |
| [`11-distributions-assets-api.md`](11-distributions-assets-api.md) | agent installers, versions, device-control violations, assets |
| [`12-data-ingestion-api.md`](12-data-ingestion-api.md) | HTTP Log Collector / alert insertion (push side — reference only) |
| [`13-healthcheck-metrics.md`](13-healthcheck-metrics.md) | `healthcheck`, XQL over `metrics_*` datasets (Phase-9 health/config) |
| [`99-harness-design-notes.md`](99-harness-design-notes.md) | How the POV engine consumes all of the above |

---

## 5. At-a-glance API family table

| `api_name` | Family | Key calls | Doc |
|------------|--------|-----------|-----|
| `incidents` | Incidents | `get_incidents`, `get_incident_extra_data`, `update_incident` | [03](03-incidents-api.md) |
| `alerts` | Alerts | `get_alerts_multi_events`, `insert_parsed_alerts`, `insert_cef_alerts` | [04](04-alerts-api.md) |
| `endpoints` | Endpoints & agent response | `get_endpoints`, `get_endpoint`, `isolate`, `unisolate`, `scan`, `delete` | [05](05-endpoints-api.md) |
| `endpoints` / `actions` | Response actions | `file_retrieval`, `quarantine`, `restore`, `get_quarantine_status`, `get_action_status` | [06](06-response-actions-api.md) |
| `hash_exceptions` | Block/allow lists | `blocklist`, `allowlist`, `get_blocklist`, `get_allowlist` | [06](06-response-actions-api.md) |
| `xql` | Query engine | `start_xql_query`, `get_query_results`, `get_query_results_stream`, `get_quota` | [07](07-xql-api.md) |
| `scripts` | Scripts library & exec | `get_scripts`, `run_script`, `run_snippet_code_script`, `get_script_execution_results` | [08](08-scripts-api.md) |
| `audits` | Audit logs | `management_logs`, `agents_reports` | [09](09-audit-api.md) |
| `indicators` / `iocs` | Threat intel | `insert_jsons`, `insert_csv`, enable/disable/delete | [10](10-threat-intel-iocs-api.md) |
| `distributions` | Agent installers | `get_versions`, `create`, `get_status`, `get_dist_url` | [11](11-distributions-assets-api.md) |
| `device_control` | Device control | `get_violations` | [11](11-distributions-assets-api.md) |
| `healthcheck` | Tenant health | `healthcheck` + XQL `metrics_*` | [13](13-healthcheck-metrics.md) |

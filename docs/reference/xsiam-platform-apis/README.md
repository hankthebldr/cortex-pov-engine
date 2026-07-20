# Cortex XSIAM Platform APIs — Engineering Vault

Reference documentation for the **Cortex XSIAM Platform APIs**, written to support building
CortexSim's next feature: a **read-only API harness** that lets the POV engine make opt-in
calls into a registered XSIAM tenant and pull incidents, alerts, XQL results, endpoint state,
audit logs, and health/metrics.

> **Upstream source:** [Cortex XSIAM Platform APIs](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM-Platform-APIs/Cortex-XSIAM-Platform-APIs)
> · interactive mirror on [Stoplight](https://cortex-panw.stoplight.io/docs/cortex-xdr/).
>
> **Provenance / accuracy:** the PANW docs portal and Stoplight mirror are bot-protected and
> could not be scraped from the build environment (HTTP 403). The base-URL pattern, the
> standard + advanced auth scheme, the `{request_data}`→`{reply}` envelope, and the endpoint
> names were reconstructed from public client libraries (`ebarti/cortex-xdr-client`), PANW
> LIVEcommunity threads, the repo's shipped `core/connectors/xsiam.py`, and the Phase-9 design
> spec. **Fields marked `⚠ verify` must be confirmed against the live tenant** (via the
> env-gated smoke test) before the harness depends on them.

---

## Read this first

1. [`00-overview.md`](00-overview.md) — the surface, base URL / FQDN, versioning, API-family map
2. [`01-authentication.md`](01-authentication.md) — standard + advanced API keys, the SHA-256 signature, RBAC scoping
3. [`02-conventions.md`](02-conventions.md) — request/response envelope, filters, pagination, rate limits, errors, timestamps, the client contract
4. [`99-harness-design-notes.md`](99-harness-design-notes.md) — **how CortexSim consumes all of this** (ties to `core/connectors/` + Phase-9 spec)

## Per-family references

| # | Family | `api_name` | Harness use |
|---|--------|-----------|-------------|
| [03](03-incidents-api.md) | Incidents | `incidents` | ✅ read |
| [04](04-alerts-api.md) | Alerts | `alerts` | ✅ read (+ ⚠ push variants documented) |
| [05](05-endpoints-api.md) | Endpoints | `endpoints` | ✅ read (🚫 action calls documented) |
| [06](06-response-actions-api.md) | Response actions | `endpoints`/`actions`/`hash_exceptions` | 🚫 mostly action; a few read status calls |
| [07](07-xql-api.md) | **XQL query engine** | `xql` | ✅ **flagship read** — the universal pull |
| [08](08-scripts-api.md) | Scripts | `scripts` | 🚫 action (read status calls documented) |
| [09](09-audit-api.md) | Audit logs | `audits` | ✅ read |
| [10](10-threat-intel-iocs-api.md) | Threat intel / IOCs | `indicators` | ⚠ mostly push, out of scope |
| [11](11-distributions-assets-api.md) | Distributions / device control / assets | `distributions`/`device_control` | ✅ some reads (prefer XQL for assets) |
| [12](12-data-ingestion-api.md) | Data ingestion (push) | collector / `alerts` | ⚠ reference-only — **do not implement** |
| [13](13-healthcheck-metrics.md) | Health & metrics | `healthcheck` + `xql` | ✅ read — **Phase-9 track** |

## The one-paragraph model

Every call is an HTTP `POST` to `https://api-{fqdn}/public_api/v1/{api_name}/{call_name}/`
(the sole exception is `healthcheck`, a `GET`), authenticated with an API key + key id
(standard) or a per-request SHA-256 signature over `key + nonce + timestamp` (advanced). The
JSON body wraps everything in `request_data` (filters, `search_from`/`search_to`, `sort`); the
response wraps everything in `reply`. Timestamps are epoch **milliseconds**. **XQL** is
async — start a query, poll for results, and stream sets over 1000 rows. The harness stays on
the **read** side of this surface and never writes to the tenant.

---

_Vault authored on branch `claude/xsiam-api-docs-gkebw3`. Keep `⚠ verify` markers until each
field is pinned against a real tenant; update this README's table if families are added._

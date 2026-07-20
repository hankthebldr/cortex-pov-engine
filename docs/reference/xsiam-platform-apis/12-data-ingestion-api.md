# Data Ingestion API (PUSH side — reference only)

> **Reference-only. Out of harness scope. DO NOT IMPLEMENT in the read-only harness.**
> This page documents how data *gets into* a Cortex XSIAM/XDR tenant so engineers do not
> confuse the **ingestion (PUSH)** path with the **query/read (PULL)** path the harness
> actually uses. CortexSim generates signal into a tenant via its **own agents and EAL
> plugins** (see `CLAUDE.md` — identity-harness execution + the EAL Traffic Simulator), **not**
> via any ingestion API. It then reads the resulting datasets back with XQL over the
> read-only `public_api` surface. The auth and URL base here are **different** from the
> public_api calls in [`01-authentication.md`](./01-authentication.md) and
> [`02-conventions.md`](./02-conventions.md): ingestion uses a **collector token**, not the
> API-key/`x-xdr-auth-id` pair, and (for the HTTP Log Collector) a **`/logs/...` URL base**,
> not `/public_api/...`. Nothing on this page should be wired into the harness client.

---

## Scope / DO NOT IMPLEMENT

| | Read path (this vault's harness) | Ingestion path (this page) |
|---|---|---|
| Direction | PULL — read datasets/alerts/incidents out | PUSH — write logs/alerts in |
| Auth | API key + `x-xdr-auth-id` (see [01](./01-authentication.md)) | Collector token (per-collector) or `public_api` key (alerts insert) |
| URL base | `https://api-{fqdn}/public_api/v1/...` | `https://api-{fqdn}/logs/v1/...` (collector) or `.../public_api/v1/alerts/...` |
| In CortexSim? | **Yes** — the harness | **No** — the *environment* (agents/EAL) puts data in |
| Implement here? | Yes | **No — reference only** |

CortexSim's job is to **generate** signal (agent-executed TTPs, EAL synthetic traffic/log
emitters like `idp_signin_emulator`, `email_emitter`, `llm_provider_egress`), which the
tenant's *own* collectors/parsers ingest. The harness never calls an ingestion endpoint. If a
future push feature is built, it belongs to the environment-provisioning layer, not this
read-only harness.

---

## 1. HTTP Log Collector (Custom Collector)

The tenant console mints a **Custom HTTP Log Collector** under
**Data Sources / Collectors → Custom Collectors → HTTP Log Collector**. It issues a **URL**
and a **collector API key (token)**. Raw JSON/NDJSON events POSTed to that URL land in a
**dataset chosen in the collector config**, which XQL can later read.

| Item | Value / shape | Notes |
|------|---------------|-------|
| Endpoint | `https://api-{fqdn}/logs/v1/event` | ⚠ verify — exact path is shown by the console; may be `/logs/v1/event` or a collector-specific path |
| Method | `POST` | |
| Auth header | `Authorization: {collector_api_key}` | ⚠ verify — the **collector token**, NOT the `public_api` key; no `x-xdr-auth-id` |
| `Content-Type` | `application/json` (single/array) or `application/x-ndjson` (newline-delimited) | ⚠ verify per collector |
| Body | JSON object, JSON array, or NDJSON of event objects | Schema is free-form; the parser/XDM maps it |
| Target dataset | Chosen in the **collector config** in the console | Not set per-request; fixed by the collector |

### Optional vendor/product routing

| Field / header | Purpose | Notes |
|----------------|---------|-------|
| `vendor` / `product` | Route events to a `{vendor}_{product}_raw` dataset | ⚠ verify — may be query-string params, headers, or body fields per collector config |
| Custom dataset name | Override default dataset | ⚠ verify — set in console, not always per-request |

### Minimal curl example (collector token — note the different auth & base)

```bash
# COLLECTOR_TOKEN is the token issued by the HTTP Log Collector config —
# it is NOT the public_api API key and there is NO x-xdr-auth-id header.
curl -sS -X POST \
  "https://api-${FQDN}/logs/v1/event" \
  -H "Authorization: ${COLLECTOR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '[
        {"event_time":"2026-07-20T12:00:00Z","user":"svc_backup","action":"login","src_ip":"10.0.0.9"},
        {"event_time":"2026-07-20T12:00:03Z","user":"svc_backup","action":"file_read","path":"/etc/shadow"}
      ]'
```

Compare with the `public_api` call in [`01-authentication.md`](./01-authentication.md): that
call uses `x-xdr-auth-id` + the API key against `/public_api/v1/...`. **This collector call
uses neither** — a single `Authorization: {collector_token}` header against `/logs/v1/...`.
The target dataset is whatever the collector was configured to write; the harness later reads
it with XQL (`dataset = <name>`), which is the only side CortexSim implements.

---

## 2. `insert_parsed_alerts` / `insert_cef_alerts` (push **alerts**, not raw logs)

These push **alerts** (already-shaped, not raw telemetry) and — unlike the HTTP Log
Collector — use the **standard `public_api` auth + key** and URL base. They are still PUSH
and still out of harness scope. Full request shapes live in
[`04-alerts-api.md`](./04-alerts-api.md).

| `call_name` (`api_name = alerts`) | Path | Body shape | Harness |
|-----------------------------------|------|-----------|---------|
| `insert_parsed_alerts` | `/public_api/v1/alerts/insert_parsed_alerts/` | JSON alert objects in `request_data` | 🚫 PUSH — CortexSim seeds signal via agents/EAL, not this call |
| `insert_cef_alerts` | `/public_api/v1/alerts/insert_cef_alerts/` | CEF-formatted alert strings | 🚫 PUSH — CEF ingestion path, out of read-only scope |

> Auth for these two is the **public_api key + `x-xdr-auth-id`** ([01](./01-authentication.md)),
> **not** a collector token — a different mechanism from §1 above. Same envelope conventions
> as [`02-conventions.md`](./02-conventions.md).

---

## 3. Alternative ingestion paths (conceptual only — no schemas)

Listed so engineers recognize them, not to implement. Each has its own auth/transport,
none used by the harness.

| Path | What it is | Where it lands |
|------|-----------|----------------|
| **Broker VM / syslog** | On-prem Broker VM receives syslog/CEF/LEEF over TCP/UDP and forwards to the tenant | Vendor-specific `*_raw` datasets |
| **XSOAR / integration-based** | XSOAR playbooks or content-pack integrations push events/alerts via their own creds | Alerts / datasets per integration |
| **Cloud/SaaS collectors** | Native cloud connectors (AWS/GCP/Azure/M365, etc.) pull provider logs | Provider-specific datasets |
| **XDR agent telemetry** | Cortex XDR agents stream endpoint EDR telemetry directly | Endpoint datasets (`xdr_data`, etc.) |

⚠ verify — auth, ports, and dataset naming for all of these are **tenant/collector-config
specific**; consult the tenant console, not this page, for exact values.

---

## ⚠ verify bullets

- **HTTP Log Collector endpoint path** — `https://api-{fqdn}/logs/v1/event` is the documented
  shape, but the exact path is **shown by the console** and may be collector-specific; confirm
  the URL the console issues.
- **Collector auth header** — `Authorization: {collector_api_key}` (bare token, no
  `x-xdr-auth-id`); confirm header name/format against the specific collector.
- **Content-Type / body format** — JSON vs NDJSON (`application/x-ndjson`) acceptance varies
  per collector; confirm.
- **Vendor/product routing** — whether routing is via query-string, headers, or body fields is
  collector-config specific; confirm.
- **Target dataset naming** — set in the collector config, not per-request; confirm the exact
  dataset name before writing XQL against it.
- Paths/headers here were **not** fetched from the PANW portal/Stoplight (403); treat all
  ingestion specifics as tenant-dependent.

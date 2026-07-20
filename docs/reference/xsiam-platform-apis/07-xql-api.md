# XQL API (`api_name = xql`)

> XQL is the harness's **primary read surface**. Where the [alerts](./04-alerts-api.md) and
> incidents families hand back curated verdict rows, XQL runs an arbitrary
> [XQL](https://docs.paloaltonetworks.com/cortex/cortex-xdr) statement over *any* dataset in
> the tenant — raw endpoint causality (`xdr_data`), the normalized alert store (`alerts`),
> and ingestion/health telemetry (`metrics_*`) — so almost every reconciliation and health
> probe the POV engine runs ultimately bottoms out here. It is a strictly **read-only,
> asynchronous** family: you *start* a query, *poll* for its results, and *stream* the tail
> when the result set is large. All calls reuse the shared
> [authentication](./01-authentication.md) headers and the request/response
> [conventions](./02-conventions.md) — envelope, error shape, backoff, and epoch-ms time
> handling are **not** repeated here.

Base URL pattern: `POST https://api-{fqdn}/public_api/v1/xql/{call_name}/` with the body
wrapped in `{"request_data": {...}}` and the response in `{"reply": {...}}`.

> **The two facts to keep in front of you the whole time:**
> 1. A single non-stream result page maxes at **1000 rows**. Over that, results come back as
>    a `stream_id`, not inline `data`. The harness must **always** handle both branches.
> 2. XQL runs on a metered **compute quota**. Guard large sweeps with `get_quota`
>    ([§4](#get_quota--public_apiv1xqlget_quota)) before you fan out.

---

## `start_xql_query` — `/public_api/v1/xql/start_xql_query/`

**Purpose.** Submit an XQL statement for **asynchronous** execution. Returns an
`execution_id` (a GUID) immediately — it does **not** block on results. You then poll
`get_query_results` with that id.

### Key request fields

Supplied as top-level keys inside `request_data` (this family does **not** use the
`{field, operator, value}` filter grammar — the filtering lives inside the XQL string):

| Field | Type | Notes |
|-------|------|-------|
| `query` | string, **required** | A valid, complete XQL statement. Put filtering/projection inside the query itself (`filter … \| fields …`). Do **not** append a presentational `limit N` stage that fights the API paging — cap rows with the `limit` field on `get_query_results` instead. |
| `tenants` | array of strings, optional | Tenant IDs for multi-tenant / MSSP fan-out. **Omit for a single tenant** (the common POV case). |
| `timeframe` | object, optional | Either a relative window `{"relativeTime": <ms>}` (e.g. `86400000` = last 24h) **or** an absolute range `{"from": <epoch ms>, "to": <epoch ms>}`. See [conventions §5](./02-conventions.md). |

### Minimal request

```json
{
  "request_data": {
    "query": "dataset = alerts | filter severity in (\"high\",\"critical\") | fields alert_id, name, severity, mitre_technique_id_and_name",
    "timeframe": { "relativeTime": 86400000 }
  }
}
```

### Trimmed response

```json
{ "reply": "a1b2c3d4-0000-1111-2222-3344556677aa" }
```

> `reply` is the bare `execution_id` **string** (a GUID), not an object. Carry it straight
> into `get_query_results` as `query_id`.

**Harness relevance.** ✅ Read (async start). Every XQL-backed reconciliation and health
probe begins here.

---

## `get_query_results` — `/public_api/v1/xql/get_query_results/`

**Purpose.** Poll a running query by its `execution_id`. Returns a `status`; on
`SUCCESS` it returns **either** inline rows (`results.data[]`, when the total is ≤ 1000)
**or** a `stream_id` (when > 1000 rows) that you pull via `get_query_results_stream`.

### Key request fields

| Field | Type | Notes |
|-------|------|-------|
| `query_id` | string, **required** | The `execution_id` from `start_xql_query`. |
| `pending_flag` | bool | If `true`, the call **returns immediately** with `status: PENDING` when results aren't ready (non-blocking poll). If `false`/omitted it may block briefly. ⚠ verify blocking semantics per tenant. |
| `limit` | int | Max inline rows to return, **≤ 1000**. |
| `format` | string | `"json"`. |

### Minimal request

```json
{
  "request_data": {
    "query_id": "a1b2c3d4-0000-1111-2222-3344556677aa",
    "pending_flag": true,
    "limit": 1000,
    "format": "json"
  }
}
```

### Trimmed response — still running

```json
{ "reply": { "status": "PENDING" } }
```

### Trimmed response — done, inline (≤ 1000 rows)

```json
{
  "reply": {
    "status": "SUCCESS",
    "number_of_results": 2,
    "query_cost": { "<tenant-id>": 0.0013 },
    "remaining_quota": 999.87,
    "results": {
      "data": [
        {
          "alert_id": "1042",
          "name": "Behavioral Threat Detected",
          "severity": "high",
          "mitre_technique_id_and_name": "T1003 - OS Credential Dumping"
        },
        {
          "alert_id": "1043",
          "name": "Reverse Shell",
          "severity": "critical",
          "mitre_technique_id_and_name": "T1059 - Command and Scripting Interpreter"
        }
      ]
    }
  }
}
```

### Trimmed response — done, streamed (> 1000 rows)

```json
{
  "reply": {
    "status": "SUCCESS",
    "number_of_results": 24500,
    "query_cost": { "<tenant-id>": 0.084 },
    "remaining_quota": 999.79,
    "results": {
      "stream_id": "stream-7f0e...c1"
    }
  }
}
```

> `status` is one of `PENDING | SUCCESS | FAIL`. On `FAIL`, surface the error and stop — do
> not re-poll. On `SUCCESS`, branch on the presence of `results.data` vs `results.stream_id`;
> **never** assume `data` is there. `query_cost` is keyed by tenant id; `remaining_quota`
> mirrors what `get_quota` reports. ⚠ verify exact `query_cost` / `remaining_quota` field
> shapes.

**Harness relevance.** ✅ Read. The poll-until-`SUCCESS` core of every XQL read; the
`stream_id` branch is mandatory, not optional.

---

## `get_query_results_stream` — `/public_api/v1/xql/get_query_results_stream/`

**Purpose.** Retrieve a result set larger than 1000 rows, referenced by the `stream_id` from
`get_query_results`. The response body is **gzip-compressed NDJSON** — one JSON result row
per line.

### Key request fields

| Field | Type | Notes |
|-------|------|-------|
| `stream_id` | string, **required** | The `results.stream_id` from `get_query_results`. |
| `is_gzip_compressed` | bool | Request gzip compression of the stream (`true` is the norm). |

### Minimal request

```json
{
  "request_data": {
    "stream_id": "stream-7f0e...c1",
    "is_gzip_compressed": true
  }
}
```

### Response body (after gunzip)

Not the usual `{"reply": {...}}` envelope — the body is **raw NDJSON** (newline-delimited
JSON), one result row per line, gzip-compressed on the wire (`Content-Encoding: gzip` may
also be set):

```
{"alert_id":"1042","name":"Behavioral Threat Detected","severity":"high"}
{"alert_id":"1043","name":"Reverse Shell","severity":"critical"}
{"alert_id":"1044","name":"Suspicious DNS","severity":"medium"}
```

> Decompress, then parse **line by line** (do not `json.loads` the whole body). ⚠ verify
> whether `is_gzip_compressed: false` yields plaintext NDJSON on every tenant — assume gzip
> and gunzip defensively.

**Harness relevance.** ✅ Read. The large-result-set path — required for any broad sweep
(full endpoint causality dumps, wide `metrics_*` windows).

---

## `get_quota` — `/public_api/v1/xql/get_quota/`

**Purpose.** Report the tenant's XQL **compute quota** — how much is allowed and how much has
been consumed. Cheap and side-effect-free; call it to guard large sweeps.

### Key request fields

None — send an empty `request_data`.

### Minimal request

```json
{ "request_data": {} }
```

### Trimmed response

```json
{
  "reply": {
    "license_quota": 1000.0,
    "used_quota": 12.4
  }
}
```

> ⚠ verify exact field names — the payload reports allowed vs. used XQL compute for the
> tenant, but the keys may be `license_quota` / `used_quota` (as above) or similar. Treat the
> difference as remaining budget.

**Harness relevance.** ✅ Read. Used as a **preflight guard**: before a wide sweep or a
multi-scenario reconciliation fan-out, check remaining budget so a POV run can't exhaust a
customer's XQL quota.

---

## Harness relevance summary

| Call | Direction | Harness | Why |
|------|-----------|:------:|-----|
| `start_xql_query` | read (async) | ✅ | Submit arbitrary XQL — the harness's main read surface |
| `get_query_results` | read | ✅ | Poll to `SUCCESS`; branch inline `data` vs `stream_id` |
| `get_query_results_stream` | read | ✅ | Pull > 1000-row result sets (gzipped NDJSON) |
| `get_quota` | read | ✅ | Preflight guard before large sweeps |

Legend: ✅ read (in harness). **All four XQL calls are read-only and IN SCOPE.** There is no
push/ingest call in this family — XQL never writes to the tenant.

---

## Worked example — the async lifecycle

The whole family is one three-step dance: **start → poll → (maybe) stream**. Advanced-auth
header construction (`${SIG}` / `${NONCE}` / `${TS}`) is defined in
[01-authentication.md §2](./01-authentication.md); compute them fresh per request.

### (a) curl walkthrough

```bash
# 0. (optional) preflight the quota so a wide sweep can't exhaust the tenant
curl -sS -X POST "https://api-${FQDN}/public_api/v1/xql/get_quota/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{"request_data": {}}'
# -> {"reply": {"license_quota": 1000.0, "used_quota": 12.4}}

# 1. START — submit the XQL, capture the execution_id (a bare GUID string)
EXEC_ID=$(curl -sS -X POST "https://api-${FQDN}/public_api/v1/xql/start_xql_query/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{
        "request_data": {
          "query": "dataset = alerts | filter severity in (\"high\",\"critical\") | fields alert_id, name, severity, mitre_technique_id_and_name",
          "timeframe": { "relativeTime": 86400000 }
        }
      }' | jq -r '.reply')

# 2. POLL — repeat until status is SUCCESS or FAIL (recompute auth headers each call)
curl -sS -X POST "https://api-${FQDN}/public_api/v1/xql/get_query_results/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d "{\"request_data\": {\"query_id\": \"${EXEC_ID}\", \"pending_flag\": true, \"limit\": 1000, \"format\": \"json\"}}"
# PENDING -> sleep, back off, poll again (see conventions §4)
# SUCCESS -> reply.results.data[]  (inline, <=1000 rows)  OR  reply.results.stream_id (>1000)

# 3. STREAM — only if step 2 returned a stream_id; body is gzipped NDJSON
curl -sS -X POST "https://api-${FQDN}/public_api/v1/xql/get_query_results_stream/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{"request_data": {"stream_id": "stream-7f0e...c1", "is_gzip_compressed": true}}' \
  | gunzip | while read -r line; do echo "$line" | jq -c '.'; done
```

### (b) Python pseudocode (matches the [conventions §6](./02-conventions.md) client contract)

```python
import gzip, io, json, time

def xql_run(client: "XsiamClient", query: str, timeframe: dict, limit: int = 1000) -> list[dict]:
    # 0. guard: don't fan out if the tenant is near its XQL compute ceiling
    q = client.call("xql", "get_quota", {})                       # reply = {license_quota, used_quota}
    if q["license_quota"] - q["used_quota"] <= 0:
        raise RuntimeError("XQL quota exhausted; refusing sweep")

    # 1. start -> reply is the bare execution_id string (a GUID)
    exec_id = client.call("xql", "start_xql_query",
                          {"query": query, "timeframe": timeframe})

    # 2. poll until terminal (exponential backoff + jitter per conventions §4)
    for attempt in range(30):
        r = client.call("xql", "get_query_results",
                        {"query_id": exec_id, "pending_flag": True,
                         "limit": limit, "format": "json"})
        if r["status"] == "SUCCESS":
            break
        if r["status"] == "FAIL":
            raise RuntimeError(f"XQL query failed: {r}")
        time.sleep(min(2 ** attempt, 30))                          # PENDING -> back off
    else:
        raise TimeoutError("XQL query never reached SUCCESS")

    # 3. branch: inline data (<=1000) vs stream_id (>1000) — ALWAYS handle both
    results = r["results"]
    if "data" in results:
        return results["data"]
    raw = client.call_raw("xql", "get_query_results_stream",     # raw body, not reply-unwrapped
                          {"stream_id": results["stream_id"], "is_gzip_compressed": True})
    body = gzip.decompress(raw)                                   # gzipped NDJSON
    return [json.loads(line) for line in io.BytesIO(body) if line.strip()]
```

> The stream call returns a **raw body**, not the `{"reply": {...}}` envelope, so the harness
> client needs a `call_raw` (or a `stream=True` flag) that bypasses envelope-unwrapping.
> Note it here so the client contract stays honest.

---

## Useful XQL for the harness

Starter queries for POV detection reconciliation and health. XQL is
whitespace/`|`-delimited stages: `dataset = … | filter … | fields …`. Dataset and field
names below are the common ones, but **⚠ verify against the target tenant's schema** — parser
config and content packs shift field availability.

```text
# Pull alerts seeded by a POV run in a time window (reconcile against Result rows)
dataset = alerts
| filter creation_time > to_timestamp(1719792000000, "MILLIS")   # ⚠ verify to_timestamp signature
| fields alert_id, name, severity, mitre_technique_id_and_name, source
```

```text
# Endpoint process causality for one host (MTTD evidence for EDR/CDR scenarios)
dataset = xdr_data
| filter agent_hostname = "web-prod-03" and event_type = ENUM.PROCESS
| fields action_process_image_name, action_process_command_line,
         causality_actor_process_image_name, actor_process_username, _time
```

```text
# Network egress shape for NDR scenarios (C2 beacon / exfil reconciliation)
dataset = xdr_data
| filter event_type = ENUM.NETWORK and action_remote_ip != null
| fields agent_hostname, action_remote_ip, action_remote_port,
         dst_action_external_hostname, action_total_download, action_total_upload   # ⚠ verify field names
```

```text
# Detections by MITRE technique — coverage roll-up for the POV heatmap
dataset = alerts
| filter creation_time > to_timestamp(1719792000000, "MILLIS")
| comp count() as hits by mitre_technique_id_and_name, severity
```

```text
# Ingestion / health metrics — is the tenant actually receiving data?
dataset in (metrics_*)                                            # ⚠ verify dataset name(s)
| filter _time > to_timestamp(1719792000000, "MILLIS")
| comp sum(ingested_bytes) as total_bytes by dataset_name         # ⚠ verify metric field names
```

```text
# Agent connectivity health (endpoint fleet freshness before a run)
dataset = xdr_data
| filter event_type = ENUM.STATUS                                 # ⚠ verify event_type enum
| comp max(_time) as last_seen by agent_hostname
```

For the health/metrics side of these — the `metrics_*` datasets, `/healthcheck`, and the
opt-in read-only tenant integration — see
[13-healthcheck-metrics.md](./13-healthcheck-metrics.md).

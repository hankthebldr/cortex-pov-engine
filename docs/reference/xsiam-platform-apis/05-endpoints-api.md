# Endpoints API (`api_name = endpoints`)

> The `endpoints` family manages and inventories Cortex XDR agent-installed endpoints. This
> page follows the vault envelope and filter grammar — see [./01-authentication.md](./01-authentication.md)
> for the standard/advanced header construction and [./02-conventions.md](./02-conventions.md)
> for the `POST https://api-{fqdn}/public_api/v1/endpoints/{call_name}/` shape, the
> `{"request_data": {...}}` request / `{"reply": {...}}` response wrapping, epoch-ms
> timestamps, `{field, operator, value}` filters, and `search_from`/`search_to` offset paging
> (~100 rows/page). Only the three **read** calls (`get_endpoints`, `get_endpoint`,
> `get_policy`) belong to the read-only harness; the five **action** calls (`isolate`,
> `unisolate`, `scan`, `abort_scan`, `delete`) mutate the tenant and the harness never calls
> them — they are documented here only so the harness can recognize and refuse them.

---

## `get_endpoints` — `/public_api/v1/endpoints/get_endpoints/`

**Purpose.** Return a **condensed** list of **all** endpoints in the tenant. No filter is
required — send an empty `request_data`. Use this for a cheap inventory sweep; call
`get_endpoint` when you need full per-endpoint detail.

| Request field | Req? | Notes |
|---------------|------|-------|
| _(none)_ | — | Send `{"request_data": {}}`; this call takes no filters. |

```json
{ "request_data": {} }
```

```json
{
  "reply": [
    {
      "agent_id": "1a2b3c...",
      "endpoint_name": "web-prod-01",
      "endpoint_status": "CONNECTED",
      "ip": ["10.0.1.20"],
      "operational_status": "PROTECTED",
      "last_seen": 1719792000000
    }
  ]
}
```

- `endpoint_status` ∈ `CONNECTED | DISCONNECTED | LOST | UNINSTALLED`.
- ⚠ verify: the reply is a bare **list** on the versions observed; some versions instead
  wrap it as `reply.endpoints[]`. Harness code should accept either shape.
- ⚠ verify: the identifier key is `agent_id` on some versions and `endpoint_id` on others —
  read both.

**Harness relevance.** ✅ Primary cheap inventory / liveness sweep; no filter, one page.

---

## `get_endpoint` — `/public_api/v1/endpoints/get_endpoint/`

**Purpose.** Return a **filtered, detailed** endpoint list. This is the workhorse read: use
filters to scope by id, status, platform, or time window, and page with
`search_from`/`search_to`.

| Filter `field` | Operator | Value |
|----------------|----------|-------|
| `endpoint_id_list` | `in` | array of endpoint ids |
| `endpoint_status` | `in` | `connected` / `disconnected` |
| `dist_name` | `in` | distribution / installation-package names |
| `ip_list` | `in` | array of IPs |
| `group_name` | `in` | endpoint group names |
| `platform` | `in` | `windows` / `linux` / `macos` / `android` |
| `alias` | `in` | endpoint aliases |
| `hostname` | `in` | hostnames |
| `isolate` | `in` | `isolated` / `unisolated` |
| `scan_status` | `in` | scan-status values (⚠ verify enum) |
| `first_seen` | `gte` / `lte` | epoch ms |
| `last_seen` | `gte` / `lte` | epoch ms |

```json
{
  "request_data": {
    "filters": [
      { "field": "platform", "operator": "in", "value": ["linux"] },
      { "field": "endpoint_status", "operator": "in", "value": ["connected"] }
    ],
    "search_from": 0,
    "search_to": 100
  }
}
```

```json
{
  "reply": {
    "total_count": 42,
    "result_count": 1,
    "endpoints": [
      {
        "endpoint_id": "1a2b3c...",
        "endpoint_name": "web-prod-01",
        "endpoint_type": "AGENT_TYPE_SECONDARY",
        "endpoint_status": "CONNECTED",
        "os_type": "AGENT_OS_LINUX",
        "os_version": "Ubuntu 22.04",
        "ip": ["10.0.1.20"],
        "public_ip": ["203.0.113.10"],
        "users": ["www-data"],
        "domain": "corp.local",
        "alias": "",
        "first_seen": 1719705600000,
        "last_seen": 1719792000000,
        "content_version": "820-...",
        "installation_package": "linux_deb",
        "active_directory": null,
        "install_date": 1719705600000,
        "endpoint_version": "8.3.0",
        "is_isolated": "AGENT_UNISOLATED",
        "isolated_date": null,
        "group_name": ["prod-web"],
        "operational_status": "PROTECTED",
        "operational_status_description": null,
        "scan_status": "SCAN_STATUS_NONE",
        "content_release_timestamp": 1719705600000,
        "last_content_update_time": 1719705600000,
        "tags": {}
      }
    ]
  }
}
```

- Response follows the standard envelope: `reply.total_count` (full match count),
  `reply.result_count` (this page), `reply.endpoints[]`.
- `endpoint_type` is an `AGENT_TYPE_*` enum; `os_type` an `AGENT_OS_*` enum (⚠ verify exact
  member names).

**Harness relevance.** ✅ Detailed, filterable read — the canonical endpoint query for
scoping runs and reconciliation.

---

## `get_policy` — `/public_api/v1/endpoints/get_policy/`

**Purpose.** Return the prevention-policy name applied to a single endpoint.

| Request field | Req? | Notes |
|---------------|------|-------|
| `endpoint_id` | ✅ | Single endpoint id (⚠ verify: some versions also accept `dist_name`). |

```json
{ "request_data": { "endpoint_id": "1a2b3c..." } }
```

```json
{ "reply": { "policy_name": "Prod-Linux-Strict" } }
```

- ⚠ verify: exact response field name (`reply.policy_name`) and whether the reply nests
  additional policy metadata.

**Harness relevance.** ✅ Read-only policy lookup for a known endpoint id.

---

## Action calls — OUT OF HARNESS SCOPE 🚫

The following mutate the tenant (isolate a host from the network, trigger/abort a scan,
delete an endpoint from the console). The **read-only harness never calls them**; they are
listed so the harness can positively identify and refuse an action path.

### `isolate` — `/public_api/v1/endpoints/isolate/` 🚫

Cuts an endpoint off the network. Filter: `endpoint_id_list`. **Limited to 1000 endpoints
per request.** Response: `reply.action_id`.

### `unisolate` — `/public_api/v1/endpoints/unisolate/` 🚫

Reverses `isolate`; same request/response shape (filter `endpoint_id_list` → `reply.action_id`).

### `scan` — `/public_api/v1/endpoints/scan/` 🚫

Starts a malware scan. Filter: `endpoint_id_list`, plus optional endpoint filters
(`group_name`, `platform`, `first_seen`/`last_seen`). Response: `reply.action_id`.

### `abort_scan` — `/public_api/v1/endpoints/abort_scan/` 🚫

Cancels an in-progress scan. Response: `reply.action_id` (⚠ verify path and request shape).

### `delete` — `/public_api/v1/endpoints/delete/` 🚫

Removes endpoints from the console. Filter: `endpoint_id_list`. Destructive — inventory-only.

---

## Harness relevance summary

| Call | Path (`…/endpoints/…/`) | Kind | Harness |
|------|-------------------------|------|---------|
| `get_endpoints` | `get_endpoints/` | Read — condensed inventory of all endpoints | ✅ read |
| `get_endpoint` | `get_endpoint/` | Read — filtered, detailed | ✅ read |
| `get_policy` | `get_policy/` | Read — policy name for one endpoint | ✅ read |
| `isolate` | `isolate/` | Action — network-isolate | 🚫 action |
| `unisolate` | `unisolate/` | Action — reverse isolate | 🚫 action |
| `scan` | `scan/` | Action — start scan | 🚫 action |
| `abort_scan` | `abort_scan/` | Action — cancel scan | 🚫 action |
| `delete` | `delete/` | Action — remove from console | 🚫 action |

---

## End-to-end example — `get_endpoint` (advanced auth)

Placeholders `${SIG}`, `${NONCE}`, `${TS}` are the advanced-auth signature, nonce, and
epoch-ms timestamp — construct them per [./01-authentication.md](./01-authentication.md) §2
(`SIG = sha256_hex(API_KEY + NONCE + TS)`).

```bash
curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/endpoints/get_endpoint/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" \
  -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{
    "request_data": {
      "filters": [
        { "field": "endpoint_status", "operator": "in", "value": ["connected"] },
        { "field": "last_seen", "operator": "gte", "value": 1719705600000 }
      ],
      "search_from": 0,
      "search_to": 100
    }
  }'
```

# Incidents API

> Covers the `incidents` API family — the grouped, stitched security cases XSIAM/XDR builds
> from correlated alerts. Three calls: two **reads** (`get_incidents`, `get_incident_extra_data`)
> the harness uses to pull case state and evidence, and one **write** (`update_incident`) the
> read-only harness never calls. All calls reuse the shared auth in
> [`./01-authentication.md`](./01-authentication.md) and the request/response envelope, filter
> grammar, epoch-ms timestamps, and offset paging in [`./02-conventions.md`](./02-conventions.md).

---

## `get_incidents` — `/public_api/v1/incidents/get_incidents/`

**Purpose:** page/filter the incident list. Primary harness read for "did a case open for the
signal I seeded, and what's its severity/status?"

Filters are `{field, operator, value}` objects, AND-combined (see [conventions](./02-conventions.md)).

| Field | Operator | Value | Notes |
|-------|----------|-------|-------|
| `incident_id_list` | `in` | array of ids | fetch specific incidents |
| `status` | `in` | array of enum | `new`, `under_investigation`, `resolved_threat_handled`, `resolved_known_issue`, `resolved_duplicate`, `resolved_false_positive`, `resolved_auto` (⚠ verify full enum per tenant version) |
| `creation_time` | `gte` / `lte` | epoch ms | window the harness scopes to its run |
| `modification_time` | `gte` / `lte` | epoch ms | catch re-scored / re-assigned cases |

Top-level `request_data` keys (not filters): `search_from` / `search_to` (0-indexed
half-open, ~100/page) and `sort` `{ "field": "creation_time" | "modification_time", "keyword": "asc" | "desc" }`.

### Request

```json
{
  "request_data": {
    "filters": [
      { "field": "creation_time", "operator": "gte", "value": 1721433600000 },
      { "field": "status", "operator": "in", "value": ["new", "under_investigation"] }
    ],
    "search_from": 0,
    "search_to": 100,
    "sort": { "field": "creation_time", "keyword": "desc" }
  }
}
```

### Response (trimmed)

```json
{
  "reply": {
    "total_count": 42,
    "result_count": 2,
    "incidents": [
      {
        "incident_id": "104",
        "incident_name": "Credential dumping on web-01",
        "description": "LSASS access followed by outbound beacon",
        "severity": "high",
        "status": "under_investigation",
        "host_count": 1,
        "alert_count": 6,
        "low_severity_alert_count": 1,
        "med_severity_alert_count": 2,
        "high_severity_alert_count": 3,
        "user_count": 1,
        "creation_time": 1721437200000,
        "modification_time": 1721440800000,
        "detection_time": 1721437000000,
        "assigned_user_mail": "soc@customer.example",
        "assigned_user_pretty_name": "SOC Analyst",
        "resolve_comment": null,
        "manual_severity": null,
        "manual_description": null,
        "xdr_url": "https://customer.xdr.us.paloaltonetworks.com/incident-view/104",
        "hosts": ["web-01:aef3..."],
        "users": ["www-data"],
        "incident_sources": ["XDR Agent"],
        "mitre_tactics_ids_and_names": ["TA0006 - Credential Access"],
        "mitre_techniques_ids_and_names": ["T1003.001 - LSASS Memory"],
        "alerts_grouping_status": "Enabled"
      }
    ]
  }
}
```

> ⚠ verify the exact severity-alert-count field names — some tenant/client versions spell them
> `low_severity_alert_count` / `med_severity_alert_count` / `high_severity_alert_count`, others
> abbreviate. Read defensively (treat unknown keys as additive).

**Harness relevance:** ✅ core read — maps a seeded `Result` row to a real incident for
evidence-backed MTTD; filter by `creation_time` to the run window and correlate MITRE
tactic/technique ids.

---

## `get_incident_extra_data` — `/public_api/v1/incidents/get_incident_extra_data/`

**Purpose:** fetch one incident plus its constituent alerts and artifacts. The drill-down after
`get_incidents` finds a candidate case.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `incident_id` | string | yes | id from a `get_incidents` row |
| `alerts_limit` | int | no | cap alerts returned, default `1000` |

Note: this call takes `incident_id` / `alerts_limit` **directly** inside `request_data` — it is
not a `filters[]` call.

### Request

```json
{
  "request_data": {
    "incident_id": "104",
    "alerts_limit": 50
  }
}
```

### Response (trimmed)

```json
{
  "reply": {
    "incident": {
      "incident_id": "104",
      "incident_name": "Credential dumping on web-01",
      "severity": "high",
      "status": "under_investigation",
      "alert_count": 6,
      "creation_time": 1721437200000
    },
    "alerts": {
      "total_count": 6,
      "data": [
        {
          "alert_id": "9001",
          "name": "LSASS memory access",
          "severity": "high",
          "source": "XDR Agent",
          "detection_timestamp": 1721437000000
        }
      ]
    },
    "network_artifacts": {
      "total_count": 1,
      "data": [
        { "type": "IP", "network_remote_ip": "203.0.113.10", "alert_count": 2 }
      ]
    },
    "file_artifacts": {
      "total_count": 1,
      "data": [
        { "type": "HASH", "file_name": "beacon.elf", "file_sha256": "ab12...", "alert_count": 3 }
      ]
    }
  }
}
```

> ⚠ verify individual alert/artifact object field names against the live tenant — the alert row
> shape here is representative, not exhaustive (see the Alerts family page for the full schema).
> `network_artifacts` / `file_artifacts` sub-object key names (e.g. `network_remote_ip`) ⚠ verify.

**Harness relevance:** ✅ read — supplies the alert-level and artifact evidence that ties a
seeded technique/IOC to the observed incident; feeds the matcher's auto-validation.

---

## `update_incident` — `/public_api/v1/incidents/update_incident/` 🚫 WRITE — OUT OF SCOPE

**Purpose:** mutate an incident's status, assignee, manual severity, or resolve comment.

> **The read-only harness never calls this.** `CLAUDE.md` forbids writing to the tenant — the
> harness generates signal *in* and only *reads* case/alert state back out. Documented here for
> completeness so the surface is fully mapped, not because the harness invokes it.

| Field | Type | Notes |
|-------|------|-------|
| `incident_id` | string | target incident |
| `update_data.status` | enum | same status vocabulary as `get_incidents` |
| `update_data.assigned_user_mail` | string | reassign |
| `update_data.assigned_user_pretty_name` | string | display name for the assignee |
| `update_data.manual_severity` | enum | operator override of computed severity |
| `update_data.resolve_comment` | string | closure note |

### Request

```json
{
  "request_data": {
    "incident_id": "104",
    "update_data": {
      "status": "resolved_false_positive",
      "resolve_comment": "Simulated during CortexSim POV run"
    }
  }
}
```

### Response

```json
{ "reply": true }
```

**Harness relevance:** 🚫 action/write — excluded from the harness by design; the read-only
RBAC role ([`./01-authentication.md`](./01-authentication.md) §4) should not even grant it.

---

## Harness relevance summary

| Call | Path | Class | In harness? |
|------|------|-------|-------------|
| `get_incidents` | `/public_api/v1/incidents/get_incidents/` | ✅ read | Yes — primary case pull |
| `get_incident_extra_data` | `/public_api/v1/incidents/get_incident_extra_data/` | ✅ read | Yes — evidence drill-down |
| `update_incident` | `/public_api/v1/incidents/update_incident/` | 🚫 action (write) | **No — harness never writes** |

Legend: ✅ read (in scope) · ⚠ push (would emit into tenant) · 🚫 action (out of scope — the
harness never writes to the tenant). The incidents family ships no ⚠ push call.

---

## End-to-end example — `get_incidents` (advanced auth)

Compute `${SIG}` / `${NONCE}` / `${TS}` per [`./01-authentication.md`](./01-authentication.md) §2
(`SIG = sha256_hex(api_key + nonce + timestamp)`), then:

```bash
curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/incidents/get_incidents/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" \
  -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{
    "request_data": {
      "filters": [
        { "field": "creation_time", "operator": "gte", "value": 1721433600000 }
      ],
      "search_from": 0,
      "search_to": 100,
      "sort": { "field": "creation_time", "keyword": "desc" }
    }
  }'
```

Page by incrementing `search_from`/`search_to` until `result_count < 100` or
`search_from >= total_count` (see [conventions](./02-conventions.md) §3).

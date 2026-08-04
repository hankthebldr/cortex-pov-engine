# Alerts API (`api_name = alerts`)

> Covers the `alerts` family: the read path that pulls detected alerts **with their raw
> associated events** (the harness's primary detection-reconciliation source), plus the two
> `insert_*` ingestion calls documented for reference only. All calls reuse the shared
> [authentication](./01-authentication.md) headers and the request/response
> [conventions](./02-conventions.md) — envelope, filter grammar, paging, and epoch-ms time
> handling are **not** repeated here.

Base URL pattern: `POST https://api-{fqdn}/public_api/v1/alerts/{call_name}/` with the body
wrapped in `{"request_data": {...}}` and the response in `{"reply": {...}}`.

---

## `get_alerts_multi_events` — `/public_api/v1/alerts/get_alerts_multi_events/`

**Purpose.** The primary "Get Alerts" call. Returns detected alerts, and — unlike the older
`get_alerts` — embeds each alert's associated **raw events** (`alerts[].events[]`) so the
harness gets both the detection verdict and its underlying causality in one round-trip.

### Key request fields

Supplied as filter objects inside `request_data.filters` (`{field, operator, value}`):

| `field` | `operator` | `value` |
|---------|-----------|---------|
| `alert_id_list` | `in` | array of alert-id strings |
| `alert_source` | `in` | array of source strings (e.g. `"XDR Agent"`, `"Analytics"`, `"Correlation"`) |
| `severity` | `in` | subset of `low` / `medium` / `high` / `critical` / `informational` |
| `creation_time` | `gte` / `lte` | epoch ms bounds |

Plus top-level `request_data` keys from [conventions](./02-conventions.md): `search_from`,
`search_to` (~100/page), and `sort` — `{"field": "creation_time" \| "severity", "keyword": "asc" \| "desc"}`.

### Minimal request

```json
{
  "request_data": {
    "filters": [
      { "field": "creation_time", "operator": "gte", "value": 1719792000000 },
      { "field": "severity", "operator": "in", "value": ["high", "critical"] }
    ],
    "search_from": 0,
    "search_to": 100,
    "sort": { "field": "creation_time", "keyword": "desc" }
  }
}
```

### Trimmed response

```json
{
  "reply": {
    "total_count": 42,
    "result_count": 1,
    "alerts": [
      {
        "alert_id": "1042",
        "name": "Behavioral Threat Detected",
        "category": "Malware",
        "severity": "high",
        "source": "XDR Agent",
        "action": "DETECTED",
        "action_pretty": "Detected (Reported)",
        "description": "Suspicious credential access chain",
        "detection_timestamp": 1719795600000,
        "creation_time": 1719795601000,
        "host_name": "web-prod-03",
        "host_ip": "10.0.2.14",
        "endpoint_id": "b7f...e21",
        "user_name": "www-data",
        "mitre_tactic_id_and_name": ["TA0006 - Credential Access"],
        "mitre_technique_id_and_name": ["T1003 - OS Credential Dumping"],
        "events": [
          {
            "event_type": "PROCESS",
            "event_timestamp": 1719795600500,
            "actor_process_image_name": "bash",
            "actor_process_command_line": "cat /etc/shadow",
            "causality_actor_process_image_name": "sshd",
            "action_file_path": "/etc/shadow",
            "action_remote_ip": null,
            "action_remote_port": null,
            "dst_action_url": null
          }
        ]
      }
    ]
  }
}
```

> Event objects are polymorphic by `event_type` (PROCESS / FILE / NETWORK / …). Network and
> firewall alerts populate `action_remote_ip`, `action_remote_port`, `dst_action_url`, and
> `fw_*` fields (e.g. `fw_app_id`, `fw_rule`, `fw_serial_number` — ⚠ verify exact `fw_*`
> field names per source); host alerts populate the process/file fields. Treat unknown event
> fields as additive.

**Harness relevance.** ✅ Primary read path — reconcile seeded `Result` rows against
`alerts[]` on technique / detection-id / name within the run's time window; `events[]` gives
the causality evidence for MTTD scoring.

---

## `insert_parsed_alerts` — `/public_api/v1/alerts/insert_parsed_alerts/`

**Purpose.** Ingest externally-produced alerts as structured JSON. **PUSH path — out of
harness scope**, documented so engineers do not confuse ingestion with the read path.

### Key request fields

`request_data.alerts` is an array (max **~600** alerts/request, rate-limited — ⚠ verify exact
limits); each element:

| Field | Notes |
|-------|-------|
| `product`, `vendor` | source identity strings |
| `local_ip`, `local_port` | source endpoint |
| `remote_ip`, `remote_port` | peer endpoint |
| `event_timestamp` | epoch ms |
| `severity` | `Low` / `Medium` / `High` / `Informational` (⚠ note: different casing than the read call) |
| `alert_name`, `alert_description` | display text |
| `action_status` | e.g. `BLOCKED` / `DETECTED` (⚠ verify allowed values) |

### Minimal request

```json
{
  "request_data": {
    "alerts": [
      {
        "product": "CustomIDS", "vendor": "Acme",
        "local_ip": "10.0.2.14", "local_port": 443,
        "remote_ip": "203.0.113.9", "remote_port": 8443,
        "event_timestamp": 1719795600000,
        "severity": "High",
        "alert_name": "Beacon to known C2",
        "alert_description": "Periodic callback",
        "action_status": "DETECTED"
      }
    ]
  }
}
```

### Trimmed response

```json
{ "reply": true }
```

> `reply` is a success indicator (⚠ verify exact shape — boolean vs. `{"success": true}`).

**Harness relevance.** ⚠ Push / ingestion only — CortexSim generates signal via its
agents/EAL plugins, **not** via this call. Do not implement in the read-only harness.

---

## `insert_cef_alerts` — `/public_api/v1/alerts/insert_cef_alerts/`

**Purpose.** Ingest alerts as raw **CEF-format** strings. **PUSH path — out of scope**,
reference only.

### Key request fields

| Field | Notes |
|-------|-------|
| `alerts` | array of raw CEF-format **strings** (not objects) |

### Minimal request

```json
{
  "request_data": {
    "alerts": [
      "CEF:0|Acme|CustomIDS|1.0|100|Beacon to known C2|7|src=10.0.2.14 dst=203.0.113.9 dpt=8443"
    ]
  }
}
```

### Trimmed response

```json
{ "reply": true }
```

> `reply` is a success indicator (⚠ verify exact shape).

**Harness relevance.** ⚠ Push / ingestion only — same as `insert_parsed_alerts`. Not part of
the read-only harness.

---

## Harness relevance summary

| Call | Direction | Harness | Why |
|------|-----------|:------:|-----|
| `get_alerts_multi_events` | read | ✅ | Primary detection read + raw events for MTTD reconciliation |
| `insert_parsed_alerts` | ingest | 🚫 | PUSH; CortexSim seeds signal via agents/EAL, not this call |
| `insert_cef_alerts` | ingest | 🚫 | PUSH; CEF ingestion path, out of read-only scope |

Legend: ✅ read (in harness) · ⚠ push (reference) · 🚫 action (never call from harness).

---

## End-to-end example — `get_alerts_multi_events` (advanced auth)

Advanced-auth header construction (`${SIG}` / `${NONCE}` / `${TS}`) is defined in
[01-authentication.md §2](./01-authentication.md); compute them first, then:

```bash
# ${NONCE}, ${TS}, ${SIG} computed per ./01-authentication.md §2 (advanced auth)
curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/alerts/get_alerts_multi_events/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" \
  -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{
        "request_data": {
          "filters": [
            { "field": "creation_time", "operator": "gte", "value": 1719792000000 },
            { "field": "alert_source",  "operator": "in",  "value": ["XDR Agent", "Analytics"] }
          ],
          "search_from": 0,
          "search_to": 100,
          "sort": { "field": "creation_time", "keyword": "desc" }
        }
      }'
```

Page by advancing `search_from`/`search_to` until `result_count < 100` or
`search_from >= total_count` — see [conventions §3](./02-conventions.md).

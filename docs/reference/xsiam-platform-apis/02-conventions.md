# Request / Response Conventions

> The single most important page for the harness client. Every family in this vault reuses
> the envelope, filter grammar, and paging described here.

---

## 1. Request envelope

Every call is `POST` with a JSON body wrapped in `request_data`:

```json
{
  "request_data": {
    "filters":     [ /* zero or more filter objects */ ],
    "search_from": 0,
    "search_to":   100,
    "sort":        { "field": "creation_time", "keyword": "desc" }
  }
}
```

- An **empty** `{"request_data": {}}` (or `{"request_data": {"filters": []}}`) means "no
  filter" — return the default/most-recent page.
- `search_from` / `search_to` are a **half-open, zero-indexed** window (offset paging).
  `search_from: 0, search_to: 100` returns the first 100 rows.
- `sort.keyword` is `"asc"` or `"desc"`.
- Some families take extra top-level keys inside `request_data` (e.g. XQL uses `query`,
  `tenants`, `timeframe`; incidents extra-data uses `incident_id`, `alerts_limit`). Those
  are documented per family.

### Filter object grammar

```json
{ "field": "creation_time", "operator": "gte", "value": 1719792000000 }
```

| `operator` | Meaning | `value` type |
|------------|---------|--------------|
| `in` | field ∈ list | array |
| `nin` | field ∉ list | array (⚠ verify per family) |
| `eq` | equals | scalar |
| `neq` | not equals | scalar (⚠ verify per family) |
| `gte` | ≥ | number (epoch ms for times) |
| `lte` | ≤ | number |
| `gt` / `lt` | strict | number |
| `contains` | substring | string |

Filters in a list are **AND**-combined. To OR values within one field, use `in` with an
array. Field names are family-specific (`incident_id_list`, `endpoint_id_list`,
`alert_id_list`, `severity`, `status`, `creation_time`, `modification_time`, …) — see each
family page.

---

## 2. Response envelope

Every response wraps its payload in `reply`:

```json
{
  "reply": {
    "total_count": 3421,
    "result_count": 100,
    "incidents": [ /* ... rows ... */ ]
  }
}
```

- The **inner list key** is family-specific (`incidents`, `alerts`, `endpoints`, `data`, …).
- `total_count` (when present) is the full match count across all pages; `result_count` is
  the size of this page. Use them to drive paging (see §3).
- Read-only harness code should treat unknown fields as additive and never fail on them.

### Error envelope

On error the HTTP status is non-2xx and the body is:

```json
{
  "reply": {
    "err_code": 500,
    "err_msg":  "An error occurred while processing XDR public API",
    "err_extra": "reached max number of retries"
  }
}
```

| HTTP | Meaning | Harness action |
|------|---------|----------------|
| `200` | OK | proceed |
| `400` | Malformed request / bad filter | fix payload; do not retry blindly |
| `401` | Auth failure | re-check signature/clock; do not hammer |
| `402` | Capability not licensed | surface to operator; disable that probe |
| `403` | Role lacks permission | surface; the key is under-scoped |
| `404` | Unknown call / object | check path |
| `429` | Rate limited (⚠ verify — some tenants return `500` w/ retry text) | back off (see §4) |
| `500` | Server error / transient | exponential backoff, capped retries |

---

## 3. Pagination

Two paging models coexist:

- **Offset paging** (incidents, alerts, endpoints, audit): increment
  `search_from`/`search_to` in fixed windows until `result_count < page_size` or
  `search_from >= total_count`. Page sizes of **100** are safe defaults.
- **Result-set streaming** (XQL only): when a query returns **> 1000 rows**,
  `get_query_results` hands back a `stream_id` instead of inline `data`, and you pull the
  full set (gzipped JSONL) via `get_query_results_stream`. See [07](07-xql-api.md).

> **Hard cap to remember:** a single non-stream XQL result page maxes at **1000 rows**.
> Most other families cap a page at **100**. Never assume you got everything from one call —
> always loop on the count.

### Paging helper (pseudocode)

```python
def page_all(call, base_request, list_key, page=100, cap=10_000):
    out, frm = [], 0
    while frm < cap:
        req = {**base_request}
        req["search_from"], req["search_to"] = frm, frm + page
        reply = call({"request_data": req})["reply"]
        rows = reply.get(list_key, [])
        out.extend(rows)
        if len(rows) < page or frm + page >= reply.get("total_count", 0):
            break
        frm += page
    return out
```

---

## 4. Rate limits & backoff

- Limits are enforced **per API / per tenant** and are not uniformly documented; treat them
  as real. XQL additionally has a **compute quota** — check it with
  `xql/get_quota` before large sweeps ([07](07-xql-api.md)).
- The harness must implement **exponential backoff with jitter** on `429`/`500` (e.g. 1s,
  2s, 4s, 8s, capped; ≤ 5 attempts) and a global concurrency cap so a POV run can't DoS a
  customer tenant.
- Prefer **fewer, larger, well-filtered** calls over tight polling loops. For "did detection
  X fire?" reconciliation, poll on an interval (e.g. every 30–60s within a bounded window),
  not continuously.

---

## 5. Time handling

- **All timestamps are epoch milliseconds (UTC).** Both request filters (`gte`/`lte` on
  `creation_time`, `modification_time`, `detection_timestamp`) and response fields use ms.
- XQL `timeframe` accepts either a relative window (`{"relativeTime": 86400000}` = last 24h)
  or an absolute range (`{"from": <ms>, "to": <ms>}`).
- Convert carefully: `int(time.time() * 1000)` in Python; `$(( $(date +%s) * 1000 ))` in
  bash. A seconds-vs-ms bug returns an empty or 1970-anchored window.

---

## 6. Minimal client contract (what the harness client must provide)

```
class XsiamClient:
    def __init__(self, fqdn, api_key, api_key_id, advanced=True, timeout=(10, 60)): ...
    def call(self, api_name, call_name, request_data=None) -> dict   # returns reply["..."] envelope
    # convenience wrappers per family: get_incidents(...), start_xql_query(...), etc.
```

- Builds the URL as `https://api-{fqdn}/public_api/v1/{api_name}/{call_name}/`.
- Injects standard **or** advanced headers ([01](01-authentication.md)).
- Wraps body in `{"request_data": ...}`, unwraps `{"reply": ...}`.
- Raises a typed error carrying `err_code`/`err_msg` on non-2xx.
- Is **transport-injectable** (per `CLAUDE.md`'s measurement-loop rule) so tests never hit
  the network.

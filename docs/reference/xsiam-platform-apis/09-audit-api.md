# Audit APIs (`audits`)

> The **Audit** family (`api_name = audits`) exposes read-only audit trails from the Cortex XSIAM tenant: console/management actions and per-endpoint agent audit history. Both calls are **READ** and fit the CortexSim read-only harness cleanly — they supply change/config-drift evidence around a POV window. This page assumes the shared request/response contract from [`./01-authentication.md`](./01-authentication.md) and [`./02-conventions.md`](./02-conventions.md) and does not re-explain auth, the `{"request_data": {...}}` envelope, the `{"reply": {...}}` response wrapper, epoch-millisecond timestamps, the `{field, operator, value}` filter object, or `search_from`/`search_to` paging. See those pages first.

All calls in this family follow the standard shape:

```
POST https://api-{fqdn}/public_api/v1/audits/{call_name}/
Body: {"request_data": {"filters": [...], "search_from": 0, "search_to": 100, "sort": {...}}}
```

---

## `get_audit_management_logs` — `/public_api/v1/audits/management_logs/`

**Purpose.** Returns the console/management audit trail — logins, configuration changes, API-key mints, policy edits, and other tenant-administration actions. This is the primary "who changed detection config during the POV" evidence source.

### Request fields

| Field | In | Type | Description |
|-------|-----|------|-------------|
| `filters[]` | `request_data` | array | Filter objects `{field, operator, value}`. Supported fields below. |
| `filters[].email` | filter | `in` | Actor email address(es). |
| `filters[].type` | filter | `in` | Audit action type(s). |
| `filters[].sub_type` | filter | `in` | Audit action sub-type(s). |
| `filters[].result` | filter | `in` | One or more of `SUCCESS` / `FAIL` / `PARTIAL`. |
| `filters[].timestamp` | filter | `gte` / `lte` | Epoch-ms bounds on the record time. |
| `search_from` | `request_data` | int | Paging start offset (inclusive). |
| `search_to` | `request_data` | int | Paging end offset (exclusive). |
| `sort` | `request_data` | object | `{"field": "timestamp", "keyword": "asc"\|"desc"}`. |

### Minimal request

```json
{
  "request_data": {
    "filters": [
      {"field": "result", "operator": "in", "value": ["FAIL", "PARTIAL"]},
      {"field": "timestamp", "operator": "gte", "value": 1718000000000}
    ],
    "search_from": 0,
    "search_to": 100,
    "sort": {"field": "timestamp", "keyword": "desc"}
  }
}
```

### Trimmed response

```json
{
  "reply": {
    "total_count": 42,
    "result_count": 2,
    "data": [
      {
        "AUDIT_ID": 90231,
        "AUDIT_OWNER_NAME": "Jane Consultant",
        "AUDIT_OWNER_EMAIL": "jane@example.com",
        "AUDIT_ASSET_JSON": "{\"policy_id\":\"pol-77\"}",
        "AUDIT_ASSET_NAMES": "Prevention Policy - Linux",
        "AUDIT_HOSTNAME": "console",
        "AUDIT_RESULT": "SUCCESS",
        "AUDIT_REASON": "",
        "AUDIT_DESCRIPTION": "Edited prevention policy",
        "AUDIT_ENTITY": "POLICY",
        "AUDIT_ENTITY_SUBTYPE": "PREVENTION",
        "AUDIT_SESSION_ID": "sess-abc123",
        "AUDIT_CASE_ID": null,
        "AUDIT_INSERT_TIME": 1718000123456,
        "AUDIT_SEVERITY": "SEV_030_MEDIUM"
      }
    ]
  }
}
```

**Harness relevance.** ✅ READ — proves *who* changed detection/policy configuration (and when) across the POV window; core config-drift evidence.

---

## `get_audit_agent_reports` — `/public_api/v1/audits/agents_reports/`

**Purpose.** Returns the per-endpoint agent audit trail — agent audit/status/policy events per host. Proves that the Cortex agent is healthy and that policy was actually applied on the target endpoints during a POV.

### Request fields

| Field | In | Type | Description |
|-------|-----|------|-------------|
| `filters[]` | `request_data` | array | Filter objects `{field, operator, value}`. Supported fields below. |
| `filters[].endpoint_id` | filter | `in` | Endpoint ID(s). |
| `filters[].endpoint_name` | filter | `in` | Endpoint hostname(s). |
| `filters[].type` | filter | `in` | One or more of `Audit` / `Status` / `Policy`. |
| `filters[].sub_type` | filter | `in` | Event sub-type(s). |
| `filters[].result` | filter | `in` | Result value(s). ⚠ verify accepted set. |
| `filters[].timestamp` | filter | `gte` / `lte` | Epoch-ms bounds on the record time. |
| `search_from` | `request_data` | int | Paging start offset (inclusive). |
| `search_to` | `request_data` | int | Paging end offset (exclusive). |
| `sort` | `request_data` | object | Sort spec. ⚠ verify sortable fields. |

### Minimal request

```json
{
  "request_data": {
    "filters": [
      {"field": "type", "operator": "in", "value": ["Policy"]},
      {"field": "endpoint_name", "operator": "in", "value": ["web-01"]}
    ],
    "search_from": 0,
    "search_to": 100
  }
}
```

### Trimmed response

```json
{
  "reply": {
    "total_count": 17,
    "data": [
      {
        "RECEIVEDTIME": 1718000200000,
        "ENDPOINTID": "e1f2a3b4",
        "ENDPOINTNAME": "web-01",
        "DOMAIN": "corp.example.com",
        "XDRVERSION": "8.4.0",
        "TYPE": "Policy",
        "SUBTYPE": "Policy Update",
        "RESULT": "SUCCESS",
        "REASON": "",
        "DESCRIPTION": "Applied prevention policy revision 12",
        "CATEGORY": "Policy",
        "ENDPOINTTYPE": "Server"
      }
    ]
  }
}
```

**Harness relevance.** ✅ READ — proves agent health, status, and policy application on the target endpoints; complements management-logs for end-to-end POV assurance.

---

## Harness relevance summary

| Call | Method | Harness | Why |
|------|--------|---------|-----|
| `get_audit_management_logs` | READ | ✅ in scope | Who changed detection/policy config during the POV. |
| `get_audit_agent_reports`   | READ | ✅ in scope | Agent health/status/policy application on target endpoints. |

---

## End-to-end example

Advanced-auth headers (`x-xdr-auth-id`, `Authorization` nonce+hash, `x-xdr-timestamp`) are constructed per [`./01-authentication.md`](./01-authentication.md); placeholders below stand in for those values.

```bash
curl -sS -X POST "https://api-${FQDN}/public_api/v1/audits/management_logs/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "Authorization: ${ADVANCED_AUTH_HASH}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TIMESTAMP_MS}" \
  -H "Content-Type: application/json" \
  -d '{
        "request_data": {
          "filters": [
            {"field": "result", "operator": "in", "value": ["FAIL", "PARTIAL"]},
            {"field": "timestamp", "operator": "gte", "value": 1718000000000}
          ],
          "search_from": 0,
          "search_to": 100,
          "sort": {"field": "timestamp", "keyword": "desc"}
        }
      }'
```

---

## Verification notes

- ⚠ verify exact uppercase `AUDIT_*` field spellings (e.g. `AUDIT_ENTITY_SUBTYPE`, `AUDIT_INSERT_TIME`, `AUDIT_SEVERITY`) across tenant versions — casing/underscore layout drifts between releases.
- ⚠ verify `AUDIT_SEVERITY` value vocabulary (the `SEV_0xx_*` form shown is illustrative).
- ⚠ verify exact `agents_reports` field spellings (`RECEIVEDTIME`, `ENDPOINTID`, `ENDPOINTNAME`, `XDRVERSION`, `ENDPOINTTYPE`, etc.) — concatenated/uppercase forms vary by version.
- ⚠ verify the accepted `result` value set for `get_audit_agent_reports` (management-logs uses `SUCCESS`/`FAIL`/`PARTIAL`; agent-reports may differ).
- ⚠ verify sortable fields / `sort` support for `get_audit_agent_reports`.

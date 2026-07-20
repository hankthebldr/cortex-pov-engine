# Threat Intelligence / IOCs API

> Covers the indicator/IOC-management family (`api_name` is **`indicators`** — ⚠ verify: some
> tenants/clients expose it as **`iocs`**). This family is **chiefly for UPLOADING IOCs (PUSH)** —
> seeding a tenant's threat-intel store with hashes, IPs, domains, and filenames plus their
> reputation/reliability metadata. **It is largely OUT OF SCOPE for the read-only CortexSim POV
> harness**, which generates signal *into* the environment and does not write to the tenant
> (`CLAUDE.md`). A **"list IOCs" read may exist** to enumerate previously uploaded indicators, but
> it is **version-dependent and unverified** — if IOC readback is ever needed, confirm the exact
> call against the live tenant portal rather than trusting this page. This whole family is
> **uncertain**: exact call names, paths, `api_name`, and nested field names are flagged **⚠ verify**
> throughout. All calls reuse the shared auth in [`./01-authentication.md`](./01-authentication.md)
> and the request/response envelope + epoch-ms convention in
> [`./02-conventions.md`](./02-conventions.md) —
> `POST https://api-{fqdn}/public_api/v1/{api_name}/{call_name}/`, body `{"request_data": {...}}`,
> response `{"reply": {...}}`.

---

## 1. `insert_jsons` — `/public_api/v1/indicators/insert_jsons/` ⚠ PUSH

**Purpose:** upload one or more IOC objects as structured JSON, each carrying its value, type,
severity, expiration, and reputation/reliability metadata. This is the primary programmatic
indicator-upload call.

> ⚠ verify `api_name` (`indicators` vs `iocs`) and the nested field names below — this is the
> least-certain part of the page.

| Field | Type | Notes |
|-------|------|-------|
| `request_data` | array | array of IOC objects (⚠ verify: `request_data[]` array vs a wrapping key) |
| `request_data[].indicator` | string | the IOC value (the hash / IP / domain / filename) |
| `request_data[].type` | enum | `HASH` \| `IP` \| `DOMAIN_NAME` \| `FILENAME` — ⚠ verify enum + exact spellings |
| `request_data[].severity` | enum | `INFO` \| `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` |
| `request_data[].expiration_date` | epoch ms \| string | epoch-ms timestamp, or the literal `"Never"` — ⚠ verify |
| `request_data[].comment` | string | free-text note |
| `request_data[].reputation` | enum | `GOOD` \| `BAD` \| `SUSPICIOUS` \| `UNKNOWN` — ⚠ verify enum |
| `request_data[].reliability` | enum | `A`–`F` reliability grade — ⚠ verify enum |
| `request_data[].class` | string | indicator class/category — ⚠ verify |
| `request_data[].vendors` | array | per-vendor scoring: `{ vendor_name, reputation, reliability }` — ⚠ verify nested names |

#### Request (minimal)

```json
{
  "request_data": [
    {
      "indicator": "8f14e45fceea167a5a36dedd4bea2543",
      "type": "HASH",
      "severity": "HIGH",
      "expiration_date": "Never",
      "reputation": "BAD",
      "reliability": "B",
      "comment": "CortexSim POV seed",
      "vendors": [
        { "vendor_name": "CortexSim", "reputation": "BAD", "reliability": "B" }
      ]
    }
  ]
}
```

#### Response (trimmed)

```json
{ "reply": { "success": true } }
```

> ⚠ verify `reply` shape — reported to return a success flag plus insert/update/error **counts**;
> exact field names unconfirmed.

**Harness relevance:** ⚠ push — writes IOCs into the tenant; the read-only harness does not call it.

---

## 2. `insert_csv` — `/public_api/v1/indicators/insert_csv/` ⚠ PUSH

**Purpose:** bulk-upload IOCs as CSV, one indicator per row, with the same logical fields as
`insert_jsons` expressed as columns (indicator value, type, severity, expiration, reputation,
reliability, class, comment). For large indicator batches.

> ⚠ verify path and the exact column set / header names — CSV schema is client/version dependent.

| Field | Type | Notes |
|-------|------|-------|
| `request_data` | string \| object | CSV payload (⚠ verify: inline CSV string vs a `{ csv: ... }` wrapper) |

#### Request (minimal, illustrative)

```json
{
  "request_data": "indicator,type,severity,reputation,reliability,expiration_date\n8f14e45fceea167a5a36dedd4bea2543,HASH,HIGH,BAD,B,Never"
}
```

⚠ verify — the transport for the CSV body (inline string above vs multipart file upload) is
**unconfirmed**; treat this request example as illustrative only.

#### Response (trimmed)

```json
{ "reply": { "success": true } }
```

**Harness relevance:** ⚠ push — bulk IOC upload; the read-only harness does not call it.

---

## 3. Read / list indicators — ⚠ REPORTED BUT UNVERIFIED

A read call to **enumerate previously uploaded IOCs** (e.g. `get_indicators` / a `list` variant)
is **reported but not verified**. The exact **call name, path, request filters, and response
schema are unknown** and must be **confirmed against the tenant's portal** before use.

> This page deliberately **does not present a fabricated response schema.** If the harness ever
> needs IOC readback, verify the call name, path, and `reply` shape live against the target tenant
> — do not assume it mirrors `insert_jsons`.

**Harness relevance:** ✅ read *if it exists* — the only member of this family that could fall
within read-only scope, but only after live verification.

---

## 4. Manage indicators (enable / disable / delete) — ⚠ CONCEPTUAL, UNVERIFIED

Management **ACTION** calls to enable, disable, or delete indicators exist **conceptually**, but
their **exact call names, paths, and request fields must be verified** against the tenant portal.
They are **WRITE/ACTION** surface and out of scope regardless.

**Harness relevance:** 🚫 action — mutate the tenant's IOC store; never invoked by the read-only harness.

---

## Harness relevance summary

| Call | Path (⚠ verify) | Direction | Harness |
|------|-----------------|-----------|---------|
| `insert_jsons` | `/public_api/v1/indicators/insert_jsons/` | push (upload) | ⚠ push — not used |
| `insert_csv` | `/public_api/v1/indicators/insert_csv/` | push (bulk upload) | ⚠ push — not used |
| list / `get_indicators` | ⚠ unverified | read | ✅ read *if verified* — confirm live |
| enable / disable / delete | ⚠ unverified | action | 🚫 action — not used |

**Bottom line for the harness engineer:** this family is **push-oriented and out of scope**. The
harness seeds signal into the environment; it does not upload IOCs and does not write to the
tenant. The *only* potentially in-scope member is an IOC **read/list**, whose existence and shape
are **unverified** — verify against the live tenant portal before relying on it.

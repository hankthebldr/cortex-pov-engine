# Distributions, Device Control & Assets APIs

> Three loosely related inventory-and-installer families. **Distributions** (`api_name =
> distributions`) builds XDR agent installer packages — **low harness relevance** (CortexSim
> ships its own Go beacon), documented for completeness only. **Device Control**
> (`api_name = device_control`) exposes USB / removable-media policy violations as a clean
> read. **Assets / Host Insights** has no dependable first-party REST inventory endpoint on
> the public API — pull asset data via **XQL over asset datasets** instead. All three reuse
> the vault envelope and filter grammar — see [./01-authentication.md](./01-authentication.md)
> for standard/advanced headers and [./02-conventions.md](./02-conventions.md) for the
> `POST https://api-{fqdn}/public_api/v1/{api_name}/{call_name}/` shape, the
> `{"request_data": {...}}` request / `{"reply": {...}}` response wrapping, epoch-ms
> timestamps, and `{field, operator, value}` filters.

---

## A. Distributions (`api_name = distributions`)

Builds and tracks XDR agent installer packages. **Low harness relevance** — CortexSim
enrolls its own beacon and does not install XDR agents. The two **action** calls (`create`,
`get_dist_url`) are OUT OF HARNESS SCOPE 🚫; the two **read** calls are listed so the harness
can recognize them but the whole family is effectively out of scope.

### `get_versions` — `/public_api/v1/distributions/get_versions/`

**Purpose.** List agent versions available to build an installer against.

| Request field | Req? | Notes |
|---------------|------|-------|
| _(none)_ | — | Send `{"request_data": {}}`; takes no filters. |

```json
{ "request_data": {} }
```

```json
{ "reply": { "versions": ["8.3.0.12345", "8.2.1.98765", "8.1.0.54321"] } }
```

- ⚠ verify response key: the reply is a bare **list** on some versions and
  `reply.versions[]` on others — harness code should accept either shape.

**Harness relevance.** ✅ read — trivial; not used by the harness (no agent install path).

---

### `create` — `/public_api/v1/distributions/create/` 🚫

**Purpose.** Build a new installer package. **ACTION** — creates a durable tenant artifact.

| Request field | Req? | Notes |
|---------------|------|-------|
| `name` | ✅ | Distribution display name. |
| `platform` | ✅ | `windows` / `linux` / `macos` / `android` (⚠ verify enum values). |
| `package_type` | ✅ | `standalone` / `upgrade` / … (⚠ verify enum values). |
| `agent_version` | ✅ | One of `get_versions`. |

```json
{
  "request_data": {
    "name": "pov-linux-standalone",
    "platform": "linux",
    "package_type": "standalone",
    "agent_version": "8.3.0.12345"
  }
}
```

```json
{ "reply": { "distribution_id": "a1b2c3d4-..." } }
```

**Harness relevance.** 🚫 action — creates an installer; OUT OF HARNESS SCOPE.

---

### `get_status` — `/public_api/v1/distributions/get_status/`

**Purpose.** Poll build status of a distribution by id.

| Request field | Req? | Notes |
|---------------|------|-------|
| `distribution_id` | ✅ | Id returned by `create`. |

```json
{ "request_data": { "distribution_id": "a1b2c3d4-..." } }
```

```json
{ "reply": { "status": "Completed", "name": "pov-linux-standalone" } }
```

- `reply.status` ∈ `Pending | Completed` (⚠ verify additional/error states).

**Harness relevance.** ✅ read — status poll; not used by the harness.

---

### `get_dist_url` — `/public_api/v1/distributions/get_dist_url/` 🚫

**Purpose.** Return a temporary signed download URL for a completed distribution.

| Request field | Req? | Notes |
|---------------|------|-------|
| `distribution_id` | ✅ | Id of a `Completed` distribution. |
| `package_type` | ✅ | Package variant to fetch (⚠ verify enum values). |

```json
{
  "request_data": {
    "distribution_id": "a1b2c3d4-...",
    "package_type": "sh"
  }
}
```

```json
{ "reply": { "distribution_url": "https://distributions.xdr.../pkg?sig=..." } }
```

- Though HTTP-shaped as a read, `get_dist_url` yields a distributable installer artifact —
  treat as **out of scope** alongside `create`.

**Harness relevance.** 🚫 action — hands out an installer download; OUT OF HARNESS SCOPE.

---

## B. Device Control (`api_name = device_control`)

USB / removable-media policy enforcement telemetry. One clean **read**.

### `get_violations` — `/public_api/v1/device_control/get_violations/`

**Purpose.** Return device-control (removable-media) policy violations, filterable by time,
device type/vendor/product/serial, endpoint, and violation id.

| Filter `field` | Operator | Value |
|----------------|----------|-------|
| `timestamp` | `gte` / `lte` | epoch ms |
| `type` | `in` | array of device types (⚠ verify enum) |
| `vendor` | `in` | array of vendor names |
| `product` | `in` | array of product names |
| `serial` | `in` | array of device serials |
| `endpoint_id_list` | `in` | array of endpoint ids |
| `violation_id_list` | `in` | array of violation ids |

```json
{
  "request_data": {
    "filters": [
      { "field": "timestamp", "operator": "gte", "value": 1719705600000 },
      { "field": "type", "operator": "in", "value": ["disk drive"] }
    ],
    "search_from": 0,
    "search_to": 100
  }
}
```

```json
{
  "reply": {
    "total_count": 3,
    "violations": [
      {
        "violation_id": 42,
        "endpoint_id": "1a2b3c...",
        "endpoint_name": "web-prod-01",
        "ip": "10.0.1.20",
        "timestamp": 1719792000000,
        "type": "disk drive",
        "vendor": "SanDisk",
        "product": "Cruzer Blade",
        "serial": "4C530001...",
        "hostname": "web-prod-01",
        "username": "www-data",
        "initiator_name": "explorer.exe",
        "initiator_cmd": "C:\\Windows\\explorer.exe",
        "os_type": "AGENT_OS_WINDOWS"
      }
    ]
  }
}
```

- Response follows the standard envelope: `reply.total_count` + `reply.violations[]`.
- ⚠ verify field spellings across versions (`initiator_name` / `initiator_cmd` / `os_type`).

**Harness relevance.** ✅ read — filterable removable-media violation query; safe inventory read.

---

## C. Assets / Host Insights

⚠ verify — **there is no dependable first-party asset-inventory REST endpoint on the public
API**; availability is limited and version-dependent. Do **not** assume a `get_asset` REST
call or fabricate its schema. In XSIAM the reliable way to pull asset / host-insight data is
**XQL over the asset datasets** (⚠ verify dataset name, e.g. `asset_*` / `host_inventory`) —
see [./07-xql-api.md](./07-xql-api.md).

- If a `get_asset`-style REST call turns out to exist on a given tenant, mark it **⚠ verify**
  and prefer XQL regardless — the XQL path is stable, filterable, and already in the harness.
- **Harness guidance:** the harness should treat asset/host-insight lookups as an **XQL query**
  (family [07](07-xql-api.md)), not a `device_control`/`distributions`-style REST call.

**Harness relevance.** ✅ read — but via XQL, not a REST asset endpoint.

---

## Harness relevance summary

| Call | Path (`…/{api_name}/…/`) | Kind | Harness |
|------|--------------------------|------|---------|
| `get_versions` | `distributions/get_versions/` | Read — available agent versions | ✅ read (unused) |
| `create` | `distributions/create/` | Action — build installer | 🚫 action |
| `get_status` | `distributions/get_status/` | Read — build status | ✅ read (unused) |
| `get_dist_url` | `distributions/get_dist_url/` | Action — signed installer URL | 🚫 action |
| `get_violations` | `device_control/get_violations/` | Read — removable-media violations | ✅ read |
| _asset inventory_ | via XQL ([07](07-xql-api.md)) | Read — no dependable REST endpoint | ✅ read (XQL) |

---

## End-to-end example — `get_violations` (advanced auth)

Placeholders `${SIG}`, `${NONCE}`, `${TS}` are the advanced-auth signature, nonce, and
epoch-ms timestamp — construct them per [./01-authentication.md](./01-authentication.md) §2
(`SIG = sha256_hex(API_KEY + NONCE + TS)`).

```bash
curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/device_control/get_violations/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" \
  -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{
    "request_data": {
      "filters": [
        { "field": "timestamp", "operator": "gte", "value": 1719705600000 },
        { "field": "type", "operator": "in", "value": ["disk drive"] }
      ],
      "search_from": 0,
      "search_to": 100
    }
  }'
```

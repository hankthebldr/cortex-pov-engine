# Response Actions API

> **Paths confirmed (2026-07):** the authoritative operation catalog
> ([`14-operation-catalog.md`](14-operation-catalog.md)) resolves several markers that were
> `⚠ verify` on this page: **Get Quarantine Status** is `POST /public_api/v1/quarantine/status`
> (not `endpoints/get_quarantine_status`); **File Retrieval Details** is
> `POST /public_api/v1/actions/file_retrieval_details`; **allow/block lists** are
> `POST /public_api/v1/hash_exceptions/allowlist` and `.../hash_exceptions/blocklist` (the
> `hash_exceptions` `api_name` is confirmed). Prefer the catalog paths where they differ below.
>
> Covers the response/remediation families — **file retrieval** (`endpoints`), **quarantine**
> (`endpoints`), **block/allow lists** (`hash_exceptions`, ⚠ verify api_name), and **action
> management** (`actions`). This is overwhelmingly a **WRITE / ACTION** surface: it isolates
> hosts' files, quarantines and restores binaries, mutates tenant block/allow lists, and pulls
> files off endpoints. **The read-only CortexSim POV harness calls almost none of it.** It does
> **not** quarantine, restore, blocklist, allowlist, or retrieve files — `CLAUDE.md` forbids
> writing to the tenant. This page exists for completeness and to document the handful of
> **read-only status calls** (`get_action_status`, `get_quarantine_status`, `get_blocklist` /
> `get_allowlist`, `file_retrieval_details`) the harness *could* poll — and normally does not.
> All calls reuse the shared auth in [`./01-authentication.md`](./01-authentication.md) and the
> request/response envelope, filter grammar (`{field, operator, value}`), and epoch-ms timestamps
> in [`./02-conventions.md`](./02-conventions.md).

---

## A. File retrieval (`endpoints`)

### `file_retrieval` — `/public_api/v1/endpoints/file_retrieval/` 🚫 ACTION — OUT OF SCOPE

**Purpose:** pull one or more files off matched endpoints for analysis. Kicks off an async
action; the retrieved files are collected via `file_retrieval_details`.

> **The read-only harness never calls this** — reading files off customer endpoints is a
> data-exfil-shaped action, forbidden by design.

| Field | Type | Notes |
|-------|------|-------|
| `filters` | array | `endpoint_id_list` filter selecting target endpoints (`{field, operator, value}`) |
| `files` | object | map keyed by platform — `windows` / `linux` / `macos` → list of absolute paths |

⚠ verify body shape — the `files`-keyed-by-platform structure vs a flat `files[]` list varies by
tenant/client version.

#### Request

```json
{
  "request_data": {
    "filters": [
      { "field": "endpoint_id_list", "operator": "in", "value": ["aef3...", "bd21..."] }
    ],
    "files": {
      "windows": ["C:\\Windows\\Temp\\beacon.exe"],
      "linux": ["/tmp/beacon.elf"],
      "macos": []
    }
  }
}
```

#### Response (trimmed)

```json
{ "reply": { "action_id": 773 } }
```

**Harness relevance:** 🚫 action — retrieves files off endpoints; never invoked.

---

### `file_retrieval_details` — `/public_api/v1/endpoints/file_retrieval_details/` ✅ READ (poll)

**Purpose:** poll a `file_retrieval` action for per-endpoint download links to the collected
files. This is the read half of the retrieval flow.

⚠ verify path/name — this call's exact spelling and path are uncertain.

| Field | Type | Notes |
|-------|------|-------|
| `group_action_id` | int/string | the `action_id` returned by `file_retrieval` |

Takes `group_action_id` **directly** inside `request_data` (not a `filters[]` call).

#### Request

```json
{ "request_data": { "group_action_id": 773 } }
```

#### Response (trimmed)

```json
{
  "reply": {
    "data": [
      {
        "endpoint_id": "aef3...",
        "status": "COMPLETED_SUCCESSFULLY",
        "retrieved_file_link": "https://.../download/773/aef3.zip"
      }
    ]
  }
}
```

⚠ verify response shape — per-endpoint object key names (`retrieved_file_link`, `status`) are
representative, not confirmed.

**Harness relevance:** ✅ read (poll) — technically a read, but only meaningful after a
`file_retrieval` action the harness never issues; effectively unused.

---

## B. Quarantine (`endpoints`)

### `quarantine` — `/public_api/v1/endpoints/quarantine/` 🚫 ACTION — OUT OF SCOPE

**Purpose:** quarantine a specific file (by path + hash) on matched endpoints.

> **The read-only harness never calls this.** Quarantine mutates endpoint state — forbidden.

| Field | Type | Notes |
|-------|------|-------|
| `filters` | array | `endpoint_id_list` filter selecting targets |
| `file_path` | string | absolute path of the file to quarantine |
| `file_hash` | string | SHA256 of the file (guards against path reuse) |

#### Request

```json
{
  "request_data": {
    "filters": [
      { "field": "endpoint_id_list", "operator": "in", "value": ["aef3..."] }
    ],
    "file_path": "/tmp/beacon.elf",
    "file_hash": "ab12..."
  }
}
```

#### Response (trimmed)

```json
{ "reply": { "action_id": 774 } }
```

**Harness relevance:** 🚫 action — never invoked.

---

### `restore` — `/public_api/v1/endpoints/restore/` 🚫 ACTION — OUT OF SCOPE

**Purpose:** restore a previously quarantined file (by hash) — optionally scoped to one endpoint.

> **The read-only harness never calls this.**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file_hash` | string | yes | SHA256 of the quarantined file |
| `endpoint_id` | string | no | limit restore to a single endpoint; omit for all |

#### Request

```json
{ "request_data": { "file_hash": "ab12...", "endpoint_id": "aef3..." } }
```

#### Response (trimmed)

```json
{ "reply": { "action_id": 775 } }
```

**Harness relevance:** 🚫 action — never invoked.

---

### `get_quarantine_status` — `/public_api/v1/endpoints/get_quarantine_status/` ✅ READ

**Purpose:** check whether specific (endpoint, path, hash) triples are currently quarantined.

| Field | Type | Notes |
|-------|------|-------|
| `files` | array | each `{ endpoint_id, file_path, file_hash }` |

#### Request

```json
{
  "request_data": {
    "files": [
      { "endpoint_id": "aef3...", "file_path": "/tmp/beacon.elf", "file_hash": "ab12..." }
    ]
  }
}
```

#### Response (trimmed)

```json
{
  "reply": [
    {
      "endpoint_id": "aef3...",
      "file_path": "/tmp/beacon.elf",
      "file_hash": "ab12...",
      "status": true
    }
  ]
}
```

⚠ verify response shape — whether the quarantined flag is `status` / `quarantined` and whether
`reply` is an array vs an object is uncertain.

**Harness relevance:** ✅ read — a status check; the harness would only call it if explicitly
asked and if the key's role granted it. Normally unused.

---

## C. Block / allow lists (`hash_exceptions` ⚠ verify api_name)

> ⚠ verify the `api_name` segment for this whole family — it may be `hash_exceptions`, or these
> may live under `endpoints`. Paths below are best-effort.

### `blocklist` — `/public_api/v1/hash_exceptions/blocklist/` 🚫 ACTION — OUT OF SCOPE

**Purpose:** add SHA256 hashes to the tenant block list. ⚠ verify path.

> **The read-only harness never calls this** — it mutates tenant-wide policy.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `hash_list` | array | yes | SHA256 hashes to block |
| `comment` | string | no | audit note |
| `incident_id` | string | no | associate the entry with an incident |

#### Request

```json
{
  "request_data": {
    "hash_list": ["ab12...", "cd34..."],
    "comment": "CortexSim POV — sample only",
    "incident_id": "104"
  }
}
```

#### Response (trimmed)

```json
{ "reply": { "added": 2 } }
```

**Harness relevance:** 🚫 action — never invoked.

---

### `allowlist` — `/public_api/v1/hash_exceptions/allowlist/` 🚫 ACTION — OUT OF SCOPE

**Purpose:** add SHA256 hashes to the tenant allow list. Same request/response shape as
`blocklist`. ⚠ verify path.

> **The read-only harness never calls this.**

#### Request

```json
{ "request_data": { "hash_list": ["ef56..."], "comment": "CortexSim POV" } }
```

#### Response (trimmed)

```json
{ "reply": { "added": 1 } }
```

**Harness relevance:** 🚫 action — never invoked.

---

### `get_blocklist` — `/public_api/v1/hash_exceptions/blocklist/` ✅ READ

**Purpose:** list the SHA256 hashes on the block list, optionally filtered.

⚠ verify — this may be the **same** path as `blocklist` (differentiated by GET-vs-POST or by an
empty/omitted `hash_list`), or a **distinct** `get_blocklist` call. Uncertain.

| Field | Type | Notes |
|-------|------|-------|
| `filters` | array | optional `{field, operator, value}` filters |

#### Request

```json
{ "request_data": { "filters": [] } }
```

#### Response (trimmed)

```json
{ "reply": [ { "hash": "ab12...", "comment": "…", "added_by": "…" } ] }
```

**Harness relevance:** ✅ read — a status/inventory read; only used if explicitly asked and
RBAC-permitted. Normally unused.

---

### `get_allowlist` — ✅ READ

**Purpose:** list the allow-list hashes — analogous to `get_blocklist`. ⚠ verify path/name (likely
`/public_api/v1/hash_exceptions/allowlist/` or a distinct `get_allowlist`).

**Harness relevance:** ✅ read — same treatment as `get_blocklist`; normally unused.

---

## D. Action management (`actions`)

### `get_action_status` — `/public_api/v1/actions/get_action_status/` ✅ READ

**Purpose:** get the per-endpoint completion status of any action (file retrieval, quarantine,
isolate, script run, …) by its group action id. **This is the one call the harness might
legitimately use** — to observe the outcome of an action it was *told about*, not one it issued.

| Field | Type | Notes |
|-------|------|-------|
| `group_action_id` | int/string | the `action_id` returned by the originating action call |

Takes `group_action_id` **directly** inside `request_data` (not a `filters[]` call).

#### Request

```json
{ "request_data": { "group_action_id": 773 } }
```

#### Response (trimmed)

```json
{
  "reply": {
    "data": {
      "aef3...": "COMPLETED_SUCCESSFULLY",
      "bd21...": "PENDING"
    }
  }
}
```

`reply.data` maps `endpoint_id → status` string: `PENDING`, `IN_PROGRESS`,
`COMPLETED_SUCCESSFULLY`, `FAILED`, `CANCELED`, `EXPIRED`, `TIMEOUT` (⚠ verify full enum per
tenant version).

**Harness relevance:** ✅ read — the sole action-management read the harness may use, to observe
a known action's terminal state; it never *starts* the action.

---

### `cancel_action` / `retry_action` — `/public_api/v1/actions/…/` 🚫 ACTION — OUT OF SCOPE

**Purpose:** cancel a pending action / retry a failed one. ⚠ verify exact call names and paths.

> **The read-only harness never calls these.** Documented only so the `actions` family is fully
> mapped.

**Harness relevance:** 🚫 action — never invoked.

---

## Harness relevance summary

| Call | Path (⚠ where noted) | Class | In harness? |
|------|----------------------|-------|-------------|
| `file_retrieval` | `/public_api/v1/endpoints/file_retrieval/` | 🚫 action | No |
| `file_retrieval_details` | `/public_api/v1/endpoints/file_retrieval_details/` ⚠ | ✅ read (poll) | No (poll for an action it never issues) |
| `quarantine` | `/public_api/v1/endpoints/quarantine/` | 🚫 action | No |
| `restore` | `/public_api/v1/endpoints/restore/` | 🚫 action | No |
| `get_quarantine_status` | `/public_api/v1/endpoints/get_quarantine_status/` | ✅ read | Only if asked + RBAC-permitted |
| `blocklist` | `/public_api/v1/hash_exceptions/blocklist/` ⚠ | 🚫 action | No |
| `allowlist` | `/public_api/v1/hash_exceptions/allowlist/` ⚠ | 🚫 action | No |
| `get_blocklist` | `/public_api/v1/hash_exceptions/blocklist/` ⚠ | ✅ read | Only if asked + RBAC-permitted |
| `get_allowlist` | ⚠ verify | ✅ read | Only if asked + RBAC-permitted |
| `get_action_status` | `/public_api/v1/actions/get_action_status/` | ✅ read | Maybe — observe a known action |
| `cancel_action` / `retry_action` | `/public_api/v1/actions/…/` ⚠ | 🚫 action | No |

Legend: ✅ read (in scope, though most reads here are still normally unused) · 🚫 action
(out of scope — the harness never isolates, quarantines, blocklists, or retrieves files).
**Emphasis:** the read-only POV harness does **not** perform any response action. The reads above
are documented for completeness; the harness would only issue one if explicitly asked *and* the
key's RBAC role ([`./01-authentication.md`](./01-authentication.md) §4) granted it — normally it
does not.

---

## End-to-end example — `get_action_status` (advanced auth, a READ)

Compute `${SIG}` / `${NONCE}` / `${TS}` per [`./01-authentication.md`](./01-authentication.md) §2
(`SIG = sha256_hex(api_key + nonce + timestamp)`), then:

```bash
curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/actions/get_action_status/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" \
  -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{
    "request_data": { "group_action_id": 773 }
  }'
```

Poll until every `endpoint_id` in `reply.data` reaches a terminal status
(`COMPLETED_SUCCESSFULLY` / `FAILED` / `CANCELED` / `EXPIRED` / `TIMEOUT`).

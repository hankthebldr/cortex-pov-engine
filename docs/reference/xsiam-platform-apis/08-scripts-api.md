# Scripts API (`api_name = scripts`)

> The `scripts` family manages the Cortex XDR **script library** and remote script
> execution on agent-installed endpoints. This page follows the vault envelope and
> filter grammar — see [./01-authentication.md](./01-authentication.md) for the
> standard/advanced header construction and [./02-conventions.md](./02-conventions.md)
> for the `POST https://api-{fqdn}/public_api/v1/scripts/{call_name}/` shape, the
> `{"request_data": {...}}` request / `{"reply": {...}}` response wrapping, epoch-ms
> timestamps, and `{field, operator, value}` filters. **The read-only harness does NOT
> run scripts on customer endpoints.** The two `run_*` calls (`run_script`,
> `run_snippet_code_script`) are documented for completeness so the harness can
> recognize and refuse an action path; they are never invoked. The five **read** calls
> (library listing, metadata, and execution status/results/result-files) could
> theoretically observe the results of an execution someone else triggered — they are
> read-only and safe.

---

## `get_scripts` — `/public_api/v1/scripts/get_scripts/`

**Purpose.** List scripts in the tenant's script library. Filterable by name,
description, author, risk, uid, and per-OS support.

| Filter `field` | Operator | Value |
|----------------|----------|-------|
| `name` | `in` / `contains` | script name(s) / substring |
| `description` | `contains` | substring |
| `created_by` | `in` | author email(s) |
| `is_high_risk` | `eq` | bool |
| `script_uid` | `in` | array of script uids |
| `windows_supported` | `eq` | bool |
| `linux_supported` | `eq` | bool |
| `macos_supported` | `eq` | bool |

```json
{
  "request_data": {
    "filters": [
      { "field": "linux_supported", "operator": "eq", "value": true },
      { "field": "is_high_risk", "operator": "eq", "value": false }
    ]
  }
}
```

```json
{
  "reply": {
    "total_count": 2,
    "scripts": [
      {
        "script_uid": "43973ba1e...",
        "name": "list_directories",
        "description": "List directories under a path",
        "modification_date": 1719792000000,
        "created_by": "dc@example.com",
        "is_high_risk": false,
        "windows_supported": true,
        "linux_supported": true,
        "macos_supported": true,
        "script_input_type": "string",
        "script_output_type": "string"
      }
    ]
  }
}
```

- `script_uid` is the stable handle passed to `get_script_metadata` and `run_script`.
- ⚠ verify: exact member values of `script_input_type` / `script_output_type` enums.

**Harness relevance.** ✅ Read — enumerate the library and resolve `script_uid`s.

---

## `get_script_metadata` — `/public_api/v1/scripts/get_script_metadata/`

**Purpose.** Return full definition of a single script: I/O typing, entry point, and
declared parameters.

| Request field | Req? | Notes |
|---------------|------|-------|
| `script_uid` | ✅ | Single script uid (from `get_scripts`). |

```json
{ "request_data": { "script_uid": "43973ba1e..." } }
```

```json
{
  "reply": {
    "script_uid": "43973ba1e...",
    "name": "list_directories",
    "script_input_type": "string",
    "script_output_type": "string",
    "entry_point_definition": "run",
    "script_parameters_definition": [
      { "name": "path", "type": "string", "required": true },
      { "name": "depth", "type": "number", "required": false }
    ]
  }
}
```

- ⚠ verify: exact key names `entry_point_definition` and
  `script_parameters_definition[]`, and the parameter `type` enum members.

**Harness relevance.** ✅ Read — inspect a script's parameter contract without executing.

---

## `get_script_execution_status` — `/public_api/v1/scripts/get_script_execution_status/`

**Purpose.** Poll the overall status of a script-execution action by `action_id`.

| Request field | Req? | Notes |
|---------------|------|-------|
| `action_id` | ✅ | The action id returned by a `run_*` call. |

```json
{ "request_data": { "action_id": "8143" } }
```

```json
{
  "reply": {
    "general_status": "COMPLETED_SUCCESSFULLY",
    "endpoints_count": {
      "total": 3,
      "completed_successfully": 3,
      "failed": 0,
      "pending": 0,
      "in_progress": 0
    }
  }
}
```

- `general_status` ∈ `PENDING | IN_PROGRESS | COMPLETED_SUCCESSFULLY | COMPLETED_PARTIAL | ...`
  (⚠ verify full `COMPLETED_*` enum set).
- ⚠ verify: exact key names within the `endpoints_count` breakdown.

**Harness relevance.** ✅ Read — observe status of an execution (the harness does not
trigger executions, but may report on one that exists).

---

## `get_script_execution_results` — `/public_api/v1/scripts/get_script_execution_results/`

**Purpose.** Return per-endpoint results for a completed/partial execution action.

| Request field | Req? | Notes |
|---------------|------|-------|
| `action_id` | ✅ | The action id to fetch results for. |

```json
{ "request_data": { "action_id": "8143" } }
```

```json
{
  "reply": {
    "results": [
      {
        "endpoint_id": "1a2b3c...",
        "endpoint_name": "web-prod-01",
        "endpoint_status": "CONNECTED",
        "execution_status": "COMPLETED_SUCCESSFULLY",
        "standard_output": "…",
        "retrieved_files": 0,
        "failed_files": 0,
        "retention_date": 1720396800000,
        "command_output": ["…"]
      }
    ]
  }
}
```

- ⚠ verify: whether `command_output` is a string or array, and the `execution_status`
  enum members.

**Harness relevance.** ✅ Read — inspect per-endpoint output/evidence of an execution.

---

## `get_script_execution_result_files` — `/public_api/v1/scripts/get_script_execution_result_files/`

**Purpose.** Return a download handle for files retrieved from a single endpoint during
an execution.

| Request field | Req? | Notes |
|---------------|------|-------|
| `action_id` | ✅ | The action id. |
| `endpoint_id` | ✅ | The endpoint whose retrieved files are wanted. |

```json
{ "request_data": { "action_id": "8143", "endpoint_id": "1a2b3c..." } }
```

```json
{ "reply": { "DATA": "https://.../download?guid=…" } }
```

- ⚠ verify: response field name (`reply.DATA`) and whether it is a signed URL or a bare
  GUID to be resolved against a separate download endpoint.

**Harness relevance.** ✅ Read — resolve a retrieved-file download link (no execution).

---

## Action calls — OUT OF HARNESS SCOPE 🚫

The following **execute code on live customer endpoints**. The **read-only harness never
calls them**; they are listed only so the harness can positively identify and refuse an
action path.

### `run_script` — `/public_api/v1/scripts/run_script/` 🚫

Runs a **library** script on a set of endpoints.

| Request field | Req? | Notes |
|---------------|------|-------|
| `script_uid` | ✅ | Library script to run. |
| `endpoint_id_list` | ✅ | Endpoint filter — target set. |
| `parameters_values` | — | Object of parameter name → value. |
| `timeout` | — | Execution timeout in **seconds**. |
| `incident_id` | — | Optional incident to associate the action with. |

Response: `reply.action_id` + `reply.endpoints_count`.

### `run_snippet_code_script` — `/public_api/v1/scripts/run_snippet_code_script/` 🚫

Runs **raw ad-hoc script text** (not from the library) on a set of endpoints.

| Request field | Req? | Notes |
|---------------|------|-------|
| `snippet_code` | ✅ | Raw script source text to execute. |
| `endpoint_id_list` | ✅ | Endpoint filter — target set. |

Response: `reply.action_id` + `reply.endpoints_count`.

> **Never invoked by the harness.** Both `run_*` calls cause remote code execution on
> customer machines. They are the canonical "action" surface of this family and are
> refused by the read-only harness, which generates signal into the tenant via its own
> agents rather than driving Cortex's live-terminal execution path.

---

## Harness relevance summary

| Call | Path (`…/scripts/…/`) | Kind | Harness |
|------|------------------------|------|---------|
| `get_scripts` | `get_scripts/` | Read — list script library | ✅ read |
| `get_script_metadata` | `get_script_metadata/` | Read — one script's definition | ✅ read |
| `get_script_execution_status` | `get_script_execution_status/` | Read — action status by id | ✅ read |
| `get_script_execution_results` | `get_script_execution_results/` | Read — per-endpoint results | ✅ read |
| `get_script_execution_result_files` | `get_script_execution_result_files/` | Read — retrieved-file link | ✅ read |
| `run_script` | `run_script/` | Action — run library script | 🚫 action |
| `run_snippet_code_script` | `run_snippet_code_script/` | Action — run raw code | 🚫 action |

---

## End-to-end example — `get_scripts` (advanced auth)

Placeholders `${SIG}`, `${NONCE}`, `${TS}` are the advanced-auth signature, nonce, and
epoch-ms timestamp — construct them per [./01-authentication.md](./01-authentication.md) §2
(`SIG = sha256_hex(API_KEY + NONCE + TS)`).

```bash
curl -sS -X POST \
  "https://api-${FQDN}/public_api/v1/scripts/get_scripts/" \
  -H "x-xdr-auth-id: ${API_KEY_ID}" \
  -H "x-xdr-nonce: ${NONCE}" \
  -H "x-xdr-timestamp: ${TS}" \
  -H "Authorization: ${SIG}" \
  -H "Content-Type: application/json" \
  -d '{
    "request_data": {
      "filters": [
        { "field": "linux_supported", "operator": "eq", "value": true },
        { "field": "is_high_risk", "operator": "eq", "value": false }
      ]
    }
  }'
```

# Parity reads: the named tenant reads behind the MCP server

> The response contracts behind the eight `xsiam.*` parity tools (in-container tenant
> reader spec §8.1, Track R): the six named reads `xsiam.tenant_info.get`,
> `xsiam.audit_log.list`, `xsiam.asset.list`, `xsiam.asset.get`,
> `xsiam.vulnerability.list` and `xsiam.assessment_result.list`, and the two ZIP
> reads `xsiam.playbook.get` and `xsiam.script.get` (§7). Each tool runs one catalog
> op through `POST /api/xsiam/tenants/{name}/operations/{op_id}` and projects the
> reply in `core/mcp_server/tools/xsiam.py` (`_SHAPES` for the six, `_archive_get`
> for the two). Shared request conventions are in
> [`02-conventions.md`](./02-conventions.md); the catalog is
> [`14-operation-catalog.md`](./14-operation-catalog.md).

## Provenance

Recorded 2026-11-13 from PANW's public Cortex XSIAM Platform API reference
(`cortex-docs.paloaltonetworks.com/xsiam-api/...`, the OpenAPI blocks on each page;
the pages carry XSIAM 3.5 and 3.6 version labels). The catalog packs' `doc_link`s
point at `cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/...`; on 2026-11-13 that
project answered 404 ("Project not found"), so the pages below are the current
public source. Nothing here was taken from `~/cortex-mcp`. **No reply below has
been checked against a live tenant**: the live check (plan Task 19) confirms each
envelope and flips what it disproves.

Field names and types are PANW's; the descriptions are ours.

## How a reply becomes a result

- **Envelope.** The tenant wraps every reply in `{"reply": ...}` (the by-id asset
  page documents it bare). The catalog client removes `reply`, so the tools read
  the inner object. A missing list key, a missing or non-integer total, or a row
  without its identifier is `XSIAM_PARSE_ERROR`, never an empty list.
- **Projection.** Each shape keeps only its identifier, type or status and time
  fields (`fields`). Free text a tenant user wrote goes under `row["tenant_text"]`
  (`tenant_text`), and so does every name someone chose for an object (an asset's
  display name, an assessment profile's or a custom standard's name), the same
  class as the issue and case names and `assigned_prevention_policy` the reader
  frames (spec §7.7). An identifier-like value such as `AUDIT_OWNER_EMAIL` stays a
  field. `required` is the identifier. Every string
  is neutralised and capped as the tenant reader's are (invisible, control and
  bidi characters stripped, 200 characters; lists and objects 50 entries), with
  per-row `text_truncated` / `list_truncated`. Epoch-millisecond and ISO times
  become UTC ISO-8601 through the repo's one coercer.
- **Framing.** Every result carries `data.data_notice`; a parse error's `detail`
  names reply keys (identifier-shaped only) and types, never a tenant value.
- **Paging.** The tools send `{"request_data": {}}`, one request, and cap rows
  client-side with `limit`/`offset` over that one reply: `offset` cannot reach past
  the tenant's first page (audit log 100 rows, vulnerabilities 500, assets the
  tenant's default; assessment results are not paginated). `truncated` is
  `offset + returned < total`, so `truncated: true` says the tenant holds more.
- **Licences.** Each page names the licence it needs; a tenant without it answers
  402, which the route maps to `XSIAM_API_ERROR`. That is a licence refusal, not
  an absence of rows.

## 1. `xsiam.tenant_info.get` — `OP-SYSTEM-GET-TENANT-INFO`

- **Page:** <https://cortex-docs.paloaltonetworks.com/xsiam-api/cortex-platform/system-management> ("Get Tenant Info").
- **Call:** `POST /public_api/v1/system/get_tenant_info`, body `{"request_data": {}}`. Matches the catalog path.
- **Licence:** XSIAM Premium, Enterprise or NG SIEM.
- **Reply:** one object (no list, no total): the tenant's licences, device counts and add-ons.

| key | type |
|---|---|
| `xsiam_premium_expiration` | integer |
| `purchased_xsiam_premium` | object `{users, gb, agents}` (integers) |
| `pro_per_endpoint_expiration` | integer |
| `purchased_pro_per_endpoint` | object `{agents}` |
| `data_enabled_pro_per_endpoint` | integer |
| `prevent_expiration` | integer |
| `purchased_prevent` | integer |
| `installed_prevent` | integer |
| `pro_gb_expiration` | integer |
| `purchased_pro_gb` | object `{gb}` |
| `installed_pro_gb` | integer |
| `compute_unit_expiration` | integer |
| `purchased_compute_unit` | integer |
| `host_insights_expiration` | integer |
| `enabled_host_insights` | integer |
| `purchased_host_insights` | integer |
| `forensics_expiration` | integer |
| `enabled_forensics` | integer |
| `pro_cloud_expiration` | integer |
| `purchased_pro_cloud` | object `{agents}` |
| `installed_pro_cloud` | integer |
| `data_enabled_pro_cloud` | integer |
| `identity_threat_expiration` | integer |
| `xth_expiration` | integer |
| `purchased_xth` | integer |
| `threat_intelligence_management_expiration` | integer |
| `purchased_threat_intelligence_management` | integer |
| `attack_surface_management_expiration` | integer |
| `purchased_attack_surface_management` | integer |
| `xsiam_ep_hot_expiration` | string |
| `purchased_xsiam_ep_hot` | integer |
| `xsiam_ep_cold_expiration` | string |
| `purchased_xsiam_ep_cold` | integer |
| `xsiam_gb_hot_expiration` | string |
| `purchased_xsiam_gb_hot` | integer |
| `xsiam_gb_cold_expiration` | string |
| `purchased_xsiam_gb_cold` | integer |

**Shape.** `data.tenant_info`: all 37 keys, values verbatim (a documented key the
tenant did not send is `null`). The page gives no units for the expirations, and
a 0 expiration has been read as "not licensed" (`identity_threat_expiration == 0`
in `docs/brainstorm/2026-11-02-cortex-bot-review-and-pov-integration.md`), so none
is converted to a date. There is no identifier: `required` is unset, and the
object must carry at least one documented key or the reply is `XSIAM_PARSE_ERROR`.
No free text, so no `tenant_text`.

**No version field.** The documented reply has no platform or tenant version
(PANW's XSOAR integration page for `xdr-get-tenant-info` lists licence outputs
only, too). PD9 still spends one request per tenant-evidence snapshot on this op
and reads a top-level `version` if a tenant sends one
(`connectors/tenant_evidence.py::TENANT_VERSION_KEY`; a string, or a number recorded
as its text; any other type is `null` with `XSIAM_PARSE_ERROR`); on a reply as documented the
snapshot records `tenant_version: null` with `TENANT_VERSION_NOT_REPORTED`. The live
check decides whether a version key exists; if none does, PD9's extra request
records nothing and should be dropped or repointed.

## 2. `xsiam.audit_log.list` — `OP-AUDITS-MANAGEMENT-LOGS`

- **Page:** <https://cortex-docs.paloaltonetworks.com/xsiam-api/cortex-platform/audit-log> ("Get Audit Management Log").
- **Call:** `POST /public_api/v1/audits/management_logs`, body `{"request_data": {}}` (empty returns all, newest first by default). At most 100 rows per request (`search_to` defaults to 100); the tool reads one request. Matches the catalog path (no trailing slash; the retired server sent one).
- **Licence:** XSIAM Premium, Enterprise or NG SIEM.
- **Reply:** list key **`data`**, total key **`total_count`** (the filter's total without paging); also `result_count`.

| key | type | projected as |
|---|---|---|
| `AUDIT_ID` | integer | `audit_id` (**required**) |
| `AUDIT_OWNER_NAME` | string | `tenant_text.owner_name` |
| `AUDIT_OWNER_EMAIL` | string | `owner_email` |
| `AUDIT_ASSET_JSON` | string (JSON text) | not projected |
| `AUDIT_ASSET_NAMES` | string | `tenant_text.asset_names` |
| `AUDIT_HOSTNAME` | string | `hostname` |
| `AUDIT_RESULT` | string | `result` |
| `AUDIT_REASON` | string | `tenant_text.reason` |
| `AUDIT_DESCRIPTION` | string | `tenant_text.description` |
| `AUDIT_ENTITY` | string enum (e.g. `AUTH`, `API_KEY`, `POLICY_PROFILES`, `RULES`, …) | `entity` |
| `AUDIT_ENTITY_SUBTYPE` | string | `entity_subtype` |
| `AUDIT_SESSION_ID` | integer | not projected |
| `AUDIT_CASE_ID` | integer | `case_id` |
| `AUDIT_INSERT_TIME` | integer (epoch ms) | `insert_time` (ISO-8601 UTC) |
| `AUDIT_SEVERITY` | string | `severity` |
| `AUDIT_LINK` | string | not projected |
| `AUDIT_SOURCE_IP` | string | not projected |
| `AUDIT_USER_AGENT` | string | not projected |
| `AUDIT_USER_ROLES` | array of string | not projected |
| `AUDIT_ADDITIONAL_INFORMATION` | object `{endpoint_names[], endpoint_count}` | not projected |
| `AUDIT_OBJECT_ID` | string | not projected |

`AUDIT_ENTITY` is kept verbatim; a value outside the documented enum is not mapped.

## 3. `xsiam.asset.list` — `OP-ASSETS`

- **Page:** <https://cortex-docs.paloaltonetworks.com/xsiam-api/cortex-platform/asset-inventory> ("Get all or filtered assets").
- **Call:** `POST /public_api/v1/assets`, body `{"request_data": {}}`. Up to 1,000 assets per request (`search_to − search_from ≤ 1000`).
- **Licence:** XSIAM Premium, Enterprise, NG SIEM or Enterprise Plus.
- **Reply:** list key **`data`**, total key **`metadata.total_count`** ("total results of this filter without paging"). `metadata.filter_count` is the number returned in this response, so it is not the total.
- **Op choice.** `OP-ASSETS` is the documented route and the nearest to the `/assets/` POST the retired server sent. `OP-ASSETS-GET-ASSETS` (`/public_api/v1/assets/get_assets/`, the cortex-bot harvest) is not on PANW's page. The live check confirms `OP-ASSETS` or flips the shape to the other op.

| key | type | projected as |
|---|---|---|
| `xdm.asset.id` | string | `asset_id` (**required**) |
| `xdm.asset.strong_id` | string | `strong_id` |
| `xdm.asset.name` | string, nullable | `tenant_text.name` (a display name the resource's creator chose) |
| `xdm.asset.type.name` | string | `type_name` |
| `xdm.asset.type.category` | string | `type_category` |
| `xdm.asset.type.class` | string | `type_class` |
| `xdm.asset.type.id` | string | not projected |
| `xdm.asset.provider` | string | `provider` |
| `xdm.asset.realm` | string | `realm` |
| `xdm.asset.cloud.region` | string, nullable | `cloud_region` |
| `xdm.asset.first_observed` | integer (epoch ms) | `first_observed` (ISO-8601 UTC) |
| `xdm.asset.last_observed` | integer (epoch ms) | `last_observed` (ISO-8601 UTC) |
| `xdm.asset.resource_group` | string, nullable | `tenant_text.resource_group` |
| `xdm.asset.hierarchy.path` | string, nullable | `tenant_text.hierarchy_path` |
| `xdm.asset.hierarchy.id_path` | string, nullable | not projected |
| `xdm.asset.group_ids` | array of string | not projected |
| `xdm.asset.owners` | array of string, nullable | not projected |
| `xdm.asset.owners_obj` | array of `{name, type}`, nullable | not projected |
| `xdm.host.ipv4_addresses` | array of string | not projected |
| `issues_critical`, `cases_critical` | integer | not projected |
| `issues_breakdown`, `cases_breakdown` | object `{critical, high, medium, low}` | not projected |

Row keys are flat dotted XDM names: `"xdm.asset.id"` is one key, not a path.

## 4. `xsiam.asset.get` — `OP-ASSETS-ID`

- **Page:** same page, "Get asset by ID".
- **Call:** `GET /public_api/v1/assets/{id}`, no body. `asset_id` fills `{id}` through the executor route's guard: a value with `/` or `\`, or one that is `.` or `..`, is refused with `XSIAM_CONFIG_ERROR` before any tenant call; anything else is percent-encoded into one segment. Matches the catalog path (no trailing slash; the retired server sent one).
- **Reply:** **`data`**, a list holding the one asset, and `metadata {filter_count, total_count}`. An unknown id is a documented 404 ("Asset Not Found"), which the route answers as `XSIAM_API_ERROR`.
- **Fields:** the asset fields of §3 minus `xdm.asset.resource_group`, `xdm.asset.hierarchy.*` and `xdm.host.ipv4_addresses`, which this page does not list (they project as `null`).

**Shape.** `data.asset`: the §3 projection of the one record. A 200 whose `data`
holds no record or more than one is `XSIAM_PARSE_ERROR`.

## 5. `xsiam.vulnerability.list` — `OP-UVEM-GET-VULNERABILITIES`

- **Page:** <https://cortex-docs.paloaltonetworks.com/xsiam-api/vulnerability-intelligence/vulnerabilities> ("Get list of vulnerabilities").
- **Call:** `POST /public_api/uvem/v1/get_vulnerabilities`, body `{"request_data": {}}` (`request_data` is required). `search_to` defaults to 500; deeper pages use `use_page_token` / `next_page_token`, which the tool does not follow. Matches the catalog path.
- **Licence:** XSIAM Premium; on Enterprise and NG SIEM, the Cortex Cloud Posture Management add-on.
- **Reply:** list key **`vulnerabilities`**, total key **`total_count`**; also `result_count` and `next_page_token`.

| key | type | projected as |
|---|---|---|
| `vulnerability_id` | string | `vulnerability_id` (**required**) |
| `cvss_severity` | string, nullable | `cvss_severity` |
| `cvss_score` | number, nullable | `cvss_score` |
| `cvss_version` | string, nullable | `cvss_version` |
| `epss_score` | nullable | `epss_score` |
| `cisa_kev` | nullable | `cisa_kev` |
| `attack_vector` | string | `attack_vector` |
| `first_published` | integer (epoch ms) | `first_published` (ISO-8601 UTC) |
| `last_modified` | integer (epoch ms) | `last_modified` (ISO-8601 UTC) |
| `cvss_score_source`, `cvss_severity_source` | string, nullable | not projected |
| `package_names`, `vendors`, `distribution_and_releases` | array of string | not projected |
| `reported_exploited_by` | array of object | not projected |

Vulnerability records are PANW intelligence, not text a tenant user wrote: no
`tenant_text`. The result is framed all the same.

## 6. `xsiam.assessment_result.list` — `OP-COMPLIANCE-GET-ASSESSMENT-RESULTS`

- **Page:** <https://cortex-docs.paloaltonetworks.com/xsiam-api/compliance-controls/assessment-results> ("Get assessment profile results"); the row model is on <https://cortex-docs.paloaltonetworks.com/xsiam-api/compliance-controls/models>.
- **Call:** `POST /public_api/v1/compliance/get_assessment_results`, body `{"request_data": {}}`. The API supports neither sorting nor pagination; the only filter is `labels` (`contains` / `not_contains`). **Path differs:** the catalog op sends a trailing slash (`.../get_assessment_results/`, the cortex-bot harvest); the page has none. The live check confirms which the tenant accepts.
- **Licence:** Cortex Cloud Runtime Security or Cloud Posture Management.
- **Reply:** list key **`data`**, total key **`total_count`** ("assessment results available"); also `result_count` (matching the filter), `standard_filter_count`, `standard_total_count`.
- **Row: not documented.** The `AssessmentResult` model is an object with no listed properties. The allowlist below follows the documented `AssessmentProfile` model, on the reading that a result row describes the profile it evaluated; it is provisional until a live reply is recorded. A row without `id` is `XSIAM_PARSE_ERROR`, so a wrong guess fails loudly rather than reading as no results.

| `AssessmentProfile` key | type | projected as |
|---|---|---|
| `id` | string | `id` (**required**) |
| `name` | string | `tenant_text.name` (user-named) |
| `enabled` | boolean | `enabled` |
| `standard_id` | string | `standard_id` |
| `standard_name` | string | `tenant_text.standard_name` (a custom standard is user-named) |
| `asset_group_id` | integer | `asset_group_id` |
| `report_type` | string | `report_type` |
| `creation_time` | string (date-time) | `creation_time` (ISO-8601 UTC) |
| `modification_time` | string (date-time) | `modification_time` (ISO-8601 UTC) |
| `description` | string | `tenant_text.description` |
| `asset_group_name`, `report_targets`, `evaluation_frequency`, `created_by`, `modified_by` | various | not projected |

## 7. `xsiam.playbook.get` / `xsiam.script.get` — `OP-PLAYBOOKS-GET` / `OP-SCRIPTS-GET` (ZIP)

- **Pages:** <https://cortex-docs.paloaltonetworks.com/xsiam-api/cortex-platform/playbooks> ("Get a playbook") and <https://cortex-docs.paloaltonetworks.com/xsiam-api/cortex-platform/scripts> ("Get a script"), read 2026-11-13.
- **Call:** `POST /public_api/v1/playbooks/get` and `POST /public_api/v1/scripts/get`, body `{"request_data": {"filter": {"field": "id" | "name", "value": "<string>"}}}` (`request_data` is required; `field` is an enum of `id` and `name`; `value` is the string the filter must match). Both pages require the feature's granular RBAC permission and state no licence. `OP-SCRIPTS-GET` is new in the catalog (the catalog had only `get_scripts`, `get_script_metadata` and the execution reads); `OP-PLAYBOOKS-GET` was there.
- **Reply:** `200`, the object's YAML "in a ZIP file". The Scripts page gives the content type `application/zip` with an object schema and no properties; the Playbooks page gives no content type. Neither page documents an error response or a not-found answer.
- **Read.** Both ops carry the catalog tag `zip-reply`, so the executor fetches the bytes with `XsiamClient.request_archive` (one request, charged to the per-tenant bucket; 401 / 429 / other 4xx-5xx mapped as for every reply; streamed with `Accept-Encoding: identity` and refused past 4 MiB, `archives.MAX_ARCHIVE_BODY_BYTES`, with a gzip or deflate body inflated in bounded steps and any other encoding refused) and returns `integrations/xsiam/archives.py::zip_members`: at most 50 file members, each `{name, text, bytes, text_truncated, binary, refused}`, plus `member_count` and `members_truncated`. The archive is read in memory with stdlib `zipfile` and never extracted. A member whose declared size is over 256 KiB, or over what is left of 1 MiB in all, is refused unopened (`MEMBER_OVER_CAP` / `TOTAL_OVER_CAP`); so is a member compressed other than stored or deflated (`UNSUPPORTED_COMPRESSION`: how far bzip2 and LZMA stop at a declared size depends on the interpreter's patch level); a member that inflates past its declared size fails zipfile's CRC check and is `UNREADABLE`, as is a corrupt or encrypted one. An archive zipfile cannot open, including a "version needed to extract" it does not read, is `XSIAM_PARSE_ERROR`. Text is UTF-8 only; anything else is `binary: true` with no text.
- **Not an archive.** An archive with no member starts `PK\x05\x06` and reads as `member_count: 0`. A JSON body is the tenant's answer in place of the archive: an `err_code` / `err_msg` goes through the shared application-error test (`has_app_error`, `_app_error_text`); any other JSON, an empty body, or a body that starts neither with a ZIP signature nor parses as JSON is `XSIAM_PARSE_ERROR`, never an empty archive.
- **Shape.** `data.members[]`: `bytes`, `binary`, `refused`, `text_truncated` and `tenant_text {name, text}`. A member's name is chosen by whoever saved the object, and its YAML is written on the tenant, so both are tenant text (spec §7.7). The text is a document: it keeps its tab and line feed, a carriage return is read as a line feed, every other character of the reader's strip set goes, and it is capped by bytes, not at 200 characters. `data.filter` echoes the request; `data.data_notice` frames the result.

## What the live check settles (Task 19)

1. Whether a tenant accepts `OP-ASSETS` (`POST /public_api/v1/assets`), and that its total sits at `metadata.total_count`; otherwise the shape moves to `OP-ASSETS-GET-ASSETS`.
2. The assessment-result row's real fields, and whether `.../get_assessment_results` wants the trailing slash.
3. Whether `get_tenant_info` carries any version key (PD9); if not, the snapshot's extra request records only `TENANT_VERSION_NOT_REPORTED`.
4. Whether the audit-log `AUDIT_*` spellings, the asset `xdm.*` keys and the UVEM keys match the documented ones.
5. That `playbooks/get` and `scripts/get` answer a ZIP whose members are the object's YAML (how many, and how named), and what a tenant answers for an id or name that does not exist.

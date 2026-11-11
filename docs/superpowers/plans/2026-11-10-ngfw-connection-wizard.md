# NGFW Connection Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register a PAN-OS NGFW in SimCore's encrypted vault and run a staged, read-only, transport-injectable preflight of every firewall setting a POV depends on, including a per-agent source/NAT/policy check, behind a console wizard under Manage.

**Architecture:** A new package `core/integrations/panos/` (host validator, codes, strict XML parsers, an allowlisted async read-only client, the preflight) mirrors `core/integrations/xsiam/`. It is exposed through a new `/api/ngfw` router plus a `panos_ngfw` branch at the top of the existing `POST /api/connectors/{kind}/preflight`. The UI adds a `Firewall` destination with Connect → Check → Sources.

**Tech Stack:** Python 3.11, FastAPI, httpx (`AsyncClient`, `MockTransport` in tests), `xml.etree.ElementTree` (stdlib; `defusedxml` if already in `core/requirements.txt`, otherwise stdlib with entity expansion refused, see Task 2), SQLAlchemy async, React + vitest.

**Spec:** `docs/superpowers/specs/2026-11-10-ngfw-connection-wizard-design.md` (approved 2026-11-10). Section refs below (§N) point into it.

## Global Constraints

- Integration kind: `panos_ngfw`. `secret_type_hint`: `panos_api_key_credential`. Codes prefixed `PN_`, all defined in `core/integrations/panos/codes.py`.
- Stage statuses come from `connectors/preflight.py`: `ok | degraded | blocked | skipped`. Same `overall` rule (any blocked → blocked; any degraded/skipped → degraded; else ready).
- Reads only: `type=op` with root `<show>`/`<test>` in an allowlist; `type=config` with `action=show` only (running config; spec §5.1). `get` is not used. `type=keygen` only from the keygen endpoint.
- API key travels in the `X-PAN-KEY` header. Every request is a POST, form-encoded body, to `https://{host}/api/`. Nothing secret goes in a URL.
- The password is never persisted, logged, echoed or included in an exception message.
- Timeout 10 s per call. `CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT` default `120`. `CORTEXSIM_PANOS_ALLOW_WRITE` default `False`, consumed by nothing.
- Clock-skew threshold 30 s. Log-type staleness 15 min.
- Default probes: `1.1.1.1 udp/53 dns-base`, `198.51.100.10 tcp/80 web-browsing`, `198.51.100.10 tcp/443 ssl`.
- Errors are structured `{error, code, detail}`. Preflight returns 200 with `overall: "blocked"`, never an HTTP error, for firewall-side failures.
- Fixtures use documentation addresses only (192.0.2.0/24 inside, 198.51.100.0/24 outside, 203.0.113.0/24 NAT pool), hostname `lab-fw-01`. No lab IP, serial or hostname from Henry's PA-440.
- Stage ids, in order: `config, dns_tls, auth, system, clock, forwarding_sls, forwarding_syslog, eal_device, log_forwarding_profiles, decryption, registered_ip`. Per source: `address, source_seen, policy_match, rule_profiles, nat, user_id, registered_tags`.
- Commits: stage files by name; subject ≤72 chars, `type(api|ui|docs): …`; end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push, no PR.

## Review Focus

1. **A firewall that answers HTML, not XML** (a captive portal, a GlobalProtect portal page on the mgmt IP, an HTTP 200 login page). Expected: `PN_SHAPE_UNRECOGNISED` (or `PN_UNREACHABLE` for non-2xx), never "auth ok". Test in Task 3.
2. **The API key surviving in an error.** An httpx exception message carries the request, and the request carries `X-PAN-KEY`. Expected: no stage detail, log line or response contains the key. Test in Task 5.
3. **An agent with `last_ip = None` (never registered from a reachable address) or an IPv6 `last_ip`.** Expected: the source is `unknown` and the source's stages are degraded/skipped. No `test security-policy-match` is sent with an empty source. Test in Task 6.
4. **Two firewalls registered.** Expected: `?integration=` picks one, and the default is the first by name. Address overrides are per firewall, not global. Test in Task 7.
5. **XML entity-expansion / external-entity payloads in a firewall response.** Expected: the parser refuses (`PN_SHAPE_UNRECOGNISED`), with no expansion. Test in Task 2.

---

### Task 1: Package scaffold, codes, host validator, credential validation, flags

**Files:**
- Create: `core/integrations/panos/__init__.py`, `core/integrations/panos/codes.py`, `core/integrations/panos/host.py`, `core/integrations/panos/exceptions.py`
- Modify: `core/api/credentials.py` (`_validate_integration_config`), `core/config.py` (two settings after `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE`)
- Test: `tests/integrations/__init__.py`, `tests/integrations/panos/__init__.py`, `tests/integrations/panos/test_host.py`, `tests/api/test_credentials_panos.py`

**Interfaces:**
- Produces: `PANOS_KIND = "panos_ngfw"` (in `__init__.py`); `normalize_mgmt_host(raw: str) -> str` (returns `host` or `host:port`, lowercased, IPv6 bracketed); `PanosConfigError(ValueError)`, `PanosShapeError(RuntimeError)`, `PanosWriteRefused(RuntimeError)`, `PanosTransportError(RuntimeError)` with attribute `code: str`; `validate_panos_config(config: dict) -> dict` (returns the config with defaults filled: `verify_tls=True, vsys="vsys1", device_name="localhost.localdomain", forwarding_path="auto", routing_instance="default", probe_destinations=DEFAULT_PROBES, observed_sources={}, agent_addresses={}`); `DEFAULT_PROBES: list[dict]`. `codes.py`: one `str` constant per `PN_*` code named in the spec (§5.2–5.5, §4.2), plus `ALL_CODES: frozenset[str]`.

- [ ] **Step 1: Write failing tests**

`test_host.py`: `normalize_mgmt_host` accepts `"192.0.2.1"`, `"fw.example.test"`, `"fw.example.test:4443"`, `"[2001:db8::1]"`, `"2001:db8::1"` → `"[2001:db8::1]"`. It raises `PanosConfigError` for `""`, `"https://192.0.2.1"`, `"192.0.2.1/api"`, `"admin@192.0.2.1"`, `"192.0.2.1?x=1"`, `"192.0.2.1:0"`, `"192.0.2.1:70000"`, `"a b"`. `validate_panos_config({"host":"192.0.2.1"})["verify_tls"] is True`. `validate_panos_config` raises for `vsys="vsys"`, `forwarding_path="ftp"`, a probe with `protocol="icmp"`, and `observed_sources={"a":"not-an-ip"}`. An AST guard test asserts `def normalize_mgmt_host` is defined exactly once under `core/`.

`test_credentials_panos.py` (uses `make_client` with `api.credentials.router`): PUT kind `panos_ngfw` with `config={"host":"https://x"}` → 400, `code == "INTEGRATION_CONFIG_INVALID"`. A valid host → 200. The stored config has `verify_tls: true` filled. A test that `GET /api/credentials/secrets` does not list the backing row (already true; pins it for this kind).

- [ ] **Step 2: Run, expect FAIL** — `PYTHONPATH=core .venv/bin/python -m pytest tests/integrations/panos/test_host.py tests/api/test_credentials_panos.py -q` → import errors.

- [ ] **Step 3: Implement.** `host.py` uses `ipaddress` for literals and the RFC 1123 label regex for names. `_validate_integration_config` gains an `if kind == PANOS_KIND:` branch that calls `validate_panos_config`, maps `PanosConfigError` → 400, and writes the defaulted dict back into `body.config` (change the call site in `put_integration` to use the returned config). Settings: `CORTEXSIM_PANOS_ALLOW_WRITE: bool = False` (comment: designed in spec §7, no consumer in v1), `CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT: int = 120`.

- [ ] **Step 4: Run, expect PASS** (same command).

- [ ] **Step 5: Commit** — `feat(api): add panos_ngfw credential kind with mgmt host validation`.

### Task 2: Strict XML parsers + fixtures

**Files:**
- Create: `core/integrations/panos/xmlparse.py`, `tests/fixtures/panos/*.xml`, `tests/integrations/panos/test_xmlparse.py`

**Interfaces:**
- Produces (all raise `PanosShapeError` on any shape they do not recognise; none return an empty value for an unrecognised shape):
  - `parse_response(body: bytes) -> ET.Element`. Requires root `<response>`. `status="success"` → returns `<result>` (or the root for keygen). `status="error"` → raises `PanosApiError(code: str|None, message: str)` (a `PanosShapeError` subclass carrying the PAN-OS `code` attribute). Refuses DOCTYPE/ENTITY (checks raw bytes for `<!DOCTYPE`/`<!ENTITY` before parsing).
  - `parse_keygen(result) -> str`
  - `parse_system_info(result) -> SystemInfo` (dataclass: `hostname, model, serial, sw_version, multi_vsys: bool, operational_mode, advanced_routing: bool, cloud_mode`)
  - `parse_clock(result) -> str` (raw text) and `parse_timezone(result) -> str|None`; `clock_to_utc(text: str, tz: str|None) -> datetime|None` (None if tz unknown)
  - `parse_logging_status(result) -> list[CollectorStatus]` (`name, connection_ip, active: bool, types: dict[str, LogTypeRow]`; `LogTypeRow(created: datetime|None, forwarded: datetime|None, total: int)`; firewall-local naive datetimes)
  - `parse_lsf_config(result) -> LsfConfig(enabled: bool, region: str|None)`; `parse_eal_device(result) -> bool`
  - `parse_log_forwarding_profiles(result) -> list[LfProfile]` (`name, eal: bool, matches: list[LfMatch(log_type, filter, send_to_panorama: bool, syslog_profiles: list[str])], has_tag_action: bool`)
  - `parse_syslog_profiles(result) -> list[SyslogProfile]` (`name, servers: list[SyslogServer(name, server, transport, port, format, facility)]`)
  - `parse_decryption_rules(result) -> list[tuple[str, bool]]` (name, enabled)
  - `parse_registered_ips(result) -> dict[str, list[str]]` (ip → tags; empty result element → `{}`; an unknown child → raise)
  - `parse_policy_match(result) -> PolicyMatch|None` (`name, index, action`); `parse_nat_match(result) -> NatMatch|None`
  - `parse_rule(result) -> RuleConfig` (`name, action, disabled, group: str|None, profiles: dict[str,str], log_setting: str|None`)
  - `parse_profile_group(result) -> dict[str, str]` (profile type → profile name)
  - `parse_profile_actions(result, ptype: str) -> ProfileActions` (`by_severity: dict[str,str]` using buckets `alert|allow|block|sinkhole|signature-default`, and for spyware `dns_security: dict[str,str]` from `botnet-domains/dns-security-categories` + `lists`)
  - `parse_user_mapping(result) -> list[str]` (users); `parse_session_count(result) -> int`; `parse_fib(result) -> str|None` (interface); `parse_zone_userid(result) -> bool`; `parse_interface_zone(result, iface: str) -> str|None`
  - `classify_action(raw: str) -> str` (`default`→`signature-default`; `drop|reset-client|reset-server|reset-both|block-ip|block`→`block`; `alert`, `allow`, `sinkhole` as is; anything else raises)

- [ ] **Step 1: Write fixtures.** One XML file per command/xpath in spec §5.2–5.3, modelled on PAN-OS 10.2.8 shapes (the logging-status text layout is in the spec's source report). Name them `<command>__<variant>.xml`, e.g. `logging_status__active.xml`, `logging_status__inactive.xml`, `logging_status__threat_stale.xml`, `policy_match__alertall.xml`, `rule__group_alertall.xml`, `profile_spyware__dns_sinkhole.xml`, `lfp__missing_wildfire.xml`, `lfp__tag_action.xml`, `syslog__two_servers_udp.xml`, `system_info__ok.xml`, `system_info__multivsys.xml`, `error__403.xml`, `html_login_page.html`, `xxe.xml`. Documentation addresses only.

- [ ] **Step 2: Write failing tests** in `test_xmlparse.py`: one positive test per parser against its fixture, with exact values asserted (e.g. `logging_status__active` → collector `active is True`, `types["threat"].total == 917`, `types["url"]` is absent and the parser does not invent it). One negative test per parser: a wrong root, or a missing required child, raises `PanosShapeError`. `parse_response(open("xxe.xml"))` and `parse_response(open("html_login_page.html"))` raise. `classify_action("default") == "signature-default"`. `classify_action("weird")` raises.

- [ ] **Step 3: Run, expect FAIL.** `pytest tests/integrations/panos/test_xmlparse.py -q`

- [ ] **Step 4: Implement `xmlparse.py`.** For `parse_logging_status`, split the collector `#text` on blank-line-separated blocks. Key on the labelled lines `Log Collector`, `Connection IP`, `Connection Status` (`Active` substring), and per-type rows matching `^\s*(\S+)\s+(Not Available|\d{4}/\d\d/\d\d \d\d:\d\d:\d\d)\s+(Not Available|…)\s+(\d+)\s+(\d+)\s+(\d+)$`. A block with a `Log Collector` line but no `Connection Status` line raises.

- [ ] **Step 5: Run, expect PASS.**

- [ ] **Step 6: Commit** — `feat(api): add strict PAN-OS XML parsers with lab-shaped fixtures`.

### Task 3: Read-only async client

**Files:**
- Create: `core/integrations/panos/client.py`, `tests/integrations/panos/test_client.py`, `tests/integrations/panos/test_readonly_guard.py`

**Interfaces:**
- Consumes: Task 1 exceptions/host; Task 2 `parse_response`, `parse_keygen`.
- Produces: `class PanosClient` with `__init__(host: str, api_key: str|None, *, verify_tls: bool, transport: httpx.AsyncBaseTransport|None = None, max_calls: int = 120, timeout: float = 10.0)`; `async op(cmd_xml: str) -> ET.Element`; `async config_show(xpath: str) -> ET.Element`; `async keygen(username: str, password: SecretStr) -> str`; `calls_issued: int`; `async aclose()`; async context manager. `OP_ALLOWLIST: frozenset[tuple[str, ...]]` of element paths (e.g. `("show","system","info")`, `("show","logging-status")`, `("test","security-policy-match")`, `("test","nat-policy-match")`, `("test","routing","fib-lookup")`, `("test","advanced-routing","fib-lookup")`, `("show","session","all")`, `("show","user","ip-user-mapping")`, `("show","object","registered-ip")`, `("show","clock")`). Over budget → `PanosTransportError(code=PN_CALL_BUDGET)`.
- Transport mapping: connect/DNS error → `PanosTransportError(PN_UNREACHABLE)`; `ssl` verification error → `PN_TLS_FAILED`; HTTP 403 or `PanosApiError` code `403` → `PN_AUTH_REJECTED`; other non-2xx → `PN_UNREACHABLE` with the status in the message. Messages never include headers, the body or the key.

- [ ] **Step 1: Failing tests** (`httpx.MockTransport` recording requests): `op("<show><system><info/></system></show>")` sends POST to `https://192.0.2.1/api/` with form `type=op&cmd=…`, header `X-PAN-KEY`, and no `key=` anywhere in the URL. `config_show(xpath)` sends `type=config&action=show&xpath=…`. `calls_issued` counts requests. The 121st call raises `PN_CALL_BUDGET` before sending. `test_readonly_guard.py`: `op()` raises `PanosWriteRefused` with zero requests sent, for each of `<request>…`, `<set>…`, `<clear>…`, `<delete>…`, `<debug>…`, `<commit/>`, `<show><config><running/></config></show>` (not allowlisted), `<test><url>x</url></test>`, a `<show>` root carrying a sibling `<request>`, and a non-XML string. An AST guard asserts no string literal under `core/integrations/panos/` equals `"set"`, `"edit"`, `"delete"`, `"move"`, `"rename"`, `"commit"`, `"multi-config"` or `"get"` used as an `action`/`type` value: walk `ast.Dict` / keyword args named `action`/`type` whose values are string constants, and allow only `{"op","config","keygen","show"}`. Error mapping: a `MockTransport` raising `httpx.ConnectError("… X-PAN-KEY: secret …")` → `PanosTransportError` whose `str()` does not contain `secret`. An HTML 200 → `PanosShapeError`.

- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** The allowlist check parses `cmd_xml` with the same entity-refusing parser and walks the single-child chain from the root until the first element with more than one child or with text, so the path is compared, and siblings at any level fail. **Step 4: PASS.** **Step 5: Commit** — `feat(api): add allowlisted read-only PAN-OS XML API client`.

### Task 4: Config-only credential update

**Files:**
- Modify: `core/security/credentials.py` (new method after `put_integration`)
- Test: `tests/security/test_credentials_update_config.py`

**Interfaces:**
- Produces: `async CredentialStore.update_integration_config(name: str, patch: dict[str, Any]) -> IntegrationCredential | None`. It shallow-merges `patch` into `config` (a key whose value is `None` is removed), sets `updated_at`, and leaves `secret_id`, `last_verified_*` and the backing Secret untouched. Returns `None` if the integration is absent.

- [ ] Steps: failing test (put → mark verified → update config → `last_verified_ok` unchanged, secret decrypts to the same value, merge and None-removal hold) → FAIL → implement (reassign a new dict so the SQLAlchemy JSON change is detected) → PASS → commit `feat(api): add config-only integration credential update`.

### Task 5: Firewall-wide preflight

**Files:**
- Create: `core/integrations/panos/preflight.py`
- Test: `tests/integrations/panos/test_preflight_firewall.py`, `tests/integrations/panos/conftest.py` (a `fake_firewall(routes: dict[str, str])` fixture → `httpx.MockTransport` mapping a request's `cmd`/`xpath` to a fixture file, recording calls; an unmapped request → 500)

**Interfaces:**
- Consumes: Tasks 1–3; `connectors.preflight.Stage, OK, DEGRADED, BLOCKED, SKIPPED`.
- Produces: `@dataclass FirewallPreflightReport(firewall: str|None, mgmt_host: str|None, stages: list[Stage], sources: list[dict], queries_issued: int, panos_version: str|None, config_source: str = "running")` with `.overall` and `.to_dict()` (keys: `firewall, kind="panos_ngfw", mgmt_host, overall, stages, sources, queries_issued, panos_version, config_source, proves, tenant_verified: 0`; `proves` is the exact sentence in spec §5.1). `async run_panos_preflight(config: dict, api_key: str, *, firewall: str|None, agents: list[AgentRef], transport=None, now: datetime|None = None, max_calls: int = 120) -> FirewallPreflightReport`. `@dataclass AgentRef(agent_id: str, hostname: str, last_ip: str|None)`. Source checks (Task 6) are called from inside it.

- [ ] **Step 1: Failing tests**, one per stage outcome in spec §5.2, using the fixture router. Pinned behaviours: every stage id appears exactly once, in order, in every report. An unreachable host → `dns_tls` blocked `PN_UNREACHABLE`, all later stages `skipped / PN_SKIPPED_UNREACHABLE`, `queries_issued == 1`. Auth rejected → `PN_SKIPPED_AUTH` on the rest. `verify_tls=False` → `dns_tls` degraded `PN_TLS_UNVERIFIED` even when everything else is ok. A parser failure in `log_forwarding_profiles` → that stage `blocked / PN_SHAPE_UNRECOGNISED` with the xpath in `detail`, and `decryption`/`registered_ip` still ran (their calls are in the recorded list). Forwarding-path matrix (`auto` with SLS only, syslog only, neither, both; `sls` with SLS off; `syslog` with no servers), with the exact codes from spec §5.2. Syslog configured → `forwarding_syslog` is always degraded `PN_SYSLOG_DELIVERY_UNVERIFIED`. Zero LFPs → blocked `PN_NO_LFP`. Clock skew 45 s → degraded `PN_CLOCK_SKEW`; unknown tz → degraded, never ok. `report.queries_issued == len(recorded_calls)`. `to_dict()["tenant_verified"] == 0`. **Review Focus 2:** a transport whose error text contains the key → `json.dumps(report.to_dict())` does not contain it. Each non-ok stage has a non-empty `remediation`.

- [ ] **Step 2: FAIL.** **Step 3: Implement.** One `async _stage_<id>(ctx) -> Stage` per stage. A small runner wraps each in `try/except PanosShapeError → blocked PN_SHAPE_UNRECOGNISED`, `PanosTransportError → short-circuit for PN_UNREACHABLE/PN_TLS_FAILED/PN_AUTH_REJECTED; otherwise blocked with its code`. Remediation strings name the POV consequence (spec §5.1). Write them as module constants so tests can assert on them. Config xpaths are built from `device_name`/`vsys` in one helper `xp(ctx, tail: str) -> str`. Lookups follow spec §5.1 order through one helper `async find_object(ctx, kind: str, name: str) -> ET.Element|None` (vsys → shared → panorama → predefined table).

- [ ] **Step 4: PASS.** **Step 5: Commit** — `feat(api): add staged read-only firewall-wide NGFW preflight`.

### Task 6: Per-source checks, NAT and collisions

**Files:**
- Create: `core/integrations/panos/sources.py`
- Test: `tests/integrations/panos/test_preflight_sources.py`

**Interfaces:**
- Consumes: Task 5 context and `find_object`; Task 2 parsers.
- Produces: `resolve_addresses(agents: list[AgentRef], config: dict) -> list[ResolvedSource]` (`agent_id, hostname, fw_observed_src: str|None, fw_src_provenance: "entered"|"inferred:last_ip"|"unknown", agent_addresses: list[str], agent_addr_provenance: "entered"|"unknown", nat_verdict: "tuple_intact"|"nat_between"|"unknown", collision_with: list[str]`). This is pure, with no I/O. `async check_source(ctx, src: ResolvedSource) -> dict` returns `{agent_id, hostname, fw_observed_src, fw_src_provenance, agent_addresses, agent_addr_provenance, nat_verdict, collision_with, stages: [...], probes: [{destination, port, protocol, application, rule, rule_index, action, profiles: {type: bucket|per-severity}, dns_security: {...}, log_setting}]}`. `stages` uses the per-source ids in Global Constraints.

- [ ] **Step 1: Failing tests.** `resolve_addresses`: the override wins over `last_ip`; `last_ip` gives `inferred:last_ip`; `last_ip=None` with no override → `unknown` (**Review Focus 3**); two agents resolving to the same source → each lists the other in `collision_with`; known agent addresses disjoint from the source → `nat_between`; a containing set → `tuple_intact`; no agent addresses → `unknown`. Through `run_panos_preflight`: no agents → `sources == [{"code": "PN_NO_AGENTS", "status": "degraded", …}]`. A collision → that source's `address` stage blocked `PN_SOURCE_COLLISION` naming both hostnames. `nat_between` → blocked `PN_NAT_BETWEEN_AGENT_AND_FW`. `unknown` agent side → degraded `PN_AGENT_ADDRESS_UNKNOWN`. An unknown source sends **no** `security-policy-match` (assert on recorded calls). Policy match → rule config → group → spyware with DNS `sinkhole` gives `dns_security` reported separately and stage `PN_DNS_SEC_SINKHOLE`. A vulnerability `default` → `signature-default` + `PN_PROFILE_SIGNATURE_DEFAULT`. An allow rule with no profile → `PN_NO_PROFILE`. A rule not in the local rulebase but under the Panorama pre-rulebase is found. A rule found nowhere → `PN_OBJECT_NOT_FOUND`. Zero sessions → `PN_SOURCE_NOT_SEEN`. A tagged source → `PN_SOURCE_TAGGED` with the tags. No user mapping → `PN_NO_USER_MAPPING`. FIB on `advanced_routing=True` sends the `advanced-routing` command. An IPv6 source (`2001:db8::5`) is passed to the firewall unmodified. The call budget exhausted mid-source → the remaining stages `skipped / PN_CALL_BUDGET`.

- [ ] **Step 2: FAIL.** **Step 3: Implement** (zones: config override, else FIB + interface→zone; command XML built with `ET` and `xml.sax.saxutils.escape` on every operator-supplied value). **Step 4: PASS.** **Step 5: Commit** — `feat(api): add per-agent NGFW source, NAT and policy-path checks`.

### Task 7: API surface

**Files:**
- Create: `core/api/ngfw.py`
- Modify: `core/api/connectors.py` (`preflight_connector`: a `panos_ngfw` branch as the first statement, plus an `agents: Optional[str]` query param), `core/main.py` (import + `app.include_router(ngfw_router, prefix="/api")` after `credentials_router`)
- Test: `tests/api/test_ngfw_api.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces endpoints (spec §4.3): `GET /api/ngfw` → `{firewalls: [{name, host, verify_tls, vsys, forwarding_path, last_verified_ok, last_verified_error, last_verified_at}], write_enabled: false, tenant_verified: 0}`. `POST /api/ngfw/keygen` body `KeygenBody(name, host, verify_tls=True, username, password: SecretStr, vsys="vsys1")` → 200 integration metadata; failures → 4xx/502 with codes `PN_KEYGEN_REJECTED` (PAN-OS error / 403), `PN_UNREACHABLE`, `INTEGRATION_CONFIG_INVALID` (400). `PUT /api/ngfw/{name}/sources/{agent_id}` body `{observed_source: str|None, agent_addresses: list[str]|None}` → the merged per-agent view; 404 `INTEGRATION_NOT_FOUND`; 400 on a bad IP. `POST /api/connectors/panos_ngfw/preflight?integration=&agents=a,b` → `FirewallPreflightReport.to_dict()`. No integration → 200 blocked `PN_NO_INTEGRATION`. Undecryptable → `PN_CREDENTIAL_UNDECRYPTABLE`. `agents` absent → all enrolled agents. After the run → `mark_integration_verified(ok=overall != "blocked", error=first blocked code)`. Module-level `_transport_override: Optional[httpx.AsyncBaseTransport] = None` is the injection seam tests set; production leaves it `None`.

- [ ] **Step 1: Failing tests.** Keygen happy path: the stored secret equals the mock's key; the response has no `key`/`password` fields. **Password hygiene (spec §4.2):** with `caplog.set_level(DEBUG)`, after keygen success **and** a keygen 403, the password string `"Pw-canary-7f3a"` appears in no `caplog.text`, no response body, and no byte of the sqlite DB file (open the `memory_db` file path, or dump every table with `SELECT *`). A 422 validation error on a malformed keygen body does not echo the password. A bad host on keygen → 400 and the mock recorded zero calls. Preflight dispatch returns `kind == "panos_ngfw"` and `PN_` codes (not `PF_`). Preflight with no integration → `PN_NO_INTEGRATION`. Sources PUT round-trips and does not reset `last_verified_ok`. **Review Focus 4:** two firewalls, `?integration=fw-b` hits fw-b's host, the default is the first by name, and an override set on fw-a does not appear in fw-b's report. `GET /api/ngfw` `write_enabled is False` even with `CORTEXSIM_PANOS_ALLOW_WRITE=true` in env (nothing consumes it). The XSIAM preflight tests still pass unchanged.

- [ ] **Step 2: FAIL.** **Step 3: Implement.** Agents come from `select(Agent)` → `AgentRef(agent_id, hostname, last_ip)`. **Step 4: PASS** plus `pytest tests/api/test_connectors_preflight_api.py -q` green. **Step 5: Commit** — `feat(api): expose NGFW registration, keygen and preflight endpoints`.

### Task 8: Console wizard

**Files:**
- Create: `ui/src/components/console/FirewallView.jsx`, `ui/src/components/console/firewall/ConnectStep.jsx`, `ui/src/components/console/firewall/CheckStep.jsx`, `ui/src/components/console/firewall/SourcesStep.jsx`, `ui/src/styles/destinations/firewall.css`
- Modify: `ui/src/api/client.js` (functions `listFirewalls()`, `putFirewall({name, host, verify_tls, vsys, forwarding_path, api_key})` via the existing integrations PUT with `kind: 'panos_ngfw'`, `firewallKeygen(body)`, `firewallPreflight(name, agentIds?)`, `putFirewallSource(name, agentId, body)`, `deleteFirewall(name)`), `ui/src/app/destinations.jsx` (lazy `FirewallView`; entry `{ id: 'firewall', label: 'Firewall', group: 'Manage', icon: 'shield', Component: FirewallSurface }` directly after `tenants`; use an icon that already exists in the icon set, checked by grep)
- Test: `ui/src/components/__tests__/FirewallView.test.jsx`; the existing `ui/src/app/__tests__/everyDestinationRenders.test.jsx` must pass with the new entry

**Interfaces:**
- Consumes: Task 7 endpoints.
- Copy (verbatim from the spec): the Check header shows `queries issued: {n}`; when `n === 0`, the text `this report was not answered by a firewall`; the ladder footer reads `Proves: the firewall is configured to write and forward these records. Does not prove: the tenant ingested them, or they stitched — tenant-verified: 0`. Keygen note: `Your password is used once to generate an API key and is not stored. Generating a key does not revoke this admin's other keys. Use a read-only API admin role.` TLS-off note: `Self-signed management certificates are common in labs. Every preflight will report this connection as unverified.`

- [ ] **Step 1: Failing vitest** (mock `api/client.js`): three step headings render. Submitting keygen calls `firewallKeygen` with the password, then the password input is cleared, and no rendered text contains the password. Preflight with `queries_issued: 0` shows the not-answered line. Blocked/degraded stages render their code + remediation and are never collapsed. A source row with `nat_verdict: 'nat_between'` and a `collision_with` renders as blocked with the other hostname. Provenance labels `inferred from last_ip` / `entered` / `unknown` render. Inline source edit calls `putFirewallSource`. Every control has an accessible name (`getByRole(..., {name})`).

- [ ] **Step 2: FAIL** — `cd ui && npx vitest run src/components/__tests__/FirewallView.test.jsx`. **Step 3: Implement** with Cortex tokens from `cortex-theme.css` and the `pov-page` layout classes TenantView uses. Status colours reuse the Readiness rung classes. **Step 4: PASS**, plus `npx vitest run src/app/__tests__/everyDestinationRenders.test.jsx` and `npm run build`. **Step 5: Commit** — `feat(ui): add Firewall wizard under Manage for NGFW connection`.

### Task 9: Docs, ledger, gates

**Files:**
- Modify: `docs/reference/api-and-agent-surface.md` (new §: `/api/ngfw`, the preflight kind, every `PN_` code with a one-line meaning), `CLAUDE.md` (an NGFW connection paragraph under the Measurement-loop area, stating read-only, no write path in v1, `tenant-verified` still 0; the route count updated to the measured number), `CHANGELOG.md` `[Unreleased]`, `.env.example` (the two new settings, commented, defaults)
- Ledger: per `.claude/skills/product-mechanics/SKILL.md`, the three NGF facts from spec §10 are recorded on branch `docs/docs-ngfw-lab-mechanics` **only if** that skill permits adding them from here. Otherwise write them to the shared notes folder as a hand-over for the ledger session, and say so in the report.

- [ ] **Step 1:** Measure routes: `PYTHONPATH=core .venv/bin/python -c "import main; print(len(main.app.routes))"`. Write the number into CLAUDE.md.
- [ ] **Step 2:** Gates (`.venv/bin` first on PATH): `PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q` (0 failed); `make test-backend-non-utc` if docker is available (else say so); `cd ui && npx vitest run` (0 failed, 0 errors) and `npm run build`; `make validate` (if `check-adapters` fails only on uninitialised Rust submodules, record that, as the KOI-009 pass did). `make -n ci` lists anything else; run what it lists that is runnable.
- [ ] **Step 3: Gate A evidence.** For the four guards (read-only allowlist, password hygiene, unknown-shape-raises, no-agents-is-degraded), temporarily revert the guarding line, run its test, observe FAIL, and restore. Record the command + failure line in `docs/superpowers/plans/2026-11-10-ngfw-connection-wizard.md` under a new "Gate A evidence" heading.
- [ ] **Step 4: Commit** — `docs(api): document NGFW connection surface, PN_ codes and gates`.

## Gate A evidence

(filled in by Task 9 Step 3)

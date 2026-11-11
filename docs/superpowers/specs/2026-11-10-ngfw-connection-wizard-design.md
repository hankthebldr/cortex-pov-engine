# NGFW connection wizard — design

**Date:** 2026-11-10 · **Branch:** `feature/api-ngfw-connection` (from `dev` 62c519e)
**Status:** draft v2 (builder check folded in; SLS + syslog scope decided by Henry), awaiting approval
**Inputs:** the PA-440 → deathstar stitch investigation of 2026-11-10 and the 90-scenario
NGFW audit (improvement notes §1–8), both in the side-session notes; the `NGF` mechanism
and `PM-NGF-01..03` on `docs/docs-ngfw-lab-mechanics` (`docs/product-mechanics/ledger.yml`).

## 1. Intent

CortexSim owns the Cortex tenant side of a POV: an encrypted credential, a staged
read-only preflight, honest rungs on Readiness. It owns nothing on the firewall side,
and the firewall is the sensor that fails most quietly. On 2026-11-10 an NGFW threat log
reached deathstar in ~12 s, but it did not stitch to an XDR issue. To get even that far
took an evening of hand-run `show`/`test` commands to find that:

* the lab hosts hit a block profile, so the test was cut off at the firewall;
* the DNS Security categories were `default` (sinkhole/drop), not alert;
* the VM sat behind libvirt NAT, so the firewall saw ms-01's IP, which is also an
  XDR endpoint, so Cortex would attribute the VM's sessions to the wrong host;
* decryption was off, so nothing inside HTTPS could ever produce a record;
* a ping to one address auto-tags a host into a no-inspection bypass rule.

None of that is a Cortex detection gap. Each item makes a missing NGFW record, which a
POV report reads as "Cortex missed it". **The wizard is how a DC finds all of it the day
before the call, in one pass, without touching the firewall.**

### Where it sits in the bigger picture (Henry, 2026-11-10)

The day before a POV the DC sets up recurring data-source sensors, each running its own
stream on a loop. Then they stage 3–5 key scenarios to run before and during the call,
chained together and mapped to the customer's requirements. The firewall is one of those
sensors. This spec covers **only the firewall connection and its preflight**. The sensor
loop scheduler and the scenario staging/mapping surface are separate projects, brainstormed
later. This design leaves room for both (§9).

## 2. Goals, non-goals, anti-goals

**Goals**

1. Register a PAN-OS NGFW: mgmt host, API key in the encrypted vault as a new
   integration kind, TLS-verify on/off for self-signed mgmt certs.
2. Optionally mint that API key from a username/password once. The password is never
   persisted and never logged.
3. A staged, read-only preflight. Every stage is reported even when an earlier one
   degrades. `queries_issued` is carried. The HTTP transport is injectable.
4. For each enrolled agent: the source the firewall sees, NAT between agent and firewall
   flagged, collisions with another agent's address flagged, and the rule + profile group
   that source actually hits, with alert vs block per profile.
5. A console destination under **Manage**, next to Tenant, that walks Connect → Check →
   Sources.

**Non-goals (v1)**

* Writing to the firewall. The write path is designed (§7) and **not shipped**: no code
  in v1 can issue `set`/`edit`/`delete`/`move`/`commit`.
* Panorama. A Panorama-managed device is checked directly on the device. Panorama-pushed
  rules show up in `test security-policy-match` like any other rule.
* Multi-vsys beyond selecting one vsys (default `vsys1`).
* Per-scenario "will the NGFW see this step" verdicts (§9.2, designed, built later).
* CSP registration / Software NGFW bootstrap (Henry's "from scratch" thread; separate).
* Anything that reads the tenant. Firewall preflight proves the firewall config, not
  ingestion and not stitching.

**Anti-goals (a failure even if it technically works)**

* **A green preflight that quotes an injected transport as firewall proof.** Every
  report says how many live calls answered it.
* **"No agents" or "no profiles" rendering as ok.** A zero is degraded.
* **A tolerant parser.** An XML shape the parser does not recognise raises; the stage
  reports `blocked` with `PN_SHAPE_UNRECOGNISED`. It never reads as "nothing configured".
* **Any write reachable from the API in v1**, including through `type=op` (`<request>`,
  `<set>`, `<clear>`, `<delete>`, `<debug>`, `<commit>` are all operational commands).
* **The password in any log line, DB row, SSE frame, exception message or URL.**
* **Reporting `tenant-verified` > 0** because a firewall check passed.

## 3. Constraints (from CLAUDE.md, CONTRIBUTING.md, Gate A5)

* Reads only, via the PAN-OS XML API (`type=op` with an allowlisted command set,
  `type=config&action=get`/`show`). The API key goes in the `X-PAN-KEY` header, never
  in a query string; requests are POSTed form-encoded so command text stays out of URLs
  and proxy logs.
* Structured JSON errors `{error, code, detail}` everywhere.
* Default-off flags for anything outbound beyond the explicit operator action. Preflight
  is an explicit action, so it is not flag-gated (same as XSIAM preflight).
* No DB schema migration in v1 (everything fits in `IntegrationCredential.config`).
* Tests never touch a real firewall. Fixtures are modelled on the PA-440's real response
  shapes (read 2026-11-10 with `show`/`test` only), with lab hosts/IPs generalised to
  RFC 5737 documentation addresses.

## 4. Credential and registration

### 4.1 Kind

New integration kind **`panos_ngfw`**, stored through the existing
`CredentialStore.put_integration` (secret = API key, `secret_type_hint =
"panos_api_key_credential"` so `_redact_backing` strips `preview_tail`).

`config` (validated at PUT time in `_validate_integration_config`, like the tenant URL):

| key | type | default | rule |
|---|---|---|---|
| `host` | str | required | IPv4/IPv6 literal or DNS name, optional `:port`. No scheme, path, query or userinfo. Rejected before any request, so the key is never sent to a malformed host. |
| `verify_tls` | bool | `true` | `false` is allowed (self-signed mgmt certs are the norm in labs) and is surfaced on every preflight as a `degraded` note on `dns_tls`, never silently. |
| `vsys` | str | `vsys1` | `^vsys\d+$` |
| `device_name` | str | `localhost.localdomain` | used in config xpaths |
| `probe_destinations` | list | see §5.3 | each `{ip, port, protocol: tcp\|udp, application}` |
| `inside_zone` / `outside_zone` | str | null | optional overrides for `test security-policy-match`; otherwise resolved (§5.3) |
| `observed_sources` | dict | `{}` | `{agent_id: ip}` operator overrides for the firewall-observed source (§5.4) |
| `agent_addresses` | dict | `{}` | `{agent_id: [ip,…]}` operator-entered agent-side addresses (§5.4) |

One module owns the host rule: `core/integrations/panos/host.py::normalize_mgmt_host`
(mirrors `integrations/xsiam/tenant_url.py`, with an AST guard asserting one definition).

### 4.2 Keygen

`POST /api/ngfw/keygen` `{name, host, verify_tls, username, password, vsys?}`:

1. Validate `host` (same function). Reject before sending anything.
2. POST `type=keygen` with `user`/`password` **in the form body** to the mgmt host.
3. Parse `<response status="success"><result><key>…</key></result></response>` strictly.
4. Store the key via `put_integration` (kind `panos_ngfw`), return the integration's
   metadata. The key is never returned.

Password hygiene, each one enforced by a test:
* the request model holds it as `pydantic.SecretStr`, so `repr`/validation errors show `**********`;
* the handler drops its reference after the single call; nothing writes it to the DB;
* the client never logs request bodies, and an error from keygen is re-raised with a
  fixed message (`PN_KEYGEN_REJECTED` / `PN_UNREACHABLE` / …), never the response or
  request text;
* a test asserts the password string appears in no log record (caplog at DEBUG), no DB
  row (full-table scan of the sqlite file) and no response body.

Generating a key does **not** invalidate the admin's earlier keys (PAN-OS 9.0+ keys carry
a timestamp and several stay valid at once). Keys die when the admin is deleted, the
password changes, the configured key lifetime expires, or someone runs "Expire All API
Keys". The wizard says that next to the keygen button, and recommends a dedicated
read-only admin role (XML API: operational requests + configuration read, nothing else).

### 4.3 Endpoints

| method | path | purpose |
|---|---|---|
| `GET` | `/api/ngfw` | registered firewalls (metadata only), plus `write_enabled: false` and `tenant_verified: 0` |
| `PUT` | `/api/credentials/integrations` | existing route, `kind: panos_ngfw` (API-key path) |
| `POST` | `/api/ngfw/keygen` | §4.2 |
| `POST` | `/api/connectors/panos_ngfw/preflight?integration=&agents=` | §5, through the existing route's kind dispatch |
| `PUT` | `/api/ngfw/{name}/sources/{agent_id}` | set/clear `observed_sources` / `agent_addresses` for one agent |

`panos_ngfw` is **not** a read-back connector (it pulls no alerts), so it is not listed by
`GET /api/connectors`' reconcile entries. The existing preflight route resolves the
integration and returns `PF_NO_INTEGRATION` / `PF_CREDENTIAL_UNDECRYPTABLE` **before** it
looks at kind, so `panos_ngfw` is branched at the **top** of `preflight_connector`. It
hands off to `core/integrations/panos/preflight.py`, which owns its own resolution and
emits `PN_` codes.

**Store additions** (`core/security/credentials.py`): `update_integration_config(name,
patch)` merges config keys without touching the secret or resetting
`last_verified_*`. `PUT /api/ngfw/{name}/sources/{agent_id}` and the stored preflight
summary use it. `put_integration` stays as is.

**Last preflight result** lives in the existing `last_verified_ok / last_verified_error /
last_verified_at` columns via `mark_integration_verified` (ok = `overall != blocked`;
error = the first blocked stage's code). There is no migration, and the §9.1 sensor loop
can read the same columns.

## 5. Preflight

### 5.1 Report shape

Reuses `connectors/preflight.py::Stage` and the status vocabulary (`ok | degraded |
blocked | skipped`, same `overall` rule). It does not reuse `PreflightReport` as is: its
key is `tenant` and its `proves` sentence says "not answered by a tenant". So a sibling
`FirewallPreflightReport` carries `firewall` (integration name), `kind: "panos_ngfw"`,
`mgmt_host`, `stages`, `sources: [...]` (§5.4), `queries_issued`, `config_source:
"running"`, `panos_version`, and its own `proves`: *"Firewall configuration as read by N
live call(s). It does not show that the tenant ingested or stitched anything. A report
with queries_issued=0 was not answered by a firewall."*

**Which config is read.** Running config (`action=show`), never candidate (`get` can
hold uncommitted edits, and a check of edits nobody committed would mislead). Lookups for
a referenced object go vsys → `/config/shared` → Panorama-pushed (`/config/panorama/…`
in the running tree) → predefined names (`default`, `strict`, …, classified from known
semantics). An object found nowhere → degraded `PN_OBJECT_NOT_FOUND` naming it. It never
raises and never reads as "no profile". A rule returned by `test security-policy-match`
that is not in the local rulebase is looked up in the Panorama pre/post rulebases the
same way.

**Versions.** Parsers are proven against PAN-OS 10.2.8 (the PA-440). The
`show logging-status` parser keys on its labelled lines (`Log Collector`, `Connection
Status`, per-type rows), not on column offsets. Anything else still raises →
`PN_SHAPE_UNRECOGNISED`, with `panos_version` in the report so the gap is attributable.
Adding an 11.x fixture set is a build task once a response is available. `queries_issued`
counts every XML API call actually sent. Codes are prefixed `PN_` and live in one module,
`core/integrations/panos/codes.py`.

Only `unreachable` and `auth rejected` short-circuit the remaining stages to
`skipped (PN_SKIPPED_UNREACHABLE / PN_SKIPPED_AUTH)`. Everything else runs. A parser
exception inside a stage marks that stage `blocked / PN_SHAPE_UNRECOGNISED` with the
command or xpath in `detail`, and the rest still run.

Each non-ok stage carries a remediation that names the **consequence in POV terms**
("threat logs from this firewall will not reach the tenant; every NGFW-dataset detection
on this run reads as missed"), not just the status.

### 5.2 Firewall-wide stages, in order

| # | id | reads | ok when | degraded / blocked when |
|---|---|---|---|---|
| 1 | `config` | — | host valid, key decrypts | blocked: `PN_CONFIG_INVALID`, `PN_NO_INTEGRATION`, `PN_CREDENTIAL_UNDECRYPTABLE` |
| 2 | `dns_tls` | (first call) | resolved + TLS verified | degraded: `PN_TLS_UNVERIFIED` when `verify_tls=false`; blocked: `PN_UNREACHABLE`, `PN_TLS_FAILED` |
| 3 | `auth` | `show system info` | 200 + `status="success"` | blocked: `PN_AUTH_REJECTED` (403 / `status="error"` code 403) |
| 4 | `system` | (same response) | model, sw-version, serial, multi-vsys, operational-mode reported | degraded: `PN_MULTI_VSYS` if multi-vsys on and `vsys` not set explicitly; `PN_NOT_NORMAL_MODE` |
| 5 | `clock` | `show clock` + `deviceconfig/system/timezone` | skew ≤ 30 s against SimCore UTC | degraded: `PN_CLOCK_SKEW`. `show system info` `time` has no zone, so it is not used for skew. |
| 6a | `forwarding_sls` | config `deviceconfig/setting/logging/logging-service-forwarding`; `show logging-status` | `enable yes`, a collector `Active`, traffic **and** threat last-forwarded within 15 min | blocked: `PN_COLLECTOR_INACTIVE`; degraded: `PN_LOG_TYPE_STALE` (per type, age shown), `PN_LOG_TYPE_NEVER`; `PN_LSF_DISABLED` (see path rule below) |
| 6b | `forwarding_syslog` | config syslog server profiles (`/config/shared/log-settings/syslog`, vsys `log-settings/syslog`) | ≥1 profile with ≥1 server: address, transport (UDP/TCP/SSL), port, format (BSD/IETF), facility reported | degraded: `PN_SYSLOG_DELIVERY_UNVERIFIED`, always when syslog is the path. The firewall keeps no per-destination forwarded/acked counters for syslog the way `show logging-status` does for SLS. Delivery to the Broker VM has to be confirmed on the tenant side, and the remediation says so. Also `PN_SYSLOG_UDP` (informational: loss is silent), `PN_SYSLOG_NO_SERVERS` |
| 7 | `eal_device` | config `deviceconfig/setting/logging/enhanced-application-logging` | `enable yes` | degraded: `PN_EAL_DEVICE_OFF` |
| 8 | `log_forwarding_profiles` | config `vsys/…/log-settings/profiles` | ≥1 profile forwards traffic, threat, url, wildfire **and** data, with `enhanced-application-logging yes` | degraded: `PN_LFP_MISSING_TYPE` (names profile + types), `PN_LFP_EAL_OFF`, `PN_LFP_TAG_ACTION` (profile has a tagging action, e.g. auto-tag on ping); blocked: `PN_NO_LFP` (zero profiles is degraded at best, never ok) |
| 9 | `decryption` | config `vsys/…/rulebase/decryption/rules` | ≥1 enabled rule | degraded: `PN_DECRYPTION_OFF`, with the consequence: HTTP body/URI/headers, TLS 1.3 cert and WildFire-over-HTTPS detections cannot fire |
| 10 | `registered_ip` | `show object registered-ip all` | reported (count + tags) | degraded: `PN_REGISTERED_TAGS_PRESENT` when any tag exists (it may move a host into another rule) |

**Forwarding path (Henry, 2026-11-10: SLS and syslog both in scope).** Config key
`forwarding_path` = `auto` (default) | `sls` | `syslog` | `both`. Both 6a and 6b always
run and report. The path decides only what is *required*:
* `auto`: the path is whichever is configured. Neither configured → blocked
  `PN_NO_FORWARDING_PATH` ("no firewall log can reach the tenant"). One configured → the
  other stage reports `skipped / PN_PATH_NOT_SELECTED`, not blocked.
* `sls` / `syslog` / `both`: the named path(s) must be healthy. A named path that is not
  configured is blocked (`PN_LSF_DISABLED` / `PN_SYSLOG_NO_SERVERS`).

Stage 8 checks each log-forwarding profile's match-list against the selected path:
`send-to-panorama` (which is the SLS target when logging-service-forwarding is on) and/or
`send-syslog` naming a profile from 6b. A match-list that forwards a type only on the path
not in use is `PN_LFP_WRONG_TARGET`.

`show logging-status` is free text inside XML. The parser accepts exactly the column
layout seen on PAN-OS 10.2 (header line, `Log Collector`, `Connection Status`, per-type
rows of `type created fwded seq seq total` or `Not Available`). Anything else raises.
**URL, WildFire and data logs are subtypes of the threat log type** and have no row of
their own there, so stage 6 checks traffic + threat. Stage 8 checks the five types in the
profile's match-list, because that is where they are configured. This is a product fact
for the `NGF` ledger entry (§10).

### 5.3 Per-source policy path

The stitch contract (Henry): endpoint and NGFW records stitch only when the 5-tuple
agrees. The source is as the agent recorded it, the destination as the firewall recorded
it. So the wizard checks the path **per enrolled agent**, not just per firewall.

For each agent the check takes:

* the **firewall-observed source** (§5.4);
* each `probe_destination`. The default set covers what the shipped scenarios mostly use:
  `1.1.1.1 udp/53 dns-base`, `198.51.100.10 tcp/80 web-browsing`, and
  `198.51.100.10 tcp/443 ssl`. These are policy lookups only: no packet is sent.
* zones: `inside_zone` / `outside_zone` if configured. Otherwise they are resolved with a
  FIB lookup for the destination (egress interface) and for the source (ingress
  interface), then the interface's zone from config. The FIB command depends on
  `advanced-routing` in `show system info`: `off` → `test routing fib-lookup
  virtual-router <vr>`, `on` → `test advanced-routing fib-lookup logical-router <lr>`.
  `<vr>`/`<lr>` come from config key `routing_instance` (default `default`).
  Unresolvable → `PN_ZONE_UNRESOLVED` (degraded) and the operator sets the override.

Stages per source:

| id | reads | reports |
|---|---|---|
| `source_seen` | `show session all filter source <src>` (count only) | whether the firewall currently has sessions from this source. 0 = degraded `PN_SOURCE_NOT_SEEN` (it may be the wrong address) |
| `policy_match` | `test security-policy-match from … to … source … destination … destination-port … protocol … application …` per probe | rule name + index. The response carries no profiles, so the rule is then read from config |
| `rule_profiles` | config `rulebase/security/rules/entry[@name=…]` → `profile-setting` (group or profiles), `log-setting`, `disabled`, `action` | resolves a group via `profile-group`; for each of virus/spyware/vulnerability/url/wildfire reports **alert / block / mixed / none** per severity. **DNS Security categories are reported separately from spyware signature rules** (`botnet-domains/dns-security-categories` + `lists`). Codes: `PN_RULE_DENIES`, `PN_NO_PROFILE` (allow with no inspection, the bypass-rule case), `PN_PROFILE_BLOCKS` (a test is cut at the firewall before the endpoint sees it), `PN_DNS_SEC_SINKHOLE`, `PN_RULE_NO_LOG_FORWARDING`, `PN_RULE_LOG_PROFILE_INCOMPLETE` (references a profile stage 8 flagged) |
| `nat` | `test nat-policy-match` same tuple | egress source NAT is reported as informational (`ok`, pre-NAT source is what gets logged) |
| `user_id` | `show user ip-user-mapping ip <src>`; zone config `enable-user-identification` | degraded `PN_NO_USER_MAPPING` / `PN_ZONE_USERID_OFF`, with the consequence: user-principal stitches cannot resolve on the firewall side (40 NGFW-dataset detections reference a user column) |
| `registered_tags` | `show object registered-ip ip <src>` | degraded `PN_SOURCE_TAGGED` with the tags (e.g. a bypass tag) |

Profile actions are classified into **five** buckets: `alert`, `allow`, `block`
(drop/reset-*/block-ip), `sinkhole`, and **`signature-default`** for `default`, which
defers to each signature's own action and is mostly reset/drop at critical/high.
`signature-default` is reported as its own value with a degraded
`PN_PROFILE_SIGNATURE_DEFAULT`: "likely blocks critical/high; set an explicit `alert`
on a lab-scoped profile to be sure". It is not folded into `block` or `alert`, because
the firewall cannot tell us which without the threat database.

`alert` vs `block` matters because a block still writes a threat log (the log is what
stitches), but it stops the artefact from reaching the endpoint. Then the XDR half never
fires and the case holds only the NGFW issue (2026-11-10). PM-NGF-02 asks whether the
action changes grouping. Until it is answered, `PN_PROFILE_BLOCKS` is degraded, not
blocked.

### 5.4 Firewall-observed source, NAT and collisions

What SimCore knows today: `Agent.last_ip`, the address SimCore saw the beacon connect
from. The beacon reports no interface addresses (checked `core/api/agents.py`,
`models.Agent`). So:

* **firewall-observed source** = `observed_sources[agent_id]` if the operator set one,
  otherwise `Agent.last_ip` *only if* SimCore and the firewall sit on the same side of
  any NAT. That is unknown in general (Docker Desktop port-forwarding can rewrite it). So
  an un-overridden value is labelled `inferred: last_ip` in the response and the UI, and
  `source_seen` is the check on it.
* **agent-side address** (what the XDR agent records) = `agent_addresses[agent_id]`
  (operator-entered), else **unknown**. Having the beacon report its own interface
  addresses needs a Go beacon change **and** a `RuntimeFacts` schema addition
  (`core/api/agents.py` drops unknown keys today). That is a follow-up `agent` branch, not
  this one.
* **NAT verdict**, per agent:
  * `tuple_intact`: the agent-side address set contains the firewall-observed source;
  * `nat_between`: both known and disjoint → **blocked** `PN_NAT_BETWEEN_AGENT_AND_FW`,
    with the consequence: no src-IP / 5-tuple / container_id stitch can form for this
    agent; the firewall will attribute its sessions to whichever host owns the observed
    address;
  * `unknown`: agent side unknown → degraded `PN_AGENT_ADDRESS_UNKNOWN`. This is never ok.
* **collision**: two enrolled agents resolve to the same firewall-observed source →
  blocked `PN_SOURCE_COLLISION`, naming both. This is exactly the pov-target-01 / ms-01
  case.

No enrolled agents (or none selected) → the `sources` section is a single degraded entry
`PN_NO_AGENTS`, not an empty list.

### 5.5 Read-only enforcement

`core/integrations/panos/client.py::PanosClient` exposes exactly `op(xml)`,
`config_get(xpath)`, `config_show(xpath)` and `keygen(user, password)`. `op` accepts only
commands whose root element is `show` or `test` and whose subtree is in an explicit
allowlist of the commands §5.2–5.3 use. Anything else raises `PanosWriteRefused` before
a byte is sent. A test feeds it `<request>`, `<set>`, `<clear>`, `<delete>`, `<debug>`,
`<commit>`, `<test><…unlisted…>` and a nested-injection attempt. An AST guard asserts no
module under `core/integrations/panos/` builds a `type=config` request with an `action`
other than `get`/`show`, and none sends `type=commit|user-id|import|export`.

Transport: `httpx.AsyncClient(transport=…)` (the route is `async`; up to 120 sync calls
would stall the event loop), injectable through the preflight function and
the API (the API uses the real transport; tests inject `httpx.MockTransport` with
fixture XML). Timeout 10 s per call, at most `2 + 9 + (agents × (3 + 2×probes))` calls,
capped by `CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT` (default 120; over the cap, the
remaining source stages report `skipped / PN_CALL_BUDGET`).

## 6. Console

New destination **Firewall** (`#/firewall`, id `firewall`, group **Manage**, after
Tenant). Three steps on one page, each usable on its own:

1. **Connect.** Name, mgmt host, `Verify TLS` (on by default; turning it off shows the
   reason it is allowed and that the preflight will say so), then either
   *Paste API key* or *Generate key from admin login* (username + password fields, a note
   that the password is used once and not stored, and a pointer to a read-only API
   admin role). Lists already-registered firewalls with last preflight
   result.
2. **Check.** Runs the preflight and renders the firewall-wide stages as a ladder: status,
   code, detail, remediation, never collapsed. The header states `queries issued: N`
   verbatim and, when N = 0, "this report was not answered by a firewall". The ladder
   says what it proves: *the firewall is configured to write and forward these records*.
   It also says what it does not: *the tenant ingested them, or they stitched* —
   `tenant-verified: 0`.
3. **Sources.** One row per enrolled agent: hostname, agent-side address(es) (with
   provenance: reported / entered / unknown), firewall-observed source (with provenance:
   entered / inferred from last_ip), `source_seen`, NAT verdict, collision, and per probe
   the matched rule → profile group → alert/block per profile, DNS Security separately.
   Inline edit for the two address fields (`PUT /api/ngfw/{name}/sources/{agent_id}`).

Readiness (`#/readiness`) gains no new rung in v1. Its connector ladder is about the
tenant. A follow-up can add an NGFW sensor row once the sensor-loop work defines what a
"sensor" is (§9.1).

Design tokens, a11y and SSE rules: as for every other console view (Cortex tokens,
keyboard reachable, labelled controls). There is no SSE; preflight is request/response.

## 7. Write path — designed, not shipped

Goal for a later branch: generate the firewall config a scenario set needs (tonight's
Deltas 1–5: address objects/group, a lab-scoped alert-all rule above the first outbound
rule the source hits, a cloned spyware profile with DNS Security categories set to alert,
a profile group, log-forwarding without tagging actions) and push it to the **candidate**
config.

Gates, all required, mirroring `CORTEXSIM_XSIAM_ALLOW_WRITE`:

1. `CORTEXSIM_PANOS_ALLOW_WRITE` (default **false**), added to `core/config.py` in v1
   but consumed by nothing in v1;
2. per-request `consent.write_authorized: true` plus the integration name echoed back;
3. **dry run by default**: the response is the exact xpath + element XML and a
   rollback for each delta, as in the 2026-11-10 report;
4. candidate only. `commit` is a separate action behind `consent.commit_authorized` and
   a pre-commit check that the change journal contains exactly the deltas generated
   (`show config list change-summary` / pending-changes);
5. rollback = disable the generated rule; nothing is ever deleted.

v1 ships none of the write code, so the `PanosWriteRefused` guard stays absolute. When
the write branch lands, it adds a second client class rather than loosening this one.

## 8. Tests (Gate A: each guard observed failing without its fix)

* **Fixtures**: XML for each command/xpath in §5, modelled on the PA-440's real shapes
  (PAN-OS 10.2.8), with documentation addresses (192.0.2.0/24 inside, 198.51.100.0/24
  outside) and a fake hostname. Variants for every non-ok code.
* **Parsers**: one test per parser that an unrecognised shape raises (and that the stage
  then reports `PN_SHAPE_UNRECOGNISED` while later stages still run).
* **Read-only guard**: §5.5 rejection list; AST guard.
* **Password hygiene**: §4.2 caplog / DB scan / response scan.
* **Host validation** at PUT and keygen: scheme, path, userinfo, empty, and a test that
  no request is sent on rejection (mock transport records zero calls).
* **Honesty**: `queries_issued` equals calls the mock transport actually saw; no-agent
  case is degraded; zero profiles is not ok; collision and NAT verdicts; `inferred`
  provenance labelled.
* **API**: keygen, preflight dispatch, sources PUT, `GET /api/ngfw`, structured errors.
* **UI (vitest)**: three steps render; keygen form never echoes the password; ladder shows
  `queries issued`; NAT/collision rows render as blocked; destination registered under
  Manage (`everyDestinationRenders`).
* `make ci` gates incl. `make validate`; route count in CLAUDE.md updated.

## 9. Room for what comes next (designed, not built)

### 9.1 Sensor loop (Henry's "recurring data source sensors")

A sensor is a named, recurring signal source with a health check. The firewall preflight
is that health check for the NGFW sensor. The scheduler (separate brainstorm) can call
`run_panos_preflight(...)` on a cadence and store the last report. The function is pure
and transport-injectable for that reason.

### 9.2 Per-scenario NGFW expectation

For the DC's 3–5 staged scenarios: join each step that carries an NGFW-dataset
detection to the agent it runs on, its destination (from the step / EAL params) and the
preflight's per-source result → "the NGFW will log this / will block before the endpoint
/ will not see this (decryption off, NAT, loopback/.invalid destination)". It needs the
step-destination extraction the audit did by hand (`extract.py`); that is its own branch.

## 10. Product-mechanics ledger

New product facts learned while building go into `docs/product-mechanics/ledger.yml`
under `NGF`, per `.claude/skills/product-mechanics/SKILL.md`, on the ledger's branch.
Candidates already seen: URL/WildFire/data logs have no own row in `show logging-status`
(threat subtypes); `test security-policy-match` returns no profile setting;
`show system info` time carries no zone.

## 11. Calls I made (Henry to check)

1. Kind name `panos_ngfw`; destination label **Firewall** under Manage.
2. v1 is firewall-wide + per-agent checks; per-scenario readout deferred (§9.2).
3. Agent-side address comes from operator entry until the beacon reports interface
   addresses (a Go `agent` branch, not this one); unknown → degraded, never ok.
4. Default probe destinations (§5.3) and the 15-minute staleness window for
   last-forwarded logs.
5. `PN_PROFILE_BLOCKS` and `PN_DNS_SEC_SINKHOLE` are degraded, not blocked, pending PM-NGF-02.
6. No write code at all in v1; only the default-off flag is added.
7. No new Readiness rung in v1.
8. Running config, not candidate. Lookup order vsys → shared → Panorama-pushed →
   predefined. An object found nowhere is degraded, never "no profile".
9. A fifth action bucket, `signature-default`, reported on its own as degraded.
10. Parsers proven on PAN-OS 10.2 only. Other versions get `PN_SHAPE_UNRECOGNISED` with
    the version named, until a fixture for them exists.
11. Last preflight result is stored in the existing `last_verified_*` columns; a new
    config-only store method; async httpx; `panos_ngfw` branched before the generic
    route logic.
12. Syslog delivery is always reported "unverified from the firewall side" (degraded),
    because PAN-OS keeps no forwarded/acked counters for it.

## 12. Build-time amendments

The build (branch `feature/api-ngfw-connection`, 2026-11-10) changed the behaviour above
in these places. Each was a controller ruling during the build; the code is the
reference, this list says where it departs from §4–§6.

1. **`auto` path, unused path → `ok / PN_PATH_NOT_SELECTED`** (amends §5.2, which said
   `skipped`). With `forwarding_path=auto` and one path configured, the other stage, once
   confirmed unconfigured, reports `ok` with code `PN_PATH_NOT_SELECTED`. A `skipped`
   there made a healthy single-path firewall unable ever to read ready. The same holds
   for an explicit `forwarding_path=sls|syslog` when the other path *is* configured: that
   side reports `ok / PN_PATH_NOT_SELECTED` with `extra.would_report {status, code}` (what
   it would have read if selected), never `skipped` (final review M1).
2. **HTTP non-2xx is stage-local.** A management-API reply with a non-2xx status (not a
   connect, DNS or TLS failure) maps to `PN_UPSTREAM_ERROR` and blocks only the stage
   that made the call. Only `PN_UNREACHABLE`, `PN_TLS_FAILED` and `PN_AUTH_REJECTED`
   short-circuit the rest (§5.1 named two of the three; TLS failure joins them). The call
   budget bounds the extra calls against a sick firewall.
3. **Multi-vsys is always degraded.** Multi-vsys on → `PN_MULTI_VSYS` degraded every
   time, with the detail naming the vsys checked and saying the others were not. The
   config always carries a defaulted `vsys`, so §5.2's "not set explicitly" cannot be
   observed.
4. **Config reads are allowlisted too, and re-serialised.** §5.5 allowlisted `op` only.
   `config_show` now accepts only xpaths that fully match one of the anchored regexes in
   `client.py::CONFIG_XPATH_ALLOWLIST` (exactly the preflight's reads), and every `op`
   command is parsed, checked against a per-command argument schema and sent as the
   client's own canonical serialisation, never the caller's string. This closes both
   config exfiltration through an arbitrary xpath and a parser differential between the
   check and the firewall. There is no `config_get` (§5.5 listed one): running config
   only (§5.1) made it dead.
5. **DNS Security `allow` with a log level counts as alert.** A DNS Security category
   with action `allow` and a `log-level` other than `none` still writes a threat log
   (lab 2026-11-10 and PANW docs), so it reads ok, not `PN_NO_PROFILE`. A spyware
   profile that configures no DNS Security category is degraded `PN_DNS_SEC_UNCONFIGURED`.
6. **`log-end no` is degraded `PN_RULE_NO_LOG_END`** (extends the §5.3 field list). No
   session-end traffic log means no record to stitch.
7. **URL filtering and WildFire analysis are not evaluated in v1.** Their profile actions
   are not classified (no parser); `rule_profiles` reports them as "not evaluated", no
   issue is raised, and the console's proves text lists them as not covered. Degrading
   them would have made ready unreachable. Cost: a URL-filtering block that cuts a test
   is not flagged.
8. **Re-registration keeps per-agent overrides only on the same host.** Both
   `POST /api/ngfw/keygen` and `PUT /api/credentials/integrations` (kind `panos_ngfw`)
   carry `observed_sources` / `agent_addresses` over when the normalised `host` is
   unchanged, and clear them (response `overrides_cleared: true`) when it changes. Probes
   and zones are kept either way. A key rotation no longer wipes the overrides; a renamed
   box no longer inherits another firewall's path. The PUT also keeps `device_name`,
   `routing_instance`, `vsys` and `forwarding_path` when the caller does not send them
   (a sent key wins), and a `panos_ngfw` PUT onto a name held by another kind is 409
   `INTEGRATION_KIND_CONFLICT`, as keygen already was (final review M3).
9. **The keygen body is parsed in the handler.** FastAPI's default 422 echoed the
   password in the error `input` even with `hide_input_in_errors=True` on the model, so
   `POST /api/ngfw/keygen` reads the raw body (capped at 16 KiB, 413 above that), parses
   JSON itself and returns a 422 naming only field locations and error types.
   `PUT /api/ngfw/{name}/sources/{agent_id}` does the same (an echoed lone surrogate was
   a 500). A non-UTF-8-encodable string is a 422.
10. **Other additions.** `PN_LFP_FILTERED` (a log-forwarding match-list with a filter
    other than "All Logs" does not count as full forwarding), `PN_INTERNAL_ERROR` (a
    CortexSim defect in one stage blocks that stage only, exception type named, message
    withheld), `PN_SOURCE_UNKNOWN`, `PN_NO_RULE_MATCH`, `PN_RULE_CONFIG_MISMATCH`,
    `PN_PROBE_FAMILY_MISMATCH`, `PN_CLOCK_TZ_UNKNOWN`, `PN_LOG_TYPE_AGE_UNKNOWN`,
    `PN_SKIPPED_CONFIG`. Scoped spyware/vulnerability rules (a specific threat name or
    category) are reported in `scoped_rules`, never folded into the per-severity verdict.
    Panorama-pushed syslog profiles are read as well as vsys and shared. Keygen against an
    existing integration of another kind is 409 `INTEGRATION_KIND_CONFLICT`. A keygen with
    `verify_tls=false` returns `warnings: ["password sent over unverified TLS"]`.

11. **Final-review additions.** `PN_SHAPE_UNRECOGNISED` also covers a well-formed PAN-OS
    `status="error"` reply other than an auth rejection (403 stays `PN_AUTH_REJECTED`;
    at keygen every `status="error"` is `PN_KEYGEN_REJECTED`). `PN_AGENT_UNKNOWN`: each
    `?agents=` id on the preflight that matches no enrolled agent is its own degraded
    `sources[]` entry naming it (`requested_agent_id`), instead of vanishing into
    `PN_NO_AGENTS`; known ids in the same request are checked as usual.

**Threat notes.** `POST /api/ngfw/keygen` and `POST /api/connectors/panos_ngfw/preflight`
are unauthenticated, like the rest of `/api/credentials`: SimCore has no API auth and is
meant to sit on a DC-controlled network. So anyone who can reach SimCore can use keygen as
a reachability and port oracle toward internal hosts: a `host` of their choosing answers
with one of four distinct 502 codes (`PN_UNREACHABLE`, `PN_TLS_FAILED`,
`PN_UPSTREAM_ERROR`, `PN_SHAPE_UNRECOGNISED`) or a 401 (`PN_KEYGEN_REJECTED`). No stored
secret leaks through it: keygen sends only the credentials in the request and has no
stored key to send, and preflight sends a stored key only to that integration's own
validated host. The fix belongs with SimCore API authentication, not in this surface.

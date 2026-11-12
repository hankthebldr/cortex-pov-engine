# Ingestion test path: Broker VM applets, XDR Collector, HTTP log collector

Date: 2026-11-11 · Branch: `docs/docs-ingestion-test-path` (cut from `origin/dev`
b1ee28fa) · Status: design only, nothing built; revised after the builder check
with Henry's decisions of 2026-11-11, ~21:00 CST. `tenant-verified` is 0.

Every choice Henry did not make is marked **Claude's call:**, so he can veto it.
Repo facts were re-read on `origin/dev` b1ee28fa. Vendor facts were re-fetched
from the cited Palo Alto Networks pages on 2026-11-11 (§14). Lab facts (IPs,
host roles) come from session notes and are re-checked at bring-up.

## Executive summary

Before a customer watches a POV, the consultant needs to know that each route
by which logs reach the customer's Cortex tenant actually works: the
on-premises Broker VM with its collectors, the XDR Collector on a server, and
the cloud HTTP log collector. Today CortexSim can test only the last one, and
only by confirming that the tenant accepted a test record. It never confirms
the record arrived, so the test can report success with nothing ingested. Two
setup steps are checkboxes that nothing verifies. The risk is a broken data
path that surfaces mid-POV as "Cortex missed it": a false claim about the
customer's security coverage, made in front of the customer. This design gives
each route the same two-part test. On the lab tenant only, CortexSim sends one
uniquely tagged record through the route, and a deliberately misdirected one
that must not arrive. A read-only search then proves that the first landed and
the second did not; only that pair reads "verified". Customer tenants get a
read-only check that traffic is arriving on each route, shown as "traffic
seen" and never as verified. They keep today's HTTP collector test record,
which proves only that the collector accepted it, never that it was ingested.
A POV that uses no Broker VM is not marked degraded for lacking one. A passing
test is labelled as proof the route delivers data, never as proof that a
detection works. Build the HTTP collector test first, because it needs no new
lab equipment. Build the Broker VM syslog test once the Broker VM is deployed.
The XDR Collector and the file and database collector tests also wait until
the lab tenant is confirmed to be licensed for them. The Windows collector
test waits for a Windows host.

## Technical detail

### 1. Intent

Henry, 2026-11-11 19:20 CST: *"We need a path to doing some additional testing
both for the BVM syslog and other applets, the collector and the HTTP event
collector"*.

This continues his 2026-11-10 ask, quoted in the collector-targets spec: *"we
need to also ensure that the different tenant components are correctly
configured/correctly e.g. BVM targets, or HTTP event Collector Targets -- we
want this to be configurable per session and persistant across the test"*.

**Why it matters.** A door that is assumed open but is closed turns an
infrastructure gap into "Cortex missed it". The ingestion-doors spec (§1)
measured the scale:

- Broker VM syslog: 12 detections in 6 scenarios.
- Windows events: 31 detections in 10 scenarios.
- The collector route: 199 detections, judged by the HTTP collector target
  (spec §1, second table).

"HTTP event collector" in Henry's words is the vendor's **HTTP log collector**
(the custom HTTP collector at `/logs/v1/event`).

### 2. What exists today (verified on `origin/dev` b1ee28fa)

| Piece | Where | What it does, and the gap |
|---|---|---|
| Collector targets (#180) | `core/eal_simulator/targets.py`, `core/engine/collector_targets.py`, `core/api/collector_targets.py` | Instance-level targets: `GET/PUT/DELETE /api/collector-targets/{name}`, `POST /{name}/preflight`. `KINDS = ("xsiam_http_collector", "http_forwarder")`. The model's `url` is NOT NULL, and `TargetSpec` accepts only `http(s)` URLs. Broker VM targets were on hold until Henry named the applets (spec §3, §10). He has now named them (§5). |
| Target preflight | `core/eal_simulator/target_preflight.py` | The ladder runs configured, then reachable (DNS + TCP + TLS), then accepted (an opt-in `write_canary` POST). `accepted` means the tenant answered 2xx; the module itself says `tenant_verified` is 0. |
| The canary | `core/eal_simulator/collector.py::canary_record` | It has **no nonce and no send time**. `cortexsim_campaign_id` is the constant `collector-target:<name>`, so a read-back cannot tell this canary from an earlier one. |
| Ingestion doors phase 1 (#191) | `core/engine/ingestion_doors.py`, `door_resolution.py`, `door_state.py`, `core/api/doors.py`, `DoorEvent` in `core/models.py` | 27 doors in three scopes (tenant, agent, target), and a timeline where `unknown` never changes state. The routes are read-only: `GET /api/doors`, `/doors/readiness`, `/scenarios/{id}/doors`. There is no check, probe or attest route. There is no door for a Broker VM transport or for the XDR Collector: `git grep -i "xdr_collector\|xdr collector"` over `core spec` returns nothing. |
| Phase-2 plan | `docs/superpowers/plans/2026-11-11-ingestion-doors.md`, tasks 7–14 | Not built. Task 7's `ROWS_QUERY` asks for **any** row in the last 3,600 s, not for a specific one. |
| `configured_facts` | `core/engine/door_state.py` | It marks **every** target `configured` for every target-scoped door. That is harmless while `http_collector` is the only target door, and wrong the moment a second one exists (§9, Task 7). |
| Ingestion routes (#183) | `core/eal_simulator/ingestion_routes.py` | `PIPES` include `broker_syslog` and `broker_wec`. There is no XDR Collector pipe. |
| NGFW wizard (#186) | `core/integrations/panos/preflight.py::_gather_syslog` | Stage `forwarding_syslog` always raises `PN_SYSLOG_DELIVERY_UNVERIFIED`. Its remediation says delivery to the Broker VM must be confirmed on the tenant side. |
| Get Started | `ui/src/components/console/setupProgress.js`, `GetStartedView.jsx:258-259` | The `collector` and `bvm` steps are `localStorage` confirmations (`cortexsim.setup.confirmed.v1`) that no other code reads. The Broker VM copy tells the DC to open UDP/TCP 514 because "network-plane scenarios send their traffic there". No scenario sends syslog. |
| Agent-run collector steps | scenario YAML | The collector-targets spec counts 89 agent-run steps reading an unset `$COLLECTOR_URL` (§2, §10). A walk of `steps[].command` on b1ee28fa finds 84 that name it. The fix is a beacon step-env branch that has not been built. |
| `email_emitter` auth | `core/eal_simulator/plugins/email_emitter.py` | On dev it has no `auth_token`/`auth_header`/`auth_scheme` fields, so its POSTs carry no `Authorization` header. The fix is local branch `fix/api-email-emitter-collector-auth` (ddc70e89, rebased on b1ee28fa). No PR existed when this was written; a parallel session is opening it. |
| Senders | `core/eal_simulator/**` | Every collector emitter is an `httpx` POST. Nothing in-tree sends syslog. |
| Read-only tenant ops | `core/integrations/xsiam/operations/packs/{xql,dataset-management,audit-log}.yml` | `OP-XQL-START-XQL-QUERY`, `-GET-QUERY-RESULTS`, `-GET-QUOTA`, `OP-XQL-GET-DATASETS` and `OP-AUDITS-MANAGEMENT-LOGS`, all `access_class: read`. There is one unwrapper, `integrations/xsiam/queries.py::result_rows`, and it raises on an unrecognised envelope. A rows runner exists: `engine/verifier.py::xsiam_rows_runner`. |

Lab, from session notes:
- **pov-target-01** is Ubuntu 24.04.5 on x86_64, a libvirt guest on ms-01 at
  192.168.122.77, NATed to the LAN through ms-01 (192.168.1.110).
  - It runs XDR agent 9.4.0.470 (CONNECTED) and beacon `pov-target-01-4dcdce`.
  - `docs/lab/2026-11-11-persistence-grouping-lab-run.md` confirms the OS and
    the agent version.
- **SimCore** runs on the Mac. Its LAN address has been 192.168.1.105 on some
  days and 192.168.1.106 on others, so it is not stable.
- **deathstar** is an XSIAM tenant, granted `xql`.

### 3. Goals, non-goals, anti-goals

**Goals**

1. Every door Henry named gets the same tested ladder: what sends, from which
   host, with what payload, which dataset the record lands in, the read-only
   query that proves it, a negative control, failure codes with remediation,
   and a read-only freshness rung that any tenant can run (§6.9).
2. **Probe** (write one nonce-tagged record) and **check** (read-only XQL) are
   separate operator actions. Probes run only on the lab tenant, and so do
   the negative controls, which SimCore sends (§6.4).
3. A probe answers one question: did *this* record, sent from *this* host,
   land? A check that cannot return a miss does not ship.
4. These doors become collector targets, entered once per POV (Henry,
   2026-11-10). Existing HTTP targets keep working unchanged.
5. A runbook brings each door up in the lab and records its first live result.

**Non-goals**

- Vendor-shaped syslog streamers (LogSimv2 phase 2). They come later and run
  on top of these doors.
- Scoring changes beyond those already planned in phase-2 tasks 9–10.
  Transport doors never exclude or credit a detection (§6.7), and the XDR
  Collector run label is a label only (§7.7).
- A probe watcher that writes on a cadence. **Claude's call:** not built;
  listed as a follow-up.
- NetFlow, Kafka, Network Mapper and the other applets Henry did not name.
- A PA-440 → Broker VM syslog leg as a real-device witness. **Claude's call:**
  out of scope; see open question 1.
- The beacon step-env branch. The 84 or 89 agent-run steps stay route-unknown
  until it lands.

**Anti-goals.** Each of these makes the work a failure even if it runs:

- A verified transport door counted as detection coverage, merged into
  `tenant_verified`, or shown next to coverage numbers without the transport
  label.
- A probe written into a customer tenant.
- A probe sent from SimCore on the Mac presented as proof of the lab host's
  path, or of a jumpbox's path.
- A read-back that goes green on any earlier row, which is what today's
  nonce-less canary would do.
- An XDR Collector failure on pov-target-01 that reads as a closed door or as
  missed detections when the cause is the unsupported host.
- The WEC door reading `closed`, or dropping off the list, because no Windows
  host exists. It must read "needs a Windows source".
- A door that was never probed reading `closed`. It reads `unknown`.
- UDP syslog reported as `reachable`.
- A probe row landing in a vendor or customer dataset instead of a
  `cortexsim_*` one.
- A `traffic seen` reading labelled `verified`, or counted in
  `doors_verified_by_probe`.
- An HTTP canary's 2xx read as ingested off the lab tenant.
- A negative control's expected miss closing a door, or a door reading
  `verified` with no recorded control miss.
- A probe sent from an endpoint while a scenario run is live there.
- A POV marked degraded only because it uses no Broker VM or XDR Collector.

### 4. Constraints

- **Read-only to the tenant API.** `CORTEXSIM_XSIAM_ALLOW_WRITE` and
  `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off and untouched.
  - Probes and negative controls are ingestion writes *through the door*:
    syslog to the Broker VM, a file on a staged share, a row in a staged
    database, a POST to the collector. They are never API writes.
  - They are the same class as today's opt-in `write_canary`. Probes and
    controls are narrowed to the lab tenant (§5, decision 4); the canary
    itself stays on every tenant (§5, decision 7).
- **Checks** use the `xsiam_tenant` credential with the `xql` capability,
  through `resolve_tenant_runner` and the per-tenant quota ledger.
  - At most two tenant calls per check, and each check reports
    `queries_issued`.
  - Quota exhaustion, missing XQL, a 401 or a 5xx records `unknown` (plan,
    Global Constraints).
- **Timestamps** are naive UTC (`door_state._require_naive`). Every new time
  computation runs under `make test-backend-non-utc`.
- **One SimCore per POV.** Targets and door events live in the instance
  database.
- **Syslog Collector port rules** (vendor doc):
  - A port must not be in 0–1024 (514 is the exception) or 63000–65000, and
    must not be 4052, 4369, 5671, 5672, 5986, 6379, 8000, 8888, 9100, 15672 or
    28672.
  - Up to 100 ports.
  - **Port and Protocol cannot be changed once a row is added.** A wrong row is
    removed and re-added.
  - Over UDP, messages are truncated at 4 KB. Over TCP with LF framing, a
    message over 64 KB is dropped and the connection closed.
- **No secrets in the repo.**
  - Lab test credentials live in the SimCore vault, or in 1Password for console
    logins: the Broker VM console password, the staged FTP/SMB/DB users, and
    the HTTP collector key.
  - The runbook names env vars and 1Password references only.
  - Target URLs never carry userinfo.
- **Henry's "functional fixes, no extra guards" rule (2026-11-11).** Validation
  is limited to what a send or a parse needs. The "unrecognised shape raises"
  rule applies to tenant replies the check depends on, not to every new field.
- **The beacon stays Go stdlib only.** Syslog, file and DB probes are shell
  steps using host tools (`logger`, `printf`, `psql`); the beacon needs no new
  code for them.

### 5. What Henry decided

1. **Applets in scope:** the Syslog Collector, the Windows Event Collector,
   and the File/DB collectors (Files and Folders, FTP, CSV, Database).
   Decided 2026-11-11, ~20:15 CST.
2. **The Broker VM is not deployed yet.** The design comes first. The source
   IP the Syslog Collector will see from pov-target-01 is TBD, so it is a
   **deploy-time input**, never an assumption. §7.1 gives the calibration.
3. **The XDR Collector test runs on pov-target-01** (Ubuntu 24.04, already
   running XDR agent 9.4). Henry chose this over the recon's warning that
   24.04 is not on the documented support list and that co-installing with the
   agent is undocumented. This design honours his choice and adds an explicit
   validity check, so that a failure caused by the unsupported host reads as
   "host unsupported" (§7.7).
4. **Door probes run on the deathstar lab tenant only.** Customer tenants get
   read-only checks.
5. **There is no Windows host in the lab.** The WEC leg is designed only, and
   it reads as needing a Windows source until one exists.
6. **A read-only freshness rung on every transport door.** Henry decided
   (2026-11-11, ~21:00 CST): customer tenants get a read-only freshness rung
   for the transport doors (the Broker VM applets and the XDR Collector). It
   reads recent rows in the door's dataset plus the door's own diagnostic
   source: `collection_auditing` errors for the Broker VM, and
   `metrics_source`/`metrics_view` freshness for collectors. It is shown as
   "traffic seen" and never as "verified"; only a deathstar probe plus check
   can read "verified". Readiness must not go degraded merely because a POV
   has no Broker VM (§6.9).
7. **The HTTP collector canary stays on every tenant.** Henry decided
   (2026-11-11, ~21:00 CST): the existing HTTP collector preflight canary
   (#180, and the campaign preflight in `core/api/eal.py`) stays on every
   tenant as a delivery check (a 2xx), and gains a nonce and `sent_at`. Only on
   deathstar does the read-back run that can mark `http_collector` verified.
   Elsewhere the canary proves "accepted", never "ingested" (§7.8). This
   answers the first draft's open question 1.

This also builds on decisions already in force:
- From the ingestion-doors spec §6 (Henry, 2026-11-11): a closed door excludes
  detections rather than missing them; a run with exclusions can FAIL but
  never PASS; and there is no expiry timer.
- One SimCore per POV, and the SimCore instance is the session (2026-11-10).

### 6. The model

#### 6.1 Probe and check

```
 operator            SimCore                         sender host            door            tenant
    | Probe ----------> gate: lab tenant? run live?   |                     |                 |
    |  (or control)     validity facts                |                     |                 |
    |                   DoorProbe row (nonce,         |                     |                 |
    |                   scan nonce, sent_at) -------> | send 1 record ----> | --------------> | lands in
    |                   <---- sender result (rc, connect, sent_at_host)     |                 | cortexsim_*
    | Check ----------> Q1: rows since sent_at - 600 s in the door's dataset ----------------> |
    |                   scan for nonce, scan nonce, pending control nonces                     |
    |                   Q2 (one call, optional): first hit -> scan control (HTTP only),        |
    |                       miss after grace -> the door's diagnostic source ----------------> |
    |                   DoorEvent (state, basis, rung, evidence, queries_issued)               |
```

- **Probe:** `POST /api/doors/{key}/probe`.
  - It writes one record through the door, records a `DoorProbe` row and
    returns. It never reads the tenant.
  - It is refused with 403 `DOOR_PROBE_NOT_LAB_TENANT` unless the validated API
    host of the instance's `xsiam_tenant` credential appears in
    `CORTEXSIM_DOOR_PROBE_TENANTS` (comma-separated, empty by default).
  - **Claude's call:** a single gate, a host pin. A customer tenant's host can
    never match deathstar's, even if the lab `.env` is copied into a POV image.
  - For HTTP targets, the target URL's host must also equal a pinned host. The
    doc gives the URL as `https://api-{tenant external URL}/logs/v1/event`.
    Whether that host equals the API host is confirmed on deathstar's first
    run; if they differ, the pin lists both.
  - It is refused with 409 `DOOR_PROBE_RUN_LIVE` while a scenario run is live
    on the beacon that would send it. **Claude's call (builder check):** a
    probe's step runs on the measured endpoint, so an alert it raised could
    otherwise be credited to the run.
  - A body of `{"control": "<control id>"}` sends one of the door's negative
    controls instead of a probe (§6.4). Controls pass the same gates.
- **Check:** `POST /api/doors/{key}/check` (the route from plan Task 7). It is
  read-only and runs on any tenant.
  - It uses the **nonce method** when the door has a probe. Only this method
    can read `verified (probe)`.
  - It uses the **recent-rows method** (Task 7's `ROWS_QUERY`) for data doors
    with no probe.
  - It uses the **freshness method** (§6.9) for transport doors with no
    probe. On a customer tenant this is the highest rung a transport door can
    reach.
  - The evidence names the method used, and the console labels the results
    differently: `verified (probe)`, `verified (recent rows)` and `traffic
    seen`. `http_collector`'s recent-rows reading is the exception and reads
    `traffic seen (recent rows)` (§7.8).
- **No polling inside a check.** Ingestion latency is undocumented; the only
  published figure is the bucketed `data_freshness_*` in `metrics_source`. The
  console shows how much grace remains, plus a "Check again" button.
  **Claude's call.**

#### 6.2 The nonce and the probe record

- **Nonce:** `cxsdp-` followed by 32 lowercase hex characters (a uuid4).
- **Scan-control nonce:** a second value of the same shape, generated with the
  probe, **never sent**, and stored on the same row. It tests the scan, not the
  door: it must never be found (§6.4). **Claude's call.**
- **Common record fields.** Each door maps them onto its own format (a CEF
  extension, a JSON line, a CSV column, a table column):
  - `cortexsim_door_probe: true`
  - `cortexsim_door_nonce`
  - `door_key`
  - `sent_at` (SimCore time, ISO-8601 UTC)
  - `sent_from` (the beacon or agent id, or `simcore`)
  - `message: "CortexSim door probe - safe to discard"`
- **Control records** (§6.4) carry the same fields with their own nonce, plus
  `cortexsim_door_control: true` and the control id.
- **The `DoorProbe` row.** The builder owns the schema. It holds no secrets.
  Probes and sent controls each get one. Fields:
  - `id`
  - `nonce` (unique) and `control_nonce` (the scan-control nonce)
  - `control` (null for a probe, otherwise the control id), `expected`
    (`absent` for every control) and `control_result` (`absent`, `landed`, or
    null while inside grace)
  - `door_key` and `target`
  - `sender_kind` (`simcore` or `beacon`) and `sender_id`
  - `sent_at` (naive UTC) and `sent_at_sender` (the sender's own clock;
    nullable)
  - `send_status` (`queued`, `sent`, `failed` or `refused`) and `send_code`
  - `records_sent` (`null` when the sender cannot say)
  - `detail`
- **Ordering:** the row is written **before** the send. A nonce found in the
  tenant that no row holds is an error, not a pass.

#### 6.3 Two witnesses, and a third when the lab offers one

`verified` requires all three:
- the **sender witness**: `send_status = sent`, plus a TCP connect or TLS
  handshake result for transports that have one;
- the **tenant witness**: a Q1 row that carries the nonce;
- the **control witness**: at least one sent control of the same door kind
  recorded `absent` after grace on this instance, and none recorded `landed`
  since (§6.4).

A **lab witness** is recorded when one exists, but it never opens a door. The
FTP and Files and Folders collectors rename a file once they pick it up. The
Broker VM console shows Logs Received and Logs Sent.

#### 6.4 Negative controls

**Claude's call (builder check):** negative controls are sent by SimCore, not
by hand, and `verified` is gated on them in code. A probe that cannot return 0
proves nothing, so the gate is the check's function, not an extra guard.

- **The scan control, on every check.** Q1 rows are scanned for the
  scan-control nonce as well as the real one.
  - A hit on it means the check cannot tell a real hit from noise. The check
    then records `unknown` with `NEGATIVE_CONTROL_MATCHED` and logs an error.
  - On doors with a dedicated `cortexsim_door_*` dataset this costs no extra
    query.
  - The HTTP collector's dataset is shared and filtered server-side, so there
    the scan control runs as Q2 on the target's first hit and is cached per
    target. **Claude's call.**
- **Sent controls, per door kind** (listed per door in §7).
  - Each is a `DoorProbe` row with `control` set and `expected: absent`, sent
    through `POST /api/doors/{key}/probe` with `{"control": "<id>"}`. It
    carries its own nonce and the control marker, so a control that lands is
    found by the same Q1 scan as a probe, at no extra query.
  - Each differs from a probe in one property that must stop it: a wrong
    source IP, a wrong format, a wrong key, or a location the applet does not
    collect.
  - For the file applets (FTP, Files and Folders, CSV) the control is a
    distinct control payload written to a `control/` folder beside the
    collected `incoming/` folder, never a nonce in a file name.
  - Controls take their own path and size. The senders' probe rules, such as
    the Files size floor (§7.4), do not apply to them.
  - **An expected miss never changes door state.** A control found absent
    after grace writes `control_result: absent` on its own row. It records no
    `DoorEvent`, never `PROBE_NOT_FOUND` and never `closed`.
  - **A control that lands** writes `control_result: landed` and a `DoorEvent`
    of `unknown` with `NEGATIVE_CONTROL_MATCHED`. It blocks `verified` for that
    door kind until a later control is recorded absent.
  - **`verified` requires one recorded control miss per door kind** on this
    instance (§6.3), enforced in code. A probe hit with none on record reads
    `unknown` with `NO_CONTROL_MISS_RECORDED` and keeps the hit in its
    evidence; the first check after a control miss reads `verified`.
  - Controls are lab-only, like probes. The lab-run doc also records each
    control's result.

#### 6.5 State mapping

| Situation | state | basis | rung | code |
|---|---|---|---|---|
| Transport door, never probed or checked | unknown | — | configured | `NO_PROBE_SENT` (off the lab its remediation says "run Check; probes are lab-only") |
| Probe or control refused (customer tenant) | no event; 403 | | | `DOOR_PROBE_NOT_LAB_TENANT` |
| Probe or control refused (a run is live on that beacon) | no event; 409 | | | `DOOR_PROBE_RUN_LIVE` |
| Sender offline or tool missing | unknown | — | configured | `DOOR_PROBE_SENDER_UNAVAILABLE` |
| Sender could not connect from its host | closed | check | configured | per door (e.g. `SYSLOG_TCP_UNREACHABLE`) |
| Sent, inside grace, no hit | unknown | check | reachable | `PROBE_PENDING` |
| Hit, scan control clean, a control miss on record for the kind | **open** | verified | verified | — |
| Hit, scan control clean, no control miss on record | unknown | check | reachable | `NO_CONTROL_MISS_RECORDED` |
| Hit, and the scan control matched or a sent control landed | unknown | — | — | `NEGATIVE_CONTROL_MATCHED` |
| Sent control absent after grace | no event; `control_result: absent` on its row | | | — |
| Grace elapsed, no hit, query answered | closed | check | reachable | `PROBE_NOT_FOUND` plus the Q2 cause code |
| Freshness: a non-CortexSim row in the window | open | traffic | traffic seen | —, or `TRAFFIC_SEEN_WITH_COLLECTION_ERRORS` |
| Freshness: no non-CortexSim row in the window | unknown | traffic | configured | `NO_RECENT_TRAFFIC` plus the classified Q2 causes |
| Rows since `sent_at` carry no recognisable probe field | unknown (the check raises internally) | — | — | `PROBE_ROW_SHAPE_UNRECOGNISED` |
| XQL error, quota, 401, 5xx, or no `xql` | unknown | — | — | the `TENANT_REMEDIATION` reasons |
| XDR Collector on an unsupported host, any miss | unknown | check | — | `XDRC_HOST_UNSUPPORTED` |
| WEC with no Windows source | unknown | — | authored | `WEC_NO_WINDOWS_SOURCE` |

The send and miss rows apply to probes. A control's refused send (for example
`wrong_key`'s 401) or miss is recorded on its own row only and changes no door
state (§6.4).

**Grace defaults (Claude's call).** The first live runs will revise them from
measured latency:
- push doors (syslog, HTTP, XDR Collector, WEC): 900 s;
- pull doors: the applet's collection interval plus 900 s;
- CSV: 600 + 900 s.

The Q1 timeframe starts at `sent_at − 600 s`, a margin for clock skew between
sender and tenant.

#### 6.6 Where the nonce is found

- **Dedicated dataset (every door except HTTP).**
  - Q1 is `dataset = <ds> | sort desc _insert_time | limit 200` over the
    timeframe.
  - SimCore scans every value of every returned row for the probe nonce, the
    scan-control nonce and the nonce of every sent control still inside grace,
    so the probe does not depend on which column a parser puts the nonce in.
  - The evidence records which column held it.
  - `_insert_time` is named on the vendor's ingestion-metrics page.
- **Shared dataset (the HTTP collector).**
  - Q1 is `dataset = <target.dataset> | filter cortexsim_door_nonce in ("<nonce>", "<sent-control nonces>") | limit 10`.
    The probe and its pending controls share one call.
  - An XQL error that names an unknown field reads `unknown`
    (`PROBE_FIELD_UNKNOWN`), never closed.
- **Field-name provenance.** Every evidence block says `field names: from
  documentation` until a deathstar lab-run doc records the names actually
  observed. Only that doc changes it.

#### 6.7 Transport doors are diagnostic, not scoring

- **A new door role, `transport`.**
  - No dataset row in `spec/dataset_requirements.yml` names these doors, so
    `door_resolution` can never put one in a detection's requirement.
  - A transport door's state never excludes, credits or misses a row.
  - Scoring stays on the data doors (`syslog_cisco_asa`, `windows_events`,
    `http_collector:<target>` and the rest).
- **What a transport door buys:**
  - confidence before the POV that the path delivers;
  - the cause line when a data door is closed. Example: `syslog_cisco_asa` is
    closed while `broker_syslog:bvm-5514` is open, so the readout says "the
    Broker VM row delivers from pov-target-01; the ASA is not sending".
  - With only a `traffic seen` reading, the line says "the Broker VM row has
    recent traffic" and names no host, because the freshness rung knows no
    sender.
- **`http_collector` stays a data door**, because detections already route to
  it (spec §5 rule 1). **Claude's call.**
- **Optional `via`.** A data door may carry an informational `via: <transport
  door id>` field to support that cross-reference.
- **No false zero.** `GET /api/doors` shows these doors with
  `detections_needing: null` and `role: transport`, never 0. A 0 would read as
  "nothing needs this".

#### 6.8 Doors and targets

**Claude's call:** these doors are collector targets, as Henry framed it on
2026-11-10, with new kinds. Every kind keeps a URL, so the host, port and
allowlist-pin machinery is reused.

| Kind | URL scheme | Door id (scope: target) |
|---|---|---|
| `xsiam_http_collector`, `http_forwarder` | `https`, `http` | `http_collector` (exists) |
| `broker_syslog` | `syslog+tcp`, `syslog+udp`, `syslog+tls` | `broker_syslog` |
| `broker_wec` | `wec` (broker FQDN, 5986) | `broker_wec` |
| `broker_ftp` | `sftp`, `ftps`, `ftp` (the staged server's folder) | `broker_ftp` |
| `broker_files` | `nfs`, `smb` | `broker_files` |
| `broker_csv` | `smb` | `broker_csv` |
| `broker_db` | `postgresql`, `mysql`, `mssql`, `oracle` | `broker_db` |
| `xdr_collector` | `file` (`file://<host>/<path>`) | `xdr_collector` |

- **A new `params` JSON column** holds the kind-specific settings:
  - syslog: `framing`, `format`, `expected_source_cidr`;
  - pull applets: `collect_every_seconds`, `sender_agent_id`;
  - `xdr_collector`: `host_agent_id`, and `collector_ip` for the freshness
    rung's Q2 (§7.7).
- **`dataset` is the dataset the door's row writes.** On the lab tenant it is
  the probe dataset. On a POV it is the customer row's dataset, which the
  freshness rung reads (§6.9).
  - One dataset per target. **Claude's call:** it keeps the freshness rung at
    two calls; a row that feeds several datasets is entered as one target per
    dataset.
  - A dataset that does not start with `cortexsim_` raises a **warning**, not a
    refusal, per the no-extra-guards rule, and only on an instance whose tenant
    is probe-pinned. **Claude's call:** a customer row's dataset is never
    `cortexsim_*`, so the warning would fire on every POV and mean nothing.
- **Only HTTP kinds act as POST destinations for collector steps.**
  `apply_targets` skips every other kind, and a behaviour test pins that.
- **`configured_facts` keys target doors by kind** (§9, Task 7). This fixes the
  latent every-target-every-door bug noted in §2.

#### 6.9 The freshness rung (read-only, every tenant)

Henry decided (2026-11-11): customer tenants get a read-only freshness rung on
the transport doors. It reads recent rows in the door's dataset plus the door's
own diagnostic source, it is shown as `traffic seen` and never as `verified`,
and only a deathstar probe plus check can read `verified` (§5, decision 6).

- **The ladder.** A transport door climbs `authored` → `configured` →
  `reachable` → `traffic seen` → `verified`.
  - On a customer tenant `reachable` is never measured. The only reachability
    evidence is the probe send itself (§7.1), and probes are lab-only, so the
    door goes from `configured` to `traffic seen`.
- **Q1, recent rows.** Task 7's `ROWS_QUERY` on the target's `dataset` over its
  3,600 s window, with `| sort desc _time | limit 20` in place of `limit 1`.
  - **Claude's call:** rows that carry a CortexSim marker
    (`cortexsim_preflight`, `cortexsim_door_probe` or `cortexsim_door_control`)
    are dropped locally before counting, on every tenant and on the
    recent-rows method too. Our own canary, probe or control row would
    otherwise let the send prove itself.
  - The evidence carries `rows_seen` (non-CortexSim rows on the page, with
    `truncated: true` when the page is full) and the newest `_insert_time`.
- **Q2, the door's diagnostic source, on every freshness check.**
  - Broker VM applets: `collection_auditing` for the applet's `collector_type`
    over the same window (§7.1–7.4, §7.6). The CSV Collector uses the
    management audit log instead (§7.5).
  - XDR Collector: `metrics_view` for the collector's IP, read for `last_seen`
    (§7.7). **Claude's call:** `metrics_source`'s `data_freshness_*` bucket is
    not queried, because Q1's newest `_insert_time` measures the same thing
    more precisely and a third call would break the two-call cap.
- **States (Claude's call).**
  - A non-CortexSim row in the window: `open`, basis `traffic`, rung `traffic
    seen`. Errors in Q2 add `TRAFFIC_SEEN_WITH_COLLECTION_ERRORS` and are
    listed. Transport doors never score, so `open` here changes no result; the
    rung carries the label.
  - No such row: `unknown` with `NO_RECENT_TRAFFIC`, plus each classified Q2
    cause (for example `SYSLOG_SOURCE_IP_REJECTED`). Never `closed`: a quiet
    source and a broken path look the same from here, and a door that was
    never probed does not read `closed` (§3).
  - A freshness reading never overrides a probe-basis reading (`verified`, or a
    probe's `closed`) on the same target. **Claude's call:** a probe names its
    host and its record, and traffic seen names neither, so the weaker reading
    cannot overturn the stronger. The console shows both lines.
  - `traffic seen` is never counted in `doors_verified_by_probe`.
- **Failure codes shared by every transport door.**

| Code | Means | Remediation |
|---|---|---|
| `NO_RECENT_TRAFFIC` | No non-CortexSim row in `<ds>` in the last 3,600 s | "Nothing reached <ds> in the last hour. A quiet source and a broken path look the same from here; the diagnostic lines below are the only cause evidence. On the lab tenant, a probe settles it." |
| `TRAFFIC_SEEN_WITH_COLLECTION_ERRORS` | Rows arrived, and the diagnostic source logged errors in the same window | "Traffic is arriving, but the applet logged errors. Some sources on this row may be failing; see the lines below." |
| `TRAFFIC_DATASET_NOT_FOUND` | XQL reports that the dataset does not exist (the error shape is unverified, §14) | "No dataset named <ds> exists. Check the row's Vendor and Product, or the profile's dataset." |
| `CSV_DIAGNOSTIC_UNAVAILABLE`, `XDRC_NO_METRICS`, `XDRC_METRICS_STALE` | Per door (§7.5, §7.7) | Per door. |

**The Readiness rule.** Henry decided (2026-11-11): Readiness must not go
degraded merely because a POV has no Broker VM.
- A transport kind with no target reads `not configured` and is stated in
  words ("this POV routes no data through a Broker VM" / "through an XDR
  Collector"). It is never hidden and never degraded. The first draft's
  `NO_TRANSPORT_TARGETS` degradation is dropped.
- **Claude's call:** a transport target that *is* configured degrades
  Readiness while its best reading is below `traffic seen` (`NO_PROBE_SENT`,
  `NO_RECENT_TRAFFIC`, `closed`, or `unknown` from a tenant error). It was
  entered because the POV uses it, so a silent one is owed an answer.
- Detection readiness still comes from the data doors' own states, as planned.

### 7. Per-door designs

The common fields from §6.2 are omitted below. "Beacon" means the CortexSim
beacon on the named host. Probe steps run as root, outside the identity
harness (**Claude's call**).

#### 7.1 Broker VM Syslog Collector — `broker_syslog:<target>`

| | |
|---|---|
| Sender | Primary: the beacon on **pov-target-01**. Secondary: SimCore in-process (stdlib socket), whose source is the Mac. That makes it the natural negative control for the source IP. |
| Transport | A dedicated applet row: **TCP 5514, octet framing (RFC 6587), format CEF, Vendor `cortexsim`, Product `door_syslog`**, and Source Network set to the calibrated sender IP (below). The row is **Claude's call**, as instructed. 5514 is outside every disallowed range. TCP gives a reachable rung, which UDP cannot. CEF carries vendor and product in-band, so the probe never lands in a customer dataset. Secure TCP is deferred: the receiver page says CEF/LEEF over syslog is "(TLS not supported)", while the activation page offers Secure TCP. A later TLS row is experimental, and its failure reads `unknown` with `SYSLOG_TLS_DOC_CONFLICT`. |
| Payload | `logger -n <bvm> -P 5514 -T --octet-count --rfc3164 -t cortexsim 'CEF:0\|cortexsim\|door_syslog\|1\|door-probe\|CortexSim door probe <nonce>\|1\|cs1Label=cortexsim_door_nonce cs1=<nonce> msg=CortexSim door probe - safe to discard rt=<epoch_ms>'`. The nonce sits in both the Name header and an extension; `rt` carries the send time. The `logger` flags come from util-linux `logger(1)`. **Claude's call:** an RFC 3164 header. Whether the CEF row expects RFC 3164 or RFC 5424 is undocumented. If Q2 returns `SYSLOG_FORMAT_MISMATCH`, try `--rfc5424`. |
| Reachable rung | The probe send itself. **Claude's call (builder check):** `logger -T` makes the TCP connect, and its exit status and stderr are the evidence. util-linux documents exit 0 on success and >0 on an error; a non-zero exit with a connect error reads `SYSLOG_TCP_UNREACHABLE`, and exit 0 reads `reachable`. No `bash /dev/tcp` or `nc` runs on the measured endpoint: the send already makes the connect, and a separate shell socket there only adds telemetry that looks like an attack. If `logger` is missing, report `DOOR_PROBE_SENDER_UNAVAILABLE`. UDP targets report `SYSLOG_UDP_UNCONFIRMABLE` and never `reachable`. |
| Lands in | `cortexsim_door_syslog_raw` (`<vendor>_<product>_raw`, per the receiver page). |
| Q1 | Dedicated-dataset scan (§6.6). |
| Q2 (on a miss after grace) | `dataset = collection_auditing \| filter collector_type = "Syslog Collector" \| sort desc _time \| limit 20` over the timeframe. Each description is classified against the documented warning texts. The `collector_type` value is unverified: the doc's example uses "Local Agent Settings". |
| Freshness rung (any tenant) | Q1: recent rows in the target's `dataset` (§6.9). Q2: the `collection_auditing` query above, over the 3,600 s window, every time. Label `traffic seen`. On a POV the dataset is the customer row's, for example a CISCO row's dataset. |
| Negative controls | SimCore-sent controls (§6.4), each `expected: absent`. (a) `wrong_source`: SimCore sends in-process from the Mac; Q2 should report "Connection rejected from <Mac IP>". (b) `wrong_format`: RAW text with no CEF, sent by the beacon on pov-target-01; expect "does not match the expected format 'CEF'". Plus the scan control. |
| Lab witness | Console › Broker VMs › Syslog › Logs Received and Logs Sent. This is console only; there is no documented API. |

**Source IP as a deploy-time input: calibrate by rejection (Claude's call).**
Source Network is an optional setting and can be edited; only Port and
Protocol are fixed.

1. Create the row with Source Network `192.0.2.1/32` (TEST-NET-1, RFC 5737).
   Nothing real sends from it.
2. Send the `calibration` control from pov-target-01 (`{"control":
   "calibration"}`, `expected: absent`). Expect no row. Because it is a
   control, its miss never closes the door, and it counts as this kind's
   control miss. Q2 returns `Connection rejected from <ip>`, and that `<ip>`
   is the address the Broker VM sees. It will be 192.168.122.77 if the Broker
   VM shares ms-01's libvirt network, or ms-01's NAT address if the Broker VM
   sits on the LAN.
3. Record `<ip>` as `params.expected_source_cidr` (evidence: `observed_by:
   rejection`). Set the row's Source Network to `<ip>/32`.
4. Probe. Expect the nonce row, which reads `verified`.
5. Send controls (a) and (b).

The row is never left blank, because a blank Source Network accepts any source.
Until step 3, the target carries `SYSLOG_SOURCE_UNCALIBRATED`.

| Code | Means | Remediation |
|---|---|---|
| `SYSLOG_PORT_DISALLOWED` | The port is in a reserved range | "Pick a port outside 0–1024 (except 514), 63000–65000 and the reserved list; the row cannot be edited later." |
| `SYSLOG_TCP_UNREACHABLE` | The probe's `logger -T` connect failed from the sending host | "From <host>, the Broker VM does not answer on <port>/TCP. Check that the row exists, that the Broker VM is up, and the routing/NAT between them." |
| `SYSLOG_UDP_UNCONFIRMABLE` | UDP has no reachable rung | "UDP loss is silent. Only a probe hit proves this row." |
| `SYSLOG_SOURCE_UNCALIBRATED` | `expected_source_cidr` is unset | "Run the calibration probe (Source Network 192.0.2.1/32) to learn the IP the Broker VM sees." |
| `SYSLOG_SOURCE_IP_REJECTED` | Q2: "Connection rejected from <ip>" | "The row's Source Network does not include <ip>. If <ip> is the lab host, set Source Network to <ip>/32." |
| `SYSLOG_FORMAT_MISMATCH` | Q2: "does not match the expected format 'CEF'" | "The row expects CEF. Check the header (RFC 3164 vs 5424) and the CEF prefix." |
| `SYSLOG_FIRST_LOG_TYPE_MISMATCH` | Q2: the first log over the connection has the wrong type | As for `SYSLOG_FORMAT_MISMATCH`. |
| `SYSLOG_OCTET_FRAMING_FAILED` | Q2: octet-framing parse failure | "The sender's framing and the row disagree. Use `--octet-count` on TCP." |
| `BROKER_RECEPTOR_UNREACHABLE` | Q2: "could not reach the receptor (Tenant)" | "The Broker VM cannot reach the tenant. Check outbound 443 to `br-<tenant>.xdr.<region>.paloaltonetworks.com` and `distributions.traps.paloaltonetworks.com`, and NTP." |
| `PROBE_NOT_FOUND` | No row after grace, and no cause from Q2 | "Sent from <host> at <t>; nothing landed in <ds>. Check Logs Received and Logs Sent for this row in the Broker VM console." |

**Verified proves:** one CEF record sent by `<host>` over TCP to `<bvm>:5514`
reached `cortexsim_door_syslog_raw`. That covers:
- the network path from the host to the Broker VM;
- this row's source ACL and format;
- the uplink from the Broker VM to the tenant.

**It does not prove:**
- that any other row works (514/UDP, or a CISCO row);
- that a vendor device sends;
- that a vendor parser or content pack maps its logs;
- XDM modeling;
- that any detection fires.

The NGFW wizard's `PN_SYSLOG_DELIVERY_UNVERIFIED` stays degraded. Its
remediation may link to this door when the host, port and protocol match the
firewall's syslog server profile.

#### 7.2 Broker VM Windows Event Collector — `broker_wec:<target>` (designed only)

| | |
|---|---|
| Today | The door is registered and visible. With no WEC target it reads `unknown` / `authored` with `WEC_NO_WINDOWS_SOURCE`: "needs a Windows Server 2012 R2 or later forwarding to this Broker VM". Probing is disabled. |
| Freshness rung (any tenant) | Runs wherever a WEC target is configured, so a customer POV with real forwarders gets it with no lab Windows host. Q1: recent rows in `microsoft_windows_raw` (§6.9). Q2: `collection_auditing` for the WEC. `microsoft_windows_raw` may also be fed by other Windows collectors (for example Winlogbeat on an XDR Collector; unverified), so the rows do not prove this Broker VM sent them; Q2's lines are the only per-applet evidence, and the evidence says so. |
| Source (once one exists) | A Windows Server 2012 R2+ forwarder (vendor prerequisite), and Broker VM 8.0 or later. A **standalone Broker VM needs an FQDN** in local DNS that the forwarder can resolve; the lab Pi-hole can serve it. Mutual TLS uses the PFX exported from the console. The forwarder needs outbound TCP 5986. The doc's procedure is a GPO on a domain. One candidate source is the ITDR module's AD lab (`infra/modules/aws/itdr`, a Server 2022 DC), but it sits in AWS and would need a network path to the lab Broker VM. |
| Sender | A beacon on the Windows host (`windows/amd64` is served). It runs `eventcreate /t INFORMATION /id 999 /l APPLICATION /so CortexSimDoorProbe /d "cortexsim_door_nonce=<nonce> sent_at=<t>"`. Per Microsoft's syntax, event IDs run from 1 to 1000, and `eventcreate` cannot write to the Security log. |
| Applet config | Add a Collected Events row for the `Application` source, with a minimum level of Information. By default the applet collects only Palo Alto's predefined Security events, so without this row the probe would never be collected. Keep the Security row: the vendor says it feeds detectors. |
| Lands in | `xdr_data` and `microsoft_windows_raw` (vendor doc). Q1 scans `microsoft_windows_raw` within the timeframe. The name of the event-message column is unverified, which the scan tolerates. |
| Q2 | `collection_auditing`, filtered to the WEC `collector_type` (the value is unverified). |
| Negative control | `uncollected_log`: a control event (own nonce, control marker) written by SimCore through the beacon to the `SYSTEM` log, which no collected row covers. It must not land. |
| Codes | `WEC_NO_WINDOWS_SOURCE`; `WEC_APPLET_START_FAILED` (doc: "Failed starting WEC applet due to an internal error"); `BROKER_RECEPTOR_UNREACHABLE`; `PROBE_NOT_FOUND`. |
| Link to data doors | A verified `broker_wec` lets the operator attest `windows_events` with `producer=broker_wec` (plan Task 7). That attestation remains an attestation; the data door still needs its own check. |

**Verified will prove** that the path from the forwarder through the WEC to the
tenant delivered one Application-log event from that host. It will not prove
that Security auditing is enabled for the events detectors need. The WEC
controls collection, not logging (vendor doc).

#### 7.3 Broker VM FTP Collector — `broker_ftp:<target>`

| | |
|---|---|
| Staged source | **SFTP** (the applet's default type) on **door-src-01** (§8): OpenSSH `internal-sftp`, with user `cortexsim-bvm` chrooted to `/srv/cortexsim/door-ftp` and given rename and delete rights there (vendor prerequisite). The chroot holds two folders: `incoming/`, which the applet collects, and `control/`, which it does not. Authenticate with a password, or with an RSA key in **PEM format**. The applet rejects the OpenSSH key format (vendor doc: `ssh-keygen -t rsa -b 4096 -m PEM`). |
| Applet config | Type SFTP. Folder `incoming/`, as the chroot presents it. Collect Every 1 minute. After Files Uploaded: rename with suffix `ingested`. Include `door-probe-*.json`. Log Format JSON (single-line JSON only). Vendor `cortexsim`, Product `door_ftp`. |
| Sender | The beacon on door-src-01 writes `incoming/door-probe-<nonce>.json`, one JSON line with the §6.2 fields, as the SFTP user's group. |
| Lands in | `cortexsim_door_ftp_raw`. |
| Q1 / Q2 | Q1 is the dedicated-dataset scan. Q2 is `collection_auditing` for the FTP Collector. |
| Freshness rung (any tenant) | Q1: recent rows in the target's `dataset`. Q2: `collection_auditing` for the FTP Collector over the window (§6.9). Label `traffic seen`. |
| Negative controls | `uncollected_folder` (§6.4): the same JSON line as a probe, with its own nonce and the control marker, written by SimCore through the beacon to `control/door-probe-<the control's own nonce>.json`, so the only property that differs from a probe is the folder. It must not land and must not be renamed. Separately, at bring-up, Generate Preview on an empty `incoming/` returns no rows. |
| Lab witness | A follow-up beacon step finds `incoming/door-probe-<nonce>.json.ingested`, and the control file in `control/` un-renamed. |
| Codes (from documented errors) | `FTP_CONNECT_FAILED` ("Failed to connect to server"); `FTP_LOGIN_FAILED`; `FTP_SSL_CERT_FAILED`; `FTP_SSH_KEY_LOAD_FAILED` ("convert the key to PEM RSA"); `FTP_PATH_DENIED`; `FTP_LIST_TIMEOUT`; `BROKER_STREAM_FAILED` ("Failed to stream collector results to server"); `BROKER_RECEPTOR_UNREACHABLE`; `FTP_FILE_NOT_PICKED_UP` (no rename after the interval plus grace, meaning the applet never read the file). |

**Verified proves:** the Broker VM pulled a file from that server and folder,
and delivered it. **It does not prove:** other folders, other formats, or the
500 MB file limit.

#### 7.4 Broker VM Files and Folders Collector — `broker_files:<target>`

| | |
|---|---|
| Staged source | An NFSv4 export, `/srv/cortexsim/door-files`, on door-src-01, read-write to the Broker VM's IP only, holding `incoming/` (collected) and `control/` (not collected). The applet supports NFSv4 for Linux paths and SMB for Windows paths. It collects **only files over 256 bytes**, and it ingests UTF-8 only. |
| Applet config | Folder `nfs://door-src-01/srv/cortexsim/door-files/incoming`. Mode **Batch** (**Claude's call:** one new file per probe, plus a rename witness, instead of Tail on a long-lived file). Collect Every 1 minute; rename suffix `ingested`. Include `door-probe-*.json`. Log Format JSON. Storage Method Append. Vendor `cortexsim`, Product `door_files`. |
| Sender | The beacon on door-src-01 writes `incoming/door-probe-<nonce>.json`. The JSON line carries a `pad` field that takes it past 256 bytes. A sender-side check refuses a probe file of 256 bytes or fewer with `FILES_PROBE_TOO_SMALL`. This checks the honesty of our own payload; it is not input validation. It applies to probes only; controls set their own size (§6.4). |
| Lands in | `cortexsim_door_files_raw`. |
| Q1 / Q2 | Q1 is the dedicated-dataset scan. Q2 is `collection_auditing` for Files and Folders. |
| Freshness rung (any tenant) | Q1: recent rows in the target's `dataset`. Q2: `collection_auditing` for Files and Folders over the window (§6.9). Label `traffic seen`. |
| Negative controls | `uncollected_folder` (§6.4): a control payload padded past 256 bytes like a probe, so size cannot be why it misses, written to `control/`. It must not land. `under_size_floor` (optional): a 200-byte control written to `incoming/`, which proves the size floor; the probe size rule does not refuse it. |
| Codes | `FILES_INVALID_MOUNT` ("Invalid mount detected at path"), `BROKER_STREAM_FAILED`, `FILES_FORMAT_MISMATCH`, `BROKER_RECEPTOR_UNREACHABLE`, `FILES_FILE_NOT_PICKED_UP`. |

**Verified proves:** an NFSv4 share on that host was mounted and read, and its
file delivered. **It does not prove:** SMB mode, Tail mode, or Replace (lookup)
mode.

#### 7.5 Broker VM CSV Collector — `broker_csv:<target>`

| | |
|---|---|
| Staged source | The vendor documents a **shared Windows directory** (`//host/<folder_path>`, with username and password). No Windows host exists, so the share is **Samba** on door-src-01 at `//door-src-01/door-csv`. **Claude's call.** The target carries `CSV_SOURCE_NOT_WINDOWS`. Henry set a rule for the XDR Collector; the same rule applies here, so any miss reads `unknown` with that code, never `closed`. |
| Applet config | Mounted folder `//door-src-01/door-csv`, user `cortexsim-bvm`. CSV files: `//door-src-01/door-csv/incoming/door-probe-*.csv`. Target dataset `cortexsim_door_csv`. The applet checks for new files every 10 minutes. Every file uses the same header. |
| Sender | The beacon writes `incoming/door-probe-<nonce>.csv`: the header `cortexsim_door_probe,cortexsim_door_nonce,door_key,sent_at,sent_from,message` and one data row. |
| Lands in | `cortexsim_door_csv` (a named target dataset, not a `_raw` one). |
| Q1 / Q2 | Q1 is the dedicated-dataset scan. **The troubleshooting page does not list the CSV Collector among the applets that `collection_auditing` covers.** Q2 is therefore `OP-AUDITS-MANAGEMENT-LOGS`, filtered to Broker VM applet events in the window (Applet Activated, Deactivated, License Expired, connection_test). If that returns nothing, the code is `CSV_DIAGNOSTIC_UNAVAILABLE`. |
| Freshness rung (any tenant) | Q1: recent rows in the target's `dataset`. Q2: the audit-log query above, over the window (§6.9). Label `traffic seen`; an empty Q2 adds `CSV_DIAGNOSTIC_UNAVAILABLE`. |
| Negative control | `uncollected_folder` (§6.4): a CSV with the same header and one control row (own nonce, control marker), written by SimCore through the beacon to `control/door-probe-<the control's own nonce>.csv`, which the file pattern does not match. It must not land. |

**Verified proves:** a CSV file on an SMB share (Samba, not Windows) was
picked up and delivered. **It does not prove:** that a Windows share works.

#### 7.6 Broker VM Database Collector — `broker_db:<target>`

| | |
|---|---|
| Staged source | PostgreSQL on door-src-01, database `cortexsim_door`, holding two tables of identical shape: `cortexsim_door_probe(id bigserial primary key, sent_at timestamptz, cortexsim_door_nonce text, door_key text, sent_from text, message text)` and `cortexsim_door_probe_excluded`. Role `cortexsim_bvm` has `SELECT` on the first table only. Its password contains no `;`, which the vendor says is unsupported. |
| Applet config | Connection PostgreSQL. Storage Method Append. Rising Column `id`, Retrieval Value `0`. Collect Every 30 s (the documented default). Vendor `cortexsim`, Product `door_db`. SQL: `SELECT id, sent_at, cortexsim_door_nonce, door_key, sent_from, message FROM cortexsim_door_probe WHERE id > ? ORDER BY id`. **Claude's call:** Enable SSL is off for the lab and recorded as `params.db_ssl=false`, which the evidence carries. A later pass can turn SSL on with the cert in the Broker VM trust store. |
| Sender | The beacon on door-src-01 runs `psql` locally as the owning role and inserts one row. |
| Lands in | `cortexsim_door_db_raw`. Rows are capped at 65 KB (vendor). |
| Q1 / Q2 | Q1 is the dedicated-dataset scan. Q2 is `collection_auditing` for the Database Collector. |
| Freshness rung (any tenant) | Q1: recent rows in the target's `dataset`. Q2: `collection_auditing` for the Database Collector over the window (§6.9). Label `traffic seen`. |
| Negative control | `excluded_table` (§6.4): SimCore inserts a control row (own nonce, control marker) into `cortexsim_door_probe_excluded` through the beacon. It must not land. That also proves the applet's role cannot read beyond its grant. |
| Codes | From the documented errors: `DB_CONNECT_FAILED`, `DB_QUERY_FAILED`, `DB_INITIAL_VALUE_UNPARSED`, `DB_STATE_WRITE_FAILED`, `DB_STREAM_FAILED`, `DB_QUERY_LONG_RUNNING`. Also `BROKER_RECEPTOR_UNREACHABLE`. |

**Verified proves:** an append-mode Postgres pull delivered a new row. **It
does not prove:** Replace or snapshot mode, MSSQL with Kerberos, or SSL.

#### 7.7 XDR Collector — `xdr_collector:<target>` on pov-target-01 (Henry's call)

| | |
|---|---|
| Host | **pov-target-01**: Ubuntu 24.04.5, with XDR agent 9.4.0.470 already installed. Henry's call. |
| Install | Create an installation package in the tenant and download the **DEB**. On the host: `tar xvf` the package, `mkdir -p /etc/panw`, copy `collector.conf` into `/etc/panw/`, then `apt-get install ./<file>.deb`. Verify with `dpkg -l \| grep xdr-collector` (vendor doc). The host needs outbound TCP 443. |
| Profile | Linux › Filebeat, named `CortexSim door probe` (letters, digits and spaces only; 30 characters at most). It holds one `filestream` input, id `cortexsim-door-probe`, on `/var/log/cortexsim/door-xdrc/probe.log`, plus `processors: [add_fields: {fields: {vendor: cortexsim, product: door_xdrc}}]`. That uses the default `fields` target, as the doc requires. The profile contains nothing else. It is mapped to a policy that covers pov-target-01. |
| Sender | The beacon on pov-target-01 appends one JSON line to `probe.log`. |
| Lands in | `cortexsim_door_xdrc_raw` (vendor doc: Filebeat writes to `<vendor>_<product>_raw`). |
| Q1 / Q2 | Q1 is the dedicated-dataset scan. Q2 is `preset = metrics_view \| filter _collector_ip = "<params.collector_ip>" \| sort desc last_seen \| limit 5`. It needs Cortex Analytics enabled. If it returns no row, the reading is `unknown`, never closed, because the doc says rows with zero values are not stored. |
| Freshness rung (any tenant) | Q1: recent rows in the target's `dataset` (§6.9). Q2: the `metrics_view` query above, every time, read for `last_seen`. A `last_seen` older than the 3,600 s window adds `XDRC_METRICS_STALE`; no row adds `XDRC_NO_METRICS`. Label `traffic seen`. |
| Negative controls | `uncovered_file` (§6.4): a control line (own nonce, control marker) written by SimCore through the beacon to `/var/log/cortexsim/door-xdrc/excluded.log`, which the profile does not cover. It must not land. Plus the scan control. |

**The validity check (designed for Henry's call).** It runs before install,
before every probe, and as a fact attached to every door event for this target.

| Fact | Read from | Effect |
|---|---|---|
| `host_os` | `/etc/os-release` `ID` and `VERSION_ID`, read through the beacon | Compared against a pinned copy of the vendor's Linux list: Ubuntu Server 12, 14, 16, 18, 20 and 22, plus RHEL, SLES and Oracle versions. The copy records its doc URL and fetch date. On 24.04 it sets `host_supported: false` and code `XDRC_HOST_UNSUPPORTED`. |
| `arch` | `uname -m` | The vendor lists x86 64-bit only; anything else is unsupported. |
| `coinstall` | The tenant's endpoint record for the host (the `xdr_agent:<agent>` door and `get_endpoints`), not a guessed path | If the agent is present: `coinstall: true`, code `XDRC_COINSTALL_UNDOCUMENTED`. |
| `/etc/panw` snapshot | A beacon file listing with sha256 sums, taken before and after install | Both products use `/etc/panw`: the collector doc copies `collector.conf` there, and Henry's agent installer README copies `cortex.conf` there. The diff is kept as evidence. If any agent file changed, the code is `XDRC_AGENT_FILES_TOUCHED`. |
| Agent regression | An `xdr_agent:<agent>` check right after install, and again before the next endpoint run | If the agent stops reading CONNECTED, the closing event's evidence carries `XDRC_COINSTALL_SUSPECTED`. Rows behind it are excluded as `DOOR_NOT_VERIFIED`, never counted as missed (plan Task 9). |

**Effect on door states**

- **Probe hit on an unsupported host:** the door reads `open`, with evidence
  `host_supported: false, coinstall: true`. The console labels it "verified on
  an unsupported host".
- **Any miss, or a failed install:** the door reads `unknown` with
  `XDRC_HOST_UNSUPPORTED`, **never `closed`**.
- **Endpoint runs on this host:** while the collector is installed, their
  readout carries the label `HOST_RUNS_XDR_COLLECTOR_UNSUPPORTED`. **Claude's
  call (builder check):** it is a label only. It changes no score, verdict,
  exclusion or MTTD, because scoring changes beyond phase-2 Tasks 9–10 are a
  non-goal (§3). A miss on that host still reads as a miss; the label tells
  the reader the host was non-standard.

| Code | Remediation |
|---|---|
| `XDRC_HOST_UNSUPPORTED` | "Ubuntu <v> is not on the XDR Collector support list (the last Ubuntu listed is 22). A failure here is about the host, not the door. For a conclusive result, use a supported host." |
| `XDRC_COINSTALL_UNDOCUMENTED` | "The XDR agent runs on this host, and co-install is undocumented. Re-check the agent after install." |
| `XDRC_NOT_INSTALLED` / `XDRC_SERVICE_NOT_RUNNING` | "`dpkg -l \| grep xdr-collector` shows nothing" / "the service is down." |
| `XDRC_NO_METRICS` | "Q2 found no metrics for this collector. Either Analytics is off, or the collector shipped nothing (was the profile applied?)." |
| `XDRC_METRICS_STALE` | "The collector's newest metrics row is older than an hour. It may have stopped shipping; check the service and its connectivity to the tenant." |
| `XDRC_AGENT_FILES_TOUCHED` | "The install changed files under `/etc/panw` that the agent owns. Restore them from the snapshot and re-check `xdr_agent`." |

**Verified proves:** this collector, on this host, shipped a line from a file
input to `cortexsim_door_xdrc_raw`. **It does not prove:**
- that Winlogbeat or Windows event collection works;
- any claim about a `windows_events` producer;
- that the collector is supportable on this host.

Whether Analytics reads XDR Collector events is unverified. The rule in doors
spec §4 stands: an XDR Collector producer opens XQL and correlation only.

#### 7.8 HTTP log collector — `http_collector:<target>` (exists; gains the nonce)

| | |
|---|---|
| Sender | SimCore in-process, from phase 1. The offline bundle and the beacon follow the step-env branch, because the beacon needs the key in the step environment. **Claude's call:** phase 1 sends from SimCore only. |
| The canary (Henry's call) | Henry decided (2026-11-11): the existing canary stays on every tenant as a delivery check (a 2xx), on both paths that send it: the collector-target "Send test record" (`write_canary`, #180) and the campaign collector preflight (`POST /api/eal/campaigns/{id}/collectors/preflight`, `core/api/eal.py`). It gains a nonce and `sent_at`. Only on deathstar does the read-back run that can mark `http_collector` verified. Elsewhere the canary proves `accepted`, never `ingested`. |
| Shipped behaviour that changes | `canary_record()` gains two fields: `cortexsim_door_nonce` (fresh per send, §6.2 shape) and `sent_at` (ISO-8601 UTC). **Nothing else changes** on either path: the same routes, gates, `write_canary` opt-in, verdicts (the target preflight's `accepted` and the campaign preflight's `delivered` stay each path's top reading), delivery taxonomy and response bodies, and no `DoorProbe` row and no read-back. **Claude's call:** `door_key` is not added, because Henry named two fields and `cortexsim_campaign_id` already identifies the sender. |
| Payload (the probe, lab only) | The probe route writes the `DoorProbe` row, then POSTs the same canary body carrying that row's nonce, as one `application/json` record with `Authorization: <key>`, the bare key (`auth_scheme=""`, the dev default). "Send test record" stays a separate, unchanged action, on deathstar too. |
| Lands in | The target's configured dataset: the collector's Vendor and Product give `<vendor>_<product>_raw`. The dataset is shared with campaign records, so the probe record is marked and matches no detection logic. |
| Q1 | A server-side filter on `cortexsim_door_nonce` (§6.6). |
| Q2 | On a target's first hit, Q2 is the scan control: the scan-control nonce must return zero rows. On a miss there is no Q2. The status returned at send time is the diagnostic, and the existing delivery taxonomy already maps 401, 404, 413, 429 and 500. |
| Negative controls | `wrong_key` (§6.4): a SimCore-sent control to the same collector with a deliberately wrong key; expect 401 at send and `absent` after grace. This is the control miss `verified` needs. `wrong_format` (optional, at bring-up): JSON sent to a second collector configured for CEF; expect 500, which the doc lists as a format mismatch. Plus the scan control. |
| Codes | The existing taxonomy codes for refusals, plus `HTTPC_ACCEPTED_NOT_INGESTED` (a 200, but no row after grace; lab only, because no read-back runs elsewhere) and `PROBE_FIELD_UNKNOWN`. |

**Verified proves:** SimCore's JSON POST to that collector was ingested into
that dataset. **It does not prove:**
- that a jumpbox inside the customer network can reach the collector (the
  existing `_PROVES` text says so);
- that batch mode works (`application/x-ndjson` is not a documented content
  type);
- that every plugin authenticates: `email_emitter` sends no auth header until
  ddc70e89 lands;
- XDM modeling, or that a detection fires.

Off deathstar, and on deathstar before a probe, the read-only recent-rows
method runs, with `method: recent_rows` in its evidence.
- Canary, probe and control rows are dropped from it (§6.9), so the canary
  never reads as ingested.
- **Claude's call:** this reading is labelled `traffic seen (recent rows)`,
  never `verified (recent rows)`, because Henry reserved `verified` on this
  door for the deathstar read-back. Its state effect is unchanged (call 15).
  Whether the other data doors follow is open question 2.

### 8. Lab topology and bring-up

```
 Mac: SimCore :8888 (LAN IP varies, .105/.106)        deathstar tenant (cloud)
   |                                                     ^   ^   ^
   | LAN 192.168.1.0/24                                  |   |   | HTTPS 443
   |                                                     |   |   +-- SimCore POST /logs/v1/event (7.8)
 ms-01 192.168.1.110 (KVM, NAT for 192.168.122.0/24)     |   +------ XDR Collector + agent (7.7)
   +- pov-target-01 192.168.122.77  beacon, XDR agent, XDR Collector (to install)
   |        | syslog TCP 5514 (7.1)
   |        v
   +- bvm-01  (placement TBD: libvirt net or LAN)  ----------+  br-<tenant>... 443, NTP 123
   |        ^ pulls: SFTP 22, NFSv4 2049, SMB 445, Postgres 5432
   +- door-src-01 (Claude's call) beacon, sshd/sftp, nfs-kernel-server, samba, postgresql
   (WEC: needs a Windows Server 2012 R2+ forwarder -> bvm-01 FQDN:5986; none exists)
```

**door-src-01 (Claude's call).** A small Ubuntu 22.04 VM on ms-01, with its
own beacon and no XDR agent.
- It keeps the Broker VM's polling sessions and the four daemons off
  pov-target-01, whose endpoint telemetry the detection runs measure.
- The fallback is pov-target-01 itself, accepting that noise.
- The code does not depend on which host is used, so this choice changes only
  the runbook.

**Bring-up order.** Record each step's result in
`docs/lab/<date>-ingestion-doors-lab-run.md`.

1. **Tenant.**
   - Give deathstar's `xsiam_tenant` credential `capabilities: ["xql"]`.
   - Set `CORTEXSIM_DOOR_PROBE_TENANTS` to deathstar's API host, in the lab
     `.env` only.
   - Run the connector preflight and record the clock skew; MTTD and grace
     both depend on it.
   - Confirm, read-only, whether the Data Collection add-on and XDR
     Collectors are enabled on deathstar: the console's license page, or a
     read-only tenant-info read. CortexSim has no license op today (none in
     `core/integrations/xsiam/operations/packs/`). Record the answer.
     **Claude's call (builder check):** this is a bring-up precondition,
     checked before P3 and P4 are scheduled; both stay parked until it is
     confirmed (§10).
2. **HTTP door** (no new lab equipment).
   - On deathstar, go to Settings › Data Sources & Integrations › + Add New ›
     HTTP. Choose JSON, uncompressed, Vendor `cortexsim` and Product
     `door_http` (or the campaign target's own).
   - Store the key on the collector target.
   - Send the `wrong_key` control, then probe, then check after grace;
     `verified` needs the control's recorded miss. Send `wrong_format` if a
     CEF collector exists.
   - Confirm "Send test record" still reads `accepted`, and that its body now
     carries `cortexsim_door_nonce` and `sent_at`.
3. **Broker VM** (when Henry deploys it). Requirements per the vendor doc:
   - 4 cores, 8 GB RAM and a 512 GB thin-provisioned disk, from the KVM QCOW2
     image (Settings › Configurations › Data Broker › Broker VMs › Add Broker).
   - Outbound 443 to `br-<tenant>.xdr.<region>.paloaltonetworks.com` and
     `distributions.traps.paloaltonetworks.com`, and UDP 123 to NTP. On
     version 19+, the web console is at `https://<ip>:4443`.
   - Generate a token (valid for 24 hours). Log in with the documented default
     password, set a new one, and store it in 1Password. Register the VM with
     the token.
   - Give it an FQDN in Pi-hole now, because WEC will need one later.
   - Record where it sits and its IP.
4. **Syslog row.**
   - Add Syslog Collector → Add New → TCP, 5514, CEF, `cortexsim`/`door_syslog`,
     Source Network `192.0.2.1/32`.
   - Run the §7.1 calibration (its first send is a control), then controls
     (a) and (b).
5. **XDR Collector on pov-target-01**, only once step 1 has confirmed XDR
   Collectors on deathstar.
   - Collect the validity facts and the `/etc/panw` snapshot, and run the
     `xdr_agent` check, before install.
   - Install, then run the `xdr_agent` check again.
   - Apply the profile and policy. Send the `uncovered_file` control, then
     probe and check.
6. **door-src-01.**
   - Build the VM and enroll its beacon.
   - Install `openssh-server`, `nfs-kernel-server`, `samba` and `postgresql`
     with apt.
   - Create the users, paths (an `incoming/` and a `control/` folder for each
     file applet), tables and grants from §7.3–7.6. The credentials are lab
     test values that live in the vault and in the applet, never in the repo.
7. **Pull applets**, only once step 1 has confirmed the Data Collection
   add-on question.
   - Activate FTP, Files and Folders, CSV and DB on bvm-01 with the §7.3–7.6
     configs. Use each applet's Test Connection and Generate Preview.
   - Send each applet's control, then probe and check it.
8. **WEC.** Skipped until a Windows source exists. The door reads
   `WEC_NO_WINDOWS_SOURCE`.

### 9. Delta to the phase-2 plan (`docs/superpowers/plans/2026-11-11-ingestion-doors.md`)

**Task 7: Door checks and attestation**

- **Probe route.** Add `POST /api/doors/{key}/probe` and the `DoorProbe` table
  (§6.1–6.2). Probing is gated by `CORTEXSIM_DOOR_PROBE_TENANTS`, refused while
  a run is live on the sending beacon (`DOOR_PROBE_RUN_LIVE`), and sends a
  negative control when the body names one (§6.4).
- **Nonce method.** `check_door` gains the nonce method (§6.6) and uses it
  whenever the door has a probe. `ROWS_QUERY` stays as the recent-rows method
  for data doors with no probe. The evidence records the `method`.
- **Control gate.** The nonce method reads `verified` only with a recorded
  control miss for the door kind. A control's expected miss writes no
  `DoorEvent`; a control that lands writes `NEGATIVE_CONTROL_MATCHED` (§6.4).
- **Recent rows drop CortexSim rows.** `ROWS_QUERY` keeps its 3,600 s window,
  takes `| sort desc _time | limit 20` in place of `limit 1`, and drops rows
  carrying a CortexSim marker locally before counting (§6.9).
- **`http_collector:<name>`.** Today it requires `last_check.verdict ==
  "accepted"` and then queries the target's `dataset`.
  - On the lab tenant, with a probe: the nonce method. Accepted, but no row
    after grace: `closed`, `HTTPC_ACCEPTED_NOT_INGESTED`.
  - Otherwise: the recent-rows method, after an accepted canary as planned,
    labelled `traffic seen (recent rows)` (§7.8).
  - The canary body gains `cortexsim_door_nonce` and `sent_at` on every
    tenant. Nothing else on the shipped canary paths changes (§7.8).
- **Seven transport doors.** Register `broker_syslog`, `broker_wec`,
  `broker_ftp`, `broker_files`, `broker_csv`, `broker_db` and `xdr_collector`,
  all with `scope="target"` and `role="transport"`. Each is checked two ways:
  - by the nonce method on the lab tenant, labelled `verified (probe)`;
  - by the freshness method on any tenant (§6.9): Q1 recent rows on the
    target's dataset, Q2 the door's diagnostic source (`collection_auditing`;
    the audit log for CSV; `metrics_view` for the XDR Collector), labelled
    `traffic seen`, and failing with the §6.9 codes and the per-door codes in
    §7.
- **Second call.** It is the scan control on a door's first hit (HTTP), the
  diagnostic on a probe miss, or the diagnostic source on a freshness check.
  This keeps the two-call cap.
- **Readiness.** A transport kind with no target is stated, not degraded; a
  configured transport target below `traffic seen` degrades (§6.9).
- **`door_state.configured_facts`** keys each target door by its kind's door
  id.
- **Added tests.** Tests for doors that do not exist yet land with their phase
  (§10).
  - `test_old_canary_without_nonce_does_not_open`
  - `test_accepted_without_row_closed_after_grace`
  - `test_inside_grace_unknown`
  - `test_control_nonce_match_unknown`
  - `test_unrecognised_row_shape_unknown`
  - `test_probe_refused_on_non_lab_tenant`
  - `test_probe_refused_while_run_live`
  - `test_probe_row_written_before_send`
  - `test_hit_without_control_miss_not_verified`
  - `test_control_miss_changes_no_door_state`
  - `test_control_landed_records_negative_control_matched`
  - `test_canary_body_gains_only_nonce_and_sent_at`
  - `test_recent_rows_drop_cortexsim_rows`
  - `test_target_door_facts_keyed_by_kind`
  - `test_transport_door_without_probe_unknown_not_closed`
  - `test_freshness_reads_traffic_seen_never_verified`
  - `test_freshness_miss_unknown_not_closed`
  - `test_freshness_never_overrides_probe_reading`
  - `test_traffic_seen_not_in_doors_verified_by_probe`
  - `test_readiness_not_degraded_without_transport_targets`
  - `test_configured_transport_target_without_reading_degrades`
  - `test_xdrc_unsupported_host_miss_unknown`
  - `test_wec_without_windows_source_unknown`

**Task 8: Door requirement on seeded results.** No interface change. Add
`test_transport_doors_never_in_requirement` and
`test_non_http_kinds_never_resolve_as_collector_destination`. Agent-run steps
that read `$COLLECTOR_URL` stay route-unknown, as planned.

**Task 9: Exclude silent rows.** No rule change. Add:
- `test_closed_transport_door_excludes_nothing`;
- `test_agent_door_closed_after_xdrc_install_excludes_not_misses`, whose
  evidence carries `XDRC_COINSTALL_SUSPECTED`.

**Task 10: PASS withheld.** No change.

**Task 11: Door watcher.**
- The watcher never probes and never sends a control.
- It re-checks transport doors by the freshness method (two read-only calls
  each, through the quota ledger) and data doors by recent rows, as planned.
  A freshness reading never overrides a probe reading (§6.9).
- `health.not_checked[]` names `POST /api/doors/{key}/probe` for the
  `verified` rung of transport doors, which the watcher cannot reach.
- A lab-only probe watcher is a follow-up and is not built here.

**Task 12: Report and run readout.**
- Add a "Data paths checked (transport, not coverage)" block for the doors
  relevant to the run. It shows the rung label (`traffic seen` or `verified
  (probe)`), the method, nonce, dataset, `sent_from`, latency,
  `queries_issued` and field-name provenance.
- The block is excluded from every count.
- Responses keep `tenant_verified: 0` and add `doors_verified_by_probe` as a
  separate field. The two are never summed, and `traffic seen` is in neither.
- The readout shows `HOST_RUNS_XDR_COLLECTOR_UNSUPPORTED` as a label. Add
  `test_xdrc_run_label_changes_no_score`: verdict, counts and MTTD are
  identical with and without the label.

**Task 13: Get Started (phase 3).**
- The `bvm` step becomes one optional step per Broker VM target door. Each
  shows its state (`traffic seen` or `verified (probe)`) and has Probe (lab
  only) and Check. A POV with no Broker VM target shows the step as not used,
  not as incomplete.
- The `collector` step becomes the `http_collector` targets.
- Delete the 514 copy (`GetStartedView.jsx:258-259`).
- Add an optional XDR Collector step on the beacon's host that shows
  `host_supported`.

**Task 14: Wizard links.**
- In `CollectorTargetsPanel`, "Send test record" stays on every tenant and
  still reads `accepted` (Henry decided, 2026-11-11). A separate "Probe (lab
  tenant only)" control is added; on other tenants it is disabled with
  `DOOR_PROBE_NOT_LAB_TENANT`.
- Stage 6b of the NGFW wizard links to the matching `broker_syslog` door.

### 10. Phasing

| Phase | Delivers | Waits on |
|---|---|---|
| **P1** | Task 7's HTTP-door slice: the probe/check split (probe route, `DoorProbe` table, lab gate, run-live refusal, nonce method), the nonce canary, the scan control, the `wrong_key` control and the control gate, and the recent-rows drop of CortexSim rows. Plus Task 8's two new tests, which need the door `role` field and the HTTP-kind filter in `apply_targets`, so P1 carries those two pieces and nothing else of the transport model. API only. | The `email_emitter` fix landing first, so the first verified HTTP door is not quoted for EMAIL steps. Deathstar. |
| **P2** | The `broker_syslog` kind and door, the transport role's registration, the freshness rung (§6.9) and the Readiness rule, the `configured_facts` fix, beacon probe and control dispatch, the calibration flow, and the `collection_auditing` Q2 with its classifier. `broker_wec` registered with `WEC_NO_WINDOWS_SOURCE` and its freshness rung. Runbook steps 3–4. | Henry deploying the Broker VM. |
| **P3** | The `xdr_collector` kind and door, its freshness rung, the validity facts, the `/etc/panw` snapshot, the agent regression check, and the run label. Runbook step 5. | P1. Two Task 7 pieces outside P1's HTTP slice: the `xdr_agent:<agent>` door check (the `coinstall` fact and the agent-regression check read it) and the `windows_events` producer attestation (the route §7.2 uses for a verified `broker_wec`, and the one that holds an XDR Collector producer to XQL and correlation only, doors spec §4). The bring-up precondition that XDR Collectors are enabled on deathstar (§8 step 1). **Parked until that is confirmed.** |
| **P4** | `broker_ftp`, `broker_files`, `broker_csv` and `broker_db`, with their freshness rungs and controls, plus door-src-01. Runbook steps 6–7. | P2, and the bring-up precondition on the Data Collection add-on (§8 step 1). **Parked until that is confirmed.** |
| **P5** | The WEC probe (`eventcreate`), its control and the Application collection row. | A Windows Server 2012 R2+ host. |

**P1's scope is Claude's call (builder check).** It is the smallest slice that
proves the probe/check split end to end on the one door that needs no new lab
equipment. The console's Probe and Check controls wait for Tasks 13–14; until
then P1 is driven through the API. The rest of Task 7 (data-door checks, the
`xdr_agent` check, attestation) proceeds on the same branch as planned.

**Branches (Claude's call).**
- P1 rides `feature/api-door-checks`, the branch the plan names.
- P2 to P4 each get their own branch: `feature/api-broker-syslog-door`,
  `feature/api-xdr-collector-door` and `feature/api-broker-pull-doors`.
- The runbook goes on `docs/docs-ingestion-doors-lab-run`.
- Tasks 9–12 proceed on top as planned.

### 11. How it is verified

**Red before green.** Every test listed in §9 is written first and seen to
fail against the pre-change code or a stub, per Gate A. Three matter most:
- `test_old_canary_without_nonce_does_not_open` must fail when a
  recent-rows-only check is fed a fixture that holds yesterday's canary.
- `test_target_door_facts_keyed_by_kind` must fail on today's
  `configured_facts`.
- `test_hit_without_control_miss_not_verified` must fail on a check that
  opens on a hit alone.

Every transport is injectable: the syslog socket, the beacon task result, the
XQL runner and the audit-log op. No test touches the network.

**Gates.**
- `make validate`, with `.venv/bin` first on `PATH`. It includes
  `check-ground-truth`.
- The host backend suite.
- `make test-backend-non-utc`, which covers the grace and `sent_at`
  arithmetic.
- vitest and `vite build` for the console.
- The Composer and Get Started e2e specs, run through Chrome on a parallel
  compose project (`:8889`). Never replace `:8888`.
- `make ci` locally before any PR.

The probe route adds one route, so the route count in CLAUDE.md (165) goes up.
Update CLAUDE.md, `docs/reference/api-and-agent-surface.md` and the CHANGELOG.

**The honesty contract, applied.**

- **`tenant-verified` is 0.** Door probes do not change it. A verified door is
  a transport claim, reported as `doors_verified_by_probe` and never merged
  into it. `traffic seen` is counted in neither.
- **Authored is not proven.**
  - A configured target with no probe reads `configured`.
  - An open from recent rows reads `verified (recent rows)`, never `verified
    (probe)`.
  - `http_collector`'s recent-rows reading reads `traffic seen (recent rows)`
    (§7.8; open question 2).
  - A transport door read only by the freshness rung reads `traffic seen`,
    never `verified` (Henry decided, 2026-11-11).
  - An HTTP canary's 2xx reads `accepted`, never `ingested`, off the lab
    tenant (Henry decided, 2026-11-11).
  - A probe hit with no recorded control miss reads `unknown`
    (`NO_CONTROL_MISS_RECORDED`).
  - Field names read "from documentation" until a lab-run doc records them.
- **A zero is degraded, not ok.**
  - No probe reads `unknown` (`NO_PROBE_SENT`).
  - A configured transport target with no reading at `traffic seen` or above
    puts Readiness in a degraded state. A POV with no transport target is not
    degraded: Readiness says in words that no data routes through a Broker VM
    or XDR Collector, and does not hide the zero (Henry decided, 2026-11-11;
    §6.9).
  - A freshness window with no row reads `unknown` (`NO_RECENT_TRAFFIC`),
    never `closed`.
  - A `metrics_view` query that returns no row reads `unknown`.
  - A transport door's `detections_needing` is `null`, not 0.
- **Unrecognised shapes raise.**
  - XQL envelopes go through `result_rows`.
  - A `collection_auditing` row missing its documented columns raises inside
    the check, which records `unknown`.
  - A probe-dataset row with no recognisable field reads
    `PROBE_ROW_SHAPE_UNRECOGNISED`.
  - An unclassified audit description is reported verbatim, never read as "no
    error".
- **Unknown counts are null, not 0.** `records_sent`, `latency_seconds`,
  `rows_seen` and `metrics_last_seen` are `null` when not measured. So is
  `queries_issued` when the ledger cannot say.

**Live.** On deathstar, run one probe and one check per door, plus each door's
red control and one freshness check. Record them in the lab-run doc with:
- the probe id, nonce, `sent_at` and `sent_from`;
- the dataset and the landed `_insert_time`;
- the latency and the column where the nonce was found;
- the Q2 output and the control results.

Until a door has that entry, the console shows `tenant-verified 0` beside it.

### 12. Left to the builder

- **Probe and control dispatch to a beacon without a corpus scenario.** It
  must also be able to tell that a run is live on that beacon, for
  `DOOR_PROBE_RUN_LIVE`. Either a Run-less
  task or a reserved run kind is acceptable. Whichever is used must:
  - seed no Results;
  - never appear as a scenario run or in run statistics;
  - carry no secret on a command line (syslog, file and DB probes need none;
    the HTTP-from-beacon probe waits for step-env);
  - return the exit code, stdout, stderr and the beacon's clock.
- The exact `DoorProbe` and target `params` schemas, and their migration
  (`ALTER TABLE` lines for prod, as with earlier JSON columns).
- XQL timeframe plumbing, and the classifier table for the documented
  `collection_auditing` descriptions.
- The console layout for Probe and Check, the grace countdown, and the method
  labels.
- The control ids and their exact payloads, within §6.4 and §7.
- How the Readiness surface words a transport kind with no target (§6.9).
- How the pinned XDR Collector OS list is stored and cited.

### 13. Claude's calls in one place

1. A single lab gate: the tenant host pin `CORTEXSIM_DOOR_PROBE_TENANTS`.
2. The scan-control nonce is scanned on every check. HTTP's scan control runs
   as Q2 on the first hit.
3. Dedicated `cortexsim_door_*` datasets with a local value scan, instead of
   column filters.
4. Grace defaults: 900 s; interval + 900 s; 1,500 s for CSV. A 600 s
   clock-skew margin.
5. No polling inside a check.
6. Transport doors are diagnostic and never touch scoring. `http_collector`
   stays a data door.
7. The new kinds are collector targets, with a URL scheme per kind and a
   `params` column. The dataset prefix rule is a warning only.
8. Syslog row: TCP 5514, octet framing, CEF, an RFC 3164 header. TLS is
   deferred.
9. The source IP is calibrated by rejection, using `192.0.2.1/32`.
10. door-src-01 is a separate VM; the fallback is pov-target-01.
11. Files and Folders runs in Batch mode. The DB runs with SSL off in the lab.
    CSV on Samba reads `unknown` on a miss.
12. The XDR Collector co-install run label (a label only, call 20), and the
    `/etc/panw` snapshot.
13. Probe steps run as root, outside the identity harness.
14. In phase 1, HTTP probes are sent from SimCore only.
15. Recent rows can still open a data door on customer tenants, labelled by
    method. The recon proposed using recent rows for liveness only. That would
    leave every collector-route detection excluded on customer POVs, where
    probes are not allowed. `http_collector`'s label is the exception (call
    31).
16. The PA-440 leg and a probe watcher are out of scope.

Added after the builder check (2026-11-11):

17. Negative controls are sent by SimCore through the probe route, flagged
    `expected: absent`. An expected miss changes no door state; a control
    that lands records `NEGATIVE_CONTROL_MATCHED`.
18. `verified` requires one recorded control miss per door kind, enforced in
    code, because a probe that cannot return 0 proves nothing.
19. File-applet controls are a distinct payload in a `control/` folder the
    applet does not collect, never a nonce in a file name. Controls take their
    own path and size, so the Files size rule cannot refuse them.
20. `HOST_RUNS_XDR_COLLECTOR_UNSUPPORTED` is a label only and changes no
    score, because scoring changes beyond Tasks 9–10 are a non-goal.
21. P1 is Task 7's HTTP-door slice plus Task 8's two tests (with the `role`
    field and the `apply_targets` kind filter they need). The console
    controls wait for Tasks 13–14. P3 waits on Task 7's `xdr_agent` check and
    attestation.
22. A probe or control is refused while a scenario run is live on the sending
    beacon, so a probe-raised alert cannot be credited to a run.
23. The syslog reachable rung is the `logger` TCP send itself. No `/dev/tcp`
    or `nc` runs on the measured endpoint.
24. The Data Collection add-on and XDR Collectors on deathstar are a
    read-only bring-up precondition. P3 and P4 stay parked until confirmed.
25. Freshness states: a row reads `open` / `traffic seen`; none reads
    `unknown` (`NO_RECENT_TRAFFIC`), never `closed`, because a quiet source
    and a broken path look the same read-only.
26. A freshness reading never overrides a probe-basis reading, because a
    probe names its host and record and traffic seen names neither.
27. CortexSim's own rows (canary, probe, control) never count as recent rows
    or traffic seen, so a send cannot prove itself.
28. One dataset per transport target, to keep the two-call cap. The
    `cortexsim_` prefix warning fires only on a probe-pinned instance, because
    a customer row's dataset never carries it.
29. The XDR Collector freshness Q2 reads `metrics_view`. `metrics_source`'s
    freshness bucket is not queried: Q1 measures it more precisely, and a third
    call breaks the cap.
30. A configured transport target below `traffic seen` degrades Readiness,
    because it was entered for a reason and a silent one is owed an answer.
31. `http_collector`'s recent-rows reading is labelled `traffic seen (recent
    rows)`, because Henry reserved `verified` on that door for the deathstar
    read-back. See open question 2.
32. The canary gains no `door_key`, because Henry named two fields and
    `cortexsim_campaign_id` already identifies the sender.

### 14. Sources

**Repo** (`origin/dev` b1ee28fa)

- `core/eal_simulator/{targets,target_preflight,collector,ingestion_routes,analytics_emitter}.py`
- `core/eal_simulator/plugins/email_emitter.py`
- `core/engine/{ingestion_doors,door_resolution,door_state}.py`
- `core/api/{doors,collector_targets}.py`
- `core/models.py` (`DoorEvent`, `collector_targets`, `QueuedTask`)
- `core/integrations/panos/preflight.py`
- `core/integrations/xsiam/{queries,tenant_url,guards}.py`
- `core/integrations/xsiam/operations/packs/{xql,dataset-management,audit-log}.yml`
- `core/connectors/service.py::resolve_tenant_runner`
- `core/engine/verifier.py::xsiam_rows_runner`
- `ui/src/components/console/{setupProgress.js,GetStartedView.jsx}`
- `docs/superpowers/specs/{2026-11-10-collector-targets,2026-11-10-ngfw-connection-wizard,2026-11-11-ingestion-doors}-design.md`
- `docs/superpowers/plans/2026-11-11-ingestion-doors.md`
- `docs/lab/2026-11-11-persistence-grouping-lab-run.md`

**Vendor** (fetched 2026-11-11). All paths are relative to
`https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/`.

Broker VM applets, under
`cortex-xsiam-data-sources/generic-on-premise-data-collectors/broker-vm-data-collector-applets/`:
- `syslog-collector-applet/activate-syslog-collector.md`
- `syslog-collector-applet/ingest-logs-from-a-syslog-receiver.md`
- `activate-windows-event-collector.md`
- `activate-ftp-collector.md`
- `activate-files-and-folders-collector.md`
- `activate-csv-collector.md`
- `activate-database-collector.md`

XDR Collector, under
`cortex-xsiam-data-sources/generic-on-premise-data-collectors/`:
- `xdr-collectors.md`
- `xdr-collectors/xdr-collector-machine-requirements-and-supported-operating-systems.md`
- `xdr-collectors/xdr-collector-datasets.md`
- `xdr-collectors/xdr-collector-audit-logs.md`
- `xdr-collectors/xdr-collector-profiles/add-an-xdr-collector-profile-for-linux.md`
- `xdr-collectors/manage-xdr-collectors/install-the-xdr-collector-installation-package-for-linux.md`

HTTP collector and ingestion metrics, under `cortex-xsiam-data-sources/`:
- `vendor-specific-data-sources-and-connectors/http-log-collector/set-up-an-http-log-collector-to-receive-logs.md`
- `administration-and-troubleshooting/overview-of-data-ingestion-metrics.md`

Broker VM setup and operations, under `data-management/broker-vm/`:
- `set-up-and-configure-broker-vm.md`
- `set-up-and-configure-broker-vm/broker-vm-image-installations/set-up-broker-vm-on-kvm-using-ubuntu.md`
- `set-up-and-configure-broker-vm/broker-vm-data-collector-applets.md` (the
  source of "Some data collector applets require the Data Collection add-on")
- `troubleshoot-broker-vm-applet-errors.md`
- `monitor-broker-vm-activity.md`

**Other**

- util-linux `logger(1)`: https://man7.org/linux/man-pages/man1/logger.1.html
- Microsoft `eventcreate`:
  https://learn.microsoft.com/windows-server/administration/windows-commands/eventcreate

**Unverified until the first deathstar run settles them**

- the `collector_type` values in `collection_auditing`;
- whether the CEF row expects an RFC 3164 or an RFC 5424 header;
- which column holds the nonce in each dataset;
- whether `_reporting_device_ip` is filled in for syslog rows;
- whether the HTTP collector's host is the same as the API host;
- how XQL reports a dataset that does not exist;
- the real ingestion latency of each door;
- which applets need the Data Collection add-on, and whether it and XDR
  Collectors are enabled on deathstar (a bring-up precondition, §8 step 1);
- whether `logger -T` exits non-zero when its TCP connect fails (the man page
  documents only "exits 0 on success, and >0 if an error occurs");
- whether rows from other Windows collectors also land in
  `microsoft_windows_raw` (§7.2).

## Open questions for Henry

1. **Is the PA-440 → Broker VM syslog leg in scope?** It would give a
   real-device witness that could let the NGFW wizard clear
   `PN_SYSLOG_DELIVERY_UNVERIFIED`. The dataset that NGFW CEF-over-syslog lands
   in is undocumented, so the leg needs its own probe design. Claude's default
   is no.
2. **Should every data door's recent-rows reading read `traffic seen`, not
   only `http_collector`'s?** Henry reserved `verified` on `http_collector` for
   the deathstar read-back, so that door's recent-rows reading is now labelled
   `traffic seen (recent rows)` (call 31). The other data doors still read
   `verified (recent rows)`, as the honesty contract says (§11). Only the
   label differs; the state effect on scoring is the same either way. Claude's
   default is to keep `verified (recent rows)` on the other data doors until
   Henry says otherwise.

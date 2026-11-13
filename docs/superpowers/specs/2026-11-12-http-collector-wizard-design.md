# HTTP collector: Configure steps and component page — design

Date: 2026-11-12, revised 2026-11-13 · Branch: `docs/docs-http-collector-wizard`
(from `origin/dev` 6029122d) · Status: **approved by Henry 2026-11-13** ("Approve
as is"), then **re-aligned the same day to the design track's v26 handoff**
(`docs/design/2026-11-13-v26-design-to-code.md`, branch
`docs/docs-design-sync-1-2-0`, PR #248, not yet on `dev`); the
deltas are listed in §0 for his look. Nothing built.
Every choice Henry did not make is marked **Claude's call:**.

## Executive summary

A consultant who points POVengine's simulated log streams at a customer's
Cortex tenant has to create a custom HTTP log collector in the tenant console,
copy its address and key into POVengine, and hope the data lands where the
detections look for it. Three mistakes are easy and silent: a collector named
so that its data lands in a dataset no detection reads, a log format that
makes the tenant refuse every record, and a key sent the wrong way. Each one
shows up in front of the customer as "Cortex missed it". The design track's
v26 console gives every component one page shape and one Configure checklist;
this design fills in the HTTP collector's. Its six steps ask what data the
collector will carry, tell the consultant exactly what to type into the
tenant, store the address and key, send one test record and a few tagged
example events, and search the tenant read-only to confirm they arrived. It
reports what arrived as a fact ("3 of 3 example records found in
duo_auth_raw"), never as proof that a detection works. It is built on what
POVengine already has plus five small backend additions, and it needs no
tenant write access. Datasets the customer's own firewall already fills are
set up only on the lab tenant. Recommended action: approve the v26 alignment
in §0 and choose the build order (§9).

## Technical detail

### 0. Alignment with design v26 (2026-11-13)

The design track handed off v26 on 2026-11-13. It is now the source of truth
for console IA, copy and colour (`docs/design/DESIGN-SYNC.md`). It already
names an HTTP collector component with six Configure steps, so this design
adopts its shape and keeps its own substance: the data declaration, the
verdicts, the lab-tenant rule, the tagged examples and the arrival count.

| Area | Approved 2026-11-13 | Now, per v26 | v26 ref |
|---|---|---|---|
| Placement | Its own *Manage* page `#/http-collector`, modelled on the Firewall wizard | The **HTTP collector component page** (rail: Onboard & connect › Components ▸ HTTP collector): Overview · Setup · Collector targets. The six steps are its Setup tab, in the shared Configure frame | §4.2, §4.3 |
| Steps | Declare the data · Vendor and product · Stand up the collector · Connect it · Check, then send examples · Confirm arrival | v26's final copy: **Add the collector in Cortex · Choose format and dataset · Generate the key · Add it to POVengine · Send a test event · Confirm it arrived**. The declaration and the vendor/product prefill move into "Choose format and dataset" | §4.3 |
| Who verifies | Implicit | you · you · you · pov · pov · cortex | §4.3 |
| Buttons | Set up with the wizard · Resume · Check reachability · Send test record · Send example events · Move the binding here | The closed verb set: the header's **Configure** and **Test**, and each pov/cortex step's **Test**. Moving an emitter is a checkbox in step 4, because "bind" is not an allowed label | §5 |
| Example events | Their own button after the test record | Part of step 5's Test: the untagged canary first, the tagged examples only after it is accepted | §4.3 |
| Arrival check | Automatic at 90 s, then on demand | On **Test** only. The exact query is shown with a copy button, so a consultant without a tenant credential can run it in Cortex and tick it: "you confirmed", never verified | §4.3, §7 |
| Overview | None | Header status + Test, Services rows, Configuration rows, from real state only (§7 here) | §4.2 |
| Product name in copy | CortexSim | POVengine. The test channel's dataset vendor stays `cortexsim`, matching #227 and the lab bring-up | §0 |
| Look | The Firewall wizard's CSS | v26 tokens (Cortex green primary), StatusIcon, ErrorBadge, Toast, PageIcon | §3, §4.1 |

Unchanged: the verdicts and the `native_dataset` lab-tenant rule (§5.2),
binding rules (§5.4), the minted canary token and the campaign body (§5.5),
the arrival result table (§5.6), and the backend additions (§6), which gain
one column (`last_arrival`) for the Overview.

Where v26's sample values differ from this design, the design wins and the
difference is reported to the design track: compression is **uncompressed**,
not the canvas's "gzip" (every collector emitter sends uncompressed unless an
analytics-family step sets `compress`); the canvas's step-6 query
(`dataset = … | limit 5`) becomes the canary count, because a bare `limit 5`
is satisfied by any earlier send.

**Dependencies.** The Setup tab is drawn by v26 slice 3 (component page shape
+ Configure frame), reached through slice 2 (the Components submenu), and
styled by slice 1 (tokens, StatusIcon, ErrorBadge, Button, Toast, PageIcon).
None of them is on `dev` at f809f140. The backend (§6) depends on none of
them. v26 puts the HTTP collector itself in slice 4.

### 1. Intent

Henry, 2026-11-12: *"we need to ensure we've designed the Custom HTTP event
collector configuration wizard — e.g. walking through the standup/data/vendor
declaration and sending it example events"*. Henry, 2026-11-13: sync it with
the design agent's handoff before it is planned.

Why now: the 2026-11-10 tenant-setup research and the LogSimv2 port measured
the failure modes these steps close:

- the dataset is named from the collector's **Vendor** and **Product**
  (`<vendor>_<product>_raw`); a record's own `dataset` field routes nothing;
- the key goes in `Authorization` as the bare key; a `Bearer` scheme gets 401;
- a JSON record sent to a collector set to CEF, LEEF or Raw gets 500;
- 12 emitters write datasets that differ from what their cards query
  (`/api/eal/data-streams` → `counts.emitter_dataset_mismatch`);
- built-in Analytics never runs on a custom collector's dataset without an XDM
  rule (`ingestion_routes.py`, `http_collector` pipe: `requires_xdm`).

### 2. What exists (verified on `origin/dev` 6029122d; dev is now f809f140, with no change in these files)

| Piece | Where | Used for |
|---|---|---|
| Collector targets | `core/api/collector_targets.py`: `GET/PUT/DELETE /api/collector-targets/{name}`, `POST /{name}/preflight` | Stores URL, dataset, auth header and scheme, bound `plugins`, default flag and `last_check` (`models.py:1231`); the key goes to the vault and is never returned. A plugin can be bound to **one** target only: a second binding is refused with `TARGET_PLUGIN_ALREADY_BOUND` (`collector_targets.py:140-143`) |
| Preflight ladder | `core/eal_simulator/target_preflight.py` | `configured → reachable → accepted`; `accepted` needs the opt-in `write_canary` (one record, no canary field, fixed campaign id: `collector.py:229-242`); the result, with `checked_at`, is stored as the target's `last_check` |
| URL check | `core/eal_simulator/targets.py:112-123` | a path other than `/logs/v1/event`, plaintext http, or a missing key come back as **warnings on a 200**, not errors; the PUT refuses only a bad kind, scheme or host |
| Data-streams catalogue | `GET /api/eal/data-streams` | 34 sources, each with the dataset its cards query, its emitters, and its routes; the `http_collector` generic route says `<vendor>_<product>_raw`, `requires_xdm`. Only the Broker syslog/WEC routes carry a vendor and product |
| Campaigns | `POST /api/eal/campaigns`, `POST /api/eal/campaigns/{id}/launch` | live in-process sends (#231) with per-step delivery accounting (`core/eal_simulator/delivery.py`: 2xx only counts as delivered); a live campaign needs a non-empty `target_allowlist` |
| Canary field | `core/eal_simulator/analytics_emitter.py` | `params.canary_token` (`^[a-z0-9][a-z0-9-]{5,63}$`) adds `cortexsim_canary` to every record of the analytics-streamer emitters. `email_emitter` has its own params model with no such field (`email_emitter.py:268`), so a token passed to it is silently dropped |
| Read-only XQL | `POST /api/xsiam/tenants/{name}/xql`, `GET /api/xsiam/tenants/{name}/xql/{query_id}` | refuses write stages before the credential is decrypted; needs the `xql` capability; reports `queries_issued`. The GET returns the raw envelope; a PENDING reply has no rows |
| Doors readiness | `GET /api/doors/readiness` | per-scenario counts and door ids; **no dataset field**, so it cannot list a dataset's detections |
| Operator confirmations | `ui/src/components/console/setupProgress.js` (`readConfirmed` / `writeConfirmed`), `useSetupProgress.js` | how Get started records "you confirmed" today |
| Today's console | `ui/src/components/console/CollectorTargetsPanel.jsx` (Tenant › Collector targets) | a form plus "Check reachability" and "Send test record"; v26 makes it the component's **Collector targets** tab |

### 3. Goals, non-goals, anti-goals

**Goals**

1. A consultant who has never created an HTTP collector ends with a working
   one whose dataset matches what the declared data source's detections read,
   or is told plainly that no HTTP collector can feed them.
2. Every value the consultant must type into the tenant is shown, with a copy
   button, derived from the declaration.
3. The last step answers one question with a count: did *these* example
   records, sent just now, land in *this* dataset?
4. Built on the existing APIs above plus the additions in §6; no new tenant
   capability and no tenant write.

**Non-goals**

- Door state. These steps write no `DoorEvent` and never set a door `open`.
  The door probe, its negative control and the `verified` label stay with
  #227 (ingestion test path, P1). **Claude's call:** step 6 links to the door
  once P1 exists, rather than duplicating it.
- Creating the collector in the tenant through an API. POVengine has no write
  path to the tenant and keeps none (v26 §10.6: a `pov` step means *check*).
- XDM rules, parsing rules, Broker VM and XDR Collector routes.
- The v26 slices this design depends on (§0). They are built in their own
  order, one PR each.
- Gzip. **Claude's call:** the emitters send uncompressed JSON by default, so
  step 2 says *uncompressed*.

**Anti-goals** (each makes the steps a failure even if they run):

- An arrival count shown as detection coverage, or next to coverage numbers.
- A passed last step when the read-back was never run (no tenant credential,
  no `xql`, quota spent). That reads **Can't check**, never Passed.
- A count that can only ever be positive. The query filters on a token minted
  for this send, so an earlier send cannot satisfy it.
- The key shown, logged or returned anywhere after it is pasted.
- Telling the consultant a source "works" over HTTP when its cards query a
  dataset an HTTP collector cannot fill.
- Seed values on screen (v26 §0.1): every row reads real state, "you
  confirmed", or Unknown.

### 4. Approaches

v26 settled the placement; the options this design weighed before it:

| | Approach | Outcome |
|---|---|---|
| **A (v26)** | The HTTP collector component page; the six steps are its Setup tab in the shared Configure frame; Collector targets is its management tab | Picked by the design handoff. One shape for all eight components; the existing panel keeps its job as the management tab |
| B | A separate *Manage* page modelled on the Firewall wizard (approved 2026-11-13, before v26) | Superseded: v26 retires per-component page shapes |
| C | A modal inside `CollectorTargetsPanel` | Rejected earlier: too dense, cannot be linked to |

### 5. The six steps

A checklist in the Configure frame (v26 §4.3): each step has a title, a
one-paragraph why, where it happens, the fields, an optional copyable
command, and who verifies it. Each step reads its state from the API or the
confirmation store, so **Configure** lands on the first step that is not done.
Until step 4 stores the target, the declaration from step 2 is held in the
page's query string (`?source=&vendor=&product=`), so a reload keeps it
(**Claude's call**).

#### 5.1 Add the collector in Cortex — verified by **you**

Where: Cortex › Settings › Data Sources & Integrations › + Add New › search
**HTTP** › Add. Field: Name, suggested `povengine <vendor> <product>` once
step 2 is filled (copy). The step says the name does not route data. The
consultant ticks it.

#### 5.2 Choose format and dataset — verified by **you**, with POVengine's help

The consultant first picks **one** data source from the data-streams
catalogue (sources with at least one collector emitter), or *POVengine test
channel* (vendor `cortexsim`, product `door_http`) for a plain delivery test.
The test channel has no emitter of its own: the consultant picks any one
taggable emitter for its example events, and they land in
`cortexsim_door_http_raw` because the collector, not the record, names the
dataset (the lab bring-up's Phase A does exactly this).

For the chosen source the step shows:

- the emitters that produce it, which step 4 sends to this target;
- the dataset its cards query, and the scenarios and detections behind it,
  with each detection's type (new read-only endpoint, §6);
- **the verdict for this route**, one of:

| Verdict | When | Message |
|---|---|---|
| `matches` | the cards query a `*_raw` dataset and a vendor/product split gives that exact name | "Name the collector `<vendor>` / `<product>` and its data lands in `<dataset>`, the dataset these detections read." |
| `xql_only` | as `matches`, but some of the source's detections are Analytics, ABIOC or BIOC | "XQL and correlation rules on `<dataset>` can fire. Analytics and ABIOC need an XDM rule for this dataset first; BIOC never fires on a custom collector." |
| `cannot_feed` | the cards query a dataset that is not `<vendor>_<product>_raw` (for example `cloud_audit_logs`, `xdr_data`) | "An HTTP collector cannot fill `<dataset>`. Records will land in `<vendor>_<product>_raw`, and only queries written against that dataset can see them." |
| `name_disputed` | the card dataset is one of the 22 names a tenant does not create (`okta_sso` where the tenant has `okta_sso_raw`); D-2 is open | "These detections query `<card dataset>`; a tenant writes `<tenant dataset>`. Records will land in `<vendor>_<product>_raw`; whether the detections see them depends on D-2." **Claude's call:** state the conflict, pick no side |
| `native_dataset` | the card dataset is one a native tenant pipe fills: `panw_ngfw_traffic_raw`, `panw_ngfw_threat_raw`, `panw_ngfw_globalprotect_raw` | see below |

**Henry decided (2026-11-13): `native_dataset` is allowed on the lab tenant
only.** On any other tenant the step shows the issue line "`<dataset>` is
filled by the tenant's own firewall pipe. A custom collector named into it
would mix synthetic records into the customer's real firewall data, so
POVengine does not set one up outside the lab tenant.", cannot be ticked, and
step 4's Test refuses to save. On the lab tenant every later step carries the
line "writes into the native `<dataset>`".

How the lab tenant is recognised (**Claude's call**): a setting listing the
lab tenants' API hosts, the same `CORTEXSIM_DOOR_PROBE_TENANTS` the #227 plan
introduces for door probes, set in the lab `.env` only; this build adds it if
#227 has not. The collector URL's host must equal one of them. Whether a
tenant's HTTP collector host equals its API host is unverified (#227 spec
§14), so any mismatch reads **not the lab tenant**, and the step stops. A
wrong guess blocks the lab; it never lets synthetic records into a customer's
dataset.

**Henry decided (2026-11-13): every other verdict is offered on any tenant.**
`cannot_feed` and `name_disputed` continue as a **delivery test**
(**Claude's call**): the verdict is repeated on every later step, and the
arrival result reads "delivery only: these detections will not see
`<dataset>`". Measured on the catalogue (2026-11-12): of the 20 sources with a
collector emitter, 8 read `matches` or `xql_only` (4 of them the native
`panw_*` datasets), 9 `name_disputed` and 3 `cannot_feed`.

Then, the values to type into the same Cortex dialog, each with a copy button:

- Compression: **uncompressed**.
- Log Format: **JSON**, with the reason: every POVengine emitter sends JSON,
  and a collector set to anything else answers 500.
- Vendor / Product: prefilled, editable, with the resulting dataset name shown
  live. **Claude's call:** the prefill splits the card dataset (minus `_raw`)
  at its first underscore (`duo_auth_raw` → `duo` / `auth`); when the card
  dataset is not `*_raw` (`cloud_audit_logs`), it splits the source key
  (`aws_audit_log` → `aws` / `audit_log`). The catalogue's route vendor/product
  pairs (Cisco ASA, Cisco Firepower, Fortinet Fortigate, Microsoft Windows)
  are hints only, never prefilled. Editing either field re-runs the verdict.

One collector carries one vendor/product, so one declaration per collector.

#### 5.3 Generate the key — verified by **you**

**Save & Generate Token**; copy the key now, the tenant shows it once. Copy
the endpoint URL from the Custom Collectors page
(`https://api-<tenant>/logs/v1/event`).

#### 5.4 Add it to POVengine — verified by **POVengine**

Fields: target name (default `<vendor>-<product>`), the URL, and the key
(write-only, as today), plus a "Use as the default target" checkbox. Fixed,
shown, not editable on this kind: header `Authorization`, scheme empty, kind
`xsiam_http_collector`, dataset from step 2.

The step lists the step-2 emitters, each with a checkbox **"Send to this
target"** (**Claude's call**, replacing the "Move the binding here" button):

- an emitter no other target serves starts ticked;
- an emitter another target serves starts unticked and says "sends to
  `<other target>` now"; ticking it shows "`<other target>` stops receiving
  `<emitter>`", and the move is two PUTs (unbind there, bind here) when the
  step's Test runs;
- the example events in step 5 always route by an explicit
  `collector_target`, so they never depend on these boxes.

**Test** (**Claude's call:** a pov step that stores values saves them on
Test) does the existing `PUT /api/collector-targets/{name}` with the
`declaration` (§6), then the no-write preflight. Its 422s show as a crit
ErrorBadge; its `warnings` (path not `/logs/v1/event`, plaintext http, no key)
show as a warn ErrorBadge, and the step is not passed while one stands,
without new server-side validation. Passed = saved with no warning and
`reachable`.

The checkbox cannot send one emitter's Okta records to one collector and its
Entra records to another. That is a routing gap in the resolver
(`targets.py`), listed as a follow-up.

#### 5.5 Send a test event — verified by **POVengine**

Fields: the emitters to send an example from (default: the ones step 4 sends
here), one event pattern each (default: the first `GET /api/eal/plugins/{name}`
lists), and **Your name** (required, becomes `authorized_by`). Before Test,
the step says what it writes: "one untagged record, then one tagged example
per ticked emitter, into `<dataset>`".

**Test** runs, in order, stopping at the first failure:

1. `POST …/preflight` with `write_canary: true`. A refusal shows its delivery
   code as an ErrorBadge with the remediation: 401 key or disabled collector ·
   404 URL · 413 too large · 429 rate limit · 500 log format.
2. Mints one canary token (`wiz-<target>-<yyyymmddhhmm>-<4 hex>`, fitting
   `^[a-z0-9][a-z0-9-]{5,63}$`), creates and launches one campaign: one step
   per ticked emitter, `iterations: 1`, `canary_token`,
   `collector_target: <name>`, `provider` from the declared source when the
   emitter has one (Okta for `okta`, Entra for `azure_signin_log`, …);
   `target_allowlist: [<collector host>]`, `simulation_authorized: true`,
   `authorized_by`. Every collector emitter is taggable once `email_emitter`
   accepts `canary_token` (§6).

Passed = the canary accepted and every example delivered (2xx). Each emitter's
row shows attempted, delivered, and the code with its remediation when they
differ; the step adds "Delivered is not ingested: confirm in the next step".

Command (copyable), for checking the path **from a jumpbox inside the
customer network**, which a send from SimCore does not prove:
`curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$URL" -H "Authorization: $KEY" -H "Content-Type: application/json" -d '{"povengine":"canary"}'`.
The key stays in `$KEY`, never in the shown text.

#### 5.6 Confirm it arrived — verified by **Cortex**

The query, shown with a copy button:

```
dataset = <dataset>
| filter cortexsim_canary = "<token>"
| comp count() as found, min(_insert_time) as first_seen, max(_insert_time) as last_seen
```

**Test** runs it through the arrival endpoint (§6), which uses the one
unwrapper (`queries.py::result_rows`), so the browser never counts rows and a
PENDING reply never reads as 0. Each Test issues at most two tenant calls
(start, results) and shows its `queries_issued`. It needs a tenant
integration with the `xql` capability: with exactly one it is used; with
several the consultant picks.

| Result | Reads |
|---|---|
| `found` = delivered | **Passed: N of N example records in `<dataset>`**, with ingest latency (`first_seen` − send time) |
| 0 < `found` < delivered | **Partly arrived: k of N**; Test again later |
| `found` = 0 | **Not found yet** in `<dataset>` after `<t>` s, with the causes in order: wrong vendor/product (the data landed elsewhere), ingestion delay, a collector disabled after the send |
| PENDING | **Still running**; Test again |
| no credential, no `xql`, quota, 401, 5xx, unrecognised reply | **Can't check**, with the reason; never a count |

With **Can't check**, the consultant may run the query in Cortex themselves
and tick the step; it then reads "you confirmed", never Passed. **Claude's
call:** no automatic check (v26: Test is the action), and no tenant-wide
search on a miss, because it costs quota on every miss.

What it proves: these records, sent from SimCore just now, were ingested into
`<dataset>`. What it does not prove: that a jumpbox inside the customer network
can reach the collector (step 5's command does), that XDM maps the dataset, or
that any detection fires. Those three lines are printed under every result.

### 6. Backend changes

Small, read-only toward the tenant, and additive. **Claude's call** for each:

1. **`declaration` on the target.** A nullable JSON column on
   `collector_targets`, `{source_key, vendor, product, verdict}`, set by the
   PUT; the `cgo_anchor` / `composer_layout` pattern (an additive `_migrate_*`
   step). Shared emitters mean `dataset` + `plugins` cannot identify the
   source on a resume.
2. **`last_arrival` on the target.** A nullable JSON column the arrival
   endpoint writes with each counted or Can't-check result
   (`{state, found, delivered, token, checked_at, reason}`), so the Overview
   (§7) shows the last arrival without a tenant call.
3. **`GET /api/eal/data-streams/{source_key}/detections`.** The scenarios,
   detection ids and detection types whose card dataset is that source's,
   from the loaded scenarios and cards. Step 2's list and the `xql_only`
   verdict need it; `/api/doors/readiness` has no dataset field.
4. **`POST /api/collector-targets/{name}/arrival`** with `{token, tenant,
   since, delivered}`. Starts the step-6 query, or with `{query_id}` reads it;
   unwraps through `result_rows`; returns `{state: counted | pending |
   not_checked, found, first_seen, last_seen, reason, queries_issued}`. Same
   capability check and quota ledger as `/api/xsiam/tenants/{name}/xql`.
5. **`email_emitter` accepts `canary_token`** and writes `cortexsim_canary`,
   like the analytics-streamer emitters. A functional fix: without it,
   arrival for `microsoft_365_emails` reads 0 forever.

### 7. The rest of the component page (v26 §4.2)

Real state only; nothing here calls the tenant on page load (v26: no polling
history).

**Header:** PageIcon, "HTTP collector", StatusIcon + word, **Configure**,
**Test**. Status: Off when no `xsiam_http_collector` target exists ("Configure
it to unlock its scenarios."); Up when the default target's `last_check` is
`accepted` and its `last_arrival` is counted with `found > 0`; Degraded when
either is missing or partial; Down when `last_check` failed. Header **Test**
runs the no-write preflight on the default target.

**Services** (one row per `xsiam_http_collector` target; **Claude's call**,
since v26 draws one):

| Row | Reads | Drills into |
|---|---|---|
| Collector endpoint `<target>` | `last_check`: "accepted 12m ago" · "reachable, nothing written yet" · the failure code | step 5 |
| Dataset `<dataset>` | `last_arrival`: "N of N arrived, 1h ago" · "not found" · Can't check · Unknown when never tested | step 6 |

**Configuration** (failing items first):

| Item | OK / Missing / Changed / Can't check from | Fix › |
|---|---|---|
| Collector exists in Cortex | `last_check` accepted → OK; else "you confirmed" from step 1, else Missing | step 1 |
| Log format is JSON | `last_check` refused with 500 → Missing; else "you confirmed" from step 2 | step 2 |
| Dataset matches the declared detections | `declaration.verdict`: `matches` → OK · `xql_only` → Changed ("XQL only") · others → Missing with the verdict's message | step 2 |
| A modeling rule maps the dataset to XDM | No SimCore read exists → **Can't check**; shown only for `xql_only` | step 2 |
| Dataset receives the example records | `last_arrival` | step 6 |

v26's own drill-in mapping for these rows is the design track's reading; this
table replaces it for the HTTP collector.

**Collector targets tab:** today's `CollectorTargetsPanel`, unchanged in
behaviour; its buttons take v26 labels when slice 1 lands.

"you confirmed" ticks reuse `readConfirmed` / `writeConfirmed`, keyed
`http:<target>:<step>` (**Claude's call**).

### 8. Tests

Gate A: each test below is observed failing before its code exists.

- Step 2 verdicts: `matches`, `xql_only`, `cannot_feed` and `name_disputed`
  from catalogue fixtures, including `cloud_audit_logs` (cannot feed),
  `duo_auth_raw` (matches) and `okta_sso` (disputed); the split and live
  dataset preview; editing re-runs the verdict; a reload keeps the
  declaration from the query string.
- `native_dataset`: stops at step 2 when the collector host is not in
  `CORTEXSIM_DOOR_PROBE_TENANTS`, when the setting is empty, and when the host
  differs only by case or port; continues with the line when it matches.
- Step 4 sends header `Authorization`, empty scheme, the `declaration`, and
  never echoes the key; a PUT warning holds the step open; an emitter served
  elsewhere starts unticked, names its target, and moves only when ticked.
- Step 5's Test stops after a refused canary and sends no campaign; the
  campaign body has one step per ticked emitter, the minted token on each,
  `target_allowlist` = the collector host, and the consent fields.
- Step 6: every row of the result table, including each **Can't check** cause
  and PENDING; a zero renders "Not found yet", never Passed; a manual tick
  reads "you confirmed".
- §6: `declaration` and `last_arrival` survive a restart and an old row reads
  null; the detections-by-source read returns each detection's type; the
  arrival endpoint returns `pending` (not 0) for a PENDING reply,
  `not_checked` for each refusal, writes `last_arrival`, and reports
  `queries_issued`; `email_emitter` records carry `cortexsim_canary`.
- §7: header status for each state; a Configuration row never reads OK from
  an unverified source; no tenant call on page load.
- Labels: no button outside v26's verb set (an assertion over the rendered
  page).
- A browser test (`ui/tests/e2e/`) walking all six steps against a stubbed
  API, because layout and focus order cannot be proven in jsdom.

### 9. Open questions for Henry

Settled on 2026-11-13: **native datasets** are lab tenant only; **every other
source** is offered on any tenant.

1. **Build order.** The steps and the Overview need v26 slices 1–3, which are
   not on `dev`. **Claude's recommendation:** build the backend (§6) now as its
   own PR, since nothing in it waits on the design; build the UI (§5, §7) as
   the HTTP collector part of v26 slice 4 once slice 3's Configure frame has
   landed, so the frame is built once. The alternatives: build the Configure
   frame here first (two people could end up building it), or wait and build
   everything in one change after slice 3.

D-2 (from the LogSimv2 port) stays open elsewhere; until it is decided step 2
reports `name_disputed` for the 9 affected sources.

### 10. Claude's calls in one place

- No door state; step 6 links to #227's door once it exists (§3).
- Uncompressed JSON only (§3, §5.2), against the canvas's "gzip" sample (§0).
- The test channel borrows one taggable emitter (§5.2).
- Non-matching verdicts continue as a delivery test (§5.2).
- Prefill splits the card dataset or the source key; route pairs are hints
  (§5.2).
- The declaration rides the query string until step 4 stores it (§5).
- "Send to this target" checkboxes instead of a move button; examples route by
  explicit target (§5.4).
- Step 4's Test saves, then checks; PUT warnings hold the step open (§5.4).
- Step 5's Test sends the untagged canary first and the tagged examples only
  after it is accepted; the jumpbox `curl` keeps the key in `$KEY` (§5.5).
- `provider` set from the declared source (§5.5).
- Arrival on Test only; no tenant-wide search on a miss; a manual tick reads
  "you confirmed" (§5.6).
- The lab tenant is recognised by `CORTEXSIM_DOOR_PROBE_TENANTS` and an exact
  host match; any doubt reads "not the lab tenant" (§5.2).
- Five backend additions, `last_arrival` among them for the Overview (§6).
- One Services row pair per target; the Configuration rows and their sources
  in §7 replace the canvas's drill-in reading.
- Confirmation ticks keyed `http:<target>:<step>` in the existing store (§7).
- Follow-up, not in this build: per-source routing for an emitter that feeds
  several sources (§5.4).

### 11. Sources

Design: `docs/design/2026-11-13-v26-design-to-code.md` (v26 handoff, §3,
§4.1–4.3, §5, §7, §9–11) and its canvas
https://claude.ai/artifact/UtUX2baFiCdFAsrkQi5tcb (page "POVengine Console":
`SETUP.http`, `STEP_INFO['http:3']`, `['http:4']`, `SERVICES.http`,
`MANAGE.http`, `SERVICE_TARGET.http`, `CONFIG_TARGET.http`), read 2026-11-13;
`docs/design/DESIGN-SYNC.md`.

Vendor, fetched 2026-11-12: *Set up an HTTP log collector to receive logs*,
`https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/http-log-collector/set-up-an-http-log-collector-to-receive-logs`
(fields, `<Vendor>_<Product>_raw`, bare-key `Authorization`, the 200/401/404/
413/429/500 table, 10 MiB body, newline-separated records).

Repo: `docs/superpowers/specs/2026-11-10-tenant-setup-research/`,
`docs/superpowers/specs/2026-11-10-logsim-v2-catalog-port-design.md`,
`docs/superpowers/specs/2026-11-10-collector-targets-design.md`,
`docs/superpowers/specs/2026-11-10-ngfw-connection-wizard-design.md`,
`docs/superpowers/specs/2026-11-11-ingestion-test-path-design.md`.

Unverified until the first lab run: whether the tenant lowercases vendor and
product in the dataset name; the real ingest latency; whether
`cortexsim_canary` survives ingestion as a top-level field; whether a
collector set to gzip refuses an uncompressed body.

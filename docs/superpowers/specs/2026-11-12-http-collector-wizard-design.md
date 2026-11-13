# HTTP log collector wizard — design

Date: 2026-11-12 · Branch: `docs/docs-http-collector-wizard` (from `origin/dev`
6029122d) · Status: **draft for Henry's approval**. Nothing built.
Every choice Henry did not make is marked **Claude's call:**.

## Executive summary

A consultant who points CortexSim's simulated log streams at a customer's
Cortex tenant has to create a custom HTTP log collector in the tenant console,
copy its address and key into CortexSim, and hope the data lands where the
detections look for it. Three mistakes are easy and silent: a collector named
so that its data lands in a dataset no detection reads, a log format that
makes the tenant refuse every record, and a key sent the wrong way. Each one
shows up in front of the customer as "Cortex missed it". This design adds a
step-by-step wizard to the CortexSim console. It asks what data the collector
will carry, tells the consultant exactly what to type into the tenant, stores
the address and key, sends one test record, sends a few tagged example events
from the matching simulators, and searches the tenant read-only to confirm
they arrived. It reports what arrived as a fact ("3 of 3 example records found
in duo_auth_raw"), never as proof that a detection works. It is built on what
CortexSim already has plus four small additions, and it needs no tenant write
access. Datasets the customer's own firewall already fills are set up only
on the lab tenant. Recommended action: approve the design and answer its one
open question, then build it as one change.

## Technical detail

### 1. Intent

Henry, 2026-11-12: *"we need to ensure we've designed the Custom HTTP event
collector configuration wizard — e.g. walking through the standup/data/vendor
declaration and sending it example events"*.

It is the HTTP-collector counterpart of the NGFW connection wizard (#186,
`ui/src/components/console/FirewallView.jsx`): one page in *Manage*, steps
that can each be used alone, every result saying what it proves and what it
does not.

Why now: the 2026-11-10 tenant-setup research and the LogSimv2 port measured
the failure modes this wizard closes:

- the dataset is named from the collector's **Vendor** and **Product**
  (`<vendor>_<product>_raw`); a record's own `dataset` field routes nothing;
- the key goes in `Authorization` as the bare key; a `Bearer` scheme gets 401;
- a JSON record sent to a collector set to CEF, LEEF or Raw gets 500;
- 12 emitters write datasets that differ from what their cards query
  (`/api/eal/data-streams` → `counts.emitter_dataset_mismatch`);
- built-in Analytics never runs on a custom collector's dataset without an XDM
  rule (`ingestion_routes.py`, `http_collector` pipe: `requires_xdm`).

### 2. What exists (verified on `origin/dev` 6029122d)

| Piece | Where | Used for |
|---|---|---|
| Collector targets | `core/api/collector_targets.py`: `GET/PUT/DELETE /api/collector-targets/{name}`, `POST /{name}/preflight` | Stores URL, dataset, auth header and scheme, bound `plugins`, default flag; the key goes to the vault and is never returned. A plugin can be bound to **one** target only: a second binding is refused with `TARGET_PLUGIN_ALREADY_BOUND` (`collector_targets.py:140-143`) |
| Preflight ladder | `core/eal_simulator/target_preflight.py` | `configured → reachable → accepted`; `accepted` needs the opt-in `write_canary` (one record, no canary field, fixed campaign id: `collector.py:229-242`) |
| URL check | `core/eal_simulator/targets.py:112-123` | a path other than `/logs/v1/event`, plaintext http, or a missing key come back as **warnings on a 200**, not errors; the PUT refuses only a bad kind, scheme or host |
| Data-streams catalogue | `GET /api/eal/data-streams` | 34 sources, each with the dataset its cards query, its emitters, and its routes; the `http_collector` generic route says `<vendor>_<product>_raw`, `requires_xdm`. Only the Broker syslog/WEC routes carry a vendor and product |
| Campaigns | `POST /api/eal/campaigns`, `POST /api/eal/campaigns/{id}/launch` | live in-process sends (#231) with per-step delivery accounting (`core/eal_simulator/delivery.py`: 2xx only counts as delivered); a live campaign needs a non-empty `target_allowlist` |
| Canary field | `core/eal_simulator/analytics_emitter.py` | `params.canary_token` (`^[a-z0-9][a-z0-9-]{5,63}$`) adds `cortexsim_canary` to every record of the analytics-streamer emitters. `email_emitter` has its own params model with no such field (`email_emitter.py:268`), so a token passed to it is silently dropped |
| Read-only XQL | `POST /api/xsiam/tenants/{name}/xql`, `GET /api/xsiam/tenants/{name}/xql/{query_id}` | refuses write stages before the credential is decrypted; needs the `xql` capability; reports `queries_issued`. The GET returns the raw envelope; a PENDING reply has no rows |
| Doors readiness | `GET /api/doors/readiness` | per-scenario counts and door ids; **no dataset field**, so it cannot list a dataset's detections |
| Today's console | `ui/src/components/console/CollectorTargetsPanel.jsx` (Tenant › Collector targets) | a form plus "Check reachability" and "Send test record" |

### 3. Goals, non-goals, anti-goals

**Goals**

1. A consultant who has never created an HTTP collector ends with a working
   one whose dataset matches what the declared data source's detections read,
   or is told plainly that no HTTP collector can feed them.
2. Every value the consultant must type into the tenant is shown, with a copy
   button, derived from the declaration.
3. The last step answers one question with a count: did *these* example
   records, sent just now, land in *this* dataset?
4. Built on the existing APIs above plus the four additions in §6; no new
   tenant capability and no tenant write.

**Non-goals**

- Door state. The wizard writes no `DoorEvent` and never sets a door `open`.
  The door probe, its negative control and the `verified` label stay with
  #227 (ingestion test path, P1). **Claude's call:** the wizard links to the
  door once P1 exists, rather than duplicating it.
- Creating the collector in the tenant through an API. CortexSim has no write
  path to the tenant and keeps none.
- XDM rules, parsing rules, Broker VM and XDR Collector routes.
- Gzip. **Claude's call:** the emitters send uncompressed JSON, so the wizard
  tells the consultant to pick *uncompressed*.

**Anti-goals** (each makes the wizard a failure even if it runs):

- An arrival count shown as detection coverage, or next to coverage numbers.
- A green last step when the read-back was never run (no tenant credential,
  no `xql`, quota spent). That reads **not checked**, never ok.
- A count that can only ever be positive. The query filters on a token minted
  for this send, so an earlier send cannot satisfy it.
- The key shown, logged or returned anywhere after it is pasted.
- Telling the consultant a source "works" over HTTP when its cards query a
  dataset an HTTP collector cannot fill.

### 4. Approaches

| | Approach | Trade-off |
|---|---|---|
| **A (picked)** | A new *Manage* destination, **HTTP collector** (`#/http-collector`), one page with six steps, built like the Firewall wizard; Tenant › Collector targets gains "Set up with the wizard" and a per-row "Resume" | Room for the per-step explanations; deep-linkable; the existing panel stays for editing |
| B | A modal inside `CollectorTargetsPanel` | Smallest change, but the panel is already dense and a modal cannot be linked to from Get Started or a launch blocker |
| C | A Get Started step | Get Started is being rebuilt from `/api/doors` (#227 Task 27); it should link to the wizard, not contain it |

**Claude's call:** A, because it matches the one wizard pattern the console
already has and can be linked from both Get Started and the launch gate.

### 5. The six steps

Each step reads its state from the API, so a reload or a resume lands on the
first step that is not done.

#### 5.1 Declare the data

The consultant picks **one** data source from the data-streams catalogue
(sources with at least one collector emitter), or *CortexSim test channel*
(vendor `cortexsim`, product `door_http`) for a plain delivery test. The test
channel has no emitter of its own: the consultant picks any one taggable
emitter for its example events, and they land in `cortexsim_door_http_raw`
because the collector, not the record, names the dataset (the lab bring-up's
Phase A does exactly this).

For the chosen source the step shows:

- the emitters that produce it, which become the target's bound `plugins`;
- the dataset its cards query, and the scenarios and detections behind it,
  with each detection's type (new read-only endpoint, §6);
- **the verdict for this route**, one of:

| Verdict | When | Message |
|---|---|---|
| `matches` | the cards query a `*_raw` dataset and a vendor/product split gives that exact name | "Name the collector `<vendor>` / `<product>` and its data lands in `<dataset>`, the dataset these detections read." |
| `xql_only` | as `matches`, but some of the source's detections are Analytics, ABIOC or BIOC | "XQL and correlation rules on `<dataset>` can fire. Analytics and ABIOC need an XDM rule for this dataset first; BIOC never fires on a custom collector." |
| `cannot_feed` | the cards query a dataset that is not `<vendor>_<product>_raw` (for example `cloud_audit_logs`, `xdr_data`) | "An HTTP collector cannot fill `<dataset>`. Records will land in `<vendor>_<product>_raw`, and only queries written against that dataset can see them." |

A fourth case comes from the 2026-11-10 tenant-setup research: 22 card
dataset names are not what a tenant creates (`okta_sso` where the tenant has
`okta_sso_raw`). Henry's decision D-2 (rename the cards, or join through a
`tenant_dataset`) is still open. Until it is settled, a source whose card
dataset is on that list reads `name_disputed`: "These detections query
`<card dataset>`; a tenant writes `<tenant dataset>`. Records will land in
`<vendor>_<product>_raw`; whether the detections see them depends on D-2."
**Claude's call:** the wizard states the conflict and does not pick a side.

A fifth case: the card dataset is one a native tenant pipe already fills, such
as `panw_ngfw_traffic_raw`, `panw_ngfw_threat_raw` and
`panw_ngfw_globalprotect_raw`. They pass the `matches` rule, but naming a
custom collector into them writes synthetic records into the firewall's own
dataset. These read `native_dataset`.

**Henry decided (2026-11-13): `native_dataset` is allowed on the lab tenant
only.** On any other tenant the wizard stops at 5.2 with: "`<dataset>` is
filled by the tenant's own firewall pipe. A custom collector named into it
would mix synthetic records into the customer's real firewall data, so the
wizard does not set one up outside the lab tenant." On the lab tenant every
later step carries the banner "writes into the native `<dataset>`".

How the lab tenant is recognised (**Claude's call**): a setting listing the
lab tenants' API hosts, the same `CORTEXSIM_DOOR_PROBE_TENANTS` the #227 plan
introduces for door probes, set in the lab `.env` only; this build adds it if
#227 has not. The collector URL's host must equal one of them. Whether a
tenant's HTTP collector host equals its API host is unverified (#227 spec
§14), so any mismatch reads **not the lab tenant**, and the step stops. A
wrong guess blocks the lab; it never lets synthetic records into a customer's
dataset.

**Claude's call:** every other verdict goes on to the next steps. `cannot_feed` and `name_disputed` continue as a **delivery test**: the
verdict is shown again on every later step, and the arrival result reads
"delivery only: these detections will not see `<dataset>`". Measured on the
catalogue (builder check, 2026-11-12): of the 20 sources with a collector
emitter, 8 read `matches` or `xql_only` (4 of them are the native `panw_*`
datasets), 9 `name_disputed` and 3 `cannot_feed`. Stopping at a non-match
would leave the wizard useful for 4 sources.

One collector carries one vendor/product, so one declaration per collector.

#### 5.2 Vendor and product

Prefilled from the declaration, editable, with the resulting dataset name
shown live. **Claude's call:** the prefill always splits the card dataset
(minus `_raw`) at its first underscore (`duo_auth_raw` → `duo` / `auth`). When
the card dataset is not `*_raw` (`cloud_audit_logs`), it splits the source key
instead (`aws_audit_log` → `aws` / `audit_log`). The catalogue's route
vendor/product pairs (Cisco ASA, Cisco Firepower, Fortinet Fortigate,
Microsoft Windows) are shown as hints only, never prefilled, because each
names a dataset other than the card's and one of them is the tenant side of
D-2. The consultant confirms. Editing either field re-runs the 5.1 verdict.

#### 5.3 Stand up the collector in the tenant

Read-only instructions with a copy button per value, nothing sent:

1. Settings › Data Sources & Integrations › + Add New › search **HTTP** › Add.
2. Name: `cortexsim <vendor> <product>` (copy).
3. Compression: **uncompressed**.
4. Log Format: **JSON**. The step says why: every CortexSim emitter sends
   JSON, and a collector set to anything else answers 500.
5. Vendor / Product: the 5.2 values (copy each).
6. **Save & Generate Token**. Copy the key now: the tenant shows it once.
7. Copy the endpoint URL from the Custom Collectors page
   (`https://api-<tenant>/logs/v1/event`).

#### 5.4 Connect it to CortexSim

Fields: target name (default `<vendor>-<product>`), the URL, and the key
(write-only, as today). Fixed, shown, not editable on this kind: header
`Authorization`, scheme empty, kind `xsiam_http_collector`, dataset from 5.2,
`plugins` from 5.1. A checkbox makes it the default target. Save is the
existing `PUT /api/collector-targets/{name}`. Its 422s show inline as errors;
its `warnings` (path not `/logs/v1/event`, plaintext http, no key) show inline
as warnings, and the step is not marked done while a warning stands, without
adding any new server-side validation.

**Binding (Claude's call).** A plugin can serve one target, and several
emitters feed several sources: `idp_signin_emulator` feeds 5 (Okta, two Entra,
two Google), `cloud_audit_emitter` and `cloud_storage_compute_emitter` each
feed AWS and GCP, and `ngfw_eal_emitter` feeds 2. So:

- an emitter that no other target serves is bound here;
- an emitter bound elsewhere is not moved silently: the step names that target
  and offers **Move the binding here**, which is two PUTs (unbind there, bind
  here) after the consultant confirms;
- the example events in 5.5 always route by an explicit `collector_target`, so
  they never depend on the binding.

The binding cannot send one emitter's Okta records to one collector and its
Entra records to another. That is a routing gap in the resolver
(`targets.py`), not in the wizard, and it is listed as a follow-up.

#### 5.5 Check, then send examples

1. **Check reachability**: `POST …/preflight` without a write. Shows the rung
   with its proves / does-not-prove text from `target_preflight.py`.
2. **Send test record**: the same call with `write_canary: true`. Says before
   it runs that it writes one record. `accepted` or the refusal's delivery
   code and remediation (401 → key or scheme, 404 → URL, 500 → log format,
   413, 429).
3. **Send example events**: the consultant ticks which declared emitters to
   use and, for each, one event pattern (default: the first listed). Only
   emitters whose params accept `canary_token` can be ticked; `email_emitter`
   gains that field in this build (§6), so every collector emitter is
   taggable. The wizard mints one canary token for this send
   (`wiz-<target>-<yyyymmddhhmm>-<4 hex>`, fitting `^[a-z0-9][a-z0-9-]{5,63}$`),
   then creates and launches one campaign: one step per ticked emitter,
   `iterations: 1`, `canary_token`, `collector_target: <name>`, and
   `provider` set from the declared source when the emitter has a provider
   parameter (Okta for `okta`, Entra for `azure_signin_log`, and so on);
   `target_allowlist: [<collector host>]`, `simulation_authorized: true`,
   `authorized_by` from a required "Your name" field. It shows each step's
   delivery accounting: attempted, delivered, and the code with its
   remediation when they differ.

#### 5.6 Confirm arrival (read-only)

Needs a tenant integration with the `xql` capability. With exactly one, it is
used; with several, the consultant picks; with none, the step reads **not
checked — no tenant credential with XQL** and links to Tenant.

The check runs once automatically 90 s after the last delivered send, and on
demand after that. It goes through the new arrival endpoint (§6), which runs
the query server-side with the one unwrapper (`queries.py::result_rows`), so
the browser never counts rows and a PENDING reply never reads as 0. Each run
issues at most two tenant calls (start, results):

```
dataset = <dataset>
| filter cortexsim_canary = "<token>"
| comp count() as found, min(_insert_time) as first_seen, max(_insert_time) as last_seen
```

| Result | Reads |
|---|---|
| `found` = delivered | **Arrived: N of N example records in `<dataset>`**, with ingest latency (`first_seen` − send time) |
| 0 < `found` < delivered | **Partly arrived: k of N**; re-check later |
| `found` = 0 | **Not found yet** in `<dataset>` after `<t>` s; lists the causes in order: wrong vendor/product (the data landed elsewhere), ingestion delay, a collector disabled after the send |
| no credential, no `xql`, quota, 401, 5xx, unrecognised reply | **not checked**, with the reason; never a count |

**Claude's call:** on a miss, the wizard does not search other datasets for
the token, because a tenant-wide search costs quota on every miss; the
consultant gets the likely cause instead. The `queries_issued` of every check
is shown.

What it proves: these records, sent from SimCore just now, were ingested into
`<dataset>`. What it does not prove: that a jumpbox inside the customer network
can reach the collector, that XDM maps the dataset, or that any detection
fires. Those three lines are printed under every result.

### 6. Backend changes

Small, read-only toward the tenant, and additive. **Claude's call** for each:

1. **`declaration` on the target.** A nullable JSON column on
   `collector_targets` — `{source_key, vendor, product, verdict}` — set by
   the PUT, the `cgo_anchor` / `composer_layout` pattern (an additive
   `_migrate_*` step). It is needed because shared emitters mean `dataset` +
   `plugins` cannot identify the source on a resume.
2. **`GET /api/eal/data-streams/{source_key}/detections`.** The scenarios,
   detection ids and detection types whose card dataset is that source's,
   from the loaded scenarios and cards. 5.1's list and the `xql_only` verdict
   need it; `/api/doors/readiness` has no dataset field.
3. **`POST /api/collector-targets/{name}/arrival`** with `{token, tenant,
   since}`. Starts the 5.6 query, or with `{query_id}` reads it; unwraps
   through `result_rows`; returns `{state: counted | pending | not_checked,
   found, first_seen, last_seen, reason, queries_issued}`. Same capability
   check and quota ledger as `/api/xsiam/tenants/{name}/xql`.
4. **`email_emitter` accepts `canary_token`** and writes `cortexsim_canary`,
   like the analytics-streamer emitters. A functional fix: without it,
   arrival for `microsoft_365_emails` reads 0 forever.

The event patterns per emitter come from `GET /api/eal/plugins/{name}`.

### 7. Console

- Destination `{ id: 'http-collector', label: 'HTTP collector', group: 'Manage' }`
  in `ui/src/app/destinations.jsx`, beside Firewall; `?target=<name>` resumes.
- `CollectorTargetsPanel` gains a "Set up with the wizard" button and a
  "Resume" link per `xsiam_http_collector` row.
- Cortex tokens and plain CSS, as the Firewall wizard; each step a
  `readiness__section` with an `h2`, so it is navigable by keyboard and
  screen reader.

### 8. Tests

Gate A: each test below is observed failing before its code exists.

- 5.1 verdicts: `matches`, `xql_only`, `cannot_feed` and `name_disputed`
  from catalogue fixtures, including `cloud_audit_logs` (cannot feed),
  `duo_auth_raw` (matches) and `okta_sso` (disputed).
- 5.2 split and live dataset preview; editing re-runs the verdict.
- 5.4 sends header `Authorization`, empty scheme, and never echoes the key;
  a PUT `warning` holds the step open; an emitter bound elsewhere is named
  and only moved after confirmation.
- §6: the `declaration` column survives a restart and an old row reads null;
  the detections-by-source read returns each detection's type; the arrival
  endpoint returns `pending` (not 0) for a PENDING reply, `not_checked` for
  each refusal, and reports `queries_issued`; `email_emitter` records carry
  `cortexsim_canary`.
- `native_dataset`: stops at 5.2 when the collector host is not in
  `CORTEXSIM_DOOR_PROBE_TENANTS`, when the setting is empty, and when the host
  differs only by case or port; continues with the banner when it matches.
- 5.5 campaign body: one step per ticked emitter, the minted token on each,
  `target_allowlist` = the collector host, consent fields set.
- 5.6 every row of the result table, including each **not checked** cause,
  and that a zero renders as "Not found yet", never as ok.
- A browser test (`ui/tests/e2e/`) walking all six steps against a stubbed API,
  because layout and focus order cannot be proven in jsdom.

### 9. Open questions for Henry

Settled: **native datasets** are lab tenant only (Henry, 2026-11-13; §5.1).

1. **Customer tenants for every other source.** Should "Send example events"
   and "Confirm arrival" be offered on a customer tenant? They are an
   ordinary campaign plus a read-only search, which the product already allows
   everywhere. **Claude's default:** yes, labelled as above, because a
   consultant needs this most on the customer's tenant.

D-2 (from the LogSimv2 port) stays open elsewhere; until it is decided the
wizard reports `name_disputed` for the 9 affected sources (§5.1).

### 10. Claude's calls in one place

- Approach A, a separate *Manage* destination (§4).
- No door state; links to #227's door once it exists (§3).
- Uncompressed JSON only (§3, §5.3).
- The test channel borrows one taggable emitter (§5.1).
- Non-matching verdicts continue as a delivery test (§5.1).
- Prefill always splits the card dataset or the source key; route pairs are
  hints (§5.2).
- Warnings from the PUT hold the step open; no new server validation (§5.4).
- Bind unbound emitters; move a binding only on confirmation; examples route
  by explicit target (§5.4).
- `provider` set from the declared source (§5.5).
- One automatic check at 90 s, then on demand; no tenant-wide search on a
  miss (§5.6).
- The lab tenant is recognised by `CORTEXSIM_DOOR_PROBE_TENANTS` and an exact
  host match; any doubt reads "not the lab tenant" (§5.1).
- Four backend additions: `declaration` column, a detections-by-source read,
  the arrival endpoint, `canary_token` on `email_emitter` (§6).
- Follow-up, not in this build: per-source routing for an emitter that feeds
  several sources (§5.4).

### 11. Sources

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
product in the dataset name; the real ingest latency (the 90 s first check is
a guess); whether `cortexsim_canary` survives ingestion as a top-level field.

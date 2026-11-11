# LogSimv2 into the Data Streams catalog: design

Status: phase 1 in build on `feature/api-logsim-catalog-port`. Phases 2 to 5 are
designed here and not started. `tenant-verified` is 0.

## Executive summary

LogSimv2 (github.com/phreezone/LogSimv2) is a mature synthetic-log tool for Cortex
XSIAM. It sends data the way real products do: the tenant's own dataset names,
vendor wire formats, and the ingestion paths on which built-in analytics actually
run. CortexSim's Data Streams family sends simplified records to a custom HTTP
collector. Per the vendor's documentation, built-in analytics never see data
that arrives that way, so a CortexSim stream can look delivered and still be
unable to fire the detector it claims. On a soakless tenant (no learning or
dedup wait) this is the only thing standing between a fired threat and a raised
alert. The recommended action is to port LogSimv2's capability in five phases,
starting with the catalog knowing the real ingestion path for every source, and
the readiness check refusing to call a detector armed when its path cannot feed it.

## Technical detail

### 1. Why

The port has to serve one POV moment. On a tenant in soakless mode, a DC fires a
named threat at a data source and expects a named analytics alert. A silence then
has to be explicable before the customer sees it, or it reads as "Cortex missed
it". Today three things make that silence likely and invisible:

| Cause | Evidence |
|---|---|
| The readiness gate ignores soakless. `outcome.soak_pending_reason` honours `soakless_attested`; `analytics_alerts.readiness_for` gates only on `max(activation, training)` and says "not armed" on a soakless tenant. | `core/engine/outcome.py:260`, `core/eal_simulator/analytics_alerts.py:325` |
| The emitters deliver through a custom HTTP collector. Analytics, IOC and BIOC run only on normalised data, and a custom-collector dataset is normalised only after an XDM modeling rule maps it. | HTTP collector and Analytics Engine pages, quoted in `docs/superpowers/specs/2026-11-10-tenant-setup-research/` (branch `docs/docs-tenant-setup-research`) |
| Out-of-the-box analytics run only on dedicated-UUID Cribl sources, never the generic UUID. | Cribl "Data source UUIDs" page, fetched 2026-11-10 |

LogSimv2 avoids all three: native pipes (Broker syslog, WEC, S3, Pub/Sub, HTTP
collector with the vendor's own vendor/product) and the Cribl receiver keyed by
each source's dedicated UUID.

### 2. Goals, non-goals, anti-goals

Goals:

- Every catalogue source carries its documented ingestion routes: pipe, the
  dataset the tenant creates, Cribl source UUID, vendor/product, and whether
  out-of-the-box analytics run on that route, each with evidence and a URL.
- The readiness gate answers armed / not armed / unknown / not armable using the
  soakless attestation and the route the campaign actually delivers on.
- LogSimv2's generators, threat catalog, kill chains and correlation rules become
  CortexSim content in later phases (section 4).

Non-goals: LogSimv2's Flask dashboard, its REST and MCP control plane (CortexSim
has its own), its WinRM Atomic Red Team runner (the beacon's job), the live Tor
exit-node fetch at startup, and its correlation-rule deploy and reload scripts.

Anti-goals, any of which makes this a failure even if it runs:

- A route or detector reading armed when the docs say it cannot fire.
- LogSimv2's own tenant runs counted toward CortexSim's `tenant-verified`.
- Any write to a Cortex tenant. Correlation rules ship as files the DC imports.
- LogSimv2 source code copied into this repo (section 3).

### 3. Constraints

- **Licence.** LogSimv2 has no LICENSE file, so its code is all rights reserved by
  default. This port reimplements behaviour and carries only facts: dataset names,
  UUIDs and alert names, each re-checked against the vendor's public docs. Vendoring
  any of its code needs the author's permission (open decision D-1).
- **No write path to Cortex.** `CORTEXSIM_XSIAM_ALLOW_WRITE` stays default-off.
- **Overlapping lanes.** `harden/eal-connectors` has unmerged edits to
  `analytics_emitter.py`, `collector.py` and `bundle.py`, and the collector auth
  fix branches are in flight. Phase 1 does not touch those files.
- **Open rename decision.** The tenant-setup research found 22 of 32 registry
  dataset names are not tenant datasets. Whether cards are renamed or each row
  carries a `tenant_dataset` is Henry's call (D-2). Phase 1 only measures the
  mismatch and renames nothing.

### 4. Phases

| Phase | Delivers | Depends on |
|---|---|---|
| 1 Catalog substrate | Ingestion routes per source; ingestion-only sources; soakless- and route-aware readiness; the emitter-vs-tenant dataset mismatch, measured | nothing |
| 2 Transports | Cribl receiver sender (Source-Identifier, Vendor/Product headers, gzip NDJSON, empty-batch preflight); Broker syslog TCP sender; WEC (WS-Management over mutual TLS) last. S3 and Pub/Sub are deferred because the Cribl dedicated UUIDs reach the same datasets. | 1, and the collector auth fix landing |
| 3 Wire-format generators | Vendor-native records per source: ASA, Firepower, FortiGate, Check Point, Zscaler and PAN-OS syslog/CEF; Okta System Log; Entra Graph sign-in and audit; O365 Management Activity; CloudTrail; GCP LogEntry; Windows Event XML; ESXi; Infoblox; Proofpoint TAP; Apache. One shared identity model so a user is the same person across feeds; generated high-cardinality values. | 2 |
| 4 Threat catalog | Each on-demand threat binds an `alert_ref` (a profile transcribed from the vendor page) or an `undocumented_reason`, with a negative control. Background-only threats feed the benign baseline. | 3 |
| 5 Kill chains and correlations | Single-pivot kill chains as scenarios; correlation rules as importable export files; card `correlation_rules` gain deployable bodies; seeded training packs (backfill, then live) | 4 |

### 5. Phase 1 as built

**Ingestion routes** (`core/eal_simulator/ingestion_routes.py`). One frozen
`IngestionRoute` per documented path into a catalogue source:

| Field | Meaning |
|---|---|
| `pipe` | one of `cribl_dedicated`, `cribl_generic`, `http_collector`, `broker_syslog`, `broker_wec`, `native_integration`, `cloud_connector` |
| `tenant_dataset` | the dataset the tenant creates on this route |
| `ootb_analytics` | `yes`, `no`, or `requires_xdm` (a custom collector, which needs an XDM modeling rule first) |
| `cribl_source_uuid`, `vendor`, `product` | what the sender must present |
| `evidence` | `documented` (read on `doc_url`) or `inferred` (reasoned, with `note`) |
| `doc_url`, `transcribed_on` | provenance, so a stale row is re-checkable |

A source key that is not in the catalogue, an unknown pipe or evidence value, or a
malformed UUID raises at import. Sources with no documented route say so: an empty
route list is shown, never omitted.

Ingestion-only sources (Proofpoint TAP, Infoblox NIOS, Apache httpd, VMware ESXi)
are listed separately. The vendor publishes no analytics for them, so they never
enter the 34-source coverage figure. They are reachable only by correlation rules
and XQL.

**Readiness.** `readiness_for` gains two keyword arguments, and evaluates them in
this order:

1. `route`: a route whose `ootb_analytics` is `no` gives `not_armable`
   (`DETECTOR_ROUTE_NO_ANALYTICS`). Soakless cannot fix a path analytics never
   reads. `requires_xdm` gives `unknown` (`DETECTOR_ROUTE_REQUIRES_XDM`), because
   an XDM rule is a tenant fact CortexSim cannot see.
2. `soakless_attested=True` gives `armed` with `armed_basis="attested:soakless"`
   (`DETECTOR_ARMED_SOAKLESS`). The documented periods stay in the verdict.
3. Otherwise the existing onboarding-date gate.

The campaign readiness endpoint derives each step's route from its own
`collector_url`: a `/logs/v1/event` path is the custom HTTP collector, a
`/logs/v1/integrated/cribl/data` path is the Cribl receiver, and anything else is
an unknown forwarder. A route that cannot be derived gives `unknown`, never `armed`.

**Measured, not fixed.** Each coverage row reports whether its emitters' declared
datasets are datasets the tenant creates on a documented route
(`EMITTER_DATASET_NOT_TENANT_DATASET`).

### 6. Validation

Offline, in the test suite: route registry integrity; readiness ordering (route
before soakless before onboarding); the API deriving routes from campaign steps;
the shipped corpus producing the measured mismatch count, pinned so it cannot
drift silently. On the tenant, by the DC: one stream per route on deathstar, read
back through the read-only `cortex-mcp` oracle. Until then `tenant-verified` is 0.

### 7. Open decisions (Henry)

- **D-1 Licence.** Ask phreezone for permission to vendor LogSimv2 code? With it,
  phase 3 shrinks from a rewrite to an adaptation.
- **D-2 Dataset names.** Rename card datasets to the tenant names, or keep card
  names and join through `tenant_dataset`? Phase 1 measures; it does not choose.
- **D-3 Deviation detectors on soakless.** Does a soakless "new country" or "rare
  destination" detector fire on the first event it sees, or does it still need
  benign history? The answer decides whether phase 4 threats ship with a benign
  warm-up.

### 8. Calls made without Henry (check these)

- Reimplement rather than copy, pending D-1.
- The soakless attestation does not override a route that cannot feed analytics.
- An undeclared route on the pure function keeps today's behaviour; the campaign
  endpoint always derives one, so the console never sees the undeclared case.
- Ingestion-only sources live outside the 34-source coverage maths.
- S3 and Pub/Sub transports deferred to after Cribl.

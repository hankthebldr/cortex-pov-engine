# Ingestion doors: setup state decides what the library can score

Status: design. Nothing built. `tenant-verified` is 0.

## Executive summary

A simulation produces evidence only if the customer's tenant has the matching
way in: an endpoint agent on the host, a firewall forwarding its logs, a cloud
account onboarded, an identity provider connected. CortexSim does not track any
of these today. Its setup checklist confirms the simulation agent but never the
Cortex agent that most detections depend on, two of its steps are checkboxes
nothing reads, and every scenario launches and is scored as if every way in
were open. A missing one turns into "Cortex missed it" in front of the
customer. This design makes each way in a tracked, tenant-checked "door".
Configuring a door in its setup step unlocks the scenarios that need it.
Scenarios still launch with a door missing, but the detections behind that
door are excluded from the score, and the report says so.

## Technical detail

### 1. Why

Measured on `dev` 8c90739 by joining each scenario's expected detections to
the dataset its card queries (method in §11): 1,411 expected detections across
198 scenarios.

| Door the detection needs | Detections | Scenarios |
|---|---:|---:|
| Cortex XDR agent on the target host (`xdr_data`) | 507 | 103 |
| NGFW forwarding to Strata Logging Service (`panw_ngfw_*`, `pan_dns`) | 214 | 71 |
| Cloud account onboarded (`cloud_audit_logs`, posture findings) | 148 | 39 |
| SaaS or identity connector (Okta, Entra, O365, Google Workspace) | 108 | 31 |
| Windows events (Broker VM WEC, agent with XTH, or XDR Collector) | 31 | 10 |
| Agent-to-NGFW stitch (`network_story`) | 12 | 10 |
| Broker VM Syslog with a vendor content pack (`cisco_asa_raw`, ESXi, Ivanti, Kubernetes audit) | 12 | 6 |
| Proofpoint TAP collector | 11 | 6 |
| Tenant products with their own data (Prisma Browser, Xpanse, TIM feed, AIRS, Koi, `xsiam_incidents`) | 75 | 30 |
| No derivable dataset (unresolved card object, or no `dataset =` anchor) | 293 | — |

Scenarios needing several doors are counted once per door.

Only 44 scenarios need the XDR agent and nothing else; 88 need the agent, the
NGFW, or both, and nothing more.

**Per door, as `engine.door_resolution` derives it** (the corpus as of
feature/api-ingestion-doors f6fd90f, measured on 2026-11-11 with the code at
its implementation commit c4e52a2, pinned in
`tests/engine/test_door_corpus.py`). These counts apply §5 in order, so they
differ from the table above in three ways. Collector steps are judged by their
route and counted under no door. Every `dataset =` anchor is read, not only the
first. `doors_unknown` rows and cloud detections that name no provider are
underived.

| Door | Detections | Scenarios |
|---|---:|---:|
| `xdr_agent` | 502 | 103 |
| `ngfw_traffic` | 136 | 49 |
| `cloud_aws` | 67 | 21 |
| `saas_okta` | 24 | 14 |
| `prisma_browser` | 23 | 6 |
| `windows_events` or `xdr_agent_xth` (one OR-set) | 22 | 8 |
| `saas_o365` | 19 | 9 |
| `saas_entra` | 15 | 9 |
| `xpanse` | 15 | 8 |
| `tenant_incidents` | 14 | 4 |
| `ngfw_stitch` | 12 | 10 |
| `cloud_gcp` | 9 | 3 |
| `ngfw_globalprotect` | 8 | 3 |
| `ngfw_threat` | 6 | 2 |
| `proofpoint_tap` | 5 | 5 |
| `cloud_azure`, `koi_aes`, `syslog_cisco_asa` | 3 each | 2, 2, 1 |
| `saas_google_workspace` | 2 | 2 |
| `syslog_ivanti_connect_secure` | 1 | 1 |
| Collector route (XQL 63, IOC 47, ABIOC 38, Analytics 21, Correlation 21, BIOC 9) | 199 | — |
| Underived: `NO_DATASET` 225, `DOORS_UNKNOWN` 61, `NO_PROVIDER` 39 | 325 | — |

The 502 `xdr_agent` detections are the 507 `xdr_data` detections less the 5
on collector steps. 501 of them need the agent and nothing else. The other one,
the `SIM-MP-015` step-03 ABIOC, inner-joins `xdr_data` with
`msft_azure_ad_signin`, so it also needs `saas_entra`. With every door open for
one agent, 150 scenarios read ready, 30 partial, 10 needs doors (collector
only) and 8 not evaluated (nothing derivable).

Four defects follow from this:

1. **The XDR agent is never checked.** Get Started's "Install an agent" step
   reads `/api/agents`, which lists CortexSim beacons. 507 detections need the
   Cortex XDR agent on the same host. A beacon on a host with no XDR agent
   reads ready and scores every endpoint detection as missed (observed on
   pov-target-01, 2026-11-02).
2. **Two setup steps are unread checkboxes.** "Set up a data collector" and
   "Set up the Broker VM" store a browser-local confirmation that no other code
   reads (`ui/src/components/console/setupProgress.js`). The Broker VM copy
   says network scenarios send traffic to it on 514; no scenario sends syslog,
   and native NGFW logs reach the tenant through Strata Logging Service.
3. **Launch never asks about the data path.** The launch preflight checks
   tool, target, platform, identity and measurement
   (`readiness/scenarioPreflight.js`). The NGFW wizard computes per-agent stitch
   readiness (`api/ngfw.py`, `firewall/SourcesStep.jsx`), and 110 NGFW
   detections come from beacon steps, but launch never reads it.
4. **Collector-delivered detections mostly cannot fire.** 131 detections come
   from steps that POST to a collector. 68 are BIOC, ABIOC or Analytics:
   a custom HTTP collector dataset is not stitched (no BIOC or IOC) and reaches
   Analytics only through an XDM rule. Most of the 63 XQL ones query dataset
   names the collector cannot create (it writes `<vendor>_<product>_raw`).

### 2. Goals, non-goals, anti-goals

Goals:

- One tracked state per door on the instance, checked against the tenant,
  read-only, on an explicit operator action.
- Every scenario in the library shows which doors it needs and which are open.
  Configuring a door in its setup step unlocks the scenarios that need it.
- A launch with a door missing still runs. Silent detections behind that door
  are excluded from the score with a named reason, and the run and the report
  list them.

Non-goals:

- New transports (syslog, Cribl, WEC senders). They are phase 2 of the LogSimv2
  port and become door producers when built.
- Fixing the card dataset names (decision D-2 of the LogSimv2 port spec). A door
  probes the dataset the tenant really creates, so this design does not depend on it.
- Writing anything to the tenant, the firewall or a connector.

Anti-goals, any of which makes this a failure even if it runs:

- A scenario hidden because its door is closed. An unlisted gap reads as no gap.
- An excluded detection counted as a pass, or dropped without a count.
- A door read as open because its check could not run. A check that could not
  run is `unknown`, never open.
- An attested door shown as verified.

### 3. Constraints

- **Read-only and opt-in.** Door checks use the existing `xsiam_tenant`
  credential and the read-only `endpoints/get_endpoints`, `xql/get_datasets` and
  XQL operations. Nothing polls without the operator asking. Each check reports
  `queries_issued`, like connector preflight.
- **Quota.** At most two calls per door per check, capped per call, under the
  existing per-tenant ledger. Quota exhaustion makes a door `unknown`, never
  closed or open.
- **Builds on PR #183** (merged into `dev` as 30b10cc on 2026-11-11). Door
  producers reuse its pipe vocabulary (`core/eal_simulator/ingestion_routes.py`)
  and its route gate (`analytics_alerts._route_gate`).
- **One instance per POV.** Door state lives in the instance database, like
  collector targets.

### 4. The door model

A door is one way data reaches the tenant, named by what it delivers:

| Door id | REACHABLE when | VERIFIED when (recent rows in) | Scope |
|---|---|---|---|
| `xdr_agent` | `get_endpoints` finds an agent on the beacon's host, status connected | `xdr_data` rows from that host | per beacon |
| `xdr_agent_xth` | `xdr_agent` reachable, plus an operator attestation of the XTH add-on (the endpoint record does not show it) | as `xdr_agent` | per beacon |
| `ngfw_traffic`, `ngfw_threat`, `ngfw_url`, `ngfw_globalprotect` | NGFW preflight forwarding stages ok for that log type | `panw_ngfw_traffic_raw`, `_threat_raw`, `_url_raw`, `_globalprotect_raw` respectively | per firewall |
| `ngfw_stitch` | the NGFW wizard's source status is ready for the beacon | `network_story` rows for the beacon's host | per beacon |
| `cloud_aws`, `cloud_gcp`, `cloud_azure` | a tenant credential with XQL exists | `cloud_audit_logs` rows for that provider | per tenant |
| `saas_okta`, `saas_entra`, `saas_o365`, `saas_google_workspace` | as above | `okta_sso_raw`, `msft_azure_ad_raw`, `msft_o365_*_raw`, the Google Workspace datasets | per tenant |
| `windows_events` | as above | `microsoft_windows_raw` | per tenant |
| `syslog_<vendor>_<product>` | as above | that vendor's dataset (`cisco_asa_raw`, `vmware_esxi_raw`, `ivanti_connect_secure_raw`) | per tenant |
| `http_collector:<target>` | the collector target's preflight `accepted` | the target's `<vendor>_<product>_raw` | per collector target |
| `proofpoint_tap` | as cloud | `proofpoint_tap_raw` | per tenant |
| `prisma_browser`, `xpanse`, `tim_feed`, `prisma_airs`, `koi_aes`, `tenant_incidents` | as cloud | their documented dataset where one is documented; `unknown` otherwise | per tenant |

`windows_events` records its producer (Broker VM WEC, agent with XTH, or XDR
Collector), as the operator declares it. Analytics and ABIOC detections on
Windows events open only when the producer is WEC or agent with XTH: the
vendor's Kerberos and DCSync detectors name those two. An XDR Collector
producer opens XQL and correlation only.

"Recent rows" means at least one row in the last 60 minutes (§10). A dataset
that exists but received nothing reads closed: connector preflight's
`dataset = X | limit 1` without a row count calls an empty dataset present,
and the door check must not copy that.

Each door uses the Readiness ladder's four rungs:

| Rung | Means |
|---|---|
| AUTHORED | the library has detections that need this door |
| CONFIGURED | the CortexSim side exists: tenant credential with XQL, a registered firewall, a collector target |
| REACHABLE | the door's own check passed: NGFW preflight, collector canary, endpoint found |
| VERIFIED | the tenant shows the door's dataset received rows within the freshness window |

The door is open for scoring at VERIFIED, or when the operator attests it
(`attested_by`, recorded, shown as attested, never as verified). An attestation
exists so a tenant key without XQL does not make every door closed. A check
that completes with a closed verdict after an attestation overrides it; a check
that could not run (`unknown`) does not.

### 5. Joining detections to doors

Each row of `spec/dataset_requirements.yml` gains **exactly one** of:

- `doors:`, an OR-set of door ids (any one open satisfies it). Examples:
  `xdr_data: [xdr_agent]`; `msft_windows_security: [windows_events, xdr_agent_xth]`;
  `panw_ngfw_traffic_raw: [ngfw_traffic]`; `network_story: [ngfw_stitch]`;
  `kubernetes_audit_logs: [syslog_kubernetes_kubernetes, cloud_aws]`.
- `doors_unknown: <reason>`, for a row whose producer is not confirmed
  (`saas_audit_logs`, `cloud_audit`, `ad_audit`, `cortexsim_airs_raw`,
  `esxi_syslog`, `threat_intel`, `pan_dns`). The registry forbids writing a
  name from memory, and this keeps that rule.

The loader rejects an unknown door id, a row with neither key, a row with both,
and a placeholder reason, as `TA-13`..`TA-16` do for payload exemptions.

How a detection gets its door, in order:

1. **A step that POSTs to a collector is judged by its route, not by the card's
   dataset.** The synthetic records land in the collector target's
   `<vendor>_<product>_raw` whatever the card claims, so a real firewall or a
   real CloudTrail must not open them. The door is `http_collector:<target>`,
   taken from the collector-target resolution the run already stores. The
   detection is scorable only when the card's dataset equals the target's
   dataset, and the route can feed the detection type: BIOC and IOC never
   (the data is not stitched); Analytics and ABIOC only with the XDM rule
   attested (`analytics_alerts._route_gate`); XQL and correlation yes. A
   collector step SimCore did not resolve (an agent-run step reading
   `$COLLECTOR_URL`) has an unknown route and is excluded.
2. **Otherwise the card's datasets decide.** Every dataset a detection reads
   (`walk_card` returns all anchors) must have an open door: a join with an
   empty side returns nothing, so this is AND across datasets and OR within
   each dataset's doors.
3. **`cloud_audit_logs` is split by provider.** A detection's provider is read
   from a `cloud_provider` filter in its query, else from the card's provider
   tag (`aws`, `gcp`, `azure`). A detection naming none is `underived`, never
   admitted by whichever provider happens to be open. Narrowing applies to
   every dataset whose OR-set holds two or more cloud provider doors (today
   `cloud_audit_logs`, `cortex_cloud_posture` and `ai_spm_findings`); a row with
   one provider door, such as `msft_azure_audit`, is not narrowed.
4. **Agent-scoped doors resolve against the step's own target**, since a
   fan-out runs steps on different beacons. A k8s pod beacon matches by the node
   name in its runtime facts; without one it is `unknown`. A push run has no
   beacon, so its agent-scoped doors open only by an attestation naming the host.
5. A detection with no derivable dataset (unresolved card object, no anchor, a
   `doors_unknown` row) is `underived`. It is scored as today and counted, so
   the gap is visible.

On today's corpus rule 1 excludes nearly every collector-delivered detection,
because the card datasets (`okta_sso`, `cloud_audit_logs`, `panw_ngfw_traffic_raw`)
are not names a custom collector creates. They come back as the card datasets
are corrected (LogSimv2 port decision D-2), or as the native-shaped transports
in that port's phase 2 make them real.

A scenario's door readiness is derived, never authored:

- **ready**: every derived detection's door is open.
- **partial**: some are, with the count of scorable detections.
- **needs doors**: none are, naming each closed door.

### 6. Launch and scoring

Henry's decisions (2026-11-11): a scenario with a closed door **launches, and
the detections behind that door are excluded**; a run with exclusions **can
FAIL but never PASS**; and a door **stays open as long as it is online**, with
no expiry timer, so a data stream can run for a month.

**Door state is a timeline, not a timestamp.** Every check appends a state
change (`opened`, `closed`, `unknown`, with time and evidence) to a per-door
log; a door's state at any moment is the last change before it. Nothing
expires on a timer. Two ways keep the timeline current:

- **On demand.** `POST /api/doors/{id}/check`, the console's check buttons and
  the launch preflight's "Re-check doors". The launch itself never calls the
  tenant.
- **The door watcher**, opt-in behind `CORTEXSIM_DOOR_WATCH` (off by default,
  like `CORTEXSIM_AUTO_RECONCILE`, because it makes outbound tenant calls). It
  re-runs every open door's check on a cadence (default 30 minutes), under the
  quota ledger, and records the moment a door stops delivering. This is what
  makes "open as long as it is online" true for a month-long stream.

With the watcher off, the last check stands until another check changes it,
and the console shows its age ("last confirmed 9 days ago") wherever the door
appears, so a persisted state is never mistaken for a fresh one.

At launch the orchestrator writes each expected detection's required doors onto
its seeded result. Scoring reads the door timeline **at the result's own
`executed_at`**, so a month-long stream is judged record by record: a
detection whose door was closed when its step ran is excluded, and one whose
door was open is scored. A door opened after a step ran does not bring that
row back, because it does not prove data flowed when the step did.

In the silent-row sweep (`connectors/service.py::_judge_silent_results`),
before the soak rule: a silent row whose door was not open at launch becomes
`pending` with reason `DOOR_NOT_VERIFIED`, and a silent row its route cannot
feed (§5 rule 1) becomes `pending` with `DOOR_ROUTE_CANNOT_FEED`. Both join
the denominator exclusions in `engine/outcome.py::is_in_denominator`, and
`measures.py` counts them under `door_not_verified`. A row with an observed
alert is credited as today, because the alert is proof the door delivered.

**Run verdict.** `verifier.score_run` keeps a FAIL computed on the scored rows:
a scorable detection that was missed is a real miss. A run that would PASS
while any row is excluded for a door reason reads `pending` with reason
`PASS_WITHHELD_DOOR_CLOSED`, naming how many detections were not tested and
behind which doors, in the style of `PASS_WITHHELD_UNSCOPED`. A run with every
row excluded reads `pending`, never `not_applicable`: those detections are
still owed, not unscoreable.

The launch preflight gains a sixth check, `data path`: `pass` when every
derived door is open, `warn` with the excluded count otherwise, plus the
"Re-check doors" button. A scenario whose every detection would be excluded
launches with a warning that says "this run scores nothing", but is not blocked.

The run report and `/api/runs/{id}` list the excluded detections grouped by
closed door, with the door's remediation and the wizard that opens it.

### 7. Surfaces

- **API.** `GET /api/doors` lists door states (zero outbound).
  `POST /api/doors/{id}/check` runs the read-only check.
  `POST /api/doors/{id}/attest` records an attestation.
  `GET /api/scenarios/{id}/doors` gives the derived readiness. The library list
  carries `door_readiness` per scenario.
- **Library.** A chip per scenario (ready / partial k of n / needs doors) and a
  "runnable here" filter. Nothing is hidden. Each closed door links to its setup
  step. Agent-scoped doors are judged against the console's selected target;
  with no target selected the chip covers tenant-scoped doors only and says so.
- **Get Started.** Steps become doors: the CortexSim beacon, then the XDR agent
  on that host, then the tenant, then optional doors. The localStorage
  confirmations and the Broker VM port-514 copy go. Each door step's primary
  action is its check.
- **Setup steps that already exist.** The Firewall wizard opens `ngfw_native`
  and `ngfw_stitch`; Tenant › Collector targets opens `http_collector:<target>`.
  Each finished check updates the library chips.

### 8. Phases

| Phase | Delivers |
|---|---|
| 1 | Door registry, `doors:` / `doors_unknown:` on every dataset row, scenario derivation, `GET /api/doors` (states read CONFIGURED from existing rows), library chip showing each door's rung ("configured", "not checked"), launch preflight check (advisory). Nothing is excluded from scoring in phase 1. Zero outbound |
| 2 | Door checks and attestation, the door timeline, the opt-in door watcher, `DOOR_NOT_VERIFIED` / `DOOR_ROUTE_CANNOT_FEED` exclusion in scoring, `PASS_WITHHELD_DOOR_CLOSED`, report readout |
| 3 | Get Started rebuilt around doors, XDR agent step, wizard links |
| Later | XDR Collector and Broker VM WEC as named producers; syslog and Cribl transports from the LogSimv2 port as producers |

### 9. Validation

Offline: the corpus join pinned (counts in §1, one test), every dataset row
carrying exactly one of `doors:` / `doors_unknown:`, the exclusion rule (silent
closed-door row excluded, observed row credited, a door opened after the step
ran does not re-include it), the run verdict (a miss still fails; an otherwise
passing run with an exclusion reads `pending`), the timeline (a watcher-recorded
outage excludes only the rows that ran inside it), and a check that could not
run reads `unknown`. Each new guard is shown failing
without its fix. Browser: the library chip and the Get Started door steps in a
real browser. Tenant: one door check per door on deathstar through the
read-only oracle; until then `tenant-verified` is 0.

### 10. Calls made without Henry (check these)

- An attestation opens a door for scoring, labelled attested; a later completed
  check that reads closed overrides it.
- A detection with no derivable door is scored as today and counted as `underived`.
- Collector-delivered detections are judged by their route, not the card's
  dataset (§5 rule 1), which excludes most of them on today's corpus.
- `xdr_agent` is VERIFIED only by `xdr_data` rows from the host; a connected
  endpoint is REACHABLE. pov-target-01 was connected with steps missing from
  telemetry.
- NGFW is four doors, one per log type; cloud audit is split by provider.
- A detection reading two datasets needs both doors open.
- Push runs open agent-scoped doors only by an attestation naming the host.
- Phase 1 excludes nothing; the chip shows each door's rung.
- "Open as long as it is online" is read as: no expiry timer, and an opt-in
  watcher (off by default, cadence 30 minutes) that records when a door stops
  delivering. With the watcher off, the last check stands and shows its age.
- Scoring reads the door timeline at each result's `executed_at`; a door opened
  after a step ran does not re-include that row.
- A run whose every detection is excluded warns and launches.
- A check's freshness window is 60 minutes of rows.
- Cloud and SaaS doors are tenant-scoped; agent and stitch doors are per beacon.
- `xdr_agent_xth` needs an attestation because the endpoint record does not
  show the XTH add-on.

### 11. Method for §1

Each scenario step's expected detections were joined to their card's detection
object by `(ttp_ref, detection_id)` with `engine.ttp_catalog._slug`. The
dataset is an XQL query's `dataset`, or the first `dataset =` / `preset =`
anchor in a BIOC, ABIOC or correlation `logic`. Each dataset was then mapped to
the door that produces it. 1,264 of 1,411 detections resolve to a card object;
the 147 that do not are 119 IOC, 9 XQL, 9 Analytics, 5 BIOC, 2 correlation,
2 Prevention and 1 ABIOC. A further 146 resolve to an object with no dataset
(139 correlation, 7 XQL). Kubernetes audit (`SIM-CDR-025`, `SIM-CDR-026`) is
counted under Broker VM Syslog; EKS audit through CloudWatch lands in
`cloud_audit_logs` instead, so its door is an OR-set in §5. `pan_dns` (17
detections) is counted under NGFW in §1, but it is not a documented dataset,
so §5 marks it `doors_unknown`.

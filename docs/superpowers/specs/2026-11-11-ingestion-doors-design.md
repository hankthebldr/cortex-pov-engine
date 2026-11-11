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
- **Quota.** One query per door per check, capped per call, under the existing
  per-tenant ledger. Quota exhaustion makes a door `unknown`, never closed or open.
- **Depends on PR #183.** Door producers reuse its pipe vocabulary
  (`core/eal_simulator/ingestion_routes.py`). Build after it merges, or stack on it.
- **One instance per POV.** Door state lives in the instance database, like
  collector targets.

### 4. The door model

A door is one way data reaches the tenant, named by what it delivers:

| Door id | Checked by (real tenant dataset or call) | Scope |
|---|---|---|
| `xdr_agent` | `get_endpoints` finds an agent on the beacon's host, status connected | per beacon |
| `xdr_agent_xth` | the `xdr_agent` check plus an operator attestation of the XTH add-on (the endpoint record does not show it) | per beacon |
| `ngfw_native` | NGFW preflight forwarding stages ok, then recent rows in `panw_ngfw_traffic_raw` | per firewall |
| `ngfw_stitch` | `ngfw_native` open, plus the NGFW wizard's source status ready for the beacon | per beacon |
| `cloud_aws`, `cloud_gcp`, `cloud_azure` | recent rows in `cloud_audit_logs` for that provider | per tenant |
| `saas_okta`, `saas_entra`, `saas_o365`, `saas_google_workspace` | recent rows in `okta_sso_raw`, `msft_azure_ad_raw`, `msft_o365_*_raw`, the Google Workspace datasets | per tenant |
| `windows_events` | recent rows in `microsoft_windows_raw`; the producer (WEC, agent+XTH, XDR Collector) is recorded because built-in Kerberos detectors need WEC or agent+XTH | per tenant |
| `syslog_<vendor>_<product>` | recent rows in that vendor's dataset (`cisco_asa_raw`, `vmware_esxi_raw`, `ivanti_connect_secure_raw`) | per tenant |
| `http_collector:<target>` | the collector target's preflight `accepted`, then recent rows in its `<vendor>_<product>_raw` | per collector target |
| `proofpoint_tap` | recent rows in `proofpoint_tap_raw` | per tenant |
| `prisma_browser`, `xpanse`, `tim_feed`, `prisma_airs`, `koi_aes`, `tenant_incidents` | recent rows in their documented dataset where one is documented; `unknown` otherwise | per tenant |

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
exists so a tenant key without XQL does not make every door closed.

### 5. Joining detections to doors

Each row of `spec/dataset_requirements.yml` gains `doors:`, an OR-set of door
ids (any one open satisfies it). Examples: `xdr_data: [xdr_agent]`;
`msft_windows_security: [windows_events, xdr_agent_xth]`;
`panw_ngfw_traffic_raw: [ngfw_native]`; `network_story: [ngfw_stitch]`. The
loader rejects an unknown door id and a row with no `doors:`.

A detection's door is its dataset's OR-set. Agent-scoped doors resolve against
the beacon the run targets. A detection whose dataset cannot be derived (no
dataset, unresolved card object) gets `door: underived`. It is scored as today
and counted, so the gap is visible.

A scenario's door readiness is derived, never authored:

- **ready**: every derived detection's door is open.
- **partial**: some are, with the count of scorable detections.
- **needs doors**: none are, naming each closed door.

### 6. Launch and scoring (Henry's decision: launch, exclude)

At launch the orchestrator writes a snapshot of the door states onto the run
(state, rung, checked_at, attested_by), and the scenario's required doors per
expected detection onto each seeded result. The snapshot is what scoring reads.
A door verified after launch does not bring rows back, because it does not
prove data flowed during the run.

In the silent-row sweep (`connectors/service.py::_judge_silent_results`),
before the soak rule: a silent row whose doors were all closed at launch
becomes `pending` with reason `DOOR_NOT_VERIFIED`. That reason joins the
denominator exclusions in `engine/outcome.py::is_in_denominator`, and
`measures.py` counts it under `door_not_verified`. A row with an observed alert
is credited as today, because the alert is proof the door delivered.

The launch preflight gains a sixth check, `data path`: `pass` when every
derived door is open, `warn` with the excluded count otherwise. A scenario
whose every detection would be excluded launches with a warning that says
"this run scores nothing", but is not blocked.

The run report and `/api/runs/{id}` list the excluded detections grouped by
closed door, with the door's remediation and the wizard that opens it.

### 7. Surfaces

- **API.** `GET /api/doors` lists door states (zero outbound).
  `POST /api/doors/{id}/check` runs the read-only check.
  `POST /api/doors/{id}/attest` records an attestation.
  `GET /api/scenarios/{id}/doors` gives the derived readiness. The library list
  carries `door_readiness` per scenario.
- **Library.** A chip per scenario (ready / partial k of n / needs doors) and a
  "runnable here" filter. Nothing is hidden. Each closed door links to its setup step.
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
| 1 | Door registry, `doors:` on every dataset row, scenario derivation, `GET /api/doors` (states read CONFIGURED from existing rows), library chip, launch preflight check (advisory). Zero outbound |
| 2 | Door checks and attestation, the launch snapshot, `DOOR_NOT_VERIFIED` exclusion in scoring, report readout |
| 3 | Get Started rebuilt around doors, XDR agent step, wizard links |
| Later | XDR Collector and Broker VM WEC as named producers; syslog and Cribl transports from the LogSimv2 port as producers |

### 9. Validation

Offline: the corpus join pinned (counts in §1, one test), every dataset row
carrying `doors:`, the exclusion rule (silent closed-door row excluded,
observed row credited, door verified after launch does not re-include), a
check that could not run reads `unknown`. Each new guard is shown failing
without its fix. Browser: the library chip and the Get Started door steps in a
real browser. Tenant: one door check per door on deathstar through the
read-only oracle; until then `tenant-verified` is 0.

### 10. Calls made without Henry (check these)

- An attestation opens a door for scoring, labelled attested.
- A detection with no derivable door is scored as today and counted as `underived`.
- The launch snapshot decides scoring; checking a door after launch does not
  re-include rows.
- A run whose every detection is excluded warns and launches.
- The freshness window is 60 minutes and a door check result is trusted for
  4 hours before it reads stale, then `unknown`.
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
`cloud_audit_logs` instead, so its door is an OR-set in §5.

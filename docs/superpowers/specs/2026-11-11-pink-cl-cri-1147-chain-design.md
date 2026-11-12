# Pink (CL-CRI-1147) chain, testable on today's lab: design

**Date:** 2026-11-11 · **Branch:** `docs/scenarios-pink-extortion-chain-spec` (from `dev` b1ee28f)
**Status:** v2, approved by Henry 2026-11-11; build plan `docs/superpowers/plans/2026-11-11-pink-cl-cri-1147-chain.md`. Henry asked to skip the questions ("go
straight to the spec, prefer ones that we can test immediately e.g. have syslog/http
event and agent / ngfw based scenarios"), so calls marked **[my call]** are mine and
open to veto. Four decisions only Henry can make are in §12.
**Inputs:** the review of `PaloAltoNetworks/Unit42-Threat-Intelligence-Article-Information`
(vault `_wip/`, 2026-11-11); Okta Threat Intelligence, "Vishing actors target Entra
passkey enrollment" (2026-07-05); the lab state in `docs/superpowers/specs/2026-11-10-ngfw-connection-wizard-design.md`,
`2026-11-10-collector-targets-design.md`, `2026-11-11-ingestion-doors-design.md`.

## 1. Intent

Pink is the freshest IOC file in the Unit 42 article repository (2026-06-03) and the
corpus has nothing on it. It is a SaaS-extortion chain with no malware: a caller
pretends to be IT, the victim types credentials into a fake Microsoft passkey page and
approves a push, the actor enrols their own passkey, pulls SharePoint and OneDrive
through Microsoft Graph, then extorts the company from the victim's own mailbox and
Teams.

Henry wants it run **on the lab we have now**, against `deathstar`, through doors that
are open today: the Cortex XDR agent on `pov-target-01`, the PA-440, and the XSIAM HTTP
log collector. Every leg in this design names its door, and every expected detection
names the dataset it lands in and whether it can fire there today. A leg that cannot be
tested today is not declared as an expectation.

## 2. Which doors are open today

| Door | State on 2026-11-11 | Evidence | What it can carry for Pink |
|---|---|---|---|
| Cortex XDR agent on `pov-target-01` | **open** — 9.4.0.470, CONNECTED, records PROCESS_START | `memory: vm-pov-target-01`; lab runs 2026-11-10/11 | the victim host resolving the lure (process telemetry) |
| PA-440 → `deathstar` | **open** — threat logs land in ~12 s | ngfw-connection-wizard spec §1 | the lure DNS query, via DNS Security |
| XSIAM HTTP log collector | **closed until Henry creates one** (L-1). SimCore side is ready: collector targets with a vault key, preflight to `accepted` | collector-targets spec; `core/api/eal.py` live launch | Entra sign-in, passkey registration, M365 file, mail and Teams records |
| Broker VM syslog | **held** — Henry has not named the applets | `memory: collector-targets` | nothing in this design |
| Native M365 / Entra connectors | not configured on `deathstar` | — | out of scope (§3 non-goals) |

The collector door has one hard property that shapes the whole design: an HTTP
collector writes into `<vendor>_<product>_raw` named in its own settings, and **no
built-in BIOC, ABIOC or Analytics detector runs on that dataset** without an XDM rule
and a 14-day activation plus 30-day training (tenant-setup research, 2026-11-10). So
every SaaS-leg expectation here is a repo-authored XQL read back by Tier-2 verification
(`verification_xql`, `POST /api/runs/{id}/verify`). It proves the record landed and the
query is right. It does not prove a Cortex detector fired, and the report must say so.

## 3. Goals, non-goals, anti-goals

**Goals**

1. One scenario, `SIM-MP-038`, with card `TTP-2026-02NN` (§11 for IDs), that runs as
   **one SimCore Run**: an agent step on `pov-target-01` plus three `channel: eal` steps
   that SimCore POSTs to the HTTP collector itself.
2. All nine published Unit 42 indicators carried in `indicators[]`, seven of them in band.
3. Every declared detection carries a `verification_xql`, so the run is scored by
   SimCore's own verify path against `deathstar`, not by hand-run queries.
4. A positive control on the NGFW leg (PANW's DNS Security phishing test domain) so a
   silent NGFW leg reads as "path broken", never as "Cortex missed Pink".
5. The engine and emitter changes that make 1–4 possible (§9), each with a test
   observed failing first.

**Non-goals**

- Native M365, Entra or Teams ingestion, XDM rules, and built-in ITDR/CIE analytics.
  They need connectors and a soak this lab does not have. Recorded as the upgrade path.
- The vishing call itself (no telemetry), the kit's `/backend.php` operator panel, and
  the external free-webmail extortion message. None is observable on these doors.
- Windows or macOS victim endpoints. `pov-target-01` (Ubuntu) stands in for "a host in
  the customer network that resolved the lure"; the scenario header says so.
- Push mode. `push_supported: false` [my call]: the chain needs SimCore at run time for
  the collector legs, which a push bundle forbids.
- Okta's two extra domains (`assignpasskey[.]com`, `setpasskey[.]com`). S-23 accepts only
  Unit 42 / PANW GitHub attribution, so they appear in card prose only, never as
  indicators and never on the wire.

**Anti-goals (failure even if it "works")**

- Any session to actor infrastructure. The lure leg is DNS resolution only.
- An expectation declared on a door that cannot fire it. Uncertain ones are
  `purpose: hunt` until a lab run observes them, then promoted.
- Reporting a Tier-2 row count as "Cortex detected Pink". It is "the data landed and the
  hunt query finds it".
- Loosening S-22..S-27 to make the chain load (§9.2 extends S-25; it never relaxes it).

## 4. Source facts

Published by Unit 42 (TTI file `2026-06-03-Pink-Extortion-Brand-Activity.txt`, in both
`PaloAltoNetworks/Unit42-Threat-Intelligence-Article-Information` and
`PaloAltoNetworks/Unit42-timely-threat-intel`):

- Vishing as IT staff; the user enters credentials on a phishing site, giving the actor
  the account and its MFA.
- Rapid identification and exfiltration from SharePoint and OneDrive.
- The compromised account sends the initial extortion email and internal Teams messages.
- Second-level domains are reused across victims; the third level names the target.
  Hosting on DDoS-Guard.
- Indicators: 3 domains, 3 IPs with stated roles, 3 user agents seen during exfiltration (§6).

Added by Okta (prose and step design only, not indicators):

- The kit is an operator-steered PHP panel, not an AiTM proxy. Pages: `/gate`,
  `/identify`, `/password`, `/processing`, `/submit-otp`, `/submit-authenticator`,
  `/approve-authenticator`, `/passkey/register`, `/passkey`, `/passkey/check`, `/done`.
- The user approves a number-matched push on the phone; the actor then **registers their
  own passkey** on the real account while the fake page keeps the user busy.

**Inferred here, marked as such in the YAML:** the timing between steps (seconds to
minutes) and the volume of files pulled. Neither source publishes them.

**Threat report URL.** There is no Unit 42 blog post for Pink; the source is the TTI
file. `threat_report_url` points at the GitHub file. Consequence: `is_stitched_chain`
requires host `unit42.paloaltonetworks.com`, so Pink counts **0** toward the stitched-chain
floor unless Henry widens that rule (§12, D-4).

## 5. The chain

| Step | Channel · runs on | What it does | Door → dataset | ATT&CK |
|---|---|---|---|---|
| step-01 | agent · `pov-target-01`, `identity: direct` | Resolves the lure: one `dig @${LAB_RESOLVER} <domain> A +short` line for each of the 3 Pink 2LDs, then the same for `test-phishing.testpanw.com` (control). No fetch, no session. | XDR agent → `xdr_data`; PA-440 DNS Security → `panw_ngfw_threat_raw` | T1566.004 |
| step-02 | eal · SimCore in-process | `idp_signin_emulator`, provider `microsoft`, new pattern `vishing_passkey_enrollment`: a successful interactive Entra sign-in for the principal from `172.93.100.252` with a number-matched push approved, then a "User registered security info" record (passkey) from the same IP, body-timestamped 90 s after the sign-in | HTTP collector → `cortexsim_saas_raw` | T1078.004, T1098.005 |
| step-03 | eal · SimCore in-process | `m365_activity_emitter`, existing pattern `storage_exfiltration`, with new `source_ip: 172.93.100.252`, `user_agents` rotating the 3 published strings, and `storage_targets: [sharepoint, onedrive]`: a `FileDownloaded` / `FileSyncDownloadedFull` burst across both | HTTP collector → `cortexsim_saas_raw` | T1530, T1213.002 |
| step-04 | eal · SimCore in-process | `m365_activity_emitter`, new pattern `internal_extortion_notice`: an Exchange `Send` from the principal's mailbox to several internal recipients, and Teams `MessageSent` by the principal, both from `96.232.20.66` | HTTP collector → `cortexsim_saas_raw` | T1657 |

**One collector, one dataset** [my call]: vendor `cortexsim`, product `saas` →
`cortexsim_saas_raw`, bound to both emitter plugins. The verification queries tell the
record kinds apart by the fields each emitter writes. One collector means one key and
one preflight for the DC. If Henry prefers a collector per source, only the dataset
names in the queries change.

**`LAB_RESOLVER`** is a public resolver reached **through** the PA-440 (not Pi-hole,
which would both hide the source and may block these names). The scenario header states
it, as the 2026-10-27 DNS ruling requires.

**Timing.** In-process EAL steps run synchronously inside the launch request, so they
cannot sleep for minutes (§9.1 caps it). The chain's minutes-long story is written into
the records' **body timestamps** instead, back-dated from the send time so no record
claims a future time: sign-in at −25 min, passkey at −23.5 min, downloads from −20 min,
extortion send and Teams at −5 min (offsets inferred, §4). Queries that reason about
order or gaps (D6, CR-1, CR-2) key on those body fields, never on `_time`, which for an
HTTP collector is likely the ingest time (the builder confirms from a captured row).

**Ordering against the agent step.** The beacon runs step-01 on its next poll, seconds
after the EAL steps were sent, so the lure lookup lands after the sign-in's ingest. The
lure-to-identity link (CR-3) therefore uses a symmetric ±30 min window and stays a hunt.

## 6. Indicators

All `source_url` = the TTI file in `PaloAltoNetworks/Unit42-Threat-Intelligence-Article-Information`
(S-23 accepts `https://github.com/paloaltonetworks/`). Values refanged on disk.

| id | type | value | safe_form | surfaces_in | in_band | why |
|---|---|---|---|---|---|---|
| ind-01 | domain | `passkeyadd.com` | dns_lookup | step-01 | true | resolved by `dig @${LAB_RESOLVER}` |
| ind-02 | domain | `passkeydeploy.com` | dns_lookup | step-01 | true | same |
| ind-03 | domain | `deploypasskey.com` | dns_lookup | step-01 | true | same |
| ind-04 | ipv4 | `185.178.208.153` | log_field | step-01 | **false** | hosted the phishing domains; the lookup may no longer return it, so nothing guarantees it on the wire |
| ind-05 | ipv4 | `172.93.100.252` | log_field | step-02, step-03 | true | sign-in, passkey registration and download records |
| ind-06 | ipv4 | `96.232.20.66` | log_field | step-04 | true | mail send and Teams records |
| ind-07 | user_agent | `Microsoft.Graph.Client/5.62.0` | log_field | step-03 | true | download records |
| ind-08 | user_agent | `python-requests/2.28.1` | log_field | step-03 | true | same |
| ind-09 | user_agent | `python-requests/2.33.1` | log_field | step-03 | true | same |

The published sign-in user agent is unknown, so step-02 does not carry ind-07..09.
Tags: `unit42`, `pink`, `cl-cri-1147`, `real-ioc-dns-lookup` (D-1).

## 7. Expected detections

Rows marked **declared** are scenario `expected_detections` and carry
`verification_xql`. Rows marked **hunt** are card detection objects with
`"purpose": "hunt"` (107 such objects exist today): the lab run executes them and the
evidence doc records the result, but they are not expectations and never score a
working tenant as a miss.

**Scoping a static query to one run.** `verification_xql` is copied verbatim onto the
`Result`, with no templating, and the emitters' `cortexsim_run_id` is minted per step at
run time, so a query cannot name it. Scoping comes from three things instead:
- the run's own absolute time window, which verify does not use today (P-5, §9.7);
- in collector rows, the scenario's fixed principal (the steps' `target_user`, a
  `cortexsim.pink.canary@…` address) and a fixed `canary_token` (`cortexsim-mp-038-pink`);
- in agent and NGFW rows, the exact domain.

No host name goes into corpus XQL. Known limit: two runs of this scenario with
overlapping windows can satisfy each other's rows; the evidence doc notes it.

| # | Step | Plane | Type | Dataset | Status | A hit proves | It does not prove |
|---|---|---|---|---|---|---|---|
| D1 | 01 | NDR | XQL | `panw_ngfw_threat_raw` | **declared** (control) | DNS Security categorised and logged `test-phishing.testpanw.com`; NGFW → tenant path works | anything about Pink |
| D2 | 01 | NDR | XQL | `panw_ngfw_threat_raw` | hunt → promote after first lab hit | PAN-DB still classes a Pink 2LD as phishing/malware and the PA-440 logged it | that the actor is active |
| D3 | 01 | EDR | XQL | `xdr_data` | **declared** | the agent recorded the host resolving a Pink domain (`dig` PROCESS_START, domain in command line) | that a browser opened the kit |
| D4 | 01 | EDR | XQL | `xdr_data` | hunt | the Linux agent records DNS queries (`dns_query_name`) | — (unverified capability, check in lab) |
| D5 | 02 | ITDR | XQL | `cortexsim_saas_raw` | **declared** | an Entra sign-in for the principal from the Pink access IP landed and the query finds it | that Cortex ITDR raised anything |
| D6 | 02 | ITDR | XQL | `cortexsim_saas_raw` | **declared** | a passkey registered from the sign-in's IP within 10 min of that sign-in | same |
| D7 | 03 | CLOUD_APP | XQL | `cortexsim_saas_raw` | **declared** | a SharePoint/OneDrive download burst by the principal from the Pink IP | that a Cortex SaaS detector fired |
| D8 | 03 | CLOUD_APP | XQL | `cortexsim_saas_raw` | **declared** | interactive-user file access through Graph SDK / python-requests user agents | same |
| D9 | 04 | CLOUD_APP | XQL | `cortexsim_saas_raw` | **declared** | an internal fan-out send from the principal's mailbox from the extortion IP | same |
| D10 | 04 | CLOUD_APP | XQL | `cortexsim_saas_raw` | **declared** | Teams messages by the principal from the extortion IP | same |
| CR-1 | 02→03 | ANALYTICS | Correlation | `cortexsim_saas_raw` | **declared** | sign-in → passkey → download burst, same principal and IP, within 3600 s | that XSIAM grouped them into a case |
| CR-2 | 03→04 | ANALYTICS | Correlation | `cortexsim_saas_raw` | **declared** | download burst then internal extortion send/Teams, same principal, within 3600 s | same |
| CR-3 | 01↔02 | ANALYTICS | XQL (hunt objects are all `xql_queries`) | `panw_ngfw_threat_raw` + `cortexsim_saas_raw` | hunt | lure resolution within ±30 min of the sign-in | any host↔user binding (the lab has none, §10 L-3) |

Correlation share: 2 of the 10 declared rows = 20 %, above the 10 % `coverage-strict`
floor that the corpus clears by only 0.1 point.

Built-in alerts. If the PA-440's threat log raises a tenant alert for D1/D2, the
reconcile matcher will see it as well; that is a bonus, not an expectation, until the
lab shows it. The card exports the CR-1/CR-2 correlation rules so Henry *can* import
them and get real alerts; the scenario does not depend on it.

**Measurement contract [my call].** No `primary_kpi` and no `threshold` (six corpus
scenarios already omit them). An MTTD threshold would hold the run at `pending`
forever: `score_run` returns `pending` when the KPI is unmeasured, before it counts any
failure, and Tier-2 verify never sets `observed_at`, so MTTD is never measured on this
path. Without a threshold the run is scored on its declared rows: a missing row is
**`fail`** (never clamped), all rows found is a pass. The index binding stays honest
rather than scoreable. The rows that fit Pink (`TC-ITDR-03`, `TC-ITDR-02`, as on
SIM-MP-034) have no measurable threshold, so a full green clamps to
**`not_applicable`**, read as "evidence, not a scored pass". That is the right reading
for data-landed proof on a collector door. Consequence: this run cannot move
`tenant-verified`, which counts only `pass`/`fail`. The scoreable rows that would let it
(`TC-IR-13` grouping, `TC-AES-02` phish-to-credential correlation) describe things this
lab cannot show, so binding them would be the false claim.

Other fields: `methodology_family: F1`, `stitching_key: user_principal`,
`required_planes_in_incident: [ITDR, CLOUD_APP]` (NDR and EDR stay detection planes, not
required in the incident, because they carry no user), `correlation_window_seconds: 3600`.
`pov_scenario_id: POV-SC-001` as on SIM-MP-034. The builder confirms the refs with
`scripts/uctc_crosswalk_v2.2.py --report` and `make check-refs`.

## 8. What a good run looks like

1. Preflight green on all three doors: collector target `accepted` (with
   `write_canary`), beacon online, XDR agent on the beacon's host CONNECTED.
2. Launch with `consent.simulation_authorized`. The three EAL steps POST, and the run's
   `channel_dispatch` ledger shows each one delivered (2xx only, per `delivery.py`);
   step-01 completes rc 0.
3. `POST /api/runs/{id}/verify` against the `deathstar` `xsiam_tenant` credential, any
   time after the run (P-5): D1, D3, D5–D10, CR-1, CR-2 return rows and the run reads
   `not_applicable` (evidence). Any missing row reads `fail`. The hunt objects D2, D4,
   CR-3 are run separately and recorded in the evidence doc.
4. Evidence written to `docs/lab/<date>-pink-cl-cri-1147-lab-run.md` in the shape of
   `2026-11-11-unit42-deep-real-verification.md`: what landed, what fired, what did not.

If D1 is empty, the NGFW path is broken (policy, profile action, log forwarding or NAT)
and D2 is not evidence of anything; the evidence doc says so.

## 9. Engine and emitter work

The items are independent of each other; the scenario and card land last, after all of
them. Each lands with behaviour tests written first and observed failing (Gate A). No
new strict validators beyond §9.2, which keeps an existing gate whole.

### 9.1 P-1: live delivery for in-process EAL steps

`orchestrator._dispatch_eal_in_process` hard-codes `dry_run=True` ("real delivery is
consent-gated (follow-up)"). No branch has lifted it. Change: the in-process campaign
runs live **only when** the launch carries `consent.simulation_authorized` **and** every
eal step resolves to a collector target that has a key; otherwise it stays dry-run and
the run says why (a degradation code naming the step and reason, never a silent
dry-run). Delivery accounting is the existing one (`delivery.py`: only 2xx counts). The
launch request must stay short: the EAL steps' total sleep is capped (builder picks the
cap; Pink needs < 30 s).

*Found while planning:* a live `Campaign` refuses to validate without `authorized_by`,
and the scenario launch request has no such field. The plan adds
`LaunchRequest.authorized_by`; live delivery needs it non-blank as well as consent.

Tests: dry-run without consent; dry-run with consent but an unresolved target, with the
code; live POST with consent and a keyed target, against an injected transport;
`Result.executed_at` taken from the real send time.

*Fallback if Henry does not want P-1 now (D-3):* the collector legs run as a standalone
EAL campaign (`POST /api/eal/campaigns` → `/launch`, live, already supported) and step-01
as a one-step scenario. That is testable tonight, but it produces two artefacts, no
single Run, and no SimCore score for CR-1/CR-2.

### 9.2 P-2: S-25 covers `eal.params`

Today an indicator value can only reach a plugin's params inside a campaign-file
heredoc, and S-25 exempts it only when every plugin there is a collector-POST emitter.
`channel: eal` steps put params in YAML that S-25 never scans, so `c2_http_beacon` with
`172.93.100.252` in `eal.params` would load and send real traffic from SimCore. Rule:
an `ipv4`/`ipv6`/`domain`/`url` indicator value (or a url's host) in a step's
`eal.params` is allowed only when `eal.plugin` ∈ `COLLECTOR_POST_PLUGINS`; otherwise
S-25. Mirrored in `.claude/hooks/lint-scenario.py`, parity pinned like the existing
regexes. This is the existing gate applied to a new surface, not a new guard.

### 9.3 P-3: an EAL step is not a marker-only step

`StepSchema.command` is required, so an eal step carries a descriptive no-op command,
and `coverage_report.is_marker_only_step` would count it as marker-only. Change: a
`channel: eal` step is never marker-only (it emits records). Without this, Pink adds 3
marker-only steps and moves the ceiling the wrong way.

### 9.4 E-0: back-dated body timestamps (shared base)

`event_time_offset_seconds: int = 0` on `AnalyticsEmitterParams`, ≤ 0 only. An emitter
writes its body timestamps (`createdDateTime`, `activityDateTime`, `CreationTime`, …)
at send time plus the offset, and a pattern's internal gaps (the passkey's +90 s) are
added on top. Zero keeps every existing record byte-identical. Tests: offset 0 is
byte-identical to today's golden records; a negative offset moves every body timestamp
by exactly that amount; a positive offset is refused.

*Refined in the plan:* the field goes on the two params models that honour it
(`idp_signin_emulator`, `m365_activity_emitter`), and only the timestamp helper is shared,
so the 19 other emitters do not advertise a field they would ignore.

### 9.5 E-1: `idp_signin_emulator`

- `source_ip: Optional[str]` — replaces the attacker-side location IP for the run's
  pattern. Accepts a routable address or RFC 5737 (the default stays the current table).
- Pattern `vishing_passkey_enrollment` (provider `microsoft`): one successful
  interactive sign-in with `authenticationDetails` showing a push with number matching
  approved, then one directory-audit record "User registered security info" with the
  method "Passkey" from the same IP, body time +90 s. Record shapes from Microsoft Graph
  `signIn` and `directoryAudit` docs, checked at build time, not guessed.
- Note: this emitter already uses `params.user_agent` both as the HTTP header and as the
  record's `rawUserAgent`. Pink's step-02 sets neither (no published sign-in user agent).

### 9.6 E-2: `m365_activity_emitter`

- `source_ip: Optional[str]` — default stays `_ANOMALOUS_IP` (back-compat for every
  existing scenario); written to `ClientIP`.
- `user_agents: list[str]` — rotated per record, written to the record's `UserAgent`.
  It is deliberately **separate** from the base class's `user_agent`, which sets the
  HTTP User-Agent on SimCore's own POST to the collector (`analytics_emitter.py`). The
  published actor user agents must never go on SimCore's requests. Empty keeps today's
  records byte-identical.
- `storage_targets: list[str]` for `storage_exfiltration`, values `onedrive` and
  `sharepoint`, default `[onedrive]`. Today the pattern writes only `Workload: OneDrive`
  and the OneDrive site, so every existing scenario keeps byte-identical records; Pink
  sets both.
- Pattern `internal_extortion_notice`: Exchange `Send` (mailbox audit) from the
  principal to N internal recipients, and Teams `MessageSent` by the principal, both
  stamped with `source_ip`. Operation names and record types checked against the Office
  365 Management Activity API schema at build time.
- `check-streamer-fidelity.py` keeps one `_DATASET` per emitter; these are still
  `msft_o365_audit`-shaped records.

### 9.7 P-5: verify queries the run's own window

`verify_run_now` sends each query with `relativeTime` = the lookback counted back from
**now** (default 3600 s). So a verify run more than an hour after the run finds 0 rows
and scores **`fail`**, a manufactured miss, and a verify inside the hour can be passed
by any other run's data. Change: query the absolute window `[run start − 5 min,
run end + the scenario's correlation window]`, capped at the tenant's limit, using the
XQL API's absolute `timeframe` (confirm the field names in the XSIAM API docs at build
time). `timeframe_seconds` stays as an explicit override. This changes verify for the
15 scenarios that already carry `verification_xql`; for them it only removes the
late-verify false `fail`. Tests: a verify two hours after the run queries the run's
window, not the last hour; a run with no `completed_at` uses start + window; the
override still wins.

`email_emitter` needs no change: the extortion mail is internal-to-internal, so it is an
Exchange audit record, not a gateway record (this corrects my 2026-11-11 review note).

## 10. Lab prerequisites (Henry, one time)

| # | What | Why |
|---|---|---|
| L-1 | Create an HTTP log collector on `deathstar` (JSON, vendor `cortexsim`, product `saas`); add it in SimCore as a collector target of kind `xsiam_http_collector` with its key, bound to `idp_signin_emulator` + `m365_activity_emitter`; run its preflight with `write_canary` to `accepted` | the SaaS legs' only door |
| L-2 | On the PA-440, the rule `pov-target-01`'s DNS egress hits carries an anti-spyware profile with DNS Security, and the Phishing and Malware categories log (alert or sinkhole). Check with the NGFW wizard's Sources stage | 2026-11-10 found these at `default` |
| L-3 | Decide the NAT (D-2). Behind libvirt NAT the PA-440 sees ms-01 (192.168.1.110), itself an XDR endpoint, so Cortex attributes the lookup to ms-01 | D1/D2 still land; host attribution and CR-3 are wrong until bridged |
| L-4 | Set `LAB_RESOLVER` to a public resolver beyond the PA-440 | so the query crosses the firewall from a known host |
| L-5 | Confirm the XDR agent's collectors are running on `pov-target-01` (`cned`/`piud` were STOPPED on 2026-11-11) | D3/D4 |
| L-6 | Keep the `xsiam_tenant` credential with XQL on `deathstar` | the verify path |

## 11. IDs and gates

IDs are taken at build time: as of 2026-11-11 `SIM-MP-038` is free; TTP cards are used
up to `TTP-2026-0203` across in-flight branches (`wf/unit42-axios`,
`feature/scenarios-unit42-backlog`), and `feature/scenarios-unit42-cloud-logging` may
take the next one, so check those branches first.

Gates before calling the build done: scenario lint, `make validate-detection`,
`make check-refs`, `make coverage-strict`, `make check-ground-truth` (counts
regenerated), host backend suite, `make test-backend-non-utc`, and the e2e isolated
suite. Then the lab run in §8. `tenant-verified` stays 0 after this run by design: a
green run reads `not_applicable` (§7), and only `pass`/`fail` count.

## 12. Decisions

**Owed by Henry** (each has a default so the build is not blocked):

- **D-1 Real-domain DNS lookups.** The 2026-10-27 provisional ruling (`real-ioc-dns-lookup`)
  was never confirmed. Pink's NGFW and endpoint legs depend on it. *Default: follow the
  provisional ruling, tagged.* If you rule against it, ind-01..03 become `in_band: false`
  and step-01 keeps only the PANW test domain.
- **D-2 NAT.** Bridge `pov-target-01` onto the LAN (correct attribution, a change on ms-01)
  or accept ms-01 attribution. *Default: accept for the first run, bridge before quoting
  CR-3.*
- **D-3 P-1 live in-process delivery.** It changes behaviour for every `channel: eal` step
  (today none in the corpus, some drafts). *Default: build it.* Fallback in §9.1.
- **D-4 Stitched-chain rule.** Accept the PANW GitHub TTI repo as a `threat_report_url`
  host in `is_stitched_chain`, matching S-23. *Default: leave the rule alone; Pink counts 0.*

**Decided here [my call]:** one collector and dataset `cortexsim_saas_raw`; push off;
no KPI or threshold, with an honest unscoreable index binding (§7); back-dated body
timestamps instead of real sleeps; P-5 (verify by the run's window) as a prerequisite,
which also changes verify for 15 existing scenarios; the NGFW positive control; `ind-04`
out of band; D2/D4/CR-3 as hunt until a lab run promotes them.

## 13. Left to the builder

XQL, correlation and BIOC bodies (must pass `validate.py`); record field names after
capturing one real collector row; the sleep cap; file and recipient counts; detection
slugs; card prose including Okta's kit pages as context; the `uc_ref`/`tc_ref` choice
within the crosswalk.

## 14. Risks

- **Pink domains may have been taken down or recategorised** since June. D2 is hunt for
  that reason; D1 keeps the leg honest.
- **Residential proxy IP.** `96.232.20.66` may belong to an unwitting user. It appears
  only inside records in a custom dataset no playbook watches; nothing is sent to it.
  Do not put it into an EDL or block rule from this scenario.
- **Collector field parsing** may flatten or stringify nested objects; capture a row
  before writing queries (the NGFW wizard lesson: two guessed fixtures were wrong).
- **The Linux agent may not record DNS queries**; D4 is hunt for that reason.
- **P-1 makes SimCore send** where it never did for scenarios. Consent plus a keyed
  target is the gate; the tests in §9.1 are the proof.

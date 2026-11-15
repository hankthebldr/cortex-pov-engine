---
packet: PMQ-2026-11-11-identity-cgo-cie-ad
created: '2026-11-11'
from: personal-mac / cortex-pov-engine
source: personal-mac
reply_to: _RoB/product-mechanics/answers/PMA-2026-11-11-identity-cgo-cie-ad.md
questions:
- PM-STI-08
- PM-CAU-07
- PM-CAU-08
- PM-COR-08
- PM-IOC-06
- PM-IDN-01
- PM-IDN-02
- PM-IDN-03
- PM-IDN-04
- PM-IDN-05
- PM-IDN-06
- PM-IDN-07
- PM-IDN-08
- PM-IDN-09
tags:
- product-mechanics
- question-packet
---

# Product-mechanics questions — PMQ-2026-11-11-identity-cgo-cie-ad

14 questions about how Cortex turns telemetry into a detection. CortexSim generates signal into customer tenants for POVs; when a signal does not fire, the POV report reads it as "Cortex missed it". These answers decide what the generator emits.

## Instructions for the answer agent

You are answering product-mechanics questions about Cortex XSIAM / XDR for the CortexSim
detection-simulation engine. Each question names the generator behaviour its answer changes,
so answer in terms the generator can act on: field names, counts, windows, which side of a
join, which record type, which setting.

**Search** enterprise search (Glean), Confluence, Jira, Drive and the public docs
(https://cortex-docs.paloaltonetworks.com). Prefer engineering design docs and resolved Jira
tickets over sales material; prefer the newest source when two disagree, and say they disagree.

**The confidentiality rule decides what you write where. It is not optional.**

- The answer file you return (below) is stored in a personal Obsidian vault and a **public**
  GitHub repository. It may contain **only** facts whose source is public (public docs,
  public KB, public blog) or that were observed in our own lab, or an operator report.
- Anything found only in an internal source (Confluence, Jira, Drive, Slack, internal Glean
  results) stays on the work Mac, in work storage, outside the vault. In the answer file,
  mark that question `status: internal_only` with no `answer`, `rule` or `sources`.
- If an internal finding can be confirmed in the lab, say so on the work side; the lab result
  is what crosses later, as a `lab_observation`.
- Quote at most 25 words from any source (`excerpt`). Paraphrase the rest.

**Honesty.** `unknown` is a good answer when the sources do not settle it. Never infer a
threshold and present it as documented. If an answer depends on version, licence or
configuration, say which.

## Reply format

Write one Markdown file to `_RoB/product-mechanics/answers/PMA-2026-11-11-identity-cgo-cie-ad.md` (vault-relative) shaped exactly like this — frontmatter,
then exactly one ```yaml block. The ingest step rejects any other shape, and rejects the whole
file if any source is not `distribution: public`.

````text
---
packet: PMQ-2026-11-11-identity-cgo-cie-ad
answered: YYYY-MM-DD
answered_by: <agent or person>
source: work-mac
---
```yaml
answers:
  - id: PM-XXX-NN
    status: answered        # answered | partial | unknown | conflicting | internal_only
    answer: >-
      Plain explanation of how the product behaves and why.
    rule: >-
      The generator-facing constraint, e.g. "emit >= N distinct dst ports from one src
      within 1 h". Omit when there is no rule.
    sources:
      - kind: public_doc    # public_doc | public_kb | public_blog | lab_observation | operator_report
        title: Page or ticket title
        ref: https://... (or a description for lab/operator)
        date: YYYY-MM-DD
        distribution: public
        excerpt: optional, at most 25 words
    follow_ups:
      - >-
        Optional new question the answer raised. Write every item as a `>-` block
        like this one: a bare item that starts "Lab: ..." or wraps onto a second
        line is not valid YAML, and ingest rejects the whole file.
```
````

Two shapes ingest rejects that are easy to miss: a source `date` that is not a full
`YYYY-MM-DD` (write the day you read the page, never `2026-08-xx`), and a `follow_ups`
item that is not a quoted string or `>-` block.

## Questions

### PM-STI-08 — P1 — Stitching (data fusion across sources)

**Question.** How do identity sources stitch to endpoints: on UPN, sAMAccountName, email, SID, or a normalized user? What normalization does the engine apply (case, domain prefix, @suffix)? When the Cloud Identity Engine is linked, does it do the resolving, so that `CORP\alice` on an agent event, `alice@corp.local` in a 4769 and `alice@corp.com` in an Okta sign-in become one identity, and which field on an event or issue carries that resolved identity?

**Why we ask.** 13 scenarios use `stitching_key: user_principal`. canary_token accepts only lowercase alphanumerics and hyphens (stitch_params.py:36), which may not survive the engine's normalization. The lab shows Linux local users as `<hostname>\<user>` (IDN known), a form no IdP record carries.

**What the answer changes.** Account formats planted by canary_token and identity emitters; whether cross-source identity scenarios need CIE linked to be satisfiable.

**Background — our current working model of STI.** The engine that links records from different sensors (agent network events, NGFW sessions, identity logs, cloud audit) into one story. Our working model: a join on shared entities, chiefly the network 5-tuple for network-endpoint, inside a time tolerance. Henry confirmed in practice that the source half has to match what the agent recorded and the destination half has to match the firewall transaction. Which fields, which side of NAT and what tolerance are still unconfirmed.

### PM-CAU-07 — P0 — Causality and the Causality Group Owner (CGO)

**Question.** On Linux hosts running systemd 255 or later, where services start through `systemd-executor` executed from a file descriptor, the agent records the CGO of every service-started chain as image name `9`, path `/proc/self/fd/9`, the SHA-256 of empty input and a null user (CAU known, 2026-11-11). Is that the intended CGO for these hosts? Do ABIOCs, the causality view and the Causality grouping artifact then treat every service-started chain on the host as owned by the same unnamed process, and does an agent version or setting resolve the executor to the service binary it becomes?

**Why we ask.** Every pulled step, every beacon-started transient unit, and 124 of 150 `causality_actor_process_image_name` predicates in the cards assume a named CGO (cortexsim-agent, apache2, nginx, …). On Ubuntu 24.04 none of those names can appear, so each such predicate is unsatisfiable however the step is launched. Ubuntu 24.04 is the default lab image.

**What the answer changes.** A Linux target preflight that reads the host's systemd version; whether cards may key on causality_actor_process_image_name for systemd-started chains; whether CGO-anchored scenarios must launch from a non-systemd spawner such as cron (whose chains had CGO `cron`).
- *if* Expected behaviour, no setting changes it → Cards key on actor_process_* and action_process_* only for Linux service chains; preflight warns on systemd >= 255; cgo_anchor reads as a label, never a claim.
- *if* An agent version or setting names the real binary → Preflight gates CGO-dependent scenarios on that version or setting and records it per run.

**Could be settled in the lab.** Repeat probe pmqfba025 (beacon step, systemd-run with a renamed ExecStart, cron) on Ubuntu 22.04 (systemd 249) and Ubuntu 24.04 (systemd 255) with the same agent version; compare causality_actor_process_image_name, path and sha256 per case.

**Background — our current working model of CAU.** The agent-side engine that assembles a process tree per chain, names one CGO as the owner, and gives every event in the chain one causality_id. Alerts inherit the causality_id of the node they fire on. Which process becomes the CGO decides what the chain is "about" and what rare-parent logic sees.

### PM-CAU-08 — P1 — Causality and the Causality Group Owner (CGO)

**Question.** Is a CGO's identity frozen at the process start the agent observed (so a process that later execs into another binary keeps its first image name), and is a process that was already running when the agent started recorded under its current image instead? Concretely: a beacon started before the XDR agent was installed was recorded as CGO `cortexsim-agent`; the same beacon re-enrolled after the agent was running was recorded as CGO `9`.

**Why we ask.** The two readings come from two lab sessions on pov-target-01 (2026-11-10, beacon installed before the agent; 2026-11-11, re-enrolled after). If start order decides the CGO, the same scenario scores differently depending on when the DC installed the beacon relative to the XDR agent.

**What the answer changes.** The install order the onboarding runbook prescribes (beacon before or after the XDR agent, or a beacon restart after agent install), and a run-record field naming the CGO actually observed.

**Could be settled in the lab.** With the agent running, start the beacon from systemd, then restart the XDR agent and run one step; then restart the beacon and run one step. Compare causality_actor_process_image_name across the three steps.

**Background — our current working model of CAU.** The agent-side engine that assembles a process tree per chain, names one CGO as the owner, and gives every event in the chain one causality_id. Alerts inherit the causality_id of the node they fire on. Which process becomes the CGO decides what the chain is "about" and what rare-parent logic sees.

### PM-COR-08 — P0 — Correlation rules

**Question.** Can any correlation rule relate two sources within 60 seconds? A Real Time rule may use only dataset, datamodel, filter, alter, fields and config stages (no join), and a Scheduled rule runs at most every 10 minutes. Can a Real Time rule read two sources through one datamodel query without a join, and does its alert then group with the other source's alert?

**Why we ask.** Nine scenarios declare `correlation_window_seconds` of 60 or less with a Correlation expectation (SIM-MP-001 to SIM-MP-006, SIM-EMAIL-003/004, SIM-TIM-001); SIM-MP-006 declares 10 s. No card correlation rule carries a mode, cadence or time frame, and 158 of 214 are written as `BIOC(x) AND BIOC(y)`.

**What the answer changes.** Whether `correlation_window_seconds` <= 60 is a valid declaration at all; the card schema for correlation rules (mode, cadence, query time frame, severity).
- *if* No single rule can relate two sources inside 60 s → The loader rejects windows under 600 s for Correlation expectations and those nine scenarios move to a 10-minute cadence.
- *if* A Real Time datamodel query can → The card schema gains `mode: real_time` with a stage allowlist the validator enforces.

**Background — our current working model of COR.** Authored, scheduled XQL that produces alerts, can join datasets, and is the mechanism behind most of our cross-plane `Correlation` detections (209 across the card corpus; the coverage gate holds the corpus at a 10 % Correlation share). Schedule, lookback and time basis decide whether a run is ever inside an evaluated window.

### PM-IOC-06 — P1 — IOC matching and threat intel

**Question.** IOC rules accept Full Path, File Name, Domain, Destination IP, MD5 and SHA256. Can a URL, user-agent, email sender, cloud account id or SHA-1 indicator ever raise an issue against agent or NGFW data, for example as a TIM indicator, or do those types only enrich?

**Why we ask.** 73 of 439 card IOCs use types outside the IOC-rule list (url 32, user-agent 26, email-sender 9, cloud-account-id 4, sha1 1, registry-key 1), and 99 are IPv4, which match only as a destination.

**What the answer changes.** Whether those card IOCs move to XQL hunts or TIM-backed expectations, and which IOC types the validator accepts.

**Background — our current working model of IOC.** Indicator-value matching (hash, IP, domain, URL) against agent and network data, fed by manually added indicators or TIM feeds. 410 IOCs across the card corpus, 353 of which declare `reputation_required`.

### PM-IDN-01 — P0 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** Which user does an issue report for a Linux process chain, and which user does the UserName grouping artifact use: the CGO's (causality_actor_effective_username), the parent's (actor_effective_username) or the process the rule matched (action_process_username)? In the lab the CGO had a null user while the matched process ran as `pov-target-01\www-data` (IDN known). Does an issue whose CGO has no user carry a user at all?

**Why we ask.** The identity harness runs Linux steps as service accounts so a chain looks owned by www-data or postgres (harness.go:7), and 13 scenarios key on `stitching_key: user_principal`. If the issue takes its user from the CGO, every systemd-started chain on a modern host reports no user and cannot group by user, whatever account ran the step.

**What the answer changes.** Whether identity steps must make the service account own the chain (for example a CGO started as that user outside systemd) or only the matched process; whether `user_principal` is a satisfiable stitching key for Linux endpoint steps.
- *if* The issue's user is the CGO's user → Identity-dependent Linux scenarios need a CGO that runs as the account; until then they declare no user-keyed grouping.
- *if* The issue's user is the matched process's user → runuser -u is enough; switch the harness from `runuser -l` (needs a login shell) to `runuser -u … --`.

**Could be settled in the lab.** Fire one Medium custom BIOC on a child of `runuser -u www-data -- /bin/sh -c '<canary>'` launched by the beacon, and one on the same child launched by `systemd-run --uid=www-data`; read the issue's user fields and the case it joins.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-02 — P0 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** Do Identity Analytics and ITDR detectors evaluate activity whose acting user is NT AUTHORITY\SYSTEM or the machine account (`WS01$`), for example a Kerberos service-ticket burst requested from a SYSTEM-context PowerShell? Or are machine and system principals excluded, so the attack must run as a domain user to be seen?

**Why we ask.** A pulled Windows step runs as the beacon's service account because Windows identities collapse to `direct` (platform.go:212). The identity-lab design plans Kerberoast and AS-REP steps from native PowerShell on a workstation through the beacon. If the detectors skip machine and SYSTEM principals, every such step is a silent miss.

**What the answer changes.** How a Windows identity step is launched: through the beacon as SYSTEM, or as a domain user (a scheduled task or a process started with that user's stored credential), and whether the beacon needs a run-as path for Windows.
- *if* Machine and SYSTEM principals are excluded → Windows identity scenarios need a domain-user execution path before any identity expectation is declared.
- *if* They are evaluated → Pulled steps as SYSTEM are valid; record the requesting principal per run.

**Could be settled in the lab.** On the identity lab, request service tickets for 10 SPNs from a SYSTEM-context process and, separately, from a domain user's logon session; compare 4769 events and any Identity Analytics issue after the training clock.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-03 — P0 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** What does one `pan_dss_raw` row hold: one object per sync (a snapshot), or one row per change? Which field names carry the object type (user, group, computer), sAMAccountName, userPrincipalName, objectSid, memberOf, adminCount, userAccountControl, servicePrincipalName, description and the sync time?

**Why we ask.** The identity-lab design proves the CIE sync with a canary user whose description carries a nonce, and its day-one detections join DC events to `pan_dss_raw` on these attributes. No card reads `pan_dss_raw` today, and the field names are unverified.

**What the answer changes.** The sync-canary query, the join keys of every CIE-context card, and whether a join must take the latest row per object.

**Could be settled in the lab.** With CIE linked, create a canary user, change its description twice 10 minutes apart, add it to a group; query `pan_dss_raw` for it after each change and record row count and field names.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-04 — P0 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** Do the Identity Analytics detectors read CIE attributes as inputs, so that a Kerberoast against an account fires only when CIE already shows that account with a servicePrincipalName (or adminCount = 1), and an AS-REP request only when CIE shows DONT_REQ_PREAUTH? Or do they read the DC's events alone and use CIE only to enrich the issue?

**Why we ask.** CIE must be linked to activate the Identity Analytics rules (IDN known), but the docs do not say what the detectors read from it. The lab seeds bait accounts (an SPN-backed `svc-sql`, a no-preauth `helpdesk-admin`) and runs the attack minutes later.

**What the answer changes.** Scenario pacing: whether each bait account must be visible in `pan_dss_raw` before the attack step runs, and whether the bait's privilege must be set in AD itself or is irrelevant to the detector.
- *if* Detectors read CIE attributes → Each identity scenario gains a sync-wait gate between seeding and attack, proven by the canary.
- *if* Detectors read events only → Seeding can happen at build time; CIE only matters for enrichment and custom joins.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-05 — P1 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** How long after a change in Active Directory (a new user, a group membership, a description edit) does it appear in `pan_dss_raw`, and does an issue raised in between carry the old or the new attributes? Docs say incremental sync runs every 5 minutes: is that from the agent's last run, and are deletions synced as tombstones or by absence?

**Why we ask.** Identity scenarios often seed a change and abuse it minutes later ("add user to a privileged group, then use it"). If the issue reads CIE context at alert time, a change that has not synced yet makes the detection describe the user wrongly or not fire.

**What the answer changes.** The minimum wait between a seeding step and the attack step; the staleness threshold of the sync canary (the design waits 10 minutes).

**Could be settled in the lab.** Rewrite the canary's description at T0 and poll `pan_dss_raw` every 30 s for 20 minutes; repeat 5 times and record the delay distribution. Delete a test user and see whether a row says so.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-06 — P0 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** Where does a domain controller's Kerberos and logon telemetry land when it is collected by the XDR agent with XTH: xdr_data `event_type = EVENT_LOG` with the event id and data fields, or a separate dataset? Which fields carry the 4769 ticket encryption type, service name and client address, and the 4768 pre-authentication type? When the same events come through the Broker VM Windows Event Collector, are they in `microsoft_windows_raw` under different names?

**Why we ask.** Nine cards read `msft_windows_security`, a dataset name a tenant does not create, and the AD emitter writes records under that name (ad_windows_emitter.py:66). Card queries and emitter fields are both built on an unverified shape.

**What the answer changes.** The AD emitter's field names and dataset; the dataset and fields every AD card queries.
- *if* Agent events land in xdr_data EVENT_LOG and WEC in microsoft_windows_raw → Cards carry two query variants and the emitter targets the WEC shape only.
- *if* Both land in one dataset with one schema → One card variant; the emitter targets that schema.

**Could be settled in the lab.** On a lab DC with the agent (XTH) and WEC both collecting, request one service ticket with RC4 and one AS-REP for a no-preauth account; query xdr_data, `microsoft_windows_raw` and the dataset list for both events.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-07 — P0 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** Can Windows Security events that arrive through an HTTP log collector (what the AD emitter sends) feed any Identity Analytics or ITDR detector, even with an XDM modeling rule mapping them? Or do those detectors read only events collected by the XDR agent with XTH or by the Broker VM Windows Event Collector, as their Required Data lists suggest?

**Why we ask.** The AD emitter backs ABIOC and Analytics expectations for Kerberoasting, pass-the-hash and DC logons (ad_windows_emitter.py:27). If those detectors never read collector data, each such expectation is unsatisfiable by construction and would read `missed` on a healthy tenant.

**What the answer changes.** Whether AD-emitter steps may declare Analytics or ABIOC expectations, or only XQL and correlation ones; the ingestion route marked for the AD source.
- *if* Agent or WEC only → Mark the AD emitter's analytics route not_armable and move its expectations to custom XQL or correlation rows.
- *if* XDM-modeled collector data works → Document the modeling rule as a tenant prerequisite and preflight it.

**Could be settled in the lab.** With a modeling rule mapping the emitter's records, POST a 4769 burst for 10 SPNs from one account through the collector on a tenant past the training clock; compare with the same burst produced on a real DC.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-08 — P1 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** When one CIE instance syncs more than one directory (on-prem AD, Entra ID, Okta), does it merge accounts that belong to one person into one identity, on which attribute, and which field on an issue names the merged identity?

**Why we ask.** Cross-source identity scenarios plant one principal in AD, Entra and Okta records and expect one person to resolve across them (analytics_emitter.py canary bindings); nothing says the product merges them.

**What the answer changes.** Which attribute the emitters must keep identical across sources (UPN, email, objectSid, an immutable id).

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

### PM-IDN-09 — P1 — Identity resolution (CGO ownership, Cloud Identity Engine, Active Directory)

**Question.** Does Cortex have a native decoy or honey-identity feature (an account flagged by a CIE attribute, a group or a tenant setting) that raises an issue on any authentication, or is "honey account touched" only ever a custom rule?

**Why we ask.** The identity-lab design seeds a honey account and plans a custom join for it. If a native feature exists, the scenario should exercise that instead and the custom rule is redundant.

**What the answer changes.** How the lab marks its honey account and which expectation type the scenario declares.

**Background — our current working model of IDN.** How Cortex decides who an event, a chain or an alert belongs to: the user fields on each process scope of a causality chain, the directory identity the Cloud Identity Engine (CIE) syncs into `pan_dss_raw`, and the Kerberos and logon events a domain controller produces. CortexSim plants identity three ways: the identity harness runs Linux steps as service accounts, the AD and IdP emitters POST shape-true logon records, and the identity-lab design builds a real domain joined to CIE. Whether any of these reaches an identity detector is unproven.

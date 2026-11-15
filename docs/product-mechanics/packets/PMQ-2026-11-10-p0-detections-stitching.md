---
packet: PMQ-2026-11-10-p0-detections-stitching
created: '2026-11-10'
from: personal-mac / cortex-pov-engine
source: personal-mac
reply_to: _RoB/product-mechanics/answers/PMA-2026-11-10-p0-detections-stitching.md
questions:
- PM-STI-12
- PM-CAU-04
- PM-BIO-07
- PM-INC-05
- PM-TEN-05
- PM-TEN-06
- PM-ALR-01
- PM-ALR-02
tags:
- product-mechanics
- question-packet
---

# Product-mechanics questions — PMQ-2026-11-10-p0-detections-stitching

8 questions about how Cortex turns telemetry into a detection. CortexSim generates signal into customer tenants for POVs; when a signal does not fire, the POV report reads it as "Cortex missed it". These answers decide what the generator emits.

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

Write one Markdown file to `_RoB/product-mechanics/answers/PMA-2026-11-10-p0-detections-stitching.md` (vault-relative) shaped exactly like this — frontmatter,
then exactly one ```yaml block. The ingest step rejects any other shape, and rejects the whole
file if any source is not `distribution: public`.

````text
---
packet: PMQ-2026-11-10-p0-detections-stitching
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
      - Optional new question the answer raised.
```
````

## Questions

### PM-STI-12 — P0 — Stitching (data fusion across sources)

**Question.** Do firewall-shaped records that arrive through a custom HTTP log collector ever enter stitching, or does only the native NGFW integration produce stitched stories? If a custom-collector dataset can be stitched, what does it take: a specific Vendor/Product pair, a modeling rule onto XDM network fields, or something else?

**Why we ask.** The docs tie stitching to the native NGFW integration and say IOC and BIOC issues exist only on stitched data (STI known). 62 of 87 EAL plugin references in the multi-plane and NDR scenarios use ngfw_eal_emitter or ngfw_threat_emitter, which POST to a custom collector. If those records are never stitched, every stitched expectation on them is unsatisfiable.

**What the answer changes.** Which detection types NGFW-emitter scenarios may declare (XQL and correlation only, or also stitched BIOC/IOC); whether the emitters need a modeling rule; MOAT claims on those scenarios.
- *if* only the native integration stitches → Cap NGFW-emitter scenarios at XQL and correlation detections; add a loader warning for stitched expectations on emitter-only network legs.
- *if* a modeled custom dataset can stitch → Ship the modeling rule with the emitter and add a stitch-proof probe to the collector preflight.

**Could be settled in the lab.** Make one real agent connection, then POST a firewall record carrying the same 5-tuple through a custom collector; query `network_story` for a stitched row.

**Background — our current working model of STI.** The engine that links records from different sensors (agent network events, NGFW sessions, identity logs, cloud audit) into one story. Our working model: a join on shared entities, chiefly the network 5-tuple for network-endpoint, inside a time tolerance. Henry confirmed in practice that the source half has to match what the agent recorded and the destination half has to match the firewall transaction. Which fields, which side of NAT and what tolerance are still unconfirmed.

### PM-CAU-04 — P0 — Causality and the Causality Group Owner (CGO)

**Question.** Does the agent drop, sample, throttle or collapse process-start events: very short-lived processes, known system binaries, or several identical starts (same parent, image and command line) inside a short window? If so, what are the window and the count, and are the children of a dropped process dropped with it?

**Why we ask.** Raised from P1 on lab evidence (CAU known, 2026-11-10): three identical `sh` starts from the beacon within about 100 ms vanished with all their children, taking steps 3–5 of SIM-EDR-003 with them, while the same commands spaced 2 s apart in one shell were all recorded. Each step is a fresh `sh` with the same argv[0] (`apache2`, the CGO masquerade), so every multi-step scenario looks like a burst of identical starts.

**What the answer changes.** Inter-step pacing in the beacon (a minimum gap between step launches), or giving each step a distinct parent or command line; whether a missing step is scored `missed` or `not_recorded`.
- *if* identical rapid starts are throttled or deduplicated → Add a minimum inter-step gap above the window to the beacon, and stop scoring steps the agent never recorded as missed.
- *if* nothing is dropped by design → Treat the lab loss as a collection fault; check agent mode and resource limits before relaunching.

**Could be settled in the lab.** Launch 5 identical `sh -c 'id'` from one parent at 0, 50, 200, 500 and 1000 ms spacing; count the process events per spacing in xdr_data.

**Background — our current working model of CAU.** The agent-side engine that assembles a process tree per chain, names one CGO as the owner, and gives every event in the chain one causality_id. Alerts inherit the causality_id of the node they fire on. Which process becomes the CGO decides what the chain is "about" and what rare-parent logic sees.

### PM-BIO-07 — P0 — BIOC matching

**Question.** Since BIOC issues exist only on stitched data, which datasets and event types can a custom BIOC predicate address? Can a BIOC be written against firewall fields at all (a raw `panw_ngfw_traffic_raw` field, or the NGFW half of a stitched network story), or only against agent event types (process, file, network, registry and so on)?

**Why we ask.** Nine NDR card BIOCs put their predicate on a raw firewall dataset (BIO assumes). If a BIOC cannot address raw data, those nine are manufactured misses, and the right artifact is a correlation rule on the raw dataset or a BIOC on the agent's own network event.

**What the answer changes.** Rewrite the nine NDR card BIOCs as correlation rules, or retarget them to stitched network-event fields; a lint rule that refuses a BIOC whose logic names a `*_raw` dataset.
- *if* BIOCs address agent event types only → Move the nine to `correlation_rules[]` and add the lint rule.
- *if* stitched NGFW fields are BIOC-addressable → Retarget the nine to those fields and document which.

**Background — our current working model of BIO.** Behavioral IOC rules: a predicate over agent event fields (process image, command line, file path, registry key, network fields, actor scopes). Our working model is deterministic matching on the event, no baseline. 365 BIOCs across the card corpus, 40 of which carry a suppression window.

### PM-INC-05 — P0 — Alert-to-incident grouping

**Question.** Does the Medium-severity floor for case creation and grouping apply to issues from every source (custom BIOC, Analytics BIOC, Analytics, IOC, XDR agent prevention, third-party ingested alerts), or only to correlation-rule issues, where the docs state it? Is Informational treated like Low?

**Why we ask.** The docs state the floor on the correlation-rule page; Henry reports it more broadly (INC known). 55 scenarios declare required_planes_in_incident, and the built-in alerts the lab raised were Low (ABI known). If the floor is universal, any plane whose alert is below Medium can never be under the case, and the correlation-rate KPI scores that as a miss.

**What the answer changes.** A severity floor on every detection a scenario counts toward required_planes_in_incident (a loader lint); a `not_groupable` reading in the correlation-rate KPI for below-floor alerts instead of a miss; card severities raised to Medium where a scenario needs grouping.
- *if* the floor applies to every source → Add the lint and the not_groupable reading; raise the 16 below-Medium card detections or drop them from required planes.
- *if* it applies to correlation issues only → Record the per-source rule and enforce it only for Correlation detections.

**Could be settled in the lab.** On one host, raise a custom BIOC at Low, the same BIOC cloned at Medium, and a Medium correlation issue sharing the host; see which issues the case holds.

**Background — our current working model of INC.** The logic that groups alerts into incidents. Our correlation-rate KPI asserts ">= N distinct planes under one incident_id", so the grouping key, not the stitch, decides whether a multi-plane scenario scores. The XSIAM docs now say "issue" for alert and "case" for incident.

### PM-TEN-05 — P0 — Tenant readiness (content, entitlements, data collection)

**Question.** After an XDR agent is installed, restarted or upgraded, how long until it collects events, and which tenant-readable field says collection is running (endpoint status, operational status, a per-module state)? Is CONNECTED reported before collection starts?

**Why we ask.** In the lab a run 15 s after CONNECTED left no events at all because the collectors had not started (TEN known). A run against a just-connected agent scores every expectation as missed.

**What the answer changes.** An agent-ready gate before launch: refuse or delay a run until the endpoint reports collection running, and read it from the endpoint record rather than guessing a delay.

**Could be settled in the lab.** Restart the agent, then every 5 s record the endpoint's status fields from the API while a 1 Hz canary process runs; note when the first canary event lands.

**Background — our current working model of TEN.** The state of the tenant that decides whether a detector can fire at all: which content is enabled, which licence or add-on gates an engine, what the agent profile collects. A detector that is off reads exactly like a miss.

### PM-TEN-06 — P0 — Tenant readiness (content, entitlements, data collection)

**Question.** Can an endpoint's Linux operation mode (Kernel, User Space, asynchronous) be read from the endpoint record through the API, so a preflight can refuse a host that collects nothing?

**Why we ask.** In asynchronous mode the agent collects no events at all (TEN known), so every BIOC, ABIOC, causality and stitch expectation on that host reads as missed while the endpoint shows CONNECTED.

**What the answer changes.** An agent-mode rung in the launch preflight; refuse or label runs on an asynchronous host.

**Could be settled in the lab.** Read the endpoint record of one Kernel-mode host and one installed with `--no-km`; find the field that differs.

**Background — our current working model of TEN.** The state of the tenant that decides whether a detector can fire at all: which content is enabled, which licence or add-on gates an engine, what the agent profile collects. A detector that is off reads exactly like a miss.

### PM-ALR-01 — P0 — Alert records and read-back

**Question.** Which field on an alert or issue record carries the identifier of the rule that raised it, for each source: custom BIOC, Analytics BIOC, Analytics, correlation rule, IOC, XDR agent prevention? Is it stable across content updates and tenants?

**Why we ask.** Lab alerts carried none of the four keys the connector reads (ALR known), so matching fell back to technique plus name, the weakest key the matcher has.

**What the answer changes.** The connector's rule-id field list (the Issues API documents `detection.rule_id`, which the connector does not read); card detections record that identifier so the matcher can key on it; preflight stops reporting PARTIAL.

**Background — our current working model of ALR.** What an alert ("issue") looks like when SimCore pulls it back: which field names the rule that fired, which timestamp is the detection time, which MITRE technique and source string it carries, and which case it joined. The matcher credits a seeded Result from these fields alone, so a misread field turns a real detection into a miss, or a noise alert into a pass.

### PM-ALR-02 — P0 — Alert records and read-back

**Question.** Which timestamp on an alert is the time of the event that triggered it, and which is the time the platform raised the alert: `detection_timestamp`, `creation_time`, `local_insert_ts`, or another field? Does the answer differ by alert source?

**Why we ask.** MTTD is the alert time minus the step's executed_at, and the connector takes detection_timestamp first (ALR assumes). If detection_timestamp is the triggering event's own time, every MTTD we report measures nearly zero instead of detection latency, and it would pass any threshold.

**What the answer changes.** The connector's timestamp order; the MTTD definition in the report.
- *if* detection_timestamp is event time → Use the creation or insert time for MTTD and keep the event time as evidence of which step fired it.
- *if* detection_timestamp is raise time → Keep the order and document it as proven.

**Could be settled in the lab.** Fire a scheduled correlation rule (10-minute grid) on a canary event; compare each timestamp field with the canary's `_time` and the rule's run time.

**Background — our current working model of ALR.** What an alert ("issue") looks like when SimCore pulls it back: which field names the rule that fired, which timestamp is the detection time, which MITRE technique and source string it carries, and which case it joined. The matcher credits a seeded Result from these fields alone, so a misread field turns a real detection into a miss, or a noise alert into a pass.

# Multi-Plane Stitching MOAT — Detection Methodology Deep-Dive

**Parent doc:** [`v2.0-methodology-master.md`](v2.0-methodology-master.md)
**Family:** F2 — Causality Stitching Validation
**Scope:** All TCs whose Primary KPI is `Cross-Source Correlation Rate` or `Causality Chain Completeness`, plus stitching-flavored MOAT TCs from IR / NDR / ITDR / ERV / CITH families
**TC count:** 45 candidates · **16 MOAT** · 12 LEAD · 17 PARITY
**Owner:** Henry Reed · Last updated 2026-05-27

---

## 1 · Why this is the Cortex moat

The competitive position columns in the v2.0 master sheet (CrowdStrike, Microsoft, SentinelOne) are at their weakest where the test case requires **stitching across planes**. Any best-of-breed EDR can detect a process anomaly. Any NGFW can flag suspicious egress. Any IdP can detect impossible-travel. **Cortex's defensible moat is producing one incident from all three, with full causality**, automatically, in seconds — without an analyst writing a correlation rule.

This deep-dive defines the detection methodology for that moat across all 45 candidate TCs, focusing engineering depth on the 16 MOAT-tier TCs that defend the strongest positioning.

---

## 2 · The shared F2 validation pattern

Every F2 TC follows the same harness shape. The variables change; the structure does not.

```
                ┌─ NDR signal   ─┐
shared_key ──── ├─ EDR signal   ─┤ ──► XSIAM stitch ──► single incident ──► XQL verifier
(host/session/  └─ ITDR signal  ─┘                          │
identity ±60s)                                              │
                                                            ▼
                                                  causality_chain.length ≥ N
                                                  planes_represented = expected_set
                                                  mttd ≤ threshold
```

**Five required properties of any F2 scenario:**

1. **Shared key** — a `src_host` or `session_id` or `user_principal` value that links all signals. Without this, stitching is impossible by design.
2. **Bounded time window** — the signals fire within ±60s (XSIAM's default correlation window). Validation harness must respect this.
3. **Plane diversity** — at minimum 2 of {EDR, NDR, ITDR, CDR, CLOUD_APP}. Single-plane scenarios are F1, not F2.
4. **Verifier XQL targets `dataset=xdr_data`** — the causality view is where stitching evidence lives. Querying `dataset=incidents` alone misses the chain.
5. **Threshold is a percentage, not a count** — `≥80% of test runs produce one stitched incident` beats `produced one incident on this run` because flaky correlation is a real-world failure mode worth catching.

This pattern is already encoded in `scenarios/multi_plane/mp-001-c2-beacon-ngfw-xdr-stitch.yml`. Every F2 TC below either reuses that file's structure or extends it.

---

## 3 · MOAT TC catalog (16 TCs)

For each MOAT TC: the simulation input, the planes involved, the expected stitched signal, the XQL verifier, and which SIM-MP-* scenario file implements it (existing or to-be-created).

### TC-IR-05 — Correlation-based detections across endpoint + network + identity

**KPI:** Cross-Source Correlation Rate ≥ 80 %
**Planes:** EDR + NDR + ITDR
**Scenario file:** `scenarios/multi_plane/mp-005-cross-plane-correlation.yml` *(proposed)*
**Status:** Net-new — closest existing analog is SIM-MP-002 (Kerberoast chain) but that's ITDR+EDR only.

**Simulation input.** Three coordinated signals from one `src_host`, ±30s window:
1. **NDR:** outbound HTTP beacon to a known IOC domain (reuse testmynids.org pattern).
2. **EDR:** `bash` spawned from `www-data` service context running `curl` (the beacon's process).
3. **ITDR:** `kinit` against the AD lab from the same host with a stale service-account credential (reuses the ITDR module's seeded service accounts).

**Expected stitched signal.** One XSIAM incident with `causality_chain.length ≥ 5` and `planes ⊇ {EDR, NDR, ITDR}`.

**Verifier XQL.**
```xql
dataset = xdr_data
| filter event_sub_type = "Process" and process_command_line contains "curl"
| join (
    dataset = pan_ngfw_traffic
    | filter dst_host = "testmynids.org"
  ) as net on (action_local_ip = net.src_ip)
| join (
    dataset = ad_audit
    | filter event_id = 4768 and pre_auth_failed = true
  ) as ident on (action_local_ip = ident.client_ip)
| comp count_distinct(incident_id) as stitched_incidents by action_local_ip
| filter stitched_incidents = 1
```

A pass means: across all three plane datasets there's exactly one `incident_id` bridging the same `action_local_ip` — that's the stitching working.

### TC-NDR-01 — Causality graph stitching: network events ↔ endpoint process telemetry

**KPI:** Causality Chain Completeness (qualitative — chain must include both the NGFW session and the originating process)
**Planes:** NDR + EDR
**Scenario file:** `scenarios/multi_plane/mp-001-c2-beacon-ngfw-xdr-stitch.yml` ✓ *(exists, well-aligned)*
**Status:** Existing scenario IS the canonical TC-NDR-01 implementation. Action: backfill `tc_ref: TC-NDR-01` into the YAML and add the verifier XQL.

**Verifier XQL.** Same as TC-IR-05 but with the ITDR clause removed.

### TC-NDR-02 — SOC-specific network story enriched with endpoint causality

**KPI:** Causality Chain Completeness
**Planes:** NDR + EDR
**Scenario file:** `scenarios/multi_plane/mp-002-network-story-edr-enrich.yml` *(proposed)*

**Simulation input.** Distinct from NDR-01: this is about *story enrichment*, not just stitching. Run a sequence where the NGFW sees an unusual destination, then the verifier checks whether the resulting XSIAM "Network Story" UI panel includes the process tree of the spawning binary on the endpoint. The simulation is the same C2 beacon, but the assertion is on the rendered Story content (via the `/public_api/v1/insights/network_stories` endpoint), not on the incident grouping.

### TC-NDR-05 — Broker VM normalization of 3rd-party network data

**KPI:** Cross-Source Correlation Rate (≥ 90 % field mapping)
**Planes:** NDR via Broker VM ingestion of non-PAN logs
**Scenario file:** `scenarios/ndr/sim-ndr-006-broker-vm-3rdparty-normalization.yml` *(proposed)*
**Family:** This is technically F8 (Integration & Ingestion) but the moat positioning is cross-source — keeping it here.

**Simulation input.** Synthetic Zeek / Suricata / Fortinet logs through the Broker VM. Verifier checks that all source-specific fields land in the unified `dataset=cef_*` or `dataset=zeek_*` with consistent `src_ip` / `dst_ip` / `bytes_sent` keys regardless of source.

### TC-ERV-02 — NGFW/Runtime event stitching into unified incident timeline

**KPI:** Cross-Source Correlation Rate
**Planes:** NDR (NGFW) + CDR (Cortex Cloud Runtime)
**Scenario file:** `scenarios/multi_plane/mp-006-ngfw-runtime-stitch.yml` *(proposed)*
**Status:** Net-new. Closest existing analog is SIM-MP-001 but that's NDR+EDR; ERV-02 specifically needs CDR runtime events stitched with NGFW session logs — different telemetry plane.

**Simulation input.** Spin up the CDR demo workload (existing `infra/modules/aws/cdr/`). Run a container that does egress to a known-bad domain. Expect XSIAM to stitch the NGFW outbound session with the Cortex Cloud runtime "process in container made network call" event into one incident.

### TC-ERV-05 — Code-to-Cloud-to-SOC traceability

**KPI:** Automation Rate (this is F9 leakage into F2 — see master doc §3 F9)
**Planes:** CSPM (code/IaC) + CDR (runtime) + ANALYTICS (SOC alert)
**Scenario file:** `scenarios/multi_plane/mp-007-code-to-cloud-to-soc.yml` *(proposed)*

**Simulation input.** The F9 pattern from the master doc: tag a commit with a nonce, build/push an image with the nonce as LABEL, deploy a pod with the nonce as annotation, run something that fires a runtime alert. Verifier asserts the resulting XSIAM incident has metadata linking back to git commit SHA + image SHA + pod identity — all three layers traceable.

### TC-ERV-06 — ITDR + Asset Security cumulative rolling risk attribution

**KPI:** Automation Rate (risk score increments deterministically)
**Planes:** ITDR + Asset Security (XSIAM's asset score subsystem)
**Scenario file:** `scenarios/multi_plane/mp-008-rolling-risk-attribution.yml` *(proposed)*

**Simulation input.** Sequence of identity-anomaly events on the same user over a 24h window (low-severity → medium → high). Verifier polls `dataset=asset_risk_scores` and asserts the score curve is monotonically increasing and crosses the alert threshold at the expected step. This is a *temporal* stitching TC — different from spatial cross-plane stitching, but same F2 family.

### TC-ASM-03 — Unit 42 TI integrated into retroactive search

**KPI:** Cross-Source Correlation Rate
**Planes:** TIM + ASM + historical XDL data
**Scenario file:** `scenarios/multi_plane/mp-009-retroactive-ioc-asm.yml` *(proposed)*

**Simulation input.** Plant a known IOC (using `mocktaxii` from `infra/modules/aws/tim/`). Run the historical asset traffic through the XDL (replay from `infra/modules/aws/telemetry-replay/` if available). Push a new IOC to the TIM feed *after* the traffic. Verifier asserts that XSIAM retroactively raises an incident on the historical traffic when the new IOC arrives — that's the cross-time stitching moat.

### TC-CITH-07 — AI-Powered Alert Stitching (≥80 % noise reduction)

**KPI:** Volume reduction % (≥ 80)
**Planes:** ANALYTICS (the meta-plane — measures incident-grouping behavior)
**Scenario file:** `scenarios/multi_plane/mp-010-ai-alert-stitching-noise-reduction.yml` *(proposed)*

**Simulation input.** Inject 1,000 alerts with a known clustering structure (200 distinct root causes × 5 related alerts each). Verifier polls `dataset=incidents` after the AI stitching cycle and asserts:
- `count(distinct incident_id) ≤ 240` (clustering compressed 1000 → ~200, allowing 20 % slack)
- For each incident, `alert_count.in_incident ≥ 4` (most alerts found their cluster)

This TC's positioning hits CrowdStrike's Charlotte AI directly (the master sheet's `CrowdStrike Position` column says "PARITY" but with `alert-to-incident ratio` as the proof point — that's exactly what this scenario measures).

### TC-TIM-02 — Automated IOC-to-EDL push for real-time NGFW blocking

**KPI:** Cross-Source Correlation Rate (TIM IOC → NGFW EDL applied)
**Planes:** TIM + NDR
**Scenario file:** `scenarios/multi_plane/mp-011-tim-edl-push.yml` *(proposed)*

**Simulation input.** Push a new IOC to TIM via `mocktaxii`. Verifier polls the NGFW EDL endpoint (or `dataset=pan_dataplane_edl_updates`) and asserts the IOC appears in the active block list within the SLA. Then emit traffic to the IOC and assert the NGFW drops it. Two stitched signals: the EDL update event and the subsequent block event.

### TC-BYOML-02 — Datamodel customization for industry-specific correlation rules

**KPI:** Cross-Source Correlation Rate (customer-defined rule fires on customer-defined data)
**Planes:** ANALYTICS (any plane — the methodology IS the customization)
**Scenario file:** `scenarios/multi_plane/mp-012-byoml-custom-correlation.yml` *(proposed)*

**Simulation input.** Deploy a sample BYOML rule that correlates an industry-specific event pattern (the scenario should ship a sample rule for, say, "PCI cardholder-data zone outbound + DLP file event"). Inject the matching pattern. Verifier checks that the custom rule fired and produced an incident with the expected score.

### TC-IR-01, IR-02, IR-03 — Auto-triage, issue grouping, case scoring

These three are tightly bound: all measure XSIAM's AI-triage subsystem against a known alert volume.

| TC | KPI | Threshold | Scenario file |
|----|-----|----------:|---------------|
| TC-IR-01 | Triage Automation Rate | > 60 % | `scenarios/multi_plane/mp-013-ai-triage-1k-alerts.yml` *(proposed)* |
| TC-IR-02 | Detection Accuracy (grouping) | > 80 % | same scenario, different assertion |
| TC-IR-03 | Detection Accuracy (scoring rank) | qualitative | same scenario, different assertion |

**Simulation input.** Inject 1,000 alerts from Atomic Red Team (T1059, T1021, T1003, T1110) plus 500 benign-noise alerts. Three assertions on the same run:
- IR-01: ≥ 60 % of the 1500 alerts get `triage_state = auto_resolved` or `auto_escalated`
- IR-02: alerts with the same TTP family collapse into ≥ 80 % fewer incidents than alerts
- IR-03: the top-10 scored incidents include all 4 of the planted "high-malicious" Atomic chains

One YAML, three named assertions — this is why the schema needs the `kpi_contribution` field per detection (master doc §5).

### TC-IR-08 — Ingestion + normalization of 3+ heterogeneous data sources via Broker VM

**KPI:** Detection Accuracy (across sources)
**Planes:** F8-flavored but listed here because the moat narrative is multi-source unification
**Scenario file:** Same as TC-NDR-05 (`scenarios/ndr/sim-ndr-006-broker-vm-3rdparty-normalization.yml`)

---

## 4 · LEAD-tier TC catalog (12 TCs, brief)

These get the same F2 harness but lower design depth — they reuse MOAT scenarios with different fixtures.

| TC | Use Case | Reuses scenario |
|----|----------|-----------------|
| TC-CDR-02 | Cloud alert correlation w/ EDR + NDR | `mp-006` |
| TC-CDR-04 | CIEM over-permissioned identity recs | `mp-004` (existing APT29 cloud creds) |
| TC-CMTTR-01 | Causality View investigation | `mp-001`, assertion-only addition |
| TC-CVM-03 | Internet Exposure Correlation (ASM + vuln) | `mp-009` |
| TC-ITDR-01..06 (LEAD only) | Identity analytics + IdP integration | reuse `mp-002` Kerberoast scenario |

Full row-level mapping: see [`v2.0-tc-mapping-table.csv`](v2.0-tc-mapping-table.csv).

---

## 5 · PARITY-tier TC catalog (17 TCs)

PARITY TCs validate Cortex's *equivalence* to competitors. They share the same F2 harness but use lower-effort fixtures because the demo motion is "show that Cortex does it too" not "show that nobody else can." Recommended approach: cover them with one omnibus scenario per use-case-family (`mp-parity-dlp.yml`, `mp-parity-email.yml`, etc.) rather than one per TC.

---

## 6 · Schema impact for F2

Beyond the master-doc §5 additions, F2 scenarios need:

```yaml
correlation_window_seconds: 60
# (F2-required) The ±N second window inside which signals must fire to
# qualify as stitched. Default 60. Validates fixture timing against XSIAM's
# default correlation window.

required_planes_in_incident:
  - EDR
  - NDR
  - ITDR
# (F2-required) The set of planes that MUST be represented in the stitched
# incident's causality chain. Verifier asserts each is present.

stitching_key: src_host
# (F2-required) The field name in the verifier XQL used as the join key.
# One of: src_host | session_id | user_principal | container_id
```

These three fields, plus the master-doc additions, make F2 scenarios self-verifying.

---

## 7 · Build order for F2

| Priority | Scenario file | Coverage | Effort |
|---------:|---------------|----------|-------:|
| P0 | `mp-001` retrofit `tc_ref` + verifier XQL | TC-NDR-01 | XS |
| P0 | `mp-005-cross-plane-correlation.yml` | TC-IR-05 (flagship MOAT) | M |
| P1 | `mp-010-ai-alert-stitching-noise-reduction.yml` | TC-CITH-07 | M |
| P1 | `mp-013-ai-triage-1k-alerts.yml` | TC-IR-01/02/03 (3 TCs in 1) | M |
| P1 | `mp-006-ngfw-runtime-stitch.yml` | TC-ERV-02 | M |
| P2 | `mp-007-code-to-cloud-to-soc.yml` | TC-ERV-05 | L |
| P2 | `mp-009-retroactive-ioc-asm.yml` | TC-ASM-03 | L |
| P2 | `mp-011-tim-edl-push.yml` | TC-TIM-02 | M |
| P3 | `mp-008-rolling-risk-attribution.yml` | TC-ERV-06 | M |
| P3 | `mp-012-byoml-custom-correlation.yml` | TC-BYOML-02 | L |

P0 lands in this session if there's room after AI-SPM. P1–P3 are scheduled.

---

## 8 · Verification XQL pattern library

Reusable XQL fragments F2 scenarios will reference. Live in a follow-up `scenarios/multi_plane/_xql_fragments/` directory so they don't duplicate across YAML files.

```xql
-- fragment: stitched_incident_count_by_host
dataset = xdr_data
| comp count_distinct(incident_id) as stitched_incidents by action_local_ip
| filter stitched_incidents = 1

-- fragment: causality_chain_length
dataset = incident_causality
| filter incident_id = "{INCIDENT_ID}"
| comp count() as chain_length

-- fragment: planes_in_incident
dataset = xdr_data
| filter incident_id = "{INCIDENT_ID}"
| comp count_distinct(_product) as plane_count
| filter plane_count >= 3
```

The verifier harness substitutes `{INCIDENT_ID}` from the run record before submitting via `/public_api/v1/xql/start_xql_query`.

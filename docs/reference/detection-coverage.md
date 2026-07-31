# Detection Coverage & MITRE ATT&CK Map — Reference

> ## ⚠ SUPERSEDED — historical snapshot
>
> This document froze the detection surface at **63 cards / 58 scenarios**
> (`2026-06-07`, commit `b7eebc5`). The corpus is now **161 cards / 161 scenarios
> across 15 planes**. Every count, table, and gap ID below is a point-in-time
> record and must not be cited as current state.
>
> **Current authoritative sources:**
> - Inventory — [`scenario-catalog.md`](scenario-catalog.md)
> - Counted ground truth — [`README.md`](README.md)
> - Live coverage analysis — `make coverage` (`detection_scanner/scripts/coverage_report.py`)
> - UC/TC join + index gaps — [`../uc_tc_mapping/README.md`](../uc_tc_mapping/README.md)
>
> Kept because its Section 5 gap analysis is the provenance for several closed
> GAP items. Read it as history.


> **Scope.** This document is the canonical, exhaustive inventory of CortexSim's detection
> surface as of `2026-06-07` (branch `main`, commit `b7eebc5`). It cross-reads two parallel
> corpora that together constitute "what we can detect":
>
> 1. **TTP detection cards** — `detection_scanner/ttps/*.json` (**63 active cards**, IDs
>    `TTP-2026-0001` … `TTP-2026-0063`). These carry the *deployable detection logic*
>    (BIOC/XQL/correlation/IOC) plus PANW product mapping, UC/TC mapping, and Unit 42 threat
>    context.
> 2. **Scenario definitions** — `scenarios/**/*.yml` (**58 active scenarios** across 11 planes).
>    These carry the execution steps, identity harness config, and per-step
>    `expected_detections[]` that *reference* TTP cards via `ttp_ref`.
>
> The two are joined by `expected_detections[].ttp_ref` (card-level granularity). The runtime
> heatmap (`GET /api/mitre/coverage`) is computed from a **third** representation — the
> `Scenario` ORM rows loaded into SQLite from YAML — which is *not* the same as either corpus
> above and is materially thinner (see [Gaps](#gaps--weaknesses-opinionated)).
>
> **This document is opinionated.** Section 5 (the gap table) is the input to the
> detection-development phase. Read it as a backlog, not a status report.

---

## 0. Corpus at a glance

| Corpus | Count | Source of truth for | Carries deployable logic? |
|---|---|---|---|
| TTP cards | 63 | BIOC/XQL/correlation/IOC logic, PANW mapping, threat intel | **Yes** |
| Scenarios (active) | 58 | execution steps, identity, `expected_detections` refs | No (refs only) |
| Distinct ATT&CK techniques (cards) | **82** | ATT&CK matrix coverage | — |
| Distinct ATT&CK tactics covered | **12 of 14** | tactic breadth | — |
| `ttp_ref` links scenario→card | 59 distinct refs | traceability | — |

**Deployable-logic tally across all 63 cards:**

| Detection kind | Count | Where it lives |
|---|---:|---|
| XQL queries | **238** | `detections.xql_queries[]` |
| BIOC rules | **144** | `detections.biocs[]` |
| Analytics modules (named, not logic) | **143** | `detections.analytics_modules[]` |
| Correlation rules | **72** | `detections.correlation_rules[]` |
| IOCs | **27** | `detections.iocs[]` |

Every one of the 144 BIOCs has populated `logic`, every one of the 238 XQL queries has a
populated `query`, and every one of the 72 correlation rules has populated `logic`. There are
**no skeleton detection objects** in the card corpus — the `content(detections)` commit
(`42a49c0`) delivered real logic for all 63 cards. (The weaknesses are structural/traceability,
not "empty stubs" — see Section 5.)

---

## 1. MITRE ATT&CK Coverage Matrix

### 1.1 Tactic-level coverage (Enterprise matrix)

Counts are derived from the **card corpus** (`mitre_attack.techniques[].tactic_names`), which is
the authoritative, multi-technique view. A single card frequently spans multiple tactics.

| # | Tactic | ATT&CK ID | # Techniques | # Cards | Maturity |
|---|---|---|---:|---:|---|
| 1 | Reconnaissance | TA0043 | **0** | **0** | ❌ **none** |
| 2 | Resource Development | TA0042 | **0** | **0** | ❌ **none** |
| 3 | Initial Access | TA0001 | 7 | 21 | ✅ strong |
| 4 | Execution | TA0002 | 5 | 15 | ✅ strong |
| 5 | Persistence | TA0003 | 11 | 11 | 🟡 broad-but-shallow (mostly 1 card/technique) |
| 6 | Privilege Escalation | TA0004 | 3 | 5 | 🟡 thin |
| 7 | Defense Evasion | TA0005 | 14 | 16 | 🟡 broad-but-shallow |
| 8 | Credential Access | TA0006 | 15 | 27 | ✅ **deepest** |
| 9 | Discovery | TA0007 | 9 | 11 | 🟡 moderate |
| 10 | Lateral Movement | TA0008 | 6 | 8 | 🟡 moderate |
| 11 | Collection | TA0009 | 7 | 9 | 🟡 moderate |
| 12 | Command and Control | TA0011 | 8 | 13 | ✅ strong |
| 13 | Exfiltration | TA0010 | 5 | 12 | ✅ strong |
| 14 | Impact | TA0014 | 6 | 6 | 🟡 thin (1 card/technique) |

**Headline:** 2 of 14 tactics (Reconnaissance, Resource Development) have **zero** coverage —
acceptable for a post-compromise detection-QA tool, but worth a conscious decision (see GAP-1).
Credential Access is the crown jewel (27 cards, 15 techniques). Persistence, Defense Evasion,
and Impact are "wide but one-card-deep" — many techniques each backed by a single card.

### 1.2 Full technique → card → scenario → plane matrix

Every technique covered by the corpus, sorted by ATT&CK ID. Card IDs abbreviated `#NNNN`
(= `TTP-2026-0NNNN`). A blank "Scenarios" cell means the card is **wired to no scenario**
(standalone seed card; see GAP-3). "Planes" = the detection planes whose scenarios reach this
technique through `ttp_ref`.

| Technique | Name | Tactic(s) | #Cards | Cards | Scenarios | Planes |
|---|---|---|---:|---|---|---|
| T1003 | OS Credential Dumping | Cred Access | 1 | #0032 | SIM-EDR-001 | EDR |
| T1003.001 | OS Cred Dumping: LSASS Memory | Cred Access | 1 | #0002 | *(unwired)* | — |
| T1003.006 | OS Cred Dumping: DCSync | Cred Access | 2 | #0004 #0063 | SIM-MP-002 | ANALYTICS |
| T1003.008 | OS Cred Dumping: /etc/passwd & shadow | Cred Access | 2 | #0024 #0032 | SIM-CDR-003 SIM-EDR-001 | CDR EDR |
| T1005 | Data from Local System | Collection | 2 | #0020 #0048 | SIM-BROWSER-004 SIM-MP-003 | ANALYTICS BROWSER |
| T1016 | System Network Config Discovery | Discovery | 1 | #0036 | SIM-EDR-005 | EDR |
| T1018 | Remote System Discovery | Discovery | 2 | #0036 #0053 | SIM-EDR-005 SIM-NDR-004 | EDR NDR |
| T1021.001 | Remote Services (RDP) | Lateral Movement | 1 | #0025 | SIM-CDR-004 | CDR |
| T1021.002 | Remote Services: SMB/Admin Shares | Lateral Movement | 2 | #0053 #0063 | SIM-MP-002 SIM-NDR-004 | ANALYTICS NDR |
| T1021.004 | Remote Services: SSH | Lateral Movement | 3 | #0006 #0036 #0062 | SIM-EDR-005 SIM-NDR-007 | EDR NDR |
| T1027 | Obfuscated Files or Information | Defense Evasion | 1 | #0057 | SIM-AISPM-004 | AI_SPM |
| T1036.005 | Masquerading: Match Legit Name | Defense Evasion | 1 | #0035 | SIM-EDR-004 | EDR |
| T1041 | Exfiltration Over C2 Channel | Exfiltration | 1 | #0007 | SIM-AIACC-001 | AI_ACCESS |
| T1046 | Network Service Discovery | Discovery | 2 | #0036 #0053 | SIM-EDR-005 SIM-NDR-004 | EDR NDR |
| T1048.003 | Exfil Over Unencrypted Non-C2 | Exfiltration | 4 | #0005 #0048 #0051 #0061 | SIM-MP-003 SIM-NDR-002 SIM-NDR-005 SIM-NDR-006 | ANALYTICS NDR |
| T1053.003 | Scheduled Task/Job: Cron | Persistence | 2 | #0024 #0034 | SIM-CDR-003 SIM-EDR-003 | CDR EDR |
| T1053.005 | Scheduled Task/Job: Cron (k8s) | Execution/Persistence | 2 | #0023 #0025 | SIM-CDR-002 SIM-CDR-004 | CDR |
| T1056.003 | Input Capture: Web Portal Capture | Collection/Cred Access | 1 | #0017 | SIM-BROWSER-001 | BROWSER |
| T1059 | Command & Scripting Interpreter | Execution | 5 | #0012 #0013 #0015 #0042 #0043 | SIM-AIRS-001/002/004 SIM-KOI-001/002 | AIRS KOI |
| T1059.004 | Cmd & Script: Unix Shell | C2/Execution | 7 | #0006 #0022 #0026 #0033 #0047 #0049 #0060 | SIM-CDR-001/005 SIM-EDR-002 SIM-MP-001/004/005 | ANALYTICS CDR EDR |
| T1059.006 | Cmd & Script: Python | Execution | 3 | #0033 #0042 #0044 | SIM-EDR-002 SIM-KOI-001/003 | EDR KOI |
| T1070.002 | Indicator Removal: Clear Linux Logs | Defense Evasion | 1 | #0035 | SIM-EDR-004 | EDR |
| T1070.003 | Indicator Removal: Clear Cmd History | Defense Evasion | 1 | #0035 | SIM-EDR-004 | EDR |
| T1070.006 | Indicator Removal: Timestomp | Defense Evasion | 1 | #0035 | SIM-EDR-004 | EDR |
| T1071 | Application Layer Protocol | C2 | 1 | #0061 | SIM-NDR-006 | NDR |
| T1071.001 | App Layer Protocol: Web | C2 | 5 | #0026 #0033 #0047 #0050 #0060 | SIM-CDR-005 SIM-EDR-002 SIM-MP-001/005 SIM-NDR-001 | ANALYTICS CDR EDR NDR |
| T1074.001 | Data Staged: Local | Collection | 2 | #0005 #0048 | SIM-MP-003 SIM-NDR-005 | ANALYTICS NDR |
| T1078 | Valid Accounts | Def Evasion/Lat Mvmt | 1 | #0060 | SIM-MP-005 | ANALYTICS |
| T1078.004 | Valid Accounts: Cloud | DE/IA/Persist/PrivEsc | 8 | #0001 #0003 #0028 #0031 #0037 #0040 #0049 #0055 | SIM-AISPM-002 SIM-CLOUD-002/005 SIM-ITDR-001/004 SIM-MP-004 | AI_SPM ANALYTICS CLOUD_APP ITDR |
| T1082 | System Information Discovery | Discovery | 2 | #0014 #0022 | SIM-AIRS-003 SIM-CDR-001 | AIRS CDR |
| T1083 | File and Directory Discovery | Discovery | 1 | #0024 | SIM-CDR-003 | CDR |
| T1087.001 | Account Discovery: Local | Discovery | 1 | #0032 | SIM-EDR-001 | EDR |
| T1090 | Proxy | C2/Defense Evasion | 2 | #0011 #0030 | SIM-AIACC-005 SIM-CLOUD-004 | AI_ACCESS CLOUD_APP |
| T1098 | Account Manipulation | Persistence | 1 | #0055 | SIM-AISPM-002 | AI_SPM |
| T1098.003 | Acct Manip: Additional Cloud Creds | Persistence | 1 | #0028 | SIM-CLOUD-002 | CLOUD_APP |
| T1098.004 | Acct Manip: SSH Authorized Keys | Persistence | 1 | #0034 | SIM-EDR-003 | EDR |
| T1105 | Ingress Tool Transfer | C2 | 4 | #0018 #0023 #0026 #0047 | SIM-BROWSER-002 SIM-CDR-002/005 SIM-MP-001 | ANALYTICS BROWSER CDR |
| T1110.001 | Brute Force: Password Guessing | Cred Access | 1 | #0041 | SIM-ITDR-005 | ITDR |
| T1110.003 | Brute Force: Password Spraying | Cred Access | 2 | #0041 #0060 | SIM-ITDR-005 SIM-MP-005 | ANALYTICS ITDR |
| T1110.004 | Brute Force: Credential Stuffing | Cred Access | 1 | #0039 | SIM-ITDR-003 | ITDR |
| T1113 | Screen Capture | Collection | 1 | #0021 | SIM-BROWSER-005 | BROWSER |
| T1114.002 | Email Collection: Remote | Collection | 1 | #0029 | SIM-CLOUD-003 | CLOUD_APP |
| T1136.001 | Create Account: Local | Persistence | 1 | #0034 | SIM-EDR-003 | EDR |
| T1176 | Software Extensions | Persistence | 2 | #0019 #0045 | SIM-BROWSER-003 SIM-KOI-004 | BROWSER KOI |
| T1189 | Drive-by Compromise | Initial Access | 1 | #0018 | SIM-BROWSER-002 | BROWSER |
| T1195 | Supply Chain Compromise | Initial Access | 3 | #0042 #0045 #0046 | SIM-KOI-001/004/005 | KOI |
| T1195.002 | Supply Chain: Compromise SW Deps | Initial Access | 2 | #0044 #0056 | SIM-AISPM-003 SIM-KOI-003 | AI_SPM KOI |
| T1213 | Data from Info Repositories | Collection | 1 | #0058 | SIM-AISPM-005 | AI_SPM |
| T1486 | Data Encrypted for Impact | Impact | 2 | #0006 #0026 | SIM-CDR-005 | CDR |
| T1490 | Inhibit System Recovery | Impact | 1 | #0006 | *(unwired)* | — |
| T1496 | Resource Hijacking | Impact | 2 | #0023 #0052 | SIM-CDR-002 SIM-NDR-003 | CDR NDR |
| T1499 | Endpoint Denial of Service | Impact | 1 | #0016 | SIM-AIRS-005 | AIRS |
| T1499.003 | Application Exhaustion Flood | Impact | 1 | #0016 | SIM-AIRS-005 | AIRS |
| T1526 | Cloud Service Discovery | Discovery | 2 | #0054 #0059 | SIM-AISPM-001 SIM-AISPM-006 | AI_SPM |
| T1528 | Steal Application Access Token | Cred Access | 4 | #0027 #0028 #0029 #0030 | SIM-CLOUD-001/002/003/004 | CLOUD_APP |
| T1530 | Data from Cloud Storage | Collection | 3 | #0003 #0049 #0058 | SIM-AISPM-005 SIM-MP-004 | AI_SPM ANALYTICS |
| T1537 | Transfer Data to Cloud Account | Exfiltration | 1 | #0049 | SIM-MP-004 | ANALYTICS |
| T1539 | Steal Web Session Cookie | Cred Access | 3 | #0019 #0037 #0040 | SIM-BROWSER-003 SIM-ITDR-001/004 | BROWSER ITDR |
| T1543.002 | Create/Modify Process: systemd | Persistence | 1 | #0034 | SIM-EDR-003 | EDR |
| T1546.004 | Event Triggered: Unix Shell Config | Persistence | 1 | #0034 | SIM-EDR-003 | EDR |
| T1550.001 | Alt Auth Material: App Access Token | DE/Initial Access | 3 | #0027 #0029 #0030 | SIM-CLOUD-001/003/004 | CLOUD_APP |
| T1550.002 | Alt Auth Material: Pass the Hash | DE/Lateral Movement | 1 | #0063 | SIM-MP-002 | ANALYTICS |
| T1550.004 | Alt Auth Material: Web Session Cookie | DE/Lateral Movement | 1 | #0040 | SIM-ITDR-004 | ITDR |
| T1552 | Unsecured Credentials | Cred Access | 1 | #0017 | SIM-BROWSER-001 | BROWSER |
| T1552.001 | Unsecured Creds: In Files | Cred Access | **10** | #0008 #0022 #0025 #0032 #0043 #0044 #0045 #0046 #0049 #0057 | SIM-AIACC-002 SIM-AISPM-004 SIM-CDR-001/004 SIM-EDR-001 SIM-KOI-002/003/004/005 SIM-MP-004 | AI_ACCESS AI_SPM ANALYTICS CDR EDR KOI |
| T1556.006 | Modify Auth Process: MFA | Cred Access/DE | 2 | #0001 #0038 | SIM-ITDR-002 | ITDR |
| T1558.003 | Steal/Forge Kerberos: Kerberoasting | Cred Access | 1 | #0063 | SIM-MP-002 | ANALYTICS |
| T1562.001 | Impair Defenses: Disable/Modify Tools | Defense Evasion | 2 | #0023 #0035 | SIM-CDR-002 SIM-EDR-004 | CDR EDR |
| T1565.001 | Data Manipulation: Stored | Impact | 1 | #0057 | SIM-AISPM-004 | AI_SPM |
| T1566.002 | Phishing: Spearphishing Link | Initial Access | 1 | #0018 | SIM-BROWSER-002 | BROWSER |
| T1567 | Exfiltration Over Web Service | Exfiltration | 6 | #0007 #0008 #0009 #0010 #0011 #0020 | SIM-AIACC-001/002/003/004/005 SIM-BROWSER-004 | AI_ACCESS BROWSER |
| T1567.002 | Exfil Over Web: Cloud Storage | Exfiltration | 2 | #0003 #0005 | SIM-NDR-005 | NDR |
| T1568 | Dynamic Resolution | C2 | 1 | #0050 | SIM-NDR-001 | NDR |
| T1572 | Protocol Tunneling | C2 | 5 | #0036 #0047 #0048 #0051 #0062 | SIM-EDR-005 SIM-MP-001/003 SIM-NDR-002/007 | ANALYTICS EDR NDR |
| T1573.002 | Encrypted Channel: Asymmetric | C2 | 1 | #0033 | SIM-EDR-002 | EDR |
| T1574 | Hijack Execution Flow | Persist/PrivEsc | 1 | #0056 | SIM-AISPM-003 | AI_SPM |
| T1580 | Cloud Infrastructure Discovery | Discovery | 3 | #0003 #0049 #0054 | SIM-AISPM-001 SIM-MP-004 | AI_SPM ANALYTICS |
| T1610 | Deploy Container | DE/Execution | 1 | #0024 | SIM-CDR-003 | CDR |
| T1611 | Escape to Host | Privilege Escalation | 1 | #0024 | SIM-CDR-003 | CDR |
| T1613 | Container & Resource Discovery | Discovery | 2 | #0022 #0025 | SIM-CDR-001 SIM-CDR-004 | CDR |
| T1621 | MFA Request Generation | Cred Access | 1 | #0038 | SIM-ITDR-002 | ITDR |
| T1656 | Impersonation | Initial Access | 8 | #0001 #0010 #0012 #0013 #0014 #0015 #0043 #0046 | SIM-AIACC-004 SIM-AIRS-001/002/003/004 SIM-KOI-002/005 | AIRS AI_ACCESS KOI |

**Depth distribution.** Of 82 techniques, **43 (52%)** appear in exactly **one** card. The
"deep" techniques (≥3 cards) are: T1552.001 (10), T1078.004 (8), T1656 (8), T1059.004 (7),
T1567 (6), T1059/T1071.001/T1572 (5 each), T1048.003/T1105/T1528 (4 each), and
T1021.004/T1195/T1530/T1539/T1550.001/T1580 (3 each). Everything else is 1–2 cards.

### 1.3 Standalone (unwired) seed cards

Four cards carry full detection logic but are **not referenced by any scenario's `ttp_ref`** —
they are the original Phase-1 seed/example cards plus the BlackSuit chain:

| Card | Title | Techniques | Why unwired |
|---|---|---|---|
| TTP-2026-0001 | Helpdesk MFA Reset Social Engineering | T1078.004, T1656, T1556.006 | seed example |
| TTP-2026-0002 | LSASS Memory Credential Dump | T1003.001 | seed example (schema reference card) |
| TTP-2026-0003 | AWS IAM Key Abuse → S3 Exfil | T1078.004, T1580, T1530, T1567.002 | seed example |
| TTP-2026-0006 | BlackSuit Blitz — Ansible ESXi Mass Encrypt | T1486, T1490, T1021.004, T1059.004 | chain-only (BlackSuit narrative) |

These are the **only** source of coverage for **T1003.001 (LSASS Memory)** and **T1490
(Inhibit System Recovery)** — meaning those techniques are detectable on paper but cannot be
*exercised* by any runnable scenario. See GAP-3.

---

## 2. Detection-Kind Distribution

### 2.1 Two different "detection_type" vocabularies (a structural seam)

There are **two distinct taxonomies** in the codebase and they do **not** agree:

| Layer | Allowed values | Source |
|---|---|---|
| **Scenario** `expected_detections[].type` and `detection_types` | `BIOC \| Analytics \| IOC` | `scenarios/_schema.yml` lines 41-45, 170 |
| **TTP card** `detections.*` object families | `biocs \| xql_queries \| correlation_rules \| iocs \| analytics_modules` | `detection_scanner/README.md` line 53; `schema/ttp-entry.schema.json` |

**Consequence:** The scenario layer has **no `XQL` and no `Correlation` type**. Scenario authors
label XQL-backed detections as `Analytics` (the `detection_id` slugs literally start with
`xql-...` while `type: Analytics`). Correlation rules are invisible at the scenario layer
entirely. This is the single biggest reporting distortion in the corpus — see GAP-2.

### 2.2 Scenario-layer `expected_detections` kind tally (58 scenarios)

| Kind (scenario `type`) | Count | Share |
|---|---:|---:|
| Analytics | **231** | 67.5% |
| BIOC | **103** | 30.1% |
| IOC | **8** | 2.3% |
| XQL | 0 | 0% (no such type at scenario layer) |
| Correlation | 0 | 0% (no such type at scenario layer) |
| **Total expected_detections** | **342** | |

Distinct `detection_id` slugs authored at the scenario layer: **337**. Of these, only **2
resolve** to a detection object inside the linked TTP card — i.e. the per-detection identifiers
are a *parallel naming scheme* that does not key into card logic (see GAP-4).

### 2.3 Card-layer deployable-logic tally (63 cards — authoritative for logic)

| Kind | Count | Notes |
|---|---:|---|
| XQL queries | **238** | hunting + validation; every card has ≥2 |
| BIOC rules | **144** | behavioral; 21 cards have **0 BIOC** (XQL/analytics-only) |
| Analytics modules | **143** | named Cortex analytics modules (references, not logic) |
| Correlation rules | **72** | every card has ≥1; MP cards have 2-3 |
| IOC | **27** | concentrated in EDR/CDR/KOI/NDR/exfil cards |

**Cards with zero BIOC** (XQL/Analytics-only detection model): all 6 AI_SPM cards
(#0054-#0059), most AI_ACCESS/cloud cards, and the SaaS-token cards (#0030, #0031). This is
intentional for posture/cloud-API planes where there is no endpoint process telemetry, but it
means **BIOC prevention demos do not exist** for those planes.

---

## 3. Per-Plane Detection Maturity

Planes are the runtime grouping (`scenario.plane`). The card-kind columns aggregate the
detection logic of every card reachable from that plane's scenarios via `ttp_ref`. Maturity is
a judgment call combining: # scenarios, # distinct techniques (incl. `additional_techniques`),
diversity of detection kinds, and whether prevention-grade BIOC exists.

| Plane | #Scen | #Cards | #Techniques | cardBIOC | cardXQL | cardCORR | cardIOC | Maturity | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| **EDR** | 5 | 5 | 22 | 23 | 24 | 5 | 3 | **A** | Deepest endpoint coverage; BIOC-rich; full kill-chain breadth |
| **CDR** | 5 | 5 | 15 | 45 | 15 | 5 | 7 | **A** | Most BIOC of any plane (45); container/k8s lifecycle well-covered |
| **ANALYTICS** (multi-plane) | 5 | 6 | 19 | 21 | 30 | 14 | 3 | **A-** | The only plane exercising correlation as the headline (14 CORR rules) |
| **NDR** | 7 | 7 | 9 | 9 | 23 | 7 | 5 | **B+** | Most scenarios; per-protocol breadth; light on BIOC (network ≠ endpoint) |
| **CLOUD_APP** | 5 | 5 | 6 | 4 | 19 | 5 | 0 | **B** | OAuth/token coverage solid but narrow (T1528/T1550 family); zero IOC |
| **ITDR** | 5 | 5 | 6 | 8 | 14 | 5 | 0 | **B** | Identity/IdP coverage is coherent but only 6 techniques; zero IOC |
| **KOI** | 5 | 5 | 5 | 8 | 21 | 5 | 6 | **B-** | Agentic/supply-chain is novel & valuable but only 5 techniques |
| **BROWSER** | 5 | 5 | 7 | 5 | 19 | 5 | 0 | **B-** | Prisma Browser coverage; thin BIOC; zero IOC |
| **AIRS** | 5 | 5 | 5 | 6 | 19 | 5 | 0 | **C+** | OWASP-LLM aligned but ATT&CK mapping is loose (impersonation/exec proxies) |
| **AI_ACCESS** | 5 | 5 | 5 | 4 | 19 | 5 | 1 | **C+** | LLM-egress DLP; mostly Analytics; ATT&CK is forced (T1567 over-used) |
| **AI_SPM** | 6 | 6 | 9 | **0** | 24 | 6 | 0 | **C** | **Undocumented plane** (not in CLAUDE.md); zero BIOC, zero IOC, XQL/analytics only |

Notes:
- "#Techniques" includes the scenario `additional_techniques` field (see GAP-5 — this field is
  **dropped at load time**, so the runtime heatmap shows fewer techniques than this table).
- AI_SPM (6 scenarios, plane `AI_SPM`) is a **first-class plane in the corpus but absent from
  the CLAUDE.md detection-planes table** — it is the 13th plane. The CLAUDE.md table lists
  AI_ACCESS, AIRS, BROWSER, KOI but stops there; AI_SPM ("Cortex AI Security Posture
  Management") is undocumented.

### 3.1 Maturity rubric used

- **A** — ≥5 scenarios, ≥12 techniques, strong BIOC (prevention-demoable) + correlation.
- **B** — coherent coverage of a real Cortex plane, but narrow technique breadth (≤9) or weak BIOC.
- **C** — coverage exists and runs, but ATT&CK mapping is loose and/or only XQL/Analytics
  (no prevention-grade BIOC), limiting POV story to "we can hunt it" not "we block it".

---

## 4. How coverage is computed at runtime (and why it disagrees with this doc)

`GET /api/mitre/coverage` (`core/api/mitre.py`) builds the heatmap from the **`Scenario` ORM
rows**, not from the card corpus. For each active scenario it counts:

1. `scenario.mitre_technique` (one primary technique), and
2. per-step `step.mitre_technique` (when it differs from the primary).

It does **not** read:
- `scenario.additional_techniques` (dropped at load — GAP-5),
- the TTP cards' `mitre_attack.techniques[]` (the multi-technique authoritative list),
- correlation rules or analytics modules of any kind.

So the live heatmap reports roughly the **union of step-level techniques across 58 scenarios**,
which is materially thinner than the **82 techniques** the card corpus actually documents. The
`status` field (`detected` / `run_not_detected` / `not_run` / `no_scenario`) is driven by
`Result.observed`, which is only ever set when a DC manually validates a run via
`PUT /api/results/{id}/validate`. On a fresh DB with no runs, **everything reads `not_run`**.

**Superseded:** `core/engine/uctc_mapper.py` was the old UC/TC chain view, and it inherited the
XQL/Correlation blindness described in GAP-2. It was **deleted** in the 2026-07-31 index
reconciliation. UC/TC alignment now runs through `core/engine/uctc_registry.py` (the v2.2 master
index as frozen dataclasses) with the scenario loader enforcing refs as a foreign key (S-10..S-16),
and it is surfaced by `core/api/uctc.py` — which joins the index to real engine evidence
(`Scenario.tc_refs` → `Run.tc_verdict`) rather than to the scenario detection vocabulary.
See [`../uc_tc_mapping/README.md`](../uc_tc_mapping/README.md).

The richer reverse-cross-ref path exists via `core/engine/ttp_catalog.py` +
`core/api/ttps.py` (`GET /api/ttps/{ttp_id}` returns full card detail + reverse refs), but
**nothing fuses the card corpus into the coverage heatmap.** See GAP-6.

---

## 5. GAPS & WEAKNESSES (opinionated — this is the detection-dev backlog)

Severity is by **POV impact**: would this gap make a DC look bad in front of a customer, or
silently under-sell the product?

### GAP-1 — Zero coverage of Reconnaissance & Resource Development (medium)
Tactics TA0043 and TA0042 have **0 techniques, 0 cards**. Defensible for a post-compromise
detection-QA tool, but it should be a *documented stance*, not a silent hole. At minimum,
Cortex ASM/Xpanse-adjacent recon (active scanning T1595, gather victim info T1592) and acquired
infrastructure (T1583) are demoable and map to PANW exposure-management stories.

### GAP-2 — Scenario detection_type vocabulary omits XQL and Correlation (critical)
`scenarios/_schema.yml` allows only `BIOC | Analytics | IOC`. The corpus has **238 XQL queries
and 72 correlation rules** that have **no representative type** at the scenario layer. Authors
mislabel XQL as `Analytics` (the `detection_id` slugs say `xql-...` while `type: Analytics`),
and correlation rules are entirely invisible in scenario `expected_detections`. Since the POV
report and the UC/TC chain view both render off scenario `detection_types`, **correlation — the
single strongest XSIAM differentiator — is unrepresented in the customer-facing artifact.**
Fix: extend the enum to `BIOC | XQL | Analytics | Correlation | IOC` and re-tag.
Files: `scenarios/_schema.yml`, `core/engine/scenario_loader.py` (StepSchema validation),
all 58 `scenarios/**/*.yml`.

### GAP-3 — Four cards (incl. LSASS, ESXi ransomware) are unrunnable (high)
TTP-2026-0001/0002/0003/0006 are wired to **no scenario**. T1003.001 (LSASS Memory dump — the
single most-requested EDR POV demo) and T1490 (Inhibit System Recovery) are **only** covered by
these unwired cards, so they cannot be *launched*. A DC who clicks "run T1003.001" finds
nothing. Either author scenarios for them or formally mark them archive/reference.
Files: `detection_scanner/ttps/TTP-2026-000{1,2,3,6}-*.json`, new `scenarios/edr/`, `scenarios/multi_plane/`.

### GAP-4 — Scenario `detection_id` slugs don't resolve into card detection objects (high)
337 distinct scenario `detection_id` values; only **2 resolve** to an actual BIOC/XQL/correlation
object in the linked card. The cards identify their detections by `name` only (no `detection_id`
on the 144 BIOC / 238 XQL / 72 CORR objects). The traceability is therefore *card-level*
(`ttp_ref`) but **claims to be detection-level** (the slug `bioc-edr-001-shadow-file-read-...`
implies a specific BIOC that you cannot actually look up). This breaks any "which exact rule
should fire" drill-down and makes the report's per-detection rows unverifiable.
Fix: add `detection_id` to card detection objects and make scenario slugs reference them, OR
generate scenario slugs *from* the card so they round-trip.
Files: `detection_scanner/scripts/validate.py` (add a cross-ref check),
`detection_scanner/ttps/*.json`, `scenarios/**/*.yml`.

### GAP-5 — `additional_techniques` is silently dropped at load (high)
`scenarios/_schema.yml` line 78 defines `additional_techniques`, and scenarios populate it
richly (ANALYTICS plane alone declares 14). But it is **absent from `core/engine/scenario_loader.py`**
(neither `ScenarioSchema` nor `StepSchema`) and **absent from `core/models.py`**. Pydantic
drops it, it never reaches the DB, and `GET /api/mitre/coverage` never sees it. The heatmap
therefore under-reports coverage by dozens of techniques. This is the gap most likely to make
the product look *less* capable than it is.
Files: `core/engine/scenario_loader.py`, `core/models.py`, `core/api/mitre.py`.

### GAP-6 — Coverage heatmap is computed from the thin DB view, not the card corpus (high)
`/api/mitre/coverage` ignores the 82-technique card corpus and the correlation/analytics
content entirely (Section 4). The authoritative ATT&CK breadth lives in
`detection_scanner/ttps/*.json` and is loaded by `ttp_catalog.py`, but never fused into the
heatmap. Result: the customer-facing heatmap is the *least* complete view of coverage that
exists. Fix: drive `/api/mitre/coverage` (or a new endpoint) off the TTP catalog's
`mitre_attack.techniques[]`, joined to scenarios via `ttp_ref`.
Files: `core/api/mitre.py`, `core/engine/ttp_catalog.py`.

### GAP-7 — AI_SPM is an undocumented 13th plane (medium)
6 active scenarios (`scenarios/ai_spm/`) and 6 cards run under `plane: AI_SPM`, but the plane is
**missing from the CLAUDE.md detection-planes table**. New maintainers won't know it exists or
what Cortex engine it maps to ("Cortex AI Security Posture Management"). Document it.
Files: `CLAUDE.md`.

### GAP-8 — 52% of techniques are single-card; key tactics are wide-but-shallow (medium)
43 of 82 techniques have exactly one card. Persistence (11 techniques / 11 cards),
Defense Evasion (14/16), and Impact (6/6) are essentially "one card per technique" — a single
detection variant each. For a *detection-quality* engine the value is in firing the **same
technique multiple ways** to find tuning gaps. Priority depth targets: Impact (ransomware is the
flagship story yet T1486/T1490/T1496 are 1-2 cards), Persistence (systemd/cron/SSH-keys each
single-card), and Privilege Escalation (only 3 techniques total).

### GAP-9 — AIRS / AI_ACCESS ATT&CK mappings are forced (medium)
OWASP-LLM scenarios are mapped onto ATT&CK techniques that don't fit well: prompt injection →
T1656 (Impersonation), tool-call abuse → T1059 (Command & Scripting), token DoS → T1499. T1567
(Exfil Over Web Service) is doing heavy lifting across 6 cards as a catch-all for "LLM egress".
This inflates the appearance of T1567/T1656/T1059 coverage while the *actual* threat
(prompt-injection, data leakage to LLM) has no native ATT&CK home. Consider adopting MITRE
ATLAS IDs alongside ATT&CK for the AI planes, and stop over-loading T1567.
Files: `scenarios/airs/*.yml`, `scenarios/ai_access/*.yml`, corresponding cards.

### GAP-10 — Three planes have zero IOC coverage (low)
CLOUD_APP, ITDR, BROWSER carry **0 IOCs**. IOC is the weakest kind corpus-wide (27 total) and
is the easiest "we match known-bad" demo. SaaS app IDs, known phishing domains, malicious
extension hashes, and known IdP-attack source IPs are all cheap IOC wins for these planes.

### GAP-11 — `analytics_modules` are named references, not testable logic (low)
143 `analytics_modules` entries are free-text module names ("Cortex XDR Credential Theft
Protection Module") with no query/criteria. They count toward "Analytics" coverage in the report
but cannot be *validated* the way a BIOC/XQL can. They are aspirational mappings, not detections.
The report should distinguish "validated detection" from "mapped analytics module".

### GAP-12 — BIOC syntax dialect unverified against current XSIAM (low, but latent)
`detection_scanner/README.md` line 125 self-flags: the corpus assumes XQL-flavored
`preset = xdr_data | ...` and notes "confirm against current XSIAM 2.x BIOC grammar." 144 BIOCs
and 238 XQL queries ride on this assumption. If the grammar drifted, every detection silently
fails to deploy. No automated grammar check exists in `detection_scanner/scripts/validate.py`.

---

## 6. Cross-references to other domains

- **TTP card schema & ingest**: `detection_scanner/README.md`, `detection_scanner/RUNBOOK.md`,
  `detection_scanner/schema/ttp-entry.schema.json`, loader `core/engine/ttp_catalog.py`,
  API `core/api/ttps.py`.
- **Scenario schema**: `scenarios/_schema.yml`; loader `core/engine/scenario_loader.py`;
  ORM `core/models.py` (`Scenario`, `Result`, `Run`).
- **Coverage/heatmap API**: `core/api/mitre.py` (`/api/mitre/coverage`).
- **UC/TC alignment**: index snapshot `docs/uc_tc_mapping/_v2.2-source/`; registry
  `core/engine/uctc_registry.py`; ref enforcement in `core/engine/scenario_loader.py` (S-10..S-16);
  read surface `core/api/uctc.py` + console `#/uctc`; entitlement scoping `core/api/pov.py`.
  (Replaces the deleted `core/engine/uctc_mapper.py`.)
- **Multi-plane correlation scenarios**: `scenarios/multi_plane/mp-00{1..5}-*.yml`
  (plane `ANALYTICS`) — the only place correlation rules are the headline detection.
- **BlackSuit Blitz chain** (Unit 42 flagship): cards #0002 → #0004 → #0005 → #0006 fused by
  correlation `CR-RANSOM-0002` (per `detection_scanner/README.md`); note #0002 and #0006 are
  unwired (GAP-3).
- **Detection logic delivery commit**: `42a49c0` ("real detection logic for all 63 TTP cards +
  full scenario linkage").

---

## 7. Quick-reference counts (for dashboards / report headers)

```
TTP cards (active) .............. 63
Scenarios (active) ............. 58
Planes (runtime) ............... 11 documented + 1 undocumented (AI_SPM) = 12
ATT&CK tactics covered ......... 12 / 14   (missing: Reconnaissance, Resource Development)
ATT&CK techniques (cards) ...... 82
  single-card techniques ....... 43 (52%)
  deepest technique ............ T1552.001 Unsecured Creds in Files (10 cards)
Deployable detection logic:
  XQL queries .................. 238
  BIOC rules ................... 144
  Correlation rules ............ 72
  IOCs ......................... 27
  Analytics modules (named) .... 143
Scenario expected_detections ... 342  (Analytics 231 / BIOC 103 / IOC 8 / XQL 0 / CORR 0)
Unwired cards .................. 4  (#0001 #0002 #0003 #0006)
```

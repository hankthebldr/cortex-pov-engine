# Scenario Catalog — CortexSim Detection Simulation Engine

> A **derived** view of every loadable scenario YAML under `scenarios/`. Source-of-truth
> is the YAML itself; the data-heavy sections (§2, §3, §5) are **regenerated from disk**.
> **Current ground truth (2026-08-02): 169 scenarios · 169 TTP cards across 15 planes**,
> all `status: active`, 0 rejected / 0 dangling refs, 1073/1073 `detection_id` slugs
> resolving. Per-plane on disk: CDR=26 · ANALYTICS=23 · EDR=21 · ITDR=20 · NDR=12 ·
> CLOUD_APP=10 · TIM=9 · KOI=8 · AI_SPM=7 · AI_ACCESS=6 · ASM=6 · BROWSER=6 · AIRS=5 ·
> CSPM=5 · EMAIL=5. The 2026-08-02 library-breadth pass added `SIM-MP-021`,
> `SIM-MP-022`, `SIM-APB-001`, `SIM-CLOUD-010`, `SIM-AIACC-006`, `SIM-TIM-008` and
> `SIM-TIM-009` (`TTP-2026-0169..0175`).
>
> **The §2/§3/§5 per-scenario data tables below are STALE.** They were last regenerated
> 2026-06-15 against a 75-scenario corpus and have not been regenerated through the
> five content passes since. Treat the header counts here, `docs/reference/README.md`
> and `make coverage` as authoritative, and do not quote the tables. Regenerating them
> is a standing task, not a claim this file makes. Stable IDs:
> `SIM-{PLANE}-{NNN}`, `TTP-2026-NNNN`, `TOOL-*`, `UCS-*/TC-*`.

## 0. Scope & method

- **What counts as a "scenario":** a top-level YAML under `scenarios/{plane}/` the loader
  ingests (carries a `scenario_id`, passes `ScenarioSchema` in `core/engine/scenario_loader.py`).
- **What does NOT count:** the loader skips three sub-trees — `scenarios/airs/probes/`,
  `scenarios/browser/campaigns/`, `scenarios/multi_plane/packages/` — these are probe
  packs / campaign declarations / runnable bundles consumed by EAL plugins + tools, **not**
  scenarios. Catalogued in §4.
- **File count reconciliation:** `find scenarios -name '*.yml'` returns **106** files. Of
  those, **75 are loadable scenarios**; the remaining 31 are supporting YAML: `_schema.yml`
  (1) + AIRS probes (19) + browser campaigns (5) + multi-plane packages (6). Do not
  conflate the raw `.yml` file count with the scenario count.

### Totals at a glance

| Metric | Value |
|---|---|
| Loadable scenarios | **75** (0 rejected / 0 dangling `ttp_ref` / 0 dangling `adapter_ref`) |
| Detection planes represented | **14** (EDR, CDR, NDR, ITDR, CLOUD_APP, ANALYTICS, AI_ACCESS, AIRS, AI_SPM, BROWSER, KOI, ASM, CSPM, TIM) |
| Total execution steps | **258** |
| Total `expected_detections` rows | **494** (all resolve to a card detection — GAP-4 closed, 494/494) |
| Detection-type vocabulary | BIOC · XQL · Analytics · Correlation · IOC (full GAP-2 vocab) |
| EAL plugins referenced | **12** |
| Distinct adapters wired | **34** across 35 scenarios (GAP-ADAPT-02: up from 17) |
| IaC modules referenced | **9** |

---

## 1. Schema field reference (what the loader actually enforces)

`scenarios/_schema.yml` is a **documentation artifact**, not the validator. The real
validator is the Pydantic `ScenarioSchema` in `core/engine/scenario_loader.py`. There are
several places where the doc-schema is **stricter than the code** (or vice versa). These
matter because a scenario can satisfy the loader yet violate the documented contract —
silent drift that surfaces in POV reports.

| Field | Required in code? | Doc-schema says | Validation in code | Notes / drift |
|---|---|---|---|---|
| `scenario_id` | yes | required | none (free string) | No format/uniqueness check at load time. |
| `name`,`version`,`status`,`plane` | yes | required | `status` ∈ {active,draft,deprecated}; `plane` ∈ 11-value set | `version` free string. |
| `detection_types` | yes | required, ∈ {BIOC,Analytics,IOC} | **none** | Values NOT validated; can drift from actual step detections — see [GAP S-09]. |
| `uc_ref`,`tc_ref`,`uc_name`,`tc_name` | yes | required | none | Not cross-checked against `docs/uc_tc_mapping/`. |
| `mitre_tactic*`,`mitre_technique*` | yes | required | none | Technique IDs not validated against ATT&CK. |
| `additional_techniques` | optional | list of {technique,name} | accepted as raw list | Some files use bare strings — defensive parse needed. |
| `threat_report` | **optional in code** | **required in doc** | none | Code makes it `Optional[str]=None`; doc says required. All 58 set it anyway. [GAP S-08] |
| `threat_report_url` | optional | optional | none | All 58 set it. |
| `execution_identity.{default,options}` | yes | required | structural only | Identity values not validated against harness modes. |
| `push_supported`,`pull_supported` | yes | required | bool | |
| `external_tools[]` | optional | optional | `.adapter_ref` optional | `adapter_ref` resolved against adapter catalog at boot (warn-only if dangling). |
| `steps[]` | yes (non-empty list) | **"1–10 entries… at least 3 required"** (self-contradictory) | **no min/max enforced** | 5 NDR scenarios have only 2 steps — load fine, violate doc. [GAP S-01] |
| `steps[].mitre_technique` | **yes** | required | required string | Every step must carry a technique. |
| `steps[].expected_detections` | optional (defaults `[]`) | required (≥1) per doc example | none | A step with zero detections loads silently. None observed empty today, but unguarded. [GAP S-02] |
| `expected_detections[].{plane,type,description}` | yes | required | structural | `type`/`plane` NOT enum-validated. |
| `expected_detections[].ttp_ref` + `detection_id` | optional | optional bridge | resolved by `ttp_catalog.find()` (warn-only) | Dangling pair = card silently absent from report (corpus currently 494/494 resolved). |
| `expected_detections[].verification_xql`,`kpi_contribution` | optional | optional (v2.0) | structural | Validation-harness fodder; not yet executed. |
| `cleanup` | optional | required | optional (`CleanupSchema`) | All 58 have ≥1 cleanup command. |
| `cleanup.k8s_teardown` | optional | optional | string | Only CDR scenarios set it (5/5). |
| `author`,`created`,`last_updated`,`tags` | author optional; created/last_updated NOT in code model | required (created/last_updated) | none | `created`/`last_updated` are **not even fields on `ScenarioSchema`** — silently dropped. [GAP S-10] |
| `required_content` | optional | optional | list[dict] | IaC hint. |
| `infra_modules_needed` | optional | optional | list[str] | IaC hint; values NOT validated against existing modules — `airs` is referenced but absent. [GAP S-06] |
| v2.0: `validation_methodology`,`methodology_family`,`primary_kpi`,`threshold`,`success_criteria`,`moat_tier` | optional | optional | `methodology_family`∈F1..F10; `moat_tier`∈{MOAT,LEAD,PARITY} | Only 8 scenarios populate any of these. [GAP S-07] |
| F2: `correlation_window_seconds`,`required_planes_in_incident`,`stitching_key` | optional | "required-by-convention for ANALYTICS" | none | Only 2 of 5 ANALYTICS scenarios set them. [GAP S-04] |

**Identity → harness mode mapping (from schema doc):** `container-runtime` → direct;
`root` → direct; `www-data`/`svc-backup` → `runuser -l`; `nobody` → `su -s /bin/bash`;
`node`/`postgres` → `sudo -u`. Enforced at execution time by the Go agent + push generator,
not at load.

---

## 2. Per-plane summary

| Plane | Cortex engine | Scenarios | Unique techniques | Steps | Detections | EAL plugin(s) | Adapters wired | IaC module(s) |
|---|---|---|---|---|---|---|---|---|
| **EDR** | Cortex XDR Agent | 9 | 26 | 38 | 83 | — | 2 (ATOMIC-RED-TEAM, NMAP) | — |
| **CDR** | Cortex Cloud / Prisma Cloud Compute | 8 | 19 | 36 | 68 | — | 6 (CLOUDSPLAINING, DEEPCE, GITLEAKS, KUBE-BENCH, KUBESCAPE, TRIVY) | base, cdr, cspm |
| **NDR** | NGFW / Network Security Analytics | 7 | 12 | 16 | 31 | `bulk_https_exfil`, `c2_http_beacon`, `dns_tunnel_exfil`, `ftp_egress`, `smb_rpc_sweep`, `ssh_egress`, `stratum_tcp_connect` | 2 (MASSCAN, NMAP) | base, itdr, ndr, tim |
| **ITDR** | Cortex ITDR | 8 | 18 | 27 | 44 | `idp_signin_emulator` | 7 (BLOODYAD, IMPACKET, KRBRELAYUP, PRINTSPOOFER, PYPYKATZ, RUBEUS, TOKENVATOR) | base, itdr |
| **CLOUD_APP** | Cortex Cloud App Security | 5 | 6 | 15 | 23 | `oauth_grant_emulator` | — | base |
| **ANALYTICS** | XSIAM Correlation Engine (multi-plane) | 5 | 18 | 21 | 48 | — | 9 (ATOMIC-RED-TEAM, BLOODHOUND, IMPACKET, MIMIKATZ, NMAP, PACU, RUBEUS, SCAPY, SLIVER) | base, cdr, edr, itdr, ndr, tim |
| **AI_ACCESS** | Cortex AI Access Security | 5 | 5 | 15 | 24 | `llm_provider_egress` | — | base |
| **AIRS** | Cortex AI Runtime Security | 5 | 4 | 15 | 25 | — | 1 (CORTEX-PROMPT-ATTACKER) | base |
| **AI_SPM** | Cortex AI Security Posture Mgmt | 6 | 11 | 19 | 24 | — | — | ai-spm, base |
| **BROWSER** | Prisma Browser | 5 | 6 | 15 | 25 | `browser_attack_runner` | 1 (CORTEX-BROWSER-ATTACKER) | base |
| **KOI** | Agentic endpoint / supply-chain | 5 | 6 | 15 | 29 | `agentic_egress` | 1 (CORTEX-AGENTIC-PACK) | base |
| **ASM** | Cortex Attack Surface Management | 4 | 10 | 16 | 43 | — | 9 (CMSEEK, COMMIX, FEROXBUSTER, GOBUSTER, NIKTO, NMAP, NUCLEI, SQLMAP, WHATWEB) | asm, base |
| **CSPM** | Cortex Cloud Posture Mgmt | 1 | 4 | 4 | 9 | — | 2 (PROWLER, SCOUTSUITE) | base, cspm |
| **TIM** | Cortex Threat Intel Management | 2 | 5 | 6 | 18 | `c2_http_beacon` | — | base, ndr, tim |
| **TOTAL** | — | **75** | — | **258** | **494** | 12 plugins | 34 distinct | 9 modules |

Detection-type legend: **B**=BIOC · **X**=XQL · **A**=Analytics · **C**=Correlation · **I**=IOC. `det` column in per-plane tables is `B/X/A/C/I (total)`.


## 3. Per-plane detail

### Plane: EDR (Cortex XDR Agent) — 9 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-EDR-001 | Credential Dumping — /etc/shadow and Mimipenguin | Credential Access (TA0006) | T1003.008 OS Credential Dumping: /etc/passwd and /etc/shadow | T1552.001, T1003 | ATOMIC-RED-TEAM | 5 | 4/5/0/0/1 (10) | — | UCS-EDR-01 / TC-EDR-01 |
| SIM-EDR-002 | Reverse Shell — Multi-Method Callback | Command and Control (TA0011) | T1059.004 Command and Scripting Interpreter: Unix Shell | T1071.001, T1059.006 | ATOMIC-RED-TEAM | 5 | 5/4/0/0/0 (9) | — | UCS-EDR-02 / TC-EDR-02 |
| SIM-EDR-003 | Linux Persistence — Cron, Systemd, and Backdoor Users | Persistence (TA0003) | T1053.003 Scheduled Task/Job: Cron | T1136.001, T1543.002, T1098.004 | ATOMIC-RED-TEAM | 5 | 5/5/0/0/0 (10) | — | UCS-EDR-03 / TC-EDR-03 |
| SIM-EDR-004 | Defense Evasion — Log Tampering, Timestomping, and Process Hiding | Defense Evasion (TA0005) | T1070.002 Indicator Removal: Clear Linux or Mac System Logs | T1070.006, T1562.001, T1036.005 | ATOMIC-RED-TEAM | 5 | 5/5/0/0/0 (10) | — | UCS-EDR-04 / TC-EDR-04 |
| SIM-EDR-005 | Lateral Movement — SSH Abuse, Tunneling, and Internal Recon | Lateral Movement (TA0008) | T1021.004 Remote Services: SSH | T1046, T1572, T1018 | ATOMIC-RED-TEAM, NMAP | 5 | 3/5/0/0/0 (8) | — | UCS-EDR-05 / TC-EDR-05 |
| SIM-EDR-006 | LSASS Memory Credential Dump — comsvcs.dll LOLBin (Windows) | Credential Access (TA0006) | T1003.001 OS Credential Dumping: LSASS Memory | T1003, T1218.011 | ATOMIC-RED-TEAM | 3 | 2/2/0/1/1 (6) | — | UCS-EDR-06 / TC-EDR-06 |
| SIM-EDR-007 | ESXi Mass-Encryption / Inhibit Recovery — SAFE-MODE (Linux → ESXi) | Impact (TA0040) | T1490 Inhibit System Recovery | T1486, T1021.004, T1059.004 | — | 3 | 4/4/0/2/1 (11) | — | UCS-EDR-07 / TC-EDR-07 |
| SIM-EDR-008 | Linux Host File Mass-Encryption for Impact — SAFE-MODE | Impact (TA0040) | T1486 Data Encrypted for Impact | T1490, T1083 | — | 3 | 3/4/0/1/2 (10) | — | UCS-EDR-08 / TC-EDR-08 |
| SIM-EDR-009 | Bulk Data Exfiltration via Rclone to Cloud Storage — SAFE-MODE | Exfiltration (TA0010) | T1567.002 Exfiltration Over Web Service: Exfiltration to Cloud Storage | T1048.003, T1074.001 | — | 4 | 3/3/0/1/2 (9) | — | UCS-EDR-09 / TC-EDR-09 |

### Plane: CDR (Cortex Cloud / Prisma Cloud Compute) — 8 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-CDR-001 | Container Enumeration via DEEPCE | Discovery (TA0007) | T1613 Container and Resource Discovery | T1082 | DEEPCE | 5 | 9/0/0/0/0 (9) | — | UCS-CDR-01 / TC-CDR-01 |
| SIM-CDR-002 | Cryptominer Deployment — Unit 42 XMRig Variant | Impact (TA0040) | T1496 Resource Hijacking | T1105 | — | 5 | 8/0/0/0/0 (8) | — | UCS-CDR-02 / TC-CDR-02 |
| SIM-CDR-003 | Container Escape via Privileged Mode | Privilege Escalation (TA0004) | T1611 Escape to Host | T1610 | — | 5 | 10/0/0/0/0 (10) | — | UCS-CDR-03 / TC-CDR-03 |
| SIM-CDR-004 | Kubernetes Lateral Movement and Persistence | Lateral Movement (TA0008) | T1021.001 Remote Services: SSH | T1053.005, T1552.001 | — | 5 | 10/0/0/0/0 (10) | — | UCS-CDR-04 / TC-CDR-04 |
| SIM-CDR-005 | WildFire Malware Trigger — Simulated Backdoor | Execution (TA0002) | T1105 Ingress Tool Transfer | T1486, T1071.001 | — | 4 | 8/0/0/0/0 (8) | — | UCS-CDR-05 / TC-CDR-05 |
| SIM-CDR-006 | Node systemd Service + Cron Reload Persistence — SAFE-MODE | Persistence (TA0003) | T1543.002 Create or Modify System Process: Systemd Service | T1053.003 | — | 3 | 3/3/0/1/2 (9) | — | UCS-CDR-06 / TC-CDR-06 |
| SIM-CDR-007 | Container & Kubernetes Supply-Chain Posture Sweep | Discovery (TA0007) | T1613 Container and Resource Discovery | T1526, T1552.001, T1046 | TRIVY, KUBE-BENCH, KUBESCAPE, GITLEAKS, CLOUDSPLAINING | 6 | 2/5/0/0/0 (7) | cdr, base | UCS-CDR-07 / TC-CDR-07 |
| SIM-CDR-008 | AWS IAM Access Key Abuse — Discovery Burst + S3 Object Exfiltration | Discovery (TA0007) | T1580 Cloud Infrastructure Discovery | T1078.004, T1530, T1567.002 | — | 3 | 3/3/0/1/0 (7) | base, cdr, cspm | UCS-CDR-08 / TC-CDR-08 |

### Plane: NDR (NGFW / Network Security Analytics) — 7 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-NDR-001 | NDR — C2 Beaconing EAL Validation | Command and Control (TA0011) | T1071.001 Application Layer Protocol: Web Protocols | T1568 | `c2_http_beacon` | 3 | 1/4/0/0/0 (5) | base, ndr | UCS-NDR-01 / TC-NDR-01 |
| SIM-NDR-002 | NDR — DNS Tunneling EAL Validation | Exfiltration (TA0010) | T1048.003 Exfiltration Over Unencrypted Non-C2 Protocol | T1572 | `dns_tunnel_exfil` | 3 | 1/5/0/0/0 (6) | base, ndr, tim | UCS-NDR-02 / TC-NDR-02 |
| SIM-NDR-003 | NDR — Cryptojacking Stratum App-ID Validation | Impact (TA0040) | T1496 Resource Hijacking | — | `stratum_tcp_connect` | 2 | 1/2/0/0/1 (4) | base, ndr | UCS-NDR-03 / TC-NDR-03 |
| SIM-NDR-004 | NDR — SMB/RPC Lateral Sweep EAL Validation | Discovery (TA0007) | T1046 Network Service Discovery | T1018, T1021.002 | `smb_rpc_sweep`, NMAP, MASSCAN | 2 | 1/3/0/0/0 (4) | base, ndr, itdr | UCS-NDR-04 / TC-NDR-04 |
| SIM-NDR-005 | NDR — Bulk HTTPS Exfiltration EAL Validation | Exfiltration (TA0010) | T1041 Exfiltration Over C2 Channel | T1567 | `bulk_https_exfil` | 2 | 2/1/0/1/0 (4) | base, ndr | UCS-NDR-05 / TC-NDR-05 |
| SIM-NDR-006 | NDR — FTP Cleartext Egress + Credentials EAL Validation | Command and Control (TA0011) | T1071 Application Layer Protocol | T1048.003 | `ftp_egress` | 2 | 1/2/0/0/1 (4) | base, ndr | UCS-NDR-06 / TC-NDR-06 |
| SIM-NDR-007 | NDR — SSH Outbound App-ID + Atypical Client-Banner EAL Validation | Command and Control (TA0011) | T1572 Protocol Tunneling | T1021.004 | `ssh_egress` | 2 | 1/2/0/0/1 (4) | base, ndr | UCS-NDR-07 / TC-NDR-07 |

### Plane: ITDR (Cortex ITDR) — 8 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-ITDR-001 | ITDR — Impossible Travel (Okta sign-ins from US-West and APAC-East) | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud Accounts | T1539 | `idp_signin_emulator` | 3 | 1/3/0/0/0 (4) | base, itdr | UCS-ITDR-01 / TC-ITDR-01 |
| SIM-ITDR-002 | ITDR — MFA Fatigue / Push-Bombing (Okta) | Credential Access (TA0006) | T1621 Multi-Factor Authentication Request Generation | T1556.006 | `idp_signin_emulator` | 3 | 2/2/0/0/0 (4) | base, itdr | UCS-ITDR-02 / TC-ITDR-02 |
| SIM-ITDR-003 | ITDR — Credential Stuffing (failed-login burst across many users) | Credential Access (TA0006) | T1110.004 Brute Force: Credential Stuffing | — | `idp_signin_emulator` | 3 | 1/3/0/0/0 (4) | base, itdr | UCS-ITDR-03 / TC-ITDR-03 |
| SIM-ITDR-004 | ITDR — Session Token Replay Across Geo / User-Agent | Credential Access (TA0006) | T1539 Steal Web Session Cookie | T1550.004, T1078.004 | `idp_signin_emulator` | 3 | 2/3/0/0/0 (5) | base, itdr | UCS-ITDR-04 / TC-ITDR-04 |
| SIM-ITDR-005 | ITDR — Brute-Force Causing Account Lockout (Microsoft) | Credential Access (TA0006) | T1110.003 Brute Force: Password Spraying | T1110.001 | `idp_signin_emulator` | 3 | 2/3/0/0/0 (5) | base, itdr | UCS-ITDR-05 / TC-ITDR-05 |
| SIM-ITDR-006 | ITDR — AD Offline Roasting Credential Harvest (AS-REP Roast + Kerberoast) | Credential Access (TA0006) | T1558.004 Steal or Forge Kerberos Tickets: AS-REP Roasting | T1558.003, T1087.002 | IMPACKET, RUBEUS | 3 | 3/3/0/1/0 (7) | base, itdr | UCS-ITDR-06 / TC-ITDR-06 |
| SIM-ITDR-007 | ITDR — AD Privilege Escalation & Token Manipulation Chain (LSASS → DACL → Kerberos Relay → SeImpersonate → Token Theft) | Privilege Escalation (TA0004) | T1068 Exploitation for Privilege Escalation | T1134, T1003.001, T1098, T1550, T1078 | PYPYKATZ, BLOODYAD, KRBRELAYUP, PRINTSPOOFER, TOKENVATOR | 6 | 5/4/0/1/0 (10) | base, itdr | UCS-ITDR-07 / TC-ITDR-07 |
| SIM-ITDR-008 | ITDR — Help-Desk MFA Reset via Voice Impersonation (Muddled Libra account takeover) | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud Accounts | T1556.006, T1656 | `idp_signin_emulator` | 3 | 2/2/0/1/0 (5) | base, itdr | UCS-ITDR-08 / TC-ITDR-08 |

### Plane: CLOUD_APP (Cortex Cloud App Security) — 5 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-CLOUD-001 | Cloud App — Okta Risky OAuth Drive-Scope Grant | Initial Access (TA0001) | T1550.001 Use Alternate Authentication Material: Application Access Token | T1528 | `oauth_grant_emulator` | 3 | 1/3/0/0/0 (4) | base | UCS-CAPP-01 / TC-CAPP-01 |
| SIM-CLOUD-002 | Cloud App — Microsoft Admin-Consent-Required Scope Request | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud Accounts | T1098, T1528 | `oauth_grant_emulator` | 3 | 2/3/0/0/0 (5) | base | UCS-CAPP-02 / TC-CAPP-02 |
| SIM-CLOUD-003 | Cloud App — Google Full-Mailbox + Offline Token Replay Risk | Collection (TA0009) | T1114.002 Email Collection: Remote Email Collection | T1528, T1550.001 | `oauth_grant_emulator` | 3 | 1/4/0/0/0 (5) | base | UCS-CAPP-03 / TC-CAPP-03 |
| SIM-CLOUD-004 | Cloud App — Cross-Provider OAuth Grant Rotation (Okta → MS → Google) | Defense Evasion (TA0005) | T1090 Proxy | T1550.001, T1528 | `oauth_grant_emulator` | 3 | 0/5/0/0/0 (5) | base | UCS-CAPP-04 / TC-CAPP-04 |
| SIM-CLOUD-005 | Cloud App — Benign OAuth Baseline (control / FP-suppression validation) | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud Accounts | — | `oauth_grant_emulator` | 3 | 0/4/0/0/0 (4) | base | UCS-CAPP-05 / TC-CAPP-05 |

### Plane: ANALYTICS (XSIAM Correlation Engine (multi-plane)) — 5 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-MP-001 | C2 Beacon Callback — NGFW + XDR Stitch Validation | Command and Control (TA0011) | T1071.001 Application Layer Protocol: Web Protocols | T1059.004, T1105 | SLIVER | 4 | 3/4/0/2/1 (10) | base, edr, ndr | UCS-MP-01 / TC-MP-01 |
| SIM-MP-002 | Kerberoast + Pass-the-Hash Lateral Movement — ITDR + EDR + NDR Stitch | Credential Access (TA0006) | T1558.003 Steal or Forge Kerberos Tickets: Kerberoasting | T1550.002, T1021.002, T1003.006 | IMPACKET, RUBEUS, MIMIKATZ, BLOODHOUND | 4 | 5/6/0/1/0 (12) | base, itdr, edr, ndr | UCS-MP-02 / TC-MP-02 |
| SIM-MP-003 | Staged Data Exfiltration via DNS Tunneling — XDR + NGFW Stitch | Exfiltration (TA0010) | T1048.003 Exfiltration Over Alternative Protocol: Exfiltration Over Unencrypted Non-C2 Protocol | T1074.001, T1572, T1005 | SCAPY | 4 | 4/4/0/1/1 (10) | base, edr, ndr | UCS-MP-03 / TC-MP-03 |
| SIM-MP-004 | APT29 Cloud Credential Theft → Lateral → Exfil | Credential Access (TA0006) | T1552.001 Unsecured Credentials: Credentials In Files | T1059.004, T1078.004, T1580, T1530, T1537 | PACU, MIMIKATZ, BLOODHOUND | 5 | 5/6/0/0/1 (12) | base, edr, cdr, tim | UCS-MP-04 / TC-MP-04 |
| SIM-MP-005 | Cross-Plane Correlation MOAT — EDR + NDR + ITDR Stitch (TC-IR-05) | Lateral Movement (TA0008) | T1078 Valid Accounts | T1071.001, T1059.004, T1110.003 | ATOMIC-RED-TEAM, NMAP | 4 | 1/2/0/1/0 (4) | base, edr, ndr, itdr | UCS-MP-05 / TC-MP-05 |

### Plane: AI_ACCESS (Cortex AI Access Security) — 5 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-AIACC-001 | AI Access — Source Code Paste to Public ChatGPT | Exfiltration (TA0010) | T1567 Exfiltration Over Web Service | T1041 | `llm_provider_egress` | 3 | 1/4/0/0/0 (5) | base | UCS-AIACC-01 / TC-AIACC-01 |
| SIM-AIACC-002 | AI Access — AWS Access Key Leaked to Anthropic API | Credential Access (TA0006) | T1552 Unsecured Credentials | T1567 | `llm_provider_egress` | 3 | 1/3/0/0/1 (5) | base | UCS-AIACC-02 / TC-AIACC-02 |
| SIM-AIACC-003 | AI Access — High-Volume Gemini Prompt Burst from Single User | Exfiltration (TA0010) | T1567 Exfiltration Over Web Service | — | `llm_provider_egress` | 3 | 0/5/0/0/0 (5) | base | UCS-AIACC-03 / TC-AIACC-03 |
| SIM-AIACC-004 | AI Access — Jailbreak Prompt Fingerprint Detection | Initial Access (TA0001) | T1656 Impersonation | T1567 | `llm_provider_egress` | 3 | 2/2/0/0/0 (4) | base | UCS-AIACC-04 / TC-AIACC-04 |
| SIM-AIACC-005 | AI Access — Cross-Provider Rotation (OpenAI → Gemini → Anthropic) | Defense Evasion (TA0005) | T1090 Proxy | T1567 | `llm_provider_egress` | 3 | 0/5/0/0/0 (5) | base | UCS-AIACC-05 / TC-AIACC-05 |

### Plane: AIRS (Cortex AI Runtime Security) — 5 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-AIRS-001 | AIRS — Direct Prompt Injection (LLM01) | Initial Access (TA0001) | T1656 Impersonation | — | CORTEX-PROMPT-ATTACKER | 3 | 1/4/0/0/0 (5) | base | UCS-AIRS-01 / TC-AIRS-01 |
| SIM-AIRS-002 | AIRS — Indirect Prompt Injection via RAG Document (LLM01 + LLM08) | Initial Access (TA0001) | T1059 Command and Scripting Interpreter | T1656 | CORTEX-PROMPT-ATTACKER | 3 | 1/4/0/0/0 (5) | base | UCS-AIRS-02 / TC-AIRS-02 |
| SIM-AIRS-003 | AIRS — System Prompt Leakage (LLM07) | Discovery (TA0007) | T1082 System Information Discovery | — | CORTEX-PROMPT-ATTACKER | 3 | 2/3/0/0/0 (5) | base | UCS-AIRS-03 / TC-AIRS-03 |
| SIM-AIRS-004 | AIRS — Excessive Agency / Tool-Call Abuse (LLM06) | Execution (TA0002) | T1059 Command and Scripting Interpreter | T1656 | CORTEX-PROMPT-ATTACKER | 3 | 2/3/0/0/0 (5) | base | UCS-AIRS-04 / TC-AIRS-04 |
| SIM-AIRS-005 | AIRS — Unbounded Consumption / Token-Exhaustion DoS (LLM10) | Impact (TA0040) | T1499 Endpoint Denial of Service | — | CORTEX-PROMPT-ATTACKER | 3 | 0/5/0/0/0 (5) | base | UCS-AIRS-05 / TC-AIRS-05 |

### Plane: AI_SPM (Cortex AI Security Posture Mgmt) — 6 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-AISPM-001 | AI Asset Discovery & Inventory — Shadow AI Detection | Discovery (TA0007) | T1526 Cloud Service Discovery | T1580 | — | 4 | 0/4/0/0/0 (4) | base, ai-spm | UCS-AISPM-01 / TC-AISPM-01 |
| SIM-AISPM-002 | AI Model Security Assessment — Overprivileged Role + Misconfig Findings | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud Accounts | T1098 | — | 3 | 0/3/0/0/0 (3) | base, ai-spm | UCS-AISPM-01 / TC-AISPM-02 |
| SIM-AISPM-003 | AI Ecosystem & Supply Chain Risk — Vulnerable ML Dependencies + EU AI Act | Initial Access (TA0001) | T1195.002 Supply Chain Compromise: Compromise Software Supply Chain | T1574 | — | 3 | 0/4/0/0/0 (4) | base, ai-spm | UCS-AISPM-01 / TC-AISPM-03 |
| SIM-AISPM-004 | AI Static Risk Analysis — Hardcoded Credentials + Insecure Pickle + Unvalidated Inputs | Credential Access (TA0006) | T1552.001 Unsecured Credentials: Credentials In Files | T1027, T1565.001 | — | 3 | 0/5/0/0/0 (5) | base, ai-spm | UCS-AISPM-01 / TC-AISPM-04 |
| SIM-AISPM-005 | AI Sensitive Data Classification — PII/PHI/PCI in Training Sets | Collection (TA0009) | T1530 Data from Cloud Storage Object | T1213 | — | 3 | 0/5/0/0/0 (5) | base, ai-spm | UCS-AISPM-01 / TC-AISPM-05 |
| SIM-AISPM-006 | AI Security Dashboard & Posture — Aggregate View Across All Discovered AI Assets | Discovery (TA0007) | T1526 Cloud Service Discovery | — | — | 3 | 0/3/0/0/0 (3) | base, ai-spm | UCS-AISPM-01 / TC-AISPM-06 |

### Plane: BROWSER (Prisma Browser) — 5 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-BROWSER-001 | Browser — Credential Paste into Untrusted Origin | Credential Access (TA0006) | T1552 Unsecured Credentials | — | `browser_attack_runner`, CORTEX-BROWSER-ATTACKER | 3 | 1/0/3/0/0 (4) | base | UCS-BROWSER-01 / TC-BROWSER-01 |
| SIM-BROWSER-002 | Browser — Drive-by Download from Phishing Page | Initial Access (TA0001) | T1189 Drive-by Compromise | T1566 | `browser_attack_runner`, CORTEX-BROWSER-ATTACKER | 3 | 1/0/4/0/0 (5) | base | UCS-BROWSER-02 / TC-BROWSER-02 |
| SIM-BROWSER-003 | Browser — Sideloaded Risky Chrome Extension | Persistence (TA0003) | T1176 Browser Extensions | — | `browser_attack_runner`, CORTEX-BROWSER-ATTACKER | 3 | 1/0/4/0/1 (6) | base | UCS-BROWSER-03 / TC-BROWSER-03 |
| SIM-BROWSER-004 | Browser — Cross-Origin SaaS Copy-Paste DLP | Exfiltration (TA0010) | T1567 Exfiltration Over Web Service | — | `browser_attack_runner`, CORTEX-BROWSER-ATTACKER | 3 | 1/0/4/0/0 (5) | base | UCS-BROWSER-04 / TC-BROWSER-04 |
| SIM-BROWSER-005 | Browser — Screen Capture of Sensitive SaaS Page | Collection (TA0009) | T1113 Screen Capture | — | `browser_attack_runner`, CORTEX-BROWSER-ATTACKER | 3 | 1/0/4/0/0 (5) | base | UCS-BROWSER-05 / TC-BROWSER-05 |

### Plane: KOI (Agentic endpoint / supply-chain) — 5 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-KOI-001 | KOI — Claude Desktop Installs Typosquat MCP Server | Initial Access (TA0001) | T1195 Supply Chain Compromise | T1059 | `agentic_egress`, CORTEX-AGENTIC-PACK | 3 | 2/4/0/0/0 (6) | base | UCS-KOI-01 / TC-KOI-01 |
| SIM-KOI-002 | KOI — Hidden Prompt Injection in MCP Tool Response | Initial Access (TA0001) | T1059 Command and Scripting Interpreter | T1656 | `agentic_egress`, CORTEX-AGENTIC-PACK | 3 | 1/4/0/0/0 (5) | base | UCS-KOI-02 / TC-KOI-02 |
| SIM-KOI-003 | KOI — Backdoored PyPI Package Consumed by Agent | Initial Access (TA0001) | T1195.002 Compromise Software Supply Chain | T1059.006 | `agentic_egress`, CORTEX-AGENTIC-PACK | 3 | 2/4/0/0/0 (6) | base | UCS-KOI-03 / TC-KOI-03 |
| SIM-KOI-004 | KOI — Malicious VS Code Extension Permission Escalation | Initial Access (TA0001) | T1195 Supply Chain Compromise | T1059, T1552.001 | `agentic_egress`, CORTEX-AGENTIC-PACK | 3 | 2/4/0/0/0 (6) | base | UCS-KOI-04 / TC-KOI-04 |
| SIM-KOI-005 | KOI — Malicious Claude Skill With Hidden Instructions | Initial Access (TA0001) | T1656 Impersonation | T1195 | `agentic_egress`, CORTEX-AGENTIC-PACK | 3 | 1/5/0/0/0 (6) | base | UCS-KOI-05 / TC-KOI-05 |

### Plane: ASM (Cortex Attack Surface Management) — 4 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-ASM-001 | ASM — Internet-Exposed Attack-Surface Discovery & Enumeration | Reconnaissance (TA0043) | T1595 Active Scanning | T1046, T1190 | NMAP, NUCLEI | 3 | 1/4/0/1/3 (9) | base, asm | UCS-ASM-01 / TC-ASM-01 |
| SIM-ASM-002 | ASM — Inbound Vulnerability-Scanning Reconnaissance | Reconnaissance (TA0043) | T1595.002 Active Scanning: Vulnerability Scanning | T1595.001, T1190 | NUCLEI | 3 | 2/3/0/1/3 (9) | base, asm | UCS-ASM-02 / TC-ASM-02 |
| SIM-ASM-003 | ASM — Passive OSINT Victim-Info Gathering Reconnaissance | Reconnaissance (TA0043) | T1592 Gather Victim Host Information | T1589, T1590.002, T1596.003 | — | 3 | 2/4/0/1/2 (9) | base, asm | UCS-ASM-03 / TC-ASM-03 |
| SIM-ASM-004 | ASM — Web-Application Attack-Surface Enumeration & Exploitation | Reconnaissance (TA0043) | T1595.002 Active Scanning: Vulnerability Scanning | T1595.003, T1190, T1592 | WHATWEB, CMSEEK, GOBUSTER, FEROXBUSTER, NIKTO, SQLMAP, COMMIX | 7 | 4/8/0/1/3 (16) | base, asm | UCS-ASM-04 / TC-ASM-04 |

### Plane: CSPM (Cortex Cloud Posture Mgmt) — 1 scenario

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-CSPM-001 | Cloud Posture Misconfiguration Sweep — Cortex Cloud CSPM | Collection (TA0009) | T1530 Data from Cloud Storage | T1078.004, T1098, T1580 | PROWLER, SCOUTSUITE | 4 | 2/6/0/1/0 (9) | base, cspm | UCS-CSPM-01 / TC-CSPM-01 |

### Plane: TIM (Cortex Threat Intel Management) — 2 scenarios

| ID | Name | Tactic | Primary technique | Additional | EAL / Adapter | Steps | Det (B/X/A/C/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-TIM-001 | TIM — TAXII IOC Feed Match + Outbound NDR Session Stitch | Command and Control (TA0011) | T1071.001 Application Layer Protocol: Web Protocols | T1568 | `c2_http_beacon` | 3 | 1/5/0/1/1 (8) | base, tim, ndr | UCS-TIM-01 / TC-TIM-01 |
| SIM-TIM-002 | TIM — Adversary Resource-Development Infrastructure Staging Detection | Resource Development (TA0042) | T1583.001 Acquire Infrastructure: Domains | T1608.001, T1585 | `c2_http_beacon` | 3 | 1/5/0/1/3 (10) | base, tim, ndr | UCS-TIM-02 / TC-TIM-02 |

---

## 4. Supporting (non-scenario) YAML — loader-skipped

31 files under skip-listed sub-trees are **not** loaded as scenarios — they are consumed
by EAL plugins / tools / packaging. Catalogued for completeness.

### 4a. AIRS probe packs — `scenarios/airs/probes/` (19 files)

`promptmap`-compatible probes (`prompt`, `mutators`, `scorer`, `fail_conditions`) consumed
by `cortex-prompt-attacker`, organized by OWASP LLM id. **Coverage is complete for OWASP
LLM01–LLM10** — the earlier LLM03/04/05/09 gap (old S-17) is **closed**.

| OWASP | Probe files |
|---|---|
| LLM01 | delimiter_smuggle · ignore_previous_basic · role_play_dan |
| LLM02 | customer_record_extract |
| LLM03 | typosquat_plugin_manifest · unsigned_plugin_autoload |
| LLM04 | feedback_poison_inject · poisoned_topic_recall |
| LLM05 | img_onerror_injection · xss_script_passthrough |
| LLM06 | tool_abuse_exec_shell · tool_abuse_send_email |
| LLM07 | initial_instruction · repeat_words_above |
| LLM08 | rag_trigger |
| LLM09 | fabricated_cve_claim · fabricated_executive_quote · fabricated_research_citation |
| LLM10 | dos_unbounded_tokens |

### 4b. Browser campaigns — `scenarios/browser/campaigns/` (5 files)

`cortex-browser-attacker` campaign declarations (`campaign_id: BC-BROWSER-NNN`,
`target_allowlist`, `browser_channel: prisma`, `actions[]`), one per SIM-BROWSER scenario:
cred-paste (BC-BROWSER-001) · drive-by-download (002) · risky-extension-install (003) ·
saas-cross-origin-dlp (004) · screen-capture (005).

### 4c. Multi-plane packages — `scenarios/multi_plane/packages/` (6 files)

Self-contained runnable bundles. **SIM-MP-004** (APT29) is the reference exemplar with a
full `docker-compose.yml` (Kali attacker + run.sh) + `detections/xsoar_playbook.yml`
(`simmp004-auto-contain`) + detection artifacts; package YAML also exists for
SIM-MP-001/002/003/005.

## 5. Cross-domain reference indexes

### 5a. EAL plugin → scenarios

| Key | Scenarios | Count |
|---|---|---|
| `agentic_egress` | SIM-KOI-001, SIM-KOI-002, SIM-KOI-003, SIM-KOI-004, SIM-KOI-005 | 5 |
| `browser_attack_runner` | SIM-BROWSER-001, SIM-BROWSER-002, SIM-BROWSER-003, SIM-BROWSER-004, SIM-BROWSER-005 | 5 |
| `bulk_https_exfil` | SIM-NDR-005 | 1 |
| `c2_http_beacon` | SIM-NDR-001, SIM-TIM-001, SIM-TIM-002 | 3 |
| `dns_tunnel_exfil` | SIM-NDR-002 | 1 |
| `ftp_egress` | SIM-NDR-006 | 1 |
| `idp_signin_emulator` | SIM-ITDR-001, SIM-ITDR-002, SIM-ITDR-003, SIM-ITDR-004, SIM-ITDR-005, SIM-ITDR-008 | 6 |
| `llm_provider_egress` | SIM-AIACC-001, SIM-AIACC-002, SIM-AIACC-003, SIM-AIACC-004, SIM-AIACC-005 | 5 |
| `oauth_grant_emulator` | SIM-CLOUD-001, SIM-CLOUD-002, SIM-CLOUD-003, SIM-CLOUD-004, SIM-CLOUD-005 | 5 |
| `smb_rpc_sweep` | SIM-NDR-004 | 1 |
| `ssh_egress` | SIM-NDR-007 | 1 |
| `stratum_tcp_connect` | SIM-NDR-003 | 1 |

### 5b. Tool adapter (`adapter_ref`) → scenarios

| Key | Scenarios | Count |
|---|---|---|
| `TOOL-ATOMIC-RED-TEAM` | SIM-EDR-001, SIM-EDR-002, SIM-EDR-003, SIM-EDR-004, SIM-EDR-005, SIM-EDR-006, SIM-MP-005 | 7 |
| `TOOL-BLOODHOUND` | SIM-MP-002, SIM-MP-004 | 2 |
| `TOOL-BLOODYAD` | SIM-ITDR-007 | 1 |
| `TOOL-CLOUDSPLAINING` | SIM-CDR-007 | 1 |
| `TOOL-CMSEEK` | SIM-ASM-004 | 1 |
| `TOOL-COMMIX` | SIM-ASM-004 | 1 |
| `TOOL-CORTEX-AGENTIC-PACK` | SIM-KOI-001, SIM-KOI-002, SIM-KOI-003, SIM-KOI-004, SIM-KOI-005 | 5 |
| `TOOL-CORTEX-BROWSER-ATTACKER` | SIM-BROWSER-001, SIM-BROWSER-002, SIM-BROWSER-003, SIM-BROWSER-004, SIM-BROWSER-005 | 5 |
| `TOOL-CORTEX-PROMPT-ATTACKER` | SIM-AIRS-001, SIM-AIRS-002, SIM-AIRS-003, SIM-AIRS-004, SIM-AIRS-005 | 5 |
| `TOOL-DEEPCE` | SIM-CDR-001 | 1 |
| `TOOL-FEROXBUSTER` | SIM-ASM-004 | 1 |
| `TOOL-GITLEAKS` | SIM-CDR-007 | 1 |
| `TOOL-GOBUSTER` | SIM-ASM-004 | 1 |
| `TOOL-IMPACKET` | SIM-ITDR-006, SIM-MP-002 | 2 |
| `TOOL-KRBRELAYUP` | SIM-ITDR-007 | 1 |
| `TOOL-KUBE-BENCH` | SIM-CDR-007 | 1 |
| `TOOL-KUBESCAPE` | SIM-CDR-007 | 1 |
| `TOOL-MASSCAN` | SIM-NDR-004 | 1 |
| `TOOL-MIMIKATZ` | SIM-MP-002, SIM-MP-004 | 2 |
| `TOOL-NIKTO` | SIM-ASM-004 | 1 |
| `TOOL-NMAP` | SIM-ASM-001, SIM-EDR-005, SIM-MP-005, SIM-NDR-004 | 4 |
| `TOOL-NUCLEI` | SIM-ASM-001, SIM-ASM-002 | 2 |
| `TOOL-PACU` | SIM-MP-004 | 1 |
| `TOOL-PRINTSPOOFER` | SIM-ITDR-007 | 1 |
| `TOOL-PROWLER` | SIM-CSPM-001 | 1 |
| `TOOL-PYPYKATZ` | SIM-ITDR-007 | 1 |
| `TOOL-RUBEUS` | SIM-ITDR-006, SIM-MP-002 | 2 |
| `TOOL-SCAPY` | SIM-MP-003 | 1 |
| `TOOL-SCOUTSUITE` | SIM-CSPM-001 | 1 |
| `TOOL-SLIVER` | SIM-MP-001 | 1 |
| `TOOL-SQLMAP` | SIM-ASM-004 | 1 |
| `TOOL-TOKENVATOR` | SIM-ITDR-007 | 1 |
| `TOOL-TRIVY` | SIM-CDR-007 | 1 |
| `TOOL-WHATWEB` | SIM-ASM-004 | 1 |

### 5c. `infra_modules_needed` → scenarios

| Key | Scenarios | Count |
|---|---|---|
| `ai-spm` | SIM-AISPM-001, SIM-AISPM-002, SIM-AISPM-003, SIM-AISPM-004, SIM-AISPM-005, SIM-AISPM-006 | 6 |
| `asm` | SIM-ASM-001, SIM-ASM-002, SIM-ASM-003, SIM-ASM-004 | 4 |
| `base` | SIM-AIACC-001, SIM-AIACC-002, SIM-AIACC-003, SIM-AIACC-004, SIM-AIACC-005, SIM-AIRS-001, SIM-AIRS-002, SIM-AIRS-003 … (+52) | 60 |
| `cdr` | SIM-CDR-007, SIM-CDR-008, SIM-MP-004 | 3 |
| `cspm` | SIM-CDR-008, SIM-CSPM-001 | 2 |
| `edr` | SIM-MP-001, SIM-MP-002, SIM-MP-003, SIM-MP-004, SIM-MP-005 | 5 |
| `itdr` | SIM-ITDR-001, SIM-ITDR-002, SIM-ITDR-003, SIM-ITDR-004, SIM-ITDR-005, SIM-ITDR-006, SIM-ITDR-007, SIM-ITDR-008 … (+3) | 11 |
| `ndr` | SIM-MP-001, SIM-MP-002, SIM-MP-003, SIM-MP-005, SIM-NDR-001, SIM-NDR-002, SIM-NDR-003, SIM-NDR-004 … (+5) | 13 |
| `tim` | SIM-MP-004, SIM-NDR-002, SIM-TIM-001, SIM-TIM-002 | 4 |

### 5d. `required_content` repo → scenarios

| Key | Scenarios | Count |
|---|---|---|
| `0xbadjuju/Tokenvator` | SIM-ITDR-007 | 1 |
| `3CORESec/testmynids.org` | SIM-MP-001, SIM-MP-003, SIM-MP-005, SIM-NDR-001 | 4 |
| `BishopFox/sliver` | SIM-MP-004 | 1 |
| `BloodHoundAD/SharpHound` | SIM-MP-002 | 1 |
| `CravateRouge/bloodyAD` | SIM-ITDR-007 | 1 |
| `Dec0ne/KrbRelayUp` | SIM-ITDR-007 | 1 |
| `GhostPack/Rubeus` | SIM-ITDR-006, SIM-MP-002 | 2 |
| `PaloAltoNetworks/mocktaxii` | SIM-TIM-001, SIM-TIM-002 | 2 |
| `RhinoSecurityLabs/pacu` | SIM-MP-004 | 1 |
| `datadog/stratus-red-team` | SIM-MP-004 | 1 |
| `fortra/impacket` | SIM-ITDR-006, SIM-MP-002, SIM-MP-005 | 3 |
| `hankthebldr/cortex-browser-attacker` | SIM-BROWSER-001, SIM-BROWSER-002, SIM-BROWSER-003, SIM-BROWSER-004, SIM-BROWSER-005 | 5 |
| `hankthebldr/cortex-malicious-agentic-pack` | SIM-BROWSER-003, SIM-KOI-001, SIM-KOI-002, SIM-KOI-003, SIM-KOI-004, SIM-KOI-005 | 6 |
| `hankthebldr/cortex-prompt-attacker` | SIM-AIRS-001, SIM-AIRS-002, SIM-AIRS-003, SIM-AIRS-004, SIM-AIRS-005 | 5 |
| `hankthebldr/cortex-vulnerable-llm` | SIM-AIRS-001, SIM-AIRS-002, SIM-AIRS-003, SIM-AIRS-004, SIM-AIRS-005 | 5 |
| `itm4n/PrintSpoofer` | SIM-ITDR-007 | 1 |
| `outflanknl/RedELK` | SIM-MP-001 | 1 |
| `skelsec/pypykatz` | SIM-ITDR-007 | 1 |

---

## 6. Gap register (current — POV-impact-ranked)

Reconciled 2026-06-15. The large 2026-06-07 audit backlog is closed (see
[`GAP-ANALYSIS.md`](GAP-ANALYSIS.md)); what remains is small and mostly cosmetic:

- **[S-01] LOW** — SIM-NDR-003..007 have 2 steps each; the loader does not enforce a
  3-step floor (the schema doc's "≥3" is advisory). Intentional for terse single-protocol
  EAL egress validations.
- **[S-07] LOW** — v2.0 KPI/MOAT methodology metadata is populated by a minority of
  scenarios (all 6 AI_SPM + the F2 multi-plane pair + the 2026-06 additions); the legacy
  corpus is bare. Roll out opportunistically or scope the v2.0 story.
- **[S-16] LOW** — `T1176 Browser Extensions` is used for VS Code / Chrome-extension KOI
  and BROWSER scenarios; a loose fit worth a technique-accuracy review for the heatmap.
- **De-hand-rolling (open)** — CDR/NDR scenarios still inline raw shell where an
  equivalent adapter exists; no CI lint yet flags `external_tools` without `adapter_ref`.

**Closed since the original audit** (no longer gaps): S-05 (dead IOC refs — 494/494
resolve) · S-06 (`airs` module — AIRS now provisions only `base`; no dead reference) ·
S-09 (declared vs actual `detection_types`) · S-11/S-12 (CLAUDE.md plane-table drift) ·
S-17 (OWASP LLM03/04/05/09 probes now present) · S-18 (multi-plane packages) · GAP-1
(TA0042 via SIM-TIM-002) · GAP-10 (every plane now carries IOC coverage).

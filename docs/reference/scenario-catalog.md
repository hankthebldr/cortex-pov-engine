# Scenario Catalog — CortexSim Detection Simulation Engine

> **Reference doc — generated 2026-06-07.** Exhaustive, canonical inventory of every
> scenario YAML in `scenarios/`. Source-of-truth is the YAML itself; this doc is a
> derived archive. If you change a scenario, regenerate or hand-patch the relevant row.
>
> Cross-domain links use stable IDs: scenario `SIM-{PLANE}-{NNN}`, TTP card
> `TTP-2026-NNNN`, tool adapter `TOOL-*`, IaC module names, EAL plugin slugs.

## 0. Scope & method

- **What counts as a "scenario":** a top-level YAML under `scenarios/{plane}/` that the
  loader (`core/engine/scenario_loader.py`) ingests and validates against `ScenarioSchema`.
- **What does NOT count:** the loader explicitly **skips three sub-trees** —
  `scenarios/airs/probes/`, `scenarios/browser/campaigns/`, and
  `scenarios/multi_plane/packages/` (`_find_yaml_files` skip set
  `{"probes", "packages", "campaigns"}`). These hold supporting artifacts (probe packs,
  browser campaign declarations, packaged scenario bundles) consumed by EAL plugins /
  tools, **not** scenarios. They are catalogued separately in §13.
- **File count reconciliation:** `find scenarios -name '*.yml'` returns **77** files
  (76 + `_schema.yml`). Of those, **58 are loadable scenarios**; the remaining 18 are
  `_schema.yml` (1) + probes (10) + campaigns (5) + packages (2:
  `xsoar_playbook.yml`, `docker-compose.yml`). **The task brief's "76 scenarios" conflates
  scenario files with supporting YAML — the true active scenario count is 58.** See
  [GAP S-13].

### Totals at a glance

| Metric | Value |
|---|---|
| Loadable scenarios | **58** |
| Detection planes represented | 11 (EDR, CDR, NDR, ITDR, CLOUD_APP, ANALYTICS, AI_ACCESS, AIRS, AI_SPM, BROWSER, KOI) |
| Total execution steps (all scenarios) | 195 |
| Total `expected_detections` rows | 342 |
| Detections wired to a TTP card (`detection_id` set) | 341 |
| Unique MITRE techniques (primary + additional) | 70 |
| Scenarios using a tool adapter (`adapter_ref`) | 27 |
| Scenarios using an EAL plugin | 32 |
| Scenarios declaring `infra_modules_needed` | 48 |
| Scenarios declaring `required_content` | 21 |
| Scenarios with `moat_tier` set | 8 (all `MOAT`) |
| Scenarios with `methodology_family` set | 8 (F2×2, F3×3, F4×3) |
| TTP catalog cards available | 63 JSON files |
| TTP catalog (ttp_ref, detection_id) pairs available | 481 |
| Catalog detection pairs **not** referenced by any scenario | 148 |

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
| `expected_detections[].ttp_ref` + `detection_id` | optional | optional bridge | resolved by `ttp_catalog.find()` (warn-only) | Dangling pair = card silently absent from report. [GAP S-05] |
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

| Plane | Cortex engine | Scenarios | Unique techniques (incl. additional) | Steps | Detections | EAL plugin(s) | Adapter usage | IaC module |
|---|---|---|---|---|---|---|---|---|
| **EDR** | Cortex XDR Agent | 5 | 18 | 25 | 47 | — | `TOOL-ATOMIC-RED-TEAM`, `TOOL-NMAP` | (none declared; bare `infra=None`) |
| **CDR** | Cortex Cloud / Prisma Cloud Compute | 5 | 11 | 24 | 45 | — | `TOOL-DEEPCE` | (none declared) |
| **NDR** | NGFW / Network Security Analytics | 7 | 12 | 17 | 31 | `c2_http_beacon`, `dns_tunnel_exfil`, `stratum_tcp_connect`, `smb_rpc_sweep`, `bulk_https_exfil`, `ftp_egress`, `ssh_egress` | `TOOL-MASSCAN`, `TOOL-NMAP` (NDR-004) | `ndr` (+`tim`,`itdr` for some) |
| **ITDR** | Cortex ITDR | 5 | 8 | 15 | 22 | `idp_signin_emulator` | — | `itdr` |
| **CLOUD_APP** | Cortex Cloud App Security | 5 | 6 | 15 | 23 | `oauth_grant_emulator` | — | `base` only |
| **ANALYTICS** (multi-plane) | XSIAM Correlation Engine | 5 | 18 | 21 | 48 | — | `TOOL-SLIVER`, `TOOL-SCAPY`, `TOOL-BLOODHOUND`, `TOOL-MIMIKATZ`, `TOOL-RUBEUS`, `TOOL-PACU`, `TOOL-ATOMIC-RED-TEAM`, `TOOL-NMAP` | `edr`,`ndr`,`itdr`,`cdr`,`tim` |
| **AI_ACCESS** | Cortex AI Access Security | 5 | 5 | 15 | 24 | `llm_provider_egress` | — | `base` only |
| **AIRS** | Cortex AI Runtime Security | 5 | 4 | 15 | 25 | — (drives `cortex-prompt-attacker` via adapter) | `TOOL-CORTEX-PROMPT-ATTACKER` | `airs` (**MISSING module** [GAP S-06]) |
| **AI_SPM** | Cortex AI Security Posture Mgmt | 6 | 11 | 19 | 24 | — | — | `ai-spm` |
| **BROWSER** | Prisma Browser | 5 | 6 | 15 | 24 | `browser_attack_runner` | `TOOL-CORTEX-BROWSER-ATTACKER` | `base` only |
| **KOI** | Agentic endpoint / supply-chain | 5 | 6 | 15 | 29 | `agentic_egress` | `TOOL-CORTEX-AGENTIC-PACK` | `base` only |
| **TOTAL** | — | **58** | **70 (de-duplicated)** | **195** | **342** | 11 distinct plugins | 13 distinct adapters | 8 modules referenced |

Notes:
- **CLAUDE.md plane table is stale:** it lists CDR/EDR as "5 scenarios", NDR as "7", ITDR
  "5", Cloud App "5", Analytics "3 multi-plane", AI_ACCESS "5", AIRS "5", BROWSER "5",
  KOI "5" — but **Analytics is actually 5** (not 3) and **AI_SPM (6 scenarios) is entirely
  absent from the table**. [GAP S-11], [GAP S-12]
- "Unique techniques" counts primary + `additional_techniques` per plane (step-level
  techniques can differ further; see per-scenario `step_techs`).

---

## 3. Plane: EDR (Cortex XDR Agent) — 5 scenarios

All EDR scenarios are **fully on-host** (no EAL plugin), driven by Atomic Red Team
(`TOOL-ATOMIC-RED-TEAM`). They are the canonical identity-harness showcase — every step
runs as a service account to build process-causality chains. `infra_modules_needed` is
unset (legacy scenarios predate the IaC hint field). Cross-ref: `infra/modules/aws/edr/`
(used by multi-plane scenarios), TTP cards `TTP-2026-0002` (LSASS/shadow) + family.

| ID | Name | Tactic (ID) | Primary technique | Additional | Identities (default → step-level) | Steps | Detections (B/A/I) | Adapters | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-EDR-001 | Credential Dumping — /etc/shadow and Mimipenguin | Credential Access (TA0006) | T1003.008 OS Cred Dumping: /etc/passwd & /etc/shadow | T1552.001, T1003 | www-data → root, www-data | 5 | 4/5/1 (10) | TOOL-ATOMIC-RED-TEAM | UCS-EDR-01 / TC-EDR-01 |
| SIM-EDR-002 | Reverse Shell — Multi-Method Callback | Command & Control (TA0011) | T1059.004 Unix Shell | T1071.001, T1059.006 | www-data → www-data | 5 | 5/4/0 (9) | TOOL-ATOMIC-RED-TEAM | UCS-EDR-02 / TC-EDR-02 |
| SIM-EDR-003 | Linux Persistence — Cron, Systemd, Backdoor Users | Persistence (TA0003) | T1053.003 Cron | T1136.001, T1543.002, T1098.004 | root → root | 5 | 5/5/0 (10) | TOOL-ATOMIC-RED-TEAM | UCS-EDR-03 / TC-EDR-03 |
| SIM-EDR-004 | Defense Evasion — Log Tampering, Timestomping, Process Hiding | Defense Evasion (TA0005) | T1070.002 Clear Linux/Mac Logs | T1070.006, T1562.001, T1036.005 | root → root | 5 | 5/5/0 (10) | TOOL-ATOMIC-RED-TEAM | UCS-EDR-04 / TC-EDR-04 |
| SIM-EDR-005 | Lateral Movement — SSH Abuse, Tunneling, Internal Recon | Lateral Movement (TA0008) | T1021.004 Remote Services: SSH | T1046, T1572, T1018 | www-data → www-data | 5 | 3/5/0 (8) | TOOL-ATOMIC-RED-TEAM, TOOL-NMAP | UCS-EDR-05 / TC-EDR-05 |

Detection-type legend: **B**=BIOC, **A**=Analytics, **I**=IOC; `(N)`=total.

---

## 4. Plane: CDR (Cortex Cloud / Prisma Cloud Compute) — 5 scenarios

The only plane that uses `cleanup.k8s_teardown` (all 5). No EAL plugins; on-host/in-container
execution. `required_content`/`infra_modules_needed` unset (legacy). Cross-ref:
`infra/modules/aws/cdr/`.

| ID | Name | Tactic | Primary technique | Additional | Identities | Steps | Det (B/A/I) | Adapters | k8s teardown | UC / TC |
|---|---|---|---|---|---|---|---|---|---|---|
| SIM-CDR-001 | Container Enumeration via DEEPCE | Discovery (TA0007) | T1613 Container & Resource Discovery | T1082 | container-runtime, root, www-data → container-runtime | 5 | 4/5/0 (9) | TOOL-DEEPCE | yes | UCS-CDR-01 / TC-CDR-01 |
| SIM-CDR-002 | Cryptominer Deployment — Unit 42 XMRig Variant | Impact (TA0040) | T1496 Resource Hijacking | T1105 | container-runtime, nobody → container-runtime, nobody | 5 | 3/5/0 (8) | — | yes | UCS-CDR-02 / TC-CDR-02 |
| SIM-CDR-003 | Container Escape via Privileged Mode | Privilege Escalation (TA0004) | T1611 Escape to Host | T1610 | container-runtime, root → root | 5 | 5/5/0 (10) | — | yes | UCS-CDR-03 / TC-CDR-03 |
| SIM-CDR-004 | Kubernetes Lateral Movement and Persistence | Lateral Movement (TA0008) | T1021.001 Remote Services: SSH | T1053.005, T1552.001 | container-runtime, node → container-runtime, node | 5 | 5/5/0 (10) | — | yes | UCS-CDR-04 / TC-CDR-04 |
| SIM-CDR-005 | WildFire Malware Trigger — Simulated Backdoor | Execution (TA0002) | T1105 Ingress Tool Transfer | T1486, T1071.001 | container-runtime, www-data → container-runtime, www-data | 4 | 4/4/0 (8) | — | yes | UCS-CDR-05 / TC-CDR-05 |

---

## 5. Plane: NDR (NGFW / Network Security Analytics) — 7 scenarios

NDR scenarios are EAL-plugin-driven (one plugin per protocol). **SIM-NDR-003..007 have only
2 steps each** — they pass the loader but violate the documented "≥3 steps" rule [GAP S-01].
`infra_modules_needed` always includes `ndr`; some add `tim`/`itdr`. Cross-ref EAL plugin
implementations: `scripts/eal_simulator/` (per CLAUDE.md NDR row).

| ID | Name | Tactic | Primary technique | Additional | EAL plugin | Steps | Det (B/A/I) | Adapters | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|---|
| SIM-NDR-001 | NDR — C2 Beaconing EAL Validation | Command & Control (TA0011) | T1071.001 Web Protocols | T1568 | `c2_http_beacon` | 3 | 1/4/0 (5) | — | base, ndr | UCS-NDR-01 / TC-NDR-01 |
| SIM-NDR-002 | NDR — DNS Tunneling EAL Validation | Exfiltration (TA0010) | T1048.003 Exfil Over Unencrypted Non-C2 | T1572 | `dns_tunnel_exfil` | 3 | 1/5/0 (6) | — | base, ndr, tim | UCS-NDR-02 / TC-NDR-02 |
| SIM-NDR-003 | NDR — Cryptojacking Stratum App-ID Validation | Impact (TA0040) | T1496 Resource Hijacking | — | `stratum_tcp_connect` | **2** | 1/2/1 (4) | — | base, ndr | UCS-NDR-03 / TC-NDR-03 |
| SIM-NDR-004 | NDR — SMB/RPC Lateral Sweep EAL Validation | Discovery (TA0007) | T1046 Network Service Discovery | T1018, T1021.002 | `smb_rpc_sweep` | **2** | 1/3/0 (4) | TOOL-MASSCAN, TOOL-NMAP | base, ndr, itdr | UCS-NDR-04 / TC-NDR-04 |
| SIM-NDR-005 | NDR — Bulk HTTPS Exfiltration EAL Validation | Exfiltration (TA0010) | T1041 Exfil Over C2 Channel | T1567 | `bulk_https_exfil` | **2** | 1/3/0 (4) | — | base, ndr | UCS-NDR-05 / TC-NDR-05 |
| SIM-NDR-006 | NDR — FTP Cleartext Egress + Credentials EAL Validation | Command & Control (TA0011) | T1071 Application Layer Protocol | T1048.003 | `ftp_egress` | **2** | 1/2/1 (4) | — | base, ndr | UCS-NDR-06 / TC-NDR-06 |
| SIM-NDR-007 | NDR — SSH Outbound App-ID + Atypical Client-Banner EAL Validation | Command & Control (TA0011) | T1572 Protocol Tunneling | T1021.004 | `ssh_egress` | **2** | 1/2/1 (4) | — | base, ndr | UCS-NDR-07 / TC-NDR-07 |

- **SIM-NDR-003** declares `detection_types: [Analytics, IOC]` but emits a BIOC detection
  too — declared/actual mismatch [GAP S-09].
- **SIM-NDR-005** step-01 has one `expected_detection` with **no `detection_id`** (the only
  un-wired detection in the whole corpus) — its TTP card won't render inline [GAP S-05a].

---

## 6. Plane: ITDR (Cortex ITDR) — 5 scenarios

Synthetic IdP audit-log emission via the `idp_signin_emulator` EAL plugin. No tool adapters.
`infra_modules_needed: [base, itdr]` on all 5. Cross-ref `infra/modules/aws/itdr/` (AD lab).

| ID | Name | Tactic | Primary technique | Additional | EAL plugin | Steps | Det (B/A/I) | infra | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-ITDR-001 | ITDR — Impossible Travel (Okta US-West / APAC-East) | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud | T1539 | `idp_signin_emulator` | 3 | 1/3/0 (4) | base, itdr | UCS-ITDR-01 / TC-ITDR-01 |
| SIM-ITDR-002 | ITDR — MFA Fatigue / Push-Bombing (Okta) | Credential Access (TA0006) | T1621 MFA Request Generation | T1556.006 | `idp_signin_emulator` | 3 | 2/2/0 (4) | base, itdr | UCS-ITDR-02 / TC-ITDR-02 |
| SIM-ITDR-003 | ITDR — Credential Stuffing (failed-login burst) | Credential Access (TA0006) | T1110.004 Credential Stuffing | — | `idp_signin_emulator` | 3 | 1/3/0 (4) | base, itdr | UCS-ITDR-03 / TC-ITDR-03 |
| SIM-ITDR-004 | ITDR — Session Token Replay Across Geo / UA | Credential Access (TA0006) | T1539 Steal Web Session Cookie | T1550.004, T1078.004 | `idp_signin_emulator` | 3 | 2/3/0 (5) | base, itdr | UCS-ITDR-04 / TC-ITDR-04 |
| SIM-ITDR-005 | ITDR — Brute-Force Causing Account Lockout (Microsoft) | Credential Access (TA0006) | T1110.003 Password Spraying | T1110.001 | `idp_signin_emulator` | 3 | 2/3/0 (5) | base, itdr | UCS-ITDR-05 / TC-ITDR-05 |

---

## 7. Plane: CLOUD_APP (Cortex Cloud App Security) — 5 scenarios

Outbound OAuth 2.0 authorize requests against Okta / Microsoft / Google via the
`oauth_grant_emulator` EAL plugin. SIM-CLOUD-005 is a **benign baseline / FP-suppression
control** (intentionally should NOT alert). `infra_modules_needed: [base]` only.

| ID | Name | Tactic | Primary technique | Additional | EAL plugin | Steps | Det (B/A/I) | UC / TC | Note |
|---|---|---|---|---|---|---|---|---|---|
| SIM-CLOUD-001 | Okta Risky OAuth Drive-Scope Grant | Initial Access (TA0001) | T1550.001 App Access Token | T1528 | `oauth_grant_emulator` | 3 | 1/3/0 (4) | UCS-CAPP-01 / TC-CAPP-01 | |
| SIM-CLOUD-002 | Microsoft Admin-Consent-Required Scope Request | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud | T1098, T1528 | `oauth_grant_emulator` | 3 | 2/3/0 (5) | UCS-CAPP-02 / TC-CAPP-02 | |
| SIM-CLOUD-003 | Google Full-Mailbox + Offline Token Replay Risk | Collection (TA0009) | T1114.002 Remote Email Collection | T1528, T1550.001 | `oauth_grant_emulator` | 3 | 1/4/0 (5) | UCS-CAPP-03 / TC-CAPP-03 | |
| SIM-CLOUD-004 | Cross-Provider OAuth Grant Rotation (Okta→MS→Google) | Defense Evasion (TA0005) | T1090 Proxy | T1550.001, T1528 | `oauth_grant_emulator` | 3 | 0/5/0 (5) | UCS-CAPP-04 / TC-CAPP-04 | |
| SIM-CLOUD-005 | Benign OAuth Baseline (control / FP-suppression) | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud | — | `oauth_grant_emulator` | 3 | 0/4/0 (4) | UCS-CAPP-05 / TC-CAPP-05 | **benign control — expected NOT to alert** |

---

## 8. Plane: ANALYTICS (XSIAM Correlation Engine, multi-plane) — 5 scenarios

These are the cross-plane stitching showcases. **Files are named `mp-NNN-*.yml`** but IDs are
`SIM-MP-NNN` — CLAUDE.md's reference `scenarios/multi_plane/SIM-MP-*.yml` is the **wrong
filename glob** [GAP S-12]. Two of five carry full F2 metadata (`methodology_family: F2`,
`correlation_window_seconds`, `required_planes_in_incident`, `stitching_key`); the other
three do not despite being multi-plane [GAP S-04]. SIM-MP-004 ships a **supporting package**
under `scenarios/multi_plane/packages/SIM-MP-004/` (see §13).

| ID | Name | Tactic | Primary technique | Additional | Detection planes (count) | Steps | Det (B/A/I) | Adapters | F2 metadata | UC / TC |
|---|---|---|---|---|---|---|---|---|---|---|
| SIM-MP-001 | C2 Beacon Callback — NGFW + XDR Stitch Validation | Command & Control (TA0011) | T1071.001 Web Protocols | T1059.004, T1105 | EDR:4 NDR:3 ANALYTICS:3 | 4 | 3/6/1 (10) | TOOL-SLIVER | **MOAT**, F2, KPI=Causality Chain Completeness, win=60, planes=[EDR,NDR], key=src_host | UCS-NDR-01 / TC-NDR-01 |
| SIM-MP-002 | Kerberoast + Pass-the-Hash Lateral — ITDR+EDR+NDR Stitch | Credential Access (TA0006) | T1558.003 Kerberoasting | T1550.002, T1021.002, T1003.006 | ITDR:5 NDR:3 ANALYTICS:3 EDR:1 | 4 | 5/7/0 (12) | TOOL-BLOODHOUND, TOOL-MIMIKATZ, TOOL-RUBEUS | none (multi-plane but no F2 fields) | UCS-MP-02 / TC-MP-02 |
| SIM-MP-003 | Staged Data Exfil via DNS Tunneling — XDR+NGFW Stitch | Exfiltration (TA0010) | T1048.003 Exfil Over Unencrypted Non-C2 | T1074.001, T1572, T1005 | EDR:5 NDR:3 ANALYTICS:2 | 4 | 4/5/1 (10) | TOOL-SCAPY | none | UCS-MP-03 / TC-MP-03 |
| SIM-MP-004 | APT29 Cloud Credential Theft → Lateral → Exfil | Credential Access (TA0006) | T1552.001 Credentials In Files | T1059.004, T1078.004, T1580, T1530, T1537 | EDR:3 CLOUD_APP:6 ANALYTICS:3 | 5 | 5/6/1 (12) | TOOL-BLOODHOUND, TOOL-MIMIKATZ, TOOL-PACU | none | UCS-MP-04 / TC-MP-04 |
| SIM-MP-005 | Cross-Plane Correlation MOAT — EDR+NDR+ITDR Stitch (TC-IR-05) | Lateral Movement (TA0008) | T1078 Valid Accounts | T1071.001, T1059.004, T1110.003 | NDR:1 EDR:1 ITDR:1 ANALYTICS:1 | 4 | 1/3/0 (4) | TOOL-ATOMIC-RED-TEAM, TOOL-NMAP | **MOAT**, F2, KPI=Cross-Source Correlation Rate, win=30, planes=[EDR,NDR,ITDR], key=src_host | UCS-IR-02 / TC-IR-05 |

- **SIM-MP-003** declares `detection_types: [Analytics, BIOC]` but emits an IOC detection —
  declared/actual mismatch [GAP S-09].
- **SIM-MP-005** uses non-`UCS-MP-*` UC ref (`UCS-IR-02 / TC-IR-05`) — IR-family reference,
  not multi-plane family. Worth noting for UC/TC traceability [GAP S-14].

---

## 9. Plane: AI_ACCESS (Cortex AI Access Security) — 5 scenarios

Outbound to OpenAI / Gemini / Anthropic via the `llm_provider_egress` EAL plugin with planted
DLP markers. `infra_modules_needed: [base]`. SIM-AIACC-005 emits a cross-provider ANALYTICS
correlation detection. Cross-ref TTP cards `TTP-2026-0007..0011` (sim-aiacc-001..005).

| ID | Name | Tactic | Primary technique | Additional | EAL plugin | Steps | Det (B/A/I) | UC / TC |
|---|---|---|---|---|---|---|---|---|
| SIM-AIACC-001 | Source Code Paste to Public ChatGPT | Exfiltration (TA0010) | T1567 Exfil Over Web Service | T1041 | `llm_provider_egress` | 3 | 1/4/0 (5) | UCS-AIACC-01 / TC-AIACC-01 |
| SIM-AIACC-002 | AWS Access Key Leaked to Anthropic API | Credential Access (TA0006) | T1552 Unsecured Credentials | T1567 | `llm_provider_egress` | 3 | 1/3/1 (5) | UCS-AIACC-02 / TC-AIACC-02 |
| SIM-AIACC-003 | High-Volume Gemini Prompt Burst from Single User | Exfiltration (TA0010) | T1567 Exfil Over Web Service | — | `llm_provider_egress` | 3 | 0/5/0 (5) | UCS-AIACC-03 / TC-AIACC-03 |
| SIM-AIACC-004 | Jailbreak Prompt Fingerprint Detection | Initial Access (TA0001) | T1656 Impersonation | T1567 | `llm_provider_egress` | 3 | 2/2/0 (4) | UCS-AIACC-04 / TC-AIACC-04 |
| SIM-AIACC-005 | Cross-Provider Rotation (OpenAI→Gemini→Anthropic) | Defense Evasion (TA0005) | T1090 Proxy | T1567 | `llm_provider_egress` | 3 | 0/5/0 (5) | UCS-AIACC-05 / TC-AIACC-05 |

---

## 10. Plane: AIRS (Cortex AI Runtime Security) — 5 scenarios

OWASP LLM01-10 against `cortex-vulnerable-llm`, driven by `cortex-prompt-attacker` via the
`TOOL-CORTEX-PROMPT-ATTACKER` adapter (note: these scenarios use the **adapter path**, not a
named EAL plugin — the `airs_prompt_attack` plugin is referenced in CLAUDE.md but the YAML
wires the adapter). `required_content`: `cortex-vulnerable-llm` + `cortex-prompt-attacker`.
`infra_modules_needed: [base, airs]` — **the `airs` IaC module does not exist** [GAP S-06].
Probe packs that feed these scenarios live in `scenarios/airs/probes/` (§13).

| ID | Name | OWASP | Tactic | Primary technique | Additional | Adapter | Steps | Det (B/A/I) | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-AIRS-001 | Direct Prompt Injection | LLM01 | Initial Access (TA0001) | T1656 Impersonation | — | TOOL-CORTEX-PROMPT-ATTACKER | 3 | 1/4/0 (5) | UCS-AIRS-01 / TC-AIRS-01 |
| SIM-AIRS-002 | Indirect Prompt Injection via RAG Document | LLM01+LLM08 | Initial Access (TA0001) | T1059 Command & Scripting Interpreter | T1656 | TOOL-CORTEX-PROMPT-ATTACKER | 3 | 1/4/0 (5) | UCS-AIRS-02 / TC-AIRS-02 |
| SIM-AIRS-003 | System Prompt Leakage | LLM07 | Discovery (TA0007) | T1082 System Information Discovery | — | TOOL-CORTEX-PROMPT-ATTACKER | 3 | 2/3/0 (5) | UCS-AIRS-03 / TC-AIRS-03 |
| SIM-AIRS-004 | Excessive Agency / Tool-Call Abuse | LLM06 | Execution (TA0002) | T1059 Command & Scripting Interpreter | T1656 | TOOL-CORTEX-PROMPT-ATTACKER | 3 | 2/3/0 (5) | UCS-AIRS-04 / TC-AIRS-04 |
| SIM-AIRS-005 | Unbounded Consumption / Token-Exhaustion DoS | LLM10 | Impact (TA0040) | T1499 Endpoint DoS | — | TOOL-CORTEX-PROMPT-ATTACKER | 3 | 0/5/0 (5) | UCS-AIRS-05 / TC-AIRS-05 |

---

## 11. Plane: AI_SPM (Cortex AI Security Posture Management) — 6 scenarios

**Entirely absent from the CLAUDE.md plane table** [GAP S-11]. Static AI asset inventory +
config posture, all run as `root`, no EAL plugins, tools = `aws-cli`/`jq`/`curl`.
`infra_modules_needed: [base, ai-spm]` (module exists at `infra/modules/aws/ai-spm/`).
**This is the only plane that broadly populates v2.0 methodology metadata** — all 6 carry
`moat_tier: MOAT` and an `F3`/`F4` family. Note the **TC ref typo family**: `TC-AISP-*`
(not `TC-AISPM-*`) while `uc_ref` is `UCS-AISPM-01` for all six (single UC) [GAP S-15].

| ID | Name | Tactic | Primary technique | Additional | Steps | Det (B/A/I) | moat / family / KPI | UC / TC |
|---|---|---|---|---|---|---|---|---|
| SIM-AISPM-001 | AI Asset Discovery & Inventory — Shadow AI Detection | Discovery (TA0007) | T1526 Cloud Service Discovery | T1580 | 4 | 0/4/0 (4) | MOAT / F3 / Asset Discovery Coverage | UCS-AISPM-01 / TC-AISP-01 |
| SIM-AISPM-002 | AI Model Security Assessment — Overprivileged Role + Misconfig | Initial Access (TA0001) | T1078.004 Valid Accounts: Cloud | T1098 | 3 | 0/3/0 (3) | MOAT / F4 / MTTD | UCS-AISPM-01 / TC-AISP-02 |
| SIM-AISPM-003 | AI Ecosystem & Supply Chain Risk — Vuln ML Deps + EU AI Act | Initial Access (TA0001) | T1195.002 Compromise Software Supply Chain | T1574 | 3 | 0/4/0 (4) | MOAT / F3 / Asset Discovery Coverage | UCS-AISPM-01 / TC-AISP-03 |
| SIM-AISPM-004 | AI Static Risk Analysis — Hardcoded Creds + Insecure Pickle | Credential Access (TA0006) | T1552.001 Credentials In Files | T1027, T1565.001 | 3 | 0/5/0 (5) | MOAT / F4 / MTTD | UCS-AISPM-01 / TC-AISP-04 |
| SIM-AISPM-005 | AI Sensitive Data Classification — PII/PHI/PCI in Training Sets | Collection (TA0009) | T1530 Data from Cloud Storage | T1213 | 3 | 0/5/0 (5) | MOAT / F4 / MTTD | UCS-AISPM-01 / TC-AISP-05 |
| SIM-AISPM-006 | AI Security Dashboard & Posture — Aggregate View | Discovery (TA0007) | T1526 Cloud Service Discovery | — | 3 | 0/3/0 (3) | MOAT / F3 / Asset Discovery Coverage | UCS-AISPM-01 / TC-AISP-06 |

---

## 12. Plane: BROWSER (Prisma Browser) — 5 scenarios + Plane: KOI — 5 scenarios

### BROWSER — Playwright-driven via `cortex-browser-attacker` + `browser_attack_runner`

`required_content: cortex-browser-attacker`. `infra_modules_needed: [base]`. SIM-BROWSER-003
**bridges into the KOI plane** (emits KOI detections) and pulls in `cortex-malicious-agentic-pack`.
Browser campaign declarations live in `scenarios/browser/campaigns/` (§13).

| ID | Name | Tactic | Primary technique | Additional | Adapter / EAL | Steps | Det (B/A/I) | Detection planes | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-BROWSER-001 | Credential Paste into Untrusted Origin | Credential Access (TA0006) | T1552 Unsecured Credentials | — | TOOL-CORTEX-BROWSER-ATTACKER / browser_attack_runner | 3 | 1/3/0 (4) | BROWSER:4 | UCS-BROWSER-01 / TC-BROWSER-01 |
| SIM-BROWSER-002 | Drive-by Download from Phishing Page | Initial Access (TA0001) | T1189 Drive-by Compromise | T1566 | TOOL-CORTEX-BROWSER-ATTACKER / browser_attack_runner | 3 | 1/4/0 (5) | BROWSER:4 NDR:1 | UCS-BROWSER-02 / TC-BROWSER-02 |
| SIM-BROWSER-003 | Sideloaded Risky Chrome Extension | Persistence (TA0003) | T1176 Browser Extensions | — | TOOL-CORTEX-BROWSER-ATTACKER / browser_attack_runner | 3 | 1/4/0 (5) | KOI:2 BROWSER:3 | UCS-BROWSER-03 / TC-BROWSER-03 |
| SIM-BROWSER-004 | Cross-Origin SaaS Copy-Paste DLP | Exfiltration (TA0010) | T1567 Exfil Over Web Service | — | TOOL-CORTEX-BROWSER-ATTACKER / browser_attack_runner | 3 | 1/4/0 (5) | BROWSER:3 ANALYTICS:2 | UCS-BROWSER-04 / TC-BROWSER-04 |
| SIM-BROWSER-005 | Screen Capture of Sensitive SaaS Page | Collection (TA0009) | T1113 Screen Capture | — | TOOL-CORTEX-BROWSER-ATTACKER / browser_attack_runner | 3 | 1/4/0 (5) | BROWSER:4 ANALYTICS:1 | UCS-BROWSER-05 / TC-BROWSER-05 |

### KOI — MCP / skills / extensions / PyPI via `cortex-malicious-agentic-pack` + `agentic_egress`

`required_content: cortex-malicious-agentic-pack`. `infra_modules_needed: [base]`. SIM-KOI-002
and SIM-KOI-005 fan into AIRS + ANALYTICS detection planes (cross-plane stitch).

| ID | Name | Tactic | Primary technique | Additional | Adapter / EAL | Steps | Det (B/A/I) | Detection planes | UC / TC |
|---|---|---|---|---|---|---|---|---|---|
| SIM-KOI-001 | Claude Desktop Installs Typosquat MCP Server | Initial Access (TA0001) | T1195 Supply Chain Compromise | T1059 | TOOL-CORTEX-AGENTIC-PACK / agentic_egress | 3 | 2/4/0 (6) | KOI:5 NDR:1 | UCS-KOI-01 / TC-KOI-01 |
| SIM-KOI-002 | Hidden Prompt Injection in MCP Tool Response | Initial Access (TA0001) | T1059 Command & Scripting Interpreter | T1656 | TOOL-CORTEX-AGENTIC-PACK / agentic_egress | 3 | 1/4/0 (5) | KOI:2 NDR:1 AIRS:1 ANALYTICS:1 | UCS-KOI-02 / TC-KOI-02 |
| SIM-KOI-003 | Backdoored PyPI Package Consumed by Agent | Initial Access (TA0001) | T1195.002 Compromise Software Supply Chain | T1059.006 | TOOL-CORTEX-AGENTIC-PACK / agentic_egress | 3 | 2/4/0 (6) | KOI:5 NDR:1 | UCS-KOI-03 / TC-KOI-03 |
| SIM-KOI-004 | Malicious VS Code Extension Permission Escalation | Persistence (TA0003) | T1176 Browser Extensions | T1195 | TOOL-CORTEX-AGENTIC-PACK / agentic_egress | 3 | 2/4/0 (6) | KOI:4 NDR:2 | UCS-KOI-04 / TC-KOI-04 |
| SIM-KOI-005 | Malicious Claude Skill With Hidden Instructions | Initial Access (TA0001) | T1656 Impersonation | T1195 | TOOL-CORTEX-AGENTIC-PACK / agentic_egress | 3 | 1/5/0 (6) | KOI:3 NDR:1 AIRS:1 ANALYTICS:1 | UCS-KOI-05 / TC-KOI-05 |

- **SIM-KOI-004** uses `T1176 Browser Extensions` for a **VS Code** extension — technique is a
  loose fit (T1176 is browser-specific); same technique reused by SIM-BROWSER-003. Worth a
  technique-accuracy review [GAP S-16].

---

## 13. Supporting (non-scenario) YAML — loader-skipped

These 17 files live under skip-listed sub-trees and are **not** loaded as scenarios. They are
consumed by EAL plugins / tools / packaging. Catalogued here for completeness.

### 13a. AIRS probe packs — `scenarios/airs/probes/` (10 files)

`promptmap`-compatible probe schema (`schema_version: 1`, `prompt`, `mutators`, `scorer`,
`extended_scorers`, `fail_conditions`). Consumed by `cortex-prompt-attacker`. Organized by
OWASP LLM id.

| File | name | OWASP | type | scorer |
|---|---|---|---|---|
| llm01/delimiter_smuggle.yml | delimiter_smuggle | LLM01 | prompt_injection | system_prompt_leak |
| llm01/ignore_previous_basic.yml | ignore_previous_basic | LLM01 | prompt_injection | system_prompt_leak |
| llm01/role_play_dan.yml | role_play_dan | LLM01 | jailbreak | instruction_override |
| llm02/customer_record_extract.yml | customer_record_extract | LLM02 | prompt_injection | pii_leak |
| llm06/tool_abuse_exec_shell.yml | tool_abuse_exec_shell | LLM06 | tool_abuse | exec_shell_called |
| llm06/tool_abuse_send_email.yml | tool_abuse_send_email | LLM06 | tool_abuse | tool_call_unsafe |
| llm07/initial_instruction.yml | initial_instruction | LLM07 | prompt_stealing | system_prompt_leak |
| llm07/repeat_words_above.yml | repeat_words_above | LLM07 | prompt_stealing | system_prompt_leak |
| llm08/rag_trigger.yml | rag_trigger | LLM08 | indirect_injection | rag_poisoned_match |
| llm10/dos_unbounded_tokens.yml | dos_unbounded_tokens | LLM10 | (DoS) | — |

Coverage note: probes exist for LLM01/02/06/07/08/10 only — **LLM03/04/05/09 have no probes**
[GAP S-17].

### 13b. Browser campaigns — `scenarios/browser/campaigns/` (5 files)

`cortex-browser-attacker` campaign declarations (`campaign_id: BC-BROWSER-NNN`,
`simulation_authorized`, `target_allowlist`, `browser_channel: prisma`, `actions[]`). One per
SIM-BROWSER scenario.

| File | campaign_id | maps to scenario |
|---|---|---|
| cred-paste.yml | BC-BROWSER-001 | SIM-BROWSER-001 |
| drive-by-download.yml | BC-BROWSER-002 | SIM-BROWSER-002 |
| risky-extension-install.yml | BC-BROWSER-003 | SIM-BROWSER-003 |
| saas-cross-origin-dlp.yml | BC-BROWSER-004 | SIM-BROWSER-004 |
| screen-capture.yml | BC-BROWSER-005 | SIM-BROWSER-005 |

### 13c. SIM-MP-004 package — `scenarios/multi_plane/packages/SIM-MP-004/` (2 YAML + supporting tree)

A self-contained, runnable bundle for the APT29 scenario. Contains `docker-compose.yml`
(Kali attacker container + run.sh) and `detections/xsoar_playbook.yml`
(`simmp004-auto-contain` XSOAR playbook: enrich host + cloud principal → isolate endpoint +
rotate IAM cred if severity ≥ High). Plus non-YAML detection artifacts
(`bioc_rules.json`, `correlation_rules.xql`, `ioc_list.csv`), `run.sh`, `README.md`, and
`architecture/ c2/ context/ evidence/ ttps/` subdirs. **Only SIM-MP-004 has a packaged
bundle — the other 4 ANALYTICS scenarios do not** [GAP S-18].

---

## 14. Cross-domain reference indexes

### 14a. EAL plugins → scenarios

| EAL plugin | Scenarios |
|---|---|
| `llm_provider_egress` | SIM-AIACC-001..005 |
| `oauth_grant_emulator` | SIM-CLOUD-001..005 |
| `idp_signin_emulator` | SIM-ITDR-001..005 |
| `browser_attack_runner` | SIM-BROWSER-001..005 |
| `agentic_egress` | SIM-KOI-001..005 |
| `c2_http_beacon` | SIM-NDR-001 |
| `dns_tunnel_exfil` | SIM-NDR-002 |
| `stratum_tcp_connect` | SIM-NDR-003 |
| `smb_rpc_sweep` | SIM-NDR-004 |
| `bulk_https_exfil` | SIM-NDR-005 |
| `ftp_egress` | SIM-NDR-006 |
| `ssh_egress` | SIM-NDR-007 |
| *(none)* | all EDR, CDR, ANALYTICS, AI_SPM, AIRS scenarios (AIRS uses an adapter, not a named plugin) |

### 14b. Tool adapter (`adapter_ref`) → scenarios (all 27 wired)

| Adapter | Scenarios |
|---|---|
| TOOL-ATOMIC-RED-TEAM | SIM-EDR-001..005, SIM-MP-005 |
| TOOL-NMAP | SIM-EDR-005, SIM-MP-005, SIM-NDR-004 |
| TOOL-DEEPCE | SIM-CDR-001 |
| TOOL-CORTEX-PROMPT-ATTACKER | SIM-AIRS-001..005 |
| TOOL-CORTEX-BROWSER-ATTACKER | SIM-BROWSER-001..005 |
| TOOL-CORTEX-AGENTIC-PACK | SIM-KOI-001..005 |
| TOOL-SLIVER | SIM-MP-001 |
| TOOL-SCAPY | SIM-MP-003 |
| TOOL-BLOODHOUND | SIM-MP-002, SIM-MP-004 |
| TOOL-MIMIKATZ | SIM-MP-002, SIM-MP-004 |
| TOOL-RUBEUS | SIM-MP-002 |
| TOOL-PACU | SIM-MP-004 |
| TOOL-MASSCAN | SIM-NDR-004 |

All 27 `adapter_ref` values resolve against an `adapter_id` in `tools/packs/*.yml` (69 packs).
No dangling adapter refs.

### 14c. `infra_modules_needed` → reference count (across 48 scenarios)

| Module | Referenced by | Module exists? |
|---|---|---|
| base | 48 | yes |
| ndr | 11 | yes |
| itdr | 8 | yes |
| ai-spm | 6 | yes |
| airs | 5 | **NO — module missing** [GAP S-06] |
| edr | 5 | yes |
| tim | 2 | yes |
| cdr | 1 | yes |

### 14d. `required_content` repos (21 scenarios)

| Repo | Referenced by (count) |
|---|---|
| hankthebldr/cortex-malicious-agentic-pack | 6 (KOI×5 + BROWSER-003) |
| hankthebldr/cortex-vulnerable-llm | 5 (AIRS) |
| hankthebldr/cortex-prompt-attacker | 5 (AIRS) |
| hankthebldr/cortex-browser-attacker | 5 (BROWSER) |
| 3CORESec/testmynids.org | 4 (MP-001, MP-003, MP-005, NDR-001) |
| fortra/impacket | 2 (MP-002, MP-005) |
| outflanknl/RedELK, GhostPack/Rubeus, BloodHoundAD/SharpHound, datadog/stratus-red-team, RhinoSecurityLabs/pacu, BishopFox/sliver | 1 each |

### 14e. Duplicate primary-technique coverage

Scenarios sharing the **same primary `mitre_technique`** (intentional cross-plane coverage in
most cases, but flagged for ATT&CK-heatmap dedup awareness):

| Technique | Scenarios | Same plane? |
|---|---|---|
| T1567 Exfil Over Web Service | SIM-AIACC-001, SIM-AIACC-003, SIM-BROWSER-004 | no (AI_ACCESS×2, BROWSER) |
| T1656 Impersonation | SIM-AIACC-004, SIM-AIRS-001, SIM-KOI-005 | no |
| T1078.004 Valid Accounts: Cloud | SIM-AISPM-002, SIM-CLOUD-002, SIM-CLOUD-005, SIM-ITDR-001 | no |
| T1059 Command & Scripting Interpreter | SIM-AIRS-002, SIM-AIRS-004, SIM-KOI-002 | **AIRS×2** |
| T1071.001 Web Protocols | SIM-MP-001, SIM-NDR-001 | no |
| T1048.003 Exfil Over Unencrypted Non-C2 | SIM-MP-003, SIM-NDR-002 | no |
| T1090 Proxy | SIM-AIACC-005, SIM-CLOUD-004 | no |
| T1176 Browser Extensions | SIM-BROWSER-003, SIM-KOI-004 | no (KOI use is a stretch — see S-16) |
| T1195.002 Compromise Software Supply Chain | SIM-AISPM-003, SIM-KOI-003 | no |
| T1496 Resource Hijacking | SIM-CDR-002, SIM-NDR-003 | no |
| T1526 Cloud Service Discovery | SIM-AISPM-001, SIM-AISPM-006 | **AI_SPM×2** |
| T1552 Unsecured Credentials | SIM-AIACC-002, SIM-BROWSER-001 | no |
| T1552.001 Credentials In Files | SIM-AISPM-004, SIM-MP-004 | no |

---

## 15. Gap register (POV-impact-ranked)

See the structured output for severity. Summary:

- **[S-05] HIGH** — 8 IOC `detection_id` refs are **dead** (don't resolve in TTP catalog) →
  inline detection cards silently missing from POV reports. Root cause: TTP IOC entries lack a
  `type` field, so the catalog slug is `ioc-none-<value>` while scenarios reference
  `ioc-<type>-<value>`. Affected: SIM-AIACC-002, SIM-EDR-001, SIM-MP-001, SIM-MP-003,
  SIM-MP-004, SIM-NDR-003, SIM-NDR-006, SIM-NDR-007.
- **[S-06] HIGH** — `airs` IaC module referenced by 5 AIRS scenarios but **does not exist**
  in any provider → IaC generator can't satisfy AIRS lab provisioning.
- **[S-01] MEDIUM** — SIM-NDR-003..007 have 2 steps, violating documented "≥3 steps" rule
  (loader doesn't enforce, so undetected).
- **[S-09] MEDIUM** — `detection_types` ⟂ actual step detections: SIM-MP-003 (missing IOC),
  SIM-NDR-003 (missing BIOC).
- **[S-11/S-12] MEDIUM** — CLAUDE.md doc drift: AI_SPM plane omitted entirely; Analytics
  listed as 3 (actually 5); `multi_plane/SIM-MP-*.yml` glob is wrong (files are `mp-*`).
- Plus lower-severity: schema doc vs code drift (S-08, S-10), naming inconsistencies (S-13,
  S-14, S-15), technique-accuracy nits (S-16), coverage gaps (S-17, S-18), unguarded empty
  detections / single un-wired detection (S-02, S-05a), partial F2 metadata (S-04), sparse
  v2.0 metadata adoption (S-07).

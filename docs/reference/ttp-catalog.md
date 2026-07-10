# TTP Detection Card Catalog — `detection_scanner/ttps/`

> Reference doc generated 2026-06-07. Domain: **TTP detection cards** (the
> schema-validated JSON detection corpus the cortex-pov-engine binds scenarios
> to). Companion runbook: [`detection_scanner/RUNBOOK.md`](../../detection_scanner/RUNBOOK.md).
> Corpus index doc: [`detection_scanner/README.md`](../../detection_scanner/README.md).
> Schema contract: `detection_scanner/schema/ttp-entry.schema.json`.

This catalog enumerates **all 63** TTP cards (every `detection_scanner/ttps/*.json`),
their detection bodies, MITRE chains, threat actors, PANW product mappings, and
their binding to scenario YAML. It also records the audit of which cards carry
real detection logic vs. skeleton placeholders, and — critically — which
**export artifacts are stale** (GitHub issue **#65**).

---

## 1. What a TTP card is and how it is consumed

A TTP card is a single JSON document describing one attack technique chain,
grounded in MITRE ATT&CK + Unit 42 / vendor reporting, with explicit Cortex /
PANW product detection mappings. Cards are the *detection* half of the engine;
scenario YAML under `scenarios/` is the *execution* half. They join via a
composite key.

**Lifecycle / authoring pipeline:**

1. `scripts/generate_card.py` — takes a scenario YAML + a new TTP id and emits a
   **draft** card under `ttps/_drafts/` with skeleton BIOC bodies
   (`// AUTO-GENERATED SKELETON — replace with real XQL before promotion.` +
   `| filter /* TODO: predicate matching the BIOC name */`). A human enriches
   the `logic` fields and promotes the file into `ttps/`.
2. `scripts/validate.py` — canonical validator. 12 checks incl. JSON Schema
   2020-12 validation, id uniqueness, filename==id, source-ref resolution,
   MITRE id format, UC/TC id patterns, score-weight sums. CI runs this.
3. `scripts/build-manifest.py` — generates `manifest.json` (the engine load-time
   index). **See gap G-01: `manifest.json` is absent and gitignored.**
4. `scripts/export_artifacts.py` — renders deployable artifacts under `exports/`
   (sigma, xql, correlation, xsoar_playbook) from the *current* card bodies.
   Pure read-then-write, deterministic.

**Engine consumption (cross-domain):** the FastAPI engine loads cards via
`core/engine/ttp_catalog.py` (it globs `ttps/*.json` directly — it does **not**
read `manifest.json`), keyed by `(ttp_ref, detection_id)`. Surfaced through
`core/api/ttps.py` (`GET /api/ttps`). Scenario YAML binds a card per step via
`expected_detections[].ttp_ref` (e.g. `ttp_ref: "TTP-2026-0012"`). Non-active
cards are loaded but skipped so historical POVs still resolve.

---

## 2. Corpus at a glance

| Metric | Value |
|---|---|
| Total cards | **63** (all `status: active`) |
| Cards with scenario binding | **59** of 63 |
| Cards with NO scenario binding | **4** (`TTP-2026-0001`, `TTP-2026-0002`, `TTP-2026-0003`, `TTP-2026-0006`) |
| Total BIOCs | **144** |
| Total XQL queries | **238** |
| Total correlation rules | **72** |
| Total IOCs | **27** |
| Total analytics-module references | **143** |
| Cards with 0 BIOCs (XQL-only detection) | **11** (`TTP-2026-0009`, `TTP-2026-0011`, `TTP-2026-0016`, `TTP-2026-0030`, `TTP-2026-0031`, `TTP-2026-0054`, `TTP-2026-0055`, `TTP-2026-0056`, `TTP-2026-0057`, `TTP-2026-0058`, `TTP-2026-0059`) |
| Destructive cards | **4** (`TTP-2026-0002`, `TTP-2026-0004`, `TTP-2026-0006`, `TTP-2026-0034`) |

### Severity distribution
| Severity | Count |
|---|---|
| high | 47 |
| critical | 10 |
| medium | 5 |
| informational | 1 |

### Safety class distribution
| Safety class | Count |
|---|---|
| lab-only | 38 |
| safe-by-design | 19 |
| destructive-with-cleanup | 6 |

### MITRE tactic coverage (by card count)
| Tactic | Count |
|---|---|
| Credential Access | 27 |
| Initial Access | 21 |
| Defense Evasion | 16 |
| Execution | 15 |
| Command and Control | 13 |
| Exfiltration | 12 |
| Persistence | 11 |
| Discovery | 11 |
| Collection | 9 |
| Lateral Movement | 8 |
| Impact | 6 |
| Privilege Escalation | 5 |

### PANW product coverage (by card count)
| PANW product / module | Count |
|---|---|
| cortex-xsiam | 63 |
| cortex-xdr | 28 |
| cortex-cloud | 24 |
| cortex-xsoar | 19 |
| ngfw-pa-series | 14 |
| ai-runtime-security | 7 |
| ai-access-security | 5 |
| advanced-wildfire | 3 |
| cortex-cdr | 2 |
| advanced-dns-security | 2 |
| prisma-access | 1 |
| cortex-asm | 1 |
| prisma-cloud | 1 |
| advanced-threat-prevention | 1 |

### Threat actors referenced (Unit 42 naming)
| Threat actor | Count |
|---|---|
| Ignoble Scorpius | 4 |
| Muddled Libra | 3 |
| APT29 | 3 |
| Akira affiliates | 2 |
| TeamTNT | 2 |
| Opportunistic credential abusers (TeamTNT, Scarlet Eel, automated scanners) | 1 |
| Black Basta affiliates | 1 |
| Lockbit affiliates (historical) | 1 |
| Volt Typhoon | 1 |
| Rocke | 1 |
| xHunt | 1 |
| Lapsus$ | 1 |
| Midnight Blizzard (APT29) | 1 |
| Multiple (Cobalt Strike operators) | 1 |

> **Cross-domain note:** the corpus is dominated by Credential Access (27),
> Initial Access (21) and Defense Evasion (16). Every card maps to
> `cortex-xsiam` (63/63); the next-most-mapped products are `cortex-xdr` (28),
> `cortex-cloud` (24), `cortex-xsoar` (19) and `ngfw-pa-series` (14).

---

## 3. Card → scenario binding map (by plane group)

Cards are numbered roughly by plane. The binding below is authoritative (read
from `scenarios/**/*.yml` `ttp_ref`), not from the in-card BIOC name convention.

| Plane group | Card id range |
|---|---|
| Hand-authored anchor cards (Unit 42 BlackSuit/Muddled Libra) | 0001-0006 |
| AI Access (AIACC) | 0007-0011 |
| AI Runtime Security (AIRS / OWASP LLM) | 0012-0016 |
| Prisma Browser (BROWSER) | 0017-0021 |
| Cloud Detection & Response / Containers (CDR) | 0022-0026 |
| Cloud App Security (CLOUD / OAuth) | 0027-0031 |
| Endpoint Detection & Response — Linux (EDR) | 0032-0036 |
| Identity Threat Detection & Response (ITDR) | 0037-0041 |
| Agentic / supply-chain (KOI) | 0042-0046 |
| Multi-plane stitching (MP) + NDR | 0047-0053, 0060-0063 |
| AI Security Posture Management (AISPM) | 0054-0059 |
| Network Detection & Response — extra (NDR) | 0061-0062 |

---

## 4. Master table — all 63 cards

Detection counts column is `bioc/xql/corr/ioc/analytics`.

| TTP id | Status | Name | Mapped scenario(s) | MITRE chain | Threat actors | bioc/xql/corr/ioc/analytics | PANW products |
|---|---|---|---|---|---|---|---|
| `TTP-2026-0001` | active | Help-Desk MFA Reset via Voice Impersonation (Muddled Libra P | (none — standalone) | T1078.004 → T1656 → T1556.006 | Muddled Libra | 2/2/1/0/2 | xdr, xsiam, xsoar, prisma-access |
| `TTP-2026-0002` | active | LSASS Memory Credential Dump via MiniDumpWriteDump (T1003.00 | (none — standalone) | T1003.001 | Ignoble Scorpius, Muddled Libra | 2/2/1/1/2 | advanced-wildfire, xdr, xsiam, xsoar |
| `TTP-2026-0003` | active | AWS IAM Access Key Abuse — Discovery + S3 Object Exfiltratio | (none — standalone) | T1078.004 → T1580 → T1530 → T1567.002 | Opportunistic credential abusers (TeamTNT, Scarlet | 3/3/1/0/3 | asm, cdr, cloud, xsiam, xsoar, prisma-cloud |
| `TTP-2026-0004` | active | DCSync — Domain Replication Abuse for Credential Extraction  | mp-002-kerberoast-lateral-smb.yml | T1003.006 | Ignoble Scorpius, Muddled Libra | 3/3/2/0/2 | xdr, xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0005` | active | Bulk Data Exfiltration via Rclone to Cloud Storage (T1567.00 | ndr-005-bulk-https-exfil.yml | T1567.002 → T1048.003 → T1074.001 | Ignoble Scorpius, Black Basta affiliates, Akira af | 3/3/1/2/3 | advanced-dns-sec, advanced-threat-prevention, cdr, xdr, xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0006` | active | Mass ESXi VM Encryption Orchestrated via Ansible (BlackSuit  | (none — standalone) | T1486 → T1490 → T1021.004 → T1059.004 | Ignoble Scorpius, Akira affiliates, Lockbit affili | 4/4/2/1/3 | cloud, xdr, xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0007` | active | AI Access — Source Code Paste to Public ChatGPT | sim-aiacc-001-source-code-to-chatgpt.yml | T1567 → T1041 | — | 1/4/1/0/2 | ai-access-sec, xsiam, ngfw-pa-series |
| `TTP-2026-0008` | active | AI Access — AWS Access Key Leaked to Anthropic API | sim-aiacc-002-aws-key-to-anthropic.yml | T1552.001 → T1567 | — | 1/3/1/1/2 | ai-access-sec, xsiam, ngfw-pa-series |
| `TTP-2026-0009` | active | AI Access — High-Volume Gemini Prompt Burst | sim-aiacc-003-high-volume-gemini.yml | T1567 | — | 0/5/1/0/2 | ai-access-sec, xsiam, ngfw-pa-series |
| `TTP-2026-0010` | active | AI Access — Jailbreak Prompt Fingerprint | sim-aiacc-004-jailbreak-fingerprint.yml | T1656 → T1567 | — | 2/2/1/0/2 | ai-access-sec, xsiam |
| `TTP-2026-0011` | active | AI Access — Cross-Provider Rotation (OpenAI → Gemini → Anthr | sim-aiacc-005-cross-provider-rotation.yml | T1090 → T1567 | — | 0/5/1/0/2 | ai-access-sec, xsiam, ngfw-pa-series |
| `TTP-2026-0012` | active | AIRS — Direct Prompt Injection (LLM01) | sim-airs-001-direct-prompt-injection.yml | T1656 → T1059 | — | 1/4/1/0/2 | ai-runtime-sec, xsiam |
| `TTP-2026-0013` | active | AIRS — Indirect Prompt Injection via RAG Document (LLM01 + L | sim-airs-002-indirect-rag-poisoning.yml | T1656 → T1059 | — | 1/4/1/0/3 | ai-runtime-sec, xsiam |
| `TTP-2026-0014` | active | AIRS — System Prompt Leakage (LLM07) | sim-airs-003-system-prompt-leak.yml | T1082 → T1656 | — | 2/3/1/0/2 | ai-runtime-sec, xsiam |
| `TTP-2026-0015` | active | AIRS — Excessive Agency / Tool-Call Abuse (LLM06) | sim-airs-004-tool-call-abuse.yml | T1059 → T1656 | — | 2/3/1/0/3 | ai-runtime-sec, xsiam |
| `TTP-2026-0016` | active | AIRS — Unbounded Consumption / Token-Exhaustion DoS (LLM10) | sim-airs-005-token-exhaustion-dos.yml | T1499 → T1499.003 | — | 0/5/1/0/3 | ai-runtime-sec, xsiam |
| `TTP-2026-0017` | active | Prisma Browser — Credential Paste into Untrusted Origin (T15 | sim-browser-001-credential-paste.yml | T1552 → T1056.003 | — | 1/3/1/0/2 | xdr, xsiam |
| `TTP-2026-0018` | active | Prisma Browser — Drive-by Download from Phishing Page (T1189 | sim-browser-002-drive-by-download.yml | T1189 → T1566.002 → T1105 | — | 1/4/1/0/3 | advanced-wildfire, xdr, xsiam |
| `TTP-2026-0019` | active | Prisma Browser — Sideloaded Risky Extension Install (T1176) | sim-browser-003-risky-extension-install.yml | T1176 → T1539 | — | 1/4/1/0/2 | xdr, xsiam |
| `TTP-2026-0020` | active | Prisma Browser — Cross-Origin SaaS Copy-Paste DLP (T1567) | sim-browser-004-saas-cross-origin-dlp.yml | T1567 → T1005 | — | 1/4/1/0/2 | xdr, xsiam |
| `TTP-2026-0021` | active | Prisma Browser — Screen Capture of Sensitive SaaS Page (T111 | sim-browser-005-screen-capture.yml | T1113 | — | 1/4/1/0/2 | xdr, xsiam |
| `TTP-2026-0022` | active | Container Enumeration via DEEPCE and LinPEAS (T1613) | cdr-001-container-enum.yml | T1613 → T1082 → T1552.001 → T1059.004 | TeamTNT | 9/3/1/2/2 | cloud, xdr, xsiam |
| `TTP-2026-0023` | active | Cryptominer Deployment — XMRig Resource Hijacking in Contain | cdr-002-cryptominer.yml | T1496 → T1105 → T1562.001 → T1053.005 | — | 8/3/1/2/2 | advanced-dns-sec, cloud, xdr, xsiam |
| `TTP-2026-0024` | active | Container Escape via Privileged Mode and nsenter (T1611) | cdr-003-container-escape.yml | T1611 → T1610 → T1083 → T1003.008 → T1053.003 | — | 10/3/1/1/2 | cloud, xdr, xsiam |
| `TTP-2026-0025` | active | Kubernetes Lateral Movement via Stolen Service-Account Token | cdr-004-k8s-lateral.yml | T1021.001 → T1552.001 → T1613 → T1053.005 | — | 10/3/1/0/2 | cloud, xdr, xsiam |
| `TTP-2026-0026` | active | WildFire Malware Trigger — Dropped Backdoor, C2 Beacon, Rans | cdr-005-wildfire-trigger.yml | T1105 → T1059.004 → T1071.001 → T1486 | — | 8/3/1/2/3 | advanced-wildfire, cloud, xdr, xsiam |
| `TTP-2026-0027` | active | Cloud App — Okta Risky OAuth Drive-Scope Grant (T1550.001) | sim-cloud-001-okta-risky-drive-grant.yml | T1550.001 → T1528 | — | 1/3/1/0/2 | cloud, xsiam |
| `TTP-2026-0028` | active | Cloud App — Microsoft Admin-Consent-Required Scope Request ( | sim-cloud-002-microsoft-admin-consent.yml | T1078.004 → T1098.003 → T1528 | Volt Typhoon | 2/3/1/0/2 | cloud, xsiam |
| `TTP-2026-0029` | active | Cloud App — Google Full-Mailbox + Offline Token Replay Risk  | sim-cloud-003-google-mailbox-takeover.yml | T1114.002 → T1528 → T1550.001 | — | 1/4/1/0/3 | cloud, xsiam |
| `TTP-2026-0030` | active | Cloud App — Cross-Provider OAuth Grant Rotation (Okta → MS → | sim-cloud-004-cross-provider-grant-rotation.yml | T1090 → T1550.001 → T1528 | — | 0/5/1/0/2 | cloud, xsiam |
| `TTP-2026-0031` | active | Cloud App — Benign OAuth Baseline FP-Suppression Control (T1 | sim-cloud-005-benign-baseline-control.yml | T1078.004 | — | 0/4/1/0/2 | cloud, xsiam |
| `TTP-2026-0032` | active | Linux Credential Dumping — /etc/shadow and Mimipenguin (T100 | edr-001-credential-dumping.yml | T1003.008 → T1552.001 → T1087.001 → T1003 | Rocke | 4/5/1/3/2 | xdr, xsiam, xsoar |
| `TTP-2026-0033` | active | Linux Reverse Shell — Multi-Method C2 Callback (T1059.004) | edr-002-reverse-shell.yml | T1059.004 → T1059.006 → T1071.001 → T1573.002 | — | 6/4/1/0/2 | xdr, xsiam |
| `TTP-2026-0034` | active | Linux Persistence — Cron, Systemd, Backdoor User and SSH Key | edr-003-persistence-mechanisms.yml | T1053.003 → T1543.002 → T1136.001 → T1098.004 → T1546.004 | TeamTNT | 5/5/1/0/2 | xdr, xsiam, xsoar |
| `TTP-2026-0035` | active | Linux Defense Evasion — Log Tampering, Timestomping, Masquer | edr-004-defense-evasion.yml | T1070.002 → T1070.003 → T1070.006 → T1036.005 → T1562.001 | xHunt | 5/5/1/0/2 | xdr, xsiam, xsoar |
| `TTP-2026-0036` | active | Linux Lateral Movement — SSH Abuse, Tunneling and Internal R | edr-005-lateral-movement.yml | T1021.004 → T1046 → T1572 → T1018 → T1016 | — | 3/5/1/0/2 | xdr, xsiam, xsoar |
| `TTP-2026-0037` | active | ITDR — Impossible Travel (Okta + Entra sign-ins from US-West | sim-itdr-001-impossible-travel.yml | T1078.004 → T1539 | — | 1/3/1/0/2 | xsiam |
| `TTP-2026-0038` | active | ITDR — MFA Fatigue / Push-Bombing (Okta) | sim-itdr-002-mfa-fatigue.yml | T1621 → T1556.006 | APT29, Lapsus$ | 2/2/1/0/2 | xsiam |
| `TTP-2026-0039` | active | ITDR — Credential Stuffing (failed-login burst across many u | sim-itdr-003-credential-stuffing.yml | T1110.004 | — | 1/3/1/0/2 | xsiam |
| `TTP-2026-0040` | active | ITDR — Session Token Replay Across Geo / User-Agent | sim-itdr-004-token-replay.yml | T1539 → T1550.004 → T1078.004 | APT29 | 2/3/1/0/2 | xsiam |
| `TTP-2026-0041` | active | ITDR — Brute-Force Causing Account Lockout (Microsoft) | sim-itdr-005-brute-force-lockout.yml | T1110.003 → T1110.001 | Midnight Blizzard (APT29) | 2/3/1/0/2 | xsiam |
| `TTP-2026-0042` | active | Typosquat MCP Server Installed by Claude Desktop (T1195) | sim-koi-001-typosquat-mcp-server.yml | T1195 → T1059 → T1059.006 | — | 2/4/1/1/2 | cloud, xdr, xsiam |
| `TTP-2026-0043` | active | Hidden Prompt Injection in MCP Tool Response (T1656 / T1059) | sim-koi-002-mcp-tool-response-injection.yml | T1656 → T1059 → T1552.001 | — | 1/4/1/1/3 | ai-runtime-sec, cloud, xsiam |
| `TTP-2026-0044` | active | Backdoored PyPI Package With Import-Time Subprocess (T1195.0 | sim-koi-003-backdoored-pypi-package.yml | T1195.002 → T1059.006 → T1552.001 | — | 2/4/1/2/2 | cloud, xdr, xsiam |
| `TTP-2026-0045` | active | Malicious VS Code Extension Reads Credentials On Activation  | sim-koi-004-vscode-extension-permission-escalation.yml | T1176 → T1195 → T1552.001 | — | 2/4/1/1/2 | cloud, xdr, xsiam |
| `TTP-2026-0046` | active | Malicious Claude Skill With Hidden Instructions In skill.md  | sim-koi-005-claude-skill-hidden-instructions.yml | T1656 → T1195 → T1552.001 | — | 1/5/1/1/3 | ai-runtime-sec, cloud, xsiam |
| `TTP-2026-0047` | active | C2 Beacon Callback — NGFW + XDR Causality Stitch (T1071.001) | mp-001-c2-beacon-ngfw-xdr-stitch.yml | T1071.001 → T1059.004 → T1572 → T1105 | Multiple (Cobalt Strike operators) | 3/6/3/1/3 | xdr, xsiam, xsoar |
| `TTP-2026-0048` | active | Staged Data Exfiltration via DNS Tunnel — XDR Staging + NGFW | mp-003-data-staged-exfil-dns-tunnel.yml | T1048.003 → T1074.001 → T1572 → T1005 | — | 4/5/3/1/3 | xdr, xsiam, xsoar |
| `TTP-2026-0049` | active | APT29 Hybrid Cloud Credential Theft → Cloud Pivot → S3 Exfil | mp-004-apt29-cloud-cred-theft.yml | T1552.001 → T1059.004 → T1078.004 → T1580 → T1530 → T1537 | APT29 | 5/6/3/1/3 | cloud, xdr, xsiam, xsoar |
| `TTP-2026-0050` | active | Periodic HTTP C2 Beaconing — NGFW EAL Network Detection (T10 | ndr-001-c2-beacon-eal-validation.yml | T1071.001 → T1568 | — | 1/4/1/0/3 | xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0051` | active | DNS Tunneling Exfiltration — High-Entropy Label Burst (T1048 | ndr-002-dns-tunnel-eal.yml | T1048.003 → T1572 | — | 1/5/1/0/3 | xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0052` | active | Cryptojacking — Stratum Mining Pool Connection via App-ID (T | ndr-003-cryptojacking-stratum.yml | T1496 | — | 1/2/1/1/2 | xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0053` | active | SMB/RPC Lateral Sweep — Internal Host Discovery (T1046 / T10 | ndr-004-smb-lateral-sweep.yml | T1046 → T1018 → T1021.002 | — | 1/3/1/0/2 | xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0054` | active | Shadow-AI Asset Discovery & Inventory — Cortex Cloud AI-SPM  | sim-aispm-001-ai-asset-discovery.yml | T1526 → T1580 | — | 0/4/1/0/2 | cloud, xsiam |
| `TTP-2026-0055` | active | AI Model Security Assessment — Overprivileged Role + Misconf | sim-aispm-002-ai-model-security-assessment.yml | T1078.004 → T1098 | — | 0/3/1/0/2 | cloud, xsiam |
| `TTP-2026-0056` | active | AI Ecosystem & Supply-Chain Risk — Vulnerable ML Dependencie | sim-aispm-003-ai-supply-chain.yml | T1195.002 → T1574 | — | 0/4/1/0/2 | cloud, xsiam |
| `TTP-2026-0057` | active | AI Static Risk Analysis — Hardcoded Creds + Insecure Pickle  | sim-aispm-004-ai-static-risk-analysis.yml | T1552.001 → T1027 → T1565.001 | — | 0/5/1/0/2 | cloud, xsiam |
| `TTP-2026-0058` | active | AI Sensitive-Data Classification — PII/PHI/PCI In Training S | sim-aispm-005-ai-sensitive-data.yml | T1530 → T1213 | — | 0/5/1/0/2 | cloud, xsiam |
| `TTP-2026-0059` | active | AI Security Dashboard & Posture — Aggregate Rollup Across Al | sim-aispm-006-ai-security-dashboard.yml | T1526 | — | 0/3/1/0/2 | cloud, xsiam |
| `TTP-2026-0060` | active | Cross-Plane Correlation MOAT — EDR + NDR + ITDR Stitch Into  | mp-005-cross-plane-correlation.yml | T1078 → T1071.001 → T1059.004 → T1110.003 | — | 3/4/1/0/2 | xdr, xsiam, ngfw-pa-series |
| `TTP-2026-0061` | active | FTP Cleartext Egress + Credential Exposure — Outbound STOR E | ndr-006-ftp-cleartext-egress.yml | T1071 → T1048.003 | — | 1/3/1/1/2 | xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0062` | active | SSH Outbound App-ID + Atypical Client Banner — C2 / Tunnel S | ndr-007-ssh-outbound-egress.yml | T1572 → T1021.004 | — | 1/3/1/1/2 | xsiam, xsoar, ngfw-pa-series |
| `TTP-2026-0063` | active | Kerberoast → Pass-the-Hash → DCSync Multi-Plane Stitch (T155 | mp-002-kerberoast-lateral-smb.yml | T1558.003 → T1550.002 → T1021.002 → T1003.006 | — | 3/6/2/0/3 | xdr, xsiam |

---

## 5. Detection-body audit (skeleton check)

**Result: all 63 card bodies contain real detection logic.** No card body
contains the `AUTO-GENERATED SKELETON`, `TODO: predicate`, `placeholder`, or
`replace with` markers; no card has identical copy-pasted BIOC bodies; no card
has a trivially short (<40 char) BIOC body. This reflects commit
`42a49c0 content(detections): real detection logic for all 63 TTP cards`.

Caveats worth surfacing:

- **11 cards have zero BIOCs** and express their detection entirely as
  XQL hunt queries (`TTP-2026-0009`, `TTP-2026-0011`, `TTP-2026-0016`, `TTP-2026-0030`, `TTP-2026-0031`, `TTP-2026-0054`, `TTP-2026-0055`, `TTP-2026-0056`, `TTP-2026-0057`, `TTP-2026-0058`, `TTP-2026-0059`).
  These are mostly the AISPM posture cards (0054-0059), the rate/rotation AIACC
  cards (0009, 0011), the OAuth rotation/baseline cloud cards (0030, 0031) and
  the AIRS token-exhaustion card (0016). This is by design (posture/volume
  detections are not process-causality BIOCs) but means a BIOC-only consumer
  sees nothing for them.
- The **AISPM cards (0054-0059) all carry `simulation_class: endpoint`** in
  their `pov_engine` block despite being cloud-posture scans — likely a
  copy-paste carryover (see gap G-05).

---

## 6. Export-artifact staleness audit — GitHub issue #65

`exports/` holds committed, deterministically-generated artifacts. They were
generated **before** the card bodies got real logic, so the SIM-* exports still
contain the skeleton. The fix is mechanical: re-run
`python3 scripts/export_artifacts.py --clean`.

### 6a. Export coverage gap

Only **15 of 63** cards have *any* export. The export README index
(`exports/README.md`) is itself stale — it lists only those 15.

| Export kind | Files on disk | Cards covered |
|---|---|---|
| `exports/xql/` | 15 | 0001-0007, 0012, 0017, 0023, 0027, 0032, 0038, 0042, 0047 |
| `exports/sigma/` | 15 | same as xql |
| `exports/correlation/` | 6 | 0001-0006 only |
| `exports/xsoar_playbook/` | 7 | 0001-0006 + 0047 |

**48 cards (0008-0011, 0013-0016, 0018-0022, 0024-0026, 0028-0031, 0033-0037,
0039-0041, 0043-0046, 0048-0063) have NO exports at all.**

### 6b. STALE exports (contain `AUTO-GENERATED SKELETON`)

These 9 cards have real logic in the card body but a skeleton in the export
(one `.xql` and one `.yml` sigma each — 18 stale files). These are the *-001
representative card per plane that was exported as a draft and never regenerated:

| Card | Stale `exports/xql/` | Stale `exports/sigma/` | Card body status |
|---|---|---|---|
| `TTP-2026-0007` | yes (`TTP-2026-0007.xql`) | yes (`TTP-2026-0007.yml`) | real logic present |
| `TTP-2026-0012` | yes (`TTP-2026-0012.xql`) | yes (`TTP-2026-0012.yml`) | real logic present |
| `TTP-2026-0017` | yes (`TTP-2026-0017.xql`) | yes (`TTP-2026-0017.yml`) | real logic present |
| `TTP-2026-0023` | yes (`TTP-2026-0023.xql`) | yes (`TTP-2026-0023.yml`) | real logic present |
| `TTP-2026-0027` | yes (`TTP-2026-0027.xql`) | yes (`TTP-2026-0027.yml`) | real logic present |
| `TTP-2026-0032` | yes (`TTP-2026-0032.xql`) | yes (`TTP-2026-0032.yml`) | real logic present |
| `TTP-2026-0038` | yes (`TTP-2026-0038.xql`) | yes (`TTP-2026-0038.yml`) | real logic present |
| `TTP-2026-0042` | yes (`TTP-2026-0042.xql`) | yes (`TTP-2026-0042.yml`) | real logic present |
| `TTP-2026-0047` | yes (`TTP-2026-0047.xql`) | yes (`TTP-2026-0047.yml`) | real logic present |

> Re-running `export_artifacts.py` was verified to regenerate real XQL for these
> (e.g. TTP-2026-0007 re-render no longer contains `SKELETON`).

### 6c. By-design placeholder (NOT a defect)

`exports/sigma/TTP-2026-0001..0006.yml` flag on a naive `grep skeleton` only
because every Sigma doc emits the intentional scaffold line
`placeholder: replace_with_target_dialect_translation_of_xql` under
`detection.selection`. Their embedded XQL bodies are real. Do not "fix" these.

---

## 7. Per-card detail blocks

Each block summarizes the *kind* of each detection and a one-line of what the
logic matches (not the full body). Detection bodies are truncated for brevity;
read the card JSON for the full XQL.

### TTP-2026-0001 — Help-Desk MFA Reset via Voice Impersonation (Muddled Libra Pattern)

- **Status:** active · **Severity:** critical · **Sim class:** identity · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** _none (standalone hand-authored card)_
- **MITRE:** `T1078.004` Valid Accounts: Cloud Accounts, `T1656` Impersonation, `T1556.006` Modify Authentication Process: Multi-Factor Authentication
- **Tactics:** Credential Access, Defense Evasion, Initial Access, Persistence, Privilege Escalation
- **Threat actors:** Muddled Libra · **Campaigns:** Retail/hospitality ransomware wave 2024-2025, BPO/MSP-targeted 2025
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar, prisma-access
- **Detection counts:** 2 BIOC · 2 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (2):**
  - *Okta MFA Reset Followed by Factor Enrollment From New Device* — `dataset = saas_okta_raw | filter event_type in ("user.account.reset_factors", "user.mfa.factor.activate", "user.session.start") | join (    `
  - *Help-Desk Admin Modifies Authentication for User Outside Their Span* — `dataset = saas_okta_raw | filter event_type = "user.account.reset_factors" | enrich actor_geo = lookup(actor_user_id, employee_directory.geo`
- **XQL queries (2):**
  - *Confirm reset → enroll → sign-in chain fired in last 30 min* — `dataset = saas_okta_raw | filter _time > to_timestamp(current_time() - 1800) | filter event_type in ("user.account.reset`
  - *Help-desk MFA resets followed by privileged action within 1h (Muddled Libra escalation hunt)* — `dataset = saas_okta_raw | filter event_type = "user.account.reset_factors" | join (dataset = saas_okta_raw | filter even`
- **Correlation rules (1):**
  - *Identity Threat — Help-Desk-Assisted Account Takeover* — `BIOC(Okta MFA Reset Followed by Factor Enrollment From New Device) AND BIOC(Anomalous Sign-In Risk High within 1h)`
- **Analytics modules:** Cortex XSIAM Identity Threat Module — anomalous sign-in; Cortex XSIAM Analytics — impossible travel

### TTP-2026-0002 — LSASS Memory Credential Dump via MiniDumpWriteDump (T1003.001)

- **Status:** active · **Severity:** critical · **Sim class:** endpoint · **Safety:** destructive-with-cleanup · **Destructive:** True
- **Mapped scenario(s):** _none (standalone hand-authored card)_
- **MITRE:** `T1003.001` OS Credential Dumping: LSASS Memory
- **Tactics:** Credential Access
- **Threat actors:** Ignoble Scorpius, Muddled Libra · **Campaigns:** BlackSuit Blitz 2025
- **PANW products:** advanced-wildfire, cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 2 BIOC · 2 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (2):**
  - *LSASS Handle Open With Sensitive Access Rights* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_ACCESS | filter target_process_name = "lsass.exe`
  - *comsvcs.dll MiniDump LOLBin Execution* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "rundl`
- **XQL queries (2):**
  - *Confirm LSASS access from non-allowlisted process* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *Confirm comsvcs.dll MiniDump invocation* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Credential Theft — LSASS Memory Dump* — `BIOC(LSASS Handle Open With Sensitive Access Rights) OR BIOC(comsvcs.dll MiniDump LOLBin Execution)`
- **IOCs (1):** filename=`lsass*.dmp`
- **Analytics modules:** Cortex XDR Credential Theft Protection Module; Cortex XDR Behavioral Threat Protection (BTP) — Credential Theft cluster

### TTP-2026-0003 — AWS IAM Access Key Abuse — Discovery + S3 Object Exfiltration

- **Status:** active · **Severity:** critical · **Sim class:** cloud · **Safety:** destructive-with-cleanup · **Destructive:** False
- **Mapped scenario(s):** _none (standalone hand-authored card)_
- **MITRE:** `T1078.004` Valid Accounts: Cloud Accounts, `T1580` Cloud Infrastructure Discovery, `T1530` Data from Cloud Storage, `T1567.002` Exfiltration Over Web Service: Exfiltration to Cloud Storage
- **Tactics:** Collection, Defense Evasion, Discovery, Exfiltration, Initial Access, Persistence, Privilege Escalation
- **Threat actors:** Opportunistic credential abusers (TeamTNT, Scarlet Eel, automated scanners) · **Campaigns:** Generic IAM exposure exploitation
- **PANW products:** cortex-asm, cortex-cdr, cortex-cloud, cortex-xsiam, cortex-xsoar, prisma-cloud
- **Detection counts:** 3 BIOC · 3 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **BIOCs (3):**
  - *IAM User Discovery Burst From New Source IP* — `dataset = cloud_audit_logs | filter cloud_provider = "AWS" and event_source in ("sts.amazonaws.com", "iam.amazonaws.com", "s3.amazonaws.com"`
  - *S3 Bulk Object Download by IAM User With No Prior Read Pattern* — `dataset = cloud_audit_logs | filter cloud_provider = "AWS" and event_source = "s3.amazonaws.com" and event_name = "GetObject" | filter user_`
  - *Long-Lived IAM User Key Used From Outside Known ASN* — `dataset = cloud_audit_logs | filter cloud_provider = "AWS" and additional_event_data.MFAUsed = "No" | filter user_identity.type = "IAMUser" `
- **XQL queries (3):**
  - *Confirm discovery burst within last 30 min* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" and even`
  - *Confirm S3 GetObject burst on target bucket* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter event_name = "GetObject" and re`
  - *Find IAM users with both discovery burst and large S3 read within 1h* — `dataset = cloud_audit_logs | filter cloud_provider = "AWS" | join (filter event_name in ("ListUsers","ListBuckets")) as `
- **Correlation rules (1):**
  - *Cloud — IAM Key Compromise With Exfiltration* — `BIOC(IAM User Discovery Burst From New Source IP) AND BIOC(S3 Bulk Object Download by IAM User With No Prior Read Patter`
- **Analytics modules:** Cortex Cloud — Identity Threat Detection (ITDR); Cortex Cloud — Anomalous API Volume; Cortex CDR — Data Exfiltration Detection

### TTP-2026-0004 — DCSync — Domain Replication Abuse for Credential Extraction (T1003.006)

- **Status:** active · **Severity:** critical · **Sim class:** identity · **Safety:** destructive-with-cleanup · **Destructive:** True
- **Mapped scenario(s):** `mp-002-kerberoast-lateral-smb.yml`
- **MITRE:** `T1003.006` OS Credential Dumping: DCSync
- **Tactics:** Credential Access
- **Threat actors:** Ignoble Scorpius, Muddled Libra · **Campaigns:** BlackSuit Blitz 2025, Generic post-AD-compromise
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 3 BIOC · 3 XQL · 2 correlation · 0 IOC · 2 analytics modules
- **BIOCs (3):**
  - *DRSUAPI Replication Request From Non-DC Host* — `preset = xdr_data | filter event_type = ENUM.NETWORK | filter event_sub_type = ENUM.NETWORK_RPC | filter rpc_interface_uuid = "e3514235-4b06`
  - *Windows Event 4662 — Replicating Directory Changes by Non-DC Principal* — `dataset = msft_windows_security | filter event_id = 4662 | filter properties contains "1131f6aa-9c07-11d1-f79f-00c04fc2dcd2" | filter subjec`
  - *Mimikatz dcsync Command-Line Pattern* — `preset = xdr_data | filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START | filter action_process_image_command_line cont`
- **XQL queries (3):**
  - *Confirm DRSUAPI replication request seen from non-DC in last 30m* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK and event_sub_`
  - *Confirm 4662 replication event on DC* — `dataset = msft_windows_security | filter _time > to_timestamp(current_time() - 1800) | filter event_id = 4662 and proper`
  - *Accounts granted replication rights in last 30d (latent DCSync risk)* — `dataset = msft_windows_security | filter event_id in (5136, 4670) | filter attribute_ldap_display_name in ("nTSecurityDe`
- **Correlation rules (2):**
  - *Credential Access — DCSync Detected* — `BIOC(DRSUAPI Replication Request From Non-DC Host) OR BIOC(Windows Event 4662 — Replicating Directory Changes by Non-DC `
  - *Credential Access — Chained LSASS + DCSync Within 4h* — `CR-CRED-0001 (LSASS dump) AND CR-CRED-0002 (DCSync) on same actor within 4h — promotes to Critical incident with auto-is`
- **Analytics modules:** Cortex XDR Identity Threat Module — DCSync detection; Cortex XSIAM Analytics — anomalous AD replication

### TTP-2026-0005 — Bulk Data Exfiltration via Rclone to Cloud Storage (T1567.002)

- **Status:** active · **Severity:** critical · **Sim class:** data-exfil · **Safety:** destructive-with-cleanup · **Destructive:** False
- **Mapped scenario(s):** `ndr-005-bulk-https-exfil.yml`
- **MITRE:** `T1567.002` Exfiltration Over Web Service: Exfiltration to Cloud Storage, `T1048.003` Exfiltration Over Alternative Protocol: Exfiltration Over Unencrypted Non-C2 Protocol, `T1074.001` Data Staged: Local Data Staging
- **Tactics:** Collection, Exfiltration
- **Threat actors:** Ignoble Scorpius, Black Basta affiliates, Akira affiliates · **Campaigns:** BlackSuit Blitz 2025
- **PANW products:** advanced-dns-security, advanced-threat-prevention, cortex-cdr, cortex-xdr, cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 3 BIOC · 3 XQL · 1 correlation · 2 IOC · 3 analytics modules
- **BIOCs (3):**
  - *Rclone Command-Line Flag Pattern* — `preset = xdr_data | filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START | filter (action_process_image_command_line con`
  - *Large Sustained Egress From File Server to Non-Corporate Cloud Provider* — `preset = xdr_data | filter event_type = ENUM.NETWORK and event_sub_type = ENUM.NETWORK_FLOW | bin _time span=1m | comp sum(bytes_sent) as bs`
  - *Outbound to Known Rclone-Backend Provider From Non-Sanctioned Host* — `preset = xdr_data | filter event_type = ENUM.NETWORK and event_sub_type = ENUM.NETWORK_DNS | filter dns_query matches_any (".*\\.mega\\.nz$"`
- **XQL queries (3):**
  - *Confirm rclone-pattern process exec in last 30m* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *Confirm large outbound flow during exfil window* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK and event_sub_`
  - *Hosts with both rclone-pattern process and large egress in last 24h* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 86400) | join (filter event_type = ENUM.PROCESS and act`
- **Correlation rules (1):**
  - *Exfiltration — Rclone-Pattern Tool + Large Egress to Non-Corp ASN* — `BIOC(Rclone Command-Line Flag Pattern) AND BIOC(Large Sustained Egress From File Server to Non-Corporate Cloud Provider)`
- **IOCs (2):** filename=`rclone.exe`, domain=`*.mega.nz`
- **Analytics modules:** Cortex XDR Data Exfiltration Detection; Advanced DNS Security — abnormal-destination domain category; NGFW App-ID — rclone signature (where available)

### TTP-2026-0006 — Mass ESXi VM Encryption Orchestrated via Ansible (BlackSuit Pattern)

- **Status:** active · **Severity:** critical · **Sim class:** ransomware-chain · **Safety:** lab-only · **Destructive:** True
- **Mapped scenario(s):** _none (standalone hand-authored card)_
- **MITRE:** `T1486` Data Encrypted for Impact, `T1490` Inhibit System Recovery, `T1021.004` Remote Services: SSH, `T1059.004` Command and Scripting Interpreter: Unix Shell
- **Tactics:** Execution, Impact, Lateral Movement
- **Threat actors:** Ignoble Scorpius, Akira affiliates, Lockbit affiliates (historical) · **Campaigns:** BlackSuit Blitz 2025
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 4 BIOC · 4 XQL · 2 correlation · 1 IOC · 3 analytics modules
- **BIOCs (4):**
  - *ESXi SSH Logon Burst — Single Source → Many Hypervisors* — `dataset = esxi_syslog | filter event_source = "sshd" and event_message contains "Accepted" | bin _time span=5m | comp count_distinct(hostnam`
  - *vim-cmd Mass Power-Off Across ESXi Cluster* — `preset = xdr_data | filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START | filter agent_os_type = ENUM.AGENT_OS_LINUX | `
  - *Mass File Creation Adjacent to VMDK Files* — `preset = xdr_data | filter event_type = ENUM.FILE and event_sub_type = ENUM.FILE_CREATE | filter action_file_path starts_with "/vmfs/volumes`
  - *Ansible-Pattern Process on Jumphost With ESXi Reach* — `preset = xdr_data | filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START | filter action_process_image_name = "ansible-p`
- **XQL queries (4):**
  - *Confirm mass vim-cmd power.off across cluster* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter agent_os_type = ENUM.AGENT_OS_LINUX | fi`
  - *Confirm SSH logon burst at ESXi syslog layer* — `dataset = esxi_syslog | filter _time > to_timestamp(current_time() - 1800) | filter event_source = "sshd" and event_mess`
  - *Confirm marker files dropped on datastores* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and event_sub_typ`
  - *ESXi hosts with SSH from unusual source ASNs in last 7d* — `dataset = esxi_syslog | filter _time > to_timestamp(current_time() - 604800) | filter event_source = "sshd" and event_me`
- **Correlation rules (2):**
  - *Ransomware — ESXi Mass Encryption Sequence* — `BIOC(ESXi SSH Logon Burst — Single Source → Many Hypervisors) AND BIOC(vim-cmd Mass Power-Off Across ESXi Cluster) AND B`
  - *Ransomware — Full Kill Chain (Identity → Exfil → ESXi Encrypt)* — `CR-CRED-0003 (LSASS + DCSync) AND CR-EXFIL-0001 (rclone exfil) AND CR-RANSOM-0001 (ESXi mass encrypt) within 7d on relat`
- **IOCs (1):** filename=`*.tttt`
- **Analytics modules:** Cortex XDR for Linux (ESXi) — destructive activity; Cortex XSIAM Analytics — anomalous SSH fan-out; Cortex XSIAM Cross-Source Correlation — vCenter + ESXi syslog + XDR

### TTP-2026-0007 — AI Access — Source Code Paste to Public ChatGPT

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-aiacc-001-source-code-to-chatgpt.yml`
- **MITRE:** `T1567` Exfiltration Over Web Service, `T1041` Exfiltration Over C2 Channel
- **Tactics:** Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-access-security, cortex-xsiam, ngfw-pa-series
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *AIACC-001 Pre-flight LLM Provider Egress Plugin Registered* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("pyt`
- **XQL queries (4):**
  - *AIACC-001 Source Code Shape POST to OpenAI With Canary Marker* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.openai.com`
  - *AIACC-001 DLP Source Code Structure Regex Match in LLM Prompt* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host in ("api.openai.c`
  - *AIACC-001 NGFW Generative AI App-ID Outbound HTTPS to OpenAI* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.openai.com`
  - *AIACC-001 Repeated Source Paste Burst to OpenAI Single User* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.openai.com`
- **Correlation rules (1):**
  - *Shadow-AI Source Code Disclosure to OpenAI* — `BIOC(AIACC-001 Pre-flight LLM Provider Egress Plugin Registered) OR XQL(AIACC-001 Source Code Shape POST to OpenAI With `
- **Analytics modules:** Cortex AI Access Security — Generative-AI App-ID classification; Cortex AI Access Security — Source-code DLP content inspection

### TTP-2026-0008 — AI Access — AWS Access Key Leaked to Anthropic API

- **Status:** active · **Severity:** critical · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-aiacc-002-aws-key-to-anthropic.yml`
- **MITRE:** `T1552.001` Unsecured Credentials, `T1567` Exfiltration Over Web Service
- **Tactics:** Credential Access, Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-access-security, cortex-xsiam, ngfw-pa-series
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (1):**
  - *AIACC-002 Pre-flight Secret Payload Contains AKIA Canary* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("pyt`
- **XQL queries (3):**
  - *AIACC-002 POST to Anthropic From Non-Sanctioned User Context* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.anthropic.`
  - *AIACC-002 NGFW Generative AI App-ID Outbound HTTPS to Anthropic* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.anthropic.`
  - *AIACC-002 Repeated Secret Egress to Anthropic Block Policy Trigger* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.anthropic.`
- **Correlation rules (1):**
  - *AWS Credential Disclosure to Anthropic LLM* — `BIOC(AIACC-002 Pre-flight Secret Payload Contains AKIA Canary) OR XQL(AIACC-002 POST to Anthropic From Non-Sanctioned Us`
- **IOCs (1):** cloud-account-id=`AKIA[0-9A-Z]{16}`
- **Analytics modules:** Cortex AI Access Security — Secrets DLP regex (AWS access keys); Cortex AI Access Security — Sanctioned-vs-unsanctioned provider policy

### TTP-2026-0009 — AI Access — High-Volume Gemini Prompt Burst

- **Status:** active · **Severity:** medium · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-aiacc-003-high-volume-gemini.yml`
- **MITRE:** `T1567` Exfiltration Over Web Service
- **Tactics:** Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-access-security, cortex-xsiam, ngfw-pa-series
- **Detection counts:** 0 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (5):**
  - *AIACC-003 Pre-flight Gemini Provider Host Resolvable* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "generativelang`
  - *AIACC-003 Gemini Request Rate Exceeds Per User Baseline* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "generativelang`
  - *AIACC-003 NGFW Sustained Generative AI Session Count Single Source* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "generativelang`
  - *AIACC-003 Unsanctioned AI Provider Gemini For User* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "generativelang`
  - *AIACC-003 Post Cooloff Single Gemini Request Below Baseline* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 300) | filter dst_host = "generativelangu`
- **Correlation rules (1):**
  - *Anomalous Gemini Usage Volume Single User* — `XQL(AIACC-003 Gemini Request Rate Exceeds Per User Baseline) OR XQL(AIACC-003 NGFW Sustained Generative AI Session Count`
- **Analytics modules:** Cortex AI Access Security — Per-user provider request-rate baselining; Cortex AI Access Security — Sanctioned-vs-unsanctioned provider classification

### TTP-2026-0010 — AI Access — Jailbreak Prompt Fingerprint

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-aiacc-004-jailbreak-fingerprint.yml`
- **MITRE:** `T1656` Impersonation, `T1567` Exfiltration Over Web Service
- **Tactics:** Exfiltration, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-access-security, cortex-xsiam
- **Detection counts:** 2 BIOC · 2 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (2):**
  - *AIACC-004 Pre-flight Jailbreak Payload Contains DAN Markers* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("pyt`
  - *AIACC-004 Outbound Prompt Requests DLP Circumvention Policy Violation* — `dataset = panw_ngfw_traffic_raw | filter dst_host in ("api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com") | filter h`
- **XQL queries (2):**
  - *AIACC-004 Jailbreak Fingerprint DAN Ignore Previous Instructions* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.openai.com`
  - *AIACC-004 Jailbreak Detection Consistent Across Providers* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host in ("api.openai.c`
- **Correlation rules (1):**
  - *Jailbreak Prompt Attempt Across Providers* — `BIOC(AIACC-004 Pre-flight Jailbreak Payload Contains DAN Markers) OR BIOC(AIACC-004 Outbound Prompt Requests DLP Circumv`
- **Analytics modules:** Cortex AI Access Security — Jailbreak / prompt-content classifier; Cortex AI Access Security — AI-usage policy-violation detection

### TTP-2026-0011 — AI Access — Cross-Provider Rotation (OpenAI → Gemini → Anthropic)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-aiacc-005-cross-provider-rotation.yml`
- **MITRE:** `T1090` Proxy, `T1567` Exfiltration Over Web Service
- **Tactics:** Defense Evasion, Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-access-security, cortex-xsiam, ngfw-pa-series
- **Detection counts:** 0 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (5):**
  - *AIACC-005 First Egress to OpenAI User Active in Provider Matrix* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "api.openai.com`
  - *AIACC-005 Same User Second Provider Gemini Within Rotation Window* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host in ("api.openai.c`
  - *AIACC-005 Three Providers Within Five Minutes Provider Hopping* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host in ("api.openai.c`
  - *AIACC-005 XSIAM Stitch Single User Three Provider Sessions* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host in ("api.openai.c`
  - *AIACC-005 NGFW Generative AI App-ID Across Three FQDNs Shared Run ID* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host in ("api.openai.c`
- **Correlation rules (1):**
  - *Cross-Provider Rotation Policy Evasion Single User* — `XQL(AIACC-005 Same User Second Provider Gemini Within Rotation Window) OR XQL(AIACC-005 Three Providers Within Five Minu`
- **Analytics modules:** Cortex AI Access Security — Cross-provider behaviour stitching; Cortex XSIAM — Identity-based session correlation across data planes

### TTP-2026-0012 — AIRS — Direct Prompt Injection (LLM01)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-airs-001-direct-prompt-injection.yml`
- **MITRE:** `T1656` Impersonation, `T1059` Command and Scripting Interpreter
- **Tactics:** Execution, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-runtime-security, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *AIRS LLM01 System Prompt Or Hidden Context Echoed In Model Response* — `dataset = panw_ngfw_traffic_raw | filter dst_port = 8089 and uri_path contains "/owasp/llm01/chat" | filter response_body contains "AKIA0000`
- **XQL queries (4):**
  - *AIRS LLM01 Preflight Vulnerable Target Healthz Responds* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM01 Direct Prompt Injection Classifier Ignore Previous Instructions* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM01 Base64 Mutated Injection Evades Naive Filter Caught By Classifier* — `dataset = cortexsim_airs_raw | filter _time > to_timestamp(current_time() - 1800) | filter event_action = "airs_probe_at`
  - *AIRS LLM01 Audit Pipeline Ingests Probe Attempt JSONL Schema* — `dataset = cortexsim_airs_raw | filter _time > to_timestamp(current_time() - 1800) | filter event_action in ("airs_probe_`
- **Correlation rules (1):**
  - *AIRS LLM01 Direct Injection Request Plus Response Leak Same Session* — `BIOC(AIRS LLM01 System Prompt Or Hidden Context Echoed In Model Response) OR XQL(AIRS LLM01 Direct Prompt Injection Clas`
- **Analytics modules:** Cortex AI Runtime Security — Prompt injection classifier (LLM01); Cortex AI Runtime Security — Sensitive data / canary leak in model response

### TTP-2026-0013 — AIRS — Indirect Prompt Injection via RAG Document (LLM01 + LLM08)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-airs-002-indirect-rag-poisoning.yml`
- **MITRE:** `T1656` Impersonation, `T1059` Command and Scripting Interpreter
- **Tactics:** Execution, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-runtime-security, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **BIOCs (1):**
  - *AIRS LLM08 RAG Response Contains AKIA Canary From Poisoned Document* — `dataset = panw_ngfw_traffic_raw | filter dst_port = 8089 and uri_path contains "/owasp/llm08/rag/query" | filter response_body contains "AKI`
- **XQL queries (4):**
  - *AIRS LLM08 Preflight RAG Endpoint Responds* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM08 RAG Ingestion Classifier Flags Embedded System Override Block* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM08 Indirect Injection Scorer Fires On Retrieved Context* — `dataset = cortexsim_airs_raw | filter _time > to_timestamp(current_time() - 1800) | filter event_action = "airs_probe_at`
  - *AIRS LLM08 Stitch RAG Upload With Later Retrieval Leak Into One Incident* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
- **Correlation rules (1):**
  - *AIRS LLM08 RAG Poison Upload Plus Retrieval Triggered Leak* — `BIOC(AIRS LLM08 RAG Response Contains AKIA Canary From Poisoned Document) OR XQL(AIRS LLM08 RAG Ingestion Classifier Fla`
- **Analytics modules:** Cortex AI Runtime Security — RAG ingestion content inspection (LLM08); Cortex AI Runtime Security — Indirect prompt-injection detection in retrieved context; Cortex XSIAM — Cross-event RAG poison-to-leak correlation

### TTP-2026-0014 — AIRS — System Prompt Leakage (LLM07)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-airs-003-system-prompt-leak.yml`
- **MITRE:** `T1082` System Information Discovery, `T1656` Impersonation
- **Tactics:** Discovery, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-runtime-security, cortex-xsiam
- **Detection counts:** 2 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (2):**
  - *AIRS LLM07 Model Response Contains Literal System Prompt Role String* — `dataset = panw_ngfw_traffic_raw | filter dst_port = 8089 and uri_path contains "/owasp/llm07/chat" | filter response_body contains "CortexSi`
  - *AIRS LLM07 System Prompt Embedded AKIA Canary Key Leaks Into Response* — `dataset = panw_ngfw_traffic_raw | filter dst_port = 8089 and uri_path contains "/owasp/llm07/chat" | filter response_body contains "AKIA0000`
- **XQL queries (3):**
  - *AIRS LLM07 Preflight Chat Endpoint Accepts Seeded System Prompt* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM07 Prompt Stealer Family Classifier Repeat Words Above* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM07 Aggregation By OWASP Code Shows Vulnerable Outcome* — `dataset = cortexsim_airs_raw | filter _time > to_timestamp(current_time() - 1800) | filter event_action = "airs_probe_at`
- **Correlation rules (1):**
  - *AIRS LLM07 Prompt Stealer Request Plus System Prompt Or Secret Leak* — `BIOC(AIRS LLM07 Model Response Contains Literal System Prompt Role String) OR BIOC(AIRS LLM07 System Prompt Embedded AKI`
- **Analytics modules:** Cortex AI Runtime Security — Prompt-stealer / system-prompt-extraction classifier (LLM07); Cortex AI Runtime Security — Secret / credential leak in model response

### TTP-2026-0015 — AIRS — Excessive Agency / Tool-Call Abuse (LLM06)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-airs-004-tool-call-abuse.yml`
- **MITRE:** `T1059` Command and Scripting Interpreter, `T1656` Impersonation
- **Tactics:** Execution, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-runtime-security, cortex-xsiam
- **Detection counts:** 2 BIOC · 3 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **BIOCs (2):**
  - *AIRS LLM06 Exec Shell Tool Invoked From Non Developer Context Prompt* — `dataset = panw_ngfw_traffic_raw | filter dst_port = 8089 and uri_path contains "/owasp/llm06/agent" | filter response_body contains "exec_sh`
  - *AIRS LLM06 Send Email Tool Argument Contains External Exfil Recipient* — `dataset = panw_ngfw_traffic_raw | filter dst_port = 8089 and uri_path contains "/owasp/llm06/agent" | filter response_body contains "send_em`
- **XQL queries (3):**
  - *AIRS LLM06 Preflight Agent Endpoint Advertises Three Tool Functions* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM06 Tool Call Argument Carries Shell Metacharacters Unsafe Input* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM06 Stitch Agentic Tool Call With Downstream EDR Process Event* — `dataset = cortexsim_airs_raw | filter _time > to_timestamp(current_time() - 1800) | filter event_action = "airs_probe_at`
- **Correlation rules (1):**
  - *AIRS LLM06 Unsafe Tool Call Exec Shell Or External Email Exfil* — `BIOC(AIRS LLM06 Exec Shell Tool Invoked From Non Developer Context Prompt) OR BIOC(AIRS LLM06 Send Email Tool Argument C`
- **Analytics modules:** Cortex AI Runtime Security — Unsafe tool-call / excessive-agency detection (LLM06); Cortex AI Runtime Security — Tool-argument metacharacter / destructive-command inspection; Cortex XSIAM — Agentic tool-call to endpoint process correlation

### TTP-2026-0016 — AIRS — Unbounded Consumption / Token-Exhaustion DoS (LLM10)

- **Status:** active · **Severity:** medium · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-airs-005-token-exhaustion-dos.yml`
- **MITRE:** `T1499` Endpoint Denial of Service, `T1499.003` Application Exhaustion Flood
- **Tactics:** Impact
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-runtime-security, cortex-xsiam
- **Detection counts:** 0 BIOC · 5 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **XQL queries (5):**
  - *AIRS LLM10 Preflight Endpoint Accepts Unbounded Max Tokens* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM10 Token Usage Rolling Sum Exceeds Per User Budget* — `dataset = cortexsim_airs_raw | filter _time > to_timestamp(current_time() - 1800) | filter event_action = "airs_probe_at`
  - *AIRS LLM10 P95 Latency On Chat Endpoint Exceeds Threshold Under Load* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM10 NGFW Sustained POST Burst Flagged Anomalous App Traffic* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
  - *AIRS LLM10 Circuit Breaker Rejects Oversized Request With 429* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 8089 and uri_pa`
- **Correlation rules (1):**
  - *AIRS LLM10 Token Budget Breach Plus Latency Anomaly Plus POST Burst* — `XQL(AIRS LLM10 Token Usage Rolling Sum Exceeds Per User Budget) OR XQL(AIRS LLM10 P95 Latency On Chat Endpoint Exceeds T`
- **Analytics modules:** Cortex AI Runtime Security — Per-user token-consumption budget / denial-of-wallet detection (LLM10); Cortex AI Runtime Security — Endpoint latency-under-load anomaly; Cortex Network Security Analytics — Sustained POST-burst anomalous-app-traffic detection

### TTP-2026-0017 — Prisma Browser — Credential Paste into Untrusted Origin (T1552)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-browser-001-credential-paste.yml`
- **MITRE:** `T1552` Unsecured Credentials, `T1056.003` Input Capture: Web Portal Capture
- **Tactics:** Collection, Credential Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *Prisma Browser — credential-shape paste into non-sanctioned origin (DLP rule)* — `dataset = prisma_browser_events | filter action = "paste" | filter data_classification in ("credential", "secret", "password") | filter orig`
- **XQL queries (3):**
  - *Pre-flight: plugin metadata visible to operator* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter campaign_id = "BC-BROWSER-`
  - *Prisma Browser — paste source vs. destination origin mismatch* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "paste" | filter `
  - *PB → XSIAM — alert with origin URL + paste DLP rule fires within MTTD target* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "paste" and corte`
- **Correlation rules (1):**
  - *Browser Credential Paste DLP — Non-Sanctioned Origin* — `BIOC(Prisma Browser — credential-shape paste into non-sanctioned origin (DLP rule))`
- **Analytics modules:** Prisma Browser DLP — Credential pattern inspection at paste time; Cortex XSIAM Analytics — Sanctioned-origin allowlist deviation

### TTP-2026-0018 — Prisma Browser — Drive-by Download from Phishing Page (T1189)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-browser-002-drive-by-download.yml`
- **MITRE:** `T1189` Drive-by Compromise, `T1566.002` Phishing, `T1105` Ingress Tool Transfer
- **Tactics:** Command and Control, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** advanced-wildfire, cortex-xdr, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **BIOCs (1):**
  - *Prisma Browser — auto-download initiated by JS without user gesture* — `dataset = prisma_browser_events | filter action = "download" | filter user_gesture = false | filter file_name != null | filter origin not in`
- **XQL queries (4):**
  - *Pre-flight: plugin metadata visible to operator* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter campaign_id = "BC-BROWSER-`
  - *Prisma Browser — downloaded artifact MIME application/x-msdownload from non-SaaS origin* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "download" | filt`
  - *NGFW EAL — file-type download policy matches downloaded artifact* — `dataset = panw_ngfw_threat_raw | filter _time > to_timestamp(current_time() - 1800) | filter log_subtype = "file" or sub`
  - *PB → WildFire submission within download-policy MTTD* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "download" and wi`
- **Correlation rules (1):**
  - *Browser Drive-by Download — Gesture-less Executable* — `BIOC(Prisma Browser — auto-download initiated by JS without user gesture)`
- **Analytics modules:** Prisma Browser — Unsolicited download heuristics; Cortex XSIAM Analytics — Executable MIME from non-SaaS origin; Advanced WildFire — Downloaded artifact verdict

### TTP-2026-0019 — Prisma Browser — Sideloaded Risky Extension Install (T1176)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-browser-003-risky-extension-install.yml`
- **MITRE:** `T1176` Browser Extensions, `T1539` Steal Web Session Cookie
- **Tactics:** Credential Access, Persistence
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *Prisma Browser — sideloaded extension blocked by managed-extension policy* — `dataset = prisma_browser_events | filter action = "extension-install" | filter install_source in ("sideload", "crx", "developer-mode") | fil`
- **XQL queries (4):**
  - *Code scan — extension manifest declares <all_urls> + cookies + webRequest + webRequestBlocking* — `dataset = koi_code_scan_events | filter _time > to_timestamp(current_time() - 1800) | filter artifact_type = "browser-ex`
  - *Prisma Browser — extension permission risk score exceeds threshold* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "extension-instal`
  - *KOI — extension manifest signature does not match approved publisher list* — `dataset = koi_code_scan_events | filter _time > to_timestamp(current_time() - 1800) | filter artifact_type = "browser-ex`
  - *Browser-attacker JSONL records install_extension outcome=blocked* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter campaign_id = "BC-BROWSER-`
- **Correlation rules (1):**
  - *Browser Risky Extension Sideload — Overbroad Permissions* — `BIOC(Prisma Browser — sideloaded extension blocked by managed-extension policy)`
- **Analytics modules:** Prisma Browser — Extension permission risk scoring; Cortex KOI — Browser-extension manifest + publisher-signature analysis

### TTP-2026-0020 — Prisma Browser — Cross-Origin SaaS Copy-Paste DLP (T1567)

- **Status:** active · **Severity:** high · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-browser-004-saas-cross-origin-dlp.yml`
- **MITRE:** `T1567` Exfiltration Over Web Service, `T1005` Data from Local System
- **Tactics:** Collection, Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *Prisma Browser — cross-origin paste from sanctioned CRM into personal webmail (DLP rule)* — `dataset = prisma_browser_events | filter action = "paste" | filter clipboard_origin in ("crm.cortexsim-test.invalid", "crm.corp.example.com"`
- **XQL queries (4):**
  - *Pre-flight: plugin metadata visible to operator* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter campaign_id = "BC-BROWSER-`
  - *Prisma Browser — clipboard origin tag mismatch with paste destination origin* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "paste" | filter `
  - *XSIAM stitching — copy + paste correlated by clipboard payload hash* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action in ("copy", "paste"`
  - *XSIAM — stitched cross-origin DLP incident with both origins on the timeline* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action in ("copy", "paste"`
- **Correlation rules (1):**
  - *Browser Cross-Origin Clipboard DLP — CRM to Webmail* — `BIOC(Prisma Browser — cross-origin paste from sanctioned CRM into personal webmail (DLP rule))`
- **Analytics modules:** Prisma Browser — Clipboard origin tagging + DLP; Cortex XSIAM Analytics — Clipboard payload-hash copy/paste stitching

### TTP-2026-0021 — Prisma Browser — Screen Capture of Sensitive SaaS Page (T1113)

- **Status:** active · **Severity:** medium · **Sim class:** web · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-browser-005-screen-capture.yml`
- **MITRE:** `T1113` Screen Capture
- **Tactics:** Collection
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *Prisma Browser — page.screenshot() called against origin tagged sensitive — block + alert* — `dataset = prisma_browser_events | filter action = "screen-capture" | filter data_classification in ("sensitive", "confidential", "restricted`
- **XQL queries (4):**
  - *Pre-flight: plugin metadata visible to operator* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter campaign_id = "BC-BROWSER-`
  - *Prisma Browser — screen-capture-attempt counter increments under sensitive-content policy* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "screen-capture" `
  - *Prisma Browser — repeat screen-capture from same user counts toward elevated risk score* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "screen-capture" `
  - *XSIAM — user risk score elevated, conditional-access policy triggered downstream* — `dataset = prisma_browser_events | filter _time > to_timestamp(current_time() - 1800) | filter action = "screen-capture" `
- **Correlation rules (1):**
  - *Browser Sensitive Screen Capture — Repeat Collection* — `BIOC(Prisma Browser — page.screenshot() called against origin tagged sensitive — block + alert)`
- **Analytics modules:** Prisma Browser — Screen-capture policy on sensitive origins; Cortex XSIAM Analytics — User risk scoring + conditional-access trigger

### TTP-2026-0022 — Container Enumeration via DEEPCE and LinPEAS (T1613)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `cdr-001-container-enum.yml`
- **MITRE:** `T1613` Container and Resource Discovery, `T1082` System Information Discovery, `T1552.001` Unsecured Credentials: Credentials In Files, `T1059.004` Command and Scripting Interpreter: Unix Shell
- **Tactics:** Credential Access, Discovery, Execution
- **Threat actors:** TeamTNT · **Campaigns:** Hildegard
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 9 BIOC · 3 XQL · 1 correlation · 2 IOC · 2 analytics modules
- **BIOCs (9):**
  - *CDR-001 Package Manager Execution Inside Running Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-001 DEEPCE Container Enumeration Script Execution* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-001 Curl Pipe Bash Download Execute In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-001 Container Capability Mask Read From Proc1 Status* — `preset = xdr_data | filter event_type = ENUM.FILE | filter container_id != null | filter action_file_path in ("/proc/1/status", "/proc/self/`
  - *CDR-001 Container Device Enumeration With Capability Inspection* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-001 Service Account Token Access From Container* — `preset = xdr_data | filter event_type = ENUM.FILE | filter container_id != null | filter action_file_path contains "serviceaccount/token" | `
  - *CDR-001 Broad Filesystem Credential Search In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-001 LinPEAS Privilege Escalation Tool In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-001 Automated Discovery Command Burst In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
- **XQL queries (3):**
  - *CDR-001 Validate DEEPCE Or LinPEAS Execution In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *CDR-001 Validate Service Account Token Read In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE | filter containe`
  - *CDR-001 Validate Capability And Device Recon In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Container Discovery Recon Chain — Same Container* — `BIOC(CDR-001 DEEPCE Container Enumeration Script Execution) OR BIOC(CDR-001 Curl Pipe Bash Download Execute In Container`
- **IOCs (2):** filename=`deepce.sh`, filename=`linpeas.sh`
- **Analytics modules:** Cortex Cloud Runtime Defense — Container reconnaissance / enumeration tooling; Cortex XDR Analytics — Container process performing host capability and credential discovery

### TTP-2026-0023 — Cryptominer Deployment — XMRig Resource Hijacking in Containers (T1496)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `cdr-002-cryptominer.yml`
- **MITRE:** `T1496` Resource Hijacking, `T1105` Ingress Tool Transfer, `T1562.001` Impair Defenses: Disable or Modify Tools, `T1053.005` Scheduled Task/Job: Cron
- **Tactics:** Command and Control, Defense Evasion, Execution, Impact, Persistence
- **Threat actors:** — · **Campaigns:** Large-Scale Monero Mining Operation
- **PANW products:** advanced-dns-security, cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 8 BIOC · 3 XQL · 1 correlation · 2 IOC · 2 analytics modules
- **BIOCs (8):**
  - *CDR-002 Container Resource Limit Tuning Pre-Mining* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-002 XMRig Binary Download And Extraction In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-002 Compressed Binary Extracted To Hidden Tmp In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-002 Known Mining Pool In Config File In Container* — `preset = xdr_data | filter event_type = ENUM.FILE | filter container_id != null | filter action_file_extension = "json" | filter action_file`
  - *CDR-002 XMRig Process Execution In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-002 Sustained High CPU Mining Workload In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter container_id != null | filter actor_process_image_name in ("xmrig", "kdevtmpfs`
  - *CDR-002 Crontab Modification To Persist Cryptominer In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-002 Cryptominer Added To Cron Schedule In Container* — `preset = xdr_data | filter event_type = ENUM.FILE | filter container_id != null | filter action_file_path contains_any ("/var/spool/cron", "`
- **XQL queries (3):**
  - *CDR-002 Validate XMRig Execution In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *CDR-002 Validate Mining Pool Config Or Download In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter container_id != null | filter action_pro`
  - *CDR-002 Validate Cron Persistence Of Miner In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Cryptominer Deploy Execute Persist — Same Container* — `BIOC(CDR-002 XMRig Binary Download And Extraction In Container) OR BIOC(CDR-002 Known Mining Pool In Config File In Cont`
- **IOCs (2):** domain=`pool.minexmr.com`, filename=`xmrig`
- **Analytics modules:** Cortex Cloud Runtime Defense — Cryptominer process and stratum pool detection; Cortex XDR Analytics — Sustained high-CPU container process with mining pool egress

### TTP-2026-0024 — Container Escape via Privileged Mode and nsenter (T1611)

- **Status:** active · **Severity:** critical · **Sim class:** cloud · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `cdr-003-container-escape.yml`
- **MITRE:** `T1611` Escape to Host, `T1610` Deploy Container, `T1083` File and Directory Discovery, `T1003.008` OS Credential Dumping: /etc/passwd and /etc/shadow, `T1053.003` Scheduled Task/Job: Cron
- **Tactics:** Credential Access, Defense Evasion, Discovery, Execution, Persistence, Privilege Escalation
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 10 BIOC · 3 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (10):**
  - *CDR-003 Container Capability Read With Block Device Access* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-003 Privileged Container Full Capability Set Anomaly* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-003 Host Filesystem Access Via Proc1 Root From Container* — `preset = xdr_data | filter event_type = ENUM.FILE | filter container_id != null | filter action_file_path contains "/proc/1/root/" | filter `
  - *CDR-003 Proc1 Root Traversal Host Namespace Enumeration* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-003 nsenter Into PID1 Mount Namespace Container Escape* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-003 Anomalous Namespace Switch From Container Process* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-003 Host Passwd Read From Container Via Namespace Escape* — `preset = xdr_data | filter event_type = ENUM.FILE | filter container_id != null | filter action_file_path in ("/etc/passwd", "/etc/shadow") `
  - *CDR-003 Sensitive Host File Read Following Container Escape* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-003 Host Cron Persistence Written From Escaped Container* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type = ENUM.FILE_CREATE | filter container_id != null | filter action_f`
  - *CDR-003 Host CronD File Creation After Namespace Escape* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
- **XQL queries (3):**
  - *CDR-003 Validate nsenter PID1 Escape From Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *CDR-003 Validate Host Credential Read After Escape* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter container_id != null | filter action_pro`
  - *CDR-003 Validate Host CronD Persistence From Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter container_id != null | filter action_pro`
- **Correlation rules (1):**
  - *Container Escape To Host Kill Chain — Same Container* — `BIOC(CDR-003 Container Capability Read With Block Device Access) OR BIOC(CDR-003 Host Filesystem Access Via Proc1 Root F`
- **IOCs (1):** filepath=`/etc/cron.d/cortexsim-sim`
- **Analytics modules:** Cortex Cloud Runtime Defense — Container escape / host namespace access; Cortex XDR Analytics — Container process performing nsenter into host PID 1 namespace

### TTP-2026-0025 — Kubernetes Lateral Movement via Stolen Service-Account Token (T1021)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `cdr-004-k8s-lateral.yml`
- **MITRE:** `T1021.001` Remote Services, `T1552.001` Unsecured Credentials: Credentials In Files, `T1613` Container and Resource Discovery, `T1053.005` Scheduled Task/Job: Cron
- **Tactics:** Credential Access, Discovery, Lateral Movement, Persistence
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 10 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (10):**
  - *CDR-004 Pod API Server Enumeration With Service Account Token* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-004 Service Account Token Used For API Discovery Audit* — `dataset = cloud_audit_logs | filter cloud_provider = "kubernetes" or log_source = "k8s_audit" | filter verb in ("list", "get") | filter obje`
  - *CDR-004 Service Account Token Read And Copied In Pod* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-004 Service Account Token Staged To Tmp In Pod* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type = ENUM.FILE_CREATE | filter container_id != null | filter action_f`
  - *CDR-004 kubectl All Namespaces Enumeration From Pod* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-004 Cluster Wide Pod Discovery Audit Anomaly* — `dataset = cloud_audit_logs | filter cloud_provider = "kubernetes" or log_source = "k8s_audit" | filter verb = "list" | filter objectRef_reso`
  - *CDR-004 kubectl Exec Into Adjacent Pod Lateral Movement* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-004 Pod Exec Subresource Create Audit Anomaly* — `dataset = cloud_audit_logs | filter cloud_provider = "kubernetes" or log_source = "k8s_audit" | filter verb = "create" | filter objectRef_re`
  - *CDR-004 Malicious CronJob Creation Via kubectl From Pod* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-004 CronJob Resource Create Audit Anomaly* — `dataset = cloud_audit_logs | filter cloud_provider = "kubernetes" or log_source = "k8s_audit" | filter verb = "create" | filter objectRef_re`
- **XQL queries (3):**
  - *CDR-004 Validate kubectl Exec From Pod In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *CDR-004 Validate Pod Exec Or CronJob Create In K8s Audit* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter verb = "create" | filter (objec`
  - *CDR-004 Validate Service Account Token Theft In Pod* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter container_id != null | filter action_pro`
- **Correlation rules (1):**
  - *Kubernetes Lateral Movement Chain — Runtime And Audit* — `BIOC(CDR-004 Service Account Token Read And Copied In Pod) OR BIOC(CDR-004 kubectl All Namespaces Enumeration From Pod) `
- **Analytics modules:** Cortex Cloud — Kubernetes audit anomaly (service account performing cross-namespace verbs); Cortex XDR Analytics — In-pod kubectl exec / token theft behavioral cluster

### TTP-2026-0026 — WildFire Malware Trigger — Dropped Backdoor, C2 Beacon, Ransomware Sim (T1105)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `cdr-005-wildfire-trigger.yml`
- **MITRE:** `T1105` Ingress Tool Transfer, `T1059.004` Command and Scripting Interpreter: Unix Shell, `T1071.001` Application Layer Protocol: Web Protocols, `T1486` Data Encrypted for Impact
- **Tactics:** Command and Control, Execution, Impact
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** advanced-wildfire, cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 8 BIOC · 3 XQL · 1 correlation · 2 IOC · 3 analytics modules
- **BIOCs (8):**
  - *CDR-005 WildFire Known Bad ELF Download In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-005 Executable Downloaded To Tmp And Made Executable In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-005 WildFire ELF Execution In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-005 Downloaded Binary Executed By Service Account In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-005 Periodic Outbound HTTP Beacon From Container* — `preset = xdr_data | filter event_type = ENUM.NETWORK | filter container_id != null | filter dst_action_external_hostname = "192.0.2.1" or ac`
  - *CDR-005 Post Execution C2 Network Activity In Container* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter container_id != null | filter act`
  - *CDR-005 Mass File Modification Ransomware Pattern In Container* — `preset = xdr_data | filter event_type = ENUM.FILE | filter container_id != null | filter event_sub_type in (ENUM.FILE_CREATE, ENUM.FILE_REMO`
  - *CDR-005 Bulk Enc Extension Rename After Execution In Container* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type = ENUM.FILE_CREATE | filter container_id != null | filter action_f`
- **XQL queries (3):**
  - *CDR-005 Validate Dropped ELF Execution In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *CDR-005 Validate C2 Beacon To Test Net Endpoint* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter conta`
  - *CDR-005 Validate Bulk Enc Rename In Container* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and event_sub_typ`
- **Correlation rules (1):**
  - *WildFire Malware Kill Chain — Download Execute Beacon Encrypt* — `BIOC(CDR-005 WildFire Known Bad ELF Download In Container) OR BIOC(CDR-005 WildFire ELF Execution In Container) OR BIOC(`
- **IOCs (2):** ipv4=`192.0.2.1`, user-agent=`Mozilla/5.0 (compatible; MSIE 9.0)`
- **Analytics modules:** Advanced WildFire — Malicious file verdict on the dropped ELF; Cortex Cloud Runtime Defense — Dropped binary execution and post-execution C2 beacon; Cortex XDR Analytics — Download-execute-beacon-encrypt behavioral cluster in container

### TTP-2026-0027 — Cloud App — Okta Risky OAuth Drive-Scope Grant (T1550.001)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-cloud-001-okta-risky-drive-grant.yml`
- **MITRE:** `T1550.001` Use Alternate Authentication Material: Application Access Token, `T1528` Steal Application Access Token
- **Tactics:** Credential Access, Defense Evasion, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *CLOUD-001 Pre-Flight oauth_grant_emulator Plugin Registered* — `dataset = saas_audit_logs | filter event_type = "eal_plugin_describe" or action contains "oauth_grant_emulator" | filter app_name = "oauth_g`
- **XQL queries (3):**
  - *CLOUD-001 Okta Risky Drive-Wide OAuth Scope Grant* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter consent_type = "user" | filter oauth_sc`
  - *CLOUD-001 NGFW Okta OAuth Authorize Risky-Scope Egress* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter app_name = "okta-oauth" or dst_h`
  - *CLOUD-001 Okta Repeated Risky Grant Enumeration Burst* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter consent_type = "user" | filter oauth_sc`
- **Correlation rules (1):**
  - *Okta Risky OAuth Drive-Scope Consent Phishing — Same Identity* — `XQL(CLOUD-001 Okta Risky Drive-Wide OAuth Scope Grant) OR XQL(CLOUD-001 NGFW Okta OAuth Authorize Risky-Scope Egress) OR`
- **Analytics modules:** Cortex Cloud App Security — Third-party app requests drive-wide OAuth scope; Cortex XSIAM Analytics — Repeated risky OAuth consent attempts from one identity

### TTP-2026-0028 — Cloud App — Microsoft Admin-Consent-Required Scope Request (T1078.004)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-cloud-002-microsoft-admin-consent.yml`
- **MITRE:** `T1078.004` Valid Accounts: Cloud Accounts, `T1098.003` Account Manipulation: Additional Cloud Roles, `T1528` Steal Application Access Token
- **Tactics:** Credential Access, Initial Access, Persistence
- **Threat actors:** Volt Typhoon · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 2 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (2):**
  - *CLOUD-002 Pre-Flight Microsoft Provider Supported* — `dataset = saas_audit_logs | filter event_type = "eal_plugin_describe" or action contains "oauth_grant_emulator" | filter app_name = "oauth_g`
  - *CLOUD-002 Microsoft Application ReadWrite All In Same Grant Body* — `dataset = msft_o365_audit | filter oauth_scopes contains "Application.ReadWrite.All" | filter consent_type != "admin" | filter client_id != `
- **XQL queries (3):**
  - *CLOUD-002 Microsoft Directory ReadWrite All Admin-Consent Scope Request* — `dataset = msft_o365_audit | filter _time > to_timestamp(current_time() - 1800) | filter consent_type != "admin" | filter`
  - *CLOUD-002 NGFW Microsoft v2 Authorize Admin-Consent Egress* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "login.microsoftonlin`
  - *CLOUD-002 Microsoft Repeat Admin-Consent Persistence Attempt* — `dataset = msft_o365_audit | filter _time > to_timestamp(current_time() - 1800) | filter consent_type != "admin" | filter`
- **Correlation rules (1):**
  - *Microsoft Admin-Consent OAuth Abuse Cloud Persistence — Same Identity* — `XQL(CLOUD-002 Microsoft Directory ReadWrite All Admin-Consent Scope Request) OR BIOC(CLOUD-002 Microsoft Application Rea`
- **Analytics modules:** Cortex Cloud App Security — Non-admin identity requests admin-consent-required Graph scope; Cortex XSIAM Analytics — Repeated admin-consent grant attempts indicate persistence intent

### TTP-2026-0029 — Cloud App — Google Full-Mailbox + Offline Token Replay Risk (T1114.002)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-cloud-003-google-mailbox-takeover.yml`
- **MITRE:** `T1114.002` Email Collection: Remote Email Collection, `T1528` Steal Application Access Token, `T1550.001` Use Alternate Authentication Material: Application Access Token
- **Tactics:** Collection, Credential Access, Defense Evasion
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **BIOCs (1):**
  - *CLOUD-003 Pre-Flight oauth_grant_emulator Plugin Registered* — `dataset = saas_audit_logs | filter event_type = "eal_plugin_describe" or action contains "oauth_grant_emulator" | filter app_name = "oauth_g`
- **XQL queries (4):**
  - *CLOUD-003 Google Full Mailbox Scope Grant Attempt* — `dataset = google_workspace_audit | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains_any`
  - *CLOUD-003 NGFW Google OAuth Authorize Mailbox-Scope Egress* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "accounts.google.com"`
  - *CLOUD-003 Microsoft Mail ReadWrite Offline Access Token-Replay Grant* — `dataset = msft_o365_audit | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains "Mail.Read`
  - *CLOUD-003 Cross-Provider Mailbox Collection Same Identity Stitch* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains_any ("mail`
- **Correlation rules (1):**
  - *Cross-Provider OAuth Mailbox Takeover Token Replay — Same Identity* — `XQL(CLOUD-003 Google Full Mailbox Scope Grant Attempt) OR XQL(CLOUD-003 Microsoft Mail ReadWrite Offline Access Token-Re`
- **Analytics modules:** Cortex Cloud App Security — Third-party app requests full-mailbox scope; Cortex Cloud App Security — offline_access refresh-token grant flagged as token-replay risk; Cortex XSIAM Analytics — Same identity solicits mailbox access at two providers

### TTP-2026-0030 — Cloud App — Cross-Provider OAuth Grant Rotation (Okta → MS → Google) (T1090)

- **Status:** active · **Severity:** high · **Sim class:** cloud · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-cloud-004-cross-provider-grant-rotation.yml`
- **MITRE:** `T1090` Proxy, `T1550.001` Use Alternate Authentication Material: Application Access Token, `T1528` Steal Application Access Token
- **Tactics:** Command and Control, Credential Access, Defense Evasion
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (5):**
  - *CLOUD-004 Okta First Risky Grant Identity Active In IdP Matrix* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains_any ("drive", "Fi`
  - *CLOUD-004 Microsoft Admin-Consent Grant Second IdP In Rotation Window* — `dataset = msft_o365_audit | filter _time > to_timestamp(current_time() - 1800) | filter consent_type != "admin" | filter`
  - *CLOUD-004 Three IdPs Same Identity Provider-Hopping Policy Evasion* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains_any ("driv`
  - *CLOUD-004 XSIAM Cross-Provider Rotation Single Incident Stitch* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter simulation_run_id != null | alte`
  - *CLOUD-004 NGFW Three Authorize Hits Across Three FQDNs Shared Run Marker* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter uri_path contains "/authorize" o`
- **Correlation rules (1):**
  - *Cross-Provider OAuth Grant Rotation Policy Evasion — Single Identity* — `XQL(CLOUD-004 Three IdPs Same Identity Provider-Hopping Policy Evasion) OR XQL(CLOUD-004 XSIAM Cross-Provider Rotation S`
- **Analytics modules:** Cortex Cloud App Security — Single identity solicits risky grants across multiple IdPs; Cortex XSIAM Analytics — Cross-provider OAuth rotation stitched by shared run marker

### TTP-2026-0031 — Cloud App — Benign OAuth Baseline FP-Suppression Control (T1078.004)

- **Status:** active · **Severity:** informational · **Sim class:** cloud · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-cloud-005-benign-baseline-control.yml`
- **MITRE:** `T1078.004` Valid Accounts: Cloud Accounts
- **Tactics:** Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (4):**
  - *CLOUD-005 Benign Okta OIDC Grant Informational Not Risky* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains "openid" | filter`
  - *CLOUD-005 Benign Microsoft User Read Grant Informational Not Risky* — `dataset = msft_o365_audit | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains_any ("User`
  - *CLOUD-005 Benign Google OIDC Grant Informational Not Risky* — `dataset = google_workspace_audit | filter _time > to_timestamp(current_time() - 1800) | filter oauth_scopes contains "op`
  - *CLOUD-005 NGFW Benign Authorize Hits Informational Not Promoted* — `dataset = saas_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter uri_path contains "/authorize" o`
- **Correlation rules (1):**
  - *Benign OAuth Baseline Suppression Invariant — No Risky Correlation* — `NOT (XQL(CLOUD-005 Benign Okta OIDC Grant Informational Not Risky) OR XQL(CLOUD-005 Benign Microsoft User Read Grant Inf`
- **Analytics modules:** Cortex Cloud App Security — Benign OIDC sign-in scopes logged informationally (suppression baseline); Cortex XSIAM Analytics — FP-suppression control: risky-grant correlations must not match benign events

### TTP-2026-0032 — Linux Credential Dumping — /etc/shadow and Mimipenguin (T1003.008)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `edr-001-credential-dumping.yml`
- **MITRE:** `T1003.008` OS Credential Dumping: /etc/passwd and /etc/shadow, `T1552.001` Unsecured Credentials: Credentials In Files, `T1087.001` Account Discovery: Local Account, `T1003` OS Credential Dumping
- **Tactics:** Credential Access, Discovery
- **Threat actors:** Rocke · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 4 BIOC · 5 XQL · 1 correlation · 3 IOC · 2 analytics modules
- **BIOCs (4):**
  - *EDR-001 Shadow File Read by Non-Root Service Account* — `preset = xdr_data | filter event_type = ENUM.FILE | filter action_file_path = "/etc/shadow" | filter actor_effective_username not in ("root"`
  - *EDR-001 Broad Credential File Sweep Across Home Directories* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "find"`
  - *EDR-001 Process Memory Scrape via proc environ* — `preset = xdr_data | filter event_type = ENUM.FILE | filter action_file_path ~= "/proc/[0-9]+/(environ|maps|mem)" | filter actor_process_imag`
  - *EDR-001 Mimipenguin Credential Dumper Execution* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter action_process_image_command_line`
- **XQL queries (5):**
  - *EDR-001 Analytics Non-Root Passwd Enumeration* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and action_file_p`
  - *EDR-001 Analytics Unauthorized Shadow Access Pattern* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and action_file_p`
  - *EDR-001 Analytics Recursive Credential File Search* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-001 Analytics Sequential Proc Environ Access* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and action_file_p`
  - *EDR-001 Analytics Curl To Bash Credential Tool* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Linux Credential Harvesting Chain — passwd/shadow + key sweep + Mimipenguin* — `BIOC(EDR-001 Shadow File Read by Non-Root Service Account) OR BIOC(EDR-001 Broad Credential File Sweep Across Home Direc`
- **IOCs (3):** url=`https://raw.githubusercontent.com/huntergregal/mimipenguin/master/mimipenguin.sh`, filename=`mimipenguin.sh`, file-sha256=`9b6e1f8c0d4a3b2e5f7a1c9d8e0b4f6a2c3d5e7f8a9b0c1d2e3f4a5b6c7d8e9f`
- **Analytics modules:** Cortex XDR Analytics — Unusual sensitive file access by service account; Cortex XDR Analytics BIOC — Credential access tool execution cluster

### TTP-2026-0033 — Linux Reverse Shell — Multi-Method C2 Callback (T1059.004)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `edr-002-reverse-shell.yml`
- **MITRE:** `T1059.004` Command and Scripting Interpreter: Unix Shell, `T1059.006` Command and Scripting Interpreter: Python, `T1071.001` Application Layer Protocol: Web Protocols, `T1573.002` Encrypted Channel: Asymmetric Cryptography
- **Tactics:** Command and Control, Execution
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam
- **Detection counts:** 6 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (6):**
  - *EDR-002 Bash devtcp Reverse Shell Redirection* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("bas`
  - *EDR-002 Interactive Bash From Service Account With Network Redirect* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("bas`
  - *EDR-002 Python Socket Subprocess Reverse Shell* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("pyt`
  - *EDR-002 Netcat Shell Exec Flag Reverse Shell* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("nc"`
  - *EDR-002 Perl Socket Reverse Shell One-Liner* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "perl"`
  - *EDR-002 OpenSSL s_client Encrypted C2 From Service Account* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "opens`
- **XQL queries (4):**
  - *EDR-002 Analytics Interactive Shell Network Redirect* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-002 Analytics Python Socket Ops By Service Account* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-002 Analytics Network Utility Shell Flag* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-002 Analytics Multi-Method Reverse Shell Sequence* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Reverse Shell Multi-Method C2 — Same Lineage* — `BIOC(EDR-002 Bash devtcp Reverse Shell Redirection) OR BIOC(EDR-002 Interactive Bash From Service Account With Network R`
- **Analytics modules:** Cortex XDR Analytics — Service account spawning interpreter with outbound socket; Cortex XDR Behavioral Threat Protection — Reverse shell cluster

### TTP-2026-0034 — Linux Persistence — Cron, Systemd, Backdoor User and SSH Keys (T1053.003)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** destructive-with-cleanup · **Destructive:** True
- **Mapped scenario(s):** `edr-003-persistence-mechanisms.yml`
- **MITRE:** `T1053.003` Scheduled Task/Job: Cron, `T1543.002` Create or Modify System Process: Systemd Service, `T1136.001` Create Account: Local Account, `T1098.004` Account Manipulation: SSH Authorized Keys, `T1546.004` Event Triggered Execution: Unix Shell Configuration Modification
- **Tactics:** Persistence
- **Threat actors:** TeamTNT · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 5 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (5):**
  - *EDR-003 Cron Job Creation With Suspicious Command Path* — `preset = xdr_data | filter (event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START and actor_process_image_name = "crontab") or (`
  - *EDR-003 Systemd Unit With Suspicious ExecStart* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type in (ENUM.FILE_CREATE, ENUM.FILE_WRITE) | filter action_file_path c`
  - *EDR-003 Local Account Created With Sudo Group* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("use`
  - *EDR-003 SSH Key Generation Then Authorized Keys Write* — `preset = xdr_data | filter (event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START and actor_process_image_name = "ssh-keygen") o`
  - *EDR-003 Shell Profile Modified With Background Execution* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type in (ENUM.FILE_CREATE, ENUM.FILE_WRITE) | filter action_file_path c`
- **XQL queries (5):**
  - *EDR-003 Analytics Crontab Modification Non-Interactive* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-003 Analytics Service Unit Restart Always Hidden Path* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and action_file_p`
  - *EDR-003 Analytics User Account Creation Non-Login Session* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-003 Analytics ssh-keygen Non-Interactive Persistence* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-003 Analytics Shell Init File Modification* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and event_sub_typ`
- **Correlation rules (1):**
  - *Linux Multi-Mechanism Persistence Installation* — `BIOC(EDR-003 Cron Job Creation With Suspicious Command Path) OR BIOC(EDR-003 Systemd Unit With Suspicious ExecStart) OR `
- **Analytics modules:** Cortex XDR Analytics — New persistence mechanism on host; Cortex XDR Analytics — Rare local account creation

### TTP-2026-0035 — Linux Defense Evasion — Log Tampering, Timestomping, Masquerading (T1070.002)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** destructive-with-cleanup · **Destructive:** False
- **Mapped scenario(s):** `edr-004-defense-evasion.yml`
- **MITRE:** `T1070.002` Indicator Removal: Clear Linux or Mac System Logs, `T1070.003` Indicator Removal: Clear Command History, `T1070.006` Indicator Removal: Timestomp, `T1036.005` Masquerading: Match Legitimate Name or Location, `T1562.001` Impair Defenses: Disable or Modify Tools
- **Tactics:** Defense Evasion
- **Threat actors:** xHunt · **Campaigns:** xHunt Campaign
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 5 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (5):**
  - *EDR-004 Bash History Clearing And HISTFILE Manipulation* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter action_process_image_command_line`
  - *EDR-004 System Log Access Or Truncation* — `preset = xdr_data | filter event_type = ENUM.FILE | filter action_file_path contains_any ("/var/log/auth.log", "/var/log/syslog", "/var/log/`
  - *EDR-004 File Timestomp Via Touch* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "touch`
  - *EDR-004 Binary Masquerade As Kernel Thread* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("cp"`
  - *EDR-004 Security Product Enumeration* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("gre`
- **XQL queries (5):**
  - *EDR-004 Analytics History Evasion Sequence* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-004 Analytics Auth Log Access Non-Syslog Process* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and action_file_p`
  - *EDR-004 Analytics Timestomp Reference File* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-004 Analytics Shell Binary Copied To System Name* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-004 Analytics Security Tool Discovery Via Process Listing* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Linux Anti-Forensics And Defense Evasion Chain* — `BIOC(EDR-004 Bash History Clearing And HISTFILE Manipulation) OR BIOC(EDR-004 System Log Access Or Truncation) OR BIOC(E`
- **Analytics modules:** Cortex XDR Analytics — Anti-forensics activity on host; Cortex XDR Analytics — Security tooling reconnaissance

### TTP-2026-0036 — Linux Lateral Movement — SSH Abuse, Tunneling and Internal Recon (T1021.004)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `edr-005-lateral-movement.yml`
- **MITRE:** `T1021.004` Remote Services: SSH, `T1046` Network Service Discovery, `T1572` Protocol Tunneling, `T1018` Remote System Discovery, `T1016` System Network Configuration Discovery
- **Tactics:** Command and Control, Discovery, Lateral Movement
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 3 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (3):**
  - *EDR-005 Port Scan Via Bash devtcp* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("bas`
  - *EDR-005 Batch Mode SSH From Service Account* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "ssh" `
  - *EDR-005 SSH Tunnel Creation With Forward Flags* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "ssh" `
- **XQL queries (5):**
  - *EDR-005 Analytics Network Interface And Routing Enumeration* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-005 Analytics Sequential Port Connection Attempts* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-005 Analytics Batch SSH StrictHostKey Disabled* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-005 Analytics SSH Port Forwarding From Service Account* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *EDR-005 Analytics DNS And Host File Enumeration* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Linux Lateral Movement Chain — Recon, Scan, SSH, Tunnel* — `BIOC(EDR-005 Port Scan Via Bash devtcp) OR BIOC(EDR-005 Batch Mode SSH From Service Account) OR BIOC(EDR-005 SSH Tunnel `
- **Analytics modules:** Cortex XDR Analytics — Service account performing internal network discovery; Cortex XDR Analytics — Automated SSH lateral movement

### TTP-2026-0037 — ITDR — Impossible Travel (Okta + Entra sign-ins from US-West and APAC-East)

- **Status:** active · **Severity:** high · **Sim class:** identity · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-itdr-001-impossible-travel.yml`
- **MITRE:** `T1078.004` Valid Accounts: Cloud Accounts, `T1539` Steal Web Session Cookie
- **Tactics:** Credential Access, Defense Evasion, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xsiam
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *ITDR-001 Pre-Flight idp_signin_emulator Metadata Visible* — `dataset = okta_sso | filter eventType in ("system.api_token.create", "application.lifecycle.activate") or _raw_log contains "idp_signin_emul`
- **XQL queries (3):**
  - *ITDR-001 Impossible Travel Two Distant Geos Within Window* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter eventType = "user.session.start" and au`
  - *ITDR-001 NGFW Outbound POST Burst To Identity Collector* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("web-browsing", "s`
  - *ITDR-001 Impossible Travel Microsoft SignInLogs Shape* — `dataset = msft_azure_ad_signin | filter _time > to_timestamp(current_time() - 1800) | filter auth_outcome = "SUCCESS" | `
- **Correlation rules (1):**
  - *Impossible Travel Account Compromise — Cross-Provider Identity* — `XQL(ITDR-001 Impossible Travel Two Distant Geos Within Window) OR XQL(ITDR-001 Impossible Travel Microsoft SignInLogs Sh`
- **Analytics modules:** Cortex ITDR Analytics — Impossible travel between successful authentications; Cortex XSIAM Identity Analytics — Geo-velocity anomaly per principal

### TTP-2026-0038 — ITDR — MFA Fatigue / Push-Bombing (Okta)

- **Status:** active · **Severity:** high · **Sim class:** identity · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-itdr-002-mfa-fatigue.yml`
- **MITRE:** `T1621` Multi-Factor Authentication Request Generation, `T1556.006` Modify Authentication Process: Multi-Factor Authentication
- **Tactics:** Credential Access, Defense Evasion
- **Threat actors:** APT29, Lapsus$ · **Campaigns:** —
- **PANW products:** cortex-xsiam
- **Detection counts:** 2 BIOC · 2 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (2):**
  - *ITDR-002 Pre-Flight mfa_fatigue Pattern Advertised* — `dataset = okta_sso | filter eventType contains "user.authentication.auth_via_mfa" or _raw_log contains "mfa_fatigue" | comp count() as prefl`
  - *ITDR-002 MFA Fatigue Marker Boolean Explicit Match* — `dataset = okta_sso | filter eventType contains "user.authentication.auth_via_mfa" | filter fatigue_marker = true or _raw_log contains "\"fat`
- **XQL queries (2):**
  - *ITDR-002 MFA Fatigue Push Burst Then Approval* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter eventType contains "user.authentication`
  - *ITDR-002 MFA Fatigue Severity Escalates Above 16 Events* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter eventType contains "user.authentication`
- **Correlation rules (1):**
  - *MFA Push-Bombing Incident — Burst Then Approve* — `BIOC(ITDR-002 MFA Fatigue Marker Boolean Explicit Match) OR XQL(ITDR-002 MFA Fatigue Push Burst Then Approval)`
- **Analytics modules:** Cortex ITDR Analytics — MFA challenge burst per principal; Cortex XSIAM Identity Analytics — Push-bombing fatigue detection

### TTP-2026-0039 — ITDR — Credential Stuffing (failed-login burst across many users)

- **Status:** active · **Severity:** high · **Sim class:** identity · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-itdr-003-credential-stuffing.yml`
- **MITRE:** `T1110.004` Brute Force: Credential Stuffing
- **Tactics:** Credential Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xsiam
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *ITDR-003 Pre-Flight idp_signin_emulator Metadata Visible* — `dataset = okta_sso | filter eventType = "user.session.start" or _raw_log contains "credential_stuffing" | comp count() as preflight_events b`
- **XQL queries (3):**
  - *ITDR-003 Credential Stuffing Many Users One Source IP* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter eventType = "user.session.start" and au`
  - *ITDR-003 NGFW Sustained POST Burst To Identity Collector* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("web-browsing", "s`
  - *ITDR-003 Credential Stuffing Severity Escalates 50 Event Burst* — `dataset = msft_azure_ad_signin | filter _time > to_timestamp(current_time() - 1800) | filter auth_outcome = "FAILURE" | `
- **Correlation rules (1):**
  - *Credential Stuffing Identity Attack — Single Origin Spray* — `XQL(ITDR-003 Credential Stuffing Many Users One Source IP) OR XQL(ITDR-003 Credential Stuffing Severity Escalates 50 Eve`
- **Analytics modules:** Cortex ITDR Analytics — Distinct-user failure spray per source IP; Cortex XSIAM Identity Analytics — Credential-stuffing origin detection

### TTP-2026-0040 — ITDR — Session Token Replay Across Geo / User-Agent

- **Status:** active · **Severity:** high · **Sim class:** identity · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-itdr-004-token-replay.yml`
- **MITRE:** `T1539` Steal Web Session Cookie, `T1550.004` Use Alternate Authentication Material: Web Session Cookie, `T1078.004` Valid Accounts: Cloud Accounts
- **Tactics:** Credential Access, Defense Evasion, Initial Access, Lateral Movement
- **Threat actors:** APT29 · **Campaigns:** —
- **PANW products:** cortex-xsiam
- **Detection counts:** 2 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (2):**
  - *ITDR-004 Pre-Flight idp_signin_emulator Metadata Visible* — `dataset = okta_sso | filter eventType = "user.session.start" or _raw_log contains "token_replay" | comp count() as preflight_events by actor`
  - *ITDR-004 Replay Marker Boolean Explicit Match* — `dataset = okta_sso | filter eventType = "user.session.start" | filter replay_marker = true or _raw_log contains "\"replay_marker\": true" | `
- **XQL queries (3):**
  - *ITDR-004 Session Token Reused Across Geo And User-Agent* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter eventType = "user.session.start" | comp`
  - *ITDR-004 Three Token-Replay Alerts Stitch Into One Incident* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter eventType = "user.session.start" and (r`
  - *ITDR-004 XSIAM Token-Replay Incidents Grouped By Identity* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter eventType = "user.session.start" and (r`
- **Correlation rules (1):**
  - *Session Token Replay Hijack — Same Identity* — `BIOC(ITDR-004 Replay Marker Boolean Explicit Match) OR XQL(ITDR-004 Session Token Reused Across Geo And User-Agent)`
- **Analytics modules:** Cortex ITDR Analytics — Session token reused across geo/user-agent; Cortex XSIAM Identity Analytics — Token-replay incident grouping per principal

### TTP-2026-0041 — ITDR — Brute-Force Causing Account Lockout (Microsoft)

- **Status:** active · **Severity:** high · **Sim class:** identity · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-itdr-005-brute-force-lockout.yml`
- **MITRE:** `T1110.003` Brute Force: Password Spraying, `T1110.001` Brute Force: Password Guessing
- **Tactics:** Credential Access
- **Threat actors:** Midnight Blizzard (APT29) · **Campaigns:** —
- **PANW products:** cortex-xsiam
- **Detection counts:** 2 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (2):**
  - *ITDR-005 Pre-Flight idp_signin_emulator Metadata Visible* — `dataset = msft_azure_ad_signin | filter auth_outcome in ("FAILURE", "SUCCESS") or _raw_log contains "brute_force_lockout" | comp count() as `
  - *ITDR-005 Account Lock State Transition Recorded* — `dataset = msft_azure_ad_signin | filter result_type = "user.account.lock" or auth_status_detail contains "locked" or _raw_log contains "user`
- **XQL queries (3):**
  - *ITDR-005 Ten Consecutive Failed Sign-Ins Brute Force* — `dataset = msft_azure_ad_signin | filter _time > to_timestamp(current_time() - 1800) | filter auth_outcome = "FAILURE" | `
  - *ITDR-005 Second Lockout Within One Hour Escalates Severity* — `dataset = msft_azure_ad_signin | filter _time > to_timestamp(current_time() - 3600) | filter result_type = "user.account`
  - *ITDR-005 XSIAM Repeat-Lockout Grouped Password Spray* — `dataset = msft_azure_ad_signin | filter _time > to_timestamp(current_time() - 3600) | filter result_type = "user.account`
- **Correlation rules (1):**
  - *Brute-Force Lockout Password Spray — Same Identity* — `XQL(ITDR-005 Ten Consecutive Failed Sign-Ins Brute Force) OR BIOC(ITDR-005 Account Lock State Transition Recorded)`
- **Analytics modules:** Cortex ITDR Analytics — Consecutive failure burst and lockout per principal; Cortex XSIAM Identity Analytics — Repeat-lockout password-spray grouping

### TTP-2026-0042 — Typosquat MCP Server Installed by Claude Desktop (T1195)

- **Status:** active · **Severity:** high · **Sim class:** supply-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-koi-001-typosquat-mcp-server.yml`
- **MITRE:** `T1195` Supply Chain Compromise, `T1059` Command and Scripting Interpreter, `T1059.006` Command and Scripting Interpreter: Python
- **Tactics:** Execution, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 2 BIOC · 4 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (2):**
  - *KOI-001 MCP Tool Description Embeds Instruction Injection and AKIA Canary* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type in (ENUM.FILE_OPEN, ENUM.FILE_READ) | filter action_file_name = "m`
  - *KOI-001 Cortex Cloud Code Instruction Injection In Tool Description* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("gre`
- **XQL queries (4):**
  - *KOI-001 Typosquat MCP Manifest Name Near-Miss of Anthropic Publisher* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *KOI-001 Claude Desktop Fetches MCP Manifest From Non Canonical FQDN* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter dst_a`
  - *KOI-001 Outbound URL Matches Typosquat Of Approved AI Publisher Domain* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter actio`
  - *KOI-001 Cortex Cloud Code Typosquat Package Name Detector* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Agentic Supply Chain — Typosquat MCP Server Installed* — `BIOC(KOI-001 MCP Tool Description Embeds Instruction Injection and AKIA Canary) OR BIOC(KOI-001 Cortex Cloud Code Instru`
- **IOCs (1):** domain=`cortexsim-canary.invalid`
- **Analytics modules:** Cortex Cloud Code — supply-chain typosquat + secret/injection scanner; Cortex XDR Analytics — agentic endpoint outbound fetch to non-canonical AI-tool FQDN

### TTP-2026-0043 — Hidden Prompt Injection in MCP Tool Response (T1656 / T1059)

- **Status:** active · **Severity:** high · **Sim class:** supply-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-koi-002-mcp-tool-response-injection.yml`
- **MITRE:** `T1656` Impersonation, `T1059` Command and Scripting Interpreter, `T1552.001` Unsecured Credentials: Credentials In Files
- **Tactics:** Credential Access, Execution, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-runtime-security, cortex-cloud, cortex-xsiam
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 1 IOC · 3 analytics modules
- **BIOCs (1):**
  - *KOI-002 MCP Tool Response Embeds Instruction Injection And Credential Exfil String* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("gre`
- **XQL queries (4):**
  - *KOI-002 JSON RPC Tools Call Response Body Contains Injection Markers* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter dst_a`
  - *KOI-002 MCP Runtime Monitor Instruction Shaped String In Tool Result* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *KOI-002 AIRS Indirect Injection Scorer On MCP Result In Prompt* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter actio`
  - *KOI-002 XSIAM Stitch MCP Injection Artifact With AIRS Detection* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter action_process_image_command_line contai`
- **Correlation rules (1):**
  - *Agentic Supply Chain — MCP Tool-Response Indirect Injection* — `BIOC(KOI-002 MCP Tool Response Embeds Instruction Injection And Credential Exfil String)`
- **IOCs (1):** domain=`cortexsim-canary.invalid`
- **Analytics modules:** Cortex Cloud Code — MCP server source injection/secret scanner; Cortex AIRS — indirect prompt-injection scorer on tool output; Cortex XSIAM Analytics — agentic-supply-chain stitching (KOI artifact + AIRS)

### TTP-2026-0044 — Backdoored PyPI Package With Import-Time Subprocess (T1195.002)

- **Status:** active · **Severity:** high · **Sim class:** supply-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-koi-003-backdoored-pypi-package.yml`
- **MITRE:** `T1195.002` Supply Chain Compromise: Compromise Software Supply Chain, `T1059.006` Command and Scripting Interpreter: Python, `T1552.001` Unsecured Credentials: Credentials In Files
- **Tactics:** Credential Access, Execution, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 2 BIOC · 4 XQL · 1 correlation · 2 IOC · 2 analytics modules
- **BIOCs (2):**
  - *KOI-003 PyPI Package Import Time Subprocess Spawn Post Install Hook* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter causality_actor_process_image_nam`
  - *KOI-003 Cortex Cloud SCA SBOM Digest Matches Known Bad Fingerprint* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("pip`
- **XQL queries (4):**
  - *KOI-003 Backdoored PyPI Package Name Typosquats Canonical Helper* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *KOI-003 Outbound HTTPS To Non Canonical PyPI Mirror With Pip User Agent* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter dst_a`
  - *KOI-003 PyPI Mirror Domain Matches Typosquat Of PyPI Org* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter dst_a`
  - *KOI-003 Cortex Cloud SCA Package Fails Publisher And Post Install Policy* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
- **Correlation rules (1):**
  - *Agentic Supply Chain — Backdoored PyPI Package Post-Install Hook* — `BIOC(KOI-003 PyPI Package Import Time Subprocess Spawn Post Install Hook) OR BIOC(KOI-003 Cortex Cloud SCA SBOM Digest M`
- **IOCs (2):** domain=`cortexsim-canary.invalid`, filename=`mcp-server-helpers`
- **Analytics modules:** Cortex Cloud SCA — typosquat + post-install hook + SBOM fingerprint scanner; Cortex XDR Analytics — python interpreter spawning child subprocess at package import

### TTP-2026-0045 — Malicious VS Code Extension Reads Credentials On Activation (T1176 / T1195)

- **Status:** active · **Severity:** high · **Sim class:** supply-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-koi-004-vscode-extension-permission-escalation.yml`
- **MITRE:** `T1176` Software Extensions, `T1195` Supply Chain Compromise, `T1552.001` Unsecured Credentials: Credentials In Files
- **Tactics:** Credential Access, Initial Access, Persistence
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam
- **Detection counts:** 2 BIOC · 4 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (2):**
  - *KOI-004 VSCode Extension Reads AWS Credentials And SSH Keys In Activate* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type in (ENUM.FILE_OPEN, ENUM.FILE_READ) | filter action_file_path cont`
  - *KOI-004 VSCode Extension Activate Beacons Outbound To Non Microsoft Domain* — `preset = xdr_data | filter event_type = ENUM.NETWORK | filter actor_process_image_name in ("code", "node", "Code", "code-server") or causali`
- **XQL queries (4):**
  - *KOI-004 VSCode Extension Manifest Declares Wildcard Activation Events* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *KOI-004 VSCode Marketplace VSIX Fetch From Non Canonical CDN* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter dst_a`
  - *KOI-004 Extension Marketplace Risk Unverified Publisher Overbroad Permissions* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *KOI-004 VSCode Child Process Emits Post Install Beacon Traffic* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter causa`
- **Correlation rules (1):**
  - *Agentic Supply Chain — Malicious IDE Extension Credential Theft* — `BIOC(KOI-004 VSCode Extension Reads AWS Credentials And SSH Keys In Activate) OR BIOC(KOI-004 VSCode Extension Activate `
- **IOCs (1):** domain=`cortexsim-canary.invalid`
- **Analytics modules:** Cortex Cloud Code — IDE-extension manifest + source scanner (wildcard activation, credential reads, beacon); Cortex XDR Analytics — editor child process reading credential files then beaconing outbound

### TTP-2026-0046 — Malicious Claude Skill With Hidden Instructions In skill.md (T1656 / T1195)

- **Status:** active · **Severity:** high · **Sim class:** supply-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `sim-koi-005-claude-skill-hidden-instructions.yml`
- **MITRE:** `T1656` Impersonation, `T1195` Supply Chain Compromise, `T1552.001` Unsecured Credentials: Credentials In Files
- **Tactics:** Credential Access, Initial Access
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** ai-runtime-security, cortex-cloud, cortex-xsiam
- **Detection counts:** 1 BIOC · 5 XQL · 1 correlation · 1 IOC · 3 analytics modules
- **BIOCs (1):**
  - *KOI-005 Claude Skill Body Contains Instruction Injection And Exfil Directives* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("gre`
- **XQL queries (5):**
  - *KOI-005 Claude Skill Claims Verified Publisher With Mismatched Signature* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *KOI-005 Claude Client Fetches Skill Bundle From Non Anthropic CDN* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter dst_a`
  - *KOI-005 Skill Fetch Destination Not In Anthropic Registry Allowlist* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter actio`
  - *KOI-005 AIRS Indirect Injection Scorer On Skill Body In Prompt* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.NETWORK | filter actio`
  - *KOI-005 XSIAM Stitch Skill Injection Artifact With AIRS Detection* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter action_process_image_command_line contai`
- **Correlation rules (1):**
  - *Agentic Supply Chain — Malicious Claude Skill Hidden Injection* — `BIOC(KOI-005 Claude Skill Body Contains Instruction Injection And Exfil Directives)`
- **IOCs (1):** domain=`cortexsim-canary.invalid`
- **Analytics modules:** Cortex Cloud Code — skill.md injection + manifest signature-mismatch scanner; Cortex AIRS — indirect prompt-injection scorer on skill body; Cortex XSIAM Analytics — agentic-supply-chain stitching (KOI artifact + AIRS)

### TTP-2026-0047 — C2 Beacon Callback — NGFW + XDR Causality Stitch (T1071.001)

- **Status:** active · **Severity:** high · **Sim class:** ransomware-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `mp-001-c2-beacon-ngfw-xdr-stitch.yml`
- **MITRE:** `T1071.001` Application Layer Protocol: Web Protocols, `T1059.004` Command and Scripting Interpreter: Unix Shell, `T1572` Protocol Tunneling, `T1105` Ingress Tool Transfer
- **Tactics:** Command and Control, Execution
- **Threat actors:** Multiple (Cobalt Strike operators) · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 3 BIOC · 6 XQL · 3 correlation · 1 IOC · 3 analytics modules
- **BIOCs (3):**
  - *MP-001 Interactive Shell Spawned From www-data Service Context* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("bas`
  - *MP-001 Curl Beacon With Suspicious User-Agent From www-data* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("cur`
  - *MP-001 Curl Wrote Second-Stage File To Tmp From Service Account* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type in (ENUM.FILE_CREATE_NEW, ENUM.FILE_WRITE) | filter actor_process_`
- **XQL queries (6):**
  - *MP-001 NGFW Repetitive HTTP Beacon To testmynids* — `dataset = pan_ngfw_traffic | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "testmynids.org" and`
  - *MP-001 Stitch NGFW Session And XDR Process By Src Host* — `dataset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter (event_type = ENUM.NETWORK and action_e`
  - *MP-001 NGFW DNS TXT High Entropy Burst* — `dataset = pan_dns | filter _time > to_timestamp(current_time() - 1800) | filter dns_query_type = "TXT" and dns_query_nam`
  - *MP-001 Repetitive Dig TXT From Service Account* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *MP-001 Stitch DNS Exfil With Process Lineage* — `dataset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter (event_type = ENUM.PROCESS and actor_pr`
  - *MP-001 NGFW Download From IOC Listed Domain* — `dataset = pan_ngfw_traffic | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "testmynids.org" and`
- **Correlation rules (3):**
  - *C2 Beacon Cross-Plane Stitch — NGFW Session + XDR Process* — `(XQL(MP-001 NGFW Repetitive HTTP Beacon To testmynids) AND (BIOC(MP-001 Curl Beacon With Suspicious User-Agent From www-`
  - *DNS Tunnel Exfil Cross-Plane Stitch — NGFW DNS Anomaly + XDR Dig Loop* — `(XQL(MP-001 NGFW DNS TXT High Entropy Burst) AND XQL(MP-001 Repetitive Dig TXT From Service Account)) -> join on src_hos`
  - *Ingress Transfer IOC Stitch — NGFW Download + XDR Tmp Write + TIM IOC* — `(XQL(MP-001 NGFW Download From IOC Listed Domain) AND BIOC(MP-001 Curl Wrote Second-Stage File To Tmp From Service Accou`
- **IOCs (1):** domain=`testmynids.org`
- **Analytics modules:** Cortex XSIAM Network Security Analytics — periodic beacon detection; Cortex XSIAM Analytics — DNS tunneling / high-entropy label anomaly; Cortex XSIAM Correlation Engine — cross-dataset causality stitching by src_host

### TTP-2026-0048 — Staged Data Exfiltration via DNS Tunnel — XDR Staging + NGFW Stitch (T1048.003)

- **Status:** active · **Severity:** high · **Sim class:** ransomware-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `mp-003-data-staged-exfil-dns-tunnel.yml`
- **MITRE:** `T1048.003` Exfiltration Over Alternative Protocol: Over Unencrypted Non-C2 Protocol, `T1074.001` Data Staged: Local Data Staging, `T1572` Protocol Tunneling, `T1005` Data from Local System
- **Tactics:** Collection, Command and Control, Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 4 BIOC · 5 XQL · 3 correlation · 1 IOC · 3 analytics modules
- **BIOCs (4):**
  - *MP-003 Passwd Read Then Write To Tmp Staging Dir* — `preset = xdr_data | filter event_type = ENUM.FILE | filter event_sub_type in (ENUM.FILE_OPEN, ENUM.FILE_READ) | filter action_file_path in (`
  - *MP-003 Tar Gzip Compression Of Tmp Staging By Service Account* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("tar`
  - *MP-003 Dig TXT Tight Loop From Service Account* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("dig`
  - *MP-003 Curl Form Upload Of Staged Data By Service Account* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("cur`
- **XQL queries (5):**
  - *MP-003 Sensitive File Path Access From Service Account* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.FILE and event_sub_typ`
  - *MP-003 NGFW DNS TXT Tunneling Burst High Entropy Labels* — `dataset = pan_dns | filter _time > to_timestamp(current_time() - 1800) | filter dns_query_type = "TXT" and dns_query_nam`
  - *MP-003 NGFW DNS Query Rate Spike From Single Source* — `dataset = pan_dns | filter _time > to_timestamp(current_time() - 1800) | filter dns_query_name contains "testmynids.org"`
  - *MP-003 Stitch Tmp Stage Write With DNS Tunnel Burst* — `dataset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter (event_type = ENUM.PROCESS and actor_pr`
  - *MP-003 NGFW HTTP Post File Upload To IOC Domain* — `dataset = pan_ngfw_traffic | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "testmynids.org" and`
- **Correlation rules (3):**
  - *Staged DNS Exfil Cross-Plane Stitch — XDR Staging + NGFW DNS Anomaly* — `(BIOC(MP-003 Tar Gzip Compression Of Tmp Staging By Service Account) AND BIOC(MP-003 Dig TXT Tight Loop From Service Acc`
  - *DNS Rate Spike Plus Sensitive Read Stitch* — `(BIOC(MP-003 Passwd Read Then Write To Tmp Staging Dir) AND XQL(MP-003 NGFW DNS Query Rate Spike From Single Source)) ->`
  - *Redundant HTTPS Exfil IOC Stitch — Curl Upload + NGFW POST + IOC* — `(BIOC(MP-003 Curl Form Upload Of Staged Data By Service Account) AND XQL(MP-003 NGFW HTTP Post File Upload To IOC Domain`
- **IOCs (1):** domain=`testmynids.org`
- **Analytics modules:** Cortex XSIAM Analytics — DNS tunneling / high-entropy TXT label detection; Cortex XSIAM Analytics — DNS query-rate spike per source; Cortex XSIAM Correlation Engine — staging-to-egress causality stitch by hostname

### TTP-2026-0049 — APT29 Hybrid Cloud Credential Theft → Cloud Pivot → S3 Exfil Stitch (T1552.001)

- **Status:** active · **Severity:** critical · **Sim class:** ransomware-chain · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `mp-004-apt29-cloud-cred-theft.yml`
- **MITRE:** `T1552.001` Unsecured Credentials: Credentials In Files, `T1059.004` Command and Scripting Interpreter: Unix Shell, `T1078.004` Valid Accounts: Cloud Accounts, `T1580` Cloud Infrastructure Discovery, `T1530` Data from Cloud Storage, `T1537` Transfer Data to Cloud Account
- **Tactics:** Collection, Credential Access, Defense Evasion, Discovery, Execution, Exfiltration, Initial Access, Privilege Escalation
- **Threat actors:** APT29 · **Campaigns:** Midnight Blizzard cloud intrusion set
- **PANW products:** cortex-cloud, cortex-xdr, cortex-xsiam, cortex-xsoar
- **Detection counts:** 5 BIOC · 6 XQL · 3 correlation · 1 IOC · 3 analytics modules
- **BIOCs (5):**
  - *MP-004 Recursive AWS Key Pattern Grep From Service Account* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("gre`
  - *MP-004 AWS CLI Invoked By www-data With Inline Access Key Env* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name = "aws" `
  - *MP-004 Cloud Multi Service Describe List Burst From Single Principal* — `dataset = cloud_audit | filter cloud_provider = "AWS" | filter event_name in ("DescribeInstances", "ListUsers", "ListBuckets", "ListAccessKe`
  - *MP-004 GetBucketAcl And ListObjects On Sensitive Tagged Bucket* — `dataset = cloud_audit | filter cloud_provider = "AWS" and event_source = "s3.amazonaws.com" | filter event_name in ("GetBucketAcl", "ListObj`
  - *MP-004 S3 CopyObject To Non Owned Destination Bucket* — `dataset = cloud_audit | filter cloud_provider = "AWS" and event_source = "s3.amazonaws.com" | filter event_name in ("CopyObject", "PutObject`
- **XQL queries (6):**
  - *MP-004 Service Account Traversing Home And Root Dirs* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *MP-004 CloudTrail GetCallerIdentity From Unenrolled Source IP* — `dataset = cloud_audit | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" and event_sou`
  - *MP-004 Stitch Endpoint Process With Cloud Audit By Principal* — `dataset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter (event_type = ENUM.PROCESS and actor_pr`
  - *MP-004 UEBA First Time Broad Cloud Discovery Pattern* — `dataset = cloud_audit | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" | filter even`
  - *MP-004 Cloud Data Egress Volume Anomaly From Tagged Source* — `dataset = cloud_audit | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" and event_sou`
  - *MP-004 Stitch Full Kill Chain Cred Dump To Cloud Exfil* — `dataset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter (event_type = ENUM.PROCESS and actor_pr`
- **Correlation rules (3):**
  - *Hybrid Cloud Cred Pivot Stitch — XDR AWS CLI + CloudTrail Principal* — `(BIOC(MP-004 AWS CLI Invoked By www-data With Inline Access Key Env) AND XQL(MP-004 CloudTrail GetCallerIdentity From Un`
  - *Cloud Discovery To Collection Stitch — Multi Service Burst + Sensitive Bucket* — `(BIOC(MP-004 Cloud Multi Service Describe List Burst From Single Principal) AND BIOC(MP-004 GetBucketAcl And ListObjects`
  - *APT29 Full Kill Chain Stitch — Cred Dump To Cloud Exfil* — `(BIOC(MP-004 Recursive AWS Key Pattern Grep From Service Account) OR BIOC(MP-004 AWS CLI Invoked By www-data With Inline`
- **IOCs (1):** user-agent=`cortexsim-attacker-drop`
- **Analytics modules:** Cortex Cloud CDR — CloudTrail anomaly analytics (unenrolled-IP API use); Cortex Cloud CDR — UEBA principal-behavior deviation; Cortex XSIAM Correlation Engine — hybrid endpoint↔cloud_audit stitch by AWS principal

### TTP-2026-0050 — Periodic HTTP C2 Beaconing — NGFW EAL Network Detection (T1071.001)

- **Status:** active · **Severity:** high · **Sim class:** network · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `ndr-001-c2-beacon-eal-validation.yml`
- **MITRE:** `T1071.001` Application Layer Protocol: Web Protocols, `T1568` Dynamic Resolution
- **Tactics:** Command and Control
- **Threat actors:** — · **Campaigns:** Cobalt Strike Malleable C2
- **PANW products:** cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 1 BIOC · 4 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **BIOCs (1):**
  - *NGFW EAL — outbound HTTP carrying X-Simulation-Run-ID header (operator filter)* — `dataset = panw_ngfw_traffic_raw | filter app = "web-browsing" or app = "http" | filter http_req_headers contains "X-Simulation-Run-ID" | fil`
- **XQL queries (4):**
  - *Pre-flight: simulator manifest visible to operator* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("web-browsing", "h`
  - *Dry-run: no NGFW EAL fires (safety policy active)* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("web-browsing", "h`
  - *NGFW EAL — periodic HTTP beacon (regular interval, anomalous User-Agent)* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("web-browsing", "h`
  - *NGFW EAL — DGA-style URI parameter on outbound HTTP* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("web-browsing", "h`
- **Correlation rules (1):**
  - *C2 HTTP Beacon — Periodic Outbound With Anomalous Client* — `BIOC(NGFW EAL — outbound HTTP carrying X-Simulation-Run-ID header (operator filter)) OR (XQL(NGFW EAL — periodic HTTP be`
- **Analytics modules:** Cortex XSIAM Network Security Analytics — Periodic Beaconing / C2 over HTTP; Cortex XSIAM Analytics — Rare User-Agent for destination; Cortex XSIAM Analytics — DGA-style domain/URI scoring

### TTP-2026-0051 — DNS Tunneling Exfiltration — High-Entropy Label Burst (T1048.003 / T1572)

- **Status:** active · **Severity:** high · **Sim class:** network · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `ndr-002-dns-tunnel-eal.yml`
- **MITRE:** `T1048.003` Exfiltration Over Unencrypted Non-C2 Protocol, `T1572` Protocol Tunneling
- **Tactics:** Command and Control, Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 1 BIOC · 5 XQL · 1 correlation · 0 IOC · 3 analytics modules
- **BIOCs (1):**
  - *NGFW EAL — long FQDN exceeding host baseline* — `dataset = panw_ngfw_traffic_raw | filter app = "dns" or app = "dns-base" | filter dst_host = "testmynids.org" or dns_query_name contains ".t`
- **XQL queries (5):**
  - *Pre-flight: dns_tunnel_exfil plugin exposes expected params schema* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("dns", "dns-base")`
  - *NGFW EAL — anomalous DNS query volume from single source* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("dns", "dns-base")`
  - *NGFW EAL — high-entropy DNS labels (base32 character profile)* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("dns", "dns-base")`
  - *NGFW EAL — TXT query burst from single host* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("dns", "dns-base")`
  - *XSIAM stitching — DNS exfil + endpoint process lineage correlated* — `preset = network_story | filter _time > to_timestamp(current_time() - 1800) | filter app in ("dns", "dns-base") | filter`
- **Correlation rules (1):**
  - *DNS Tunneling Exfiltration — Volume Entropy And Length* — `BIOC(NGFW EAL — long FQDN exceeding host baseline) OR (XQL(NGFW EAL — anomalous DNS query volume from single source) AND`
- **Analytics modules:** Cortex XSIAM Network Security Analytics — DNS Tunneling / Exfiltration; Cortex XSIAM Analytics — High-entropy DNS query name scoring; Cortex XSIAM Analytics — Excessive DNS queries to a single domain

### TTP-2026-0052 — Cryptojacking — Stratum Mining Pool Connection via App-ID (T1496)

- **Status:** active · **Severity:** high · **Sim class:** network · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `ndr-003-cryptojacking-stratum.yml`
- **MITRE:** `T1496` Resource Hijacking
- **Tactics:** Impact
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 1 BIOC · 2 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (1):**
  - *NGFW EAL — repeated short-lived TCP sessions to mining pool port* — `dataset = panw_ngfw_traffic_raw | filter dst_port in (3333, 4444, 5555, 7777, 14444, 45700) | filter action = "allow" | comp count() as sess`
- **XQL queries (2):**
  - *Pre-flight: sinkhole port 3333 reachable* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_ip = "10.50.0.42" and `
  - *NGFW App-ID — Stratum/cryptocurrency-mining identified on outbound TCP* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app in ("stratum", "crypto`
- **Correlation rules (1):**
  - *Cryptojacking — Stratum Mining Pool Resource Hijacking* — `BIOC(NGFW EAL — repeated short-lived TCP sessions to mining pool port) OR XQL(NGFW App-ID — Stratum/cryptocurrency-minin`
- **IOCs (1):** user-agent=`stratum-json-rpc-mining-subscribe`
- **Analytics modules:** Cortex XSIAM Network Security Analytics — Cryptocurrency Mining / Stratum; Cortex XSIAM Analytics — Repeated short-lived sessions to a single destination port

### TTP-2026-0053 — SMB/RPC Lateral Sweep — Internal Host Discovery (T1046 / T1018 / T1021.002)

- **Status:** active · **Severity:** high · **Sim class:** network · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `ndr-004-smb-lateral-sweep.yml`
- **MITRE:** `T1046` Network Service Discovery, `T1018` Remote System Discovery, `T1021.002` Remote Services: SMB/Windows Admin Shares
- **Tactics:** Discovery, Lateral Movement
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (1):**
  - *NGFW EAL — anomalous SMB traffic outside admin baseline* — `dataset = panw_ngfw_traffic_raw | filter dst_port in (445, 139, 135) or app in ("smb", "ms-ds-smb", "msrpc", "netbios-ss") | filter src_ip !`
- **XQL queries (3):**
  - *Dry-run report shows planned host count and ports* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port in (445, 139, 135`
  - *NGFW EAL — host sweeping pattern (1 src to many dst on TCP/445)* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 445 | filter ds`
  - *XSIAM correlation — sweep src host stitched with workstation context* — `preset = network_story | filter _time > to_timestamp(current_time() - 1800) | filter dst_port in (445, 139, 135) | filte`
- **Correlation rules (1):**
  - *SMB RPC Lateral Sweep — Host Fan-Out From One Source* — `BIOC(NGFW EAL — anomalous SMB traffic outside admin baseline) OR XQL(NGFW EAL — host sweeping pattern (1 src to many dst`
- **Analytics modules:** Cortex XSIAM Network Security Analytics — Internal Network Scan / Host Sweep; Cortex XSIAM Analytics — Lateral Movement reconnaissance over SMB/RPC

### TTP-2026-0054 — Shadow-AI Asset Discovery & Inventory — Cortex Cloud AI-SPM (T1526)

- **Status:** active · **Severity:** medium · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-aispm-001-ai-asset-discovery.yml`
- **MITRE:** `T1526` Cloud Service Discovery, `T1580` Cloud Infrastructure Discovery
- **Tactics:** Discovery
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (4):**
  - *AISPM-001 Ground Truth Manifest Capture Bookkeeping* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter operation_name in ("terraform.o`
  - *AISPM-001 AI Asset Scan Cycle Discovery* — `dataset = ai_spm_findings | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "asset_discovered`
  - *AISPM-001 Inventory API AI Tagged Assets Count* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter asset_type in ("ai_endpoint`
  - *AISPM-001 Asset Discovery Coverage Reconciliation* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter tags contains "CortexSimAIS`
- **Correlation rules (1):**
  - *Shadow-AI Inventory Coverage — Full Asset Discovery* — `XQL(AISPM-001 AI Asset Scan Cycle Discovery) AND XQL(AISPM-001 Inventory API AI Tagged Assets Count) AND XQL(AISPM-001 A`
- **Analytics modules:** Cortex Cloud AI-SPM — Shadow-AI asset discovery and inventory enrichment; Cortex Cloud Posture — AI asset coverage reconciliation

### TTP-2026-0055 — AI Model Security Assessment — Overprivileged Role + Misconfig (T1078.004)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-aispm-002-ai-model-security-assessment.yml`
- **MITRE:** `T1078.004` Valid Accounts: Cloud Accounts, `T1098` Account Manipulation
- **Tactics:** Initial Access, Persistence
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (3):**
  - *AISPM-002 MTTD Anchor Bookkeeping* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" and reso`
  - *AISPM-002 Model Security Assessment Cycle Complete* — `dataset = ai_spm_findings | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "assessment_compl`
  - *AISPM-002 Three Model Security Findings On SageMaker Fixture* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter finding_type in ("overprivi`
- **Correlation rules (1):**
  - *AI Model Security — Overprivileged Role Plus Misconfig Same Model* — `XQL(AISPM-002 Model Security Assessment Cycle Complete) AND XQL(AISPM-002 Three Model Security Findings On SageMaker Fix`
- **Analytics modules:** Cortex Cloud AI-SPM — Model security assessment and IAM exposure analysis; Cortex Cloud Posture — Encryption-at-rest and input-validation policy checks

### TTP-2026-0056 — AI Ecosystem & Supply-Chain Risk — Vulnerable ML Dependencies (T1195.002)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-aispm-003-ai-supply-chain.yml`
- **MITRE:** `T1195.002` Supply Chain Compromise: Compromise Software Supply Chain, `T1574` Hijack Execution Flow
- **Tactics:** Initial Access, Persistence, Privilege Escalation
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (4):**
  - *AISPM-003 Supply Chain Manifest Capture Bookkeeping* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" and reso`
  - *AISPM-003 Supply Chain Scan Discovers ML Dependencies* — `dataset = ai_spm_findings | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "dependency_disco`
  - *AISPM-003 CVE Tagged ML Dependency Findings* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "vulnerable_`
  - *AISPM-003 Dependency Graph Chain SageMaker Lambda Provider* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter asset_type = "ai_dependency`
- **Correlation rules (1):**
  - *AI Supply Chain Risk — CVE Dependency In Traced AI Chain* — `XQL(AISPM-003 CVE Tagged ML Dependency Findings) AND XQL(AISPM-003 Dependency Graph Chain SageMaker Lambda Provider)`
- **Analytics modules:** Cortex Cloud AI-SPM — ML dependency CVE enrichment and EU AI Act classification; Cortex Cloud Posture — AI dependency-graph reconstruction

### TTP-2026-0057 — AI Static Risk Analysis — Hardcoded Creds + Insecure Pickle + Unvalidated Inputs (T1552.001)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-aispm-004-ai-static-risk-analysis.yml`
- **MITRE:** `T1552.001` Unsecured Credentials: Credentials In Files, `T1027` Obfuscated Files or Information, `T1565.001` Data Manipulation: Stored Data Manipulation
- **Tactics:** Credential Access, Defense Evasion, Impact
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (5):**
  - *AISPM-004 MTTD Anchor And Fixture Verify Bookkeeping* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" and reso`
  - *AISPM-004 Static Analyzer Scans AI Pipeline Artifacts* — `dataset = ai_spm_findings | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "static_scan_comp`
  - *AISPM-004 Hardcoded Credentials In ML Pipeline Finding* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "ai_static_a`
  - *AISPM-004 Insecure Model Serialization Pickle Finding* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "ai_static_a`
  - *AISPM-004 Unvalidated Model Inputs Finding* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "ai_static_a`
- **Correlation rules (1):**
  - *AI Static Risk — Hardcoded Creds Plus Insecure Pickle Plus Unvalidated Input* — `XQL(AISPM-004 Hardcoded Credentials In ML Pipeline Finding) OR XQL(AISPM-004 Insecure Model Serialization Pickle Finding`
- **Analytics modules:** Cortex Cloud AI-SPM — Static analysis of ML pipeline code and artifacts; Cortex Cloud Posture — Secret detection and insecure-serialization policy checks

### TTP-2026-0058 — AI Sensitive-Data Classification — PII/PHI/PCI In Training Sets (T1530)

- **Status:** active · **Severity:** high · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-aispm-005-ai-sensitive-data.yml`
- **MITRE:** `T1530` Data from Cloud Storage Object, `T1213` Data from Information Repositories
- **Tactics:** Collection
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 5 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (5):**
  - *AISPM-005 MTTD Anchor And Canary Verify Bookkeeping* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter cloud_provider = "AWS" and reso`
  - *AISPM-005 DSPM AISPM Cross Scan Links Bucket To AI* — `dataset = ai_spm_findings | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "data_lineage_lin`
  - *AISPM-005 DSPM Three Regulated Data Classes In Training Bucket* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "sensitive_d`
  - *AISPM-005 Data Lineage Edge Bucket To Training Job* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter asset_type = "ai_data_linea`
  - *AISPM-005 Governance Violation Regulated Data In Training Set* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter finding_type = "AI-GOV-001"`
- **Correlation rules (1):**
  - *AI Data Governance — Regulated Data In Training Set With Lineage* — `XQL(AISPM-005 DSPM Three Regulated Data Classes In Training Bucket) AND XQL(AISPM-005 Data Lineage Edge Bucket To Traini`
- **Analytics modules:** Cortex Cloud DSPM — Regulated-data classification of AI training stores; Cortex Cloud AI-SPM — Data lineage and AI governance violation detection

### TTP-2026-0059 — AI Security Dashboard & Posture — Aggregate Rollup Across All AI Findings (T1526)

- **Status:** active · **Severity:** medium · **Sim class:** endpoint · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `sim-aispm-006-ai-security-dashboard.yml`
- **MITRE:** `T1526` Cloud Service Discovery
- **Tactics:** Discovery
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-cloud, cortex-xsiam
- **Detection counts:** 0 BIOC · 3 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **XQL queries (3):**
  - *AISPM-006 Expected Aggregate Counts Bookkeeping* — `dataset = cloud_audit_logs | filter _time > to_timestamp(current_time() - 1800) | filter operation_name = "terraform.out`
  - *AISPM-006 Dashboard Summary Aggregate Across All Risk Categories* — `dataset = cortex_cloud_posture | filter _time > to_timestamp(current_time() - 1800) | filter tags contains "CortexSimAIS`
  - *AISPM-006 Dashboard Trend Series Per Risk Category* — `dataset = ai_spm_findings | filter _time > to_timestamp(current_time() - 86400) | filter risk_category in ("model_securi`
- **Correlation rules (1):**
  - *AI Posture Rollup — Executive Dashboard Covers All Risk Categories* — `XQL(AISPM-006 Dashboard Summary Aggregate Across All Risk Categories) AND XQL(AISPM-006 Dashboard Trend Series Per Risk `
- **Analytics modules:** Cortex Cloud AI-SPM — Aggregate posture rollup and executive summary; Cortex Cloud Posture — Risk-category trend-series aggregation

### TTP-2026-0060 — Cross-Plane Correlation MOAT — EDR + NDR + ITDR Stitch Into One Incident (T1078 / T1071.001 / T1110.003)

- **Status:** active · **Severity:** high · **Sim class:** network · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `mp-005-cross-plane-correlation.yml`
- **MITRE:** `T1078` Valid Accounts, `T1071.001` Application Layer Protocol: Web Protocols, `T1059.004` Command and Scripting Interpreter: Unix Shell, `T1110.003` Brute Force: Password Spraying
- **Tactics:** Command and Control, Credential Access, Defense Evasion, Execution, Lateral Movement
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam, ngfw-pa-series
- **Detection counts:** 3 BIOC · 4 XQL · 1 correlation · 0 IOC · 2 analytics modules
- **BIOCs (3):**
  - *MP-005 Interactive Bash From www-data Service Context* — `preset = xdr_data | filter event_type = ENUM.PROCESS | filter event_sub_type = ENUM.PROCESS_START | filter actor_process_image_name in ("bas`
  - *MP-005 NGFW Outbound HTTP Beacon To Known IOC Domain* — `dataset = panw_ngfw_traffic_raw | filter app in ("web-browsing", "ssl", "http") or dst_port in (80, 443) | filter dst_host = "testmynids.org`
  - *MP-005 Identity Kerberos PreAuth Failure Burst From One Client* — `dataset = ad_audit | filter event_id = 4768 | filter result_code != "0x0" | comp count_distinct(target_user_name) as targeted_users, count()`
- **XQL queries (4):**
  - *MP-005 NGFW Outbound HTTP To Known IOC testmynids* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_host = "testmynids.org`
  - *MP-005 EDR Interactive Bash From Service Context* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.PROCESS and event_sub_`
  - *MP-005 ITDR Kerberos PreAuth Failure Burst Against Service Accounts* — `dataset = ad_audit | filter _time > to_timestamp(current_time() - 1800) | filter event_id = 4768 | filter result_code !=`
  - *MP-005 Cross-Plane Stitch Single Incident Spanning Multiple Products* — `preset = xdr_data | filter _time > to_timestamp(current_time() - 1800) | comp count_distinct(_product) as plane_count, c`
- **Correlation rules (1):**
  - *Cross-Plane Stitch — EDR Plus NDR Plus ITDR From One Source Host* — `(BIOC(MP-005 Interactive Bash From www-data Service Context) AND (BIOC(MP-005 NGFW Outbound HTTP Beacon To Known IOC Dom`
- **Analytics modules:** Cortex XSIAM Correlation Engine — Cross-Plane Causality (EDR + NDR + ITDR); Cortex XSIAM Analytics — Multi-Source Incident Stitching by Source Host

### TTP-2026-0061 — FTP Cleartext Egress + Credential Exposure — Outbound STOR Exfiltration (T1071 / T1048.003)

- **Status:** active · **Severity:** high · **Sim class:** network · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `ndr-006-ftp-cleartext-egress.yml`
- **MITRE:** `T1071` Application Layer Protocol, `T1048.003` Exfiltration Over Unencrypted Non-C2 Protocol
- **Tactics:** Command and Control, Exfiltration
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (1):**
  - *NGFW EAL outbound FTP STOR file transfer with data payload* — `dataset = panw_ngfw_traffic_raw | filter app = "ftp" or dst_port = 21 | filter ftp_command = "STOR" or action_total_upload > 0 | filter dst_`
- **XQL queries (3):**
  - *Pre-flight FTP sinkhole reachable on port 21* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 21 | filter dst`
  - *NGFW App-ID FTP outbound session from non-admin endpoint* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app = "ftp" or dst_port = `
  - *XSIAM correlation FTP cleartext creds plus STOR egress stitched* — `preset = network_story | filter _time > to_timestamp(current_time() - 1800) | filter app = "ftp" or dst_port = 21 | comp`
- **Correlation rules (1):**
  - *FTP Cleartext Egress — Credential Exposure Plus STOR Upload* — `IOC(ftp-cleartext-credentials-user-pass) OR BIOC(NGFW EAL outbound FTP STOR file transfer with data payload) OR XQL(NGFW`
- **IOCs (1):** user-agent=`ftp-cleartext-credentials-user-pass`
- **Analytics modules:** Cortex XSIAM Network Security Analytics — Cleartext Protocol Exfiltration; Cortex XSIAM Analytics — Outbound FTP Data Transfer to Non-Corporate Destination

### TTP-2026-0062 — SSH Outbound App-ID + Atypical Client Banner — C2 / Tunnel Setup (T1572 / T1021.004)

- **Status:** active · **Severity:** high · **Sim class:** network · **Safety:** lab-only · **Destructive:** False
- **Mapped scenario(s):** `ndr-007-ssh-outbound-egress.yml`
- **MITRE:** `T1572` Protocol Tunneling, `T1021.004` Remote Services: SSH
- **Tactics:** Command and Control, Lateral Movement
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xsiam, cortex-xsoar, ngfw-pa-series
- **Detection counts:** 1 BIOC · 3 XQL · 1 correlation · 1 IOC · 2 analytics modules
- **BIOCs (1):**
  - *NGFW EAL repeated short-lived outbound SSH handshakes from single source* — `dataset = panw_ngfw_traffic_raw | filter app = "ssh" or dst_port = 22 | filter dst_ip != null and not incidr(dst_ip, "10.0.0.0/8") | filter `
- **XQL queries (3):**
  - *Pre-flight SSH sinkhole reachable on port 22* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 22 | filter dst`
  - *NGFW App-ID SSH outbound session from non-admin endpoint* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter app = "ssh" or dst_port = `
  - *XSIAM correlation SSH banner anomaly plus handshake burst stitched* — `preset = network_story | filter _time > to_timestamp(current_time() - 1800) | filter app = "ssh" or dst_port = 22 | comp`
- **Correlation rules (1):**
  - *SSH Outbound Tunnel Setup — Atypical Banner Plus Handshake Burst* — `IOC(SSH-2.0-Cortex_atypical_lateral_001) OR BIOC(NGFW EAL repeated short-lived outbound SSH handshakes from single sourc`
- **IOCs (1):** user-agent=`SSH-2.0-Cortex_atypical_lateral_001`
- **Analytics modules:** Cortex XSIAM Network Security Analytics — Outbound SSH Tunnel / C2 Setup; Cortex XSIAM Analytics — Atypical SSH Client Banner and Handshake Burst

### TTP-2026-0063 — Kerberoast → Pass-the-Hash → DCSync Multi-Plane Stitch (T1558.003 / T1550.002 / T1003.006)

- **Status:** active · **Severity:** critical · **Sim class:** identity · **Safety:** safe-by-design · **Destructive:** False
- **Mapped scenario(s):** `mp-002-kerberoast-lateral-smb.yml`
- **MITRE:** `T1558.003` Steal or Forge Kerberos Tickets: Kerberoasting, `T1550.002` Use Alternate Authentication Material: Pass the Hash, `T1021.002` Remote Services: SMB/Windows Admin Shares, `T1003.006` OS Credential Dumping: DCSync
- **Tactics:** Credential Access, Defense Evasion, Lateral Movement
- **Threat actors:** — · **Campaigns:** —
- **PANW products:** cortex-xdr, cortex-xsiam
- **Detection counts:** 3 BIOC · 6 XQL · 2 correlation · 0 IOC · 3 analytics modules
- **BIOCs (3):**
  - *MP-002 LDAP SPN Sweep From Non-Typical Host* — `dataset = okta_sso | filter event_type = ENUM.ACTIVE_DIRECTORY | filter operation_name in ("LDAP_SEARCH", "DirectorySearch") | filter lower(`
  - *MP-002 Service Account Used Interactively* — `dataset = okta_sso | filter event_type = ENUM.AUTHENTICATION | filter lower(account_name) in ("sql-svc", "cortexsim\\sql-svc") | filter logo`
  - *MP-002 Service Account Remote SMB Authentication On Workstation* — `preset = xdr_data | filter event_type = ENUM.AUTHENTICATION | filter auth_logon_type = 3 | filter lower(auth_username) contains "sql-svc" | `
- **XQL queries (6):**
  - *MP-002 Kerberos TGS-REQ Flood From Single Source* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter event_type = ENUM.ACTIVE_DIRECTORY | fi`
  - *MP-002 NGFW Kerberos Port 88 Traffic Spike From Attacker Host* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 88 | filter app`
  - *MP-002 NGFW SMB Port 445 Session Attacker To Workstation* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 445 | filter ap`
  - *MP-002 NGFW DCE RPC Port 135 Ephemeral Traffic To DC* — `dataset = panw_ngfw_traffic_raw | filter _time > to_timestamp(current_time() - 1800) | filter dst_port = 135 or (dst_por`
  - *MP-002 XSIAM Stitch Same Identity Across DC Workstation And NGFW* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 1800) | filter lower(account_name) contains "sql-svc" `
  - *MP-002 XSIAM MTTD Clock Kerberoast Recon To Lateral Auth* — `dataset = okta_sso | filter _time > to_timestamp(current_time() - 7200) | filter (event_id = 4769 and lower(account_name`
- **Correlation rules (2):**
  - *Kerberoast PtH Lateral Movement Same Identity Stitch* — `BIOC(MP-002 LDAP SPN Sweep From Non-Typical Host) OR BIOC(MP-002 Service Account Used Interactively) OR BIOC(MP-002 Serv`
  - *Kerberoast To Lateral Auth MTTD Timeline Window* — `BIOC(MP-002 LDAP SPN Sweep From Non-Typical Host) AND (BIOC(MP-002 Service Account Used Interactively) OR BIOC(MP-002 Se`
- **Analytics modules:** Cortex ITDR — Kerberoasting recon (LDAP SPN sweep + TGS-REQ burst); Cortex XDR Analytics — Service account remote authentication anomaly; Cortex XSIAM — Cross-plane identity stitching and MTTD timeline reconstruction

---

## 8. Gaps, defects, and stale content (audit appendix)

Severity is by POV impact (a failing demo or wrong content shown to a customer
is worse than an internal-only doc drift).

| id | Severity | Title |
|---|---|---|
| G-01 | critical | `scripts/validate.py` FAILS — 12 errors, corpus does not pass its own CI gate |
| G-02 | high | 9 SIM-* export artifacts are STALE skeletons (issue #65) |
| G-03 | high | 48 of 63 cards have NO export artifacts; export README index stale |
| G-04 | high | `manifest.json` absent + gitignored; RUNBOOK calls it the engine entry point |
| G-05 | medium | AISPM cards 0054-0059 mislabeled `simulation_class: endpoint` (cloud posture) |
| G-06 | medium | 6 hand-authored cards (0001,0002,0003,0006 unbound) have no scenario |
| G-07 | low | `ttp_ref` quoting inconsistent across scenarios (13 quoted / 46 unquoted) |
| G-08 | low | RUNBOOK/README documentation drift on loader path + placeholder rule_ids |
| G-09 | low | `ttps/_drafts/` empty + untracked; generate_card.py writes there |

### G-01 — validator FAILS (critical)
`python3 scripts/validate.py` exits non-zero: **132 pass, 0 warn, 12 fail**.
- `SRC-OWASP-LLM` referenced by `TTP-2026-0012`, `-0013`, `-0014`, `-0015`,
  `-0016` (both `metadata.source_refs[]` and `references[].publisher_id`) but is
  **not** in `sources/source-registry.json`. There is no OWASP source at all.
- `SRC-MICROSOFT` referenced by `TTP-2026-0041`. Registry has
  `SRC-MICROSOFT-LEARN` and `SRC-MSTIC` but not `SRC-MICROSOFT`.
- Fix: add the two sources to `sources/source-registry.json` (bump
  `registry_version`) OR rename the refs to existing ids. RUNBOOK states CI runs
  this validator and it must pass — currently it does not.

### G-02 — stale skeleton exports (high, issue #65)
`exports/xql/` and `exports/sigma/` for `TTP-2026-0007, 0012, 0017, 0023, 0027,
0032, 0038, 0042, 0047` still contain `// AUTO-GENERATED SKELETON — replace with
real XQL before promotion.` and `| filter /* TODO: predicate matching the BIOC
name */`, even though every corresponding card body now has real logic. 18 stale
files total. Fix: `python3 scripts/export_artifacts.py --clean` (verified to
regenerate real XQL).

### G-03 — export coverage gap (high)
Only 15 cards have any export; 48 have none. `exports/README.md` only indexes the
15 and therefore misrepresents corpus coverage to anyone browsing exports. Same
fix as G-02 (a full re-run produces all four artifact kinds for every card that
has the corresponding structure).

### G-04 — manifest.json absent (high)
`detection_scanner/manifest.json` does not exist on disk and is gitignored.
RUNBOOK calls it "the engine's load-time entry point" and lists it among "the
four files that matter most"; `build-manifest.py` exists to produce it and the
RUNBOOK weekly cadence says to regenerate + `git diff --exit-code` it as a CI
gate. In reality the engine (`core/engine/ttp_catalog.py`) globs `ttps/*.json`
directly and never reads the manifest, so nothing breaks at runtime — but the
documented CI gate is dead and any tool that *does* expect the manifest gets
nothing. README's "Open contracts" already flags this loader-path ambiguity.

### G-05 — AISPM simulation_class mislabel (medium)
`TTP-2026-0054` through `-0059` (AI-SPM cloud-posture scans) all declare
`metadata.pov_engine.simulation_class: endpoint`. They are cloud posture scans,
not endpoint executions — almost certainly a copy-paste carryover. Could route
them to the wrong execution path / wrong UI plane filter.

### G-06 — unbound hand-authored cards (medium)
`TTP-2026-0001` (help-desk MFA reset), `-0002` (LSASS dump), `-0003` (AWS IAM
key abuse), `-0006` (ESXi mass encrypt) have no scenario YAML `ttp_ref` binding
them, so they are not exercised by any runnable scenario — they exist only as
detection content / schema examples. `-0004` (DCSync) and `-0005` (rclone) are
bound (mp-002 / ndr-005). If these anchor cards are meant to be demoable, they
need scenarios; if reference-only, that should be stated on the card.

### G-07 — ttp_ref quoting inconsistency (low)
46 scenario files use unquoted `ttp_ref: TTP-2026-0022`; 13 use quoted
`ttp_ref: "TTP-2026-0012"` (airs/edr/mp). Both parse, but the split bit a naive
grep during this audit and will bite future tooling. Normalize to one style.

### G-08 — documentation drift (low)
- RUNBOOK presents `manifest.json` as load-bearing; the engine ignores it (G-04).
- RUNBOOK "Common pitfalls" states all `rule_ids` (`XSIAM-AN-*`, `XDR-BIOC-*`,
  `XSOAR-PB-*`) are illustrative placeholders — true across the corpus, so any
  POV that pastes them verbatim into a tenant will not match real rule ids.
- RUNBOOK weekly/monthly cadence references `git diff --exit-code manifest.json`
  which cannot pass while the file is gitignored/absent.

### G-09 — empty drafts dir (low)
`ttps/_drafts/` is empty and untracked. `generate_card.py` writes drafts there
per the documented pipeline; nothing is currently staged. Harmless, but the
documented "promote out of `_drafts/`" monthly task has no inputs.

---

## 9. Cross-domain links

- **Scenarios** — `scenarios/{plane}/*.yml` bind cards via
  `expected_detections[].ttp_ref`. See the card→scenario map in §3/§4 and the
  scenario catalog reference doc (if present).
- **Engine** — `core/engine/ttp_catalog.py` (loader, composite-key lookup),
  `core/api/ttps.py` (`GET /api/ttps`), `core/engine/scenario_loader.py`
  (resolves `ttp_ref + detection_id` at scenario load).
- **Sources** — every `metadata.source_refs[]` / `references[].publisher_id`
  resolves to `sources/source-registry.json` (34 sources). See G-01 for the two
  that do not.
- **MITRE** — technique/subtechnique/tactic ids validated by `validate.py`
  against `Txxxx[.xxx]` / `TAxxxx` formats; coverage rolled up in §2.
- **Exports** — `exports/{sigma,xql,correlation,xsoar_playbook}/` are the
  customer-deliverable artifacts; see issue #65 / §6.

# Tenant-side setup: EDR, CDR, KOI planes

Researched 2026-11-10. Docs host: cortex-docs.paloaltonetworks.com (docs-cortex.* deep links redirect to it). Short URLs below are the URLs actually fetched. Repo registry rows referenced: `xdr_data`, `cloud_audit_logs`, `cloud_audit`, `kubernetes_audit_logs`, `koi_code_scan_events` in `spec/dataset_requirements.yml`.

Evidence key: D = DOCUMENTED (fetched), I = INFERRED, NF = NOT FOUND.

## EDR (22 scenarios; Linux, Windows, macOS targets)

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| E1 | macOS: user/MDM must approve system extension, content filter, notifications, managed login items and grant Full Disk Access (TCC). PANW ships a unified `.mobileconfig` (V5, signed profile valid to Jun 2027) so no user prompt is needed | MDM profile; or per-endpoint approval during install | ingest, detect, prevent | per-host (or MDM once) | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/9.1/Cortex-XDR-Agent-Administrator-Guide/Install-with-a-unified-configuration-profile-for-MDMs | `agent_installed` |
| E2 | Linux: agent runs in Kernel Mode (supported kernel list) or User Space (eBPF, kernel >= 5.0, agent >= 7.7). Unsupported kernel with no fallback = asynchronous mode | Agent Settings profile › Agent Operation Mode | ingest, detect | per-host + profile | D https://cortex-docs.paloaltonetworks.com/cortex-xdr-agent/8.7/cortex-xdr-agent-for-linux (8.7); https://cortex-docs.paloaltonetworks.com/cortex-xdr-agent/9.1/cortex-xdr-agent-for-linux/cortex-xdr-agent-for-linux-requirements.md | `agent_installed` |
| E3 | Linux asynchronous mode has no event collection, no BTP, no EDR. A host in this mode produces no `xdr_data` causality, so every BIOC/ABIOC scenario reads "missed" | same | ingest, detect | per-host | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Set-up-agent-settings-profiles | `agent_installed` (add a `linux_agent_mode_not_async` note in detail) |
| E4 | User Space mode lacks Local Privilege Escalation protection and kernel-module examination. Prevention-style EDR scenarios that depend on anti-LPE only work in Kernel Mode | same | prevent | per-host + profile | D https://cortex-docs.paloaltonetworks.com/cortex-xdr-agent/8.7/cortex-xdr-agent-for-linux | `agent_installed` |
| E5 | Authoring BIOC rules requires a Cortex XDR Pro licence (event types FILE/PROCESS/NETWORK/REGISTRY/INJECTION/LOAD_IMAGE/EVENT_LOG/STORY). Repo cards tag BIOC as `pro`; consistent | Threat Management › Detection Rules › BIOC | detect | licence | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Create-a-BIOC-rule | `agent_installed` (licence noted in detail) |
| E6 | Pro per Endpoint agents apply limits and filters to network, file and registry telemetry; lifting them needs the Extended Threat Hunting Data (XTH) add-on. Hunt-style XQL on `xdr_data` for file/registry/network can return less than the scenario produced | licence | ingest, detect | licence or add-on | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Pre-installation-steps-for-Cortex-XDR-agents | `agent_installed` |
| E7 | "XDR Pro Endpoint Capabilities" toggles in the Agent Settings profile consume a Pro per Endpoint licence; Host Insights and Forensics are separate add-ons | Endpoints › Policy Management › Profiles › Agent Settings | detect | licence or add-on | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Set-up-agent-settings-profiles | `agent_installed` |
| E8 | Malware prevention profile has three action modes per module: Block, Report, Disabled. BTP in Block terminates processes up to the causality group owner; Report raises the alert without killing | Policy Management › Prevention › Profiles › Malware | detect vs prevent | per-POV profile | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Set-up-malware-prevention-profiles | new `prevention_profile_mode` |
| E9 | IOC/BIOC suppression rules (Settings › Exceptions Configuration › IOC/BIOC Suppression Rules) remove matching activity from a rule. A pre-existing suppression, disable-prevention rule or alert exclusion on the test host silences a scenario | Settings › Exceptions Configuration | detect, prevent | tenant audit before POV | D (suppression) https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Add-an-IOC-or-BIOC-rule-exception . I (disable-prevention, alert exclusion: titles seen in search results only, pages not fetched) | new `no_exception_suppressing_test_host` |
| E10 | `xdr_data` EDR telemetry arrives only from a connected agent; no separate data-source onboarding | n/a | ingest | per-host | I: registry row `xdr_data` is built_in; agent docs treat EDR data collection as an agent function (E2 table: "Endpoint EDR Data Collection") | `agent_installed` |
| E11 | Analytics (ABIOC/behavioral analytics) baseline period, minimum endpoints, time to first alert | n/a | detect | n/a | NF: XSIAM Analytics page https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Cortex-XSIAM-Analytics and Cortex Cloud Analytics page state no number | n/a |
| E12 | Paired run (report then block on one endpoint) maps to flipping E8 modes between runs; agent must check in to receive the changed profile before run 2 | Profiles + Check-in | prevent | per-POV | I: from E8 plus agent policy pull behaviour; check-in interval not researched | new `prevention_profile_mode` |

### Timing and volume constraints
- Profile change to endpoint: not documented in what was fetched; force `Check-in` before the block-mode run (I).
- ABIOC/analytics: NF for any baseline. Treat every ABIOC expectation as "cannot be shown in a one-day POV" until a Palo Alto Networks source gives the number.
- Linux User Space mode: BTP and EDR collection are present, so BIOC works; anti-LPE does not (E4).
- XTH limits (E6): file/registry/network volume can be filtered on Pro per Endpoint; `SIM-EDR-009` (rclone bulk exfil), `SIM-EDR-008`/`011` (ransomware, many file events) are the most exposed.

### Scenario-specific gotchas
- `SIM-EDR-001`..`022` on Linux: confirm Kernel vs User Space vs async on the actual jumpbox (`cat /proc/$(pidof pmd)/maps | grep bpfec` is the doc's user-space check, k8s page). In async mode all BIOC rows are false "missed".
- macOS targets (`SIM-KOI-009` host also): beacon must run as the session user, and TCC approvals (E1) must already be in place; without them the agent is installed but file/process/network telemetry is degraded. Doc does not state exactly what degrades; D only for the approvals list.
- `SIM-EDR-021` (BYOVD ThrottleStop.sys) and `SIM-EDR-007` (ESXi): Windows kernel driver load and ESXi are outside what Linux/macOS agent docs cover; not researched here.
- Card `agent_version_min: 8.4.0` for KOI-009 BIOC rows: not checked against the compatibility matrix (NF).

### Report vs block
- Block on Malware profile BTP kills the process tree to the CGO, so later steps that depend on the killed process never run: those steps must read `prevented` (two-witness rule) and not `missed`. Source: E8.
- Linux User Space mode cannot block LPE (E4): a "prevented" expectation for a privesc step will read as detected-only there.

## CDR (28 scenarios; container runtime, Kubernetes, cloud audit logs)

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| C1 | XDR agent on Kubernetes = DaemonSet from the unedited YAML generated in the console; needs agent >= 7.9, a Cortex XDR Cloud per Host licence, supported k8s (docker or containerd). Editing the YAML is unsupported. Not installable inside Kubernetes-in-Docker | Console agent installer › Kubernetes YAML | ingest, detect, prevent | per-cluster, per-node licence | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/9.1/Cortex-XDR-Agent-Administrator-Guide/Install-the-Cortex-XDR-Agent-for-Kubernetes-Hosts | `agent_installed` |
| C2 | GKE COS and OpenShift RHCOS force User Space mode (no kernel module). GKE Autopilot needs the partner allowlist (AllowlistSynchronizer) for the privileged workload and blocks live terminal/remote script | same | ingest, prevent | per-cluster | D same URL as C1 | `agent_installed` |
| C3 | If Prisma Cloud Compute is also present, its Runtime protection must be disabled while the XDR agent runs | PCC › Defend › Runtime | detect | per-cluster | D same URL as C1 | new `no_conflicting_runtime_agent` |
| C4 | Cortex Cloud Kubernetes Connector wizard: Settings › Data Sources & Integrations › Create Integration › Kubernetes. Capabilities: Posture Management (default on) and Realtime Protection (installs agent as DaemonSet; not supported on Fargate). Agent type: Cloud optimized agent (runtime/threat detection) or XDR agent (full prevention and response) | Cortex Cloud | detect, prevent | per-cluster | D https://cortex-docs.paloaltonetworks.com/cortex-cloud-posture-management/onboard-and-configure/deployment-steps-and-checklist/onboard-the-kubernetes-connector.md | `api_integration` (posture) + `agent_installed` (runtime) |
| C5 | Posture scan cadence default 12 h (configurable 1-24 h). `SIM-CDR-007`/cspm-style posture findings appear only after a scan cycle | same | detect | per-POV setting | D same URL as C4 | `api_integration` |
| C6 | AWS CloudTrail: Cortex CSP onboarding Automated (creates trail, S3, SNS, SQS, KMS; role `cortex-logs-ingestion-access-*`) or Custom (existing S3 bucket). Management events by default; data events (S3 object-level, Lambda) are optional | Settings › Data Sources & Integrations › Amazon Web Services | ingest | one-time per account | D https://cortex-docs.paloaltonetworks.com/cortex-cloud-runtime-security/onboard-and-configure/deployment-steps-and-checklist/cloud-service-provider-csp-onboarding/amazon-web-services-cloud-onboarding/cortex-cloud-and-aws-audit-log-collection-architecture ; https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/How-to-onboard-Amazon-Web-Services | `audit_log_export` |
| C7 | AWS audit-log collection is included with an XDR licence carrying the Cloud Posture Security or Cloud Runtime Security add-on | licence | ingest | licence | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/How-to-onboard-Amazon-Web-Services | `audit_log_export` |
| C8 | S3 mass-object read/exfil (`SIM-CDR-019`) and DynamoDB/RDS dumps (`027`/`028`) are data-plane events; CloudTrail data events must be enabled in the onboarding | AWS onboarding (data events option) | ingest | one-time | I: C6 says data events are optional and cover S3 object-level; mass GetObject cannot appear without them | `audit_log_export` |
| C9 | GCP: ingestible log types include audit logs (including GKE audit logs), generic logs, DNS, flow. Licence statement given: Cortex XDR NG SIEM / Enterprise / Enterprise+ | Data Sources › Google Cloud Platform | ingest | licence + one-time | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Google-Cloud-Platform | `audit_log_export` |
| C10 | GCP Data Access audit logs (needed by BigQuery exfil `SIM-CDR-022`), Pub/Sub sink and service-account roles | GCP | ingest | one-time | NF: the Cortex GCP onboarding page https://cortex-docs.paloaltonetworks.com/cortex-cloud-runtime-security/onboard-and-configure/deployment-steps-and-checklist/cloud-service-provider-csp-onboarding/google-cloud-platform-cloud-onboarding/how-to-onboard-google-cloud-platform does not list them. I: Data Access logs are off by default in GCP and BigQuery reads are Data Access events | `audit_log_export` |
| C11 | Azure: Event Hub collector. Datasets `MSFT_Azure_raw` and `cloud_audit_logs`. Diagnostic settings categories listed (Administrative, Security, Policy..., plus Resource logs incl. AKS audit). Data Collection add-on licence. "Use audit logs in analytics" normalizes into `cloud_audit_logs` | Data Sources › Azure Event Hub | ingest | one-time + licence | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Ingest-logs-from-Microsoft-Azure-Event-Hub | `audit_log_export` |
| C12 | EKS audit logs: Amazon CloudWatch → Kinesis Firehose (Direct PUT, HTTP endpoint, GZIP) into the tenant; normalized EKS audit is queried in dataset `cloud_audit_logs` | Data Sources › Amazon CloudWatch | ingest | one-time | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Ingest-logs-from-Amazon-CloudWatch | `audit_log_export` |
| C13 | Dataset literally named `kubernetes_audit_logs` | n/a | ingest | n/a | NF: the docs fetched (C11, C12) place EKS/AKS audit in `cloud_audit_logs`; no page found naming `kubernetes_audit_logs` | n/a |
| C14 | Cloud analytics "first-time", "dormant cloud", impossible-style baselines (`SIM-CDR-016`, `024`, `026`) learning period | n/a | detect | n/a | NF: Cortex Cloud Analytics page https://docs-cortex.paloaltonetworks.com/r/Cortex-CLOUD/Cortex-Cloud-Runtime-Security-Documentation/Analytics-issues-and-Analytics-BIOCs states no period | n/a |

### Timing and volume constraints
- Posture: 12 h default scan (C5); a one-day POV sees at most two cycles.
- Cloud-analytics-based CDR scenarios (`016`, `024`, `026`: "first-time", "dormant"): baseline length NF. These cannot be promised for a one-day POV.
- CloudTrail ingestion delay: NF in the pages fetched.

### Scenario-specific gotchas
- `SIM-CDR-025` and `SIM-CDR-026` XQL read `dataset = kubernetes_audit_logs`. Real EKS/AKS audit lands in `cloud_audit_logs` (C11/C12). A real cluster feed would not populate the dataset these cards query. The repo's `k8s_audit_emitter` produces the card-named dataset via a collector, so the synthetic path works while a real-cluster path would read zero. Decide which dataset is the contract before the registry row `kubernetes_audit_logs` is finalised (this row currently lists source-side `audit_log_export` from `kubernetes`, which matches no documented Cortex path).
- Registry row `cloud_audit` (one card, AWS control-plane events) is probably the same dataset as `cloud_audit_logs`; docs (C11/C12) name only `cloud_audit_logs`.
- `SIM-CDR-019`/`027`/`028` need CloudTrail data events (C8); a default management-events-only onboarding cannot show them.
- `SIM-CDR-022` GCP: Data Access audit logs unconfirmed (C10).
- Kubernetes agent: Fargate not supported for Realtime Protection (C4); Docker-in-Docker/kind labs not supported (C1). The repo's K3s lab is not in the supported-platform text that was fetched (the supported-platforms matrix page was not opened) - NF for K3s.
- GKE COS / RHCOS forced User Space (C2): kernel-module-dependent prevention may be unavailable.

### Report vs block
- Cloud optimized agent = detect; XDR agent = prevention and response (C4). A POV using the cloud-optimized agent should not expect `prevented` on `SIM-CDR-003` (container escape), `010` (ransomware in container), `013`/`014` (ELF detonation). Source: C4 text; mapping of those scenarios is I.
- Container exec prevention requires malware/exploit profile in Block (E8, same profile model on Linux hosts); I for container processes: doc says the Linux agent extends exploit and malware protection to containerized processes (D https://cortex-docs.paloaltonetworks.com/cortex-xdr-agent/8.7/cortex-xdr-agent-for-linux).

## KOI (9 scenarios; agentic endpoint / supply chain)

Product mapping (D): KOI plane = **Cortex Agentic Endpoint Security (AES)**, the product built from the Koi acquisition (closed 2026-04-14). Delivered three ways: standalone, a module in Cortex XDR, and inside Prisma AIRS. Public docs exist, so this is not NOT FOUND. Repo descriptor `core/planes/koi.py` names `cortex_engine="Cortex (agentic endpoint / supply-chain)"`; the cards' `panw_mapping` instead lists `cortex-cloud/code-security` (prevention, advanced) and `cortex-xdr/behavioral-analytics` (pro, agent >= 8.4.0).

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| K1 | KOI plane = Cortex AES (Koi). Covers IDE extensions, MCP servers, browser extensions, packages (npm, pip), AI agents and coding tools; four pillars: visibility, context-aware risk, governance, real-time prevention | n/a | n/a | n/a | D https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-koi-to-secure-the-agentic-endpoint ; https://www.paloaltonetworks.com/resources/techbriefs/cortex-agentic-endpoint-security | n/a |
| K2 | AES needs the AES add-on licence. Supported on Platform XDR and XSIAM tenants only (not legacy). Windows and macOS endpoints only | licence | detect, prevent | licence | D https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Set-up-agent-settings-profiles | new `aes_addon_licensed` |
| K3 | AES run mode is set per platform in Agent Settings profile › Agentic Endpoint Security (AES). Default Disabled. Options: AES only (no event collection, no prevention modules) or XDR + AES | Endpoints › Policy Management › Profiles › Agent Settings | detect, prevent | per-POV profile | D same URL as K2 | new `aes_run_mode_enabled` |
| K4 | For KOI-009 (macOS) the XDR behavioural leg needs run mode XDR + AES, not AES only (AES only collects no events) | same | detect | per-POV | I: from K3 "AES only: no event collection" and card rows that query `xdr_data` | `agent_installed` |
| K5 | Koi connector in the tenant: ingests Koi alerts and audit logs via API. Only for tenants onboarded after 2026-07-26; older tenants use the Marketplace pack. Needs any of: active XSIAM, Cortex Cloud (Posture w/ ASPM, Runtime), Cortex XDR, or AgentiX licence, with the ASM, Exposure Management or TIM add-on | Settings › Data Sources & Integrations › Koi | ingest | one-time + licence | D https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/koi/koi.md | `api_integration` |
| K6 | Koi side: API key created in Koi Settings › API Access (needs `xt-Administrator`); fetch types Behavior Analytics (default), Addressable, Detect and Protect alerts; audit log type filter; default 5000 events per type | Koi console | ingest | one-time | D (primary source: pack README in the content repo, secondary to cortex-docs) https://github.com/demisto/content/blob/master/Packs/Koi/README.md | `api_integration` |
| K7 | Resulting XQL dataset is `koi_koi_raw` (modeling rule shipped in pack) | XQL | ingest | n/a | D same URL as K6 (GitHub README); https://xsoar.pan.dev/docs/reference/integrations/koi lists the integration commands only | `api_integration` |
| K8 | Dataset `koi_code_scan_events` and its fields (`artifact_type`, `manifest_host_permissions`, `signature_valid`, `scan_finding_text`) used by KOI cards | n/a | ingest | n/a | NF: no public doc or pack uses that dataset name; pack README names `koi_koi_raw` | n/a |
| K9 | Cortex Cloud code-scan path (typosquat package, injected MCP tool description) used by KOI-001..005 cards (`cortex-cloud/code-security`, rule IDs `CCS-*`) | Cortex Cloud Application Security / software supply chain | detect | licence | I: cortex-docs home links "Software supply chain security" (https://cortex-docs.paloaltonetworks.com/application-security/software-supply-chain-security/software-supply-chain-security, link seen, page not fetched). Rule IDs `CCS-*` NF | `api_integration` |
| K10 | NGFW egress leg (PyPI/NPM/Chrome Web Store App-ID, URL filtering) | PAN-OS | detect | per-POV | not researched here (NGFW plane) | `allow_rule_for_sim_traffic`, `url_filtering_logged` |

### Timing and volume constraints
- Koi connector polls on a fetch interval: value NF in pages fetched. Default max 5000 events per type (K6).
- AES discovery/inventory latency, risk-score timing: NF.
- Connector is API pull, so findings reach XQL after Koi generates the alert plus one fetch cycle (I).

### Scenario-specific gotchas
- `SIM-KOI-001`..`008` are fetch/scan scenarios run from a Linux jumpbox through the agentic_egress plugin. AES supports Windows and macOS only (K2), so no AES sensor can observe them; they depend on Cortex Cloud code scan and NGFW only. Treat any "AES saw it" expectation on a Linux host as unachievable.
- `SIM-KOI-006` card reads `koi_code_scan_events` (K8); the real Koi connector writes `koi_koi_raw` alert/audit records, which are Koi findings, not raw scan rows. The detection will not match a real tenant as written. Same for `SIM-BROWSER-003`.
- Registry row `koi_code_scan_events` says "how Koi events reach the tenant is not confirmed": now answered by K5-K7 (connector → `koi_koi_raw`); proposed fix is to rename the row, not to keep both.
- `SIM-KOI-009`: lab runbook says AES leg is observation-only and AES is installed when "entitled". Per K2-K4 the AES add-on, a Platform tenant, and a profile run mode other than Disabled are all required; the default is Disabled, so an untouched profile means the AES leg silently never appears.
- The tenant-gating note in K5 (onboarded after 2026-07-26) means the connector appears only on newer tenants; older POV tenants need the Marketplace pack.

### Report vs block
- AES "real-time prevention" and guardrails block risky downloads/tool executions (D techbrief https://www.paloaltonetworks.com/resources/techbriefs/cortex-agentic-endpoint-security). A profile with blocking policy active stops the poisoned extension install or tool call before the XDR behavioural leg sees it, so `SIM-KOI-009` step results must read `prevented` rather than `missed`. Per-policy settings (what is blocked in which mode) NF in the pages fetched.

## Counts

By table row, classified by primary evidence (mixed rows counted under their first label). 36 rows.

- DOCUMENTED: 25 (EDR 9, CDR 10, KOI 6)
- INFERRED: 5 (EDR E10, E12; CDR C8; KOI K4, K9)
- NOT FOUND: 6 (EDR E11; CDR C10, C13, C14; KOI K8, K10 not researched here)

## Sources

Fetched (content read):
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/9.1/Cortex-XDR-Agent-Administrator-Guide/Install-with-a-unified-configuration-profile-for-MDMs
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/8.7/Cortex-XDR-Agent-Administrator-Guide/Cortex-XDR-Agent-for-Linux
- https://cortex-docs.paloaltonetworks.com/cortex-xdr-agent/9.1/cortex-xdr-agent-for-linux/cortex-xdr-agent-for-linux-requirements.md
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/9.1/Cortex-XDR-Agent-Administrator-Guide/Install-the-Cortex-XDR-Agent-for-Kubernetes-Hosts
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Set-up-agent-settings-profiles
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Set-up-malware-prevention-profiles
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Create-a-BIOC-rule
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Add-an-IOC-or-BIOC-rule-exception
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Pre-installation-steps-for-Cortex-XDR-agents
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Cortex-XSIAM-Analytics (no baseline stated)
- https://docs-cortex.paloaltonetworks.com/r/Cortex-CLOUD/Cortex-Cloud-Runtime-Security-Documentation/Analytics-issues-and-Analytics-BIOCs (no baseline stated)
- https://cortex-docs.paloaltonetworks.com/cortex-cloud-posture-management/onboard-and-configure/deployment-steps-and-checklist/onboard-the-kubernetes-connector.md
- https://docs-cortex.paloaltonetworks.com/r/Cortex-CLOUD/Cortex-Cloud-Runtime-Security-Documentation/Kubernetes (thin)
- https://docs-cortex.paloaltonetworks.com/r/Cortex-CLOUD/Cortex-Cloud-Runtime-Security-Documentation/Cortex-Cloud-and-AWS-audit-log-collection-architecture
- https://docs-cortex.paloaltonetworks.com/r/Cortex-CLOUD/Cortex-Cloud-Runtime-Security-Documentation/How-to-onboard-Google-Cloud-Platform (does not list GCP prerequisites)
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/How-to-onboard-Amazon-Web-Services
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Google-Cloud-Platform
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Ingest-logs-from-Microsoft-Azure-Event-Hub
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Ingest-logs-from-Amazon-CloudWatch
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/koi/koi.md
- https://xsoar.pan.dev/docs/reference/integrations/koi
- https://github.com/demisto/content/blob/master/Packs/Koi/README.md (PANW content repo; secondary to cortex-docs)
- https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-koi-to-secure-the-agentic-endpoint
- https://www.paloaltonetworks.com/resources/techbriefs/cortex-agentic-endpoint-security (read via search result text)
- https://cortex-docs.paloaltonetworks.com/llms.txt (index, used to find the Koi page)
- https://live.paloaltonetworks.com/t5/community-blogs/securing-kubernetes-clusters-the-cortex-xdr-and-xsiam-approach/ba-p/586871 (secondary, community; nothing usable)

Seen in search results only, not fetched, not relied on: Install-the-Cortex-XDR-Agent-Using-JAMF (9.2/8.7), Install-the-Cortex-XDR-Agent-Manually (8.7), Add-a-disable-prevention-rule-for-endpoints, Set-up-exploit-prevention-profiles (Cortex Cloud), Exception-configuration, https://www.paloaltonetworks.com/cyberpedia/agentic-endpoint-security, https://www.wwt.com/blog/cortex-agentic-endpoint-security-watching-the-layer-your-edr-forgot (secondary).

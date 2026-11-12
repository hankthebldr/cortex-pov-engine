# Cortex Marketplace content packs vs. the CortexSim dataset registry

Scope: third-party and PANW content packs for the non-first-party datasets in `spec/dataset_requirements.yml`. Per-plane product configuration is covered by other agents.

Method and evidence legend
- Pack facts come from the public `demisto/content` repo (`Packs/<Folder>/`), fetched by partial clone at commit `05e3556f882a6238ecf1028beb9b832d8ebb4572` (2026-11-11). Dataset names are read from the `[MODEL: dataset=...]` lines in `ModelingRules/*.xif` and the `[INGEST:vendor=..., product=..., target_dataset=...]` lines in `ParsingRules/*.xif`. Evidence URL form: `https://github.com/demisto/content/blob/master/Packs/<Folder>/<path>`.
- Pack folder names are not always the marketplace id (folder `AWS-CloudTrail` is listed at `https://cortex.marketplace.pan.dev/marketplace/details/AWSCloudTrail/`). Only the Okta marketplace page was fetched directly; the rest were verified from the repo.
- Cortex docs fetched: Datasets and presets (D1), Set up an HTTP log collector (D2), Ingest logs and data from Okta (D3). URLs in `## Sources`.
- DOCUMENTED = fetched page or file says it. INFERRED = reasoning, no page. NOT FOUND = looked, public material does not say.

## Why dataset names matter more than anything else here

D2 (fetched): the HTTP log collector names the dataset `<Vendor>_<Product>_raw` from the **Vendor and Product set on the collector**, not from anything in the posted record. The emitters put a `"dataset": "<name>"` field in the JSON body, but that field is a label for the operator; it does not route. Two consequences, both INFERRED from D2:
1. A registry name without a `_raw` suffix (`okta_sso`, `msft_azure_ad_signin`, `msft_o365_audit`, `msft_windows_security`, `prisma_browser_events`, `kubernetes_audit_logs`, `esxi_syslog`, `google_workspace_audit`) can never be produced by an HTTP collector. A card that queries `dataset = okta_sso` against a tenant matches nothing, whatever the emitter posts.
2. The pack's modeling rule (XDM mapping) is bound to the dataset name in its `[MODEL: dataset=...]` line. The DC must set the collector's Vendor/Product so the resulting dataset equals the one the pack models (for Okta, vendor `okta`, product `sso` gives `okta_sso_raw`, which the Okta pack's parsing rule claims: `Packs/Okta/ParsingRules/Okta/Okta.xif`).

## Dataset name check

"Pack's dataset" is what the pack's rules or Cortex docs say the source lands in. "Match?" compares it to the registry key.

| Registry dataset | Producer | Pack (repo folder) | Pack's dataset | Match? | Evidence |
|---|---|---|---|---|---|
| `okta_sso` | okta | Okta | `okta_sso_raw` (native Okta data source, and parsing rule vendor `okta` product `sso`); `okta_okta_raw` (pack's Okta Event Collector integration) | NO: missing `_raw` | DOCUMENTED. D3; `Packs/Okta/ParsingRules/Okta/Okta.xif`; `Packs/Okta/ModelingRules/OktaModelingRules_2_11/OktaModelingRules_2_11.xif` |
| `saas_okta_raw` | okta | Okta | same as above; no pack produces `saas_okta_raw` | NO | INFERRED (name absent from every Okta rule and from D1). The one card using it (5 queries) is probably a stale name for `okta_sso_raw` |
| `msft_azure_ad_signin` | microsoft-entra | Microsoft Entra ID / Azure Logs (MicrosoftEntraID) | `msft_azure_ad_raw` (sign-ins via the Office 365 data source, Graph API); `msft_azure_ad_audit_raw` (audit); `msft_azure_raw` (Azure Event Hub sign-in tables) | NO | DOCUMENTED. `Packs/MicrosoftEntraID/README.md`; `Packs/MicrosoftEntraID/ParsingRules/MicrosoftEntraID/MicrosoftEntraID.xif`; D1 |
| `msft_o365_audit` | microsoft-365 | Office 365 (Office365) | `msft_o365_general_raw`, `msft_o365_exchange_online_raw`, `msft_o365_sharepoint_online_raw`, `msft_o365_dlp_raw`, `msft_o365_azure_ad_raw` | NO (no single audit dataset; it is split by workload) | DOCUMENTED. `Packs/Office365/README.md`; `Packs/Office365/ParsingRules/Office365/Office365.xif`; D1 |
| `msft_o365` (email) | microsoft-365 | Office 365 (Office365) | `msft_o365_emails_raw` (Microsoft 365 email collector, Graph API) | NO | DOCUMENTED. `Packs/Office365/README.md`; D1 |
| `msft_windows_security` | microsoft-windows | Microsoft Windows Logs (MicrosoftWindowsEvents) | `microsoft_windows_raw` (Security, System, Application, Firewall, PowerShell via WEC/Broker VM or XDR Collector/Winlogbeat) | NO | DOCUMENTED. `Packs/MicrosoftWindowsEvents/README.md`; `.../ParsingRules/MicrosoftWindowsEvents/MicrosoftWindowsEvents.xif`; D1 |
| `ad_audit` | microsoft-windows | Microsoft Windows Logs | `microsoft_windows_raw` carries Security-provider events such as 4768/4662 | NO (no dataset named `ad_audit`) | INFERRED: pack says it supports the Security provider; it does not list event IDs. `Packs/MicrosoftWindowsEvents/README.md` |
| `google_workspace_audit` | google-workspace | none in public repo for login/admin audit (`GSuiteAdmin` is an XSOAR integration with no XSIAM content; `GoogleChrome` pack models Chrome only) | Native Google Workspace data source: `google_workspace_login_raw`, `_admin_console_raw`, `_token_raw`, `_saml_raw`, `_drive_raw`, `_alerts_raw`, `_chat_raw`, ... ; Chrome: `google_workspace_chrome_raw` | NO | DOCUMENTED. D1; `Packs/GoogleChrome/ParsingRules/GoogleChrome/GoogleChrome.xif` |
| `cloud_audit_logs` | cloud-provider-audit | AWS - CloudTrail (AWS-CloudTrail), Azure Logs/Event Hub (AzureEventsHub), Google Cloud Logging (GoogleCloudLogging) | Raw: `amazon_aws_raw`, `msft_azure_raw`, `google_cloud_logging_raw`. Normalised audit dataset: `cloud_audit_logs` for Azure Event Hub, GCP, EKS audit | YES on the name; but it is a platform-normalised dataset, not an `_raw` landing dataset | DOCUMENTED that the name exists (D1). INFERRED that an HTTP collector cannot write it directly (D2 only produces `<Vendor>_<Product>_raw`) |
| `cloud_audit` | cloud-provider-audit | same | `cloud_audit_logs` / `amazon_aws_raw` | NO (no dataset named `cloud_audit`) | INFERRED: the single card reads CloudTrail fields; likely meant `cloud_audit_logs` |
| `msft_azure_audit` | cloud-provider-audit | Azure Logs (AzureEventsHub, MicrosoftEntraID) | `msft_azure_raw` (all Azure Event Hub logs); `cloud_audit_logs` (normalised audit) | NO | DOCUMENTED. D1; `Packs/AzureEventsHub/ParsingRules/AzureEventsHub/AzureEventsHub.xif` |
| `kubernetes_audit_logs` | kubernetes | Kubernetes | `kubernetes_kubernetes_raw` (Broker VM Syslog collector, vendor `kubernetes`, product `kubernetes`); EKS audit via CloudWatch normalises to `cloud_audit_logs` | NO | DOCUMENTED. `Packs/Kubernetes/README.md`; `Packs/Kubernetes/ParsingRules/Kubernetes/Kubernetes.xif`; CloudWatch excerpt in search results (page not fetched) |
| `proofpoint_tap_raw` | proofpoint | ProofpointTAP | `proofpoint_tap_raw` (vendor `Proofpoint`, product `Tap`) | YES | DOCUMENTED. `Packs/ProofpointTAP/ParsingRules/ProofpointTAP/ProofpointTAP.xif`; D1 |
| `cisco_asa_raw` | cisco-asa | CiscoASA | `cisco_asa_raw` (vendor `cisco`, product `asa`; Broker VM Syslog app) | YES | DOCUMENTED. `Packs/CiscoASA/ParsingRules/CiscoASA/CiscoASA.xif`; D1 |
| `ivanti_ics_raw` | ivanti | IvantiConnectSecure | `ivanti_connect_secure_raw` (vendor `ivanti`, product `connect_secure`) | NO | DOCUMENTED. `Packs/IvantiConnectSecure/ParsingRules/IvantiConnectSecure/IvantiConnectSecure.xif` |
| `esxi_syslog` | vmware-esxi | VMwareESXi | `vmware_esxi_raw` (vendor `VMware`, product `ESXi`; Broker VM Syslog app) | NO | DOCUMENTED. `Packs/VMwareESXi/ParsingRules/VMwareESXiParsingRules/VMwareESXiParsingRules.xif` |
| `koi_code_scan_events` | koi | KOI (Koi) | `koi_koi_raw` (vendor `koi`, product `koi`; fetched by the pack's integration from the KOI API) | NO. Also the fields the cards read (`artifact_type`, `manifest_host_permissions`, `signature_valid`) are absent from the pack's schema (OCSF-like: `finding_info`, `object_type`, `risk_level`, `source_log_type` Audit/Alerts, ...) | DOCUMENTED. `Packs/Koi/ModelingRules/Koi/Koi_schema.json`; `Packs/Koi/README.md` |
| `prisma_browser_events` | prisma-browser | none in public repo (no Prisma Browser pack found in the 87k-path tree) | `panw_prisma_access_browser_raw` | NO | DOCUMENTED. D1 |
| `saas_audit_logs` | saas-generic | none | `saas_audit_logs` is a platform-normalised dataset (Okta events "normalized to `xdr_data` or `saas_audit_logs`") | YES on the name; the cards' fields (`dst_host`, `uri_path`, `oauth_scopes`) are not shown in any doc fetched | DOCUMENTED that the name exists (D3). Field shape NOT FOUND |
| `panw_ngfw_traffic_raw` | strata-ngfw | none needed (platform data source: Strata Logging Service) | `panw_ngfw_traffic_raw` | YES | DOCUMENTED. D1 |
| `panw_ngfw_threat_raw` | strata-ngfw | same | `panw_ngfw_threat_raw` | YES | DOCUMENTED. D1 |
| `panw_ngfw_globalprotect_raw` | strata-ngfw | same | `panw_ngfw_globalprotect_raw` | YES | DOCUMENTED. D1 |
| `pan_ngfw_traffic` | strata-ngfw | same | `panw_ngfw_traffic_raw` | NO | INFERRED: same signal as the row above |
| `pan_dns` | strata-ngfw | same | none in D1's NGFW list (auth, config, filedata, globalprotect, hipmatch, system, threat, traffic, url, userid, tunnel) | NO | NOT FOUND. DNS Security data is not a listed standalone NGFW dataset |
| `cortexsim_airs_raw` | cortexsim-synthetic | none | `<Vendor>_<Product>_raw`: matches only if the collector is set to vendor `cortexsim`, product `airs` | By construction only | NOT FOUND for any PANW pack. The only AIRS pack (`PaloAltoNetworks_Prisma_AIRs`) is a community XSOAR integration with no data rules |

Mismatch count: 17 (okta_sso, saas_okta_raw, msft_azure_ad_signin, msft_o365_audit, msft_o365, msft_windows_security, ad_audit, google_workspace_audit, cloud_audit, msft_azure_audit, kubernetes_audit_logs, ivanti_ics_raw, esxi_syslog, koi_code_scan_events, prisma_browser_events, pan_ngfw_traffic, pan_dns). Matches: 7. Not applicable: 1 (`cortexsim_airs_raw`).
Row evidence tally: DOCUMENTED 19, INFERRED 4, NOT FOUND 2 (25 rows incl. the synthetic one).

Card exposure (counted in `detection_scanner/ttps/*.json`, `dataset = X` occurrences): `okta_sso` 45 · `msft_o365_audit` 45 · `msft_windows_security` 33 · `msft_azure_ad_signin` 32 · `prisma_browser_events` 28 · `cloud_audit_logs` 135 · `saas_audit_logs` 15 · `msft_azure_audit` 10 · `kubernetes_audit_logs` 8 · `cloud_audit` 8 · `ad_audit` 7 · `saas_okta_raw` 5 · `ivanti_ics_raw` 5 · `esxi_syslog` 4 · `koi_code_scan_events` 4 · `google_workspace_audit` 4 · `msft_o365` 3. Read this as the blast radius if the names are wrong: roughly 200 queries sit on names no HTTP collector can create.

## Packs by source

All packs below: support `xsoar` (PANW-supported, author "Cortex XSOAR" unless noted), marketplaces include `marketplacev2` (XSIAM) and `platform` unless noted. Version is `currentVersion` in `pack_metadata.json`.

### Okta (`Packs/Okta`, v3.4.11)
- Marketplace: https://cortex.marketplace.pan.dev/marketplace/details/Okta/ (fetched). Certified, supported by Cortex, XSOAR + XSIAM.
- Ships: modeling rules (3 versions: 1.3, 2.0, 2.11; the 2.11 one adds `xdm.*.identity.*`), 1 parsing rule, classifiers, playbooks, integrations (Okta v2, Okta IAM, Okta Event Collector). Dashboards, reports, correlation rules: none.
- Datasets: parsing rule claims `okta_okta_raw` (vendor `okta`, product `okta`) and `okta_sso_raw` (vendor `okta`, product `sso`). Both are modelled.
- Ingest: native Okta data source (Settings > Data Sources & Integrations > Add New > Okta, API token) lands in `okta_sso_raw` (D3). The pack's Okta Event Collector integration lands in `okta_okta_raw`. D3 also says the native path normalises into `xdr_data` or `saas_audit_logs` and that `user.authentication.sso` events must be collected for Okta to join authentication stories.
- For CortexSim: an HTTP collector with vendor `okta` product `sso` yields `okta_sso_raw` and the pack's XDM rule applies. INFERRED, not tested: the platform-side normalisation into `xdr_data`/`saas_audit_logs`/authentication stories described in D3 belongs to the native path, so a synthetic POST may get XDM but not the story stitching.
- Dependencies: Base, CommonScripts, FiltersAndTransformers (required); CommonTypes (optional).
- Note: the older deprecated Okta collector is "deprecating soon" and replaced by the Okta connector (D3).

### Office 365 (`Packs/Office365`, v1.0.24) and Microsoft 365 email
- Ships: modeling rules (1.x and 2.11 variants) and parsing rules only. Dependency: CloudIncidentResponse. No dashboards/correlation.
- Datasets: `msft_o365_general_raw`, `_exchange_online_raw`, `_sharepoint_online_raw`, `_dlp_raw`, `_azure_ad_raw`; email collector writes `msft_o365_emails_raw` and users/groups/devices/mailboxes/rules/contacts siblings. Parsing vendor `msft`, products `O365 General`, `O365 Exchange Online`, `O365 Sharepoint Online`, `O365 DLP`, `O365 Azure AD`, `o365_emails`.
- Ingest: Settings > Data Sources > Office 365 (native collector, Management Activity API) or Microsoft 365 email collector.
- Other related packs in the repo, not read in depth: `MicrosoftManagementActivity` and `Office365AndAzureAuditLog` (XSOAR integrations, no rules).

### Microsoft Entra ID / Azure Logs (`Packs/MicrosoftEntraID`, v1.0.35)
- Ships: modeling + parsing rules. Dependencies: AzureFirewall, AzureWAF, AzureKubernetesServices, AzureAppService, AzureDevOps, AzureFlowLogs.
- Datasets: `msft_azure_ad_raw` (sign-ins) and `msft_azure_ad_audit_raw` via the Office 365 data source with "Azure AD Authentication Logs" and "Azure AD Audit Logs" ticked under Microsoft Graph API; `msft_azure_raw` for the Azure Event Hub path (SignInLogs, AuditLogs, risky users, activity log). Parsing: vendor `msft`, products `Azure AD`, `Azure AD Audit`, `Azure`.
- Companion pack `AzureEventsHub` (v1.0.14) models `msft_azure_raw` (vendor `MSFT`, product `Azure`) and ships 4 XDM models (Endpoint, Network, Auth, Audit, Email variants).

### Microsoft Windows Logs (`Packs/MicrosoftWindowsEvents`, v1.1.33)
- Ships: modeling + parsing rules, 1 XSIAM dashboard. Dependencies: MicrosoftDNS, MicrosoftADFS, MicrosoftWindowsAMSI.
- Dataset `microsoft_windows_raw` (vendor `microsoft`, product `windows`). Supports Security, Firewall, System, Application, PowerShell; excludes Sysmon, DNS, ADFS, AMSI (separate packs; `MicrosoftWindowsSysmon` gives `microsoft_sysmon_raw`).
- Ingest: Broker VM Windows Event Collector, or XDR Collector (Winlogbeat profile). D1 adds that Windows agents also write `microsoft_windows_raw`.

### AWS - CloudTrail (`Packs/AWS-CloudTrail`, v1.1.21) and Google Cloud Logging (`Packs/GoogleCloudLogging`, v1.0.34)
- AWS: modeling (plain + 2.11) and parsing; dataset `amazon_aws_raw` (vendor `amazon`, product `aws`); ingest via the Amazon S3 data source. Marketplace id AWSCloudTrail.
- GCP: datasets `google_cloud_logging_raw` and `google_dns_raw`; parsing vendor `Google`, products `Cloud Logging`, `Cloud_Logging`, `DNS`.
- D1: Azure Event Hub and GCP audit logs are also normalised into `cloud_audit_logs`; EKS audit via CloudWatch likewise (CloudWatch page excerpt only).

### Kubernetes (`Packs/Kubernetes`, v1.0.8; marketplaces `marketplacev2`, `platform`)
- Modeling + parsing; dataset `kubernetes_kubernetes_raw` (vendor `kubernetes`, product `kubernetes`). Ingest: Broker VM Syslog collector with those vendor/product values.
- Server-side steps (README): create the audit policy, mount it into the kube-apiserver pod, set `--audit-policy-file` and `--audit-log-path`, and send timestamps in UTC ending in `Z`.

### Proofpoint TAP (`Packs/ProofpointTAP`, v1.3.3)
- Modeling (plain + 1.3) and parsing, classifiers, layouts, playbook, integration. Dataset `proofpoint_tap_raw` (vendor `Proofpoint`, product `Tap`). Ingest: the pack's event-collector integration against the TAP API (README: fetches message and click events). No correlation rules or dashboards.

### Cisco ASA (`Packs/CiscoASA`, v2.0.9)
- Modeling (1.4, 2.11) + parsing. Dataset `cisco_asa_raw` (vendor `cisco`, product `asa`). Ingest: Broker VM Syslog app, Vendor `Cisco`, Product `ASA`, UDP or TCP; ASA `logging host ... format emblem`. Supports a long list of ASA message IDs (106xxx, 302xxx, 710xxx...).

### Ivanti Connect Secure (`Packs/IvantiConnectSecure`, v1.0.10; marketplaces `marketplacev2`, `platform`)
- Modeling + parsing; dataset `ivanti_connect_secure_raw`. Ingest: Broker VM Syslog (UDP), WELF filter recommended on the appliance (System > Log/Monitoring).

### VMware ESXi (`Packs/VMwareESXi`, v1.0.21) and vCenter (`Packs/VMwareVCenter`, v1.0.29)
- ESXi: modeling (1.3, 2.9, 2.11) + parsing; dataset `vmware_esxi_raw`. Ingest: ESXi `Syslog.global.loghost` to the Broker VM Syslog app. The registry's `esxi_syslog` cards also read vSphere VM-creation events; those belong to vCenter: `vmware_vcenter_raw` (vendor `vmware`, product `vcenter`). INFERRED from the card text in the registry detail line.

### KOI (`Packs/Koi`, v1.2.8)
- Modeling rule only (no parsing rule file; the integration sets vendor `koi` product `koi`), integration with API key (needs `xt-Administrator` role to create). Dataset `koi_koi_raw`; event types Audit and Alerts (Behavior Analytics, Addressable, Detect and Protect). Created 2026-03-26 per metadata.

### Google Chrome (`Packs/GoogleChrome`, v1.1.0; marketplaces `marketplacev2`, `platform`)
- Modeling, parsing, **4 correlation rules**, 1 dashboard. Dataset `google_workspace_chrome_raw`. Fed by the Google Workspace data source. This is Chrome Enterprise telemetry, not Prisma Browser.

### Prisma Browser
- No pack in the public repo. Dataset `panw_prisma_access_browser_raw` (D1). Admin docs (fetched, `Explore Prisma Browser Events`) say events can be forwarded to a SIEM when SIEM integration is enabled; the Cortex doc page for the "Prisma Browser Integration" collector was not fetched. NOT FOUND for the exact path in Cortex.

### Duo (not in the registry but emitted by `duo_auth_emitter`, dataset `duo_auth_raw`)
- `DuoAdminApi` pack models `duo_duo_raw` (vendor `duo`, product `duo`). `DuoAuth` is a community pack (author Mihovil Magdic) with no data rules. The emitter's `duo_auth_raw` name does not match `duo_duo_raw`. Not in the registry, so out of the required comparison, but the same defect.

## Correlation rules a DC can enable

The public Marketplace ships very few correlation rules. Counted across the whole tree (`Packs/*/CorrelationRules/`): 10 rule files in 6 packs. None cover Okta, Entra, Microsoft 365, Windows, cloud audit, Kubernetes, Proofpoint, Cisco, Ivanti, ESXi or KOI. Detection content for those sources comes from the XSIAM analytics/ABIOC engines and built-in rules, which are out of scope here.

| Pack | Rule | Fires on | ATT&CK | Maps to repo scenario? |
|---|---|---|---|---|
| GoogleChrome | Chrome - Chrome Extension Install Event (real-time, Low) | `google_workspace_chrome_raw` `BROWSER_EXTENSION_INSTALL` | T1176 | Same technique as `SIM-BROWSER-003` and `SIM-KOI-004`, **but the data source is Chrome Enterprise, not Prisma Browser or VS Code**. Will not fire on our simulations. |
| GoogleChrome | Chrome - Known Malicious Site Visit (Medium) | `UNSAFE_SITE_VISIT` | none | Behaviour overlaps BROWSER malicious-site sims; wrong source. |
| GoogleChrome | Chrome - Known Malware Downloaded (Medium) | `MALWARE_TRANSFER` | T1204.002 | Overlaps `T1204.002` scenarios; wrong source. |
| GoogleChrome | Chrome - User Phished and/or Password Re-use/Breach (Medium) | `UNSAFE_SITE_VISIT` and related | T1566, T1078 | Overlaps phishing sims; wrong source. |
| MicrosoftDefenderAdvancedThreatProtection | Malware detected by Microsoft Defender for Endpoints | `alerts` dataset | EXECUTION | Needs a Defender feed; none in CortexSim. |
| Dropbox | DropBox - Massive File Downloads / Massive File Alterations | Dropbox events | EXFILTRATION | No Dropbox scenario. |
| GitLab | Gitlab - User Permission Changed | GitLab audit | PRIVILEGE_ESCALATION | No GitLab scenario. |
| CyberArkIdentity | CyberArk Failed Logins | CyberArk | CREDENTIAL_ACCESS | No CyberArk scenario. |
| AlibabaActionTrail | multiple unauthorized actions by a user | Alibaba ActionTrail | EXECUTION | No Alibaba scenario. |

Net: a DC cannot use Marketplace correlation rules instead of authoring for any CortexSim identity, email, cloud, Windows or firewall scenario. The cards' own BIOC/XQL/correlation content stays the only content, and each only fires if the dataset name above is right. Rule bodies were read from `Packs/GoogleChrome/CorrelationRules/*.yml`.

Other pack content a DC may want: dashboards in `GoogleChrome` and `MicrosoftWindowsEvents` (34 XSIAM dashboard files across the repo, 14 report files in 1 pack); `CortexInsights` (community, PANW Technical Services) ships Cortex Insights dashboards for environment health. None are scenario-specific.

## First-party packs

All `support: xsoar`, author Cortex XSOAR unless noted. Folder, pack name, platforms.

| Folder | Name | Platforms | Useful for POV content |
|---|---|---|---|
| PAN-OS | PAN-OS by Palo Alto Networks | xsoar, marketplacev2, xpanse, platform | Firewall and Panorama management and Prisma Access via Panorama; playbooks and layouts. No log modeling rules (NGFW logs arrive through Strata Logging Service as `panw_ngfw_*_raw`). |
| PrismaAccess | Strata Cloud Manager | xsoar, marketplacev2, platform | Prisma SASE queries and actions; playbooks. |
| CortexDataLake | Strata Logging Service | xsoar, platform | Log query connector. |
| PANOStoCDLMonitoring | PAN-OS to Strata Logging Service Monitoring | community (Brice RENAUD), xsoar only | Job that checks a firewall's log upload to SLS; useful to prove the NGFW feed is live before a run. |
| PaloAltoNetworksAIOps | Palo Alto Networks AIOps | xsoar, marketplacev2, platform | Best Practice Assessment of NGFW/Panorama configs. |
| Palo_Alto_Networks_WildFire | WildFire | xsoar, marketplacev2, platform | Verdict lookups; relevant to the WildFire-verdict sims. |
| Palo_Alto_Networks_Enterprise_DLP | Enterprise DLP | xsoar, marketplacev2, platform | Author "Palo Alto Networks Enterprise DLP". |
| PaloAltoNetworks_IoT | IoT | xsoar, marketplacev2, platform | Not used by the registry. |
| PrismaCloud | Prisma Cloud | xsoar, marketplacev2, xpanse, platform | Modeling + parsing, dataset `prisma_cloud_raw`; also a Trigger. Closest pack to the `cortex_cloud_posture` area. |
| PrismaCloudCompute | Prisma Cloud Compute | xsoar, marketplacev2, platform | Dataset `prisma_cloud_compute_raw` per D1. |
| PaloAltoNetworks_Prisma_AIRs | Prisma AIRS AI Security | community (Eric Partington) | Integration for runtime scanning and red teaming. Not first party and has no data rules, so it does not feed `cortexsim_airs_raw`. |
| CortexAttackSurfaceManagement | Cortex Attack Surface Management | marketplacev2, xpanse | ASM playbooks, layouts and triggers (many dependencies: PAN-OS, ServiceNow, AWS/Azure/GCP enrichment). For the `asm_findings` plane. |
| CortexXpanse | Cortex Xpanse | xsoar | Older ASM content. `ExpanseV2` is deprecated. |
| CortexExposureManagement / CortexVulnerabilityManagement | Cortex Exposure/Vulnerability Management | platform / marketplacev2 | Exposure content. |
| Unit42ThreatIntelligencebyPaloAltoNetworks | Unit 42 Threat Intelligence | xsoar, marketplacev2, platform | Replaces deprecated `Unit42Intel`, `FeedUnit42v2`, `FeedUnit42`. Supplies the indicators the `threat_intel` dataset reads (feed connection not confirmed against the registry). |
| ThreatIntelligenceManagement | Threat Intelligence Management | xsoar, marketplacev2, platform | TIM pack; no playbook list read. |
| Palo_Alto_Networks_Threat_Vault | Threat Vault | xsoar, marketplacev2, platform | Threat lookups. |
| CortexResponseAndRemediation | Cortex Response and Remediation | marketplacev2, platform | Automated response playbooks. |
| PANWComprehensiveInvestigation | Comprehensive Investigation | xsoar, marketplacev2, platform | Investigation playbooks across PANW products. |
| CortexXDR | Cortex XDR by Palo Alto Networks | xsoar, xpanse, platform | XDR incident automation and layouts. |
| XDRBestPracticeAssessment | XDR Best Practice Assessment | community (Lauren Lee) | Assessment incident type and playbook. |
| Cortex911 | Cortex 911 | community (PANW Sales) | Not read in detail. |
| XSIAMCompliance_* | GDPR, HIPAA, ISO 27001, NIST 800-171/800-53/CSF, PCI DSS, SOX | marketplacev2 | Listed in the pack tree; not opened. |

Breach and attack simulation packs, in case a DC wants a comparison to CortexSim: `AttackIQFireDrill` (AttackIQ Platform, xsoar-supported), `SafeBreach` (partner), `MitreCaldera` (community), `Cryptosim` (partner SIEM, not BAS). Test-data generators: `sampleSiem` (random incidents), `JSONSampleIncidentGenerator` (community). No pack in the repo is named POV, demo or lab apart from `Cortex911` and the CTF packs (`CTF01`, `CTF02`).

## Findings that change the registry or the cards

1. **Most emulated sources point at dataset names that no ingestion path can create.** 17 of 25 non-first-party names do not match any pack or documented dataset. For example `okta_sso` should be `okta_sso_raw`; `msft_windows_security` should be `microsoft_windows_raw`; `msft_azure_ad_signin` should be `msft_azure_ad_raw`; `prisma_browser_events` should be `panw_prisma_access_browser_raw`; `kubernetes_audit_logs` should be `kubernetes_kubernetes_raw`.
2. **The emitter `dataset` body field does not route.** The DC chooses Vendor and Product on the HTTP collector (D2). The runbook for each emitter needs a vendor/product pair that yields the pack's dataset: Okta `okta`/`sso`, Windows `microsoft`/`windows`, Entra `msft`/`Azure AD`, Kubernetes `kubernetes`/`kubernetes`, ESXi `VMware`/`ESXi`, Ivanti `ivanti`/`connect_secure`, Proofpoint `Proofpoint`/`Tap`, Cisco `cisco`/`asa`. Each is read from the pack's parsing rule INGEST line.
3. **`cloud_audit_logs`, `saas_audit_logs` and the `panw_ngfw_*` names are platform-produced.** 135 + 15 query occurrences sit on the first two. An HTTP collector cannot create those names (INFERRED from D2). What reaches them is the raw dataset plus platform normalisation, so synthetic cloud-audit records may land only in `<vendor>_<product>_raw`. The route to `cloud_audit_logs` for a synthetic record is NOT FOUND in public docs.
4. **KOI cards read fields the KOI pack does not carry.** The pack's `koi_koi_raw` is alert/audit-shaped; `artifact_type`, `manifest_host_permissions` and `signature_valid` are not in its schema.
5. **No Marketplace correlation rule substitutes for authored content** in any identity, email, cloud, endpoint-log or firewall scenario (see above); the Chrome rules match on the wrong source.
6. **vCenter vs ESXi.** VM-creation events belong to the vCenter pack's dataset, not `vmware_esxi_raw`.

## Sources

Fetched in full or in part:
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/reference-and-developer-docs/cortex-agentix-xql/get-started-with-xql/datasets-and-presets (D1)
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Set-up-an-HTTP-log-collector-to-receive-logs (resolves to the cortex-docs page `.../http-log-collector/set-up-an-http-log-collector-to-receive-logs`) (D2)
- https://cortex-docs.paloaltonetworks.com/cortex-xdr/configure-cortex-xdr/cortex-xdr-data-sources/vendor-specific-data-sources/okta/ingest-logs-and-data-from-okta (D3)
- https://cortex.marketplace.pan.dev/marketplace/details/Okta/
- https://docs.paloaltonetworks.com/content/techdocs/en_US/prisma-browser/administration/investigate-prisma-browser-events (Explore Prisma Browser Events; arrived through the marketplace listing URL redirect)
- https://github.com/demisto/content (commit 05e3556f882a6238ecf1028beb9b832d8ebb4572, paths under `Packs/`: Okta, OktaAuth0, Office365, MicrosoftEntraID, AzureEventsHub, MicrosoftWindowsEvents, MicrosoftWindowsSysmon, AWS-CloudTrail, GoogleCloudLogging, GoogleChrome, Kubernetes, ProofpointTAP, CiscoASA, IvantiConnectSecure, VMwareESXi, VMwareVCenter, Koi, DuoAdminApi, plus `pack_metadata.json` for the first-party table and the CorrelationRules files listed above)

Search-result excerpts only (page not fetched, treat as secondary):
- https://cortex.marketplace.pan.dev/marketplace/details/AWSCloudTrail/ (dataset `amazon_aws_raw`)
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Ingest-logs-from-Amazon-CloudWatch (EKS audit to `cloud_audit_logs`)
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Ingest-detection-data-from-Strata-Logging-Service
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Ingest-data-from-Prisma-Access

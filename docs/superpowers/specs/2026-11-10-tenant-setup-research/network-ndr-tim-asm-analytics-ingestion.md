# Tenant-side setup: NDR, TIM, ASM, ANALYTICS and the collector-POST ingestion path

Researched 2026-11-10. Docs are Cortex XSIAM 3.x on cortex-docs.paloaltonetworks.com unless noted. Evidence tags: DOCUMENTED (URL fetched, listed under Sources), INFERRED (reasoning, no URL), NOT FOUND (looked, public docs do not say). Secondary sources (live.paloaltonetworks.com) are labelled "secondary" and counted as INFERRED.

Repo grounding (read-only): NDR 12 scenarios, TIM 9, ASM 6, multi_plane 38 files. Datasets the cards read (counted from `detection_scanner/ttps`): NDR `panw_ngfw_traffic_raw` (most), `pan_dns`, `cisco_asa_raw`, `xdr_data`, `network_story`; TIM `threat_intel`, `panw_ngfw_traffic_raw`, `pan_dns`, `asm_findings`; ASM `asm_findings`, `panw_ngfw_traffic_raw`, `panw_ngfw_threat_raw`, `cloud_audit_logs`; multi-plane `xdr_data` 63, `panw_ngfw_traffic_raw` 33, `panw_ngfw_threat_raw` 20, `cloud_audit_logs` 12. 59 of 74 multi-plane/NDR step-streams use the `ngfw_eal_emitter` / `ngfw_threat_emitter` collector-POST path, so the Cross-cutting section decides most of the result.

---

## NDR

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| NGFW or Panorama device connected to the tenant, log types selected (or Select all) | XSIAM › Settings › Data Sources & Integrations › + Add New › NGFW › Add NGFW Device / Add Panorama Device › Select log types | ingest | one-time tenant | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/next-generation-firewall/ingest-data-from-next-generation-firewall | log_forwarding |
| Firewall pushes logs to Strata Logging Service / Cloud Logging: certificate (PAN-OS 10.1+, one per firewall, from the support portal) or PSK (10.0-); cloud logging licence pulled; Panorama Cloud Services plugin if Panorama-managed | NGFW › Device › Setup › Management › Cloud Logging › Enable Strata Logging Service | ingest | per-firewall; allow an hour or more for cert approval | DOCUMENTED (same page) | log_forwarding |
| Log Forwarding profile defined and mapped to the Security rule the sim traffic matches; push config | NGFW › Objects › Log Forwarding; Policies › Security rule | ingest | per-rule | DOCUMENTED (same page, steps 10-14) | log_forwarding |
| Enhanced Application Logging on globally AND per rule ("Enable enhanced application logs in cloud logging (including traffic and url logs)") | NGFW › Device › Setup › Management › Cloud Logging; Objects › Log Forwarding profile | ingest + detect | per-firewall plus per-rule | DOCUMENTED https://docs.paloaltonetworks.com/ngfw/administration/subscriptions/enhanced-application-logs ; XSIAM page above step 8 | enhanced_application_logs |
| EAL needs a Strata Logging Service subscription; User-ID recommended for the fullest set | NGFW | ingest | licence | DOCUMENTED (EAL page) | enhanced_application_logs |
| Analytics detectors that read EAL: DNS Tunneling requires "Palo Alto Networks Firewall EAL Logs OR XDR Agent" | Analytics alert reference | detect | per-detector | DOCUMENTED https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/dns-tunneling.md | enhanced_application_logs |
| Traffic-log detectors (Port Scan, Failed Connections, crypto miner, Large Upload HTTPS) require "PAN firewall traffic logs" (or third-party firewall / XDR agent as listed) | Analytics alert reference | detect | per-detector | DOCUMENTED https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/port-scan.md , .../a-possible-crypto-miner-was-detected-on-a-host.md , .../large-upload-https.md | log_forwarding |
| Allow rule matches the sim flow and logs at session end | NGFW › Policies › Security › Actions › Log at Session End | ingest | per-POV | DOCUMENTED for "Log at Session End" (DNS Security page); the allow rule itself INFERRED (a deny rule produces no app-level session) | allow_rule_for_sim_traffic |
| Anti-Spyware profile with DNS Policies (per signature source: log severity + action) attached to the rule; DNS Security needs its own licence on top of Threat Prevention; threat log filter `action eq sinkhole` | NGFW › Objects › Security Profiles › Anti-Spyware › DNS Policies | ingest (threat/DNS rows) + prevent | licence + per-rule | DOCUMENTED https://docs.paloaltonetworks.com/dns-security/activation-and-onboarding/enable-dns-security | threat_profile_attached, dns_security_logged |
| URL logs: Cortex lists URL logs as a forwardable type; EAL itself causes URL Filtering log entries for allowed categories | NGFW log types | ingest | per-rule | DOCUMENTED (XSIAM ingest page lists `panw_ngfw_url_raw`; EAL page states the side effect). A URL Filtering profile on the rule is INFERRED | url_filtering_logged |
| Decryption so App-ID and HTTP fields exist for C2 / bulk-HTTPS (ndr-001, 005, 008, 011, 012) | NGFW › Policies › Decryption | detect | per-POV | INFERRED (without decryption only SNI/cert and byte counts exist). Docs only confirm DNS-over-TLS (853) decryption for DNS Security | decryption_for_app_id |
| Analytics Engine enabled with network parameters set (see ANALYTICS) | Settings › Configurations › Cortex XSIAM - Analytics | detect | one-time | DOCUMENTED (see ANALYTICS) | analytics_engine_enabled (new) |
| Cisco ASA (ndr-010): ASA `logging host ...`; Broker VM Syslog applet Vendor=Cisco Product=ASA; Cisco ASA marketplace pack (parsing + modeling rules) → `cisco_asa_raw` | Settings › Configuration › Data Broker › Broker VMs › APPS › Syslog | ingest | one-time + per-device | DOCUMENTED https://cortex.marketplace.pan.dev/marketplace/details/CiscoASA/ | log_forwarding |
| Native NGFW integration (not a custom collector) so records are normalized and stitched | Data Sources & Integrations | detect | one-time | DOCUMENTED: Analytics, IOC and BIOC issues are only generated on normalized logs; IOC and BIOC only on stitched data, not raw (ingest page) | native_ngfw_integration (new) |

### NDR: timing and volume
- Analytics detectors on network data: Activation 14 days, Training 30 days for every one fetched (Port Scan 1 h test; DNS Tunneling 10 min test, "10 KB or more encoded in subdomain names"; crypto miner single event; Large Upload HTTPS 1 day test; Abnormal SMB scanning 20 min test). DOCUMENTED (alert pages above, plus intervals page https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-detection-time-intervals.md). A one-day POV cannot show a fresh-tenant Analytics detector; ABIOC and Analytics scenarios (ndr-008/009/011/012) need a tenant already ingesting the firewall for ~30 days.
- Baseline floor for the engine: EDR or network logs from at least 30 endpoints over at least 2 weeks. DOCUMENTED (Enable the Analytics Engine page).
- Firewall onboarding lag: certificate approval and licence appearance "an hour or longer". DOCUMENTED.
- Daily per-log-type quota is tracked on the NGFW Ingestion Dashboard; EAL is non-billable. DOCUMENTED (ingest page; analytics-sensors page).
- Dataset naming: docs name `panw_ngfw_traffic_raw`, `panw_ngfw_threat_raw`, `panw_ngfw_url_raw`, `panw_ngfw_globalprotect_raw`, `panw_ngfw_system_raw`. Cards also read `pan_ngfw_traffic` (8 multi-plane cards, ndr-001) and `pan_dns` (ndr-009, tim-003, tim-008, 6 multi-plane cards). NOT FOUND in public docs; DNS lives in EAL/threat records per docs, so confirm both names in a tenant before the registry marks them verified.

### NDR: scenario gotchas
- SIM-NDR-001, SIM-TIM-001 (`c2_http_beacon`, `--live`) generate real traffic. It only becomes `panw_ngfw_*` rows if it crosses a logging firewall. The NDR IaC module's default pattern (`external_ngfw_forward`) only provisions a collector; the DC's firewall must be in path and forwarding. INFERRED from `infra/modules/aws/ndr/README.md`.
- SIM-NDR-012 and the `ngfw_eal_emitter` family do not touch a firewall. They POST to a custom HTTP collector, so data is not the native NGFW dataset (see Cross-cutting). Analytics ABIOCs defined on `panw_ngfw_traffic_raw` will not see it unless the dataset is modeled into XDM.
- BIOC/IOC on NGFW data fire only on stitched data. Cards whose BIOC reads raw `panw_ngfw_traffic_raw` (ndr-001..007 carry exactly one BIOC each) cannot fire. DOCUMENTED (ingest page note: "IOC and BIOC issues are applicable to stitched data only, and are not available on raw data").
- SIM-NDR-005 sends bulk HTTPS: Large Upload (HTTPS) has a 1-day test period and 30-day training; a single burst will not raise it on a young tenant.

### NDR: report vs block
- Anti-Spyware DNS Policies set to `block` or `sinkhole`: block drops the query (no resolution, no C2 session, tunnel yields no further queries); sinkhole forges an answer so the client later hits the sinkhole IP. Default for PAN content signatures is sinkhole; cloud DNS Security defaults to allow. DOCUMENTED (DNS Security page). Run for ndr-002/009 must say "prevented" when the threat log shows action block/sinkhole and traffic log shows no follow-on session.
- Vulnerability Protection and Anti-Spyware reset/drop actions on ndr-003/004: behaviour per action INFERRED; the profile-action table was not fetched.

---

## TIM

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| Threat Intel Management entitlement: XSIAM Premium, or any XSIAM licence with the TIM add-on | licence | detect | licence/add-on | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management/get-started-with-threat-intel-management.md | tim_license (new) |
| A feed integration brings indicators in: TAXII 2 Feed (TAXII 2.0 and 2.1) with Server URL (discovery), Collection, API Root, auth, Feed Fetch Interval, Indicator Reputation, First Fetch Time. mocktaxii must be reachable from the tenant or an engine | Data Sources & Integrations › add TAXII 2 Feed instance | ingest | one-time | DOCUMENTED params https://xsoar.pan.dev/docs/reference/integrations/taxii-2-feed ; integrations fetch indicators from sources such as TAXII, CSV or JSON: https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Indicator-concepts . XSIAM menu path to add the instance not fetched; network reachability (public :9000 or engine) INFERRED | api_integration |
| Indicators exist in the TIM database before any rule is built | Threat Management › Threat Intelligence › Indicators | detect | per-POV | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management/indicator-configuration/generate-issues-from-indicators-using-indicator-rules-for-prevention-and-detection.md | api_integration |
| Detection Indicator Rule created and Enabled; targets File, Domain or IP indicators; `Detectable=True`, `Status=Active` fixed; raises a Threat Intelligence-source issue ("Detected") | Threat Management › Detection Rules › Indicator Rules › Add Rule › Detection Rule | detect | per-POV | DOCUMENTED (same page) | indicator_rule_enabled (new) |
| Ingested indicators are passive: they do not by themselves fire Analytics or BIOC; Indicator Rules, or XQL/correlation on the indicators dataset, are the alert path | Detection Rules | detect | per-POV | INFERRED (secondary: https://live.paloaltonetworks.com/t5/cortex-xsiam-discussions/xsiam-threat-intelligence-management-module/td-p/1254103 , community answer; official page says only that rules raise issues on a match) | indicator_rule_enabled |
| Events to match must be ingested: IP/domain matches need firewall or DNS rows in the tenant (see NDR) | NGFW ingest | detect | per-POV | INFERRED | log_forwarding |
| Prevention via indicator rule is for File (SHA256/MD5) indicators only, through a Restrictions profile with Custom Indicator Prevention Rules = Enabled; IP/domain have detection only | Inventory › Endpoints › Policy Management › Prevention › Profiles; Detection Rules › Indicator Rules › Add Rule › Prevention Rule | prevent | per-POV | DOCUMENTED (rules page) | indicator_prevention_profile (new) |
| Enforcing an IP/domain indicator at the firewall (tim-007) = export indicators as an External Dynamic List via the Generic Export Indicators Service (long-running integration) and have the NGFW consume it | Indicators › Export; integration instance | prevent | per-POV | DOCUMENTED export path https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management/indicator-configuration/export-indicators.md ; NGFW EDL consumption and refresh interval INFERRED | edl_export_configured (new) |

### TIM: timing and volume
- Feed fetch interval is a per-instance parameter; no default stated on the page fetched. NOT FOUND.
- After an Indicator Rule is enabled, time to first issue: NOT FOUND. Retro-hunt of events ingested before the rule existed: NOT FOUND.
- Unit 42 feed is included; third-party feeds are separate commercial agreements. INFERRED (secondary, same community post).

### TIM: gotchas
- `threat_intel` (read by tim-001/002/004/005/006/007/008/009 cards and asm-006) is not a name public docs use. Docs and the community answer refer to an `indicators` dataset ("platform version 3.2+"). NOT FOUND for `threat_intel`; INFERRED that the cards' XQL on `threat_intel` returns nothing. Verify `dataset = indicators` in the tenant and fix the registry row.
- `infra/modules/aws/tim` plants IOC names in a Route53 PRIVATE zone. Only hosts inside the VPC resolve them, so a firewall sees the queries only if DNS goes through it. INFERRED.
- SIM-TIM-007 depends on an EDL round-trip (promote then withdraw). Needs the export integration running and the NGFW EDL refresh; neither is covered by `infra/modules/aws/tim`.
- SIM-TIM-003/008 read `pan_dns` (see NDR dataset-name note).

### TIM: report vs block
- Hash indicators can be blocked at the endpoint (Restrictions profile). Domain/IP can only be blocked through the firewall EDL path above. Runs that expect "prevented" on a domain indicator with only an Indicator Rule configured will read "detected", not "prevented". DOCUMENTED.

---

## ASM

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| ASM entitlement: included in XSIAM Premium; any other XSIAM licence needs the ASM add-on. Cortex Cloud External Surface inventory "requires the ASM add-on" | licence | detect | licence/add-on | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/attack-surface-management.md ; https://cortex-docs.paloaltonetworks.com/cortex-cloud-posture-management/cloud-inventory-and-environment/asset-classes/external-surface-assets.md | asm_license (new) |
| Asset is attributed to the org. Reasons: IP range registered to org (ARIN/RIPE/APNIC/LACNIC/AFRINIC), ASN advertisement, certificate, DNS record, self-provided list; cloud assets via domain/certificate observations plus AWS/Azure/GCP integrations | Xpanse › Network mapping | detect | per-POV | DOCUMENTED https://docs-cortex.paloaltonetworks.com/r/Cortex-XPANSE/2/Cortex-Xpanse-Expander-User-Guide/Network-mapping ; https://cortex-docs.paloaltonetworks.com/cortex-xpanse/inventory/asset-attribution.md | asset_scope_configured |
| Self-provide scope: upload CSV (BusinessUnits, AssetType = Domain or IP_Range, Asset); max 500 assets per request; reviewed within 5 days (Accepted/Rejected); IPv4 only, no certificates or IPv6; Instance Administrator role | XSIAM › Settings › Configurations › Asset Management › Asset Uploads/Removals › Asset Upload/Removal › Add Asset(s) | detect | per-POV (5-day lead) | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/attack-surface-management/attack-surface-assets.md | asset_scope_configured |
| A cloud (AWS) public IP is REJECTED as an uploaded IP_Range ("we do not add these asset types to customer maps"). The lab EC2 must be attributed by domain, subdomain or a TLS certificate that attributes to the org, or by the cloud integration | same page, "Rejection reasons" | detect | per-POV | DOCUMENTED (same page) and https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/attack-surface-management/asm-enrichment-of-cloud-assets.md (managed vs unmanaged cloud) | asset_scope_configured |
| Attack Surface Rules enabled (most are default-disabled; opt-in); severity editable (Low/Medium/High default, Critical settable); "Unmanaged Cloud Service" rule is enabled by default | Xpanse › Rules › Attack Surface Rules (right-click Enable) | detect | one-time | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xpanse/attack-surface-rules.md ; ASM-enrichment page for the default-on rule | attack_surface_rules_enabled (new) |
| Known Assets Monitoring opt-in (daily deeper port and TLS scanning on attributed assets); requires validating ranges and unblocking scanner IPs | contact Customer Success | detect | per-tenant, consent | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xpanse/learn-about-cortex-xpanse/scanning/known-assets-monitoring.md | kam_opt_in (new) |
| Issues appear under Cases & Issues; filter `Attack Surface Rule ID`; external IP ranges under Inventory › Assets › Network Configuration › IP Address Ranges › External IP Address Ranges | XSIAM | detect | n/a | DOCUMENTED (ASM-enrichment page; attack-surface-assets page) | asset_scope_configured |

### ASM: timing and volume
- Global Base discovery: twice a week, about 250 common ports, all IPv4. Global Extended: low background rate for the other 65k ports. KAM Base daily about 300 ports on attributed assets; KAM Extended weekly about 2800 ports. Monitoring scans daily on responsive services. Attack Surface Testing daily on configured services. DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xpanse/learn-about-cortex-xpanse/scanning/scanning-cadences.md
- A newly enabled rule yields alerts within 24 h if instances exist; disabling does not close open alerts. DOCUMENTED.
- Registry data updates about biweekly. DOCUMENTED (Network mapping).
- Net: a lab stood up the morning of the POV will not be found on the same day unless it is already attributed AND KAM is on. Twice-weekly global scans plus a possible 5-day upload review make "within a POV" a plan, not a default. Ports 2222, 6379, 9001, 9200 in the lab module: whether each is inside the ~250 Global Base set is NOT FOUND (port list not published).

### ASM: gotchas
- `asm_findings` is not a name in the ASM docs; findings surface as Issues and as `External Surface` inventory. NOT FOUND for the dataset. Cards for asm-001..006, tim-006, tim-008 and mp scenarios query it.
- SIM-ASM-002/003/004 run active scans from a host (nmap, nuclei, sqlmap and so on). The ASM service does not generate those rows; the `panw_ngfw_traffic_raw` / `panw_ngfw_threat_raw` hits need a firewall in path (see NDR). INFERRED.
- SIM-ASM-001 reads `cloud_audit_logs` (AWS CloudTrail onboarding needed, see Cross-cutting).
- SIM-ASM-006 reads `okta_sso` and `threat_intel`; both need their own setup.
- KAM scans from Xpanse source IPs will hit the lab host. Any IPS or anti-scanning rule in front must not block them. DOCUMENTED.

### ASM: report vs block
- ASM is detection/exposure only; there is no prevent path for the lab host. Where a scanner like nmap/nuclei trips an NGFW Vulnerability or Anti-Spyware profile in `reset`/`drop`, the NGFW log (not ASM) is where "prevented" shows (see NDR).

---

## ANALYTICS

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| Analytics Engine enabled (baseline creation "can take up to three hours") | XSIAM › Settings › Configurations › Cortex XSIAM - Analytics › Enable | detect | one-time | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics/enable-the-analytics-engine-and-identity-analytics.md | analytics_engine_enabled (new) |
| Minimum data: EDR or network logs from at least 30 endpoints over at least 2 weeks, OR cloud audit logs over at least 5 days | same page | detect | per-tenant | DOCUMENTED (same page) | analytics_engine_enabled |
| Internal IP ranges and internal domain suffixes defined | Inventory › Assets › Network Configuration › Internal IP Address Ranges / Internal Domain Suffixes | detect | one-time | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics/configure-cortex-xsiam-network-parameters.md | network_parameters_configured (new) |
| Identity Analytics: Cloud Identity Engine set up, toggle on | Settings › Configurations › Cortex XSIAM - Analytics | detect | one-time | DOCUMENTED (Enable page; prerequisites on https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics.md) | identity_analytics_enabled (new) |
| Sensors the engine reads: PAN firewall traffic logs, EAL, GlobalProtect/Prisma Access, URL logs, XDR agent data (needs XSIAM Pro per Endpoint, agent 6.0+), Directory Sync (CIE), third-party firewalls (Check Point, Fortinet, Cisco ASA via syslog), Entra/Okta/PingOne auth logs, Windows Event Collector on Broker VM | various | detect | per-source | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-sensors.md | log_forwarding |
| Analytics rules enabled (each detector can be disabled; severity editable; edits survive content updates) | Threat Management › Detection Rules › Analytics Rules (right-click Enable/Disable/Edit) | detect | one-time | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/view-and-manage-analytics-rules.md | analytics_rules_enabled (new) |
| Correlation rule enabled and imported: all authored correlation rules are tenant-created or imported; Create adds as active unless Disable chosen | Threat Management › Detection Rules › Correlations › + Add Correlation / Import (JSON) | detect | per-POV | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md | correlation_rule_enabled (new) |
| Correlation scheduling: Real Time (stages limited to dataset, datamodel, filter, alter, fields, config case_sensitive; must contain a filter) or Scheduled (every 10/20/30 min, hourly default, daily, custom cron; minimum every 10 min; query window up to 7 days) | same | detect | per-rule | DOCUMENTED (same page) | correlation_rule_enabled |
| Case grouping across sources: Medium or High issues create cases; grouping needs domain Security or Posture and severity Medium or higher; Low issues create no case | same page (Case Creation Rules) | detect | per-rule | DOCUMENTED (same page) | correlation_rule_enabled |
| Stitching: native NGFW integration "stitches logs with other Palo Alto Networks-generated logs to form stories"; stitched raw data via `xdr_data` or presets such as `network_story` | NGFW ingest | detect | one-time | DOCUMENTED (NGFW ingest page) | native_ngfw_integration |
| Analytics runs on all XDM-mapped network and authentication data; auth stories need all 12 mandatory XDM fields or the event is dropped from auth stories and identity analytics | Settings › Configurations › Data Management › Data Model Rules | detect | per-dataset | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-engine.md ; https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/data-management/data-model-rules/how-to-map-authentication-story-events.md | xdm_modeling_for_analytics (new) |

### ANALYTICS: timing and volume
- Every Analytics detector has its own Activation / Training / Test / Deduplication period; documented examples are 14 d / 30 d. First possible alert is after the activation period; "most but not all detectors start after it". DOCUMENTED (intervals page).
- Baseline engine floor: 30 endpoints for 2 weeks, or cloud audit logs for 5 days. DOCUMENTED. A 4-hour burst cannot satisfy either.
- ABIOCs: tailored to the environment "after analyzing your logs"; no numeric period published. NOT FOUND.
- Hard caps: Analytics detectors and correlation rules auto-disable at 5000 or more hits in 24 h; max 1200 enabled scheduled correlations; `current_time()` is unreliable in correlations (use `time_frame_end()`); `call`, `top`, dataset wildcards unsupported in correlation. DOCUMENTED.
- Ingestion-to-detection delay for real-time rules and scheduled run offset: only the schedule grid is documented (10-minute boundaries); lag from ingest to first issue NOT FOUND.

### ANALYTICS: gotchas
- Repo plane docs and `plane-coverage.md` still say 5 multi-plane scenarios; the tree has 38 files. Not a tenant issue, but the registry rows should be driven by the cards, not the doc.
- Any multi-plane scenario whose correlation depends on Analytics (not correlation rules) will read "missed" until the activation/training periods of the underlying detector have passed, regardless of data quality. The repo's `detector-readiness` gate encodes 14 d + 30 d from the same pages (verified `port-scan`, `dns-tunneling` match).
- Auth stories explicitly exclude Kerberos, NTLM, and traditional domain logon events from on-prem infrastructure ("Scope Clarification"). SIM-MP-002, SIM-MP-016/020 style Kerberoast/DCSync chains cannot get identity-analytics coverage through `msft_windows_security` / `ad_audit` XDM auth mapping. DOCUMENTED.
- Real-time correlation cannot run `transaction`/aggregations; cards whose Correlation uses `join` or `comp` must be scheduled. INFERRED from the supported-stage list.

### ANALYTICS: report vs block
- Not applicable to the engine itself. For stitched chains, the "prevented" evidence is endpoint (XDR agent) and NGFW action, joined by the Result's `step_outcomes`; the engine only needs both rows ingested.

---

## CROSS-CUTTING INGESTION (collector-POST emitters: EMAIL, ITDR, cloud audit, M365, AD, NGFW EAL/threat)

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| One tenant HTTP Log Collector per target dataset: Name, Compression (gzip or uncompressed), Log Format (Raw, JSON, CEF, LEEF), Vendor, Product; "Save & Generate Token" | XSIAM › Settings › Data Sources & Integrations › + Add New › HTTP | ingest | one-time + per dataset | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/http-log-collector/set-up-an-http-log-collector-to-receive-logs.md | http_collector_configured (new) |
| Dataset name is derived from the collector's Vendor and Product as `<Vendor>_<Product>_raw`; logs are auto-parsed | same | ingest | per dataset | DOCUMENTED (same page) | http_collector_configured |
| The emitters' top-level `dataset` body hint does NOT choose the dataset; Vendor/Product on the collector does. One collector (token) per emitter dataset; for CEF/LEEF the row's own vendor/product wins | same | ingest | per dataset | DOCUMENTED for the CEF/LEEF override and `<Vendor>_<Product>_raw`; that the JSON body hint is ignored is INFERRED (page never mentions a body field) | http_collector_configured |
| Request contract: POST `https://api-{tenant external URL}/logs/v1/event`, header `Authorization: {api_key}` (raw key, no scheme), `Content-Type: application/json` (JSON) or `text/plain`; records newline-separated; body up to 10 MiB (1 MiB recommended), record under 5 MB | HTTP collector page | ingest | per request | DOCUMENTED (same page) | http_collector_configured |
| Status codes: 200 ok; 401 wrong token or collector deleted/disabled; 404 wrong URL; 413 body over 10 MB; 429 rate limit 400 requests/s per customer per endpoint; 500 body format does not match collector's Log Format | same | ingest | n/a | DOCUMENTED (same page); HTTP collector ingests up to 80,000 events/s | http_collector_configured |
| Collector Compression setting must match what the sender does (emitter `compress=true` adds gzip) | same | ingest | per collector | DOCUMENTED setting; mismatch consequence INFERRED | http_collector_configured |
| Broker VM "HTTP collector applet": the Broker VM applet pages list Syslog, CSV, Database, FTP, Kafka, Network Mapper, WEC and so on; no HTTP applet. The HTTP log collector is a tenant-side cloud endpoint | Broker VM docs | ingest | n/a | NOT FOUND for an applet that receives HTTP POSTs. Sources checked: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/data-management/broker-vm/set-up-and-configure-broker-vm/broker-vm-data-collector-applets.md , https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Manage-Broker-VM-data-collector-applets | http_collector_configured |
| Data Model Rules (XDM) map a custom dataset so Analytics, stories and XDM-based rules can use it; created at Settings › Configurations › Data Management › Data Model Rules (XQL `[MODEL: dataset=...]`) | tenant | detect | per dataset | DOCUMENTED https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/data-management/data-model-rules/create-data-model-rules.md (via search excerpt; page fetched through docs-cortex mirror) | xdm_modeling_for_analytics (new) |
| Marketplace packs that ship Parsing + Modeling rules for a source (example: Cisco ASA pack for `cisco_asa_raw`, installed with Broker VM Syslog vendor=Cisco product=ASA). Packs are keyed to a vendor/product name and a log format | Marketplace | ingest + detect | per source | DOCUMENTED https://cortex.marketplace.pan.dev/marketplace/details/CiscoASA/ . Which pack matches each emitter's record shape: NOT FOUND | pack_installed (new) |
| Built-in analytics visibility of a custom-collector dataset: only after XDM mapping. Correlation rules work on non-normalized and normalized logs; Analytics, IOC, BIOC only on normalized | tenant | detect | per dataset | DOCUMENTED (analytics-engine page; NGFW ingest page); real-time correlation runs on third-party datasets and data models (create-a-correlation-rule page) | xdm_modeling_for_analytics |
| Egress from the sender to `api-{tenant}`: captive portal or SSO wall returns HTML 200 (`collector_intercepted`) | network | ingest | per-POV | INFERRED (repo `delivery.py` taxonomy; the docs list no HTML response) | http_collector_configured |

### Cross-cutting gotchas (repo vs docs)
- `analytics_emitter.py` default `auth_scheme="Bearer"` sends `Authorization: Bearer <key>`. The tenant collector expects the raw key. Every campaign must set `auth_scheme: ""` or all POSTs return 401. (`delivery.py` REMEDIATION text already says "raw API key under 'Authorization'"; the default contradicts it.)
- Batch mode sends `application/x-ndjson`. The docs name only `application/json` and `text/plain`. NOT FOUND whether ndjson is accepted; test with a one-record canary (`/collectors/preflight`) before a campaign.
- REMEDIATION strings mention a "Broker VM HTTP applet". Docs show no such applet (row above). Reword toward "HTTP log collector instance".
- Emitters claim real dataset names (`okta_sso`, `cloud_audit_logs`, `msft_o365_audit`, `panw_ngfw_traffic_raw`). Through a tenant HTTP collector those names are producible only if Vendor+Product spell them (`okta`+`sso` → `okta_sso_raw`, `panw`+`ngfw_traffic` → `panw_ngfw_traffic_raw`). That a custom collector may write into a native NGFW dataset name: NOT FOUND, and a collision with the native firewall integration is INFERRED to be a risk. Prefer a distinct vendor/product and point the card XQL at it.
- A custom-collector `panw_ngfw_traffic_raw` is not stitched and not on the native schema; IOC/BIOC on stitched data (`xdr_data`, `network_story`) will not see it. This caps every NGFW-EAL-emitter scenario at XQL and correlation detections unless a modeling rule is added.
- Auth stories drop events missing any of the 12 mandatory XDM fields (`xdm.source.user.upn` must be UPN-shaped, `xdm.source.ipv4` never empty), so ITDR/IdP emitter records need those fields or identity analytics will ignore them.

---

## Sources (fetched 2026-11-10)
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/next-generation-firewall/ingest-data-from-next-generation-firewall
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/http-log-collector
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/http-log-collector/set-up-an-http-log-collector-to-receive-logs.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/data-management/broker-vm/set-up-and-configure-broker-vm/broker-vm-data-collector-applets.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/generic-on-premise-data-collectors.md
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Manage-Broker-VM-data-collector-applets
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/data-management/data-model-rules/how-to-map-authentication-story-events.md
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Create-Data-Model-Rules (search excerpt)
- https://cortex.marketplace.pan.dev/marketplace/details/CiscoASA/
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics/enable-the-analytics-engine-and-identity-analytics.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics/configure-cortex-xsiam-network-parameters.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-overview.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-engine.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-sensors.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-detection-time-intervals.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-issues-and-analytics-biocs.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/view-and-manage-analytics-rules.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md
- https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name.md
- https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/port-scan.md
- https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/dns-tunneling.md
- https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/a-possible-crypto-miner-was-detected-on-a-host.md
- https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/large-upload-https.md
- https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/abnormal-smb-scanning-activity-to-multiple-hosts.md
- https://docs.paloaltonetworks.com/ngfw/administration/subscriptions/enhanced-application-logs
- https://docs.paloaltonetworks.com/dns-security/activation-and-onboarding/enable-dns-security (search excerpt, full page text)
- https://docs.paloaltonetworks.com/network-security/security-policy/administration/security-profiles/security-profile-anti-spyware (search excerpt)
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management/get-started-with-threat-intel-management.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management/indicator-configuration.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management/indicator-configuration/generate-issues-from-indicators-using-indicator-rules-for-prevention-and-detection.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/threat-intel-management/indicator-configuration/export-indicators.md
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-3.x-Documentation/Indicator-concepts
- https://xsoar.pan.dev/docs/reference/integrations/taxii-2-feed
- https://live.paloaltonetworks.com/t5/cortex-xsiam-discussions/xsiam-threat-intelligence-management-module/td-p/1254103 (secondary)
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/attack-surface-management.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/attack-surface-management/get-started-with-attack-surface-management.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/attack-surface-management/attack-surface-assets.md
- https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/attack-surface-management/asm-enrichment-of-cloud-assets.md
- https://cortex-docs.paloaltonetworks.com/cortex-xpanse/attack-surface-rules.md
- https://cortex-docs.paloaltonetworks.com/cortex-xpanse/inventory/asset-attribution.md
- https://cortex-docs.paloaltonetworks.com/cortex-xpanse/inventory/asset-types.md
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XPANSE/2/Cortex-Xpanse-Expander-User-Guide/Network-mapping
- https://docs-cortex.paloaltonetworks.com/r/Cortex-XPANSE/2/Cortex-Xpanse-Expander-User-Guide/Scanning-cadences
- https://cortex-docs.paloaltonetworks.com/cortex-xpanse/learn-about-cortex-xpanse/scanning/known-assets-monitoring.md
- https://cortex-docs.paloaltonetworks.com/cortex-cloud-posture-management/cloud-inventory-and-environment/asset-classes/external-surface-assets.md

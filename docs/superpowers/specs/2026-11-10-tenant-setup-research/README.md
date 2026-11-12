# Tenant-side setup for CortexSim simulations: research (2026-11-10)

Status: research. Nothing here was tested against a tenant. Every requirement
below comes from a Palo Alto Networks public page that was fetched, and the five
appendices in this folder carry the URL for each one. Where the docs were silent
the appendix says NOT FOUND; where a conclusion is reasoned rather than read it
says INFERRED. `tenant-verified` is still 0.

| Appendix | Planes |
|---|---|
| [endpoint-edr-cdr-koi.md](endpoint-edr-cdr-koi.md) | EDR, CDR, KOI |
| [network-ndr-tim-asm-analytics-ingestion.md](network-ndr-tim-asm-analytics-ingestion.md) | NDR, TIM, ASM, ANALYTICS, and the HTTP collector path |
| [identity-itdr-cloudapp-email-cspm.md](identity-itdr-cloudapp-email-cspm.md) | ITDR, CLOUD_APP, EMAIL, CSPM, cloud audit logs |
| [data-ai-dlp-aiaccess-airs-browser-aispm.md](data-ai-dlp-aiaccess-airs-browser-aispm.md) | DLP, AI_ACCESS, AIRS, BROWSER, AI_SPM |
| [marketplace-content-packs.md](marketplace-content-packs.md) | Cortex Marketplace packs for every third-party source |

## Executive summary

A simulation only shows up in a POV if the customer's tenant, and the products
that feed it, are set up to receive and judge it. This research checked that
setup for all 16 planes against the vendor's public documentation, and found the
largest risk is in our own content, not the customer's tenant. About a quarter of
the detection queries look for data under names a real tenant never creates, so
in a live POV they return nothing and read as "Cortex missed it". Separately,
several detections need weeks of learned behaviour before they can fire, and a
few scenarios cannot produce their signal at all in the current lab design. The
recommended action is to correct the data names and the log-sending defaults
before any further POV use, and to label each scenario with what a one-day POV
can actually show.

## Technical detail

### 1. Dataset names: 22 of the registry's 32 are not datasets a tenant creates

Measured on this tree with the registry walker's rules (xql_queries[].dataset
plus `preset =` / `dataset =` anchors in BIOC, ABIOC and correlation logic):

| | Count |
|---|---:|
| Registry datasets (`spec/dataset_requirements.yml`) | 32 |
| Names that do not match a documented tenant dataset | **22** |
| Card dataset references on those names | **281 of 1,159 (24%)** |
| Cards affected | 94 of 191 |
| Scenarios citing an affected card | 93 of 194 |

| Registry name | Card refs | What the docs say the tenant creates | Evidence |
|---|---:|---|---|
| `msft_o365_audit` | 34 | `msft_o365_general_raw`, `_exchange_online_raw`, `_sharepoint_online_raw`, `_dlp_raw`, `_azure_ad_raw` (split by workload) | marketplace, identity |
| `okta_sso` | 33 | `okta_sso_raw` (native connector; Okta pack parsing rule) | marketplace, identity |
| `cortex_cloud_posture` | 31 | `findings` (and Asset Inventory) | identity, data-ai |
| `msft_windows_security` | 27 | `microsoft_windows_raw` | marketplace, identity |
| `msft_azure_ad_signin` | 26 | `msft_azure_ad_raw` | marketplace, identity |
| `prisma_browser_events` | 23 | `panw_prisma_access_browser_raw` | data-ai, marketplace |
| `pan_dns` | 16 | not found as a dataset; DNS lands in threat/EAL records | network |
| `threat_intel` | 15 | `indicators` | network |
| `asm_findings` | 14 | not found; ASM results are Issues and External Surface inventory | network |
| `msft_azure_audit` | 8 | `msft_azure_raw`, normalised into `cloud_audit_logs` | identity |
| `pan_ngfw_traffic` | 8 | `panw_ngfw_traffic_raw` | network |
| `ai_spm_findings` | 7 | `asset_inventory`, `findings`, `issues` | data-ai |
| `ad_audit` | 6 | `microsoft_windows_raw` | marketplace |
| `cloud_audit` | 6 | `cloud_audit_logs` | endpoint |
| `kubernetes_audit_logs` | 6 | `kubernetes_kubernetes_raw` (syslog); EKS/AKS audit lands in `cloud_audit_logs` | endpoint, marketplace |
| `cortexsim_airs_raw` | 6 | Prisma AIRS API logs land in `panw_prisma_airs_raw` | data-ai |
| `saas_okta_raw` | 4 | `okta_sso_raw` | marketplace |
| `msft_o365` | 3 | `msft_o365_emails_raw` | identity |
| `koi_code_scan_events` | 3 | `koi_koi_raw` (alert/audit shape; the cards' fields are not in it) | endpoint, marketplace |
| `google_workspace_audit` | 2 | `google_workspace_login_raw`, `_token_raw`, `_admin_console_raw`, ... | identity |
| `esxi_syslog` | 2 | `vmware_esxi_raw` (VM-creation events are vCenter: `vmware_vcenter_raw`) | marketplace |
| `ivanti_ics_raw` | 1 | `ivanti_connect_secure_raw` | marketplace |

The ten that match: `xdr_data`, `network_story`, `xsiam_incidents`,
`cloud_audit_logs`, `saas_audit_logs`, `panw_ngfw_traffic_raw`,
`panw_ngfw_threat_raw`, `panw_ngfw_globalprotect_raw`, `proofpoint_tap_raw`,
`cisco_asa_raw`. Two of those still carry a caveat: `saas_audit_logs` is a
normalised SaaS audit dataset, not the proxy-shaped data its cards filter on, and
`cloud_audit_logs` is filled by platform normalisation, which a synthetic record
posted to an HTTP collector does not pass through (route NOT FOUND).

### 2. How synthetic logs reach a tenant, and three defects in our sender

Read from the XSIAM 3.x page "Set up an HTTP log collector to receive logs"
(fetched; quoted in the network appendix):

- The dataset is created as `<Vendor>_<Product>_raw` from the **collector's**
  Vendor and Product settings. The emitters' `dataset` field in the JSON body
  routes nothing. One collector, with its own token, is needed per target
  dataset.
- Requests go to `https://api-{tenant}/logs/v1/event` with
  `Authorization: {api_key}` (the bare key) and `Content-Type: application/json`
  or `text/plain`.
- Records in a custom-collector dataset are not normalised or stitched. Analytics,
  IOC and BIOC detections run on normalised (and, for IOC/BIOC, stitched) data, so
  synthetic records reach only XQL and correlation rules unless a Data Model
  (XDM) rule maps the dataset.

Defects in this repo, confirmed in code on 2026-11-10. PR #178 (merge
`cef57d4b`, 2026-11-11) fixed the first and third; the batch content type is
still open.

| Defect | Where (as found) | Effect | Status |
|---|---|---|---|
| Default `auth_scheme="Bearer"` sends `Authorization: Bearer <key>` | `core/eal_simulator/analytics_emitter.py:119` | Every collector POST with defaults is rejected (401); delivery accounting reports `not_delivered` | **Fixed by #178.** `AnalyticsEmitterParams.auth_scheme` defaults to `""`, so the 14 collector emitters send the bare key. `llm_provider_egress` keeps `Bearer` for provider APIs. Guard: `tests/eal_simulator/test_collector_auth_default.py` |
| Batch mode sends `application/x-ndjson`, which the docs do not list | emitter batch path (`analytics_emitter.py`, `bundle.py`) | Unknown; test with a one-record canary first | Open |
| Remediation text names a "Broker VM HTTP applet"; the docs list no such applet (the HTTP collector is a tenant endpoint) | `core/eal_simulator/delivery.py` remediation strings | Sends a DC to the wrong screen | **Fixed by #178.** Remediation in `delivery.py`, `bundle.py`, `collector.py` and `analytics_emitter.py` names the HTTP log collector, and a collector 500 reads as a body that does not match the collector's Log Format |

`email_emitter` was outside #178. It has no auth parameters, so it still cannot
authenticate to an XSIAM collector.

Vendor/Product pairs that produce each pack's dataset (from the packs' parsing
rules): Okta `okta`/`sso`; Windows `microsoft`/`windows`; Entra `msft`/`Azure AD`;
Kubernetes `kubernetes`/`kubernetes`; ESXi `VMware`/`ESXi`; Ivanti
`ivanti`/`connect_secure`; Proofpoint `Proofpoint`/`Tap`; Cisco `cisco`/`asa`.

### 3. What a one-day POV can show

| Detection kind | Fires on day one? | Why |
|---|---|---|
| Cards' own XQL, imported correlation rules | Yes, once data lands in the right dataset and the rule is enabled | No learning period. Scheduled correlations run every 10 min at most often |
| Cards' own BIOC | Yes on agent data (`xdr_data`); not on raw custom-collector data | BIOC needs stitched data |
| Built-in Analytics detectors | **No** | Every detector checked (network, identity, Kerberos, email, consent) has a 14-day activation and 30-day training period; the engine needs 30 endpoints over 2 weeks, or cloud audit logs over 5 days |
| ABIOC | Not promisable | "Tailored after analysing your logs"; no period published |
| Xpanse / ASM discovery | **No**, unless already attributed and Known Assets Monitoring is on | Global scans twice a week; self-provided assets take up to 5 days to review; cloud IPs are rejected as uploads |
| Cloud posture findings (CSPM, AI-SPM) | Within a day if forced | Full discovery every 12 h; Discover Now forces a scan; event-assisted ingestion needs the Audit Logs capability. The module README's "15-60 min" is unsupported |
| Detectors auto-disable | Hard limit | At 5,000 hits in 24 h |

### 4. Scenarios that cannot produce their signal, however the tenant is set up

| Scenario(s) | Reason (appendix) |
|---|---|
| SIM-AIRS-001..005 | Target on `127.0.0.1:8089`; nothing calls the AIRS Scan API and no firewall is in path. Cards also read `uri_path` / `request_body` from NGFW traffic logs, which carry no such fields (data-ai) |
| SIM-BROWSER-001..005 | Plain Chromium emits no Prisma Browser events; `*.cortexsim-test.invalid` never resolves (data-ai) |
| SIM-DLP-001..005 | Linux shell steps; Endpoint DLP supports Windows and macOS only; TTP-2026-0179 reads `panw_dlp_raw`, which no doc names (data-ai) |
| SIM-CLOUD-001..005 | Authorize requests with a fake client_id create no consent and no audit event; cards expect proxy fields in `saas_audit_logs` (identity) |
| SIM-KOI-001..008 | Run from Linux; Agentic Endpoint Security covers Windows and macOS only (endpoint) |
| SIM-CDR-019, 027, 028 | Need CloudTrail data events, which are off in a default onboarding (endpoint) |
| SIM-ITDR-013, 017 | Read Windows events 5136/5137, which are not in the agent's documented event list (identity) |
| AI Access DLP legs | Planted markers miss predefined patterns: `AKIA0000CORTEXSIMCANARY` is 23 characters (an AWS key ID is 20); SSN/card markers contain letters. A custom `CORTEXSIM` data pattern is needed (data-ai) |

### 5. Setup checklist by plane (summary; full tables with menu paths in the appendices)

| Plane | Licence / add-on | One-time tenant setup | Per-POV setup |
|---|---|---|---|
| EDR | XDR Pro per endpoint (BIOC); XTH for full file/registry/network telemetry | macOS MDM profile (system extension, Full Disk Access) | Linux agent in Kernel or User Space mode, never asynchronous; Malware profile Report for run 1, Block for run 2; no exception or suppression on the test host |
| CDR | Cloud per host; Cloud Posture or Runtime add-on for audit logs | Kubernetes connector or unedited DaemonSet YAML; cloud account onboarding with audit logs | CloudTrail **data events** for S3/DynamoDB/RDS scenarios; disable Prisma Cloud Compute runtime if present |
| KOI | Agentic Endpoint Security add-on (Platform XDR or XSIAM tenant) | Koi connector (tenants onboarded after 2026-07-26) or Koi pack | Agent Settings › AES run mode "XDR + AES" (default is Disabled; "AES only" collects no events) |
| NDR | Strata Logging Service; DNS Security for DNS legs | Native NGFW integration (not a custom collector), log types selected | Log Forwarding profile on the rule; Enhanced Application Logs globally and per rule; Anti-Spyware with DNS policies; decryption for HTTP-level fields; allow rule logging at session end |
| TIM | XSIAM Premium or TIM add-on | TAXII 2 feed instance reachable from the tenant | Detection indicator rule enabled; EDL export for firewall enforcement (SIM-TIM-007) |
| ASM | XSIAM Premium or ASM add-on | Attribution by domain or certificate (cloud IPs rejected as uploads) | Attack surface rules enabled (most default-off); Known Assets Monitoring opt-in; scanner IPs not blocked |
| ANALYTICS | | Analytics Engine on; internal IP ranges and domain suffixes; Identity Analytics with Cloud Identity Engine | Correlation rules imported and enabled; XDM rules for custom datasets |
| ITDR | ITDR add-on; XTH for Kerberos/DCSync detectors | Cloud Identity Engine paired; Okta and Entra collectors | DC events via XDR agent or Broker VM Windows Event Collector (the lab AD module provisions neither) |
| CLOUD_APP | XSIAM Premium, Cloud Posture or Cloud Runtime | SaaS Security connectors | NGFW URL logging with full URL for the OAuth scenarios |
| EMAIL | Email Security / Advanced Email Security add-on for built-in email detections | Proofpoint TAP collector; Microsoft 365 email collector | One HTTP collector per synthetic source, named to match the pack |
| CSPM | Cloud Posture Security add-on | AWS onboarding (CortexPlatformRole), Audit Logs capability | Discover Now after the lab is applied |
| DLP | Endpoint DLP add-on (Win/macOS) or Enterprise DLP | Data profiles (six built in) and custom patterns | Data-in-motion rule targeting the endpoint, or data filtering profile on the rule, with decryption and non-file inspection |
| AI_ACCESS | AI Access Security + Strata Logging Service | | Traffic through NGFW/Prisma Access; DLP rule with file and non-file inspection; decryption; custom `CORTEXSIM` pattern |
| AIRS | Prisma AIRS deployment profile | Strata Cloud Manager connector (AIRS API logs) | Wire the Scan API into the target, or put an AI-security firewall in path |
| BROWSER | Prisma Browser | Prisma Access Browser data source (one per tenant) | Real Prisma Browser enrolled; data, clipboard, screenshot and extension controls configured |
| AI_SPM | Cloud Posture, Cloud Runtime or XSIAM Premium | Comprehensive (not Foundational) CSP onboarding; DSPM for data findings | Verify inventory in the tenant before promising findings (cadence not published) |

### 6. Cortex Marketplace

- Packs exist, and supply parsing and XDM modeling rules, for Okta, Office 365,
  Entra ID / Azure, Windows, AWS CloudTrail, Google Cloud Logging, Kubernetes,
  Proofpoint TAP, Cisco ASA, Ivanti Connect Secure, VMware ESXi and vCenter, and
  Koi. No public pack was found for Prisma Browser.
- The public Marketplace ships **10 correlation rules in 6 packs**, none for any
  source CortexSim simulates. The four Google Chrome rules key on Chrome
  Enterprise data (`google_workspace_chrome_raw`), so they do not fire on Prisma
  Browser or the KOI extension scenarios. The cards' own correlation rules remain
  the only correlation content.
- First-party packs useful around a POV: PAN-OS to Strata Logging Service
  Monitoring (proves the firewall feed is live), Unit 42 Threat Intelligence,
  Cortex Attack Surface Management, Enterprise DLP, Strata Cloud Manager.

### 7. Registry changes this implies

`spec/dataset_requirements.yml` keys on the card dataset names, so it inherits
section 1's mismatch. Two options, for a decision:

- **Rename in the cards** (the cards then query what a tenant has). Largest
  change, 281 references, but it is the only option under which a real
  integration and a synthetic run land in the same place.
- **Keep the card names and record the tenant name per row** (a `tenant_dataset`
  field). Smaller change; the cards still miss on a real integration, and the
  collector must be named to produce the card's name, which the `<Vendor>_<Product>_raw`
  rule does not allow for names without `_raw`.

New requirement codes the appendices propose (none added yet):
`analytics_engine_enabled`, `analytics_baseline_elapsed`,
`identity_analytics_enabled`, `cloud_identity_engine_activated`,
`network_parameters_configured`, `correlation_rule_enabled`,
`xdm_modeling_for_analytics`, `http_collector_configured`,
`marketplace_pack_installed`, `native_ngfw_integration`,
`prevention_profile_mode`, `no_exception_suppressing_test_host`,
`aes_addon_licensed`, `aes_run_mode_enabled`, `indicator_rule_enabled`,
`attack_surface_rules_enabled`, `kam_opt_in`, `xth_licensed`,
`windows_dc_audit_policy`, `discovery_scan_complete`, `posture_policy_enabled`,
`data_profile_selected`, `dlp_rule_configured`, `data_filtering_profile_attached`,
`dlp_non_file_inspection`, `genai_prompt_logging`, `airs_api_intercept_wired`,
`airs_network_intercept_in_path`, `browser_policy_configured`, and several
`*_licensed` codes. Many are licence or enablement conditions that belong on a
plane or scenario, not on a dataset row; that shape decision is open.

### 8. Recommended order

1. ~~Fix the sender: default `auth_scheme` to empty, and correct the remediation
   text.~~ **Done in PR #178** (merge `cef57d4b`, 2026-11-11). What remains of
   this step: the batch `application/x-ndjson` content type (section 2) and
   `email_emitter`, which has no auth parameters.
2. Decide section 7's naming option, then correct the datasets in descending
   order of card references.
3. Add a per-scenario "day-one" label from section 3 so a report never scores an
   Analytics or ASM expectation as missed on a fresh tenant.
4. Mark the section 4 scenarios as not runnable in the current lab, and decide
   per plane whether to rebuild the lab path (AIRS Scan API wiring, Prisma
   Browser, Windows DLP target) or retire the expectation.
5. Confirm one dataset per source in deathstar before editing at scale: the docs
   are consistent, but a tenant is the only proof.

### 9. Limits of this research

- Public documentation only. Behaviour can differ by tenant version and region.
- Several timings are NOT FOUND: ABIOC baselines, AI-SPM and Koi cadence,
  ingestion-to-detection delay, NGFW log-to-Cortex delay.
- Whether built-in detectors treat HTTP-collected records as the matching vendor
  data source is NOT FOUND.
- The Prisma Browser ingestion page in Cortex and GCP Data Access audit
  prerequisites were not fetched.

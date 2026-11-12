# Tenant-side setup: ITDR, CLOUD_APP, EMAIL, CSPM

Researched 2026-11-10 against cortex-docs.paloaltonetworks.com (XSIAM current, GitBook portal; the old `docs-cortex` deep links redirect to the same pages). Nothing here was tested on a tenant. Evidence tags: DOCUMENTED (URL fetched, listed under Sources), INFERRED (reasoning given), NOT FOUND (looked, public docs silent).

Registry codes: existing = `audit_log_export`, `api_integration`, `agent_installed`, `log_forwarding`. Proposed new codes are marked (new).

## Cross-cutting: the dataset names the cards read are mostly not the names the docs give

The registry has `cortex_side.name: null` for every row. These are the names the docs state, for whoever fills it in. They are docs-stated, not tenant-verified.

| Registry / card dataset | Name the docs give | Evidence |
|---|---|---|
| `okta_sso`, `saas_okta_raw` | `okta_sso_raw`; also normalized into `xdr_data` or `saas_audit_logs` by event type | DOCUMENTED okta-ingest, datasets-and-presets |
| `msft_azure_ad_signin` | `msft_azure_ad_raw` (Entra authentication/sign-in via Graph); `msft_o365_azure_ad_raw` (Management Activity API); `MSFT_Azure_raw` (Event Hub) | DOCUMENTED o365-ingest, eventhub-ingest |
| `msft_o365_audit` | `msft_o365_general_raw`, `msft_o365_exchange_online_raw`, `msft_o365_sharepoint_online_raw`, `msft_o365_dlp_raw`, `msft_o365_azure_ad_raw`, `msft_azure_ad_audit_raw` | DOCUMENTED o365-ingest, marketplace Office365 |
| `msft_o365`, `msft_o365_email` (emitter Meta) | `msft_o365_emails_raw` (plus `_users_`, `_groups_`, `_mailboxes_`, `_rules_` ... `_raw`) | DOCUMENTED o365-email-ingest |
| `proofpoint_tap_raw` | `proofpoint_tap_raw` (matches) | DOCUMENTED proofpoint-tap-ingest |
| `google_workspace_audit` | `google_workspace_login_raw`, `_token_raw`, `_drive_raw`, `_admin_console_raw`, `_saml_raw` ...; SaaS audit normalizes to `saas_audit_logs` | DOCUMENTED google-workspace-ingest |
| `msft_windows_security`, `ad_audit` | `microsoft_windows_raw` (also `xdr_data`) from XDR agent or Windows Event Collector | DOCUMENTED endpoint-data-collection, wec |
| `msft_azure_audit` | `MSFT_Azure_raw` by default; with "Use audit logs in analytics" it is also normalized into `cloud_audit_logs` | DOCUMENTED eventhub-ingest |
| `cloud_audit_logs` | exists as the cross-cloud normalized dataset; the raw CloudTrail dataset name | DOCUMENTED (normalized); NOT FOUND (raw CloudTrail name) |
| `cortex_cloud_posture` | no dataset by this name in the docs; findings are queried in `findings` (`dataset = findings`), assets in Asset Inventory | DOCUMENTED review-findings |
| `saas_audit_logs` | normalized SaaS audit dataset (vendor `saas`, product `Audit Logs`), filled by Okta, Google Workspace, O365 etc. It is not an HTTP/proxy-shaped dataset | DOCUMENTED o365-ingest, google-workspace-ingest |

Consequence: any card query that says `dataset = okta_sso` or `dataset = msft_o365_audit` matches nothing on a real tenant even if the data is flowing. Fix the card or add a dataset alias before blaming Cortex.

### Posting synthetic records to an HTTP collector (applies to ITDR, CLOUD_APP, EMAIL emitters)

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| HTTP log collector created; dataset name is set by the collector's Vendor and Product as `<vendor>_<product>_raw`, not by a field in the body | Settings › Data Sources & Integrations › Add New › HTTP | ingest | per-POV, one collector per target dataset | DOCUMENTED http-collector | `api_integration` |
| To land in `okta_sso_raw` set Vendor=okta, Product=sso; for `proofpoint_tap_raw` Vendor=proofpoint, Product=tap. The body's `"dataset"` hint is just a field | same | ingest | per-POV | INFERRED from the naming rule above; no doc says the body field is ignored | `http_collector_vendor_product` (new) |
| Auth header carries the raw API key: `Authorization: {api_key}`. The emitter default is `auth_scheme="Bearer"`, which sends `Authorization: Bearer <key>`; set `auth_scheme` to empty | HTTP collector › Save & Generate Token; URL `https://api-<tenant>/logs/v1/event` | ingest | per-POV | DOCUMENTED http-collector (curl example is raw key); INFERRED that `Bearer` prefix is rejected (401 documented for wrong token) | `api_integration` |
| Content-Type `application/json` for JSON format, `text/plain` for text; records newline-separated. `batch: true` sends `application/x-ndjson`, which is not in the list | same | ingest | per-POV | DOCUMENTED http-collector; NOT FOUND whether `x-ndjson` is accepted | `api_integration` |
| Records posted this way get the collector's dataset only. They do not pass through the vendor connector, so the marketplace parsing/modeling rule for that vendor applies only if the pack is installed and the field names match | Marketplace › Okta / ProofpointTAP packs | detect | one-time tenant setup | INFERRED (packs ship Modeling + Parsing Rules per marketplace page; no doc states rules bind by dataset name) | `marketplace_pack_installed` (new) |
| Whether built-in Analytics detectors treat HTTP-collected records as the "Okta" / "Microsoft 365 Emails" data source | Analytics alert reference "Required Data" | detect | n/a | NOT FOUND | none |

Limits: 80,000 events/s collector, 400 requests/s per customer per endpoint (429), 10 MiB request, 5 MB record (DOCUMENTED http-collector).

---

## ITDR

Scenarios: SIM-ITDR-001..005 (idp_signin_emulator, XQL+BIOC), 006/007/015/017 (tools against a lab AD), 008, 009/010/011/013/014/016 (ABIOC), 012 (GlobalProtect, NGFW), 018/019 (azure_audit_emitter, m365_activity_emitter), 020 (ad_windows_emitter, ABIOC/Analytics).

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| ITDR add-on licensed (asset roles, User Risk view, compromised-account and insider analytics). Licence table lists it as Add-on on NG-SIEM, Enterprise and Premium | Settings › Cortex XDR License (XDR docs); licence tiers page | detect | licence or add-on | DOCUMENTED itdr-overview, xsiam-licenses | `itdr_addon_licensed` (new) |
| Activating the ITDR add-on switches Identity Analytics on; verify the toggle | Settings › Configurations › Analytics | detect | one-time tenant setup | DOCUMENTED get-started-itdr | `identity_analytics_enabled` (new) |
| Cortex XSIAM Analytics engine enabled; baseline creation "can take up to three hours" | Settings › Configurations › Cortex XSIAM - Analytics › Enable | detect | one-time tenant setup | DOCUMENTED enable-analytics | `identity_analytics_enabled` (new) |
| Analytics baseline minimum: EDR or network logs from 30+ endpoints over 2 weeks, or cloud audit logs over 5 days | same | detect | per-tenant data volume | DOCUMENTED enable-analytics | `analytics_baseline_elapsed` (new) |
| Cloud Identity Engine activated in the same region as the tenant, then paired (wait ~10 min). "Required to activate Identity Analytics detection rules" | Settings › Configuration › Integrations › Cloud Identity Engine | detect | one-time tenant setup | DOCUMENTED set-up-cie | `cloud_identity_engine_activated` (new) |
| Okta System Log collected (Okta Domain + API token, "Any IP"); events `user.authentication.sso` must be collected for authentication stories. Dataset `okta_sso_raw`. The older "Okta (Deprecated)" collector is being replaced by an Okta connector | Settings › Data Sources & Integrations › Okta | ingest | per-POV | DOCUMENTED okta-ingest | `api_integration` |
| Entra ID sign-in and audit logs via Office 365 collector (Graph `AuditLog.Read.All`, `Directory.Read.All`); needs Entra ID P1 or P2; 10-minute latency. Real-time alternative is Azure Event Hub | Settings › Data Sources & Integrations › Office 365 / Azure Event Hub | ingest | per-POV, third-party licence | DOCUMENTED o365-ingest, eventhub-ingest | `api_integration` |
| Domain controller events reach the tenant: XDR agent on the DC (writes `microsoft_windows_raw` and `xdr_data`) or a Broker VM Windows Event Collector (default collects PANW's predefined Security events; do not remove the Security collector) | Broker VM › APPS › Add › Windows Event Collector; GPO "Windows Event Forwarding"; Network Service in Event Log Readers | ingest | per-host (DC) | DOCUMENTED wec, endpoint-data-collection | `agent_installed` or `log_forwarding` |
| Event IDs the agent can send include 4624, 4625, 4662, 4768, 4769, 4771, 4776, 4740, 4886, 4887, 4738. Cards also read 5136 and 5137 (SIM-ITDR-013 BadSuccessor, 017 ESC1). Neither 5136 nor 5137 is in the agent list | Windows security auditing setup | ingest | per-host (DC) | DOCUMENTED endpoint-data-collection; NOT FOUND for 5136/5137 collection | `windows_dc_audit_policy` (new) |
| Kerberos/DCSync/lockout built-in detectors need "Windows Event Collector OR XDR Agent with eXtended Threat Hunting (XTH)". XTH is included in Enterprise and Premium, an add-on on NG-SIEM | Alerts by data source › Windows Event Collector | detect | licence or add-on | DOCUMENTED weakly-encrypted-tickets, multiple-tgt, dcsync, xsiam-licenses | `xth_licensed` (new) |
| Identity Profile (Windows, agent 9.1+) assigned to DC policy for AD-SPM, Conditional Access (9.3+), LDAP Protection | Inventory › Endpoints › Policy Management › Prevention › Profiles › Identity | detect / prevent | per-host (DC) | DOCUMENTED get-started-itdr | `identity_profile_attached` (new) |
| Identity-role RBAC (Identity Security) | Settings › Configurations › Access Management › Roles | detect | one-time | DOCUMENTED get-started-itdr | none |
| Impossible travel, MFA fatigue, spray, brute force, new-country detectors read Okta, Azure SignIn, AzureAD, Duo, Google, OneLogin, PingOne or Idira | Alerts by data source › Okta, Azure SignIn Log | detect | n/a | DOCUMENTED impossible-traveler-sso, sso-password-spray, multiple-okta-mfa | none |
| Azure service-principal / Conditional Access / mailbox-rule detectors read Azure Audit Log, AzureAD Audit Log or Microsoft Graph Logs | Alerts by name › Azure application consent, Azure mailbox rule creation | detect | n/a | DOCUMENTED azure-application-consent, azure-mailbox-rule-creation | none |

### Timing and volume constraints
- Every built-in Analytics detector checked has **Activation Period 14 days and Training Period 30 days** (impossible traveler SSO, multiple Okta MFA requests, SSO password spray, SSO brute force, weakly encrypted service tickets, TGT without pre-auth, DCSync, account lockout, new country, Azure application consent) (DOCUMENTED, alert pages listed). Test periods range from 10 minutes to 6 hours. A one-day POV cannot show a built-in Analytics alert on a tenant that was not already collecting this data 14 to 44 days earlier. Whether the 14 days count from tenant data start or from detector enablement is NOT FOUND.
- Detectors auto-disable at 5,000 matches in 24 hours (DOCUMENTED analytics-issues-and-biocs). A burst of `burst_count=200` events is within that.
- ABIOCs "tailor each ABIOC to your environment after analyzing your logs" (DOCUMENTED); no period is stated, so SIM-ITDR-009/010/011/013/014/016/017/020 ABIOC legs have an unstated baseline (NOT FOUND).
- Entra: 10-minute collection latency (DOCUMENTED). Windows DHCP logs improve the engine (DOCUMENTED enable-analytics).
- What fires on day one: the cards' own XQL, BIOC and Correlation rules, once the records are in the dataset and the rules are imported and enabled (INFERRED; they do not use the learning engine).

### Scenario-specific gotchas
- **SIM-ITDR-001..005, 008, 016**: cards query `okta_sso` / `msft_azure_ad_signin`; real datasets are `okta_sso_raw` / `msft_azure_ad_raw`. Emulator default `auth_scheme=Bearer` fails the HTTP collector. One emulator run posts one provider to one URL, so a Microsoft run needs its own collector with a different Vendor/Product.
- **SIM-ITDR-001..005**: even with correct names, synthetic Okta records posted over HTTP are not the Okta connector, so they may not be tagged as the "Okta" data source for built-in detectors (NOT FOUND). Expect the card XQL/BIOC to fire, not "Impossible traveler - SSO".
- **SIM-ITDR-006/020**: Kerberoast/AS-REP detectors need 4768/4769 from a DC via XDR agent+XTH or WEC. The lab DC from `infra/modules/aws/itdr` needs one of these installed; the module README does not provision either.
- **SIM-ITDR-013, 017**: read 5136/5137 (directory service changes). Not in the agent's documented event list; use WEC with a custom subscription and enable "Audit Directory Service Changes" on the DC (INFERRED, not documented in the pages fetched).
- **SIM-ITDR-012**: GlobalProtect log forwarding is an NGFW concern, not covered here.
- **SIM-ITDR-018/019/020**: need `MSFT_Azure_raw` plus "Use audit logs in analytics" for `cloud_audit_logs` normalization (DOCUMENTED eventhub-ingest).

### Report vs block
- Identity Profile › LDAP Protection has Action Mode Block / Report / Disabled; in Block the agent stops suspicious LDAP queries to the DC, so enumeration steps (SIM-ITDR-015 enum4linux-ng, bloodyAD in 007) would be prevented and must score `prevented`, not `missed` (DOCUMENTED get-started-itdr; INFERRED which steps are affected). Needs agent 9.1+, restart.
- Conditional Access Policy: Silent Logging Mode On observes without enforcing; Off blocks per rule (DOCUMENTED). Agent 9.3+.
- Signal posted to an HTTP collector cannot be blocked by any tenant profile.

---

## CLOUD_APP

Scenarios: SIM-CLOUD-001..005 (oauth_grant_emulator: outbound OAuth authorize GETs with a bogus client_id), 006/007 (Salesforce export, cloud_audit_logs), 008/009 (m365_activity_emitter), 010 (vendor integration pivot).

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| Cloud SaaS Security needs Cortex XSIAM Premium, or Cloud Runtime Security, or Cloud Posture Security; IP allow-list for connectivity | Modules › SaaS Security; Setup SaaS Security | detect | licence or add-on | DOCUMENTED saas-setup | `saas_security_licensed` (new) |
| SaaS Security reads connected-app inventory, permission scopes and posture checks by API connector. It does not read a request to an authorize endpoint | Modules › SaaS Security › Connected Apps / Detection Rules | detect | per-POV | DOCUMENTED saas-overview, saas-connected, saas-rules | `api_integration` |
| Built-in Analytics for consent: "Azure application consent" (needs AzureAD Audit Log), Google "app added to trusted OAuth apps list" | Alerts by name | detect | n/a | DOCUMENTED azure-application-consent; the Google alert is listed in llms.txt only (page not fetched) | none |
| Microsoft consent events: Office 365 collector, `msft_o365_azure_ad_raw` / `msft_azure_ad_audit_raw`; Entra permissions as in ITDR | Settings › Data Sources & Integrations › Office 365 | ingest | per-POV | DOCUMENTED o365-ingest | `api_integration` |
| Google Workspace Token/Login/SAML/Drive logs via Admin SDK Reports API with domain-wide delegation; lag per Google; dataset `google_workspace_token_raw` etc. | Settings › Data Sources & Integrations › Google Workspace | ingest | per-POV | DOCUMENTED google-workspace-ingest | `api_integration` |
| Okta System Log (see ITDR) | | ingest | per-POV | DOCUMENTED okta-ingest | `api_integration` |
| `saas_audit_logs`: filled automatically by SaaS audit sources; there is no way to post into it other than a collector with Vendor=saas Product=Audit Logs | HTTP collector | ingest | per-POV | DOCUMENTED o365-ingest ("Vendor is saas and Product is Audit Logs"); INFERRED that posting there bypasses normalization | `http_collector_vendor_product` (new) |
| A proxy/NGFW URL log with the full authorize URL including `scope=` query string | PAN-OS URL Filtering profile + log forwarding | detect | per-NGFW | NOT FOUND (belongs to the NGFW agent's section) | `url_filtering_logged` |
| Relevant CASB product for an inline, request-shape detection | Strata / SaaS Security Inline | detect | licence | NOT FOUND | none |

### Timing and volume constraints
- Azure application consent: 14-day activation, 30-day training (DOCUMENTED). Okta/Azure sign-in analytics as in ITDR.
- Google Workspace Reports API events arrive with Google's own lag (DOCUMENTED); Entra 10 minutes.
- Okta rate limits reduce collection automatically (DOCUMENTED).

### Scenario-specific gotchas
- **SIM-CLOUD-001..005 (all five)**: the emulator sends authorize GETs with a fake client_id that the IdP rejects. No consent is ever granted, so there is no connected app, no consent audit event and nothing for SaaS Security or the consent analytics to read. The card says detection is "on the outbound request shape at the proxy", but the cards read `saas_audit_logs` / `okta_sso` with `dst_host`, `uri_path`, `oauth_scopes`, which are not fields of the SaaS audit datasets (DOCUMENTED shape: audit events; INFERRED mismatch). These five cannot fire from Cloud App Security or a SaaS audit integration. They need NGFW URL logging of the full URL. Whether the IdP logs the failed authorize in its audit log is NOT FOUND.
- **SIM-CLOUD-005** (benign control) passes trivially if nothing is ingested; it needs the positive scenarios to have been seen to fire first. A zero is degraded, not ok.
- **SIM-CLOUD-006/007**: card dataset `cloud_audit_logs` is a normalized cross-cloud audit dataset; a Salesforce Bulk API export is not in it. Salesforce would arrive via its own collector (not researched).
- **SIM-CLOUD-008/009/010**: need `msft_o365_*_raw` names and the Azure Event Hub path as in ITDR.

### Report vs block
No profile in the tenant can stop the emulator's outbound GETs. Only an NGFW URL/decryption policy could, which is the NGFW section's call.

---

## EMAIL

Scenarios: SIM-EMAIL-001..004 and 005 (email_emitter posting `proofpoint_tap_raw` and M365 shapes; 005 also idp_signin_emulator).

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| Proofpoint TAP collector: service principal + API secret from TAP dashboard, host `tap-api-v2.proofpoint.com`; dataset `proofpoint_tap_raw` | Settings › Data Sources & Integrations › Proofpoint Targeted Attack Protection | ingest | per-POV, third-party credential | DOCUMENTED proofpoint-tap-ingest | `api_integration` |
| Marketplace pack `ProofpointTAP` provides Parsing and Modeling Rules (v1.2.27 Apr 2026) | Marketplace | detect | one-time tenant setup | DOCUMENTED marketplace ProofpointTAP | `marketplace_pack_installed` (new) |
| Microsoft 365 email collector via Graph (`Mail.ReadWrite` all mailboxes, `Directory.Read.All` etc.), Azure Account Administrator to set up; no compliance mailbox. Datasets `msft_o365_emails_raw` and eight siblings | Settings › Data Sources & Integrations › Microsoft 365 | ingest | per-POV, third-party consent | DOCUMENTED o365-email-ingest | `api_integration` |
| Email subject/body are stored encrypted (body kept 48 hours). Viewing or alerting on content needs the Email Security module licence; XQL hunting on subject/body is not possible | same | detect | licence or add-on | DOCUMENTED o365-email-ingest | `email_security_addon_licensed` (new) |
| Cortex Advanced Email Security add-on enabled (Analytics must be activated first); Settings › Cortex XSIAM License › Advanced Email Security Module › Enable. Cloud-hosted Microsoft 365 only; no on-prem Exchange, SMTP, IMAP | Settings › Cortex XSIAM License | detect | licence or add-on | DOCUMENTED aes-overview, aes-deploy, xsiam-licenses | `email_security_addon_licensed` (new) |
| Built-in "Email" detection module alerts (display-name impersonation, fake reply/forward, malicious extension, redirected link, ...) all list Required Data = "Microsoft 365 Emails" | Alerts by detection module › Email | detect | n/a | DOCUMENTED external-email-display-name, email-mimics-replies, email-attachment-ext, email-redirected-link | none |
| Exchange forwarding/inbox rule alerts read Office 365 Audit | Alerts by name | detect | n/a | DOCUMENTED exchange-inbox-forwarding-rule | none |
| Any built-in detector keyed to Proofpoint TAP data | Alerts by data source | detect | n/a | NOT FOUND (Proofpoint is not in the data-source list) | none |
| Entra sign-in for SIM-EMAIL-005 token-replay leg | see ITDR | ingest | per-POV | DOCUMENTED o365-ingest | `api_integration` |

### Timing and volume constraints
- Every Email-module alert checked: 14-day activation, 30-day training (DOCUMENTED, four alert pages).
- Email body retained 48 hours (DOCUMENTED).
- Module regions limited to AU, CA, FR, DE, IN, JP, NL, SG, KR, UK, US (DOCUMENTED aes-overview).

### Scenario-specific gotchas
- **All five**: the emitter Meta names `msft_o365_email` and the registry names `msft_o365`; the collector creates `msft_o365_emails_raw`. The Proofpoint name matches.
- **All five**: the card detection types are BIOC / XQL / Correlation (no Analytics). They fire from the synthetic `proofpoint_tap_raw` rows once the custom collector is named `proofpoint`/`tap` and the cards' rules are loaded. The built-in Email module will not fire on them: it needs real Graph-collected mail, and the emitter Meta itself declares only partial reach into "Microsoft 365 Emails".
- **SIM-EMAIL-003** also reads `xdr_data`; requires an XDR agent on the endpoint leg.
- A synthetic record posted to a custom collector lands in the same dataset name as the real integration only if Vendor and Product are chosen to match (DOCUMENTED rule). It does not make the tenant treat the dataset as a configured Proofpoint integration (INFERRED).

### Report vs block
No block mode applies: both collectors are passive, and the module "operates passively, requiring no mail flow changes" (DOCUMENTED aes-overview). Remediation (delete/move/tag) acts on real mailboxes only (DOCUMENTED aes-overview).

---

## CSPM

Scenarios: SIM-CSPM-001..005 against `infra/modules/aws/cspm` (planted: public/unversioned/no-KMS S3, SSH and DB ports open, AdministratorAccess role, wildcard IAM user, unencrypted EBS, weak CloudTrail) plus Prowler/ScoutSuite runs.

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| Cloud Posture Security: Premium includes it with Runtime; Enterprise and NG-SIEM need the Cloud Posture Security add-on. "Requires a Cloud Posture Security, Cloud Runtime Security or Cortex XSIAM Premium license" | licence tiers; Cloud security rules and policies | detect | licence or add-on | DOCUMENTED xsiam-licenses, cs-rules-policies | `cloud_posture_licensed` (new) |
| AWS account onboarded via the wizard (CloudFormation or Terraform); deploys `CortexPlatformRole` (Discovery is mandatory). Needs a role with Data Sources View & Edit | Settings › Data Sources & Integrations › Cloud Service Providers › AWS | ingest | one-time per account | DOCUMENTED aws-security-capabilities, aws-prerequisites, how-to-onboard-aws | `api_integration` |
| Scan mode chosen: Cloud Scan (recommended) or Outpost. Optional capabilities: agentless disk scanning, DSPM, Kubernetes, registry, serverless | wizard | detect | one-time | DOCUMENTED how-to-onboard-aws | `api_integration` |
| Audit Logs capability (CloudTrail via Cortex-managed or BYOB trail, SQS/SNS) so drift can be detected from events | wizard › Logging capabilities | ingest | one-time | DOCUMENTED aws-security-capabilities, aws-audit-architecture | `audit_log_export` |
| Default Cloud Posture Security Policy is on by default; rules alone create findings; a rule inside a policy scope promotes them to issues. Findings are only generated for out-of-the-box rules; custom rules produce issues only through a policy | Cloud security › Rules and Policies | detect | tenant default | DOCUMENTED cs-policies, cs-rules | `posture_policy_enabled` (new) |
| Full discovery scan every 12 hours; Event Assisted Ingestion triggers a scan of the changed resource from audit logs ("near-real-time", VMs and data assets on AWS and GCP); **Discover Now** for an on-demand scan | Asset Inventory › cloud account › Discover Now | detect | per-POV | DOCUMENTED discovery-engine | `discovery_scan_complete` (new) |
| Cloud-policy blocks (e.g. SCP) raise `[BLOCKED_BY_POLICY]` and the engine stops scanning that service for 18 hours | Discovery status › Errors | detect | per-POV | DOCUMENTED discovery-engine | `discovery_scan_complete` (new) |
| Findings queryable with `dataset = findings`; compliance via assessment profiles | XQL; Monitor and track compliance adherence | detect | n/a | DOCUMENTED review-findings | none |
| Some finding types need Premium or the posture/runtime add-on | | detect | licence | DOCUMENTED review-findings | `cloud_posture_licensed` (new) |
| A dataset named `cortex_cloud_posture` | | detect | n/a | NOT FOUND | none |
| Minutes from onboarding to first finding | | detect | n/a | NOT FOUND (docs say discovery starts automatically; no figure) | none |

### Timing and volume constraints
- Worst case for a new finding without audit-log assist is a 12-hour wait; with Event Assisted Ingestion on supported assets it is near-real-time; Discover Now forces a scan unless one is running (DOCUMENTED discovery-engine).
- CloudTrail in automated mode is retained 7 days in the S3 bucket (DOCUMENTED aws-audit-architecture).
- The module README promises findings "within its scan interval (typically 15-60 min)". The docs give 12 hours, so that figure is unsupported (INFERRED).
- Scan cadence for other connectors is configurable; the default is not stated (DOCUMENTED scan-settings; value NOT FOUND).

### Scenario-specific gotchas
- **All five**: cards read `cortex_cloud_posture`; real data is `findings` and Asset Inventory. Treat as a card/dataset fix.
- **SIM-CSPM-002 (drift)**: depends on Event Assisted Ingestion, which needs the Audit Logs capability enabled. Without it, drift appears at the next 12-hour scan.
- **SIM-CSPM-003/004/005**: join posture findings to `cloud_audit_logs`/`xdr_data` runtime; needs audit logs onboarding and, for 003, an XDR/cloud agent on the compute.
- The lab's own weak CloudTrail (`*-cspm-trail`) is a planted finding, not the audit-log source; onboarding creates or uses a separate trail (INFERRED from the BYOB/automated modes).
- A custom rule or a rule outside the default policy gives findings only if out-of-the-box, otherwise nothing: "scan ran, no issue" is a policy gap, not a miss.

### Report vs block
Posture is detective. Remediation actions exist but are manual or playbook-driven (INFERRED from the SaaS and posture pages); SCPs or permission boundaries on the POV account would show as `[BLOCKED_BY_POLICY]` and read as a miss unless the run labels them.

---

## Cloud audit log onboarding as it affects ITDR and CSPM

| Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|
| AWS CloudTrail: Audit Logs capability, one of Cortex-managed, Custom (BYOB), Control Tower BYOB; customer-managed KMS key if the bucket is KMS-encrypted (single key only) | AWS onboarding wizard | ingest | one-time | DOCUMENTED aws-security-capabilities, aws-audit-architecture | `audit_log_export` |
| Azure Activity and Entra logs via Event Hub (diagnostic settings), "Use audit logs in analytics" to normalize into `cloud_audit_logs`; dataset `MSFT_Azure_raw` | Settings › Data Sources & Integrations › Azure Event Hub | ingest | one-time | DOCUMENTED eventhub-ingest | `audit_log_export` |
| GCP audit via Pub/Sub collector | Data Sources › GCP Pub/Sub | ingest | one-time | NOT FOUND (page listed in llms.txt, not fetched) | `audit_log_export` |
| Cloud audit logs over a minimum of 5 days seed the Analytics baseline | Enable Analytics | detect | per-tenant | DOCUMENTED enable-analytics | `analytics_baseline_elapsed` (new) |

---

## Counts and next steps for the registry
- The `kind` for most rows above is `data_source`; the licence and enablement rows have no registry home (see the proposed new codes). Add them before any of this lands in `spec/dataset_requirements.yml`.
- The registry rule "do not write a marketplace pack or data-source name from memory" is satisfied: every name above came from a fetched page, but none is tenant-verified, so every `verified:` block stays empty.

## Sources (all fetched 2026-11-10)
cortex-docs.paloaltonetworks.com unless noted. Paths are under `https://cortex-docs.paloaltonetworks.com`.

- enable-analytics: /cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics/enable-the-analytics-engine-and-identity-analytics
- xsiam-identity-analytics: /cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/identity-analytics
- analytics-issues-and-biocs: /cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-issues-and-analytics-biocs.md
- view-and-manage-analytics-rules: /cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/view-and-manage-analytics-rules.md
- itdr-overview: /cortex-xdr/detect-investigate-and-respond-to-threats/identity-threat-module-itdr
- get-started-itdr: /cortex-xdr/detect-investigate-and-respond-to-threats/identity-threat-module-itdr/get-started-with-itdr.md
- set-up-cie: /cortex-xsiam/onboard-cortex-xsiam/deployment-steps/set-up-cloud-identity-engine.md
- xsiam-licenses: /cortex-xsiam/learn-about-cortex-xsiam/cortex-xsiam-product-licenses.md
- analytics alert reference index: /analytics-alerts ; /analytics-alerts/alerts-by-data-source.md ; /analytics-alerts/alerts-by-data-source/okta.md ; /analytics-alerts/alerts-by-data-source/azure-signin-log.md ; /analytics-alerts/alerts-by-data-source/windows-event-collector.md ; /analytics-alerts/alerts-by-detection-module.md
- alert pages under /analytics-alerts/alerts-by-name/ : impossible-traveler-sso, multiple-okta-mfa-requests-sent-to-a-user, a-user-received-multiple-weakly-encrypted-service-tickets, sso-password-spray, multiple-tgt-requests-for-users-without-kerberos-pre-authentication, potential-dcsync-by-an-unusual-user, single-account-excessively-locked-out, azure-application-consent, external-email-display-name-impersonation-of-internal-personnel, email-mimics-replies-or-forwards-without-an-actual-ongoing-conversation, email-attachment-with-a-potentially-malicious-file-extension, email-containing-a-redirected-link, exchange-inbox-forwarding-rule-configured, azure-mailbox-rule-creation, a-user-accessed-an-abnormal-number-of-remote-shared-folders, a-user-connected-from-a-new-country, sso-brute-force, okta-account-enumeration, suspicious-sso-authentication
- datasets-and-presets: /cortex-xsiam/reference-and-developer-docs/cortex-agentix-xql/get-started-with-xql/datasets-and-presets.md
- okta-ingest: /cortex-xdr/configure-cortex-xdr/cortex-xdr-data-sources/vendor-specific-data-sources/okta/ingest-logs-and-data-from-okta
- google-workspace-ingest: /cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/google/google-workspace/ingest-logs-and-data-from-google-workspace
- http-collector: .../vendor-specific-data-sources-and-connectors/http-log-collector/set-up-an-http-log-collector-to-receive-logs.md
- proofpoint-tap-ingest: .../vendor-specific-data-sources-and-connectors/proofpoint/proofpoint-targeted-attack-protection/ingest-logs-from-proofpoint-targeted-attack-protection.md
- o365-ingest: .../microsoft/microsoft-office-365/ingest-logs-from-microsoft-office-365.md
- m365-new: .../microsoft/microsoft-office-365/microsoft-365.md
- o365-email-ingest: .../microsoft/microsoft-office-365-email/ingest-logs-and-data-from-microsoft-365.md
- entra: .../microsoft/microsoft-entra-id.md
- eventhub-ingest: .../microsoft/azure-event-hub/ingest-logs-from-microsoft-azure-event-hub.md
- endpoint-data-collection: /cortex-xsiam/onboard-cortex-xsiam/deployment-steps/install-cortex-xdr-agents/endpoint-data-collection.md
- windows auditing: /cortex-xsiam/reference-and-developer-docs/reference/microsoft-windows-security-auditing-setup.md ; /cortex-xdr/reference-and-developer-docs/microsoft-windows-security-auditing-setup/enable-additional-event-logs-using-event-viewer.md
- wec: /cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/generic-on-premise-data-collectors/broker-vm-data-collector-applets/activate-windows-event-collector.md
- aws pages under .../cloud-service-provider-csp-onboarding/amazon-web-services-cloud-onboarding/ : aws-security-capabilities-and-deployment-planning, prerequisites-for-onboarding-aws, cortex-cloud-and-aws-audit-log-collection-architecture, aws-post-deployment-verification, how-to-onboard-amazon-web-services, onboard-amazon-web-services
- posture data sources: /cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/cloud-posture-and-runtime-security-data-sources.md
- scan-settings: /cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/administration-and-troubleshooting/manage-instances/how-to-configure-the-scanning-settings-for-supported-services.md
- cs-rules-policies (+ rules, policies): /cortex-xsiam/cloud-security/rules-and-policies/cloud-security-rules-and-policies.md ; .../cloud-security-rules.md ; .../cloud-security-policies.md
- discovery-engine: /cortex-xsiam/detect-investigate-and-respond-to-threats/asset-management/all-cloud-assets/discovery-engine.md
- issues-findings-events, review-findings: /cortex-xsiam/detect-investigate-and-respond-to-threats/investigation-and-response/case-concepts/issues-findings-and-events.md ; .../investigation-and-response/review-findings.md
- Advanced Email Security: /cortex-xsiam/detect-investigate-and-respond-to-threats/cortex-advanced-email-security/cortex-advanced-email-security-module-overview.md ; .../deploy-and-configure-the-email-security-module.md ; .../getting-started-with-the-cortex-advanced-email-security-module.md
- SaaS Security: /cortex-xsiam/cloud-security/cortex-cloud-saas-security.md ; .../setup-saas-security.md ; .../connected-apps-inventory.md ; .../detection-rules.md
- llms.txt: /llms.txt (page index)
- Secondary (marketplace, official PANW but not docs): https://cortex.marketplace.pan.dev/marketplace/details/ProofpointTAP/ ; .../details/Okta/ ; .../details/Office365/

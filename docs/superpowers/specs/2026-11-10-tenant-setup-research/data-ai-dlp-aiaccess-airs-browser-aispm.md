# Tenant-side setup: DLP, AI_ACCESS, AIRS, BROWSER, AI_SPM

Researched 2026-11-10. Cortex XSIAM 3.x docs (cortex-docs.paloaltonetworks.com, `.md` mirrors) and docs.paloaltonetworks.com. Every URL under DOCUMENTED was fetched; all are in `## Sources`. Evidence tags: DOCUMENTED / INFERRED / NOT FOUND.

**Headline: the repo's dataset names and field names for four of these five planes do not match the datasets the docs say Cortex creates.** Fix the registry rows before any POV.

| Repo says | Docs say (DOCUMENTED) |
|---|---|
| `prisma_browser_events` | `panw_prisma_access_browser_raw` (S11) |
| `cortexsim_airs_raw`, and AIRS cards reading `panw_ngfw_traffic_raw` with `uri_path`/`request_body`/`response_body` | Prisma AIRS API logs land in `panw_prisma_airs_raw` (S13). PAN-OS traffic-log fields carry no URI or body (S27) |
| `ai_spm_findings`, `cortex_cloud_posture` | AI Security datasets are `asset_inventory`, `findings`, `issues`, `classification_mgmt_data_profile` (S15) |
| `panw_dlp_raw` (TTP-2026-0179) | No such dataset documented. Enterprise DLP reaches Cortex as incidents via an integration (S14, S25) |

`spec/dataset_requirements.yml` has no rows for DLP at all, and its `cortex_side.name` is null everywhere. The names above are what to put there, each with a `verified:` block still empty (nothing here has been seen in a tenant).

---

## DLP

Cards read `dataset = xdr_data` (TTP-0177/78/80/81) and `panw_dlp_raw` (TTP-0179 only). All five scenarios are Linux shell steps under `svc-backup`: `cp` into `/tmp/cortex_mock_usb`, `zip -e`, `curl` to `httpbin.org`, a python file copy standing in for "clipboard". Two products could legitimately detect DLP behaviour, and the scenarios exercise neither: Cortex Endpoint DLP (Windows/macOS only) and Enterprise DLP on NGFW / Prisma Access (needs the traffic to cross a decrypting firewall).

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| D1 | Endpoint DLP add-on licence; Cortex agent 9.1+ on Windows (x64) or macOS; 9.4+ for file-share channel. **Linux is not a supported platform.** | Cortex › Endpoint DLP module | detect / prevent | licence | DOCUMENTED S01 | `agent_installed` (+ propose `endpoint_dlp_licensed`) |
| D2 | DLP module is pushed only to endpoints targeted by an active data-in-motion rule | Modules › Data Security › Endpoint Data-in-Motion Rules › Create New Rule › Target | detect / prevent | per-POV | DOCUMENTED S03 | propose `dlp_rule_configured` |
| D3 | A data-in-motion rule per channel (USB, web app, local app, file share) with action Allow/Block/Report, severity, and a Data Scope = data profile | same, Context & Data page | detect / prevent | per-POV | DOCUMENTED S03 | `dlp_rule_configured` |
| D4 | Endpoint DLP settings: fail-open vs fail-close default, Corporate Account Domain, browser-extension mode (MDM / forced / disable) | Endpoint DLP settings page | detect | one-time | DOCUMENTED S02 | `dlp_rule_configured` |
| D5 | Cortex data security browser extension (Chrome, Edge) installed for the web channel; endpoint must be domain-joined or use a managed browser | Endpoint DLP settings › Web DLP Channel | detect | per-host | DOCUMENTED S01, S02 | `agent_installed` |
| D6 | A data profile that defines "sensitive". Six OOTB: Developer Secrets, Financial, PCI, PHI, PII, Sensitive. Custom patterns/profiles supported. | Settings › Configurations › Data Classification › Data Patterns / Data Profiles | detect | one-time | DOCUMENTED S04 | propose `data_profile_selected` |
| D7 | Network channel: Enterprise DLP licence (or CASB-PA / CASB-X / Data Security licence) | Strata Cloud Manager / Panorama | detect / prevent | licence | DOCUMENTED S05 | propose `enterprise_dlp_licensed` |
| D8 | Enterprise DLP data profile attached to a Security policy rule (data filtering profile on Panorama, DLP rule on SCM). Inactive until attached. | SCM: Configuration › Data Loss Prevention › DLP Rules; profile group on the rule. Panorama: Objects › Security Profiles › Data Filtering | detect / prevent | per-POV | DOCUMENTED S05, S06 | propose `data_filtering_profile_attached` |
| D9 | SSL decryption policy + decryption profile for the inspected traffic; do not enable Strip ALPN | SCM: Configuration › NGFW and Prisma Access › Security Services › Decryption | detect | per-POV | DOCUMENTED S07 | `decryption_for_app_id` |
| D10 | Non-file inspection enabled in the DLP rule (otherwise text bodies are not inspected, only file uploads) | DLP Rules › Non-File Based Match Criteria | detect | per-POV | DOCUMENTED S07 | propose `dlp_non_file_inspection` |
| D11 | NGFW data-filtering hits are logged as Threat log, subtype `data`; a log-forwarding profile must send Threat logs, and Cortex must ingest the Threat log type | Objects › Log Forwarding; Cortex › Data Sources & Integrations › NGFW/Prisma Access › Select log types | ingest | one-time | DOCUMENTED S27 (subtype, PAN-OS 10.1 doc), S10 (log types) | `log_forwarding` |
| D12 | Enterprise DLP incidents into Cortex: Enterprise DLP connector (tenants onboarded after 2026-07-26) or Marketplace "Enterprise DLP" pack (older tenants); token+refresh token created in the DLP UI, Settings › Alerts | Settings › Data Sources & Integrations; DLP UI › Settings › Alerts | ingest | one-time | DOCUMENTED S14, S25 | `api_integration` |
| D13 | Dataset named `panw_dlp_raw` | n/a | ingest | n/a | NOT FOUND (the integration fetches incidents; no raw dataset is documented) | n/a |
| D14 | Predefined NGFW data-filtering patterns: Credit Card Numbers (16-digit), SSN with dashes, SSN without dashes. Enterprise DLP has 1,000+ predefined patterns; custom regex patterns supported. | Data Patterns | detect | n/a | DOCUMENTED S06, S05, S26 | n/a |
| D15 | Whether the Enterprise DLP card/SSN patterns validate the check digit or SSN area range | n/a | detect | n/a | NOT FOUND | n/a |

### Timing and volume constraints
- Rule suppression: a rule whose hits exceed a threshold is auto-disabled (Settings › Management Audit Logs shows it). Repeat runs of a scenario can silence the rule (S02). DOCUMENTED.
- Files up to 300 MB; archives scanned to 50 nesting levels (agent 9.3+). DOCUMENTED S01.
- DLP module download to an endpoint (80-110 MB) happens only after a rule targets it; allow time before the first run. DOCUMENTED S01, S03.
- Prisma Access Browser and NGFW log-type filter changes take effect in 5 minutes (S10). DOCUMENTED.

### Scenario-specific gotchas
- **SIM-DLP-001..005 (all):** steps run on Linux as `svc-backup`. Endpoint DLP has no Linux support (S01), and `cp` to `/tmp/cortex_mock_usb` is a plain directory, not a USB device. The cards' `xdr_data` queries match file/process telemetry, not DLP verdicts. Passing them proves XDR saw a process, not that DLP classified data.
- **SIM-DLP-001 markers:** the card numbers `4532-1100-8877-9912` and `5412-7500-1122-3344` fail the Luhn check (computed locally); `3782-822463-10005` passes. SSNs begin `987-`/`876-`/`765-`. Whether the classifier checks Luhn or SSN area ranges is NOT FOUND (D15), so expect at best partial matches on the Financial/PCI/PII profiles.
- **SIM-DLP-003:** `curl -sk` to `httpbin.org` is a multipart POST from a Linux process, not a browser upload. It crosses an inline Enterprise DLP enforcement point only if the host's traffic goes through a decrypting firewall or Prisma Access with D8-D10 in place. `-k` does not help: the firewall's decryption, not the client's cert check, is what exposes the body. TTP-2026-0179's `panw_dlp_raw` query will return nothing (D13).
- **SIM-DLP-005:** the "clipboard" is a python file write. Endpoint DLP clipboard/print control is a Windows/macOS browser or local-app channel; there is no clipboard channel in the data-in-motion rule options (web app, local app, USB, file share per S03).
- **SIM-DLP-002:** DLP inspects inside archives (S01) but a password-protected zip cannot be opened to classify; behaviour for encrypted archives is NOT FOUND.

### Report vs block
- Data-in-motion action Block/Report/Allow is per rule; Report logs only (S03). A Block rule changes what the user sees and can make "third-party applications send a dummy file", so the simulated upload may still complete with a placeholder (S03). Run report mode first and block second, as the repo's paired-run model does.
- Fail-close default blocks when DLP times out and raises its own issue (S02): a timeout is not a detection.

---

## AI_ACCESS

Six scenarios drive `llm_provider_egress`: authentic-shape POSTs with bogus keys to api.openai.com, api.anthropic.com, generativelanguage.googleapis.com, plus DeepSeek and OpenRouter. Cards read `panw_ngfw_traffic_raw` (37 refs), `xdr_data` (3), and declare products `ai-access-security`, `ngfw-pa-series`, `cortex-xsiam`.

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| A1 | AI Access Security licence (or CASB-PA / CASB-X / Prisma Browser standalone) + Strata Logging Service licence | Strata Cloud Manager | detect | licence | DOCUMENTED S08, S09 | propose `ai_access_licensed` |
| A2 | Minimum versions: PAN-OS 11.2.2-h1 (AI Access licence); PAN-OS 10.2.3+ / 11.1.0+ with CASB-PA/X; Enterprise DLP plugin 5.0.4+ on Panorama; SCM Essentials/Pro | NGFW / Prisma Access | detect | per-POV | DOCUMENTED S08 | `log_forwarding` |
| A3 | Traffic must traverse the NGFW / Prisma Access enforcement point. The EAL plugin must run from a host behind it. | topology | detect | per-POV | INFERRED: AI Access is an inline service (S09) | `allow_rule_for_sim_traffic` |
| A4 | Internet Access / security policy rules naming GenAI apps (sanctioned/unsanctioned/tolerated, by risk or use case) | SCM › AI Access Security policy rules | detect / prevent | per-POV | DOCUMENTED S09 | `allow_rule_for_sim_traffic` |
| A5 | Enterprise DLP data profile + DLP rule with **both file and non-file inspection** enabled; included in the AI Access licence | DLP Rules | detect | per-POV | DOCUMENTED S07, S09, S12 | `data_filtering_profile_attached` |
| A6 | SSL decryption policy and profile (Strip ALPN off) so prompt bodies are visible | SCM › Decryption | detect | per-POV | DOCUMENTED S07 | `decryption_for_app_id` |
| A7 | GenAI Prompt Logging: enable per app, only ChatGPT, Claude, Google Gemini, Perplexity selectable. Logs go to Strata Logging Service (AI Conversation Log). | SCM › Data Loss Prevention › Settings › GenAI Prompt Analysis | detect | per-POV | DOCUMENTED S12 | propose `genai_prompt_logging` |
| A8 | AI Access responses are not scanned or logged | n/a | detect | n/a | DOCUMENTED S09 | n/a |
| A9 | Cortex ingestion of NGFW/Prisma Access Traffic and Threat logs through the SLS connector (direct integration for tenants onboarded after the XDR-to-XSIAM change) | Settings › Data Sources & Integrations › Prisma Access / NGFW | ingest | one-time | DOCUMENTED S10, S28 | `log_forwarding` |
| A10 | AI Conversation Log / AI Access logs as a Cortex data source | n/a | ingest | n/a | NOT FOUND (log types offered to Cortex are Authentication, Configuration, File Data, GlobalProtect, HIP Match, System, Threat, Traffic, Tunnel, URL, User ID per S10) | n/a |
| A11 | App-ID classification of raw API endpoints (`/v1/chat/completions`) as the same GenAI apps as the web UIs | n/a | detect | n/a | NOT FOUND | n/a |

### Timing and volume constraints
- Prompt logging is "near-real-time" in SLS (S12). Ingestion lag into Cortex is not documented (NOT FOUND).
- SIM-AIACC-003 "30 sequential prompts" rate detections need a per-user baseline; no baseline period is documented for AI Access (NOT FOUND). The ABIOC detection in SIM-AIACC-006 (stolen key replay) needs a learned per-key history and is unlikely to fire in a one-day POV (INFERRED from the ABIOC design; also the scenario uses its own baseline arm).

### Scenario-specific gotchas
- **`pii` payload** (SIM-AIACC-005, rotation): `SSN: 555-00-CORTEXSIMCANARY`, `Card: 4242-4242-4242-CANARY` (plugin lines 193-197). They contain letters, so the 9-digit SSN and 16-digit card predefined patterns (S06) cannot match. A DC must add a custom regex data pattern on `CORTEXSIM` for the AI Access DLP rule to fire. INFERRED from S06 pattern definitions.
- **`secret` payload** (SIM-AIACC-002): `AKIA0000CORTEXSIMCANARY` is 23 characters; a real AWS key id is 20. A predefined AWS-key pattern anchored on length will not match. Predefined pattern internals are NOT FOUND, so verify with the profile test (S05, "Test a Data Profile") before the POV.
- **`source` payload** (SIM-AIACC-001): a "proprietary" python snippet with a `CORTEXSIM-CANARY` header. Detection needs a source-code or custom pattern; the Developer Secrets profile (S04) is not source-code detection. INFERRED.
- **SIM-AIACC-003/005** include DeepSeek and OpenRouter. Prompt Logging does not list them (A7); app-dictionary coverage is NOT FOUND.
- **Bogus bearer tokens**: provider returns 401. The README says the egress alone triggers detection. That holds for App-ID and URL logs; it holds for DLP only if decryption (A6) exposes the body before the 401.
- **SIM-AIACC-004** is tagged UC "AI Runtime Security (Prisma AIRS)" but is an AI Access egress; its jailbreak string is not a DLP pattern. No AI Access jailbreak detector is documented (NOT FOUND).
- **Lab mock target** (`127.0.0.1:8089`) never crosses a firewall; the lab-contained fallback in the scenario headers cannot produce AI Access signal.

### Report vs block
- A DLP rule set to Block (or an Internet Access rule blocking the GenAI app) stops the request before the provider; the plugin's response status becomes a reset or a block page rather than 401. Expect `prevented` shape, not `missed`. Detail of block-page behaviour is NOT FOUND. Pair report/block runs.

---

## AIRS

Five scenarios (OWASP LLM01/07/06/10/LLM01+08) run `cortex-prompt-attacker` against `cortex-vulnerable-llm` on `127.0.0.1:8089` (`cli.py` default `--host 127.0.0.1`). Cards read `panw_ngfw_traffic_raw` (29) and `cortexsim_airs_raw` (6); product `ai-runtime-security`. No file in `core/`, `sources/cortex-*`, `scenarios/airs/` or `tools/` calls the AIRS Scan API (greps for the API host, `x-pan-token`, `scan/sync` return nothing).

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| R1 | Prisma AIRS licence and deployment profile (Customer Support Portal); one API key per deployment profile, region-bound | CSP › Deployment profile; SCM › Prisma AIRS | detect / prevent | licence | DOCUMENTED S16 | propose `airs_licensed` |
| R2 | **API intercept:** the app must call the Scan API with prompts/responses; a security profile defines detectors (prompt injection, sensitive data/DLP, malicious URL, toxic content, contextual grounding, custom topics, DB security, malicious code) with Allow/Block | SCM › Insights › Prisma AIRS › AI Runtime: API intercept › Manage › Security Profiles | detect / prevent | per-POV | DOCUMENTED S16, S17 | propose `airs_api_intercept_wired` |
| R3 | **Network intercept:** an AI security profile on a firewall (VM-Series/AI Runtime Firewall) in the path between app and model; threats log as Threat logs subtype `ai-security` (Panorama) or AI Security logs (via SLS) | Panorama / SCM › Log Viewer | detect / prevent | per-POV | DOCUMENTED S18 | propose `airs_network_intercept_in_path` |
| R4 | Enable SLS log forwarding when associating the deployment profile with the TSG, so API logs exist | SCM › Deployment profile | ingest | per-POV | DOCUMENTED S18 | `log_forwarding` |
| R5 | Ingest AIRS API logs into Cortex: Strata Cloud Manager connector, product Prisma AIRS, log type "AI Runtime Security API Logs"; dataset `panw_prisma_airs_raw` | Settings › Data Sources & Integrations › Strata Cloud Manager | ingest | one-time | DOCUMENTED S13 | `api_integration` |
| R6 | Connector permissions: View/Edit on Data Sources; Super User on CSP and device accounts for cross-account | same | ingest | one-time | DOCUMENTED S13 | `api_integration` |
| R7 | Dataset `cortexsim_airs_raw` | n/a | ingest | n/a | NOT FOUND (CortexSim-defined; no PANW product writes it) | n/a |
| R8 | Network-intercept licence capacity: 10K AI transactions per day per vCPU | n/a | detect | licence | DOCUMENTED S18 | n/a |
| R9 | Scan API limits: 2 MB sync / 5 MB async payload, 100 URLs per request | n/a | detect | n/a | DOCUMENTED S16 | n/a |
| R10 | Prompt-injection detection languages: English, Spanish, Russian, German, French, Japanese, Portuguese, Italian | n/a | detect | n/a | DOCUMENTED S17 | n/a |

### Timing and volume constraints
- None documented for AIRS detection onset; detection is per-request inline (INFERRED from S16). Ingestion delay SLS to Cortex: NOT FOUND.
- SIM-AIRS-005 (LLM10 token exhaustion): rate limiting is an AIRS feature added April 2026 (S29 headline listing), details NOT FOUND; payload ceilings in R9 cap what a "DoS" prompt can be.

### Scenario-specific gotchas
- **All AIRS scenarios:** the target listens on loopback, the attacker connects to loopback, and nothing sits between them. No firewall (R3) sees the packets, no code calls the Scan API (R2). Result: zero AIRS signal by construction. To make the claim testable a lab must either (a) wrap the vulnerable app's `/owasp/*/chat` handlers in Scan API calls with an API key and profile, or (b) bind the target on a routed address and send the traffic through a firewall carrying an AI security profile.
- **`panw_ngfw_traffic_raw` queries on `uri_path`, `request_body`, `response_body`, `http_rsp_code`:** the PAN-OS traffic log has no such fields (S27 lists the field set; only an HTTP/2 flag and URL category appear). Those queries cannot return rows against a real firewall. Request/response bodies are exposed only in AIRS's own logs (S18).
- **`cortexsim_airs_raw`** rows exist only if the attacker's JSONL is shipped to a collector; the AIRS plane has no collector-POST emitter today (`airs_prompt_attack` forwards to the ECS audit pipeline, per `eal-plugin-catalog.md`).
- **Datasets that would prove AIRS:** `panw_prisma_airs_raw` (API) and Threat logs with subtype `ai-security` (network). Neither is read by any card.

### Report vs block
- Security profile actions are Allow or Block per detector (S17). With Block, the Scan API response tells the app to drop the prompt; the vulnerable LLM would have to honour it for the attack to show as prevented. With the current code it never asks, so every attack executes and reads as "missed". Network intercept in block mode terminates the stream (S18/S29, streamed-response note).

---

## BROWSER

Six scenarios: 001-005 drive `cortex-browser-attacker` with `browser_channel: prisma` against `*.cortexsim-test.invalid` hosts; 006 is shell `curl` to `127.0.0.1` plus markers. Cards read `prisma_browser_events` (28), `xdr_data` (8), `koi_code_scan_events`, `pan_dns`, `panw_ngfw_threat_raw`.

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| B1 | Prisma Browser licence (standalone or Prisma Access bundle) | Strata Cloud Manager | detect / prevent | licence | DOCUMENTED S19 | propose `prisma_browser_licensed` |
| B2 | Real Prisma Browser (or its extension) installed on the host and the user enrolled/signed in so events are attributed to a tenant | Prisma Browser deployment | detect | per-host | INFERRED: events come from the managed browser (S19, S20); enrolment steps are in pages not fetched | `agent_installed` |
| B3 | Data controls in the browser policy: File Protection (download/upload), Clipboard (copy/paste out), Screenshot, Print, GenAI Prompt, Typing Guard, etc. | SCM › Configuration › Policy › Control Sets › Data Control › Data Leak Prevention | detect / prevent | per-POV | DOCUMENTED S20, S21 | propose `browser_policy_configured` |
| B4 | Extension controls: allowed/blocked extensions, block by permissions, host permissions, force-install | Browser policy profile › Browser Security › Extensions | detect / prevent | per-POV | DOCUMENTED S19 (TOC), S22 (event types) | `browser_policy_configured` |
| B5 | Ingest into Cortex: Prisma Access Browser instance; one instance per tenant; select log types (Audit, Events, Devices); dataset `panw_prisma_access_browser_raw`; takes up to several minutes to connect | Settings › Data Sources & Integrations › Prisma Access Browser | ingest | one-time | DOCUMENTED S11, S10 | `log_forwarding` |
| B6 | Event types available to detect on: fileDownload, fileUpload, clipboardCopy, clipboardPaste, screenshot, screenshare, genAIPrompt, extensionInstall, maliciousFile, maliciousWebsite, maliciousExtension, plus others | Prisma Browser Events | detect | n/a | DOCUMENTED S22, S23, S24 | n/a |
| B7 | Browser events also reach SLS and can go to Security Lake/S3/Snowflake; tenant-detection data goes to SIEM only "if SIEM integration is enabled" | Log Viewer / settings | ingest | one-time | DOCUMENTED S19 | `log_forwarding` |
| B8 | Fields `campaign_id`, `cortex_canary`, `data_classification`, `origin`, `target_selector`, `action` in `prisma_browser_events` | n/a | detect | n/a | NOT FOUND: the documented DLP/file/extension event fields (S22, S23) use `file.*`, `browserExtension.*`, `content.*`; none of the card fields appear | n/a |
| B9 | Endpoint DLP as a second browser-channel product: Cortex data security extension for Chrome/Edge | Cortex › Endpoint DLP | detect | per-host | DOCUMENTED S01, S02 | `agent_installed` |

### Timing and volume constraints
- Connection to Cortex takes "up to several minutes" (S11); log-type filter edits apply in 5 minutes (S10). Event-to-dataset latency: NOT FOUND.

### Scenario-specific gotchas
- **Plain Chromium produces no Prisma Browser telemetry.** Prisma Browser events (S22-S24) are emitted by the managed browser. A Playwright-driven vanilla Chromium has no tenant, policy or logging. `browser_channel: prisma` implies a Prisma Browser build; `stub` and plain `chromium` channels will leave `panw_prisma_access_browser_raw` empty. INFERRED from S19/S20.
- **`*.cortexsim-test.invalid` never resolves** (RFC 2606 reserved TLD). `navigate` with `wait_for` on a form selector will time out unless the lab maps these names in DNS or hosts and serves a page. INFERRED. Without a loaded page, paste/click steps do not execute.
- **Card field names** (B8): the card queries filter on `data_classification in ("credential",...)` and `campaign_id`. Prisma Browser's clipboard events carry content classification only when a data profile is attached in the Clipboard control (S21); the `campaign_id` and `cortex_canary` fields are CortexSim's and will not exist in tenant data. Cards need to key on `clipboardPaste`, destination URL and the DLP profile name instead.
- **SIM-BROWSER-003** (sideload `.crx`): `install.type` includes `sideload`/`development` (S22); the extension events fire only if the browser honours the install attempt. Managed-extension policy block (B4) produces a policy event, not an install event.
- **SIM-BROWSER-005** screenshot: the event is `screenshot` / `screenshare` (S23) and needs the Screenshot control; headless Playwright `page.screenshot()` is a CDP call that does not use the browser's user-level screenshot path (INFERRED), so it may not register.
- **SIM-BROWSER-006**: pure endpoint shell (`curl http://127.0.0.1/...`, SAFE-MODE file writes). It needs the XDR agent (`xdr_data`) and nothing from Prisma Browser; tag it endpoint, not BROWSER, in readiness checks.
- **Prisma Browser event ingestion into Cortex**: DLP verdicts depend on the Enterprise DLP profile for `Cloud & Local` detection (S05).

### Report vs block
- Browser rules can Block, Allow with warning, or Bypass (S19 mentions bypass events). A blocked file download / paste yields a policy event with the blocked action; the campaign's own step still "ran". Distinguish with the event's action field (not captured in the fetched pages: NOT FOUND for exact name).

---

## AI_SPM

Seven scenarios (001-006 backed by `infra/modules/aws/ai-spm`, 007 an emulated GCP Vertex AI double-agent). Cards read `cortex_cloud_posture` (19), `cloud_audit_logs` (10), `ai_spm_findings` (7), `xdr_data` (3); product `cortex-cloud`.

| # | Requirement | Where | Needed for | Scope | Evidence | Registry code |
|---|---|---|---|---|---|---|
| S1 | Licence: Cloud Posture Security, Cloud Runtime Security or XSIAM Premium | Cortex licence | detect | licence | DOCUMENTED S15, S30 | propose `cloud_ai_security_licensed` |
| S2 | Comprehensive CSP onboarding (not Foundational) of the AWS account; Cloud Scan or Outpost mode; IaC-provisioned permissions | Settings › Data Sources & Integrations › Cloud Service Providers › Amazon Web Services | detect | one-time | DOCUMENTED S31, S32 | `api_integration` |
| S3 | Supported AWS AI services: Bedrock, Bedrock AgentCore, SageMaker AI, S3 Vectors. Also self-managed models, MCP servers, SaaS AI agents. Azure and GCP (Vertex AI, Gemini Enterprise Agent Platform) listed. | AI Security › AI Inventory | detect | n/a | DOCUMENTED S33 | n/a |
| S4 | DSPM enabled for sensitive-data findings (training data PII/PHI/PCI); Agentless Disk Scanning + DSPM for MCP-server discovery | CSP instance › Additional Security Capabilities | detect | per-POV | DOCUMENTED S32, S33 | `api_integration` |
| S5 | Scan settings (managed services, self-managed assets, cadence) editable per account; default cadence applies if unset | Data Sources & Integrations › Cloud Instances › account › Edit › Data assets classification options | detect | per-account | DOCUMENTED S34 | n/a |
| S6 | Audit-log collection (CloudTrail via automated or manual) for the `cloud_audit_logs` cards | CSP onboarding › Log Collection Configuration | ingest | one-time | DOCUMENTED S32 | `audit_log_export` |
| S7 | RBAC: AI Security View / View-Edit; plus Cases & Issues, Dashboards, Asset Inventory, Query Center as recommended | Settings › Roles | detect | one-time | DOCUMENTED S35 | n/a |
| S8 | Data lives in `asset_inventory`, `findings`, `issues`, `classification_mgmt_data_profile`; UI under Modules › AI Security (Dashboard, Inventory, Issues, Detection Rules) | XQL / Modules | detect | n/a | DOCUMENTED S15, S35 | n/a |
| S9 | Scan cadence / time from onboarding to first AI asset and finding | n/a | detect | n/a | NOT FOUND (docs give none; the repo module README says 10-15 minutes, unsourced) | n/a |
| S10 | Detection of an OpenAI key hardcoded in a Lambda environment | n/a | detect | n/a | NOT FOUND (Lambda-to-third-party AI is not in the supported-services list S33; serverless-functions scanning is a separate onboarding option S32) | n/a |
| S11 | Detection of insecure pickle model artifacts in S3 | n/a | detect | n/a | NOT FOUND (no pickle/serialization finding in fetched AI Security pages) | n/a |
| S12 | Custom AI risk rules | AI Security › Detection Rules | detect | optional | DOCUMENTED S30 | n/a |

### Timing and volume constraints
- Cadence is not published (S9); the docs explicitly tell you to verify visibility during the evaluation. Do not promise findings inside a one-day POV without checking in the tenant first.
- AI agents and model endpoints are marked inactive by 30-day invocation history (S37) so freshly created canary endpoints will show no usage trend. DOCUMENTED.
- Cost note from the repo module: the SageMaker endpoint and GPU instance are billed hourly; the shadow GPU is opt-in (`enable_shadow_gpu`).

### Scenario-specific gotchas
- **Registry datasets:** `ai_spm_findings` and `cortex_cloud_posture` are not documented dataset names (S15 lists four others). Repoint cards to `findings` / `issues` / `asset_inventory`; the use-case XQL in S15 shows the join shape (`xdm.finding.asset_id`, `xdm.asset.type.class = "AI"`).
- **SIM-AISPM-001..006 findings table** (module README): of eight claimed findings, two have no AWS Terraform resource (Bedrock invocation logging off; vulnerable Lambda layer) per the module's own "Deferred" list, so they cannot be detected. Three (S10, S11 above) have no documented Cortex detection. The shadow-GPU Ollama VM maps to "self-managed AI models" (S33) only when the GPU option is enabled, and S4 notes discovery of self-managed assets depends on scanning settings (S34).
- **SIM-AISPM-007** (GCP Vertex AI): no GCP IaC module exists (Phase C pending in repo); the scenario emits `xdr_data` process events and `cloud_audit_logs` GCP-style rows from a host. A real Vertex AI finding needs a GCP project onboarded (S33 lists Vertex AI) and a deployed reasoning engine; neither is provisioned.
- **Azure note (not used by repo today):** outpost scan mode and DSPM are mandatory for full Azure AI discovery (S33).
- **Prisma AIRS cross-link:** AIRS (Aug 2026 release) can import AI Security posture data from Cortex Cloud (S29 listing). Not required for this plane.

### Report vs block
- Posture only; detect-only. Nothing is blocked. A findings/issues row appearing is the pass. Remediation actions exist in the product but are out of scope.

---

## Counts and open items for the registry

- New `requirement` codes proposed: `endpoint_dlp_licensed`, `dlp_rule_configured`, `data_profile_selected`, `enterprise_dlp_licensed`, `data_filtering_profile_attached`, `dlp_non_file_inspection`, `ai_access_licensed`, `genai_prompt_logging`, `airs_licensed`, `airs_api_intercept_wired`, `airs_network_intercept_in_path`, `prisma_browser_licensed`, `browser_policy_configured`, `cloud_ai_security_licensed`.
- Dataset rows to add or correct in `spec/dataset_requirements.yml`: `panw_prisma_access_browser_raw` (replaces `prisma_browser_events`), `panw_prisma_airs_raw`, `asset_inventory`, `findings`, `issues`, `classification_mgmt_data_profile`; DLP has no dataset (incident integration instead).
- Not checked: enrolment steps for Prisma Browser (separate onboarding pages), Prisma Access Browser log-type names, exact AWS permission list for AI Security, App-ID behaviour for raw LLM API calls.

## Sources

Fetched in this session.

- S01 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/protect-your-endpoints/endpoint-dlp-1/cortex-data-loss-prevention-dlp-module-overview.md
- S02 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/protect-your-endpoints/endpoint-dlp-1/configure-dlp-end-to-end/configure-endpoint-dlp-settings.md
- S03 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/protect-your-endpoints/endpoint-dlp-1/configure-dlp-end-to-end/create-data-in-motion-rules.md
- S04 https://cortex-docs.paloaltonetworks.com/data-security-documentation/data-security/data-classification/what-is-cortex-data-classification.md
- S05 https://docs.paloaltonetworks.com/enterprise-dlp/administration/configure-enterprise-dlp/create-an-enterprise-dlp-data-profile
- S06 https://docs.paloaltonetworks.com/network-security/security-policy/administration/security-profiles/security-profile-data-filtering
- S07 https://docs.paloaltonetworks.com/enterprise-dlp/administration/configure-enterprise-dlp/enterprise-dlp-and-ai-apps/create-a-security-policy-rule-for-chatgpt
- S08 https://docs.paloaltonetworks.com/ai-access-security/activation-and-onboarding/ai-access-security-setup-prerequisites
- S09 https://docs.paloaltonetworks.com/ai-access-security/getting-started/introducing-ai-access-security
- S10 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/log-type-filtering.md
- S11 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/prisma-access-browser/ingest-logs-from-prisma-access-browser.md
- S12 https://docs.paloaltonetworks.com/ai-access-security/administration/genai-prompt-analysis
- S13 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/strata-cloud-manager.md
- S14 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/enterprise-dlp.md
- S15 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/cloud-security/cortex-cloud-ai-security/how-to-perform-advanced-ai-security-investigations-using-xql.md
- S16 https://docs.paloaltonetworks.com/prisma-airs/ai-runtime-security/airs-api/ai-runtime-security-api-intercept-overview
- S17 https://docs.paloaltonetworks.com/ai-runtime-security/administration/prevent-network-security-threats/api-intercept-create-configure-security-profile
- S18 https://docs.paloaltonetworks.com/ai-runtime-security/administration/detect-and-alert-on-malicious-traffic
- S19 https://docs.paloaltonetworks.com/prisma-access-browser/administration/investigate-prisma-access-browser-events
- S20 https://docs.paloaltonetworks.com/prisma-browser/administration/manage-prisma-browser-policy-profiles/configure-prisma-browser-data-controls/configure-data-leak-prevention
- S21 https://docs.paloaltonetworks.com/content/techdocs/en_US/prisma-browser/administration/manage-prisma-browser-policy-profiles/configure-prisma-browser-data-controls/configure-data-leak-prevention/clipboard.html
- S22 https://docs.paloaltonetworks.com/content/techdocs/en_US/prisma-browser/administration/investigate-prisma-browser-events/prisma-browser-event-tyoes/prisma-browser-extension-events.html
- S23 https://docs.paloaltonetworks.com/content/techdocs/en_US/prisma-browser/administration/investigate-prisma-browser-events/prisma-browser-event-tyoes/prisma-browser-data-protection-dlp-events.html
- S24 https://docs.paloaltonetworks.com/content/techdocs/en_US/prisma-browser/administration/investigate-prisma-browser-events/prisma-browser-event-tyoes/prisma-browser-malware-protection-events.html
- S25 https://xsoar.pan.dev/docs/reference/integrations/palo-alto-networks-enterprise-dlp
- S26 https://docs.paloaltonetworks.com/enterprise-dlp/getting-started/about-enterprise-data-loss-prevention
- S27 https://docs.paloaltonetworks.com/pan-os/10-1/pan-os-admin/monitoring/use-syslog-for-monitoring/syslog-field-descriptions/traffic-log-fields and .../threat-log-fields (PAN-OS 10.1, older version)
- S28 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations.md
- S29 https://docs.paloaltonetworks.com/prisma-airs/getting-started/ai-runtime-security-overview (index excerpt of New Features listing only)
- S30 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/cloud-security/cortex-cloud-ai-security/what-is-cortex-cloud-ai-security.md
- S31 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/cloud-service-provider-csp-onboarding/understand-csp-onboarding-tiers-and-licensing.md
- S32 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/cloud-service-provider-csp-onboarding/amazon-web-services-cloud-onboarding/how-to-onboard-amazon-web-services.md
- S33 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/cloud-security/cortex-cloud-ai-security/supported-services-in-cortex-cloud-ai-security.md
- S34 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/administration-and-troubleshooting/manage-instances/how-to-configure-the-scanning-settings-for-supported-services.md
- S35 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/reference-and-developer-docs/role-based-access-control/cloud-security-and-posture-management-permissions/ai-security-permissions.md
- S37 https://cortex-docs.paloaltonetworks.com/cortex-xsiam/cloud-security/cortex-cloud-ai-security/cortex-cloud-ai-security-use-cases.md

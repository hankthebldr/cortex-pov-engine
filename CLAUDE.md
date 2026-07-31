# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

CortexSim — an enterprise detection simulation engine for Palo Alto Networks Domain Consultants. It generates controlled, high-fidelity signals into customer Cortex environments (XSIAM/XDR) to validate detection logic across the full `detection_type` vocabulary — **`BIOC | XQL | Analytics | Correlation | IOC | ABIOC`** — plus the XDM modeling-rule normalization substrate (`detections.modeling_rules[]`) and stitching/grouping. Think "MITRE Caldera's opinionated nephew" — not a red team C2, but a detection quality assurance engine.

**No Cortex API connection** *(Phase 1 rule; relaxed in Phase 9 Health & Config track)*. SimCore generates signals INTO the environment via agent-based execution. As of Phase 9 it MAY make **opt-in, read-only** calls to a registered XSIAM tenant for health/metrics (`/healthcheck`, XQL over `metrics_*`) — see `docs/superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md`. It still does **not** write to Cortex and does **not** read alerts OUT for detection auto-validation (that track is parked).

## Build & Run Commands

### Quick start (local dev)
```bash
cp .env.example .env        # set CORTEXSIM_MASTER_KEY etc.
./scripts/dev-up.sh         # one-shot: builds the image + brings up SimCore via docker compose
```
`scripts/dev-up.sh` is the canonical easy-deploy entry point; `.env.example`
documents every required/optional env var (including the master-key guard the
compose stack enforces).

### Full Bootstrap (Linux jumpbox)
```bash
./install.sh   # handles deps, submodules, Go build, Rust builds, React build, Docker Compose
```

### SimCore (FastAPI backend)
```bash
docker compose up -d --build          # start SimCore container (port 8888)
docker compose logs -f simcore        # live logs
docker compose down                   # stop

# Local dev (outside Docker) — requires Python 3.11:
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r core/requirements.txt
cd core && CORTEXSIM_ENV=development CORTEXSIM_BASE_DIR=$(pwd)/.. uvicorn main:app --host 0.0.0.0 --port 8888 --reload
```

### React UI
```bash
cd ui
npm install
npm run dev       # dev server with hot reload (proxies /api to localhost:8888)
npm run build     # production build to ui/dist/
```
UI build output must be copied to `core/static/` for SimCore to serve. The Vite dev server proxies `/api` to `http://localhost:8888`.

### Go Beacon Agent
```bash
cd agent && go build -o ../bin/cortexsim-agent .
./bin/cortexsim-agent --server http://localhost:8888 --id my-jumpbox --interval 10
```
Go module: `github.com/hankthebldr/cortexsim/agent` — Go 1.21+, stdlib only (no external deps).

### Rust Submodule Tools (signalbench, ackbarx, xdrtop)
```bash
cd sources/<tool> && cargo build --release
```

## Architecture

**Three-tier system:**

1. **SimCore** (`core/`) — FastAPI orchestrator, port 8888. Loads scenarios from YAML, manages tool lifecycle, dispatches tasks to agents or generates push bundles.
2. **cortexsim-agent** (`agent/`) — Go pull-model beacon. Polls SimCore for tasks, executes via identity harness, streams output back.
3. **React UI** (`ui/`) — SPA served by SimCore's static file mount at `/`. Three-column layout: plane selector → scenario browser/launcher → tool status. Plus MITRE heatmap and results validation views.

**Execution modes:**
- **Pull** — agent polls SimCore, receives task, executes with identity harness, reports back
- **Push** — SimCore generates self-contained bash bundle or K8s YAML; DC downloads and executes offline

**Execution lifecycle (pull mode is end-to-end working).** Launch → orchestrator seeds Results + enqueues a step task → the Go beacon polls `/api/agents/{id}/tasks`, iterates `steps[]` resolving identity agent-side, and POSTs per-step `/output` → `/complete`. Operators can **abort** a live run (`POST /api/runs/{id}/abort` → `aborted` state; the agent polls `GET /api/runs/{id}/control` every 2s and kills the in-flight process group). Live progress streams over **SSE** (`GET /api/runs/{id}/events` scoped + `GET /api/events` global). Agent liveness is **online/stale/offline**, derived from `last_seen` age with a background heartbeat sweep that emits `agent.status` SSE frames. The task queue is now **durable** (GAP-API-005 closed): it is a write-through cache over the `queued_tasks` DB table and is rehydrated on startup (`orchestrator.rehydrate()`), so a SimCore restart restores undelivered tasks and fails any orphaned `running` run whose task was lost. Push-mode runs reach a terminal `staged` state on bundle generation. Full surface: `docs/reference/api-and-agent-surface.md`.

**Identity harness** — every TTP step runs via a service account (`www-data`, `postgres`, `node`, `nobody`, etc.) to create realistic process causality chains in XSIAM. The harness wraps commands with `runuser -l`, `sudo -u`, or `su -s /bin/bash`. Push and pull resolve a step's identity from one shared spec (`spec/identity_harness.json`, loaded by `core/engine/identity_spec.py`; a Go test guards drift).

**Measurement loop (`core/connectors/`)** — the OPTIONAL read-back path that closes detection efficacy. SimCore still generates signal IN and does **not** require a Cortex connection, but when an integration credential is configured (the encrypted vault, `/api/credentials/integrations`), a `Connector` (the `xsiam` connector ships) pulls observed alerts and the pure `matcher` auto-validates seeded `Result` rows on technique/detection-id/name within a time window → real, evidence-backed MTTD (no manual checkbox). Surfaces: `GET /api/connectors`, `POST /api/runs/{id}/observations` (manual batch, offline), `POST /api/runs/{id}/reconcile?connector=xsiam` (credential-backed pull), and an **opt-in** background `auto_reconcile_loop` (`CORTEXSIM_AUTO_RECONCILE`, off by default — it makes outbound tenant calls). The HTTP transport is injectable so tests never hit the network.

**Agent onboarding** — the front door is the **enrollment-token** flow: mint a TTL/max-uses/revocable token (`POST /api/agents/enroll/tokens`), run one line on the jumpbox (`curl '<server>/api/agents/install?token=…' | bash`), and SimCore *assigns* the agent id via `POST /api/agents/enroll` (no more self-asserted `--id`). Legacy `POST /api/agents/register` + explicit-id installer remain for back-compat.

### Core Module Structure

- `core/main.py` — FastAPI app entry, lifespan handler (init DB → load scenarios → init tool instantiator)
- `core/config.py` — Pydantic Settings from env vars (`CORTEXSIM_PORT`, `CORTEXSIM_ENV`, etc.)
- `core/database.py` — async SQLAlchemy with SQLite at `{BASE_DIR}/data/cortexsim.db`
- `core/models.py` — ORM: Scenario, Run, Result (with MTTD timing), ToolInstance, Agent (all have `.to_dict()`)
- `core/api/` — FastAPI routers: scenarios, runs (with report export), results (with validation), tools, agents, mitre (coverage heatmap data), uctc (UC/TC index read surface), pov (entitlement scoping)
- `core/engine/` — scenario_loader (YAML→DB with Pydantic validation), orchestrator (auto-seeds Result rows from expected_detections), push_generator, uctc_registry (v2.2 index → frozen dataclasses), verifier (scores runs against thresholds)
- `core/tools/` — `registry.py` (static TOOL_REGISTRY dict) + `instantiator.py` (subprocess lifecycle manager)
- `core/planes/` — declarative `PlaneDescriptor` registry, one frozen-dataclass module per active plane (edr, cdr, ndr, itdr, cloud_app, analytics, ai_access, airs, ai_spm, asm, browser, cspm, koi, tim); `base.py` defines the descriptor model and `__init__.py` the registry

### Key Data Flows

- **Run launch** → orchestrator creates Run record → auto-seeds Result rows from scenario `expected_detections` (one per detection per step) with `executed_at` timestamp
- **Detection validation** → DC marks results as observed via `PUT /api/results/{id}/validate` → sets `observed_at` → `mttd_seconds` computed as `observed_at - executed_at`
- **Report export** → `GET /api/runs/{id}/report?format=markdown` generates Cortex-branded POV report with coverage stats and MTTD metrics
- **MITRE coverage** → `GET /api/mitre/coverage` aggregates technique coverage across all scenarios/runs for the heatmap UI

### Key Design Rules

- **No wrapper code around external tools.** The Tool Instantiation Layer calls real binaries with their native CLI flags. SimCore is the process manager, not a translation layer. `TOOL_REGISTRY` holds `run_template` strings formatted and passed to `subprocess.Popen`.
- **Scenarios are YAML source-of-truth.** DB stores run history only. Scenarios load from `scenarios/` on startup.
- **Schema validation is strict.** Scenario loader validates every YAML against the Pydantic schema derived from `scenarios/_schema.yml`. Invalid files are rejected at startup.
- **All API responses are structured JSON** — including errors: `{"error": "...", "code": "...", "detail": "..."}`.
- **Push bundles must be self-contained** — execute on clean Ubuntu 22.04 with no SimCore dependency at runtime.

## Scenario YAML

Scenarios live in `scenarios/{plane}/` (e.g., `scenarios/edr/edr-001-credential-dumping.yml`). Schema reference is `scenarios/_schema.yml`. ID format: `SIM-{PLANE}-{NNN}`.

Every scenario has: UC/TC alignment refs, MITRE ATT&CK mapping, execution identity config, ordered steps with expected detections per step, and cleanup commands.

**Causality contract (optional, additive, back-compat).** Scenarios without these fields load unchanged; declaring them collapses the causality graph's synthetic `cortexsim-agent` star into one connected CGO→process→process spine (`core/engine/causality_graph.py`). The scenario loader + `.claude/hooks/lint-scenario.py` validate all four:
- **`cgo_anchor`** (scenario-level): `{image_name, primary_username?}` — the realistic initial-access process that OWNS the chain (e.g. `apache2`/`www-data`, a phishing `winword.exe`, a k8s runtime). Labels/names the CGO root node instead of the default `cortexsim-agent`/`root`. Persisted to the `Scenario` ORM (`cgo_anchor` JSON column; prod needs `ALTER TABLE scenarios ADD COLUMN cgo_anchor JSON`).
- **`causality`** (per-step): `{parent_step, pivot?}` — this step's process descends from an EARLIER step's process via the declared edge kind. The loader rejects forward/self refs and allows at most one root step (the step that omits `causality`, which links from the CGO). `pivot` ∈ `process_lineage · network_session · endpoint_network_stitch · shared_entity · exposure_exploit · exploit_impact · temporal` (default `process_lineage`). A `process_lineage` pivot chains parent→child process nodes; any non-process pivot emits its own typed edge and leaves the step rooted at the CGO.
- **`platforms`** (per-step): subset of `linux · windows · macos · container · k8s`.
- **`platform_variants`** (per-step): `{os: command}` per-OS equivalents so a cross-platform TTP is exercised across environments (keys must be in the enum; lint warns if a key isn't also in that step's `platforms`).

## UC/TC Alignment (FY27 v2.2 index)

The FY27 Use-Case / Test-Case master index is the sales-motion source of truth. Its versioned snapshot lives at `docs/uc_tc_mapping/_v2.2-source/` (**49 UC · 203 UCS · 266 TC · 140 POV-SC payloads · 38 SKU**) and is loaded at boot by `core/engine/uctc_registry.py` into frozen dataclasses.

**Scenario refs are a validated foreign key into that index, not free text.** Every scenario carries `uc_ref`, `tc_ref` (primary), `tc_refs[]` (the full evidence set, always containing `tc_ref`), and `pov_scenario_id`, plus the measurement contract (`validation_methodology`, `methodology_family` F1..F10, `primary_kpi`, `threshold`, `success_criteria`, `moat_tier`, `correlation_window_seconds`, `stitching_key`, `required_planes_in_incident`). `required_base_platform` / `required_addons` are **derived at load** from the registry, never authored. `core/engine/scenario_loader.py` enforces codes **S-10** (`tc_ref` not in index, ERROR) · **S-11** (unknown `uc_ref`, ERROR) · **S-12** (`tc_ref` parent ≠ `uc_ref`, ERROR) · **S-13** (tier disagreement, WARNING) · **S-14** (bound TC is POS/PLT/AUT, WARNING) · **S-15** (dangling `tc_refs[]` entry, ERROR) · **S-16** (unknown `pov_scenario_id`, WARNING). ERRORs reject only when `CORTEXSIM_STRICT_REFS` is true — **it defaults true**. If the snapshot is absent the registry degrades to advisory and never rejects, so a stripped deploy still boots.

**In-product surface.** `core/api/uctc.py` serves the index read-only at `GET /api/uctc/{summary,use-cases,use-cases/{id},test-cases,test-cases/{id},coverage,gaps,payloads,by-scenario/{id}}`, joined to the engine's own evidence (`Scenario.tc_refs` → `Run.tc_verdict`). The console destination is **UC / TC Index** under *Analyze* (`ui/src/components/console/UcTcIndexView.jsx`, `#/uctc`, deep-linkable via `?uc=&tc=`). `core/api/pov.py` scopes the corpus to a tenant entitlement set (`GET /api/pov/profiles`, `/capabilities`, `POST /api/pov/scope`) and generates the upsell list with real `PAN-*` part numbers. Every `/api/uctc` response carries `{index_loaded, index_version}` — a snapshot-less deploy returns **200 with `index_loaded: false`**, which callers must render as degraded, never as "0 test cases".

**Do not "fix" these.** 100 S-13 tier disagreements and 13 S-14 posture bindings are deliberate positioning calls (see `docs/uc_tc_mapping/index-gaps-v2.2.md`). 57 of the 107 detection-backable TCs carry no measurable threshold (`is_scoreable: false`) and are surfaced as such. Evidence never joins through `pov_scenario_id` — `POV-SC-001` alone binds 21 test cases. Three namespaces have historically been called "UC"/"TC"; only the index is canonical — `detection_scanner/ttps/*.json` uses card-local `threat_scenario_id` (`TS-*`) / `threat_step_id` and a guard test fails if a card id ever takes a `UC-`/`TC-` prefix again. Current state: **161/161 scenarios resolve, zero S-10/S-11/S-12/S-15; 84 of 266 index TCs evidenced (65 of 107 DET/HNT)**. `make check-refs` (CI job `refs`) walks the real corpus through the loader under strict mode — that gate is what makes the enforcement meaningful. Authoritative doc: `docs/uc_tc_mapping/README.md`; the crosswalk is hand-authored in `scripts/uctc_crosswalk_v2.2.py` (`--report` / `--emit` / `--apply`).

## Detection Planes

> **Per-plane counts below are a snapshot and drift easily.** The counted
> ground truth is `python3 scripts/uctc_crosswalk_v2.2.py --report` and
> `docs/reference/README.md`; when the two disagree, the command wins.

| Plane | Cortex Engine | Status |
|-------|--------------|--------|
| CDR | Cortex Cloud / Prisma Cloud Compute | 26 scenarios (container enum · cryptominer · container escape · k8s lateral · wildfire trigger · systemd/cron persistence (`SIM-CDR-006`) · cluster/container posture sweep wiring Trivy/Kube-bench/Kubescape/Gitleaks/Cloudsplaining (`SIM-CDR-007`) · IAM-key abuse + S3 exfil (`SIM-CDR-008`) · **sock-shop microservices behavioral anomaly — first ABIOC (`SIM-CDR-009`)** · ransomware-in-container (`SIM-CDR-010`) · k8s-goat escape chain (`SIM-CDR-011`) · WildFire-in-container + misconfig (`SIM-CDR-012`) · insecure-deployment ELF detonation (`SIM-CDR-013`) · malicious-k8s multi-stage ABIOC chain (`SIM-CDR-014`) · **XDM modeling-rule substrate proof (`SIM-CDR-015`)** — all six ported from `xsiam-prisma-cdr-lab`) + IaC module (EKS) |
| EDR | Cortex XDR Agent | 21 scenarios (credential dumping · reverse shell · persistence · defense evasion · lateral movement · LSASS memory dump (`SIM-EDR-006`) · ESXi inhibit-recovery (`SIM-EDR-007`) · Linux ransomware impact (`SIM-EDR-008`) · rclone bulk exfil (`SIM-EDR-009`)) + IaC module (diverse Linux targets) |
| NDR | Network Security / Firewall Analytics | 12 scenarios (C2 HTTP beacon · DNS tunnel · Stratum cryptojacking · SMB lateral sweep · bulk HTTPS exfil (`TTP-2026-0068`) · FTP cleartext+STOR · SSH outbound+KEXINIT) + IaC module (3 stitching patterns) + per-protocol EAL plugins (`c2_http_beacon`, `dns_tunnel_exfil`, `stratum_tcp_connect`, `smb_rpc_sweep`, `bulk_https_exfil`, `ftp_egress`, `ssh_egress`) |
| ITDR | Cortex ITDR | 20 scenarios (active) — 5 synthetic IdP audit-log emissions via the `idp_signin_emulator` EAL plugin (impossible travel, MFA fatigue, credential stuffing, token replay, brute-force lockout) · `SIM-ITDR-006` AD offline-roasting harvest (AS-REP Roast + Kerberoast via `TOOL-IMPACKET`/`TOOL-RUBEUS`) · `SIM-ITDR-007` AD privilege-escalation chain wiring Pypykatz/BloodyAD/KrbRelayUp/PrintSpoofer/Tokenvator · `SIM-ITDR-008` helpdesk-MFA-reset account takeover — IaC module (AD lab with seeded roastable accounts) |
| CSPM | Cortex Cloud Posture Management | 5 scenarios (`cspm-001` posture misconfiguration sweep against the module's planted findings) + IaC module (intentional misconfigs) |
| ASM | Cortex Attack Surface Management | 6 scenarios (`asm-001` exposed-surface discovery · `asm-002` vuln-scan recon · `asm-003` OSINT victim-info — first Reconnaissance (TA0043) coverage · `asm-004` web-app enumeration + injection wiring WhatWeb/Gobuster/Feroxbuster/Nikto/SQLmap/Commix/CMSeek) + IaC module (multi-service exposed host) |
| TIM | Cortex Threat Intel Management | 6 scenarios (`tim-001` TAXII IOC-feed match · `tim-002` adversary infrastructure staging — first Resource Development (TA0042) coverage) + IaC module (TAXII + fake C2) |
| Cloud App | Cortex Cloud App Security | 9 scenarios (active) — outbound OAuth 2.0 authorize requests against Okta / Microsoft / Google with planted risky scopes via the `oauth_grant_emulator` EAL plugin (Phase 9) |
| Analytics | XSIAM Correlation Engine | 20 multi-plane stitching scenarios (`scenarios/multi_plane/mp-001..020-*.yml`; `SIM-MP-006` is the NGFW↔container 5-tuple+10s causality stitch) |
| AI_ACCESS | Cortex AI Access Security | 5 scenarios (active) — outbound to OpenAI/Gemini/Anthropic via the `llm_provider_egress` EAL plugin (Phase 4) with planted DLP markers |
| AIRS | Cortex AI Runtime Security | 5 scenarios (active) — OWASP LLM01-10 against `cortex-vulnerable-llm` driven by `cortex-prompt-attacker` + `airs_prompt_attack` EAL plugin |
| BROWSER | Prisma Browser | 6 scenarios (active) — Playwright-driven via `cortex-browser-attacker` + `browser_attack_runner` EAL plugin (Phase 6) |
| KOI | Agentic endpoint / supply-chain | 8 scenarios (active) — MCP / skills / extensions / PyPI via `cortex-malicious-agentic-pack` artifact pack + `agentic_egress` EAL plugin (Phase 5) · `SIM-KOI-006` MCP runtime tool-response poisoning (connect-time↔runtime trust gap, CVE-2025-49596/-54136) |
| AI_SPM | Cortex AI Security Posture Management | 7 scenarios (active, `sim-aispm-001..007`) — AI asset discovery, model security assessment, AI supply-chain, static risk analysis, sensitive-data, security dashboard — backed by 6 TTP cards (`TTP-2026-0054..0059`) + dedicated `infra/modules/aws/ai-spm` IaC module (14 resources, 8 planted findings) |
| EMAIL | Cortex XSIAM / NG-SIEM (Proofpoint TAP + M365 ingestion) | 5 scenarios (active, `sim-email-001..005`) — phishing credential-link · malicious attachment · BEC/executive impersonation · thread-hijack reply-chain — via the `email_emitter` EAL plugin (synthetic `proofpoint_tap_raw` / `msft_o365` events to a collector, ITDR-pattern) + endpoint/identity correlation stitch. Third-party log ingestion + correlation, NOT a first-party PANW product surface |

> **EAL plane scope.** The EAL Traffic Simulator (`core/eal_simulator/`) now hosts two families of plugins. (1) The original **signal-injection** plugins (network/identity/SaaS/AI/browser/email shapes the identity harness cannot produce). (2) The **analytics log-streamer family** — spine `core/eal_simulator/analytics_emitter.py` plus per-data-source emitters — that POST **shape-true audit/log JSON** to an operator-supplied collector (HTTP log collector / XSIAM Broker VM) so a customer can validate their **Analytics / ABIOC** detections fire on that data source, mirroring the `idp_signin_emulator` / `email_emitter` collector-POST pattern. The **21 EAL plugins** cover NDR, ITDR, Cloud App, AI Access, AIRS, Browser, KOI, EMAIL, **and** the analytics-streamer data sources: AWS/GCP CloudTrail + cloud storage/compute → `cloud_audit_logs` (`cloud_audit_emitter`, `cloud_storage_compute_emitter`); Azure Activity/Audit → `msft_azure_audit` (`azure_audit_emitter`); Kubernetes audit → `kubernetes_audit_logs` (`k8s_audit_emitter`); M365/Exchange → `msft_o365_audit` (`m365_activity_emitter`); Active Directory/Windows → `msft_windows_security` (`ad_windows_emitter`); PAN-OS NGFW Enhanced Application Logs (`ngfw_eal_emitter`); and Okta/Entra IdP sign-in → `okta_sso` (the extended `idp_signin_emulator`). The cloud-audit-log emitters drive the CDR-plane cloud-audit scenarios (`SIM-CDR-019..026`) — a log-record signal shape distinct from the **identity harness** endpoint-process causality that still serves the EDR/CDR container-endpoint scenarios and `signalbench` telemetry. Only **CSPM, ASM, TIM, and Analytics** carry no EAL plugin: CSPM/ASM/TIM are served by IaC-planted findings, and Analytics is a cross-plane correlation layer over the others. See `docs/reference/eal-plugin-catalog.md`.

> **Canonical scenario count.** The counted ground truth (verified 2026-07-25, analytics log-streamer + ABIOC/Analytics content pass) is **161 loadable scenarios** across **15 detection planes**, all `status: active`, loaded by the schema validator at boot with **0 rejected · 0 dangling `ttp_ref` · 0 dangling `adapter_ref`**. This pass adds the **14 `TTP-2026-0154..0167` analytics-streamer pairs** (AWS/GCP CloudTrail, cloud storage & compute, Azure Activity/Audit, Kubernetes audit, M365/Exchange, Active Directory/Windows, NGFW-EAL, and Okta/Entra IdP sign-in) whose **ABIOC + Analytics** detections fire on shape-true logs POSTed by the new **analytics log-streamer EAL plugin family** to an operator-supplied collector → **147 + 14 = 161**. It registers two real XSIAM ingestion datasets in `validate.py` KNOWN_DATASETS (`msft_azure_audit`, `kubernetes_audit_logs`). The prior 147 F5/F10 baseline is the 135 Kali-toolkit baseline plus **12 correlation/ABIOC-terminal `TTP-2026-0142..0153` pairs** (Unit 42-sourced) that light up the previously-empty **F5 Automation & Workflow (0→3)** and **F10 Qualitative Evidence (0→2)** methodology families and deepen Resource Development (TA0042, 1→3) and Reconnaissance (TA0043, 4→6): `SIM-MP-020` (BlackSuit ransomware-precursor auto-containment), `SIM-ITDR-016` (closed-loop account auto-disable), `SIM-CLOUD-007` (auto-revoke OAuth tokens), `SIM-EDR-021` (GentleKiller ThrottleStop.sys BYOVD EDR-kill ABIOC), `SIM-ITDR-017` (AD CS ESC1→PKINIT ABIOC), `SIM-TIM-004/005/006` (TLS-fingerprint infra pivot, code-signing-cert impersonation IOC, edge-VPN probing surge), `SIM-EMAIL-005` (AiTM session-token theft ATO), `SIM-ASM-005/006` (WSUS CVE-2025-59287 exposure-to-RCE, Scattered Spider OSINT vishing precursor), `SIM-CSPM-005` (exposed-.env code-to-cloud extortion) → **135 + 12 = 147**. This pass registers the first-party `xsiam_incidents` dataset in `validate.py` KNOWN_DATASETS (queried by the correlation-terminal closed-loop/SLA-measurement queries). The 135 Kali baseline itself is the 125 two-track baseline plus **8 causality-strong `TTP-2026-0132..0139` pairs** (`SIM-EDR-019` Akira/Howling-Scorpius vCenter→ESXi, `SIM-EDR-020` CL-UNK-1068 web-shell→in-memory-Mimikatz+FRP, `SIM-ITDR-014` ROADtools/roadtx Entra token abuse, `SIM-MP-016` Muddled-Libra Okta admin takeover, `SIM-MP-017` React2Shell pod-RCE→cloud-control-plane CVE-2025-55182, `SIM-MP-018` TeamPCP supply-chain, `SIM-KOI-008` Shai-Hulud npm worm, `SIM-AISPM-007` GCP Vertex-AI double-agent), plus the **2 chainable Kali kill-chains** — `TTP-2026-0140`/`SIM-MP-019` (external→internal recon→exploit→lateral, 12 adapters) and `TTP-2026-0141`/`SIM-ITDR-015` (internal AD enum→poison→crack→lateral, 7 adapters) → **125 + 8 + 2 = 135**. See `docs/reference/kali-toolkit.md`. This pass also installs the **causality contract** (see the Scenario YAML section's optional `cgo_anchor` / per-step `causality` / `platforms` / `platform_variants` fields): `core/engine/causality_graph.py` now builds a real CGO-rooted, parent→child **connected** process spine instead of a synthetic `cortexsim-agent` star — the connectedness sweep confirms 100 % (53/53) of process_lineage-spine scenarios yield a connected `proc:`-sourced chain and 114/117 (97.4 %) of contract scenarios are non-star; 114 cards were retuned to key on `causality_actor_process_*` + `causality_id`. The detection corpus is **161 TTP cards** (`detection_scanner/ttps/*.json`, **1356 resolvable catalog detection objects** — BIOC/XQL/correlation/IOC/**ABIOC** + the **XDM modeling-rule substrate** `detections.modeling_rules[]` — plus analytics-module references). **All 1001 scenario `detection_id` slugs resolve to a card detection object (GAP-4 held, 1001/1001).** `make validate` is green (**328 pass / 0 warn / 0 fail**). A **coverage-analyzer** (`detection_scanner/scripts/coverage_report.py`, `make coverage` / `make coverage-strict`) reports MITRE / plane / detection-type / methodology-family coverage vs floors. The `detection_type` vocabulary is six: `BIOC | XQL | Analytics | Correlation | IOC | ABIOC` (ABIOC = PANW-authored, auto-tuned behavioral-ML with a causality chain); XDM modeling rules are a normalization **substrate**, surfaced/exported but counted informationally, not a detection_type. Do not conflate the raw `.yml` file count with the scenario count (loader skips `_schema.yml` + AIRS probes + browser campaigns + multi-plane packages). Authoritative inventory: `docs/reference/scenario-catalog.md`; counted ground truth in `docs/reference/README.md`.

## Submodules (`sources/`)

10 git submodules under `sources/` — **do not edit source files in these directories**. Key tools:
- **signalbench** (Rust) — MITRE-mapped endpoint telemetry generator
- **mocktaxii** (Python) — STIX/TAXII 2.1 server, port 9000
- **gocortexbrokenbank** (Python) — vulnerable CI/CD app, port 9001
- **ackbarx** (Rust) — SNMP trap forwarder to XSIAM HTTP
- **xdrtop** (Rust) — terminal live XSIAM/XDR monitor
- **atomic-red-team** — Atomic TTP library

In-tree (not submodules):
- **cortex-vulnerable-llm** (Python/Flask, `sources/cortex-vulnerable-llm/`) — deliberately
  vulnerable LLM target for AIRS validation. One Flask blueprint per OWASP LLM01–LLM10
  vulnerability backed by a deterministic regex canary. No real LLM calls, no API keys.
  CLI: `cortex-vulnerable-llm serve --port 8089 --vuln all`. See its README + the design
  brief at `docs/eal-simulator/research-dvllm-prompt-attacker.md`.
- **cortex-prompt-attacker** (Python, `sources/cortex-prompt-attacker/`) — Probe →
  Mutator → Target → Scorer pipeline for AIRS validation. promptmap-compatible YAML
  (no GPL imports — schema only), PyRIT-shape mutator chain, garak-shape JSONL output.
  CLI: `cortex-prompt-attacker run --probes <dir> --target-url <url> --out events.jsonl`.
  Driven by the `airs_prompt_attack` EAL plugin. Probe pack lives under
  `scenarios/airs/probes/`.
- **cortex-malicious-agentic-pack** (`sources/cortex-malicious-agentic-pack/`) —
  static artifact tree for KOI detection validation. Six components: typosquat
  MCP server, malicious MCP server with hidden injection in tool replies,
  backdoored PyPI package (post-install subprocess on import), malicious Claude
  skill (`Ignore previous instructions` in skill.md), VS Code extension
  (`activationEvents:["*"]` + reads `~/.aws/credentials`), Chrome extension
  (`<all_urls>` + cookies + webRequest). All side effects are gated on
  `CORTEXSIM_C2_URL` so static scanning is safe. Driven by the
  `agentic_egress` EAL plugin which tarballs and POSTs the artifact against an
  authorised staging host so the NGFW sees the egress shape.
- **cortex-browser-attacker** (Python/Playwright, `sources/cortex-browser-attacker/`)
  — drives real Chromium / Prisma Browser through a YAML-declared sequence of
  browser actions (navigate, paste, copy, click, download, install_extension,
  screenshot). Playwright is an optional extra so unit tests use a `StubDriver`
  that never spins up a browser. Driven by the `browser_attack_runner` EAL plugin
  with the same shell-out-to-CLI pattern as `airs_prompt_attack`. JSONL output
  shape rhymes with garak `Attempt` + cortex-prompt-attacker so SOC tooling
  consumes both streams the same way.

## Tool Adapter Framework

Declarative `ToolAdapter` model — one YAML per security tool under `tools/packs/<tool>.yml` — telling the engine where a tool lives, how to install/invoke it, its dual-use safety class, and which Cortex plane its signal lands on. Scenarios reference adapters by id (`external_tools[].adapter_ref: TOOL-NMAP`) instead of hand-rolling CLI. Loaded + validated at boot (`core/tools/adapter_loader.py` → `adapter_catalog.py`), consumed by scenario_loader, orchestrator, infra_generator, and `GET /api/tools/adapters`. 5-tier model (1 in-tree · 2 submodule · 3 IaC-provisioned · 4 runtime-fetched · 5 external-only). **84 packs** ship across all 5 tiers (Phase A/B/C complete; **42 scenarios wired via `adapter_ref`, 45 distinct adapters referenced** — the 2026-07-24 Kali-toolkit pass added **15 tier-4 Kali adapters** (hydra, netexec, enum4linux-ng, wpscan, ffuf, wfuzz, responder, metasploit, john, hashcat, theharvester, smbmap, evil-winrm, dnsrecon, amass) chained through the 2 new kill-chains `SIM-MP-019`/`SIM-ITDR-015`; see `docs/reference/kali-toolkit.md`). Push bundles self-install tier-4 tools and refuse to auto-stage c2 frameworks; gated adapters require launch consent (`consent.simulation_authorized` / `c2_authorized`). `tools/` must be in the image (Dockerfile `COPY tools/`) or the catalog loads empty.

**Full doc + current state (shipped vs pending): [`docs/tool-adapters.md`](docs/tool-adapters.md).** Design spec: `docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md`. Pack authoring: `tools/packs/README.md`.

## CI & Quality Gates

`.github/workflows/ci.yml` runs a **5-job matrix** on push / PR / manual dispatch:

- **backend** — Python test suite (`pytest`, runs inside the prod image; **3316 pass / 3 fail / 220 skip** on the full `tests/` tree as of the 2026-07-31 verification). The 3 failures are `tests/e2e_isolated/test_tier_b_push_bundle.py` on `edr-013` / `edr-017` / `edr-021`, which emit PowerShell steps into a bash bundle — a known open defect in the push generator, not a regression.
- **agent** — Go beacon `build` + `vet` + `test -race -count=1`.
- **ui** — vitest + `vite build`.
- **detection** — detection-corpus validator (**328 pass / 0 warn / 0 fail**) + deterministic export regeneration check (`sha256sum -c`, SKELETON=0).
- **adapters** — `scripts/check-adapter-sources.sh`: tier-2 adapter source trees (git submodules) **must exist on disk** (FAIL if missing — GAP-ADAPT-01 guard); tier-4 runtime-fetched misses are WARN only.

`make -n ci` enumerates the local equivalents. Run `scripts/check-adapter-sources.sh`
standalone to preflight adapter source availability before pushing.

## Cortex Branding

UI uses specific Cortex design tokens — `--cortex-navy: #003366`, `--cortex-teal: #00C0E8`, `--cortex-steel: #6B7E8E`. Plain CSS (no Tailwind). Font: Inter for UI, JetBrains Mono for code. See `ui/src/styles/cortex-theme.css`.

## Spec Reference

`CORTEXSIM_AGENT_CONTEXT.md` in repo root is the complete Phase 1 build specification. Section numbers (4.1–4.6) correspond to deliverables. Phase 2 preview is in Section 11 — context only, do not build yet.

## IaC Topology Generator

The IaC generator produces Terraform bundles Torque can consume as blueprints. Phase A supports AWS with `base`, `edr`, `cdr`, and `content-library` modules. The DC selects modules + parameters in the UI and downloads a tar.gz bundle containing a ready-to-apply root Terraform config plus all selected module directories.

### Key paths

- `infra/modules/{provider}/{module}/` — Terraform modules (+ `content.yml`, `README.md` with YAML frontmatter)
- `infra/templates/*.j2` — Jinja2 root-bundle templates rendered by the generator
- `infra/blueprints/` — generated bundles (gitignored)
- `core/engine/infra_generator.py` — core generation logic (uses `shutil.copytree` with ignore callback to strip `.terraform/` artifacts)
- `core/engine/infra_catalog.py` — module metadata loader (reads README.md frontmatter + content.yml)
- `core/engine/infra_models.py` — Pydantic request/response models
- `core/api/infra.py` — `/api/infra/*` endpoints
- `core/content_loader.py` — merges `/opt/cortexsim/content/installed.json` into TOOL_REGISTRY at startup
- `core/tools/registry.py` — now exposes `STATIC_TOOL_REGISTRY` (built-ins) and runtime `TOOL_REGISTRY`
- `scripts/jumpbox/install-content.sh` — runs on provisioned jumpbox via cloud-init; clones/installs each module's declared content

### API endpoints

- `POST /api/infra/generate` — generate a bundle, returns `bundle_id` and `download_url`
- `GET  /api/infra/modules[?provider=aws]` — list available modules
- `GET  /api/infra/bundles` — list previously generated bundles
- `GET  /api/infra/bundles/{bundle_id}/download` — download tar.gz

### Design rules (IaC-specific)

- **Base module always included** in any bundle (enforced in `InfraGenerator._normalize_modules`).
- **Static TOOL_REGISTRY always wins** over installed-content entries — `content_loader` never overwrites.
- **Module metadata lives in `README.md` frontmatter**, not in Python — adding a module is filesystem-only.
- **Bundles are stateless artifacts** — no DB schema. File-system is source of truth.
- **Never commit `.terraform/` or `.terraform.lock.hcl`** into module directories — they pollute generated bundles. The generator also strips them via an `ignore` callback.

### Scenario schema additions

Scenarios may optionally declare `required_content` (open-source tool repos needed) and `infra_modules_needed` (IaC module names to pre-select). Both default to empty lists — existing scenarios load unchanged.

### Tests

Python tests live under `tests/`. Run: `.venv/bin/pytest tests/ -v`. The suite covers Pydantic models, module catalog, generator (including a regression guard that bundles don't contain `.terraform` artifacts), API endpoints, and content loader. The `tests/conftest.py` sets `CORTEXSIM_BASE_DIR` to the repo root via `setdefault` so tests resolve `infra/modules/` correctly.

### Phase scope

- **Phase A** (done): AWS + `base`, `edr`, `cdr`, `content-library`
- **Phase B-1** (done): AWS + `itdr`, `ndr`
- **Phase B-2** (done): AWS + `cspm`, `asm`, `tim`, `telemetry-replay`
- **Phase B-3** (done): AWS + `ai-spm` (AI Security Posture Management — 14 resources, 8 planted findings; backs the AI_SPM plane)
- **Phase C** (pending): GCP provider port of all above
- **Phase D** (pending): Azure provider port of all above
- **Phase E** (design only): `onprem` provider type (Ansible + Docker Compose)

AWS is feature-complete with **11 modules** covering every active detection plane (`base`, `edr`, `cdr`, `content-library`, `itdr`, `ndr`, `cspm`, `asm`, `tim`, `telemetry-replay`, `ai-spm`). Full design: `docs/superpowers/specs/2026-04-20-iac-topology-generator-design.md`.

### CSPM, ASM, TIM, telemetry-replay modules (AWS)

**`cspm`** — Intentionally misconfigured AWS resources for Cortex Cloud CSPM validation. Plants 9 findings: public S3 bucket, unversioned bucket, no-KMS bucket, SG with SSH open to world, SG with DB ports (3306/5432/6379) open to world, IAM role with `AdministratorAccess`, IAM user with wildcard `iam:*` policy, unencrypted EBS volume, weak CloudTrail (no log-file-validation, no multi-region, no global events). Every resource tagged with `CortexSimCSPMFinding=<type>` for easy cross-reference.

**`asm`** — Deliberately exposed public EC2 running nginx (directory listing + bait files), weak TLS (self-signed + RSA-1024), SSH on non-standard port 2222 with password auth, Redis on 6379 with no auth, fake Elasticsearch banner on 9200, gocortexbrokenbank on 9001. Plus a separate public-website S3 bucket. For validating that Cortex ASM discovers and enumerates the full attack surface.

**`tim`** — TAXII 2.1 server (mocktaxii) + fake C2 HTTP endpoint + Route53 private zone with 5 IOC-style subdomain records (`c2-beacon`, `exfil-drop`, `payload-delivery`, `dga-1a2b3c`, `cryptominer-pool`) that resolve to the fake C2. Produces both the IOC feed *and* the matching outbound traffic for testing stitched IOC+NDR+EDR detection.

**`telemetry-replay`** — Content-only module (no Terraform resources). Clones curated EVTX/PCAP/JSON attack datasets (EVTX-ATTACK-SAMPLES, mordor, cyber_simulation, ML datasets, EDR-Telemetry coverage comparisons) plus replay tooling (chainsaw, tcpreplay, sigma-rules-crawler). For POVs focused on parser/correlation validation without live attack execution.

### AI_SPM module (AWS)

**`ai-spm`** — AI Security Posture Management lab for Cortex AI-SPM validation. 14 Terraform resources plant 8 posture findings (exposed AI assets, weak model-endpoint config, AI supply-chain risk, sensitive-data exposure, etc.) that the 6 AI_SPM scenarios (`sim-aispm-001..006`) and their TTP cards (`TTP-2026-0054..0059`) exercise. Backs the 13th active detection plane (AI_SPM). See `infra/modules/aws/ai-spm/README.md` and `scenarios/ai_spm/`.

### ITDR and NDR modules (AWS)

**`itdr`** — Windows AD lab: Domain Controller (Server 2022) auto-promotes to new forest on boot, seeds 50 users + 5 Kerberoast-vulnerable service accounts (weak password + SPN set) + 1 AS-REP-Roastable DA-equivalent account. Workstations (Server 2022 Core) auto-join the domain via user_data. Content: Impacket, Rubeus, Certipy, SharpHound/BloodHound, Mimikatz, msInvader. DA password stored in SSM SecureString.

**`ndr`** — Network topology for firewall+XDR stitching: VPC Flow Logs enabled, attack endpoint in DMZ generates controlled C2/DNS-tunnel traffic against `testmynids.org`, log collector (nginx + ackbarx) accepts HTTP/syslog/SNMP from NGFW. Three stitching patterns via `ndr_stitching_pattern` var:
- `marketplace_vmseries` — PAN VM-Series from AWS Marketplace (DC brings license)
- `external_ngfw_forward` — existing customer NGFW forwards logs to collector (default)
- `suricata_lab` — Suricata IDS stand-in for labs without NGFW

### Multi-plane stitching scenarios

`scenarios/multi_plane/mp-*.yml` (5 scenarios) — scenarios with `plane: ANALYTICS` that exercise XSIAM's correlation engine across firewall + endpoint + identity + cloud planes (the on-disk filenames are `mp-NNN-*.yml`; the scenario IDs are `SIM-MP-NNN`):

- **SIM-MP-001** (`mp-001-c2-beacon-ngfw-xdr-stitch.yml`) — C2 beacon callback stitching NGFW session logs with XDR process lineage
- **SIM-MP-002** (`mp-002-kerberoast-lateral-smb.yml`) — Kerberoast → Pass-the-Hash → DCSync chain correlating ITDR + EDR + NDR signal
- **SIM-MP-003** (`mp-003-data-staged-exfil-dns-tunnel.yml`) — Staged exfiltration via DNS tunnel, XDR stage detection + NGFW DNS anomaly stitched
- **SIM-MP-004** (`mp-004-apt29-cloud-cred-theft.yml`) — APT29-style cloud credential theft; the only multi-plane scenario shipping a self-contained runnable package under `scenarios/multi_plane/packages/SIM-MP-004/`
- **SIM-MP-005** (`mp-005-cross-plane-correlation.yml`) — cross-plane correlation incident stitching endpoint + network + identity signal

### On-prem provider (Phase E, design only)

Future phase adds `"onprem"` as a fourth provider alongside aws/gcp/azure. Modules emit Ansible playbooks + Docker Compose instead of Terraform HCL. DC supplies target host inventory; playbooks configure existing VMs as CortexSim targets. See the design doc's On-Prem Provider addendum for details.

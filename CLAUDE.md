# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

CortexSim — an enterprise detection simulation engine for Palo Alto Networks Domain Consultants. It generates controlled, high-fidelity signals into customer Cortex environments (XSIAM/XDR) to validate detection logic (BIOC, Analytics, IOC, stitching/grouping). Think "MITRE Caldera's opinionated nephew" — not a red team C2, but a detection quality assurance engine.

**No Cortex API connection.** SimCore is standalone — it generates signals INTO the environment via agent-based execution; it does not read alerts OUT of Cortex.

## Build & Run Commands

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

**Identity harness** — every TTP step runs via a service account (`www-data`, `postgres`, `node`, `nobody`, etc.) to create realistic process causality chains in XSIAM. The harness wraps commands with `runuser -l`, `sudo -u`, or `su -s /bin/bash`.

### Core Module Structure

- `core/main.py` — FastAPI app entry, lifespan handler (init DB → load scenarios → init tool instantiator)
- `core/config.py` — Pydantic Settings from env vars (`CORTEXSIM_PORT`, `CORTEXSIM_ENV`, etc.)
- `core/database.py` — async SQLAlchemy with SQLite at `{BASE_DIR}/data/cortexsim.db`
- `core/models.py` — ORM: Scenario, Run, Result (with MTTD timing), ToolInstance, Agent (all have `.to_dict()`)
- `core/api/` — FastAPI routers: scenarios, runs (with report export), results (with validation), tools, agents, mitre (coverage heatmap data)
- `core/engine/` — scenario_loader (YAML→DB with Pydantic validation), orchestrator (auto-seeds Result rows from expected_detections), push_generator, uctc_mapper
- `core/tools/` — `registry.py` (static TOOL_REGISTRY dict) + `instantiator.py` (subprocess lifecycle manager)
- `core/planes/` — per-plane modules: edr, cdr, ndr, itdr, cloud_app, analytics

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

## Detection Planes

| Plane | Cortex Engine | Status |
|-------|--------------|--------|
| CDR | Cortex Cloud / Prisma Cloud Compute | 5 scenarios + IaC module (EKS) |
| EDR | Cortex XDR Agent | 5 scenarios + IaC module (diverse Linux targets) |
| NDR | Network Security / Firewall Analytics | 5 scenarios + IaC module (3 stitching patterns) + EAL simulator |
| ITDR | Cortex ITDR | IaC module (AD lab with seeded roastable accounts) |
| CSPM | Cortex Cloud Posture Management | IaC module (intentional misconfigs) |
| ASM | Cortex Attack Surface Management | IaC module (multi-service exposed host) |
| TIM | Cortex Threat Intel Management | IaC module (TAXII + fake C2) |
| Cloud App | Cortex Cloud App Security | Planned |
| Analytics | XSIAM Correlation Engine | 3 multi-plane stitching scenarios |
| AI_ACCESS | Cortex AI Access Security | 5 scenarios (active) — outbound to OpenAI/Gemini/Anthropic via the `llm_provider_egress` EAL plugin (Phase 4) with planted DLP markers |
| AIRS | Cortex AI Runtime Security | 5 scenarios (active) — OWASP LLM01-10 against `cortex-vulnerable-llm` driven by `cortex-prompt-attacker` + `airs_prompt_attack` EAL plugin |
| BROWSER | Prisma Browser | 5 scenarios (draft) — Playwright-driven via `cortex-browser-attacker` (Phase 6) |
| KOI | Agentic endpoint / supply-chain | 5 scenarios (active) — MCP / skills / extensions / PyPI via `cortex-malicious-agentic-pack` artifact pack + `agentic_egress` EAL plugin (Phase 5) |

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
- **Phase C** (pending): GCP provider port of all above
- **Phase D** (pending): Azure provider port of all above
- **Phase E** (design only): `onprem` provider type (Ansible + Docker Compose)

AWS is feature-complete with **10 modules** covering every active detection plane. Full design: `docs/superpowers/specs/2026-04-20-iac-topology-generator-design.md`.

### CSPM, ASM, TIM, telemetry-replay modules (AWS)

**`cspm`** — Intentionally misconfigured AWS resources for Cortex Cloud CSPM validation. Plants 9 findings: public S3 bucket, unversioned bucket, no-KMS bucket, SG with SSH open to world, SG with DB ports (3306/5432/6379) open to world, IAM role with `AdministratorAccess`, IAM user with wildcard `iam:*` policy, unencrypted EBS volume, weak CloudTrail (no log-file-validation, no multi-region, no global events). Every resource tagged with `CortexSimCSPMFinding=<type>` for easy cross-reference.

**`asm`** — Deliberately exposed public EC2 running nginx (directory listing + bait files), weak TLS (self-signed + RSA-1024), SSH on non-standard port 2222 with password auth, Redis on 6379 with no auth, fake Elasticsearch banner on 9200, gocortexbrokenbank on 9001. Plus a separate public-website S3 bucket. For validating that Cortex ASM discovers and enumerates the full attack surface.

**`tim`** — TAXII 2.1 server (mocktaxii) + fake C2 HTTP endpoint + Route53 private zone with 5 IOC-style subdomain records (`c2-beacon`, `exfil-drop`, `payload-delivery`, `dga-1a2b3c`, `cryptominer-pool`) that resolve to the fake C2. Produces both the IOC feed *and* the matching outbound traffic for testing stitched IOC+NDR+EDR detection.

**`telemetry-replay`** — Content-only module (no Terraform resources). Clones curated EVTX/PCAP/JSON attack datasets (EVTX-ATTACK-SAMPLES, mordor, cyber_simulation, ML datasets, EDR-Telemetry coverage comparisons) plus replay tooling (chainsaw, tcpreplay, sigma-rules-crawler). For POVs focused on parser/correlation validation without live attack execution.

### ITDR and NDR modules (AWS)

**`itdr`** — Windows AD lab: Domain Controller (Server 2022) auto-promotes to new forest on boot, seeds 50 users + 5 Kerberoast-vulnerable service accounts (weak password + SPN set) + 1 AS-REP-Roastable DA-equivalent account. Workstations (Server 2022 Core) auto-join the domain via user_data. Content: Impacket, Rubeus, Certipy, SharpHound/BloodHound, Mimikatz, msInvader. DA password stored in SSM SecureString.

**`ndr`** — Network topology for firewall+XDR stitching: VPC Flow Logs enabled, attack endpoint in DMZ generates controlled C2/DNS-tunnel traffic against `testmynids.org`, log collector (nginx + ackbarx) accepts HTTP/syslog/SNMP from NGFW. Three stitching patterns via `ndr_stitching_pattern` var:
- `marketplace_vmseries` — PAN VM-Series from AWS Marketplace (DC brings license)
- `external_ngfw_forward` — existing customer NGFW forwards logs to collector (default)
- `suricata_lab` — Suricata IDS stand-in for labs without NGFW

### Multi-plane stitching scenarios

`scenarios/multi_plane/SIM-MP-*.yml` — scenarios with `plane: ANALYTICS` that exercise XSIAM's correlation engine across firewall + endpoint + identity planes:

- **SIM-MP-001** — C2 beacon callback stitching NGFW session logs with XDR process lineage
- **SIM-MP-002** — Kerberoast → Pass-the-Hash → DCSync chain correlating ITDR + EDR + NDR signal
- **SIM-MP-003** — Staged exfiltration via DNS tunnel, XDR stage detection + NGFW DNS anomaly stitched

### On-prem provider (Phase E, design only)

Future phase adds `"onprem"` as a fourth provider alongside aws/gcp/azure. Modules emit Ansible playbooks + Docker Compose instead of Terraform HCL. DC supplies target host inventory; playbooks configure existing VMs as CortexSim targets. See the design doc's On-Prem Provider addendum for details.

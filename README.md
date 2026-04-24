# CortexSim — Detection Simulation Engine

Enterprise-grade, Cortex-branded detection simulation platform for Palo Alto Networks Domain Consultants. Replaces ad-hoc scripts with a structured, UC/TC-aligned simulation engine that directly validates Cortex detection logic.

> **Analogy:** MITRE Caldera's opinionated nephew — not a red team C2, but a *detection quality assurance engine*. Controlled, high-fidelity signal generation that validates BIOC, Analytics, IOC, and stitch/grouping logic in XSIAM/XDR.

---

## Quick Deploy

### Prerequisites
- Ubuntu 22.04 LTS+ or Debian 12+ jumpbox
- Internet access (for dependency installation and submodule clone)

### One-command install
```bash
git clone https://github.com/hankthebldr/cortexsim.git
cd cortexsim
./install.sh
```

Or via curl:
```bash
curl -sSL https://raw.githubusercontent.com/hankthebldr/cortexsim/main/install.sh | bash
```

`install.sh` handles everything: system deps, submodules, Go agent build, Rust tool builds, React UI build, Docker Compose startup.

---

## What Gets Deployed

```
SimCore (FastAPI)   →  http://localhost:8888   (Cortex-branded React UI + REST API)
cortexsim-agent     →  ./bin/cortexsim-agent   (pull-model execution agent)
```

### Start the pull agent
```bash
./bin/cortexsim-agent --server http://localhost:8888 --id my-jumpbox --interval 10
```

### Manage SimCore
```bash
docker compose ps                  # status
docker compose logs -f simcore     # live logs
docker compose down                # stop
docker compose up -d --build       # restart / rebuild after changes
```

---

## Detection Planes

| Plane | Cortex Engine | Phase 1 Scenarios |
|-------|--------------|-------------------|
| **CDR** | Cortex Cloud / Prisma Cloud Compute | 5 (container enum, cryptominer, escape, K8s lateral, WildFire) |
| **EDR** | Cortex XDR Agent | Coming in Phase 2 |
| **NDR** | Network Security / Firewall Analytics | Coming in Phase 2 |
| **ITDR** | Cortex ITDR | Coming in Phase 2 |
| **Cloud App** | Cortex Cloud App Security | Coming in Phase 2 |
| **Analytics** | XSIAM Correlation Engine | Coming in Phase 2 |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  SimCore (FastAPI, port 8888)                   │
│  ┌──────────┐  ┌─────────────┐  ┌────────────┐ │
│  │ Scenario │  │ Orchestrator│  │  Tool      │ │
│  │ Loader   │  │ (pull/push) │  │Instantiator│ │
│  └──────────┘  └─────────────┘  └────────────┘ │
│       ↓               ↓                ↓        │
│  scenarios/      Agent Task        sources/     │
│   (YAML)          Queue           (submodules)  │
└─────────────────────────────────────────────────┘
         ↑ HTTP poll                ↑ native CLI
┌────────────────┐         ┌──────────────────────┐
│ cortexsim-agent│         │ signalbench / ackbarx│
│ (pull model)   │         │ mocktaxii / xdrtop   │
└────────────────┘         └──────────────────────┘
```

**Execution modes:**
- **Pull** — agent polls SimCore, receives task, executes with identity harness, streams output back
- **Push** — SimCore generates a self-contained bash bundle or K8s YAML; DC downloads and executes offline

**Identity harness** — every TTP step runs via a service account (`www-data`, `postgres`, `node`, `nobody`, etc.) to create realistic process causality chains in XSIAM.

---

## Repository Layout

```
cortexsim/
├── install.sh              ← jumpbox bootstrap (one-liner)
├── docker-compose.yml      ← SimCore container
├── .gitmodules             ← 10 tool submodules
├── core/                   ← SimCore FastAPI app (Python 3.11)
├── agent/                  ← Go pull-model beacon agent
├── ui/                     ← React 18 + Vite frontend
├── scenarios/              ← YAML scenario library (UC/TC tagged)
│   └── cdr/                ← 5 CDR scenarios (Phase 1)
├── sources/                ← git submodules (do not edit)
├── scripts/                ← generated push bundles land here
├── bin/                    ← compiled binaries (cortexsim-agent)
├── logs/                   ← runtime logs
└── docs/                   ← UC/TC mapping, threat report index
```

---

## Phase 1 Scenarios (CDR)

| ID | Scenario | Key TTPs | UC Ref |
|----|----------|----------|--------|
| SIM-CDR-001 | Container Enumeration via DEEPCE | T1613, T1082 | UCS-CDR-01 |
| SIM-CDR-002 | Cryptominer Deployment (XMRig/Unit42) | T1496, T1105 | UCS-CDR-02 |
| SIM-CDR-003 | Container Escape via Privileged Mode | T1611, T1610 | UCS-CDR-03 |
| SIM-CDR-004 | K8s Lateral Movement + Persistence | T1021.001, T1053.005 | UCS-CDR-04 |
| SIM-CDR-005 | WildFire Malware Trigger | T1105, T1486 | UCS-CDR-05 |

---

## No Cortex API Connection Required

SimCore is fully standalone. It generates signals *into* the customer's Cortex environment via agent-based execution — it does not read alerts *out of* Cortex. The DC validates detections manually in the XSIAM console.

---

*CortexSim v1.0 | Owner: Henry Reed, DC2 GTM NAM Cortex | Phase 2 in development*

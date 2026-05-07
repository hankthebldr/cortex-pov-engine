# CortexSim — Detection Simulation Engine

Enterprise-grade, Cortex-branded detection simulation platform for Palo
Alto Networks Domain Consultants. Replaces ad-hoc scripts with a
structured, UC/TC-aligned simulation engine that directly validates
Cortex detection logic across the XSIAM / XDR / AIRS / Browser / KOI
surfaces.

> **Analogy:** MITRE Caldera's opinionated nephew — not a red team C2,
> but a *detection quality assurance engine*. Controlled, high-fidelity
> signal generation that validates BIOC, Analytics, IOC, prompt-injection
> classifiers, and stitch/grouping logic in XSIAM/XDR.

---

## Quick Deploy

Landing page with the latest install one-liners and verified downloads:
**https://hankthebldr.github.io/cortexsim/**

### Prerequisites
- Ubuntu 22.04 LTS+ or Debian 12+ jumpbox (or laptop for dev mode)
- Python 3.11+
- Internet access (for dependency installation and submodule clone)

### One-line install — Linux
```bash
git clone https://github.com/hankthebldr/cortex-pov-engine.git
cd cortex-pov-engine
./install.sh
```

`install.sh` handles everything: system deps, submodules, Go agent
build, Rust tool builds, React UI build, Docker Compose startup.

### Local dev (no Docker)
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r core/requirements.txt
cd core && CORTEXSIM_ENV=development CORTEXSIM_BASE_DIR=$(pwd)/.. \
  uvicorn main:app --host 0.0.0.0 --port 8888 --reload
```

---

## Releases & Packaging

- **Container image:** [`ghcr.io/hankthebldr/cortexsim`](https://github.com/hankthebldr/cortexsim/pkgs/container/cortexsim) — multi-arch (`linux/amd64`, `linux/arm64`), tagged `:vX.Y.Z` and `:latest`.
- **GitHub Releases:** https://github.com/hankthebldr/cortexsim/releases — every `v*.*.*` tag publishes the image, stage-2 installer bundles, `manifest.json`, and `SHA256SUMS` via [`.github/workflows/release.yml`](.github/workflows/release.yml).
- **Landing page:** [`docs/site/`](docs/site/) — Cortex-branded GitHub Pages site, redeployed on every release by [`.github/workflows/pages.yml`](.github/workflows/pages.yml).
- **Cutting a release:** `git tag v0.1.0 && git push origin v0.1.0` (or `Actions → Release → Run workflow`).

---

## What Gets Deployed

```
SimCore  (FastAPI, port 8888)   →  React UI + REST API + EAL simulator
cortexsim-agent                 →  pull-model execution agent
EAL Traffic Simulator           →  /api/eal/* — campaign launcher + plugins
cortex-vulnerable-llm           →  AIRS canary target (Phase 2)
cortex-prompt-attacker          →  AIRS probe runner (Phase 3)
```

### Manage SimCore
```bash
docker compose up -d --build       # start
docker compose ps                  # status
docker compose logs -f simcore     # live logs
docker compose down                # stop
```

### Run the pull agent
```bash
./bin/cortexsim-agent --server http://localhost:8888 --id my-jumpbox --interval 10
```

---

## Detection Planes

| Plane | Cortex Engine | Status |
|-------|---------------|--------|
| **CDR** | Cortex Cloud / Prisma Cloud Compute | 5 scenarios + IaC module (EKS) |
| **EDR** | Cortex XDR Agent | 5 scenarios + IaC module (diverse Linux targets) |
| **NDR** | Network Security / Firewall Analytics | 5 scenarios + IaC module + EAL simulator |
| **ITDR** | Cortex ITDR | IaC module (AD lab w/ seeded roastable accounts) |
| **CSPM** | Cortex Cloud Posture Management | IaC module (intentional misconfigs) |
| **ASM** | Cortex Attack Surface Management | IaC module (multi-service exposed host) |
| **TIM** | Cortex Threat Intel Management | IaC module (TAXII + fake C2) |
| **Cloud App** | Cortex Cloud App Security | Planned |
| **Analytics** | XSIAM Correlation Engine | 3 multi-plane stitching scenarios |
| **AI_ACCESS** | Cortex AI Access Security | 5 scenarios — outbound to OpenAI / Gemini / Anthropic with planted DLP markers |
| **AIRS** | Cortex AI Runtime Security | 5 scenarios driven by `cortex-prompt-attacker` against `cortex-vulnerable-llm` (OWASP LLM01–LLM10) |
| **BROWSER** | Prisma Browser | 5 scenarios (draft) — Playwright-driven, awaits Phase 6 `cortex-browser-attacker` |
| **KOI** | Agentic endpoint / supply-chain | 5 scenarios (draft) — MCP / skills / extensions / PyPI, awaits Phase 5 artifact pack |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  SimCore (FastAPI, port 8888)                                    │
│  ┌──────────┐ ┌─────────────┐ ┌────────────┐ ┌──────────────┐ │
│  │ Scenario │ │ Orchestrator│ │ Tool       │ │ EAL Simulator│ │
│  │ Loader   │ │ (pull/push) │ │ Instantiator│ │ /api/eal/*   │ │
│  └──────────┘ └─────────────┘ └────────────┘ └──────────────┘ │
│       ↓             ↓                ↓               ↓           │
│  scenarios/    Agent Task        sources/       plugin registry  │
│   (YAML)        Queue           (submodules)    + 6 built-ins    │
└──────────────────────────────────────────────────────────────────┘
         ↑ HTTP poll              ↑ native CLI          ↑ HTTP API
┌────────────────┐         ┌──────────────────────┐ ┌─────────────┐
│ cortexsim-agent│         │ signalbench / ackbarx│ │ React UI    │
│ (pull model)   │         │ mocktaxii / xdrtop   │ │ /api/eal/UI │
└────────────────┘         └──────────────────────┘ └─────────────┘
```

**Three execution surfaces:**

- **Pull (agent)** — agent polls SimCore, receives task, executes with
  identity harness, streams output back.
- **Push (bundle)** — SimCore generates a self-contained bash bundle or
  K8s YAML; DC downloads and executes offline.
- **EAL simulator (/api/eal/*)** — declarative network-traffic
  campaigns; plugin-based; supports C2 beaconing, DNS tunnelling, bulk
  exfil, Stratum cryptojacking, SMB sweep, AIRS probe attacks.

**Identity harness** — every TTP step runs via a service account
(`www-data`, `postgres`, `node`, `nobody`, etc.) to create realistic
process causality chains in XSIAM.

---

## EAL Traffic Simulator

A plugin-based subsystem under `core/eal_simulator/` that emits
controlled network telemetry to validate Palo Alto Networks NGFW
**Enhanced Application Logs** and Cortex XDR / XSIAM NDR analytics.

Built-in plugins:

| Plugin | Purpose | EAL targets |
|--------|---------|------------|
| `c2_http_beacon` | Periodic HTTP/S beacon | Unusual UA, periodic beaconing, DGA URI |
| `dns_tunnel_exfil` | DNS-tunneling exfiltration | DNS tunnelling, anomalous volume, high-entropy labels |
| `bulk_https_exfil` | Large outbound transfer | Anomalous data transfer size |
| `stratum_tcp_connect` | Cryptojacking JSON-RPC | Cryptojacking App-ID |
| `smb_rpc_sweep` | Lateral SMB / RPC sweep | Host sweeping, anomalous SMB / RPC |
| `airs_prompt_attack` | AIRS validation runner | AIRS prompt-injection / tool-abuse / RAG / DoS |

```bash
# Inspect available plugins
python -m scripts.eal_simulator.cli list-plugins | jq .

# Run a campaign
python -m scripts.eal_simulator.cli run path/to/campaign.yml --live
```

Full design: [`docs/eal-simulator/architecture.md`](./docs/eal-simulator/architecture.md).

---

## AIRS Validation Stack (Phase 2 + 3)

For AI Runtime Security POVs the repo ships a self-contained
canary + attacker pair so the customer's AIRS layer can be validated
without a real LLM, real keys, or any external dependency.

```
┌──────────────────────┐  HTTP  ┌──────────────────────┐
│ cortex-prompt-       │ ─────> │ cortex-vulnerable-   │
│ attacker (Phase 3)   │        │ llm (Phase 2)        │
│ probes/mutators/     │        │ Flask + canary       │
│ scorers              │ <───── │ OWASP LLM01..LLM10   │
└──────────────────────┘ JSONL  └──────────────────────┘
        │                              ↑
        │                              │
        └─────► airs_prompt_attack ────┘
                EAL plugin (forwards Attempts → ECS audit pipeline)
```

**Canary**: deterministic regex-driven Flask app with one blueprint per
OWASP LLM Top 10 (v2025/2.0) class. **No real LLM calls. No keys. Ever.**

**Attacker**: Probe → Mutator → Target → Scorer pipeline. Probes are
**promptmap-compatible YAML** (no GPL code is imported — schema mirrored
only). Mutator chain is PyRIT-shape (composable, stateless). JSONL
output mirrors NVIDIA garak's `Attempt` field naming.

```bash
# Stand the canary up locally
cortex-vulnerable-llm serve --port 8089 --vuln all

# Run the LLM01 probe pack against it
cortex-prompt-attacker run \
    --probes scenarios/airs/probes/llm01/ \
    --target-url http://127.0.0.1:8089/owasp/llm01/chat \
    --scorers system_prompt_leak,secret_leak \
    --out /tmp/airs-001.jsonl
```

See [`sources/cortex-vulnerable-llm/README.md`](./sources/cortex-vulnerable-llm/README.md)
and [`sources/cortex-prompt-attacker/README.md`](./sources/cortex-prompt-attacker/README.md).
Design grounded in
[`docs/eal-simulator/research-dvllm-prompt-attacker.md`](./docs/eal-simulator/research-dvllm-prompt-attacker.md).

---

## Repository Layout

```
cortex-pov-engine/
├── install.sh              ← jumpbox bootstrap (one-liner)
├── docker-compose.yml      ← SimCore container
├── .gitmodules             ← 10 tool submodules
├── core/                   ← SimCore FastAPI app (Python 3.11)
│   ├── api/                  ← REST routers (scenarios, runs, eal, infra, ...)
│   └── eal_simulator/        ← EAL traffic simulator + plugins
├── agent/                  ← Go pull-model beacon agent
├── ui/                     ← React 18 + Vite frontend
├── scenarios/              ← YAML scenario library (UC/TC tagged)
│   ├── cdr/   edr/   ndr/   itdr/   multi_plane/
│   ├── ai_access/   airs/   browser/   koi/
│   └── airs/probes/          ← cortex-prompt-attacker probe pack
├── sources/                ← submodules + in-tree tools
│   ├── cortex-vulnerable-llm/    (in-tree, Phase 2)
│   ├── cortex-prompt-attacker/   (in-tree, Phase 3)
│   ├── signalbench/  mocktaxii/  ackbarx/  xdrtop/  ...
├── infra/                  ← IaC modules (Terraform; AWS/GCP/Azure)
├── deploy/                 ← Helm charts (eal-simulator)
├── scripts/                ← operator CLIs (eal_simulator, etc.)
├── tests/                  ← pytest suite (CortexSim core)
└── docs/
    └── eal-simulator/      ← architecture + runbook + research briefs
```

---

## Roadmap

| Phase | Component | Status |
|-------|-----------|--------|
| 1 | Schema + 20 declarative scenarios across `AI_ACCESS / AIRS / BROWSER / KOI` | ✅ shipped |
| 2 | `sources/cortex-vulnerable-llm/` — Flask canary, OWASP LLM01–10 | ✅ shipped |
| 3 | `sources/cortex-prompt-attacker/` + `airs_prompt_attack` EAL plugin | ✅ shipped |
| 4 | `llm_provider_egress` EAL plugin (replaces curl in AI_ACCESS scenarios) | pending |
| 5 | `sources/cortex-malicious-agentic-pack/` + `agentic_egress` plugin | pending |
| 6 | `sources/cortex-browser-attacker/` (Playwright + JSONL audit) | pending |

---

## Test

```bash
# Core CortexSim suite
pytest tests/ --ignore=tests/installer

# Per-package suites (in-tree tools)
pytest sources/cortex-vulnerable-llm/tests/
pytest sources/cortex-prompt-attacker/tests/
```

---

## No Cortex API Connection Required

SimCore is fully standalone. It generates signals *into* the customer's
Cortex environment via agent-based execution and EAL traffic generation;
it does not read alerts *out of* Cortex. The DC validates detections
manually in the XSIAM console (or via the customer's own analytics
pipeline).

---

*CortexSim | Owner: Henry Reed, DC2 GTM NAM Cortex*

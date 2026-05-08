# CortexSim — Detection Simulation Engine

Enterprise-grade, Cortex-branded detection simulation platform for Palo
Alto Networks Domain Consultants. Validates BIOC, Analytics, IOC, and
cross-plane stitching across XSIAM / XDR / AIRS / Browser / KOI surfaces
with controlled, high-fidelity signal generation.

> This wiki is auto-generated from `docs/wiki/` in the
> [`hankthebldr/cortex-pov-engine`](https://github.com/hankthebldr/cortex-pov-engine)
> repo on every merge to `main`. Do not edit pages directly in the
> GitHub wiki — they will be overwritten.

## Quick links

- **[[Architecture]]** — three-tier design, plugin model, identity harness
- **[[Detection Planes]]** — what's covered, what's pending
- **[[EAL Simulator]]** — plugin catalog + campaign model
- **[[AIRS Validation]]** — vulnerable-LLM canary + prompt-attacker pipeline
- **[[KOI Validation]]** — agentic supply-chain artifact pack + agentic_egress
- **[[Tools Catalog]]** — what every in-tree tool does + how to invoke
- **[[Roadmap]]** — phase-by-phase shipped vs. pending
- **[[POV Runbook]]** — DC playbook for a customer engagement
- **[[Plugin Development]]** — adding a new EAL plugin
- **[[Scenario Authoring]]** — writing a new scenario YAML
- **[[Contributing]]** — how to land changes

## Repo layout

```
core/                  ← SimCore FastAPI app
  api/                   REST routers
  eal_simulator/         EAL traffic simulator + 7 plugins
  engine/                scenario loader, orchestrator, push generator
agent/                 ← Go pull-model beacon
ui/                    ← React 18 + Vite frontend
scenarios/             ← YAML scenario library, per plane
sources/               ← submodules + in-tree tools
infra/                 ← Terraform IaC modules (AWS)
deploy/helm/           ← Helm chart for the EAL simulator
docs/                  ← architecture, runbooks, research briefs, wiki
tests/                 ← pytest suite
```

## Status snapshot — Phase 5 (latest shipped)

| Plane | Status |
|---|---|
| CDR | 5 scenarios + IaC |
| EDR | 5 scenarios + IaC |
| NDR | 5 scenarios + IaC + EAL simulator |
| ITDR | IaC module (no scenarios yet) |
| CSPM / ASM / TIM | IaC modules |
| Cloud App | planned |
| Analytics | 3 multi-plane stitching scenarios |
| **AI_ACCESS** | 5 active scenarios via `llm_provider_egress` plugin |
| **AIRS** | 5 active scenarios via `cortex-prompt-attacker` + `airs_prompt_attack` plugin |
| **BROWSER** | 5 draft scenarios — Phase 6 |
| **KOI** | 5 active scenarios via `cortex-malicious-agentic-pack` + `agentic_egress` plugin |

7 EAL plugins shipped (`c2_http_beacon`, `dns_tunnel_exfil`,
`bulk_https_exfil`, `stratum_tcp_connect`, `smb_rpc_sweep`,
`airs_prompt_attack`, `llm_provider_egress`, `agentic_egress`).

352-test pytest surface across `tests/`,
`sources/cortex-vulnerable-llm/tests/`,
`sources/cortex-prompt-attacker/tests/`.

See [[Roadmap]] for what's next.

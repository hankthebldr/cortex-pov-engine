# CortexSim — Detection Simulation Engine

Enterprise detection simulation engine for Palo Alto Networks Cortex Domain
Consultants. It generates controlled, high-fidelity signal into a customer's
Cortex environment (XSIAM / XDR / Cortex Cloud) to validate detection logic
across the full `detection_type` vocabulary — **`BIOC · XQL · Analytics ·
Correlation · IOC · ABIOC`** — plus the XDM modeling-rule normalization
substrate and cross-plane stitching.

> Not a red-team C2 — a **detection quality-assurance engine**. Real binaries,
> real process causality, real telemetry.

> **This wiki is generated.** Narrative pages come from `docs/wiki/` in the
> [source repo](https://github.com/hankthebldr/cortex-pov-engine); the whole
> scenario catalog is rebuilt from the live corpus by `scripts/gen_wiki.py` on
> every merge to `main`. Direct edits in the wiki UI are overwritten.

## Read this before quoting any number

| Term | Meaning | Count |
|---|---|---|
| **Authored** | Exists and loads clean under strict validation | 177 scenarios · 22 assertions |
| **Executed** | Has run end-to-end through a beacon or push bundle | partial |
| **Tenant-verified** | Has run against a **live Cortex tenant**, alert read back | **0** |

***tenant-verified is 0.*** Every green in the test suite and the console comes
from an injected transport. **Authored is not proven.** The console's
*Readiness* surface states this verbatim and renders the connector ladder as
four never-collapsed rungs: **AUTHORED · CONFIGURED · REACHABLE · VERIFIED**.

## Current state — counted, not estimated

Measured **2026-08-30** by running the real scenario loader and the real EAL
plugin registry over the tree. Where any prose disagrees, re-run the count and
the count wins.

| Surface | Count |
|---|---|
| Loadable scenarios | **177** across **16 planes** |
| Steps · step-detections | **667 · 1116** |
| TTP detection cards | **175** |
| Assertion artifacts (POS/PLT/AUT) | **22** (15 · 4 · 3) |
| EAL simulator plugins | **21** |
| Tool-adapter packs | **91** (8 shelf-backed · 48 exemption-declared) |
| AWS IaC modules | **11** |
| HTTP routes at boot | **133** |
| MITRE ATT&CK tactics | **14** |

```bash
make validate && make check-refs && make coverage-strict
```

## The catalog

- **[[Scenario Index]]** — all 177 scenarios, every plane, one table
- **[[ATT-CK-Coverage]]** — tactic → technique → scenario matrix
- **[[Detection Planes]]** — how planes work, and the per-plane pages

## Narrative pages

- **[[Architecture]]** — three-tier design, execution modes, identity harness
- **[[EAL Simulator]]** — the 21-plugin catalog + campaign model
- **[[AIRS Validation]]** — vulnerable-LLM canary + prompt-attacker pipeline
- **[[KOI Validation]]** — agentic supply-chain artifact pack + `agentic_egress`
- **[[Tools Catalog]]** — what every in-tree tool does + how to invoke it
- **[[Detection Coverage Lab]]** — coverage analysis workflow
- **[[POV Runbook]]** — DC playbook for a customer engagement
- **[[Scenario Authoring]]** — writing a new scenario YAML
- **[[Plugin Development]]** — adding a new EAL plugin
- **[[Contributing]]** — how to land changes
- **[[Roadmap]]** — shipped vs pending

## Repo layout

```
core/                  ← SimCore FastAPI app — 133 routes
  api/                   REST routers
  engine/                scenario_loader · orchestrator · push_generator
                         uctc_registry · verifier · assertions · payload_shelf
  connectors/            optional read-back measurement loop
  integrations/xsiam/    ~116 read-only operation packs + Tier-2 XQL
  eal_simulator/         EAL traffic simulator + 21 plugins
  planes/                declarative PlaneDescriptor registry (16 planes)
agent/                 ← Go pull-model beacon (5-target build matrix)
ui/                    ← React 18 + Vite console
scenarios/             ← 177 scenario YAMLs, per plane
assertions/            ← 22 POS/PLT/AUT artifacts
detection_scanner/ttps ← 175 TTP detection cards
tools/packs/           ← 91 tool-adapter packs
payloads/              ← digest-pinned payload shelf
infra/modules/aws/     ← 11 Terraform modules
docs/reference/        ← counted ground truth — the authority
```

## Distribution

CortexSim is **build-from-source**. There is no tagged release and no published
container image — `.github/workflows/release.yml` implements that pipeline and
fires on a `v*.*.*` tag, but no tag has been cut. See [[Contributing]] for
build instructions, or the
[landing page](https://hankthebldr.github.io/cortex-pov-engine/).

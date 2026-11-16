# CortexSim — Detection Simulation Engine

Enterprise detection simulation engine for Palo Alto Networks Cortex Domain
Consultants. It generates controlled, high-fidelity signal into a customer's
Cortex environment (XSIAM / XDR / Cortex Cloud) to validate detection logic
across the full `detection_type` vocabulary — **`BIOC · XQL · Analytics ·
Correlation · IOC · ABIOC · Prevention`** — plus the XDM modeling-rule normalization
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
| **Authored** | Exists and loads clean under strict validation | 187 scenarios · 14 assertions |
| **Executed** | Has run end-to-end through a beacon or push bundle | partial |
| **Tenant-verified** | Has run against a **live Cortex tenant**, alert read back | **0** |

***tenant-verified is 0.*** Every green in the test suite and the console comes
from an injected transport. **Authored is not proven.** The console's
*Launch gate* states this verbatim and renders the connector ladder as
four never-collapsed rungs: **AUTHORED · CONFIGURED · REACHABLE · VERIFIED**.

## Current state — counted, not estimated

Copied **2026-11-15** (the 1.2.5 release) from [`docs/reference/ground-truth.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/reference/ground-truth.md),
which `make ground-truth` regenerates from the real loader and registries, and
which CI diffs on every push. This page is **not** gated; where it disagrees
with `ground-truth.md`, that file wins.

| Surface | Count |
|---|---|
| Loadable scenarios | **187** across **13 planes** |
| Steps · step-detections | **779 · 1328** |
| TTP detection cards | **185** |
| Assertion artifacts (POS/PLT/AUT) | **14** (5 · 6 · 3) |
| EAL simulator plugins | **27** |
| Tool-adapter packs | **91** (8 shelf-backed · 48 exemption-declared) |
| HTTP routes | **162** decorators · **172** served at boot |
| MITRE ATT&CK tactics | **14** |

The CSPM, ASM and AI_SPM planes and the AWS IaC generator moved to
`feature/scenarios-cloud-posture` for a 1.4.x release; they are not in 1.2.5.

```bash
make validate && make check-refs && make coverage-strict
```

## The catalog

- **[[Scenario Index]]** — every scenario, every plane, one table
- **[[ATT-CK-Coverage]]** — tactic → technique → scenario matrix
- **[[Detection Planes]]** — how planes work, and the per-plane pages

## Narrative pages

- **[[Architecture]]** — three-tier design, execution modes, identity harness
- **[[EAL Simulator]]** — the 27-plugin catalog + campaign model
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
core/                  ← SimCore FastAPI app — 172 routes served (162 decorators)
  api/                   REST routers
  engine/                scenario_loader · orchestrator · push_generator
                         uctc_registry · verifier · assertions · payload_shelf
  connectors/            optional read-back measurement loop
  integrations/xsiam/    152-op gated operation catalog (writes preview) + Tier-2 XQL
  eal_simulator/         EAL traffic simulator + 27 plugins
  planes/                declarative PlaneDescriptor registry (15 registered; dlp.py is not)
  mcp_server/            MCP server, 54 tools (writes preview; opt-in HTTP at /mcp)
agent/                 ← Go pull-model beacon (5-target build matrix)
ui/                    ← React 18 + Vite console
scenarios/             ← 187 scenario YAMLs, per plane
assertions/            ← 14 POS/PLT/AUT artifacts
detection_scanner/ttps ← 185 TTP detection cards
tools/packs/           ← 91 tool-adapter packs
payloads/              ← digest-pinned payload shelf
docs/reference/        ← counted ground truth — the authority
```

## Distribution

CortexSim is **build-from-source**. It ships as tagged
[GitHub Releases](https://github.com/hankthebldr/cortex-pov-engine/releases)
(v1.2.5 is the release this page describes; v1.2.4 is the latest tag until it
merges to `main`). `.github/workflows/release.yml` also pushes an image to
GHCR, but that image is not publicly pullable, so build it locally. See [[Contributing]] for
build instructions, or the
[landing page](https://hankthebldr.github.io/cortex-pov-engine/).

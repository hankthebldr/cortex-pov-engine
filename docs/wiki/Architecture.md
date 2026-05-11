# Architecture

## Three-tier design

```
┌──────────────────────────────────────────────────────────────────┐
│  SimCore (FastAPI, port 8888)                                    │
│  ┌──────────┐ ┌─────────────┐ ┌────────────┐ ┌──────────────┐ │
│  │ Scenario │ │ Orchestrator│ │ Tool       │ │ EAL Simulator│ │
│  │ Loader   │ │ (pull/push) │ │ Instantiator│ │ /api/eal/*   │ │
│  └──────────┘ └─────────────┘ └────────────┘ └──────────────┘ │
│       ↓             ↓                ↓               ↓           │
│  scenarios/    Agent Task        sources/       plugin registry  │
│   (YAML)        Queue           (submodules)    + 7 built-ins    │
└──────────────────────────────────────────────────────────────────┘
         ↑ HTTP poll              ↑ native CLI          ↑ HTTP API
┌────────────────┐         ┌──────────────────────┐ ┌─────────────┐
│ cortexsim-agent│         │ signalbench / ackbarx│ │ React UI    │
│ (pull model)   │         │ mocktaxii / xdrtop   │ │ /api/eal/UI │
└────────────────┘         └──────────────────────┘ └─────────────┘
```

## Three execution surfaces

- **Pull (agent)** — `cortexsim-agent` polls SimCore, receives a task,
  executes it via the identity harness, streams output back.
- **Push (bundle)** — SimCore generates a self-contained bash bundle or
  K8s YAML; the DC downloads and executes offline.
- **EAL simulator (`/api/eal/*`)** — declarative network-traffic
  campaigns; plugin-based; supports C2 beaconing, DNS tunnelling, bulk
  exfil, Stratum cryptojacking, SMB sweep, AIRS probe attacks,
  LLM-provider egress, agentic supply-chain fetches.

## Identity harness

Every TTP step runs via a service account (`www-data`, `postgres`,
`node`, `nobody`, etc.) to create realistic process causality chains in
XSIAM. The harness wraps commands with `runuser -l`, `sudo -u`, or
`su -s /bin/bash`.

This is what differentiates CortexSim from Atomic Red Team — the
parent-process tree the customer sees is a *realistic* one, not a bash
shell on the jumpbox.

## Plugin model

The EAL simulator plugin registry auto-discovers everything under
`core/eal_simulator/plugins/`. Adding a plugin = drop a `.py` file. See
[[Plugin Development]] for the full contract.

Every plugin:

1. Inherits `BaseSimulation` (`core/eal_simulator/base.py`)
2. Declares a Pydantic params model
3. Implements `async def run(self, ctx: SimulationContext) -> SimulationResult`
4. Authorises every target host before emitting traffic via
   `ctx.authorise(host)`
5. Emits ECS-shaped audit events via `await ctx.emit_event(...)`

## Safety model

Every campaign declares:

```yaml
authorized_by: <operator email>
simulation_authorized: true
target_allowlist:
  - api.openai.com
  - 10.0.0.0/24
dry_run: false
```

The `SafetyPolicy` in `core/eal_simulator/safety.py` enforces:

- Live execution requires `simulation_authorized=true`,
  named `authorized_by`, non-empty `target_allowlist`
- Every target host must be in the allowlist (hostname suffix match
  for FQDNs, CIDR membership for IPs, `/128` for IPv6 literals)
- Plugins call `ctx.authorise(target)` before each network call

Dry-run skips the network checks but still requires authorisation
context.

## Data flow

- **Scenario load** — YAML files under `scenarios/{plane}/` are parsed
  via Pydantic on startup; invalid scenarios are rejected with a
  descriptive error.
- **Run launch** — `Orchestrator` creates a `Run` row, auto-seeds
  `Result` rows from `expected_detections` (one per detection per
  step) with `executed_at` timestamp.
- **Detection validation** — DC marks results as observed via
  `PUT /api/results/{id}/validate` → sets `observed_at` →
  `mttd_seconds` computed as `observed_at - executed_at`.
- **Report export** — `GET /api/runs/{id}/report?format=markdown`
  generates a Cortex-branded POV report with coverage stats and MTTD
  metrics.
- **MITRE coverage** — `GET /api/mitre/coverage` aggregates technique
  coverage across all scenarios/runs for the heatmap UI.

## Persistence

Async SQLAlchemy with SQLite by default at
`{CORTEXSIM_BASE_DIR}/data/cortexsim.db`. Tables: `Scenario`, `Run`,
`Result`, `ToolInstance`, `Agent`, `EalCampaign`, `EalCampaignRun`.

DB schema is created at startup (`init_db`); no migrations system —
SimCore is a single-binary POV tool, not a long-running multi-version
service.

## Deeper reading

- [`docs/eal-simulator/architecture.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/architecture.md)
- [`docs/eal-simulator/runbook.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/runbook.md)
- [`docs/eal-simulator/plugin-development.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/plugin-development.md)
- [`docs/eal-simulator/research-dvllm-prompt-attacker.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/research-dvllm-prompt-attacker.md)

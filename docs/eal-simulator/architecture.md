# EAL Traffic Simulator — Architecture

## Purpose

The EAL Traffic Simulator generates the high-fidelity network telemetry
required to trigger Palo Alto Networks **Enhanced Application Logs (EALs)**
and validate Cortex XDR / XSIAM Network Detection and Response (NDR)
analytics. It is a *detection quality assurance* engine — emit signal,
confirm Cortex sees it, walk the customer through the result.

It ships as a subsystem of CortexSim (`core/eal_simulator/`) but is fully
self-contained: plugins, executor, audit log, and API surface have no
dependency on the main CortexSim agent path.

## Components

```
                      ┌─────────────────────────┐
                      │  React UI / CortexSim   │
                      │  /api/eal/* endpoints   │
                      └──────────┬──────────────┘
                                 │
                                 ▼
┌──────────────────┐     ┌────────────────┐     ┌────────────────────┐
│ FastAPI router   │ ──► │ CampaignExecutor│ ──► │ Plugin (BaseSim.) │
│  api/eal.py      │     │  executor.py   │     │  e.g. c2_http_…   │
└──────────────────┘     └───────┬────────┘     └──────────┬─────────┘
                                 │                         │
                                 ▼                         ▼
                       ┌─────────────────┐        ┌──────────────────┐
                       │ AuditLogger     │        │ httpx / sockets  │
                       │ ECS-JSON file   │        │ → customer NGFW  │
                       └─────────────────┘        └──────────────────┘
```

| Component | File | Responsibility |
|-----------|------|----------------|
| `BaseSimulation` | `core/eal_simulator/base.py` | Abstract contract every plugin implements |
| `PluginRegistry` | `core/eal_simulator/registry.py` | Dynamic loader; discovers plugins under `core/eal_simulator/plugins/` |
| `Campaign` | `core/eal_simulator/campaign.py` | Pydantic schema for declarative campaigns |
| `CampaignExecutor` | `core/eal_simulator/executor.py` | Async runner; drives steps, enforces safety, emits audit events |
| `SafetyPolicy` | `core/eal_simulator/safety.py` | Per-target allowlist gate; blocks live execution without authorisation |
| `AuditLogger` | `core/eal_simulator/audit.py` | ECS-JSON event writer (file + Python logging) |
| API | `core/api/eal.py` | `/api/eal/*` REST endpoints (plugins, campaigns, runs) |
| ORM | `core/models.py` (`EalCampaign`, `EalCampaignRun`) | History persistence in shared SQLite DB |
| CLI | `scripts/eal_simulator/cli.py` | Operator entrypoint for offline / pod-internal use |

## Plugin contract

```python
class MyPlugin(BaseSimulation):
    class Meta:
        name = "my_plugin"           # unique registry key
        version = "1.0.0"
        description = "..."
        mitre_techniques = ["TXXXX"]
        eal_targets = ["EAL signal name"]
        params_model = MyPydanticModel

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        ...
```

Plugins **must**:

* Validate every target against `ctx.authorise(target)` before sending a
  packet. The executor injects this hook from the campaign's `SafetyPolicy`.
* Emit at least one ECS event via `await ctx.emit_event(ecs_event(...))`.
* Branch on `ctx.dry_run` and return without emitting traffic when true.
* Add `**ctx.telemetry_headers` to every HTTP request.

## Campaign lifecycle

1. **Author** the YAML/JSON (see `docs/eal-simulator/runbook.md`) and POST
   to `/api/eal/campaigns`. Step params validate against the plugin's
   Pydantic schema at this point — invalid campaigns never persist.
2. **Launch** via `POST /api/eal/campaigns/{id}/launch`. The API pre-flights
   the safety policy synchronously, creates an `EalCampaignRun` row, and
   schedules the executor as a `BackgroundTask`.
3. **Executor** drives steps sequentially. Each step emits `step_started` /
   `step_finished` ECS events plus per-action events from inside the plugin.
4. **Persistence**: the background task writes the final `ExecutorState`
   back to `EalCampaignRun.step_results` so the UI can render history.

## Safety

The simulator emits real network traffic that can be indistinguishable from
malicious activity at the wire. The single chokepoint is `SafetyPolicy`
(`core/eal_simulator/safety.py`):

| Rule | Enforced where |
|------|----------------|
| `dry_run=True` skips all network checks | `SafetyPolicy.authorise` |
| Live execution requires `simulation_authorized=true` | Pydantic + executor |
| Live execution requires non-empty `authorized_by` | Pydantic + executor |
| Live execution requires non-empty `target_allowlist` | Pydantic + executor |
| Every emitted target must match the allowlist (host suffix or CIDR) | `SafetyPolicy.authorise` invoked by every plugin |

Every emitted HTTP request additionally carries
`X-Simulation-Run-ID: cortexsim-<uuid>` so SOC analysts can filter
simulator traffic out of post-incident review.

## Task queue

The default executor uses an in-process `InMemoryTaskQueue`
(`asyncio.create_task`). For multi-pod K3s deployments the architecture
supports swapping in a Celery/Redis queue (`deploy/helm/eal-simulator/`
ships a Redis dependency disabled by default). The queue interface is a
single `submit(coro_factory)` method, so any worker substrate can plug in.

## Deployment

The Helm chart at `deploy/helm/eal-simulator/` deploys:

* `<release>-api` — FastAPI gateway pods (replicaCount 2)
* `<release>-worker` — worker pods scheduled onto nodes routed through the
  customer NGFW (`nodeSelector: cortexsim.paloaltonetworks.com/role=simulator`)
* `<release>-redis` — optional broker
* Tailscale sidecar — recommended; never expose the API to the public internet

See `docs/eal-simulator/runbook.md` for a complete install + first-campaign
walkthrough.

# EAL Traffic Simulator — Plugin Development

## TL;DR

Drop a Python file in `core/eal_simulator/plugins/` that defines a class
inheriting from `BaseSimulation`. The default plugin registry imports every
file in that directory at startup and registers any subclasses it finds —
no further wiring is required.

## Skeleton

```python
# core/eal_simulator/plugins/my_thing.py
from __future__ import annotations

from pydantic import BaseModel, Field
from ..audit import ecs_event
from ..base import BaseSimulation, SimulationContext, SimulationResult


class MyThingParams(BaseModel):
    target: str
    iterations: int = Field(default=5, ge=1, le=1000)


class MyThing(BaseSimulation):
    class Meta:
        name = "my_thing"
        version = "1.0.0"
        description = "Two-line summary of what this plugin emits and why."
        mitre_techniques = ["T1234"]
        eal_targets = ["EAL signal name 1", "EAL signal name 2"]
        params_model = MyThingParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: MyThingParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        # 1. Authorise the target — raises SafetyError if not in allowlist
        ctx.authorise(params.target)

        # 2. Honour dry-run
        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="my_thing_dry_run",
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.target,
                message="DRY-RUN — no traffic emitted",
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                detail={"dry_run": True},
            )

        # 3. Real work — always include ctx.telemetry_headers in HTTP calls
        ...

        return SimulationResult(...)
```

## Conventions

* **Pydantic params model** keeps the API self-documenting. It is published
  via `GET /api/eal/plugins/{name}` so the React UI can render a form.
* **ECS event names** use the form `<plugin>_<action>` (e.g.
  `c2_beacon_request`, `dns_tunnel_query`). Outcome is `success` / `failure`.
* **Bytes accounting**: track `bytes_sent` cumulatively and put it in
  `SimulationResult.bytes_sent`. The audit logger uses this to compute
  per-campaign totals.
* **Long sleeps**: wrap them in `await asyncio.sleep(...)` not
  `time.sleep(...)`, so `asyncio.CancelledError` propagates cleanly.
* **Blocking syscalls** (raw sockets, OS DNS): wrap in `asyncio.to_thread`.

## Testing

Each plugin gets a parametrised dry-run test in
`tests/eal_simulator/test_plugins.py`. Add a row with the plugin name and a
minimal valid params dict — the test verifies the dry-run path returns
`status=success`.

For richer behaviour, add a dedicated test module under
`tests/eal_simulator/`. Use the `make_executor` and `isolated_registry`
fixtures from `conftest.py` to register the plugin under test.

## Out-of-tree plugins

Operators with sensitive techniques can drop plugin files outside the
package and load them via:

```python
from eal_simulator.registry import PluginRegistry

reg = PluginRegistry()
reg.load_directory("/etc/cortexsim/plugins")
```

The `CampaignExecutor` accepts any registry, so swap it in:

```python
executor = CampaignExecutor(registry=reg)
```

## Submitting a plugin upstream

1. Add the plugin file under `core/eal_simulator/plugins/`.
2. Add a parametrised entry in `tests/eal_simulator/test_plugins.py`.
3. Add a row to `docs/eal-simulator/plugin-catalog.md` (if it exists) or a
   bullet to the architecture doc's plugin table.
4. If the plugin emits a new EAL signal, add an NDR scenario YAML under
   `scenarios/ndr/` that exercises it end-to-end.

# EAL Traffic Simulator — Plugin Development

> **Canonical plugin catalog:** [`docs/reference/eal-plugin-catalog.md`](../reference/eal-plugin-catalog.md)
> is the single source of truth for the shipped plugin set (params, MITRE
> mapping, EAL targets, safety model, test coverage, and known gaps). This
> development guide covers *authoring* a plugin; the catalog covers *what
> already exists*. (An earlier draft referenced a `docs/eal-simulator/plugin-catalog.md`
> file that was never created — use the reference doc instead.)
>
> **13 plugins ship today.** The 5 *original* NDR plugins
> (`c2_http_beacon`, `dns_tunnel_exfil`, `stratum_tcp_connect`,
> `smb_rpc_sweep`, `bulk_https_exfil`) are in the shared dry-run param matrix in
> `tests/eal_simulator/test_plugins.py`. The **8 newer plugins**
> (`ftp_egress`, `ssh_egress`, `idp_signin_emulator`, `oauth_grant_emulator`,
> `llm_provider_egress`, `airs_prompt_attack`, `browser_attack_runner`,
> `agentic_egress`) each have a *dedicated* test module but are **not yet in the
> shared matrix** (gap EAL-G04) — see the catalog's test-coverage map. When you
> add a plugin, add it to **both** (a dedicated module *and* a row in the shared
> matrix) so the dry-run contract regression-guard covers it.

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

Every plugin should get a parametrised dry-run test in the **shared** matrix in
`tests/eal_simulator/test_plugins.py`. Add a row with the plugin name and a
minimal valid params dict — the test verifies the dry-run path returns
`status=success`. This shared matrix is the regression-guard for the dry-run
contract; the 8 newer plugins skipped it (gap EAL-G04), so the contract is only
guarded for the 5 original NDR plugins until they are backfilled — do not repeat
that omission.

For richer behaviour, add a dedicated `test_plugin_<name>.py` module under
`tests/eal_simulator/`. Use the `make_executor` and `isolated_registry`
fixtures from `conftest.py` to register the plugin under test. (All 8 newer
plugins have such a module; see the catalog's test-coverage map.)

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
2. Add a parametrised entry in the shared dry-run matrix in
   `tests/eal_simulator/test_plugins.py` (and, for richer behaviour, a dedicated
   `test_plugin_<name>.py` module).
3. Add a row to the canonical catalog
   [`docs/reference/eal-plugin-catalog.md`](../reference/eal-plugin-catalog.md)
   (and, if relevant, the architecture doc's component table).
4. If the plugin emits a new EAL signal, add a scenario YAML under the relevant
   plane (e.g. `scenarios/ndr/`) that exercises it end-to-end.

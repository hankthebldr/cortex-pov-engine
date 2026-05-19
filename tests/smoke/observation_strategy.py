"""Detection observation strategy — the bridge between SimCore-generated
signal and "did Cortex actually detect it?".

CortexSim is, by design, a signal generator — it does NOT read alerts back
out of XSIAM/XDR (see CLAUDE.md "No Cortex API connection").  That leaves
a hole in the smoke test: when a scenario fires, how do we decide whether
the lab is healthy from a *detection-quality* perspective?

Three viable strategies (mutually exclusive — pick one per environment):

  STRUCTURAL   — the lab is "good" if Result rows are seeded from the
                 scenario's expected_detections.  Validates only that
                 SimCore wired the run correctly; says nothing about
                 Cortex.  Fastest and zero external dependencies.

  SYNTHETIC    — auto-mark every seeded Result as observed=true via the
                 validate endpoint.  Exercises the full MTTD pipeline +
                 report rendering, so we know the *reporting* path is
                 healthy even though detection truth is fabricated.  This
                 is what most CI runs should do.

  CORTEX_XQL   — actually query Cortex via XQL for the expected detections
                 within a time window after the run and only mark observed
                 the ones that show up.  Requires CORTEX_TENANT_URL +
                 CORTEX_API_KEY env vars and a network path to the tenant.
                 Use this when running a real lab POV dry-run.

The smoke suite reads ``CORTEXSIM_OBSERVATION_STRATEGY`` from env
(default: "synthetic") and routes through the function below.
"""

from __future__ import annotations

import os
from typing import Iterable

import httpx


SYNTHETIC = "synthetic"
STRUCTURAL = "structural"
CORTEX_XQL = "cortex_xql"


def chosen_strategy() -> str:
    return os.environ.get("CORTEXSIM_OBSERVATION_STRATEGY", SYNTHETIC).lower()


def observe(client: httpx.Client, run_id: str, results: Iterable[dict]) -> None:
    """Apply the configured observation strategy to a freshly-launched run.

    Parameters
    ----------
    client : httpx.Client
        Active SimCore client.
    run_id : str
        Run identifier returned from POST /api/run.
    results : iterable of dict
        Result rows from GET /api/results/{run_id} — already loaded by the
        caller so we don't double-fetch.
    """
    strategy = chosen_strategy()

    if strategy == STRUCTURAL:
        # No-op: caller only inspects row shape.  Nothing to mark.
        return

    if strategy == SYNTHETIC:
        for r in results:
            client.put(
                f"/api/results/{r['id']}/validate",
                json={"observed": True, "notes": "smoke-synthetic"},
            ).raise_for_status()
        return

    if strategy == CORTEX_XQL:
        _observe_via_cortex(client, run_id, results)
        return

    raise ValueError(
        f"Unknown CORTEXSIM_OBSERVATION_STRATEGY={strategy!r}. "
        f"Expected one of: {STRUCTURAL}, {SYNTHETIC}, {CORTEX_XQL}"
    )


def _observe_via_cortex(client: httpx.Client, run_id: str, results: Iterable[dict]) -> None:
    """Query the Cortex tenant via XQL and only mark detections that fire.

    NOTE: This is the design choice Henry needs to land before lab POVs run
    against a live tenant.  Wire this up in a follow-up commit once the
    XQL query shape for "detections matching scenario X in time window Y"
    is locked in with the SOC team.  The function deliberately fails loud
    so we never silently skip real-tenant validation.

    Expected env vars (read inside this function so unit tests don't need
    them):

        CORTEX_TENANT_URL     e.g. https://api-tenant.xdr.us.paloaltonetworks.com
        CORTEX_API_KEY        Advanced API key from XSIAM
        CORTEX_API_KEY_ID     Key ID (header X-XDR-AUTH-ID)
        CORTEX_XQL_TIMEOUT    seconds to wait for detections to land (default 180)
    """
    raise NotImplementedError(
        "CORTEXSIM_OBSERVATION_STRATEGY=cortex_xql is the live-tenant path and "
        "is not wired yet.  Implement _observe_via_cortex() in "
        "tests/smoke/observation_strategy.py — see docstring for env vars."
    )

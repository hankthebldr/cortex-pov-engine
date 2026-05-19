"""Shared fixtures for end-to-end smoke tests.

Smoke tests assume a *running* SimCore reachable at ``CORTEXSIM_SMOKE_URL``.
They will skip (not fail) the whole suite if the health endpoint is not
reachable, so the same files are safe to run in CI matrices where SimCore
isn't always up.
"""

from __future__ import annotations

import os
import time
from typing import Iterator

import httpx
import pytest


SMOKE_URL = os.environ.get("CORTEXSIM_SMOKE_URL", "http://localhost:8888")
SMOKE_TIMEOUT = float(os.environ.get("CORTEXSIM_SMOKE_TIMEOUT", "60"))


def _wait_for_healthy(url: str, timeout: float) -> bool:
    """Poll ``/api/health`` until 200 or timeout expires."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/api/health", timeout=2.0)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return True
        except Exception as e:  # noqa: BLE001 — surface any connection issue
            last_err = e
        time.sleep(1)
    if last_err:
        print(f"[smoke] last error waiting for {url}: {last_err}")
    return False


@pytest.fixture(scope="session")
def simcore_url() -> str:
    """SimCore base URL.  Skips the entire smoke suite if SimCore is unreachable."""
    if not _wait_for_healthy(SMOKE_URL, SMOKE_TIMEOUT):
        pytest.skip(
            f"SimCore not reachable at {SMOKE_URL} within {SMOKE_TIMEOUT}s — "
            "start it via `docker compose up -d --build` or run "
            "`scripts/smoke/lab-smoke.sh` which handles the lifecycle."
        )
    return SMOKE_URL


@pytest.fixture(scope="session")
def client(simcore_url: str) -> Iterator[httpx.Client]:
    """HTTPX client preconfigured against SimCore."""
    with httpx.Client(base_url=simcore_url, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session")
def known_scenario_id(client: httpx.Client) -> str:
    """Return a scenario_id that is guaranteed to be loaded.

    Prefers ``SIM-EDR-001`` (credential dumping — present in seed data) but
    falls back to the first scenario the API reports if the catalogue moves.
    """
    preferred = "SIM-EDR-001"
    r = client.get("/api/scenarios")
    r.raise_for_status()
    scenarios = r.json().get("scenarios", [])
    ids = {s["scenario_id"] for s in scenarios}
    if preferred in ids:
        return preferred
    if not scenarios:
        pytest.skip("SimCore has zero scenarios loaded — nothing to smoke-test")
    return scenarios[0]["scenario_id"]

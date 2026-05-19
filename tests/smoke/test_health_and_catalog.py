"""Layer 0 smoke: SimCore is alive and its content registries are populated.

Every other smoke test depends on these passing first.  If health is red or
scenarios/tools/MITRE are empty, fail loud and skip the rest.
"""

from __future__ import annotations

import httpx
import pytest


def test_health_ok(client: httpx.Client) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_openapi_reachable(client: httpx.Client) -> None:
    """OpenAPI schema is the contract — if it doesn't render, routers misloaded."""
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    # Spot-check every router we know exists is mounted
    paths = schema["paths"]
    for must_have in (
        "/api/health",
        "/api/scenarios",
        "/api/runs",
        "/api/run",
        "/api/results",
        "/api/agents",
        "/api/agents/register",
        "/api/mitre/coverage",
        "/api/infra/modules",
        "/api/infra/generate",
    ):
        assert must_have in paths, f"missing path {must_have} in OpenAPI"


def test_scenarios_loaded(client: httpx.Client) -> None:
    r = client.get("/api/scenarios")
    r.raise_for_status()
    body = r.json()
    scenarios = body.get("scenarios", [])
    assert len(scenarios) > 0, "no scenarios loaded — startup YAML loader failed"

    # Every active detection plane mentioned in CLAUDE.md should appear at
    # least once.  Keep this conservative — we only enforce the ones marked
    # "active" in the doc.
    planes_seen = {s["plane"] for s in scenarios}
    required = {"EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "AI_ACCESS", "AIRS", "BROWSER", "KOI"}
    missing = required - planes_seen
    assert not missing, f"missing scenarios for planes: {sorted(missing)}"


def test_tools_registry_populated(client: httpx.Client) -> None:
    r = client.get("/api/tools")
    r.raise_for_status()
    body = r.json()
    tools = body.get("tools", body)  # tolerate both shapes
    assert tools, "tools registry empty — instantiator init failed at startup"


def test_mitre_coverage_renders(client: httpx.Client) -> None:
    r = client.get("/api/mitre/coverage")
    assert r.status_code == 200
    body = r.json()
    assert "techniques" in body or "matrix" in body or "tactics" in body, (
        f"unexpected coverage shape: {list(body.keys())}"
    )


@pytest.mark.parametrize("provider", ["aws"])
def test_infra_modules_listed(client: httpx.Client, provider: str) -> None:
    r = client.get("/api/infra/modules", params={"provider": provider})
    assert r.status_code == 200
    body = r.json()
    modules = body.get("modules", body)
    # AWS is feature-complete with 10 modules per CLAUDE.md.  Allow drift but
    # fail if catalog is empty.
    assert len(modules) >= 4, f"only {len(modules)} {provider} modules listed"

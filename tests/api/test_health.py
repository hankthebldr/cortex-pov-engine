"""GAP-API-007 — /api/health surfaces a commit SHA + per-component health.

We test the helpers directly (importing ``main`` would configure logging and
mount static files). The full-app shape is covered by the smoke suite.
"""
from __future__ import annotations

import asyncio

import pytest


def test_commit_sha_is_nonempty_string(monkeypatch):
    import main

    # Env var takes precedence and is honoured.
    monkeypatch.setenv("CORTEXSIM_COMMIT_SHA", "deadbeefcafe")
    assert main._commit_sha() == "deadbeefcafe"


def test_commit_sha_falls_back_to_unknown(monkeypatch):
    import main

    for var in ("CORTEXSIM_COMMIT_SHA", "GIT_COMMIT", "SOURCE_COMMIT"):
        monkeypatch.delenv(var, raising=False)
    # Point BASE_DIR at a tmp dir with no COMMIT_SHA file and no git checkout.
    import config
    monkeypatch.setattr(config.settings, "CORTEXSIM_BASE_DIR", "/nonexistent-xyz")
    sha = main._commit_sha()
    assert isinstance(sha, str) and sha  # never empty
    # With no env/file/git it should be "unknown".
    assert sha == "unknown"


def test_component_health_reports_each_component():
    """Each probe runs independently and reports a status string. The DB probe
    opens a real session against the configured (dev) SQLite DB."""
    import main

    components = asyncio.run(main._component_health())
    for key in ("db", "scenario_catalog", "ttp_catalog", "adapter_catalog", "eal"):
        assert key in components, f"missing component {key}"
        assert components[key]["status"] in {"ok", "error"}
    # db probe is a trivial SELECT 1 — it should be ok against the dev DB.
    assert components["db"]["status"] == "ok"


def test_health_check_endpoint_shape():
    """The endpoint returns version + commit + components and an overall status."""
    import main

    body = asyncio.run(main.health_check())
    assert body["version"] == "1.0.0"
    assert "commit" in body and isinstance(body["commit"], str)
    assert "components" in body
    assert body["status"] in {"ok", "degraded"}

"""Contract tests for GET /api/scenarios/{id}/download — the format/target seam.

Before this change the endpoint had exactly two formats and zero platform
awareness: a Windows-only scenario returned 200 with a bash script whose steps
were PowerShell. The customer-visible failure is not an error page, it is a
green run that emits no telemetry and reads as "the stack detected nothing".

Fixtures are built locally rather than reusing tests/api/conftest.py, which is
not visible from tests/engine/.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


POSIX_STEP = {"id": "step-01", "command": "curl -s http://127.0.0.1/ -o /tmp/a.bin"}
WINDOWS_STEP = {
    "id": "step-01",
    "platforms": ["windows"],
    "command": "Invoke-AtomicTest T1003.001 -TestNumbers 1",
}
BOTH_STEP = {
    "id": "step-01",
    "command": "curl -s http://127.0.0.1/ -o /tmp/a.bin",
    "platform_variants": {"windows": 'cmd.exe /c "certutil.exe -urlcache -f http://127.0.0.1/"'},
}


@pytest.fixture
def env():
    """(client, seed) over an isolated in-memory DB with the scenarios router."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base  # noqa: PLC0415
        import models  # noqa: F401,PLC0415

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with SessionLocal() as session:
            yield session

    from api.scenarios import router  # noqa: PLC0415
    from database import get_db  # noqa: PLC0415

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    def _seed(scenario_id: str, steps: list[dict], cleanup: dict | None = None) -> None:
        from models import Scenario  # noqa: PLC0415

        async def _do():
            async with SessionLocal() as db:
                db.add(Scenario(
                    scenario_id=scenario_id, name="t", plane="EDR", version="1.0",
                    status="active", uc_ref="UCS-EDR-01", uc_name="x",
                    tc_ref="TC-EDR-01", tc_name="y",
                    mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                    mitre_technique="T1003.001", mitre_technique_name="LSASS",
                    steps=steps, external_tools=[],
                    cleanup=cleanup or {"commands": []},
                ))
                await db.commit()

        asyncio.run(_do())

    return TestClient(app), _seed


class TestAutoFormat:
    def test_auto_prefers_posix_for_a_dual_target_scenario(self, env):
        """Back-compat: the console has always downloaded .sh. A scenario that
        gains a Windows path must not silently change format under an existing
        caller."""
        client, seed = env
        seed("SIM-TEST-BOTH", [BOTH_STEP])
        r = client.get("/api/scenarios/SIM-TEST-BOTH/download")
        assert r.status_code == 200
        assert r.headers["X-CortexSim-Bundle-Target"] == "posix"
        assert r.headers["X-CortexSim-Bundle-Alternates"] == "windows"
        assert 'filename="cortexsim-SIM-TEST-BOTH.sh"' in r.headers["content-disposition"]
        assert r.text.startswith("#!")

    def test_auto_falls_through_to_powershell_for_a_windows_only_scenario(self, env):
        """The headline fix: SIM-EDR-006's shape used to return a bash script
        that dies at step-01 with `Invoke-AtomicTest: command not found`."""
        client, seed = env
        seed("SIM-TEST-WIN", [WINDOWS_STEP])
        r = client.get("/api/scenarios/SIM-TEST-WIN/download")
        assert r.status_code == 200
        assert r.headers["X-CortexSim-Bundle-Target"] == "windows"
        assert "X-CortexSim-Bundle-Alternates" not in r.headers
        assert 'filename="cortexsim-SIM-TEST-WIN.ps1"' in r.headers["content-disposition"]
        assert r.headers["content-type"].startswith("text/plain")
        assert "Invoke-CsStep" in r.text

    def test_posix_only_scenario_still_gets_bash(self, env):
        client, seed = env
        seed("SIM-TEST-POSIX", [POSIX_STEP])
        r = client.get("/api/scenarios/SIM-TEST-POSIX/download")
        assert r.status_code == 200
        assert r.headers["X-CortexSim-Bundle-Target"] == "posix"
        assert "X-CortexSim-Bundle-Alternates" not in r.headers


class TestExplicitFormat:
    def test_bash_still_works_unqualified(self, env):
        client, seed = env
        seed("SIM-TEST-POSIX", [POSIX_STEP])
        r = client.get("/api/scenarios/SIM-TEST-POSIX/download?format=bash")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/x-shellscript")

    def test_powershell_on_a_posix_only_scenario_is_409_naming_the_steps(self, env):
        """409, not 400: the REQUEST is well-formed — the scenario's CONTENT
        cannot satisfy it, and the fix is a YAML edit, not a query-string edit."""
        client, seed = env
        seed("SIM-TEST-POSIX", [POSIX_STEP, {"id": "step-02", "command": "rm -rf /tmp/x"}])
        r = client.get("/api/scenarios/SIM-TEST-POSIX/download?format=powershell")
        assert r.status_code == 409
        body = r.json()["detail"]
        assert set(body) == {"error", "code", "detail"}
        assert body["code"] == "BUNDLE_TARGET_UNSATISFIABLE"
        assert "step-01" in body["detail"] and "step-02" in body["detail"]
        assert "WINDOWS_COMMAND_UNAVAILABLE" in body["detail"]
        assert "platform_variants.windows" in body["detail"]

    def test_bash_on_a_windows_only_scenario_is_409(self, env):
        """The old behaviour was 200 + a broken script. Refusing is the fix."""
        client, seed = env
        seed("SIM-TEST-WIN", [WINDOWS_STEP])
        r = client.get("/api/scenarios/SIM-TEST-WIN/download?format=bash")
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "BUNDLE_TARGET_UNSATISFIABLE"
        assert "POSIX_COMMAND_UNAVAILABLE" in r.json()["detail"]["detail"]

    def test_k8s_is_gated_on_the_posix_resolution(self, env):
        """A K8s Job runs /bin/bash -c inside ubuntu:22.04 — it needs exactly
        the POSIX resolution `format=bash` does."""
        client, seed = env
        seed("SIM-TEST-WIN", [WINDOWS_STEP])
        assert client.get("/api/scenarios/SIM-TEST-WIN/download?format=k8s").status_code == 409

    def test_k8s_still_works_for_a_posix_scenario(self, env):
        client, seed = env
        seed("SIM-TEST-POSIX", [POSIX_STEP])
        r = client.get("/api/scenarios/SIM-TEST-POSIX/download?format=k8s")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-yaml")
        assert 'filename="cortexsim-SIM-TEST-POSIX-k8s.yaml"' in r.headers["content-disposition"]

    def test_unknown_format_is_still_400(self, env):
        """An unknown format IS a malformed request — that distinction is the
        whole reason the unsatisfiable case is 409."""
        client, seed = env
        seed("SIM-TEST-POSIX", [POSIX_STEP])
        r = client.get("/api/scenarios/SIM-TEST-POSIX/download?format=perl")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "INVALID_FORMAT"

    def test_missing_scenario_is_still_404(self, env):
        client, _ = env
        r = client.get("/api/scenarios/SIM-NOPE-999/download")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"


def test_auto_on_a_scenario_with_no_emittable_target_is_409(env):
    """Population is zero across the shipped corpus; the invariant is what stops
    a future content edit from handing a DC an undeliverable bundle."""
    client, seed = env
    seed("SIM-TEST-NONE", [
        {"id": "step-01", "command": "curl -s http://h/ -o /tmp/x"},
        {"id": "step-02", "platforms": ["windows"], "command": "Get-Process lsass"},
    ])
    r = client.get("/api/scenarios/SIM-TEST-NONE/download")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "BUNDLE_TARGET_UNSATISFIABLE"

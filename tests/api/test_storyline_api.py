"""Router tests for GET /api/runs/{run_id}/storyline (Detection Proof Layer).

These exercise the HTTP face in isolation from module A (``engine.detection_storyline``).
Because the builder is resolved lazily via ``importlib.import_module`` at call
time, we can inject a stub ``engine.detection_storyline`` module into ``sys.modules`` to
drive the happy path deterministically — and set the entry to ``None`` to force
the "engine not landed yet" 503 path — regardless of whether the real module A
has shipped on disk.
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.storyline import router
    return make_client(router)


@pytest.fixture
def seeded_run(session_factory):
    """Insert one complete Run + Scenario + 2 Results. Returns run_id."""
    from models import Run, Result, Scenario

    async def _seed():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-001",
                name="Credential Dumping",
                plane="EDR",
                version="1.0",
                status="active",
                uc_ref="UCS-EDR-01",
                uc_name="Endpoint Credential Theft",
                tc_ref="TC-EDR-01",
                tc_name="Linux Credential Harvest",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.008",
                mitre_technique_name="OS Credential Dumping",
                steps=[
                    {"id": "step-01", "name": "Read passwd", "mitre_technique": "T1087.001"},
                    {"id": "step-02", "name": "Read shadow", "mitre_technique": "T1003.008"},
                ],
            ))
            db.add(Run(
                run_id="r-1",
                scenario_id="SIM-EDR-001",
                mode="pull",
                status="complete",
                started_at=datetime.utcnow() - timedelta(minutes=5),
                completed_at=datetime.utcnow() - timedelta(minutes=2),
            ))
            for i, (sig, det) in enumerate(
                [("ABIOC", "passwd read"), ("BIOC", "shadow read")], start=1
            ):
                db.add(Result(
                    run_id="r-1",
                    step_id=f"step-0{i}",
                    step_name=f"step {i}",
                    plane="EDR",
                    signal_type=sig,
                    expected_detection=det,
                    observed=(i == 1),
                    observed_at=datetime.utcnow() - timedelta(minutes=1) if i == 1 else None,
                    executed_at=datetime.utcnow() - timedelta(minutes=3),
                ))
            await db.commit()

    asyncio.run(_seed())
    return "r-1"


@pytest.fixture
def stub_storyline_engine(monkeypatch):
    """Inject a fake ``engine.detection_storyline`` whose ``build_storyline`` records its
    inputs and returns an object with a ``.to_dict()`` (the repo convention)."""
    captured: dict = {}

    class _FakeStoryline:
        def __init__(self, run_dict, results, steps):
            self._run = run_dict
            self._results = results
            self._steps = steps

        def to_dict(self):
            return {
                "run_id": self._run["run_id"],
                "scenario_id": self._run["scenario_id"],
                "steps": self._steps,
                "coverage": {
                    "detected": sum(1 for r in self._results if r["observed"]),
                    "expected": len(self._results),
                    "pct": 50.0,
                },
                "mttd": {"p50": 120.0, "p90": 120.0, "count": 1},
                "provenance": {"machine_verified": 0, "attested": 1},
            }

    def _build(run_dict, results, scenario_steps):
        captured["run_dict"] = run_dict
        captured["results"] = results
        captured["scenario_steps"] = scenario_steps
        return _FakeStoryline(run_dict, results, scenario_steps)

    fake_mod = types.ModuleType("engine.detection_storyline")
    fake_mod.build_storyline = _build
    monkeypatch.setitem(sys.modules, "engine.detection_storyline", fake_mod)
    return captured


def test_storyline_happy_path(client, seeded_run, stub_storyline_engine):
    r = client.get(f"/api/runs/{seeded_run}/storyline")
    assert r.status_code == 200
    body = r.json()
    assert "detection_storyline" in body
    story = body["detection_storyline"]
    assert story["run_id"] == "r-1"
    assert story["scenario_id"] == "SIM-EDR-001"
    assert story["coverage"] == {"detected": 1, "expected": 2, "pct": 50.0}

    # The builder received the frozen dict inputs — Run.to_dict, list of
    # Result.to_dict, and the scenario's ordered step spec.
    cap = stub_storyline_engine
    assert cap["run_dict"]["run_id"] == "r-1"
    assert len(cap["results"]) == 2
    assert cap["results"][0]["step_id"] == "step-01"
    assert [s["id"] for s in cap["scenario_steps"]] == ["step-01", "step-02"]


def test_storyline_serializes_plain_dict(client, seeded_run, monkeypatch):
    """A builder that returns a raw dict is passed through unchanged."""
    fake_mod = types.ModuleType("engine.detection_storyline")
    fake_mod.build_storyline = lambda run, results, steps: {"run_id": run["run_id"], "ok": True}
    monkeypatch.setitem(sys.modules, "engine.detection_storyline", fake_mod)

    r = client.get(f"/api/runs/{seeded_run}/storyline")
    assert r.status_code == 200
    assert r.json()["detection_storyline"] == {"run_id": "r-1", "ok": True}


def test_storyline_unknown_run_404(client, stub_storyline_engine):
    r = client.get("/api/runs/no-such-run/storyline")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_storyline_engine_unavailable_503(client, seeded_run, monkeypatch):
    """When module A has not landed, importlib raises → structured 503."""
    # A None entry in sys.modules forces import_module to raise ModuleNotFound.
    monkeypatch.setitem(sys.modules, "engine.detection_storyline", None)
    r = client.get(f"/api/runs/{seeded_run}/storyline")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "STORYLINE_ENGINE_UNAVAILABLE"


def test_storyline_build_failure_500(client, seeded_run, monkeypatch):
    """A builder that raises yields a structured 500 (no stack in body)."""
    def _boom(run, results, steps):
        raise ValueError("kaboom")

    fake_mod = types.ModuleType("engine.detection_storyline")
    fake_mod.build_storyline = _boom
    monkeypatch.setitem(sys.modules, "engine.detection_storyline", fake_mod)

    r = client.get(f"/api/runs/{seeded_run}/storyline")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"]["code"] == "STORYLINE_BUILD_FAILED"
    assert "kaboom" in body["detail"]["detail"]

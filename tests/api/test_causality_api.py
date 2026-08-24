"""Router tests for GET /api/runs/{run_id}/causality (Causality Graph).

These exercise the HTTP face in isolation from module A
(``engine.causality_graph``). Because the builder is resolved lazily via
``importlib.import_module`` at call time, we inject a stub
``engine.causality_graph`` module into ``sys.modules`` to drive the happy path
deterministically — and set the entry to ``None`` to force the "engine not
landed yet" 503 path — regardless of whether the real module A has shipped on
disk. We stub the REAL module name so this stays a true unit test of the router,
not a false-green against a fixture.
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.causality import router
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
def stub_causality_engine(monkeypatch):
    """Inject a fake ``engine.causality_graph`` whose ``build_causality_graph``
    records its inputs and returns an object with a ``.to_dict()`` (the repo
    convention). Signature matches the real builder's keyword-only tail so the
    router can pass ``scenario_meta``."""
    captured: dict = {}

    class _FakeGraph:
        def __init__(self, run_dict, results, steps, scenario_meta):
            self._run = run_dict
            self._results = results
            self._steps = steps
            self._meta = scenario_meta

        def to_dict(self):
            return {
                "run_id": self._run["run_id"],
                "scenario_id": self._run["scenario_id"],
                "nodes": [
                    {"id": f"cgo:{self._run['run_id']}", "kind": "cgo"},
                    *[
                        {"id": f"proc:{self._run['run_id']}:{s['id']}", "kind": "process"}
                        for s in self._steps
                    ],
                    *[
                        {"id": f"alert:{i}", "kind": "alert",
                         "status": "detected" if r["observed"] else "pending"}
                        for i, r in enumerate(self._results)
                    ],
                ],
                "edges": [
                    {"source": f"cgo:{self._run['run_id']}",
                     "target": f"proc:{self._run['run_id']}:{self._steps[0]['id']}",
                     "kind": "process_lineage", "state": "EXPECTED"}
                ] if self._steps else [],
                "summary": {
                    "detected": sum(1 for r in self._results if r["observed"]),
                    "expected": len(self._results),
                    "pct": 50.0,
                },
                "causality_summary": {
                    "edges_by_type": {"process_lineage": 1},
                    "plane": self._meta.get("plane"),
                },
            }

    def _build(run_dict, results, scenario_steps, *,
               observations=None, correlation=None, scenario_meta=None):
        captured["run_dict"] = run_dict
        captured["results"] = results
        captured["scenario_steps"] = scenario_steps
        captured["scenario_meta"] = scenario_meta
        return _FakeGraph(run_dict, results, scenario_steps, scenario_meta or {})

    fake_mod = types.ModuleType("engine.causality_graph")
    fake_mod.build_causality_graph = _build
    monkeypatch.setitem(sys.modules, "engine.causality_graph", fake_mod)
    return captured


def test_causality_happy_path(client, seeded_run, stub_causality_engine):
    r = client.get(f"/api/runs/{seeded_run}/causality")
    assert r.status_code == 200
    body = r.json()
    assert "causality_graph" in body
    graph = body["causality_graph"]
    assert graph["run_id"] == "r-1"
    assert graph["scenario_id"] == "SIM-EDR-001"
    # CGO root + one process node per step + one alert node per result.
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds.count("cgo") == 1
    assert kinds.count("process") == 2
    assert kinds.count("alert") == 2
    assert graph["summary"] == {"detected": 1, "expected": 2, "pct": 50.0}

    # The builder received the frozen dict inputs — Run.to_dict, list of
    # Result.to_dict, the ordered step spec, and parsed scenario stitch meta.
    cap = stub_causality_engine
    assert cap["run_dict"]["run_id"] == "r-1"
    assert len(cap["results"]) == 2
    assert cap["results"][0]["step_id"] == "step-01"
    assert [s["id"] for s in cap["scenario_steps"]] == ["step-01", "step-02"]
    # scenario_meta carries at least the plane lane hint for a known scenario.
    assert cap["scenario_meta"]["plane"] == "EDR"


def test_causality_serializes_plain_dict(client, seeded_run, monkeypatch):
    """A builder that returns a raw dict is passed through unchanged."""
    fake_mod = types.ModuleType("engine.causality_graph")
    fake_mod.build_causality_graph = (
        lambda run, results, steps, **kw: {"run_id": run["run_id"], "ok": True}
    )
    monkeypatch.setitem(sys.modules, "engine.causality_graph", fake_mod)

    r = client.get(f"/api/runs/{seeded_run}/causality")
    assert r.status_code == 200
    assert r.json()["causality_graph"] == {"run_id": "r-1", "ok": True}


def test_causality_unknown_run_404(client, stub_causality_engine):
    r = client.get("/api/runs/no-such-run/causality")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_causality_engine_unavailable_503(client, seeded_run, monkeypatch):
    """When module A has not landed, importlib raises → structured 503."""
    # A None entry in sys.modules forces import_module to raise ModuleNotFound.
    monkeypatch.setitem(sys.modules, "engine.causality_graph", None)
    r = client.get(f"/api/runs/{seeded_run}/causality")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "CAUSALITY_ENGINE_UNAVAILABLE"


def test_causality_build_failure_500(client, seeded_run, monkeypatch):
    """A builder that raises yields a structured 500 (no stack in body)."""
    def _boom(run, results, steps, **kw):
        raise ValueError("kaboom")

    fake_mod = types.ModuleType("engine.causality_graph")
    fake_mod.build_causality_graph = _boom
    monkeypatch.setitem(sys.modules, "engine.causality_graph", fake_mod)

    r = client.get(f"/api/runs/{seeded_run}/causality")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"]["code"] == "CAUSALITY_BUILD_FAILED"
    assert "kaboom" in body["detail"]["detail"]

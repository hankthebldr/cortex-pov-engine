"""The served bootstrap — determinism, refusals, and the unstaged-tool guard.

The bootstrap is the in-pod payload. Its sha256 is baked into the manifest at
GENERATION time, so the served bytes must be a pure function of the scenario:
if this endpoint's output ever drifts from what the manifest anchored, every
served pod dies on a checksum mismatch.

The unstaged-tool guard is the false-negative closer. A scenario that declares
`cluster_posture.payloads` needs those artifacts to exist; without the guard the
pod would run every step, look green, and the absent detection would read as
"Cortex missed it".
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest


@pytest.fixture
def client(make_client):
    from api.payloads import router
    return make_client(router)


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    monkeypatch.delenv("CORTEXSIM_K8S_PAYLOAD_AUTH", raising=False)
    (tmp_path / "deepce.sh").write_bytes(b"#!/bin/sh\n# fake deepce\n")
    return tmp_path


def _seed(session_factory, *, scenario_id="SIM-CDR-999", steps=None):
    from models import Scenario  # noqa: PLC0415

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id, name="K8s delivery probe", plane="CDR",
                version="1.0", status="active",
                uc_ref="UCS-CDR-03", uc_name="x", tc_ref="TC-CDR-01", tc_name="x",
                mitre_tactic="TA0007", mitre_tactic_name="Discovery",
                mitre_technique="T1613", mitre_technique_name="Container Discovery",
                execution_identity={"default_user": "www-data"},
                steps=steps if steps is not None else [{
                    "id": "step-01", "name": "enumerate",
                    "command": "id && cat /proc/self/cgroup",
                    "identity": "www-data",
                    "platforms": ["container"],
                }],
                external_tools=[],
            ))
            await db.commit()

    asyncio.run(_do())


# ---------------------------------------------------------------------------
# Determinism + the digest seam
# ---------------------------------------------------------------------------


def test_bootstrap_is_served_and_the_header_matches_the_body(client, session_factory, shelf):
    _seed(session_factory)
    r = client.get("/api/k8s/bootstrap/SIM-CDR-999")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/x-shellscript")
    assert r.headers["X-CortexSim-Bootstrap-SHA256"] == hashlib.sha256(
        r.content).hexdigest()
    assert "cortexsim" in r.text
    assert "step-01" in r.text


def test_bootstrap_is_deterministic_across_calls(client, session_factory, shelf):
    """The manifest bakes this digest at generation time. Any timestamp, uuid or
    unsorted iteration in the body would make every served pod fail its
    checksum — loudly, but for a reason that is entirely our fault."""
    _seed(session_factory)
    first = client.get("/api/k8s/bootstrap/SIM-CDR-999")
    second = client.get("/api/k8s/bootstrap/SIM-CDR-999")
    assert first.content == second.content
    assert first.headers["X-CortexSim-Bootstrap-SHA256"] == \
        second.headers["X-CortexSim-Bootstrap-SHA256"]


def test_bootstrap_matches_the_generator_the_manifest_uses(client, session_factory, shelf):
    """One definition of the payload. If this endpoint rendered its own variant,
    the manifest's baked digest would describe bytes nobody serves."""
    from engine.k8s_manifest import bootstrap_digest, generate_bootstrap
    from models import Scenario
    from sqlalchemy import select

    _seed(session_factory)

    async def _dict():
        async with session_factory() as db:
            row = (await db.execute(
                select(Scenario).where(Scenario.scenario_id == "SIM-CDR-999")
            )).scalar_one()
            return row.to_dict()

    scenario = asyncio.run(_dict())
    r = client.get("/api/k8s/bootstrap/SIM-CDR-999")
    assert r.text == generate_bootstrap(scenario)
    assert r.headers["X-CortexSim-Bootstrap-SHA256"] == bootstrap_digest(scenario)


def test_bootstrap_sha256_endpoint_agrees_with_the_body(client, session_factory, shelf):
    _seed(session_factory)
    body = client.get("/api/k8s/bootstrap/SIM-CDR-999").content
    hex_only = client.get("/api/k8s/bootstrap/SIM-CDR-999/sha256")
    assert hex_only.text == hashlib.sha256(body).hexdigest() + "\n"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_unknown_scenario_is_a_structured_404(client, shelf):
    r = client.get("/api/k8s/bootstrap/SIM-NOPE-001")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"


def test_windows_only_scenario_is_refused_not_half_rendered(client, session_factory, shelf):
    """A pod must never receive a script that parses and then dies in front of a
    customer — the same 409 `format=k8s` already returns at download time."""
    _seed(session_factory, scenario_id="SIM-WIN-001", steps=[{
        "id": "step-01", "name": "win only",
        "command": "Get-Process", "identity": "SYSTEM",
        "platforms": ["windows"],
    }])
    for path in ("/api/k8s/bootstrap/SIM-WIN-001",
                 "/api/k8s/bootstrap/SIM-WIN-001/sha256"):
        r = client.get(path)
        assert r.status_code == 409, path
        assert r.json()["detail"]["code"] == "BUNDLE_TARGET_UNSATISFIABLE"


# ---------------------------------------------------------------------------
# The unstaged-tool guard — the false-negative closer
# ---------------------------------------------------------------------------


def _posture(monkeypatch, payloads):
    """Force a cluster_posture onto the served scenario.

    `cluster_posture` has no ORM column yet (owned by another track), so a
    DB-derived scenario dict cannot carry it. Patching the parse point exercises
    the guard exactly as it will behave the day the column lands.
    """
    from engine.k8s_manifest import ClusterPosture
    import api.payloads as mod

    posture = ClusterPosture(payloads=payloads) if payloads is not None else None
    monkeypatch.setattr("engine.k8s_manifest.parse_posture", lambda _s: posture)
    return mod


def test_declared_payload_missing_from_the_shelf_is_refused(
        client, session_factory, shelf, monkeypatch):
    _seed(session_factory)
    _posture(monkeypatch, ["deepce.sh", "linpeas.sh"])

    r = client.get("/api/k8s/bootstrap/SIM-CDR-999")
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "PAYLOAD_NOT_STAGED"
    assert d["missing"] == ["linpeas.sh"]
    assert d["staged"] == ["deepce.sh"]
    # The refusal must explain WHY refusing beats serving — otherwise the next
    # person to hit it "fixes" it by removing the guard.
    assert "WITHOUT its tooling" in d["detail"]
    assert "build-payloads.sh" in d["detail"]


def test_declared_payloads_all_staged_are_served(
        client, session_factory, shelf, monkeypatch):
    _seed(session_factory)
    _posture(monkeypatch, ["deepce.sh"])
    assert client.get("/api/k8s/bootstrap/SIM-CDR-999").status_code == 200


def test_no_declared_payloads_means_no_guard(
        client, session_factory, shelf, monkeypatch):
    """A scenario with no cluster_posture is the overwhelming majority today and
    must be unaffected."""
    _seed(session_factory)
    _posture(monkeypatch, None)
    assert client.get("/api/k8s/bootstrap/SIM-CDR-999").status_code == 200


def test_the_guard_also_covers_the_sha256_endpoint(
        client, session_factory, shelf, monkeypatch):
    """If /sha256 answered while the body refused, a DC could bake a digest for
    a payload this SimCore will never serve."""
    _seed(session_factory)
    _posture(monkeypatch, ["absent.sh"])
    r = client.get("/api/k8s/bootstrap/SIM-CDR-999/sha256")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PAYLOAD_NOT_STAGED"

"""Router tests for /api/scenarios/drafts (Composer draft persistence).

Covers the full CRUD round-trip (create → get → list → update → delete), the
DRAFT_SCHEMA_INVALID / DRAFT_NOT_FOUND / DRAFT_NOT_EDITABLE / DRAFT_NOT_DELETABLE
error paths, and the launchable verdict in both directions:

  * an UNBOUND draft is a legal saved state but NOT launchable (tc_bound False,
    refusal_code DRAFT_NOT_TC_BOUND) — the honesty gate,
  * a draft bound to a real FY27 index test case IS tc_bound True once the
    UC/TC index snapshot is loaded.

The draft path must never leak into the Library list — asserted here against the
scenarios router mounted alongside.

NOTE: placed in tests/api/ (not the bare tests/ path named in the task) so it
reuses the make_client / session_factory fixtures in tests/api/conftest.py; the
frozen contract's test_invocation points here too.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(make_client):
    """Drafts router alone — enough for every CRUD + launchable test."""
    from api.drafts import router  # noqa: PLC0415
    return make_client(router)


@pytest.fixture
def client_with_library(make_client):
    """Drafts + scenarios routers together, to prove drafts do not leak into the
    Library list. Order mirrors main.py (drafts first)."""
    from api.drafts import router as drafts_router  # noqa: PLC0415
    from api.scenarios import router as scenarios_router  # noqa: PLC0415
    return make_client(drafts_router, scenarios_router)


def _step(sid: str, *, command: str = "id", detections=None, causality=None) -> dict:
    step = {
        "id": sid,
        "name": f"Step {sid}",
        "command": command,
        "identity": "www-data",
        "mitre_technique": "T1059",
        "expected_detections": detections
        if detections is not None
        else [{"plane": "EDR", "type": "BIOC", "description": "shell exec"}],
    }
    if causality is not None:
        step["causality"] = causality
    return step


def _draft_body(**overrides) -> dict:
    body = {
        "name": "My Composer Draft",
        "plane": "EDR",
        "steps": [_step("step-01")],
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_draft_persists_status_draft_and_sentinels(client):
    resp = client.post("/api/scenarios/drafts", json=_draft_body())
    assert resp.status_code == 201, resp.text
    doc = resp.json()

    assert doc["scenario_id"] == "SIM-DRAFT-my-composer-draft"
    assert doc["status"] == "draft"
    assert doc["plane"] == "EDR"
    assert doc["author"] == "composer"          # default when omitted
    assert "composer-draft" in doc["tags"]
    # Sentinels filled for a from-scratch draft.
    assert doc["uc_ref"] == "UNBOUND"
    assert doc["tc_ref"] == "UNBOUND"
    assert doc["version"] == "0.1-draft"
    # detection_types derived from the union of step detections.
    assert doc["detection_types"] == ["BIOC"]
    # launchable block present; UNBOUND → not launchable but chain is valid.
    lb = doc["launchable"]
    assert lb["chain_valid"] is True
    assert lb["tc_bound"] is False
    assert lb["launchable"] is False
    assert lb["refusal_code"] == "DRAFT_NOT_TC_BOUND"


def test_create_draft_honours_client_author(client):
    resp = client.post(
        "/api/scenarios/drafts", json=_draft_body(author="hank")
    )
    assert resp.status_code == 201
    assert resp.json()["author"] == "hank"


def test_create_draft_slug_collision_auto_suffixes(client):
    a = client.post("/api/scenarios/drafts", json=_draft_body())
    b = client.post("/api/scenarios/drafts", json=_draft_body())
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["scenario_id"] == "SIM-DRAFT-my-composer-draft"
    assert b.json()["scenario_id"] == "SIM-DRAFT-my-composer-draft-2"


@pytest.mark.parametrize(
    "bad_body",
    [
        {"name": "x", "plane": "EDR", "steps": []},                 # empty steps
        {"name": "x", "plane": "NOTAPLANE", "steps": [_step("s1")]},  # bad plane
        {                                                            # dup step id
            "name": "x",
            "plane": "EDR",
            "steps": [_step("dup"), _step("dup")],
        },
        {                                                            # no detection anywhere
            "name": "x",
            "plane": "EDR",
            "steps": [_step("s1", detections=[])],
        },
        {                                                            # bad detection type
            "name": "x",
            "plane": "EDR",
            "steps": [
                _step(
                    "s1",
                    detections=[
                        {"plane": "EDR", "type": "NOPE", "description": "d"}
                    ],
                )
            ],
        },
        {                                                            # forward causality ref
            "name": "x",
            "plane": "EDR",
            "steps": [
                _step("s1", causality={"parent_step": "s2"}),
                _step("s2"),
            ],
        },
    ],
)
def test_create_draft_schema_invalid(client, bad_body):
    resp = client.post("/api/scenarios/drafts", json=bad_body)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "DRAFT_SCHEMA_INVALID"


# ---------------------------------------------------------------------------
# Get / list
# ---------------------------------------------------------------------------


def test_get_draft_roundtrip(client):
    created = client.post("/api/scenarios/drafts", json=_draft_body()).json()
    sid = created["scenario_id"]
    got = client.get(f"/api/scenarios/drafts/{sid}")
    assert got.status_code == 200
    assert got.json()["scenario_id"] == sid
    assert "launchable" in got.json()


def test_get_draft_404_for_missing(client):
    resp = client.get("/api/scenarios/drafts/SIM-DRAFT-nope")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DRAFT_NOT_FOUND"


def test_list_drafts_and_author_filter(client):
    client.post("/api/scenarios/drafts", json=_draft_body(name="Alpha", author="a"))
    client.post("/api/scenarios/drafts", json=_draft_body(name="Beta", author="b"))

    allrows = client.get("/api/scenarios/drafts").json()
    assert allrows["total"] == 2
    assert allrows["projection"] == "summary"

    filtered = client.get("/api/scenarios/drafts", params={"author": "a"}).json()
    assert filtered["total"] == 1
    assert filtered["drafts"][0]["author"] == "a"


def test_drafts_do_not_leak_into_library(client_with_library):
    c = client_with_library
    c.post("/api/scenarios/drafts", json=_draft_body())
    # The drafts list shows it; the Library list must not.
    assert c.get("/api/scenarios/drafts").json()["total"] == 1
    lib = c.get("/api/scenarios").json()
    assert lib["total"] == 0
    assert lib["scenarios"] == []


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_draft_full_replace(client):
    created = client.post("/api/scenarios/drafts", json=_draft_body()).json()
    sid = created["scenario_id"]

    new_body = _draft_body(
        name="Renamed Draft",
        plane="NDR",
        steps=[
            _step(
                "step-01",
                command="curl http://evil",
                detections=[
                    {"plane": "NDR", "type": "XQL", "description": "beacon"}
                ],
            )
        ],
    )
    resp = client.put(f"/api/scenarios/drafts/{sid}", json=new_body)
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    # scenario_id + status immutable; mutable columns replaced.
    assert doc["scenario_id"] == sid
    assert doc["status"] == "draft"
    assert doc["name"] == "Renamed Draft"
    assert doc["plane"] == "NDR"
    assert doc["detection_types"] == ["XQL"]


def test_update_draft_404_for_missing(client):
    resp = client.put("/api/scenarios/drafts/SIM-DRAFT-nope", json=_draft_body())
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DRAFT_NOT_FOUND"


def test_update_draft_409_when_not_a_draft(client, session_factory):
    _seed_active(session_factory, "SIM-EDR-999")
    resp = client.put("/api/scenarios/drafts/SIM-EDR-999", json=_draft_body())
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DRAFT_NOT_EDITABLE"


def test_update_draft_422_on_bad_body(client):
    sid = client.post("/api/scenarios/drafts", json=_draft_body()).json()["scenario_id"]
    resp = client.put(
        f"/api/scenarios/drafts/{sid}",
        json={"name": "x", "plane": "EDR", "steps": []},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "DRAFT_SCHEMA_INVALID"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_draft(client):
    sid = client.post("/api/scenarios/drafts", json=_draft_body()).json()["scenario_id"]
    resp = client.delete(f"/api/scenarios/drafts/{sid}")
    assert resp.status_code == 204
    assert resp.content == b""
    assert client.get(f"/api/scenarios/drafts/{sid}").status_code == 404


def test_delete_draft_404_for_missing(client):
    resp = client.delete("/api/scenarios/drafts/SIM-DRAFT-nope")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DRAFT_NOT_FOUND"


def test_delete_draft_409_when_not_a_draft(client, session_factory):
    _seed_active(session_factory, "SIM-EDR-998")
    resp = client.delete("/api/scenarios/drafts/SIM-EDR-998")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "DRAFT_NOT_DELETABLE"
    # The corpus row survived the guarded delete.
    import asyncio
    from sqlalchemy import select
    from models import Scenario

    async def _still_there():
        async with session_factory() as db:
            row = (
                await db.execute(
                    select(Scenario).where(Scenario.scenario_id == "SIM-EDR-998")
                )
            ).scalar_one_or_none()
            return row is not None

    assert asyncio.run(_still_there())


# ---------------------------------------------------------------------------
# Launchable verdict
# ---------------------------------------------------------------------------


def test_launchable_endpoint_unbound(client):
    sid = client.post("/api/scenarios/drafts", json=_draft_body()).json()["scenario_id"]
    lb = client.get(f"/api/scenarios/drafts/{sid}/launchable").json()
    assert lb["chain_valid"] is True
    assert lb["tc_bound"] is False
    assert lb["launchable"] is False
    assert lb["refusal_code"] == "DRAFT_NOT_TC_BOUND"
    assert lb["reasons"]  # names the fix


def test_launchable_endpoint_404_for_missing(client):
    resp = client.get("/api/scenarios/drafts/SIM-DRAFT-nope/launchable")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DRAFT_NOT_FOUND"


def test_launchable_true_when_bound_to_real_index_tc(client):
    """A draft bound to a real FY27 test case is tc_bound True once the UC/TC
    index snapshot is loaded."""
    import os
    from engine.uctc_registry import registry, default_index_dir

    n = registry.load(default_index_dir(os.environ.get("CORTEXSIM_BASE_DIR", ".")))
    if not registry.loaded or n == 0:
        pytest.skip("UC/TC index snapshot not available in this environment")

    # Pick a real (uc_ref, tc_ref) pair straight from the loaded index.
    tc_ref, tc = next(iter(registry._tc.items()))
    uc_ref = tc.ucs_id

    body = _draft_body(name="Bound Draft", uc_ref=uc_ref, tc_ref=tc_ref)
    created = client.post("/api/scenarios/drafts", json=body).json()
    assert created["uc_ref"] == uc_ref
    assert created["tc_ref"] == tc_ref

    lb = created["launchable"]
    assert lb["chain_valid"] is True
    assert lb["tc_bound"] is True, lb["reasons"]
    assert lb["launchable"] is True
    assert lb["refusal_code"] is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_active(session_factory, scenario_id: str):
    """Insert an ACTIVE corpus row to prove the draft routes never touch it."""
    import asyncio
    from models import Scenario  # noqa: PLC0415

    async def _do():
        async with session_factory() as db:
            db.add(
                Scenario(
                    scenario_id=scenario_id,
                    name="Active corpus scenario",
                    plane="EDR",
                    version="1.0",
                    status="active",
                    detection_types=["BIOC"],
                    uc_ref="UCS-EDR-01",
                    uc_name="X",
                    tc_ref="TC-EDR-01",
                    tc_name="Y",
                    mitre_tactic="TA0006",
                    mitre_tactic_name="Credential Access",
                    mitre_technique="T1003",
                    mitre_technique_name="OS Credential Dumping",
                    steps=[_step("step-01")],
                )
            )
            await db.commit()

    asyncio.run(_do())

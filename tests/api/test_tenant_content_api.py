"""Routes that serve a scenario's tenant content.

    GET  /api/scenarios/{id}/tenant-content        a corpus scenario or a saved draft
    POST /api/scenarios/drafts/tenant-content      an unsaved draft body; persists nothing

Both are READ-ONLY and derive from the TTP cards. What is pinned here is what a
caller could otherwise get wrong about a customer's coverage: nothing is
invented for a stub, a missing catalog is degraded and not empty, and no call
leaves the process.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CARDS = json.loads((REPO / "spec" / "tenant_content_cases.json").read_text(encoding="utf-8"))["scenarios"][0]["cards"]


@pytest.fixture
def catalog(monkeypatch):
    """Point the catalog singleton at the fixture card for one test."""
    from engine.ttp_catalog import catalog as ttp_catalog  # noqa: PLC0415

    def _set(cards):
        monkeypatch.setattr(ttp_catalog, "_raw_by_ttp", dict(cards))

    _set(CARDS)
    return _set


@pytest.fixture
def client(make_client, catalog):
    from api.drafts import router as drafts_router  # noqa: PLC0415
    from api.scenarios import router as scenarios_router  # noqa: PLC0415
    # Same order as main.py: drafts first, so /scenarios/drafts/* is not
    # captured by /scenarios/{scenario_id}.
    return make_client(drafts_router, scenarios_router)


def _det(type_, ttp_ref=None, detection_id=None):
    d = {"plane": "EDR", "type": type_, "description": f"{type_} expectation"}
    if ttp_ref:
        d["ttp_ref"] = ttp_ref
    if detection_id:
        d["detection_id"] = detection_id
    return d


def _body(**over):
    body = {
        "name": "Tenant content draft",
        "plane": "EDR",
        "steps": [
            {
                "id": "step-01", "name": "Query", "command": "id", "identity": "www-data",
                "mitre_technique": "T1059",
                "expected_detections": [_det("XQL", "TTP-T-0001", "xql-q-one")],
            },
            {
                "id": "step-02", "name": "Fuse", "command": "whoami", "identity": "www-data",
                "mitre_technique": "T1059",
                "causality": {"parent_step": "step-01", "pivot": "process_lineage"},
                "expected_detections": [
                    _det("BIOC", "TTP-T-0001", "bioc-b-one"),
                    _det("Correlation", "TTP-T-0001", "CR-T-0001"),
                ],
            },
        ],
    }
    body.update(over)
    return body


def _saved(client) -> str:
    resp = client.post("/api/scenarios/drafts", json=_body())
    assert resp.status_code == 201, resp.text
    return resp.json()["scenario_id"]


# ---------------------------------------------------------------------------
# GET — a saved scenario row
# ---------------------------------------------------------------------------


def test_get_returns_the_manifest_for_a_saved_scenario(client):
    sid = _saved(client)
    resp = client.get(f"/api/scenarios/{sid}/tenant-content")
    assert resp.status_code == 200, resp.text
    m = resp.json()
    assert m["scenario_id"] == sid
    assert m["catalog_loaded"] is True
    assert m["tenant_verified"] == 0
    assert m["write_path"] is False
    declared = next(r for r in m["rules"] if r["rule_id"] == "CR-T-0001")
    assert declared["content_status"] == "intent-only"
    assert declared["binding"] == "declared"
    assert declared["declared_by"] == ["step-02"]


def test_get_invents_nothing_for_a_stub(client):
    m = client.get(f"/api/scenarios/{_saved(client)}/tenant-content").json()
    for rule in m["rules"]:
        assert rule["deploy"] is None
        assert "severity" not in rule
    assert m["summary"]["correlation"]["deployable"] == 0


def test_get_unknown_scenario_is_a_structured_404(client):
    resp = client.get("/api/scenarios/SIM-NOPE-999/tenant-content")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"


def test_get_without_a_catalog_is_degraded_not_empty(client, catalog):
    sid = _saved(client)
    catalog({})
    resp = client.get(f"/api/scenarios/{sid}/tenant-content")
    assert resp.status_code == 200
    m = resp.json()
    assert m["catalog_loaded"] is False
    assert m["rules"] == []
    assert m["summary"]["correlation"]["total"] is None
    assert [n["code"] for n in m["notes"]] == ["CATALOG_NOT_LOADED"]


def test_get_names_a_cited_card_that_is_missing(client, catalog):
    sid = _saved(client)
    catalog({"TTP-OTHER": {"id": "TTP-OTHER", "detections": {}}})
    m = client.get(f"/api/scenarios/{sid}/tenant-content").json()
    assert m["catalog_loaded"] is True
    assert "CARD_NOT_FOUND" in {n["code"] for n in m["notes"]}
    assert m["rules"] == []


# ---------------------------------------------------------------------------
# POST — an unsaved draft
# ---------------------------------------------------------------------------


def test_post_returns_the_manifest_for_an_unsaved_draft(client):
    resp = client.post("/api/scenarios/drafts/tenant-content", json=_body())
    assert resp.status_code == 200, resp.text
    m = resp.json()
    assert m["scenario_id"] is None
    assert m["name"] == "Tenant content draft"
    assert [q["detection_id"] for q in m["queries"]] == ["xql-q-one"]
    assert [b["detection_id"] for b in m["biocs"]] == ["bioc-b-one"]
    assert {r["rule_id"]: r["binding"] for r in m["rules"]}["CR-T-0001"] == "declared"


def test_post_persists_nothing(client):
    assert client.post("/api/scenarios/drafts/tenant-content", json=_body()).status_code == 200
    listed = client.get("/api/scenarios/drafts").json()
    assert listed["total"] == 0
    assert listed["drafts"] == []


def test_post_follows_the_body_it_is_given(client):
    body = _body()
    body["steps"][1]["expected_detections"] = [_det("BIOC", "TTP-T-0001", "bioc-b-one")]
    m = client.post("/api/scenarios/drafts/tenant-content", json=body).json()
    # The Correlation expectation was removed, so the rule is no longer declared.
    assert {r["rule_id"]: r["binding"] for r in m["rules"]}["CR-T-0001"] == "card-only"


def test_post_rejects_an_invalid_draft_body(client):
    resp = client.post("/api/scenarios/drafts/tenant-content", json={"name": "x", "plane": "EDR", "steps": []})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "DRAFT_SCHEMA_INVALID"


def test_draft_route_is_not_captured_as_a_scenario_id(client):
    """/scenarios/drafts/tenant-content must reach the draft route, not
    GET-by-id with scenario_id='drafts'."""
    assert client.post("/api/scenarios/drafts/tenant-content", json=_body()).status_code == 200


# ---------------------------------------------------------------------------
# Read-only, in process
# ---------------------------------------------------------------------------


def test_neither_route_opens_a_connection(client, monkeypatch):
    sid = _saved(client)

    def _refuse(*_a, **_k):
        raise AssertionError("tenant-content made an outbound connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    assert client.get(f"/api/scenarios/{sid}/tenant-content").status_code == 200
    assert client.post("/api/scenarios/drafts/tenant-content", json=_body()).status_code == 200


def test_the_resolver_imports_no_network_or_tenant_client():
    """No write path, by construction: the module cannot reach a tenant."""
    import ast

    tree = ast.parse((REPO / "core" / "engine" / "tenant_content.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported <= {"__future__", "re", "typing", "engine.ttp_catalog"}, imported

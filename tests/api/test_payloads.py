"""K8s payload serving — `/api/k8s/*`.

These guard the properties that make the container delivery honest:
  1. a caller-supplied filename cannot escape the shelf,
  2. a missing artifact is a structured 404 that names the fix, not a trace,
  3. the served digest matches the served bytes (integrity at the seam),
  4. the bootstrap is deterministic, because the manifest bakes its sha256,
  5. a scenario that declares a tool the shelf does not have is REFUSED rather
     than served a bootstrap that would run the attack without it,
  6. the reachability probe can never fail for an auth reason.
"""
from __future__ import annotations

import hashlib
import json

import pytest


@pytest.fixture
def client(make_client):
    from api.payloads import router
    return make_client(router)


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    """Point the shelf at a hermetic temp dir with two staged artifacts."""
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    monkeypatch.delenv("CORTEXSIM_K8S_PAYLOAD_AUTH", raising=False)
    monkeypatch.delenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", raising=False)
    (tmp_path / "deepce.sh").write_bytes(b"#!/bin/sh\n# fake deepce\n")
    (tmp_path / "linpeas.sh").write_bytes(b"#!/bin/sh\n# fake linpeas\n")
    (tmp_path / "MANIFEST.json").write_text(json.dumps({
        "generated_at": "2026-08-03T00:00:00Z",
        "payloads": [
            {"name": "deepce.sh", "source_url": "https://example.invalid/deepce.sh",
             "license": "GPL-3.0", "pinned": True},
        ],
    }))
    return tmp_path


@pytest.fixture
def empty_shelf(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path / "nothing-here"))
    monkeypatch.delenv("CORTEXSIM_K8S_PAYLOAD_AUTH", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_inventory_lists_the_shelf_with_digests_and_provenance(client, shelf):
    body = client.get("/api/k8s/payloads").json()
    names = {p["name"] for p in body["payloads"]}
    assert names == {"deepce.sh", "linpeas.sh"}
    assert body["total"] == 2
    assert body["dist_dir"] == str(shelf)
    deepce = next(p for p in body["payloads"] if p["name"] == "deepce.sh")
    assert deepce["sha256"] == hashlib.sha256((shelf / "deepce.sh").read_bytes()).hexdigest()
    # Provenance travels with the artifact — it is what the manifest banner and
    # the attribution story are built from.
    assert deepce["license"] == "GPL-3.0"
    assert deepce["source_url"] == "https://example.invalid/deepce.sh"
    # Not declared in MANIFEST.json: served anyway, provenance null. A hand-scp'd
    # artifact must not be invisible.
    linpeas = next(p for p in body["payloads"] if p["name"] == "linpeas.sh")
    assert linpeas["license"] is None


def test_inventory_hides_shelf_bookkeeping_files(client, shelf):
    (shelf / "SHA256SUMS").write_text("deadbeef  deepce.sh\n")
    names = {p["name"] for p in client.get("/api/k8s/payloads").json()["payloads"]}
    assert "MANIFEST.json" not in names and "SHA256SUMS" not in names


@pytest.fixture
def cold_catalog(monkeypatch):
    """An EMPTY adapter catalog, asserted rather than assumed — see the twin
    fixture in ``tests/api/test_payloads_shelf.py`` for the full argument. Short
    version: ``declared`` is derived from the ``tools.adapter_catalog`` module
    SINGLETON, so a test that hopes for a cold catalog passes or fails on
    collection order."""
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    monkeypatch.setattr(catalog, "_by_id", {})
    return catalog


def test_empty_shelf_is_a_valid_state(client, empty_shelf, cold_catalog):
    body = client.get("/api/k8s/payloads").json()
    assert body["payloads"] == []
    assert body["total"] == 0
    assert body["auth"] == "open"
    # `declared` is the other half of the answer and is what lets a console show
    # what is MISSING rather than only what is present. On an empty shelf with
    # no adapter pack declaring an artifact, both lists are empty — and that is
    # a genuinely empty shelf, not a degraded read.
    assert body["declared"] == []
    assert "writable" in body


def test_unreadable_manifest_degrades_to_no_provenance(client, shelf):
    shelf.joinpath("MANIFEST.json").write_text("{ this is not json")
    body = client.get("/api/k8s/payloads").json()
    assert body["total"] == 2
    assert all(p["license"] is None for p in body["payloads"])


# ---------------------------------------------------------------------------
# Name handling — traversal is REACHABLE here, unlike /api/agents/binary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [".hidden", "x" * 100, "deep ce.sh", "a;b", "-x"])
def test_junk_names_reach_the_handler_and_get_a_structured_400(client, shelf, name):
    r = client.get(f"/api/k8s/payload/{name}")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_PAYLOAD_NAME"


@pytest.mark.parametrize("name", [
    "../../etc/passwd", "..%2f..%2fetc%2fpasswd", "a/b", "..", "../MANIFEST.json",
])
def test_traversal_never_returns_content(client, shelf, name):
    """Starlette's router normalises most of these away before the handler sees
    them, which is why the guard is asserted directly below as well — the
    property is 'nothing outside the shelf is ever served', not 'the regex
    fires'."""
    r = client.get(f"/api/k8s/payload/{name}")
    assert r.status_code in (400, 404)
    assert b"root:" not in r.content


def test_resolve_payload_never_escapes_the_shelf(shelf):
    from fastapi import HTTPException

    from api.payloads import payload_dist_dir, resolve_payload

    for bad in ("../../etc/passwd", "a/b", "..", "", "/etc/passwd", "x" * 100):
        with pytest.raises(HTTPException) as exc:
            resolve_payload(bad)
        assert exc.value.detail["code"] == "BAD_PAYLOAD_NAME"
    root = payload_dist_dir().resolve()
    assert resolve_payload("deepce.sh").parent == root


def test_a_symlink_out_of_the_shelf_is_not_served(client, shelf, tmp_path):
    """The name passes the regex; containment is what stops it. Two independent
    gates, because one of them being wrong must not be enough."""
    secret = tmp_path.parent / "outside-secret.txt"
    secret.write_text("root:x:0:0:\n")
    (shelf / "escape.sh").symlink_to(secret)
    r = client.get("/api/k8s/payload/escape.sh")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_PAYLOAD_NAME"
    assert b"root:" not in r.content


def test_bookkeeping_files_are_not_servable(client, shelf):
    r = client.get("/api/k8s/payload/MANIFEST.json")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_PAYLOAD_NAME"


# ---------------------------------------------------------------------------
# Serving + integrity at the seam
# ---------------------------------------------------------------------------


def test_payload_download_echoes_a_digest_of_the_served_bytes(client, shelf):
    r = client.get("/api/k8s/payload/deepce.sh")
    assert r.status_code == 200
    assert r.content == (shelf / "deepce.sh").read_bytes()
    assert r.headers["X-CortexSim-Payload-SHA256"] == hashlib.sha256(r.content).hexdigest()


def test_payload_sha256_endpoint_is_bare_hex(client, shelf):
    r = client.get("/api/k8s/payload/deepce.sh/sha256")
    assert r.status_code == 200
    assert r.text == hashlib.sha256((shelf / "deepce.sh").read_bytes()).hexdigest() + "\n"


def test_digest_tracks_a_restaged_artifact(client, shelf):
    """The (path, mtime, size) cache key must invalidate when the shelf is
    re-staged, or an updated artifact would keep serving a stale digest — the
    exact way an integrity check quietly becomes decorative."""
    first = client.get("/api/k8s/payload/deepce.sh/sha256").text.strip()
    p = shelf / "deepce.sh"
    p.write_bytes(b"#!/bin/sh\n# fake deepce v2 (longer, so size changes)\n")
    second = client.get("/api/k8s/payload/deepce.sh/sha256").text.strip()
    assert second != first
    assert second == hashlib.sha256(p.read_bytes()).hexdigest()


def test_the_three_digest_surfaces_agree(client, shelf):
    """Inventory, download header and /sha256 must be one number. A DC who
    verifies with one and a pod anchored on another is how an integrity story
    becomes decorative."""
    inv = next(p for p in client.get("/api/k8s/payloads").json()["payloads"]
               if p["name"] == "linpeas.sh")
    dl = client.get("/api/k8s/payload/linpeas.sh")
    bare = client.get("/api/k8s/payload/linpeas.sh/sha256").text.strip()
    assert inv["sha256"] == dl.headers["X-CortexSim-Payload-SHA256"] == bare
    assert bare == hashlib.sha256(dl.content).hexdigest()


def test_missing_payload_404_names_the_shelf_and_the_fix(client, shelf):
    r = client.get("/api/k8s/payload/nope.sh")
    assert r.status_code == 404
    d = r.json()["detail"]
    assert d["code"] == "PAYLOAD_UNAVAILABLE"
    assert str(shelf) in d["detail"]
    assert "build-payloads.sh" in d["detail"]
    assert "CORTEXSIM_PAYLOAD_DIST" in d["detail"]
    # names what IS available, so the DC does not have to guess at a typo
    assert "deepce.sh" in d["detail"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_default_is_open_and_says_so(client, shelf):
    assert client.get("/api/k8s/payloads").json()["auth"] == "open"
    assert client.get("/api/k8s/payload/deepce.sh").status_code == 200


def test_token_mode_gates_artifacts(client, shelf, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_AUTH", "token")
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", "cxp_good,cxp_other")

    r = client.get("/api/k8s/payload/deepce.sh")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "PAYLOAD_TOKEN_DENIED"

    r = client.get("/api/k8s/payload/deepce.sh", headers={"Authorization": "Bearer cxp_wrong"})
    assert r.status_code == 403

    r = client.get("/api/k8s/payload/deepce.sh", headers={"Authorization": "Bearer cxp_good"})
    assert r.status_code == 200
    r = client.get("/api/k8s/payload/deepce.sh/sha256", headers={"Authorization": "Bearer cxp_other"})
    assert r.status_code == 200


def test_token_mode_never_gates_the_reachability_probe(client, shelf, monkeypatch):
    """The init container reports a failed /payloads call as SIMCORE_UNREACHABLE.
    If a credential could also fail it, that message would send a DC to argue
    with the customer's network team over an auth problem."""
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_AUTH", "token")
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", "cxp_good")
    r = client.get("/api/k8s/payloads")
    assert r.status_code == 200
    assert r.json()["auth"] == "token"


def test_token_mode_without_tokens_fails_closed(client, shelf, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_AUTH", "token")
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", "")
    r = client.get("/api/k8s/payload/deepce.sh", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "PAYLOAD_AUTH_MISCONFIGURED"


def test_open_shelf_logs_a_warning_naming_the_directory(shelf, caplog):
    import logging

    from api.payloads import _warn_if_shelf_is_open

    with caplog.at_level(logging.WARNING, logger="api.payloads"):
        _warn_if_shelf_is_open()
    assert any(str(shelf) in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Posture-finding vocabulary
# ---------------------------------------------------------------------------


def test_posture_findings_is_served_from_the_derived_vocabulary(client):
    from engine.k8s_manifest import vocabulary

    body = client.get("/api/k8s/posture-findings").json()
    assert body["findings"] == vocabulary()
    assert body["total"] == len(body["findings"])

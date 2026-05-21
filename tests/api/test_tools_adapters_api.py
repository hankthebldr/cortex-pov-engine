"""Direct router tests for the tool-adapter catalog endpoints
(``GET /api/tools/adapters`` + ``GET /api/tools/adapters/{adapter_id}``).

The catalog is a process-wide singleton populated from ``tools/packs/*.yml``
at startup. These tests pre-load the singleton against the real corpus so
the API surface is exercised end-to-end without needing the full lifespan
handler.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKS_DIR = REPO_ROOT / "tools" / "packs"


@pytest.fixture(scope="module", autouse=True)
def _load_catalog():
    """Populate the singleton once for this test module."""
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    catalog.load(str(PACKS_DIR))
    assert catalog.count() > 0
    yield


@pytest.fixture
def client(make_client):
    from api.tools import router  # noqa: PLC0415
    return make_client(router)


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_adapters_returns_full_catalog(client):
    resp = client.get("/api/tools/adapters")
    assert resp.status_code == 200
    body = resp.json()
    assert "adapters" in body
    assert "total" in body
    assert body["total"] == len(body["adapters"])
    assert body["total"] >= 18  # current catalog floor — bump with each batch
    # Sorted by adapter_id for stable rendering
    ids = [a["adapter_id"] for a in body["adapters"]]
    assert ids == sorted(ids)


def test_list_adapters_summary_shape(client):
    body = client.get("/api/tools/adapters").json()
    one = body["adapters"][0]
    # Required keys for the UI card render
    for key in (
        "adapter_id", "name", "version", "tier", "category",
        "safety_class", "planes", "expected_techniques",
        "target_platform", "license", "tags",
    ):
        assert key in one, f"missing key {key!r} in summary payload"


def test_filter_by_plane(client):
    body = client.get("/api/tools/adapters?plane=EDR").json()
    for a in body["adapters"]:
        assert "EDR" in a["planes"], a
    # Mimikatz lives on EDR + ITDR — must be in the EDR-filtered set
    ids = {a["adapter_id"] for a in body["adapters"]}
    assert "TOOL-MIMIKATZ" in ids


def test_filter_by_tier(client):
    body = client.get("/api/tools/adapters?tier=4").json()
    for a in body["adapters"]:
        assert a["tier"] == 4
    # Tier 4 includes nmap, nuclei, trivy, prowler — pick the most stable
    ids = {a["adapter_id"] for a in body["adapters"]}
    assert "TOOL-NMAP" in ids


def test_filter_by_safety_class(client):
    body = client.get("/api/tools/adapters?safety_class=c2-framework").json()
    for a in body["adapters"]:
        assert a["safety_class"] == "c2-framework"
    ids = {a["adapter_id"] for a in body["adapters"]}
    assert "TOOL-SLIVER" in ids


def test_filter_by_category(client):
    body = client.get("/api/tools/adapters?category=identity-credential").json()
    for a in body["adapters"]:
        assert a["category"] == "identity-credential"


def test_unknown_filter_value_returns_empty(client):
    # Defensive: a stale UI sending `plane=NOPE` shouldn't 400 — it should
    # quietly return an empty list so the picker shows "no matches".
    body = client.get("/api/tools/adapters?plane=NOPE").json()
    assert body == {"adapters": [], "total": 0}


def test_filters_compose_with_logical_and(client):
    body = client.get(
        "/api/tools/adapters?plane=EDR&tier=3&safety_class=dual-use-lab-only"
    ).json()
    for a in body["adapters"]:
        assert "EDR" in a["planes"]
        assert a["tier"] == 3
        assert a["safety_class"] == "dual-use-lab-only"


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


def test_get_adapter_detail_returns_full_schema(client):
    resp = client.get("/api/tools/adapters/TOOL-NMAP")
    assert resp.status_code == 200
    body = resp.json()
    # The detail payload is the full ToolAdapterSchema dump — includes
    # invoke, install, upstream sub-objects the summary omits.
    assert body["adapter_id"] == "TOOL-NMAP"
    assert "invoke" in body and body["invoke"]["target_platform"] == "linux"
    assert "install" in body
    assert body["upstream"]["license"] == "NPSL"


def test_get_adapter_detail_unknown_id_404(client):
    resp = client.get("/api/tools/adapters/TOOL-DOES-NOT-EXIST")
    assert resp.status_code == 404
    err = resp.json()["detail"]
    assert err["code"] == "ADAPTER_NOT_FOUND"
    assert "TOOL-DOES-NOT-EXIST" in err["detail"]

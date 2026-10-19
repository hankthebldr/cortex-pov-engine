# tests/integration/xsiam/test_operations_path_params.py
"""Operation path params must not be able to change WHICH operation runs.

The gated executor substitutes ``path_params`` into the catalog path verbatim,
and httpx resolves dot-segments before sending. So the write-gated operation
``OP-ASSET-GROUPS-UPDATE-GROUP-ID`` (``POST /asset-groups/update/{group_id}``)
called with ``group_id="../delete/123"`` put

    POST /public_api/v1/asset-groups/delete/123

on the wire — the DESTRUCTIVE delete — authorised only by
``CORTEXSIM_XSIAM_ALLOW_WRITE`` + ``consent.write_authorized``. The separate
``CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE`` / ``destructive_authorized`` key, which
exists precisely so write permission does not imply delete permission, was never
consulted. The dry-run preview echoed the un-normalised path, so even a careful
operator previewing first saw ``/update/../delete/123`` rather than a delete.

Read ops (live by default, no gate) could likewise be steered to any GET
endpoint on the tenant outside the declared catalog.

These tests drive the REAL ``XsiamClient`` over an ``httpx.MockTransport`` so
what is asserted is the request that would reach the tenant.
"""
from __future__ import annotations

import httpx
import pytest

from tests.integration.xsiam.test_operations_api import _build_app, _ret


def _wire(monkeypatch, xsiam_api):
    """Swap the stub for a real XsiamClient whose transport records the wire."""
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig

    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        # raw_path = path + query exactly as sent, so a smuggled query shows.
        seen.append((req.method, req.url.raw_path.decode()))
        return httpx.Response(200, json={"reply": {"ok": True}})

    client = XsiamClient(
        XsiamTenantConfig(base_url="https://api-acme.xdr.us.paloaltonetworks.com",
                          region="us", api_key_id="1"),
        "k" * 40,
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(xsiam_api, "load_xsiam_client", lambda session, name: _ret(client))
    return seen


@pytest.mark.parametrize("group_id", [
    "../delete/123", "..", ".", "a/b", "..\\delete\\123",
])
def test_write_op_path_param_cannot_reach_another_operation(tmp_path, monkeypatch, group_id):
    client, _stub, xsiam_api = _build_app(tmp_path, monkeypatch)
    seen = _wire(monkeypatch, xsiam_api)
    monkeypatch.setattr(xsiam_api.settings, "CORTEXSIM_XSIAM_ALLOW_WRITE", True)
    monkeypatch.setattr(xsiam_api.settings, "CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE", False)

    r = client.post(
        "/api/xsiam/tenants/acme/operations/OP-ASSET-GROUPS-UPDATE-GROUP-ID",
        json={"dry_run": False, "path_params": {"group_id": group_id},
              "consent": {"write_authorized": True}},
    )
    assert seen == [], f"reached the tenant: {seen}"
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "XSIAM_CONFIG_ERROR"


def test_read_op_path_param_cannot_leave_the_catalog_path(tmp_path, monkeypatch):
    client, _stub, xsiam_api = _build_app(tmp_path, monkeypatch)
    seen = _wire(monkeypatch, xsiam_api)
    r = client.post(
        "/api/xsiam/tenants/acme/operations/OP-ASSETS-ID",
        json={"path_params": {"id": "../../v1/endpoints/get_endpoint"}},
    )
    assert seen == [], f"reached the tenant: {seen}"
    assert r.status_code == 400, r.text


def test_ordinary_path_params_still_reach_their_operation(tmp_path, monkeypatch):
    client, _stub, xsiam_api = _build_app(tmp_path, monkeypatch)
    seen = _wire(monkeypatch, xsiam_api)
    monkeypatch.setattr(xsiam_api.settings, "CORTEXSIM_XSIAM_ALLOW_WRITE", True)

    r = client.post(
        "/api/xsiam/tenants/acme/operations/OP-ASSET-GROUPS-UPDATE-GROUP-ID",
        json={"dry_run": False, "path_params": {"group_id": "42"},
              "consent": {"write_authorized": True}},
    )
    assert r.status_code == 200, r.text
    assert seen == [("POST", "/public_api/v1/asset-groups/update/42")]

    # A value with URL-significant characters is encoded into ONE segment, so
    # it cannot smuggle a query string or fragment either.
    seen.clear()
    r = client.post(
        "/api/xsiam/tenants/acme/operations/OP-ASSETS-ID",
        json={"path_params": {"id": "a b?c=1#x"}},
    )
    assert r.status_code == 200, r.text
    assert seen == [("GET", "/public_api/v1/assets/a%20b%3Fc%3D1%23x")], seen

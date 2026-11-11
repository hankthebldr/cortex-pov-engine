"""The write gate through MCP (spec section 6): a write never reaches a tenant.

Driven against the real SimCore xsiam executor (``stubbed_app``). The tenant
witness is ``stub.calls``: every assertion that the tenant saw nothing is on
that list being empty, with both global flags monkeypatched ``True``.
"""
from __future__ import annotations

import json
import shlex
from types import SimpleNamespace

import pytest

from tests.mcp_server.conftest import call

WRITE_OP = "OP-ALERTS-INSERT-PARSED-ALERTS"
DESTRUCTIVE_OP = "OP-BIOC-DELETE"
# The stubbed_app harness imports starlette.testclient (via _build_app), which
# warns that httpx should be httpx2 (pre-existing, seen in Task 10b). Exactly
# that category is ignored here; any other warning stays an error under -W error.
pytestmark = pytest.mark.filterwarnings(
    "ignore::starlette.exceptions.StarletteDeprecationWarning")

BODY = {"request_data": {"alerts": [{"product": "CortexSim", "vendor": "PANW"}]}}


def _every_flag_on(stubbed_app, monkeypatch) -> None:
    settings = stubbed_app.xsiam_api.settings
    monkeypatch.setattr(settings, "CORTEXSIM_XSIAM_ALLOW_WRITE", True)
    monkeypatch.setattr(settings, "CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE", True)


def _human_body(command: str) -> dict:
    return json.loads(shlex.split(command)[-1])


@pytest.mark.asyncio
async def test_write_op_with_every_key_set_still_previews_and_tenant_saw_nothing(stubbed_app, monkeypatch):
    _every_flag_on(stubbed_app, monkeypatch)
    await stubbed_app.seed_tenant("deathstar")
    res = await call(stubbed_app.mcp(), "xsiam.operation.execute", op_id=WRITE_OP,
                     tenant="deathstar", dry_run=False, body=BODY)

    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "MCP_WRITE_NOT_EXECUTABLE"
    data = sc["data"]
    assert data["dry_run"] is True and data["op_id"] == WRITE_OP and data["access_class"] == "write"
    assert data["request"] == {"method": "POST", "path": "/public_api/v1/alerts/insert_parsed_alerts",
                               "body": BODY, "query": None}
    assert data["consent_required"] == {"flag": "CORTEXSIM_XSIAM_ALLOW_WRITE=1",
                                        "consent": {"write_authorized": True}, "dry_run": False}
    cmd = data["execute_as_human"]
    assert cmd.startswith(
        f"curl -X POST http://simcore/api/xsiam/tenants/deathstar/operations/{WRITE_OP}")
    assert _human_body(cmd) == {"path_params": {}, "query": {}, "body": BODY, "dry_run": False,
                                "consent": {"write_authorized": True}}
    ev = sc["evidence"]
    assert ev["basis"] == "preview" and ev["queries_issued"] == 0
    assert ev["statement"].startswith("Preview only. Nothing was sent to the tenant.")
    assert stubbed_app.stub.calls == []


@pytest.mark.asyncio
async def test_destructive_op_same(stubbed_app, monkeypatch):
    _every_flag_on(stubbed_app, monkeypatch)
    await stubbed_app.seed_tenant("deathstar")
    res = await call(stubbed_app.mcp(), "xsiam.operation.execute", op_id=DESTRUCTIVE_OP,
                     dry_run=False, body={"request_data": {"rule_id": 7}})

    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "MCP_WRITE_NOT_EXECUTABLE"
    data = sc["data"]
    assert data["dry_run"] is True and data["access_class"] == "destructive"
    assert data["request"]["path"] == "/public_api/v1/bioc/delete"
    assert data["consent_required"] == {"flag": "CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE=1",
                                        "consent": {"destructive_authorized": True}, "dry_run": False}
    assert _human_body(data["execute_as_human"])["consent"] == {"destructive_authorized": True}
    assert sc["evidence"]["basis"] == "preview" and sc["evidence"]["queries_issued"] == 0
    assert stubbed_app.stub.calls == []


@pytest.mark.asyncio
async def test_consent_kwarg_is_rejected_by_the_schema(stubbed_app, monkeypatch):
    _every_flag_on(stubbed_app, monkeypatch)
    await stubbed_app.seed_tenant("deathstar")
    res = await call(stubbed_app.mcp(), "xsiam.operation.execute", op_id=WRITE_OP, dry_run=False,
                     body=BODY, consent={"write_authorized": True})

    assert res.isError
    assert res.structuredContent["code"] == "MCP_INVALID_ARGUMENTS"
    assert "consent" in res.structuredContent["detail"]
    assert not [p for p in stubbed_app.simcore_calls if "/operations" in p]
    assert stubbed_app.stub.calls == []


@pytest.mark.asyncio
async def test_write_preview_without_dry_run_false_is_a_preview_not_an_error(stubbed_app):
    await stubbed_app.seed_tenant("deathstar")
    res = await call(stubbed_app.mcp(), "xsiam.operation.execute", op_id=WRITE_OP, body=BODY)

    assert not res.isError
    data = res.structuredContent["data"]
    assert data["dry_run"] is True and data["consent_required"]["consent"] == {"write_authorized": True}
    assert "execute_as_human" in data
    ev = res.structuredContent["evidence"]
    assert ev["basis"] == "preview" and ev["queries_issued"] == 0 and ev["tenant"] == "deathstar"
    assert stubbed_app.stub.calls == []


@pytest.mark.asyncio
async def test_unrecognised_access_class_is_refused_never_read(stubbed_app, monkeypatch):
    """Tolerance hides bugs: an access class the gate does not know is not 'read'."""
    await stubbed_app.seed_tenant("deathstar")
    real = stubbed_app.xsiam_api.op_catalog.find("OP-INDICATORS-GET")
    odd = {**real.model_dump(), "access_class": "admin"}
    monkeypatch.setattr(stubbed_app.xsiam_api.op_catalog, "find",
                        lambda op_id: SimpleNamespace(model_dump=lambda: odd, **odd))
    res = await call(stubbed_app.mcp(), "xsiam.operation.execute", op_id="OP-INDICATORS-GET")

    assert res.isError and res.structuredContent["code"] == "SIMCORE_SHAPE_UNKNOWN"
    assert "admin" in res.structuredContent["detail"]
    assert not [p for p in stubbed_app.simcore_calls if p.startswith("POST ")]
    assert stubbed_app.stub.calls == []

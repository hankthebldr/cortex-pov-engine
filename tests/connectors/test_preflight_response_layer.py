"""The ``response_layer`` preflight stage: is the SOC Framework on this tenant?

Driven by an ``XsiamClient`` over ``httpx.MockTransport``. A green result here
proves the stage LOGIC, not the tenant: ``queries_issued`` is the tell.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import httpx

from connectors import preflight as pf
from connectors.socfw import CONFIG_LISTS

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "socfw"
NOW = datetime(2026, 11, 11, 12, 0, 0)


def _fx(name):
    return json.loads((FIX / name).read_text())


def _stage(report, sid):
    return next(s for s in report.stages if s.id == sid)


def _client(handler):
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig

    cfg = XsiamTenantConfig(base_url="https://api-t.xdr.us.paloaltonetworks.com",
                            region="us", auth_mode="standard", api_key_id="1")
    return XsiamClient(cfg, "key", transport=httpx.MockTransport(handler))


def _tenant(*, packs=None, names=None, list_status=200, packs_status=200,
            exec_rows=None, xql_status=200):
    packs = _fx("installed_packs.json") if packs is None else packs
    names = _fx("list_names.json") if names is None else names
    exec_rows = [] if exec_rows is None else exec_rows
    docs = {"SOCFWTenantConfig": _fx("tenant_config.json"),
            "SOCFWConfigModes": _fx("config_modes.json")}
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        seen.append(p)
        if p.endswith("healthcheck"):
            return httpx.Response(200, json={"reply": {"status": "ok"}},
                                  headers={"date": "Wed, 11 Nov 2026 12:00:00 GMT"})
        if p.endswith("contentpacks/metadata/installed"):
            return httpx.Response(packs_status, json=packs)
        if p.endswith("lists/names"):
            return httpx.Response(200, json=names)
        if "/lists/" in p:
            if list_status != 200:
                return httpx.Response(list_status, json={})
            return httpx.Response(200, json=docs[p.rsplit("/", 1)[1]])
        if p.endswith("start_xql_query"):
            return httpx.Response(xql_status, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {
            "status": "SUCCESS", "results": {"data": exec_rows}}})

    handler.seen = seen
    return handler


async def _run(handler, **kw):
    return await pf.preflight_tenant(_client(handler), integration_name="acme",
                                     now=NOW, **kw)


def run(handler, **kw):
    return asyncio.run(_run(handler, **kw))


def test_present_framework_stage_ok():
    st = _stage(run(_tenant()), "response_layer")
    assert st.status == "ok" and st.code == "PF_OK"
    assert st.extra["response_layer"]["state"] == "PRESENT"


def test_absent_framework_degrades_overall():
    r = run(_tenant(packs=[{"id": "Base", "currentVersion": "1.0.0"}], names=[]))
    st = _stage(r, "response_layer")
    assert st.status == "degraded" and st.code == "PF_RESPONSE_LAYER_ABSENT"
    assert st.extra["response_layer"]["state"] == "ABSENT"
    assert r.overall == "degraded"


def test_list_content_404_is_present_unread():
    st = _stage(run(_tenant(list_status=404)), "response_layer")
    assert st.code == "PF_RESPONSE_LAYER_UNREAD" and st.status == "degraded"
    assert st.extra["response_layer"]["state"] == "PRESENT_UNREAD"


def test_list_content_405_is_present_unread():
    st = _stage(run(_tenant(list_status=405)), "response_layer")
    assert st.code == "PF_RESPONSE_LAYER_UNREAD"


def test_unrecognized_packs_reply_is_shape_code_not_absent():
    st = _stage(run(_tenant(packs={"unexpected": True})), "response_layer")
    assert st.code == "PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED"
    assert st.status == "degraded"
    assert st.extra["response_layer"]["state"] == "UNKNOWN"


def test_packs_forbidden_is_unknown_naming_the_credential_role():
    st = _stage(run(_tenant(packs_status=403)), "response_layer")
    assert st.extra["response_layer"]["state"] == "UNKNOWN"
    assert st.code == "PF_RESPONSE_LAYER_UNREAD"
    assert "role" in st.remediation.lower()


def test_queries_issued_counts_each_call():
    # one for installed packs, one for list names, one read per CONFIG_LISTS
    # entry (2), one XQL
    r = run(_tenant())
    base = pf.preflight_tenant  # noqa: F841 — baseline taken with the stage off
    off = run(_tenant(), response_layer=False)
    assert len(CONFIG_LISTS) == 2
    assert r.queries_issued == off.queries_issued + 5


def test_stage_skipped_when_xql_blocked():
    r = run(_tenant(xql_status=403))
    assert _stage(r, "response_layer").status == "skipped"


def test_stage_skipped_when_auth_rejected():
    def handler(request):
        return httpx.Response(401, json={})
    assert _stage(run(handler), "response_layer").status == "skipped"


def test_opt_out():
    r = run(_tenant(), response_layer=False)
    assert all(s.id != "response_layer" for s in r.stages)


def test_execution_evidence_marks_triage_live():
    rows = [{"action_taken": "auto_triage_closed", "execution_mode": "production",
             "action_actor": "automation", "n": 4}]
    st = _stage(run(_tenant(exec_rows=rows)), "response_layer")
    assert st.extra["response_layer"]["auto_triage"]["live"] is True


def test_xql_text_has_no_current_time_arithmetic():
    assert "current_time()" not in pf.RESPONSE_LAYER_XQL
    assert pf.RESPONSE_LAYER_XQL == (
        "dataset = xsiam_socfw_ir_execution_raw | comp count() as n "
        "by action_taken, execution_mode, action_actor")


def test_no_write_method_is_ever_sent():
    h = _tenant()
    seen_methods: list = []
    inner = h

    def spy(request):
        seen_methods.append((request.method, request.url.path))
        return inner(request)
    run(spy)
    reads = [m for m, p in seen_methods
             if "contentpacks" in p or "/lists/" in p]
    assert reads and all(m == "GET" for m in reads)


def test_socfw_operation_pack_loads_and_is_read_only():
    from integrations.xsiam.operations.loader import _parse_and_validate

    base = Path(__file__).resolve().parents[2]
    ops, errors = _parse_and_validate(str(
        base / "core/integrations/xsiam/operations/packs/socfw.yml"))
    assert errors == []
    assert {o.op_id for o in ops} == {
        "OP-SOCFW-PACKS-INSTALLED", "OP-SOCFW-LIST-NAMES", "OP-SOCFW-LIST-GET"}
    assert all(o.access_class == "read" and o.method == "GET" for o in ops)
    assert all("povkit-observed" in o.tags for o in ops)


def _wrap(inner, fail_when):
    def h(request):
        if fail_when(request):
            raise httpx.ConnectError("reset")
        return inner(request)
    return h


def test_transport_error_on_list_read_degrades_not_raises():
    h = _wrap(_tenant(), lambda r: "/lists/download/" in r.url.path)
    st = _stage(run(h), "response_layer")
    assert st.status == "degraded" and st.code == "PF_RESPONSE_LAYER_UNREAD"
    assert "list content" in st.detail


def test_transport_error_on_xql_degrades_not_raises():
    h = _wrap(_tenant(), lambda r: r.url.path.endswith("start_xql_query")
              and b"xsiam_socfw" in r.content)
    st = _stage(run(h), "response_layer")
    assert st.status == "degraded" and st.code == "PF_RESPONSE_LAYER_UNREAD"
    assert "XQL read" in st.detail


def test_unrecognised_xql_envelope_is_shape_code():
    def h(request):
        if request.url.path.endswith("get_query_results"):
            return httpx.Response(200, json={"reply": {"status": "SUCCESS",
                                                       "results": "oops"}})
        return _inner(request)
    _inner = _tenant()
    st = _stage(run(h), "response_layer")
    assert st.code == "PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED"
    assert "XQL" in st.detail


def test_xql_non_quota_error_names_the_xql_read():
    def h(request):
        if request.url.path.endswith("start_xql_query") and b"xsiam_socfw" in request.content:
            return httpx.Response(200, json={"reply": {"err_code": "X", "err_msg": "bad"}})
        return _inner(request)
    _inner = _tenant()
    st = _stage(run(h), "response_layer")
    assert st.status == "degraded" and st.code == "PF_RESPONSE_LAYER_UNREAD"
    assert "XQL read" in st.detail


def test_list_content_not_a_json_object_is_shape_code():
    def h(request):
        if "/lists/download/" in request.url.path:
            return httpx.Response(200, json=[1, 2])
        return _inner(request)
    _inner = _tenant()
    st = _stage(run(h), "response_layer")
    assert st.code == "PF_RESPONSE_LAYER_SHAPE_UNRECOGNIZED"

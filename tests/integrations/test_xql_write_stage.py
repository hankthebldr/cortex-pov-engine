"""SEC-05 — XQL is not read-only, so SimCore must refuse its write stage.

``docs/reference/xsiam-platform-apis/07-xql-api.md`` says "XQL never writes to
the tenant", and ``OP-XQL-START-XQL-QUERY`` is catalogued ``access_class: read``.
Both are wrong: XQL's ``target`` stage writes the query's result into a dataset
or a lookup — ``| target type=lookup append=false <name>`` REPLACES an existing
lookup table, which may be one the customer's own correlation rules read. Four
paths sent an operator- or content-supplied query string straight to
``start_xql_query`` with no inspection:

* ``POST /api/xsiam/tenants/{name}/xql`` (free text from the console);
* ``POST /api/xsiam/tenants/{name}/operations/OP-XQL-START-XQL-QUERY`` (the
  generic executor — a "read" op, so it ran live with no consent);
* ``verifier.xsiam_query_runner`` / ``xsiam_rows_runner`` (scenario
  ``verification_xql`` and assertion probes, i.e. authored content);
* a Composer draft's ``verification_xql``, persisted and then verified.

That is a write path to Cortex, which this product promises not to have
(``CORTEXSIM_XSIAM_ALLOW_WRITE`` default-off is meaningless if XQL bypasses it).

The fix is ONE guard, ``integrations.xsiam.client.refuse_xql_write_stages``,
enforced inside ``XsiamClient.start_xql_query`` — the only method that puts a
query string on the wire (a drift guard below holds that) — and called early by
the API routes, the operation executor and the verifier runners so a refused
query costs no credential decrypt and no quota.

The lexer is deliberately fail-closed. It splits on ``|`` outside double-quoted
string literals under every combination of {``//``/``/* */`` comments honoured,
not honoured} x {backslash escapes honoured, not honoured}, and refuses if ANY
reading finds a ``target`` stage. Where XQL's real grammar differs from one
reading, that reading can only add refusals, never remove one. An unterminated
quote is not treated as a string.

RED on base cc26ddf: ``refuse_xql_write_stages`` did not exist and the
documented write query reached the transport on every path.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "core"

DOCUMENTED_WRITE = "dataset = xdr_data | target type=lookup append=false x"
START_PATH = "/public_api/v1/xql/start_xql_query"


def _cfg():
    from integrations.xsiam.config import XsiamTenantConfig

    return XsiamTenantConfig(
        base_url="https://api-test.xdr.us.paloaltonetworks.com",
        region="us", auth_mode="standard", api_key_id="1",
    )


class _Recorder:
    """An httpx.MockTransport that records every request it is handed."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.path.endswith("start_xql_query"):
                return httpx.Response(200, json={"reply": "q-1"})
            return httpx.Response(200, json={"reply": {
                "status": "SUCCESS", "results": {"data": []}}})
        return httpx.MockTransport(handler)


def _client(rec: _Recorder):
    from integrations.xsiam.client import XsiamClient

    return XsiamClient(_cfg(), "key", transport=rec.transport())


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", [
    DOCUMENTED_WRITE,
    "dataset = xdr_data | TARGET type=dataset stolen",
    "dataset=xdr_data|target type=lookup x",
    "dataset = xdr_data\n| target type=lookup x",
    "dataset = xdr_data |\n\ttarget type=lookup x",
    "target type=lookup x",
    "dataset = xdr_data | /* hide */ target type=lookup x",
    "dataset = xdr_data | // hide\n target type=lookup x",
    "dataset = a | join (dataset = b | target type=lookup x) as j j.k = k",
    # Ambiguous escaping: if XQL does NOT honour backslash escapes the string
    # closes at \" and the target stage is live. Fail closed.
    'dataset = a | filter f = "\\" | target type=lookup x | filter g = "',
    # An unterminated quote is not a string.
    'dataset = a | filter f = "abc | target type=lookup x',
])
def test_write_stage_is_refused(query):
    from integrations.xsiam.client import XqlWriteStageRefused, refuse_xql_write_stages
    from integrations.xsiam.exceptions import XsiamConfigError

    with pytest.raises(XqlWriteStageRefused) as exc:
        refuse_xql_write_stages(query)
    assert isinstance(exc.value, XsiamConfigError)
    assert exc.value.code == "XQL_WRITE_STAGE_REFUSED"
    assert exc.value.http_status == 400
    assert "target" in exc.value.detail


@pytest.mark.parametrize("query", [
    'dataset = xdr_data | filter action_process_image_name = "target.exe" | fields x',
    'dataset = xdr_data | filter cmd contains "| target type=lookup y" | fields cmd',
    # NB a backslash-escaped quote before `| target` is NOT here: whether XQL
    # honours \" decides whether that stage is live, so it is refused (above).
    "dataset = xdr_data | alter target_host = agent_hostname | fields target_host",
    "dataset = xdr_data | fields targets, target_ip",
    "dataset = xdr_data | filter x = 1 // a comment that mentions target",
    "dataset = xdr_data // http://example.test/target\n| fields x",
    'dataset = xdr_data | filter url = "http://x/y" | comp count() as n',
])
def test_target_inside_a_string_or_identifier_is_allowed(query):
    from integrations.xsiam.client import refuse_xql_write_stages

    assert refuse_xql_write_stages(query) == query


def test_non_string_query_is_refused():
    from integrations.xsiam.client import refuse_xql_write_stages
    from integrations.xsiam.exceptions import XsiamConfigError

    for bad in (None, 42, ["dataset = x"], b"dataset = x"):
        with pytest.raises(XsiamConfigError):
            refuse_xql_write_stages(bad)


def test_the_shipped_corpus_is_not_refused():
    """Every verification_xql and assertion query the product ships must pass —
    a guard that refuses real content would be switched off, not trusted."""
    import yaml

    from integrations.xsiam.client import XqlWriteStageRefused, refuse_xql_write_stages

    queries: list[tuple[str, str]] = []
    for path in (REPO / "scenarios").rglob("*.yml"):
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            continue
        for step in doc.get("steps") or []:
            for det in (step or {}).get("expected_detections") or []:
                q = (det or {}).get("verification_xql")
                if isinstance(q, str):
                    queries.append((str(path), q))
    for path in (REPO / "assertions").rglob("*.yml"):
        def _walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if (k.endswith("query") or k.endswith("xql")) and isinstance(v, str):
                        queries.append((str(path), v))
                    else:
                        _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)
        _walk(yaml.safe_load(path.read_text()))

    assert len(queries) > 30, f"corpus walk found only {len(queries)} queries"
    refused = []
    for path, q in queries:
        try:
            refuse_xql_write_stages(q)
        except XqlWriteStageRefused as exc:
            refused.append((path, exc.detail))
    assert refused == []


# ---------------------------------------------------------------------------
# Client: the choke point
# ---------------------------------------------------------------------------


def test_start_xql_query_refuses_with_zero_transport_calls():
    from integrations.xsiam.client import XqlWriteStageRefused

    rec = _Recorder()
    with pytest.raises(XqlWriteStageRefused):
        asyncio.run(_client(rec).start_xql_query(DOCUMENTED_WRITE, {"relativeTime": 1}))
    assert rec.requests == []


def test_run_xql_refuses_with_zero_transport_calls():
    from integrations.xsiam.client import XqlWriteStageRefused

    rec = _Recorder()
    with pytest.raises(XqlWriteStageRefused):
        asyncio.run(_client(rec).run_xql(DOCUMENTED_WRITE, {"relativeTime": 1},
                                         max_wait=0, interval=0))
    assert rec.requests == []


def test_a_read_query_still_goes_out():
    rec = _Recorder()
    q = 'dataset = xdr_data | filter f = "target" | fields f'
    qid = asyncio.run(_client(rec).start_xql_query(q, {"relativeTime": 1}))
    assert qid == "q-1"
    assert [r.url.path for r in rec.requests] == [START_PATH]


def test_generic_request_onto_the_xql_path_is_guarded():
    """``XsiamClient.request`` is the operation executor's door. A POST to the
    start path through it must be the same call as start_xql_query — not a
    second, unguarded way to put a query on the wire."""
    from integrations.xsiam.client import XqlWriteStageRefused

    rec = _Recorder()
    client = _client(rec)
    with pytest.raises(XqlWriteStageRefused):
        asyncio.run(client.request("POST", START_PATH, json={
            "request_data": {"query": DOCUMENTED_WRITE, "timeframe": {"relativeTime": 1}}}))
    assert rec.requests == []

    reply = asyncio.run(client.request("POST", START_PATH, json={
        "request_data": {"query": "dataset = xdr_data | limit 1"}}))
    assert reply == "q-1"
    assert [r.url.path for r in rec.requests] == [START_PATH]


@pytest.mark.parametrize("body", [
    None, {}, {"request_data": {}}, {"request_data": {"query": 7}},
    {"request_data": "dataset = x"}, {"query": "dataset = x"},
    {"request_data": {"query": "dataset = x"}, "smuggled": True},
])
def test_unrecognised_xql_start_body_is_refused(body):
    """An unrecognised shape must raise, not read as 'nothing to inspect'."""
    from integrations.xsiam.exceptions import XsiamConfigError

    rec = _Recorder()
    with pytest.raises(XsiamConfigError):
        asyncio.run(_client(rec).request("POST", START_PATH, json=body))
    assert rec.requests == []


@pytest.mark.parametrize("path", [START_PATH, START_PATH + "/", "public_api/v1/xql/start_xql_query",
                                  "/public_api/v1/xql/./start_xql_query"])
def test_path_spellings_cannot_dodge_the_guard(path):
    from integrations.xsiam.client import XqlWriteStageRefused

    rec = _Recorder()
    with pytest.raises(XqlWriteStageRefused):
        asyncio.run(_client(rec).request("POST", path, json={
            "request_data": {"query": DOCUMENTED_WRITE}}))
    assert rec.requests == []


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@pytest.fixture
def xsiam_app(monkeypatch):
    import os

    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    import api.xsiam as xsiam_api
    from database import get_db
    from integrations.xsiam.exceptions import XsiamError
    import integrations.xsiam.operations.catalog as catmod

    xsiam_api.op_catalog.load(os.path.join(os.path.dirname(catmod.__file__), "packs"))

    rec = _Recorder()
    loads: list[str] = []

    async def _load(session, name):
        loads.append(name)
        return _client(rec)

    monkeypatch.setattr(xsiam_api, "load_xsiam_client", _load)

    app = FastAPI()
    app.include_router(xsiam_api.router, prefix="/api")

    async def _no_db():
        yield None

    app.dependency_overrides[get_db] = _no_db

    @app.exception_handler(XsiamError)
    async def _h(request: Request, exc: XsiamError):
        return JSONResponse(status_code=exc.http_status,
                            content={"error": "XSIAM integration error",
                                     "code": exc.code, "detail": exc.detail})

    return TestClient(app), rec, loads


def test_xql_route_refuses_before_touching_the_tenant(xsiam_app):
    client, rec, loads = xsiam_app
    resp = client.post("/api/xsiam/tenants/acme/xql", json={"query": DOCUMENTED_WRITE})
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "XQL_WRITE_STAGE_REFUSED"
    assert rec.requests == []
    assert loads == [], "the credential was decrypted for a query that was refused"


def test_xql_route_still_runs_a_read_query(xsiam_app):
    client, rec, _ = xsiam_app
    resp = client.post("/api/xsiam/tenants/acme/xql",
                       json={"query": "dataset = xdr_data | limit 1"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"query_id": "q-1"}
    assert len(rec.requests) == 1


@pytest.mark.parametrize("dry_run", [None, True, False])
def test_operation_executor_refuses_the_write_stage(xsiam_app, dry_run):
    client, rec, loads = xsiam_app
    body = {"body": {"request_data": {"query": DOCUMENTED_WRITE}}}
    if dry_run is not None:
        body["dry_run"] = dry_run
    resp = client.post("/api/xsiam/tenants/acme/operations/OP-XQL-START-XQL-QUERY", json=body)
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "XQL_WRITE_STAGE_REFUSED"
    assert rec.requests == []
    assert loads == []


def test_operation_executor_still_runs_a_read_query(xsiam_app):
    client, rec, _ = xsiam_app
    resp = client.post("/api/xsiam/tenants/acme/operations/OP-XQL-START-XQL-QUERY",
                       json={"body": {"request_data": {"query": "dataset = xdr_data | limit 1"}}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["reply"] == "q-1"
    assert [r.url.path for r in rec.requests] == [START_PATH]


def test_xql_start_op_is_relabelled_body_inspected():
    import os

    from integrations.xsiam.operations.catalog import catalog
    import integrations.xsiam.operations.catalog as catmod

    catalog.load(os.path.join(os.path.dirname(catmod.__file__), "packs"))
    op = catalog.find("OP-XQL-START-XQL-QUERY")
    assert "body-inspected" in op.tags


def test_body_inspected_op_with_an_unknown_path_fails_closed():
    from integrations.xsiam.client import inspect_operation_body
    from integrations.xsiam.exceptions import XsiamConfigError

    with pytest.raises(XsiamConfigError):
        inspect_operation_body("/public_api/v1/some/new_op", {"request_data": {}})


# ---------------------------------------------------------------------------
# Verifier runners (authored content)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", ["xsiam_query_runner", "xsiam_rows_runner"])
def test_verifier_runners_refuse_without_charging_quota(factory):
    from engine import verifier
    from integrations.xsiam.client import XqlWriteStageRefused
    from integrations.xsiam.ledger import ledger

    rec = _Recorder()
    client = _client(rec)
    tenant = verifier._tenant_name(client)
    ledger.reset(tenant)
    runner = getattr(verifier, factory)(client, {"relativeTime": 1})
    with pytest.raises(XqlWriteStageRefused):
        asyncio.run(runner(DOCUMENTED_WRITE))
    assert rec.requests == []
    assert ledger.snapshot(tenant, limit=10)["queries_last_24h"] == 0
    ledger.reset(tenant)


def test_verify_results_records_pending_for_a_refused_query():
    """A refused verification_xql proves nothing either way: pending, never fail."""
    from types import SimpleNamespace

    from engine import verifier

    rec = _Recorder()
    runner = verifier.xsiam_query_runner(_client(rec), {"relativeTime": 1})
    row = SimpleNamespace(id=1, detection_id="d", verification_xql=DOCUMENTED_WRITE,
                          kpi_verdict=None, verified_at=None, expected_outcome="detect",
                          step_outcome=None, outcome=None, outcome_reason=None,
                          ttp_ref=None)
    counts = asyncio.run(verifier.verify_results([row], runner))
    assert row.kpi_verdict == "pending", counts
    assert rec.requests == []


# ---------------------------------------------------------------------------
# Drift guard: one sender
# ---------------------------------------------------------------------------


def _py_files():
    return [p for p in CORE.rglob("*.py") if "static" not in p.parts]


def test_only_the_client_names_the_xql_start_path():
    hits = []
    for path in _py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "xql/start_xql_query" in node.value:
                hits.append(str(path.relative_to(CORE)))
    assert hits == ["integrations/xsiam/client.py"], (
        f"the XQL start path is spelled outside the client: {hits}. Every query "
        f"must reach the wire through XsiamClient.start_xql_query."
    )


def test_start_xql_query_is_the_only_sender():
    """Inside the client, the only HTTP call that targets the start path is in
    start_xql_query, and it is preceded by the guard."""
    path = CORE / "integrations" / "xsiam" / "client.py"
    tree = ast.parse(path.read_text())

    senders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            if call.func.attr not in ("post", "request", "send", "stream"):
                continue
            names = {n.id for a in call.args for n in ast.walk(a) if isinstance(n, ast.Name)}
            if "XQL_START_PATH" in names:
                senders.append(fn.name)
    assert senders == ["start_xql_query"], senders

    [start] = [n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "start_xql_query"]
    body_src = ast.unparse(start)
    assert body_src.index("refuse_xql_write_stages(") < body_src.index("XQL_START_PATH"), (
        "start_xql_query must refuse a write stage BEFORE it builds the request"
    )


def test_every_query_caller_outside_the_client_goes_through_the_client():
    """No module may post to an ``/xql/`` path itself; query strings reach the
    tenant only via ``run_xql`` / ``start_xql_query``."""
    offenders = []
    for path in _py_files():
        if path == CORE / "integrations" / "xsiam" / "client.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "/xql/" in node.value and "public_api" in node.value:
                offenders.append(f"{path.relative_to(CORE)}:{node.lineno}")
    assert offenders == [], offenders

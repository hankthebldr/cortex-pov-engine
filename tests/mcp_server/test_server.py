"""The /mcp streamable-HTTP mount on SimCore's real ``main.app`` (spec test 3, ASGI half).

Each test re-imports ``main`` against a throwaway BASE_DIR (inside the
``restore_core_modules`` fixture, so the fresh modules never outlive the test)
and boots it through its real lifespan via ``TestClient``. The /mcp route is
reached in-process; its tools reach SimCore's REST routes through
``httpx.ASGITransport`` — no socket, no tenant.
"""
from __future__ import annotations

import json
import logging
import sys

import pytest

from tests._core_reimport import CORE_DIR, is_core_module, reimport_core

# Two pre-existing warnings, each ignored by exact category/message; any other
# warning is an error under -W error:
# * starlette.testclient warns at import that httpx should be httpx2;
# * core/engine/k8s_manifest.py:1235-1236 carries `\$` in a non-raw string, and a
#   fresh compile of it (main imports it via the k8s router) is a
#   DeprecationWarning on 3.11 (SyntaxWarning on 3.12+) — a SyntaxError under
#   -W error. Not this task's file.
pytestmark = [
    pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning"),
    pytest.mark.filterwarnings(r"ignore:invalid escape sequence '\\\$':DeprecationWarning"),
    # Python >= 3.12 raises the same compile warning as SyntaxWarning.
    pytest.mark.filterwarnings(r"ignore:invalid escape sequence '\\\$':SyntaxWarning"),
]

MCP_HEADERS = {"Host": "localhost", "Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "0"}}}


@pytest.fixture
def boot_main(tmp_path, monkeypatch, restore_core_modules):
    """``boot_main(http=bool)`` -> a freshly imported ``main`` module."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>console</title>")
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    def _boot(*, http: bool, allowed_hosts: str = ""):
        monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
        monkeypatch.setenv("CORTEXSIM_STATIC_DIR", str(static))
        monkeypatch.setenv("CORTEXSIM_ENV", "development")
        monkeypatch.setenv("CORTEXSIM_SECRET", "test-master-key-please-ignore-32+chars-entropy")
        monkeypatch.setenv("CORTEXSIM_MCP_HTTP", "true" if http else "false")
        monkeypatch.setenv("CORTEXSIM_MCP_ALLOWED_HOSTS", allowed_hosts)
        # Another suite (tests/scripts/test_lab_readiness.py) puts scripts/ at
        # sys.path[0] during collection, and scripts/eal_simulator/ is a package
        # of the same name as core/eal_simulator/. The fresh import below must
        # resolve core/ first; monkeypatch restores sys.path at teardown.
        monkeypatch.syspath_prepend(str(CORE_DIR))
        reimport_core("main", *[n for n, m in list(sys.modules.items()) if is_core_module(m)])
        import main  # noqa: PLC0415

        return main

    yield _boot
    # main._configure_logging() attaches a stream + file handler per import.
    for h in [h for h in root.handlers if h not in handlers_before]:
        root.removeHandler(h)
        h.close()


def _rpc(client, body, headers=MCP_HEADERS):
    return client.post("/mcp", content=json.dumps(body), headers=headers)


def _mcp_route_index(app) -> int | None:
    for i, r in enumerate(app.router.routes):
        if getattr(r, "path", None) == "/mcp":
            return i
    return None


def test_mount_off_registers_nothing_and_health_says_disabled(boot_main):
    from starlette.testclient import TestClient

    main = boot_main(http=False)
    assert not [r for r in main.app.router.routes if str(getattr(r, "path", "")).startswith("/mcp")]
    assert getattr(main.app.state, "mcp", None) is None
    assert "mcp_server.server" not in sys.modules, "flag off must not even import the MCP server"
    with TestClient(main.app) as client:
        comp = client.get("/api/health").json()["components"]["mcp"]
    assert comp["mounted"] is False
    assert comp["code"] == "MCP_DISABLED"
    assert comp["status"] == "ok"


def test_mount_on_initialize_then_tools_list_over_asgi(boot_main):
    from starlette.testclient import TestClient

    main = boot_main(http=True)
    from mcp_server.naming import CANONICAL_IDS, wire_name

    with TestClient(main.app) as client:
        init = _rpc(client, INIT)
        assert init.status_code == 200, init.text
        assert init.json()["result"]["serverInfo"]["name"] == "cortexsim"

        listed = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert listed.status_code == 200, listed.text
        names = sorted(t["name"] for t in listed.json()["result"]["tools"])
        assert names == sorted(wire_name(i) for i in CANONICAL_IDS)

        # The in-process loopback: a SimCore-local tool reaches GET /api/readiness
        # through httpx.ASGITransport(app) and answers with an evidence statement.
        called = _rpc(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": wire_name("cortexsim.readiness.get"),
                                          "arguments": {}}})
        assert called.status_code == 200, called.text
        result = called.json()["result"]
        assert result["isError"] is False, result
        assert "tenant-verified: 0" in result["content"][0]["text"]

        health = client.get("/api/health").json()["components"]["mcp"]
    assert health["mounted"] is True
    assert health["tools"] == len(CANONICAL_IDS)


def test_mcp_mount_precedes_the_static_catch_all(boot_main):
    main = boot_main(http=True)
    routes = main.app.router.routes
    mcp_idx = _mcp_route_index(main.app)
    ui_idx = next(i for i, r in enumerate(routes) if getattr(r, "name", None) == "ui")
    assert mcp_idx is not None, "no route at exactly /mcp"
    assert mcp_idx < ui_idx, "the static catch-all at / would swallow /mcp"


def test_unlisted_host_header_is_refused(boot_main):
    from starlette.testclient import TestClient

    main = boot_main(http=True, allowed_hosts="lab.example, ,")
    with TestClient(main.app) as client:
        evil = _rpc(client, INIT, headers={**MCP_HEADERS, "Host": "evil.example"})
        listed = _rpc(client, INIT, headers={**MCP_HEADERS, "Host": "lab.example:8888"})
        loop = _rpc(client, INIT, headers={**MCP_HEADERS, "Host": "127.0.0.1:8888"})
    assert evil.status_code == 421
    assert listed.status_code == 200, listed.text
    assert loop.status_code == 200, loop.text


def test_flag_on_with_the_sdk_unimportable_health_is_degraded_not_mounted(boot_main, monkeypatch):
    from starlette.testclient import TestClient

    # A None entry makes `import mcp_server.server` raise ImportError, which is
    # what a missing/broken SDK does; monkeypatch puts sys.modules back.
    monkeypatch.setitem(sys.modules, "mcp_server.server", None)
    main = boot_main(http=True)
    assert _mcp_route_index(main.app) is None
    with TestClient(main.app) as client:
        body = client.get("/api/health").json()
    comp = body["components"]["mcp"]
    assert comp["mounted"] is False
    assert comp["status"] == "degraded"
    assert comp["code"] == "MCP_SDK_IMPORT_FAILED"
    assert "mcp" in body["degraded_components"]


def test_lifespan_closes_the_in_process_client(boot_main):
    from starlette.testclient import TestClient

    main = boot_main(http=True)
    client = main.app.state.mcp_client
    with TestClient(main.app):
        assert client._http.is_closed is False
    assert client._http.is_closed is True


def test_lifespan_runs_the_session_manager_only_when_mounted(boot_main):
    from starlette.testclient import TestClient

    main = boot_main(http=True)
    sm = main.app.state.mcp.session_manager
    assert sm._has_started is False
    with TestClient(main.app):
        assert sm._has_started is True


def test_build_host_allowlist_is_loopback_plus_configured():
    from mcp_server.server import allowed_hosts_for

    hosts = allowed_hosts_for(["lab.example"])
    assert hosts == ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*",
                     "lab.example", "lab.example:*"]


@pytest.mark.parametrize("raised, expected", [
    (RuntimeError("tenant connection reset"), "queries_issued=unknown"),
    ("tenant_call_error_unreported", "queries_issued=unknown"),
    ("tenant_call_error_reported", "queries_issued=1"),
])
def test_tenant_call_log_line_never_states_an_unread_count_as_zero(raised, expected, caplog):
    import asyncio

    from mcp_server.registry import TenantCallError
    from mcp_server.server import _logged
    from mcp_server.tools import xsiam

    spec = next(s for s in xsiam.specs() if s.id == "xsiam.alert.list")
    if raised == "tenant_call_error_unreported":
        raised = TenantCallError("XSIAM_HTTP_ERROR", "boom", "upstream 502", tenant="lab")
    elif raised == "tenant_call_error_reported":
        raised = TenantCallError("XSIAM_HTTP_ERROR", "boom", "upstream 502", tenant="lab",
                                 queries_issued=1)

    async def impl(ctx, args):
        raise raised

    logged = _logged(type(spec)(**{**spec.__dict__, "impl": impl}))
    args = spec.input_model(tenant="lab")
    with caplog.at_level(logging.INFO, logger="cortexsim.mcp"):
        with pytest.raises(type(raised)):
            asyncio.run(logged.impl(None, args))
    (line,) = [r.getMessage() for r in caplog.records if r.name == "cortexsim.mcp"]
    assert "tool=xsiam.alert.list" in line and "tenant=lab" in line and expected in line
    assert "duration_ms=" in line
    assert "upstream 502" not in line, "a detail/body must never be logged"

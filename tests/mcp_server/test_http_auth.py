"""Bearer token on the /mcp streamable-HTTP mount (tenant-reader spec §7.1)."""
from __future__ import annotations

import json
import logging

import pytest

from tests.mcp_server.conftest import mcp_client

pytestmark = [
    pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning"),
]

TOKEN = "tok-" + "q" * 40
HEADERS = {"Host": "localhost", "Accept": "application/json, text/event-stream",
           "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "pytest", "version": "0"}}}


def _init(client, **headers):
    return client.post("/mcp", content=json.dumps(INIT), headers={**HEADERS, **headers})


def test_no_header_is_401_with_www_authenticate(boot_main):
    main = boot_main(http=True, token=TOKEN)
    with mcp_client(main.app) as client:
        resp = _init(client)
    assert resp.status_code == 401, resp.text
    assert resp.headers["WWW-Authenticate"] == "Bearer"
    body = resp.json()
    assert body["code"] == "MCP_UNAUTHORIZED"
    assert body["error"] and body["detail"]


def test_wrong_token_is_401(boot_main):
    main = boot_main(http=True, token=TOKEN)
    with mcp_client(main.app, token="not-the-token") as client:
        resp = _init(client)
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "MCP_UNAUTHORIZED"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_right_token_initialises(boot_main):
    main = boot_main(http=True, token=TOKEN)
    with mcp_client(main.app, token=TOKEN) as client:
        resp = _init(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["serverInfo"]["name"] == "cortexsim"


def test_bearer_scheme_is_case_insensitive(boot_main):
    main = boot_main(http=True, token=TOKEN)
    with mcp_client(main.app) as client:
        lower = _init(client, Authorization=f"bearer {TOKEN}")
        upper = _init(client, Authorization=f"BEARER {TOKEN}")
        basic = _init(client, Authorization=f"Basic {TOKEN}")
    assert lower.status_code == 200, lower.text
    assert upper.status_code == 200, upper.text
    assert basic.status_code == 401


def test_flag_on_without_token_does_not_mount_and_health_is_degraded(boot_main):
    main = boot_main(http=True)
    assert not [r for r in main.app.router.routes if getattr(r, "path", None) == "/mcp"]
    assert getattr(main.app.state, "mcp", None) is None
    with mcp_client(main.app) as client:
        body = client.get("/api/health").json()
    comp = body["components"]["mcp"]
    assert comp["status"] == "degraded"
    assert comp["code"] == "MCP_TOKEN_MISSING"
    assert comp["mounted"] is False
    assert "CORTEXSIM_MCP_TOKEN" in comp["detail"]
    assert "mcp" in body["degraded_components"]


def test_token_never_appears_in_logs(boot_main, caplog):
    main = boot_main(http=True, token=TOKEN)
    wrong = "wrong-" + "z" * 30
    with caplog.at_level(logging.DEBUG):
        with mcp_client(main.app, token=TOKEN) as client:
            assert _init(client).status_code == 200
            client.headers["Authorization"] = f"Bearer {wrong}"
            assert _init(client).status_code == 401
    text = "\n".join(r.getMessage() + " " + str(r.args) for r in caplog.records)
    assert TOKEN not in text and wrong not in text
    assert "Bearer " not in text


def test_non_ascii_token_matches_and_raw_header_bytes_are_compared(boot_main):
    token = "tök-" + "é" * 20 + "-ключ"
    main = boot_main(http=True, token=token)
    with mcp_client(main.app) as client:
        good = _init(client, Authorization=("Bearer " + token).encode("utf-8"))
        bad = _init(client, Authorization=("Bearer " + token + "x").encode("utf-8"))
    assert good.status_code == 200, good.text
    assert bad.status_code == 401


def test_unencodable_token_fails_at_mount_not_per_request():
    from types import SimpleNamespace

    from pydantic import SecretStr
    from starlette.applications import Starlette

    from mcp_server.auth import McpGate
    from mcp_server.server import mount

    async def inner(scope, receive, send):  # pragma: no cover - never reached
        raise AssertionError

    with pytest.raises(ValueError):
        McpGate(inner, token="bad-\ud800-token", loopback_only=False)
    settings = SimpleNamespace(
        CORTEXSIM_MCP_HTTP=True, CORTEXSIM_MCP_TOKEN=SecretStr("bad-\ud800-token"),
        CORTEXSIM_URL="http://simcore.test", CORTEXSIM_MCP_ALLOWED_HOSTS="",
        CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE=30, CORTEXSIM_MCP_ALLOW_HOST_ACTIONS=False)
    with pytest.raises(ValueError):
        mount(Starlette(), settings=settings)


def test_whitespace_only_token_is_missing(boot_main):
    main = boot_main(http=True, token="   ")
    assert not [r for r in main.app.router.routes if getattr(r, "path", None) == "/mcp"]
    with mcp_client(main.app) as client:
        comp = client.get("/api/health").json()["components"]["mcp"]
    assert comp["status"] == "degraded" and comp["code"] == "MCP_TOKEN_MISSING"


def test_padded_token_still_authenticates(boot_main):
    main = boot_main(http=True, token=f"  {TOKEN}\n")
    with mcp_client(main.app, token=TOKEN) as client:
        resp = _init(client)
        health = client.get("/api/health").json()["components"]["mcp"]
    assert resp.status_code == 200, resp.text
    assert health["status"] == "ok" and health["mounted"] is True


def test_non_http_scope_is_refused_by_the_gate_itself():
    import asyncio

    from mcp_server.auth import McpGate

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    gate = McpGate(inner, token=TOKEN, loopback_only=False)

    async def send(msg):
        pass

    async def receive():
        return {"type": "websocket.connect"}

    asyncio.run(gate({"type": "websocket", "headers": []}, receive, send))
    assert reached == []

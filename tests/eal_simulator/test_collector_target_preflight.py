"""Guards for the per-target preflight ladder.

configured -> reachable (no HTTP request at all) -> accepted (one canary POST,
only when the caller opts in). Every transport is injected; nothing here
touches a network.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from eal_simulator.target_preflight import preflight_target
from eal_simulator.targets import TargetSpec

KEY = "collector-key-must-not-leak-9f2a"


def _spec(kind="xsiam_http_collector", has_token=True):
    return TargetSpec(
        name="okta-hec", kind=kind, url="https://api-acme.example.test/logs/v1/event",
        dataset="okta_sso_raw", auth_header="Authorization", auth_scheme="",
        plugins=("idp_signin_emulator",), is_default=False, has_token=has_token,
    )


class _Recorder:
    def __init__(self, status=202, body=b"", content_type="application/json"):
        self.requests: list[httpx.Request] = []
        self.status, self.body, self.ctype = status, body, content_type

    def factory(self, timeout, verify_tls):
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.status, content=self.body,
                                  headers={"content-type": self.ctype})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _ok_connect(host, port, use_tls, timeout):
    return None


def _run(**kw):
    return asyncio.run(preflight_target(**kw))


def test_missing_key_is_not_configured_and_touches_nothing():
    calls = []

    async def connect(*a):
        calls.append(a)

    rec = _Recorder()
    out = _run(spec=_spec(has_token=False), token=None, write_canary=True,
               connect=connect, client_factory=rec.factory)
    assert out["verdict"] == "not_configured"
    assert out["code"] == "COLLECTOR_KEY_MISSING"
    assert calls == [] and rec.requests == []
    assert out["records_written"] == 0


def test_connect_failure_is_unreachable_and_never_posts():
    async def connect(*a):
        raise ConnectionRefusedError("[Errno 61] Connection refused")

    rec = _Recorder()
    out = _run(spec=_spec(), token=KEY, write_canary=True,
               connect=connect, client_factory=rec.factory)
    assert out["verdict"] == "unreachable"
    assert out["code"] == "collector_unreachable"
    assert rec.requests == []


def test_without_opt_in_the_ladder_stops_at_reachable_and_writes_nothing():
    rec = _Recorder()
    out = _run(spec=_spec(), token=KEY, write_canary=False,
               connect=_ok_connect, client_factory=rec.factory)
    assert out["verdict"] == "reachable"
    assert rec.requests == []
    assert out["records_written"] == 0
    accepted = next(r for r in out["rungs"] if r["rung"] == "accepted")
    assert accepted["status"] == "not_checked"


def test_401_is_refused_and_the_key_went_bare():
    rec = _Recorder(status=401)
    out = _run(spec=_spec(), token=KEY, write_canary=True,
               connect=_ok_connect, client_factory=rec.factory)
    assert out["verdict"] == "refused"
    assert out["code"] == "collector_unauthorized"
    assert out["status_code"] == 401
    assert rec.requests[0].headers["authorization"] == KEY


def test_2xx_is_accepted_and_one_record_was_written():
    rec = _Recorder(status=202)
    out = _run(spec=_spec(), token=KEY, write_canary=True,
               connect=_ok_connect, client_factory=rec.factory)
    assert out["verdict"] == "accepted"
    assert out["records_written"] == 1
    assert out["tenant_verified"] == 0
    body = json.loads(rec.requests[0].content)
    assert body["cortexsim_preflight"] is True


def test_captive_portal_200_is_refused_not_accepted():
    rec = _Recorder(status=200, body=b"<html><form>login</form></html>",
                    content_type="text/html")
    out = _run(spec=_spec(), token=KEY, write_canary=True,
               connect=_ok_connect, client_factory=rec.factory)
    assert out["verdict"] == "refused"
    assert out["code"] == "collector_intercepted"


@pytest.mark.parametrize("status", [202, 401])
def test_the_key_never_appears_in_the_result(status):
    rec = _Recorder(status=status)
    out = _run(spec=_spec(), token=KEY, write_canary=True,
               connect=_ok_connect, client_factory=rec.factory)
    assert KEY not in json.dumps(out)


def test_a_keyless_forwarder_may_proceed():
    rec = _Recorder(status=200)
    out = _run(spec=_spec(kind="http_forwarder", has_token=False), token=None,
               write_canary=True, connect=_ok_connect, client_factory=rec.factory)
    assert out["verdict"] == "accepted"
    assert "authorization" not in rec.requests[0].headers


def test_send_test_record_gzips_for_a_gzip_target():
    import dataclasses
    import gzip

    rec = _Recorder(status=200, body=b'{"error":"false"}')
    spec = dataclasses.replace(_spec(), compress=True)
    out = _run(spec=spec, token=KEY, write_canary=True,
               connect=_ok_connect, client_factory=rec.factory)
    assert out["verdict"] == "accepted"
    sent = rec.requests[0]
    assert sent.headers["content-encoding"] == "gzip"
    assert json.loads(gzip.decompress(sent.content))["cortexsim_preflight"] is True

"""The reconcile fetcher never follows a redirect with the tenant API key.

``normalize_tenant_base_url`` pins the FIRST hop to ``*.paloaltonetworks.com``
so the raw API key in ``Authorization`` (standard auth mode) only ever goes to
a Cortex tenant. urllib's default opener defeated that pin: a 301/302/303
answer to the POST — from an intercepting egress proxy, a captive portal, or
anything sitting in front of the tenant — was re-issued as a GET to
``Location`` carrying every header, API key included. The tenant client
(httpx, ``follow_redirects`` off) never had this problem; the stdlib path did.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from connectors.base import ConnectorConfig, default_http_fetcher
from connectors.xsiam import XsiamConnector


class _Server:
    def __init__(self, status, headers=None, body=b""):
        self.status, self.headers, self.body = status, dict(headers or {}), body
        self.received: list[dict] = []

    def __enter__(self):
        server = self

        class H(BaseHTTPRequestHandler):
            def _answer(self):
                n = int(self.headers.get("content-length") or 0)
                if n:
                    self.rfile.read(n)
                server.received.append({"method": self.command,
                                        "headers": {k.lower(): v for k, v in self.headers.items()}})
                self.send_response(server.status)
                for k, v in server.headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(server.body)))
                self.end_headers()
                self.wfile.write(server.body)

            do_GET = do_POST = _answer  # noqa: N815

            def log_message(self, *a):
                return

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        self._t.join(timeout=5)

    @property
    def url(self):
        host, port = self._srv.server_address[:2]
        return f"http://{host}:{port}/public_api/v1/alerts/get_alerts_multi_events"


_KEY = "raw-api-key-must-not-leave"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_returned_not_followed(status):
    ok_body = json.dumps({"reply": {"total_count": 0, "alerts": []}}).encode()
    with _Server(200, {"content-type": "application/json"}, ok_body) as elsewhere:
        with _Server(status, {"location": elsewhere.url}) as tenant:
            code, _text = default_http_fetcher(
                "POST", tenant.url,
                {"Authorization": _KEY, "x-xdr-auth-id": "1",
                 "Content-Type": "application/json"},
                b'{"request_data": {}}', 5.0)

    assert elsewhere.received == [], (
        f"HTTP {status} was followed; the Location host received "
        f"{[(r['method'], r['headers'].get('authorization')) for r in elsewhere.received]}"
    )
    assert code == status


def test_a_redirected_pull_fails_instead_of_reading_the_location_reply():
    ok_body = json.dumps({"reply": {"total_count": 0, "alerts": []}}).encode()
    with _Server(200, {"content-type": "application/json"}, ok_body) as elsewhere:
        with _Server(302, {"location": elsewhere.url}) as tenant:
            def fetcher(method, url, headers, body, timeout):
                # The connector pins the first hop to *.paloaltonetworks.com;
                # stand a local server in for that hop and use the REAL fetcher.
                return default_http_fetcher(method, tenant.url, headers, body, timeout)

            cfg = ConnectorConfig(integration_name="t", secret=_KEY,
                                  config={"fqdn": "api-t.xdr.us.paloaltonetworks.com",
                                          "api_key_id": "1"})
            pull = XsiamConnector(fetcher=fetcher).pull(
                cfg, since=datetime.utcnow() - timedelta(hours=1), until=datetime.utcnow())

    assert elsewhere.received == []
    assert pull.ok is False
    assert pull.code == "XSIAM_API_ERROR"

"""F-04 + PART 2 — one URL validator, one signer, proven identical.

The reconcile connector and the tenant client send the SAME API key to the SAME
tenant. Before this pass only one of them validated where it was sending it, and
they carried byte-identical copies of the advanced-auth signer. Both are single
definitions now, and these tests are the guard that keeps them that way.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest


def _cfg(url):
    return {"fqdn": url, "api_key_id": "1"}


def _capture(box):
    def fetcher(method, url, headers, body, timeout):
        box.append({"url": url, "headers": dict(headers)})
        return 200, json.dumps({"reply": {"alerts": []}})
    return fetcher


# ── F-04: the reconcile connector would ship the key over plaintext HTTP ───


@pytest.mark.parametrize("bad", [
    "http://10.0.0.5:8080",
    "http://api-t.xdr.us.paloaltonetworks.com",
    "https://evil.example.com",
    "https://user:pass@api-t.xdr.us.paloaltonetworks.com",
    "10.0.0.5:8080",
    "ftp://api-t.xdr.us.paloaltonetworks.com",
    "https://api-t.xdr.us.paloaltonetworks.com.evil.tld",
])
def test_the_connector_refuses_to_send_the_api_key_to_a_non_tenant_host(bad):
    """Pre-fix ``_base_url`` accepted anything and prepended https:// only when
    no scheme was present — so ``http://10.0.0.5:8080`` was used verbatim and the
    next reconcile POSTed ``Authorization: <raw api key>`` in cleartext."""
    from connectors.base import ConnectorConfig
    from connectors.xsiam import XsiamConnector

    sent: list = []
    pull = XsiamConnector(fetcher=_capture(sent)).pull(
        ConnectorConfig("t", _cfg(bad), "SUPER-SECRET-KEY"),
        since=datetime(2026, 1, 1), until=datetime(2026, 1, 2))

    assert pull.ok is False
    assert pull.code == "XSIAM_CONFIG_ERROR"
    assert sent == [], "no request may be made at all — the key must not leave"


@pytest.mark.parametrize("good", [
    "api-t.xdr.us.paloaltonetworks.com",
    "https://api-t.xdr.us.paloaltonetworks.com",
    "https://api-acme.xsiam.eu.paloaltonetworks.com/",
])
def test_real_tenant_urls_still_work(good):
    from connectors.base import ConnectorConfig
    from connectors.xsiam import XsiamConnector

    sent: list = []
    pull = XsiamConnector(fetcher=_capture(sent)).pull(
        ConnectorConfig("t", _cfg(good), "k"),
        since=datetime(2026, 1, 1), until=datetime(2026, 1, 2))
    assert pull.ok and sent[0]["url"].startswith("https://api-")


def test_both_stacks_reject_the_same_urls():
    """The two validators drifted once — one was strict and one did not exist.
    Assert they agree rather than trusting that they were both updated."""
    from integrations.xsiam.config import XsiamTenantConfig
    from integrations.xsiam.tenant_url import is_valid_tenant_base_url

    for bad in ("http://evil", "https://evil.example.com", "not-a-url"):
        assert not is_valid_tenant_base_url(bad)
        with pytest.raises(Exception):
            XsiamTenantConfig(base_url=bad, region="us", api_key_id="1")

    good = "https://api-acme.xdr.us.paloaltonetworks.com"
    assert is_valid_tenant_base_url(good)
    assert XsiamTenantConfig(base_url=good, region="us", api_key_id="1").base_url == good


def test_exactly_one_tenant_url_regex_exists():
    """PART 7 item 6."""
    import pathlib

    core = pathlib.Path(__file__).resolve().parents[2] / "core"
    hits = [str(p) for p in core.rglob("*.py")
            if "__pycache__" not in str(p) and "paloaltonetworks\\.com" in p.read_text()]
    assert hits == [str(core / "integrations" / "xsiam" / "tenant_url.py")], hits


# ── PART 2: one signer ─────────────────────────────────────────────────────


def test_both_stacks_produce_byte_identical_advanced_auth_headers(monkeypatch):
    """The drift guard for de-duplicating the signer.

    The nonce is random per call, so pin the timestamp and compare the digest of
    the SAME (key, nonce, timestamp) triple by construction: both paths must be
    the same function object, which is a stronger statement than "the outputs
    matched once".
    """
    import connectors.xsiam as conn_mod
    from integrations.xsiam import auth

    monkeypatch.setenv("CORTEXSIM_XSIAM_TS_MS", "1717200000000")

    assert conn_mod.advanced_auth_headers is auth.advanced_auth_headers
    assert conn_mod.standard_auth_headers is auth.standard_auth_headers

    h = auth.advanced_auth_headers("key", "1")
    assert h["x-xdr-timestamp"] == "1717200000000"
    assert len(h["Authorization"]) == 64 and h["Authorization"] != "key"


def test_advanced_mode_never_puts_the_raw_key_on_the_wire():
    """The property that actually matters, asserted on the real request."""
    from connectors.base import ConnectorConfig
    from connectors.xsiam import XsiamConnector

    sent: list = []
    XsiamConnector(fetcher=_capture(sent)).pull(
        ConnectorConfig("t", {**_cfg("api-t.xdr.us.paloaltonetworks.com"),
                              "auth_mode": "advanced"},
                        "SUPER-SECRET-KEY"),
        since=datetime(2026, 1, 1), until=datetime(2026, 1, 2))
    blob = json.dumps(sent[0])
    assert "SUPER-SECRET-KEY" not in blob
    assert len(sent[0]["headers"]["Authorization"]) == 64


def test_exactly_one_advanced_auth_signer_exists():
    import pathlib

    core = pathlib.Path(__file__).resolve().parents[2] / "core"
    hits = [str(p) for p in core.rglob("*.py")
            if "__pycache__" not in str(p) and "x-xdr-nonce" in p.read_text()]
    assert hits == [str(core / "integrations" / "xsiam" / "auth.py")], hits


# ── F-08: the tenant's response body must not leave the process ────────────


def test_a_tenant_error_body_is_not_echoed_into_the_pull_result():
    """``PullResult.detail`` is carried into ReconcileError -> the 502 body AND
    persisted to IntegrationCredential.last_verified_error, which the console
    and any POV export both read. An upstream 4xx body is unbounded and
    attacker-influenceable."""
    from connectors.base import ConnectorConfig
    from connectors.xsiam import XsiamConnector

    leak = "SESSION=abcd1234; internal-host=vault.corp.local; <script>alert(1)</script>"

    def fetcher(method, url, headers, body, timeout):
        return 403, leak

    pull = XsiamConnector(fetcher=fetcher).pull(
        ConnectorConfig("t", _cfg("api-t.xdr.us.paloaltonetworks.com"), "k"),
        since=datetime(2026, 1, 1), until=datetime(2026, 1, 2))

    blob = json.dumps(pull.to_dict())
    assert leak not in blob and "vault.corp.local" not in blob
    assert pull.detail["status"] == 403
    assert len(pull.detail["body_sha256"]) == 16
    assert pull.code == "XSIAM_API_ERROR"
    assert pull.to_dict()["remediation"], "a code with no remediation is a riddle"


def test_a_transport_error_reports_the_host_not_the_full_url():
    from connectors.base import ConnectorError, ConnectorConfig
    from connectors.xsiam import XsiamConnector

    def fetcher(method, url, headers, body, timeout):
        raise ConnectorError("transport error reaching https://api-t.xdr.us."
                             "paloaltonetworks.com: [Errno -2] Name or service not known")

    pull = XsiamConnector(fetcher=fetcher).pull(
        ConnectorConfig("t", _cfg("api-t.xdr.us.paloaltonetworks.com"), "k"),
        since=datetime(2026, 1, 1), until=datetime(2026, 1, 2))
    assert pull.ok is False
    assert pull.code == "XSIAM_TRANSPORT_ERROR"
    assert "/public_api/" not in (pull.error or "")


def test_one_error_vocabulary_serves_both_carriers():
    """PART 2: PullResult.code and XsiamError.code draw from the same set."""
    from integrations.xsiam import codes
    from integrations.xsiam.exceptions import (
        XsiamApiError, XsiamAuthError, XsiamConfigError, XsiamQueryError, XsiamQuotaError,
    )

    for exc in (XsiamAuthError, XsiamApiError, XsiamConfigError,
                XsiamQueryError, XsiamQuotaError):
        assert exc.code in codes.ALL_CODES, exc
        assert codes.remediation_for(exc.code), f"{exc.code} has no remediation"

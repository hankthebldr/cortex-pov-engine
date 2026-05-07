"""App factory + health probe + docs route tests."""
from __future__ import annotations

import pytest

from cortex_vulnerable_llm import OWASP_VULNERABILITIES
from cortex_vulnerable_llm.owasp import normalise_vuln_codes


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_readyz_and_version(client):
    assert client.get("/readyz").get_json()["status"] == "ready"
    v = client.get("/version").get_json()
    assert v["name"] == "cortex-vulnerable-llm"
    assert "." in v["version"]


def test_root_lists_all_mounted_when_vulns_all(client):
    body = client.get("/").get_json()
    assert body["mounted_vulns"] == OWASP_VULNERABILITIES
    assert "/healthz" in body["endpoints"]


def test_partial_mount(client_factory):
    c = client_factory(vulns=["LLM01", "LLM07"])
    body = c.get("/").get_json()
    assert body["mounted_vulns"] == ["LLM01", "LLM07"]
    # LLM02 endpoint should 404 when not mounted.
    resp = c.post("/owasp/llm02/chat", json={"prompt": "anything"})
    assert resp.status_code == 404


def test_unknown_owasp_code_rejected():
    with pytest.raises(ValueError, match="Unknown OWASP code"):
        normalise_vuln_codes(["LLM99"])


def test_normalise_accepts_lower_and_short_form():
    assert normalise_vuln_codes("llm01,llm07") == ["LLM01", "LLM07"]
    assert normalise_vuln_codes(None) == OWASP_VULNERABILITIES
    assert normalise_vuln_codes("all") == OWASP_VULNERABILITIES


def test_docs_index(client):
    body = client.get("/docs").get_json()
    codes = [d["code"] for d in body["docs"]]
    assert codes == OWASP_VULNERABILITIES


def test_docs_render_markdown(client):
    resp = client.get("/docs/llm01")
    assert resp.status_code == 200
    assert resp.mimetype == "text/markdown"
    assert b"LLM01" in resp.data
    assert b"Prompt Injection" in resp.data


def test_docs_unknown_code_404(client):
    resp = client.get("/docs/llm99")
    assert resp.status_code == 404

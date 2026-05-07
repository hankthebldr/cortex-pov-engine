"""LLM05 — Improper Output Handling."""

import pytest


def test_safe_text_passthrough(client):
    body = client.post(
        "/owasp/llm05/render", json={"prompt": "Hello, world!"},
    ).get_json()
    assert body["vulnerable"] is False
    assert "Hello, world!" in body["text"]


@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    "<iframe src=//evil.invalid></iframe>",
])
def test_html_injection_flagged(client, payload):
    body = client.post(
        "/owasp/llm05/render", json={"prompt": payload},
    ).get_json()
    assert body["vulnerable"] is True
    assert body["rule_name"] == "unescaped_html_output"
    assert payload in body["text"]

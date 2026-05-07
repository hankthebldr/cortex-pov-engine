"""LLM10 — Unbounded Consumption."""


def test_default_max_tokens_safe(client):
    body = client.post(
        "/owasp/llm10/chat", json={"prompt": "summarise"},
    ).get_json()
    assert body["vulnerable"] is False
    assert body["rule_name"] == "bounded"
    assert body["token_count"] > 0


def test_unbounded_max_tokens_flagged(client):
    body = client.post(
        "/owasp/llm10/chat", json={"prompt": "long output", "max_tokens": 50000},
    ).get_json()
    assert body["vulnerable"] is True
    assert body["rule_name"] == "unbounded_max_tokens"
    assert body["token_count"] >= 8000


def test_max_tokens_zero_rejected(client):
    resp = client.post(
        "/owasp/llm10/chat", json={"prompt": "x", "max_tokens": 0},
    )
    assert resp.status_code == 400


def test_hard_ceiling_caps_response(client):
    body = client.post(
        "/owasp/llm10/chat", json={"prompt": "x", "max_tokens": 10_000_000},
    ).get_json()
    assert body["hard_ceiling_applied"] is True
    # Generated text never exceeds 1 MiB
    assert len(body["text"]) <= 1_048_576


def test_invalid_max_tokens_falls_back_to_default(client):
    body = client.post(
        "/owasp/llm10/chat", json={"prompt": "x", "max_tokens": "not-a-number"},
    ).get_json()
    assert body["requested_max_tokens"] == 256
    assert body["vulnerable"] is False

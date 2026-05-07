"""LLM03 — Supply Chain."""


def test_manifest_includes_typosquat_publisher(client):
    body = client.get("/owasp/llm03/plugins").get_json()
    publishers = {p["publisher"] for p in body["plugins"]}
    assert "anthroopic-tools" in publishers  # typosquat
    assert "anthropic-official" in publishers


def test_install_typosquat_marks_vulnerable(client):
    body = client.post(
        "/owasp/llm03/install", json={"plugin": "calculator-pro"},
    ).get_json()
    assert body["vulnerable"] is True
    assert body["rule_name"] == "typosquat_publisher"


def test_install_verified_safe(client):
    body = client.post(
        "/owasp/llm03/install", json={"plugin": "calculator"},
    ).get_json()
    assert body["vulnerable"] is False


def test_install_unknown_404(client):
    resp = client.post("/owasp/llm03/install", json={"plugin": "does-not-exist"})
    assert resp.status_code == 404

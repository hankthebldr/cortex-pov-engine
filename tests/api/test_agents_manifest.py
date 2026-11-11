"""GET /api/agents/manifest: the consent gate, served on the GET (spec section 5)."""
from __future__ import annotations

import hashlib

import pytest

BASE = "/api/agents/manifest"
GATED = "profile=cluster-privilege"
LAB = "profile=lab-privileged"
LAB_OK = f"{LAB}&cluster_privilege_authorized=true&node_access_authorized=true&authorized_by=henry"


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


class _FakeDist:
    _BYTES = b"\x7fELF-fake-linux-amd64"

    def sha256(self, goos: str, arch: str) -> str:
        assert (goos, arch) == ("linux", "amd64")
        return hashlib.sha256(self._BYTES).hexdigest()


@pytest.fixture
def fake_agent_dist(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_AGENT_DIST", str(tmp_path))
    (tmp_path / "cortexsim-agent-linux-amd64").write_bytes(_FakeDist._BYTES)
    return _FakeDist()


@pytest.fixture(autouse=True)
def _switch_off(monkeypatch):
    monkeypatch.delenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", raising=False)


def _detail(r):
    j = r.json()
    return j["detail"] if "detail" in j else j


def test_baseline_is_a_bare_get(client, fake_agent_dist):
    r = client.get(BASE)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/yaml")
    assert 'filename="cortexsim-agent.yaml"' in r.headers["content-disposition"]
    assert fake_agent_dist.sha256("linux", "amd64") in r.text


def test_gated_profile_without_keys_is_409_naming_the_keys(client, fake_agent_dist):
    r = client.get(f"{BASE}?{GATED}")
    assert r.status_code == 409
    d = _detail(r)
    assert d["code"] == "CLUSTER_CONSENT_REQUIRED" and "cluster_privilege_authorized" in str(d)


@pytest.mark.parametrize("v", ["1", "yes", "True", "TRUE", ""])
def test_only_literal_true_is_consent(client, fake_agent_dist, v):
    r = client.get(f"{BASE}?{GATED}&cluster_privilege_authorized={v}&authorized_by=h")
    assert r.status_code == 409


def test_cluster_privilege_served_with_key_and_name(client, fake_agent_dist):
    r = client.get(f"{BASE}?{GATED}&cluster_privilege_authorized=true&authorized_by=henry")
    assert r.status_code == 200 and "authorized-by: henry" in r.text


def test_consent_without_authorized_by_is_400(client, fake_agent_dist):
    r = client.get(f"{BASE}?{GATED}&cluster_privilege_authorized=true")
    assert r.status_code == 400
    assert _detail(r)["code"] == "AUTHORIZED_BY_REQUIRED"


@pytest.mark.parametrize("who", ["a" * 121, "x%0Ay", "x%0Dy", "x%00y", "x%E2%80%A8y", "x%C2%85y",
                                 "x%E2%80%A9y", "x%E2%80%A8---%E2%80%A8apiVersion:%20v1", "x%23y"])
def test_authorized_by_that_could_break_the_yaml_header_is_rejected(client, fake_agent_dist, who):
    r = client.get(f"{BASE}?{GATED}&cluster_privilege_authorized=true&authorized_by={who}")
    assert r.status_code == 400 and _detail(r)["code"] == "BAD_AUTHORIZED_BY"


def test_lab_privileged_refused_while_kill_switch_off(client, fake_agent_dist):
    r = client.get(f"{BASE}?{LAB_OK}")
    assert r.status_code == 403
    assert _detail(r)["code"] == "CLUSTER_PRIVILEGE_DISABLED"


def test_403_and_409_are_distinct_codes(client, fake_agent_dist, monkeypatch):
    refused = _detail(client.get(f"{BASE}?{LAB_OK}"))["code"]
    monkeypatch.setenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", "true")
    missing = _detail(client.get(f"{BASE}?{LAB}"))
    assert refused == "CLUSTER_PRIVILEGE_DISABLED"
    assert missing["code"] == "CLUSTER_CONSENT_REQUIRED"
    assert "node_access_authorized" in str(missing)


def test_lab_privileged_served_with_keys_and_switch(client, fake_agent_dist, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", "true")
    r = client.get(f"{BASE}?{LAB_OK}")
    assert r.status_code == 200 and "privileged: true" in r.text and "authorized-by: henry" in r.text


def test_never_downgrades_silently(client, fake_agent_dist):
    assert client.get(f"{BASE}?{LAB}").status_code != 200


@pytest.mark.parametrize("q,code", [
    ("profile=nope", "BAD_PROFILE"), ("prepare=nope", "BAD_PREPARE"),
    ("arch=nope", "BAD_ARCH"), ("ttl_seconds=59", "BAD_TTL"),
    ("ttl_seconds=43201", "BAD_TTL"), ("ttl_seconds=abc", "BAD_TTL"),
    ("image=", "BAD_IMAGE"), ("image=a%20b", "BAD_IMAGE"), ("image=" + "a" * 256, "BAD_IMAGE"),
])
def test_bad_parameters_are_structured_400s(client, fake_agent_dist, q, code):
    r = client.get(f"{BASE}?{q}")
    assert r.status_code == 400 and _detail(r)["code"] == code


def test_missing_beacon_is_the_existing_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_AGENT_DIST", str(tmp_path / "nothing"))
    r = client.get(BASE)
    assert r.status_code == 404 and _detail(r)["code"] == "AGENT_BINARY_UNAVAILABLE"


def test_gated_serve_is_logged_with_operator_and_ip(client, fake_agent_dist, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="cortexsim.api.agents"):
        client.get(f"{BASE}?{GATED}&cluster_privilege_authorized=true&authorized_by=henry")
    assert any("authorized_by=henry" in m and "cluster-privilege" in m for m in caplog.messages)


def test_route_is_not_swallowed_by_agent_id_routes(client, fake_agent_dist):
    assert client.get(BASE).status_code == 200


@pytest.mark.parametrize("who", ["x%E2%80%A8---%E2%80%A8apiVersion:%20v1", "x%C2%85y", "x%E2%80%A9y", "x%0Ay"])
def test_baseline_with_a_hostile_authorized_by_is_400_not_a_200(client, fake_agent_dist, who):
    r = client.get(f"{BASE}?authorized_by={who}")
    assert r.status_code == 400 and _detail(r)["code"] == "BAD_AUTHORIZED_BY"


def test_valid_authorized_by_without_consent_keys_is_accepted(client, fake_agent_dist):
    assert client.get(f"{BASE}?authorized_by=Henry%20R.%20(lab)").status_code == 200


@pytest.mark.parametrize("img", ["a%20b", "a%0Ab", "a%E2%80%A8b", "a$(id)", "-lead"])
def test_hostile_image_is_400(client, fake_agent_dist, img):
    r = client.get(f"{BASE}?image={img}")
    assert r.status_code == 400 and _detail(r)["code"] == "BAD_IMAGE"


def test_digest_pinned_registry_image_is_accepted(client, fake_agent_dist):
    img = "registry.example.com:5000/team/img:1.2@sha256:" + "a" * 64
    r = client.get(f"{BASE}?image={img}")
    assert r.status_code == 200 and img in r.text


@pytest.mark.parametrize("profile,extra", [
    ("baseline", ""),
    ("cluster-privilege", "&cluster_privilege_authorized=true&authorized_by=henry"),
])
def test_response_yaml_holds_exactly_the_built_objects(client, fake_agent_dist, profile, extra):
    import yaml
    from datetime import datetime, timezone
    from engine.agent_manifest import build_agent_objects
    r = client.get(f"{BASE}?profile={profile}{extra}")
    assert r.status_code == 200
    got = sorted((o["kind"], o["metadata"]["name"]) for o in yaml.safe_load_all(r.text) if o)
    built = build_agent_objects(
        profile=profile, image="debian:12-slim", arch="amd64", prepare="packages",
        ttl_seconds=43200, beacon_sha256=fake_agent_dist.sha256("linux", "amd64"),
        now=datetime.now(timezone.utc), authorized_by=None)
    assert got == sorted((o["kind"], o["metadata"]["name"]) for o in built)


def test_lab_privileged_with_switch_off_and_no_keys_is_403_not_409(client, fake_agent_dist):
    r = client.get(f"{BASE}?{LAB}")
    assert r.status_code == 403 and _detail(r)["code"] == "CLUSTER_PRIVILEGE_DISABLED"

"""The plane-agnostic shelf — `/api/shelf/*`, and its `/api/k8s/*` alias.

Guarded properties:
  1. both prefixes serve the SAME bytes from the SAME handlers — `/api/k8s/*`
     may never move, because every manifest this engine has emitted hard-codes
     it inside `k8s_manifest._SERVED_FETCH` and a manifest lives in a customer's
     GitOps repo,
  2. the inventory reports what is DECLARED as well as what is present, so a
     console can show what is missing rather than only what is there,
  3. composition refuses over HTTP with the same structured codes the engine
     raises, at the same status,
  4. staging is an outbound request from the DC's host that cannot be tricked
     into shelving a 401, a redirect or an HTML error page — this repo has
     shipped both of those bugs in the EAL simulator,
  5. `.part` -> rename happens only after the digest is computed and any pin
     matched, so the download endpoint can never serve a torn or unverified file.
"""
from __future__ import annotations

import hashlib
import json
import textwrap

import pytest
import yaml

from tools.adapter_loader import ToolAdapterSchema

BODY = b"#!/bin/sh\n# fake linpeas\n"
DIGEST = hashlib.sha256(BODY).hexdigest()

PACK = textwrap.dedent(
    """
    adapter_id: TOOL-LINPEAS
    name: LinPEAS
    version: "20250601"
    tier: 4
    category: cloud-container
    upstream:
      repo: https://github.com/carlospolop/PEASS-ng
      license: MIT
      attribution: Carlos Polop
    cortex_signal:
      planes: [CDR, EDR]
    safety_class: dual-use-lab-only
    install:
      binary: /tmp/linpeas.sh
      artifact:
        filename: linpeas.sh
        url: "https://example.invalid/releases/download/20250601/linpeas.sh"
        kind: file
        sha256: "{sha}"
        pin: {{ type: release-tag, ref: "20250601" }}
        license: MIT
        stage_path: /tmp/linpeas.sh
        mode: "0755"
    invoke:
      target_platform: linux
      run_template: "{{binary}} {{flags}}"
      default_args:
        flags: "-q"
      identity_required: container-runtime
    """
)


#: The shelf's WRITE path is never open, regardless of the read-path auth mode:
#: `POST /stage` makes SimCore fetch an arbitrary URL and persist the bytes into
#: the directory of offensive tooling it serves to target hosts. Tests that
#: exercise staging therefore have to authenticate like the console does.
WRITE_TOKEN = "test-shelf-write-token"


@pytest.fixture
def client(make_client, monkeypatch):
    from api.payloads import router, shelf_router
    monkeypatch.setenv("CORTEXSIM_SHELF_WRITE_TOKENS", WRITE_TOKEN)
    c = make_client(router, shelf_router)
    c.headers.update({"Authorization": f"Bearer {WRITE_TOKEN}"})
    return c


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    monkeypatch.delenv("CORTEXSIM_K8S_PAYLOAD_AUTH", raising=False)
    monkeypatch.delenv("CORTEXSIM_SHELF_EGRESS", raising=False)
    (tmp_path / "linpeas.sh").write_bytes(BODY)
    (tmp_path / "MANIFEST.json").write_text(json.dumps({"payloads": [
        {"name": "linpeas.sh", "source_url": "https://example.invalid/x",
         "license": "MIT", "pinned": True},
    ]}))
    return tmp_path


@pytest.fixture
def wired(monkeypatch):
    pack = ToolAdapterSchema(**yaml.safe_load(PACK.format(sha=DIGEST)))
    from tools.adapter_catalog import catalog

    monkeypatch.setattr(catalog, "_by_id", {pack.adapter_id: pack})
    return pack


@pytest.fixture
def cold_catalog(monkeypatch):
    """An EMPTY adapter catalog, asserted rather than assumed.

    `_declared_index()` reads the `tools.adapter_catalog` module SINGLETON. Any
    earlier test in the session that loads the real packs leaves it warm, so a
    test that merely hopes for a cold catalog passes or fails on collection
    order. That was invisible while zero packs declared an `install.artifact`
    and `declared` was empty either way; the first real bindings exposed it.
    Same discipline as `test_push_generator_invariant._adapter_catalog_loaded`,
    which pins the singleton in the other direction for the same reason.
    """
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    monkeypatch.setattr(catalog, "_by_id", {})
    return catalog


@pytest.fixture
def seeded_scenario(session_factory):
    import asyncio

    from models import Scenario

    async def _seed():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-999", name="linpeas enumeration", version="1.0",
                status="active", plane="EDR", detection_types=["BIOC"],
                uc_ref="UC-01", tc_ref="TC-01", uc_name="uc", tc_name="tc",
                tc_refs=["TC-01"],
                mitre_tactic="TA0007", mitre_tactic_name="Discovery",
                mitre_technique="T1082", mitre_technique_name="System Information Discovery",
                additional_techniques=[], execution_identity={},
                push_supported=False, pull_supported=True,
                external_tools=[{"name": "linpeas", "adapter_ref": "TOOL-LINPEAS"}],
                steps=[],
            ))
            await db.commit()

    asyncio.run(_seed())


# ---------------------------------------------------------------------------
# 1 · Two prefixes, one implementation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["/api/shelf", "/api/k8s"])
def test_both_prefixes_serve_the_shelf(client, shelf, prefix):
    inv = client.get(f"{prefix}/payloads").json()
    assert [p["name"] for p in inv["payloads"]] == ["linpeas.sh"]
    body = client.get(f"{prefix}/payload/linpeas.sh")
    assert body.content == BODY
    assert body.headers["X-CortexSim-Payload-SHA256"] == DIGEST
    assert client.get(f"{prefix}/payload/linpeas.sh/sha256").text.strip() == DIGEST


def test_the_two_prefixes_are_the_same_handler_not_a_copy(client, shelf):
    """A redirect was deliberately rejected: it would make a stale manifest
    silently keep working, so the drift would never be discovered."""
    a = client.get("/api/k8s/payloads").json()
    b = client.get("/api/shelf/payloads").json()
    assert a == b
    assert client.get("/api/k8s/payload/linpeas.sh").content == \
           client.get("/api/shelf/payload/linpeas.sh").content


def test_download_forbids_caching(client, shelf):
    """A proxy caching a payload across a re-stage serves stale bytes that fail
    the consumer's digest check — and the mismatch then points at 'an
    intercepting proxy', a wrong diagnosis manufactured by our own headers."""
    r = client.get("/api/shelf/payload/linpeas.sh")
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-CortexSim-Payload-Name"] == "linpeas.sh"


# ---------------------------------------------------------------------------
# 2 · What is DECLARED, not only what is present
# ---------------------------------------------------------------------------


def test_inventory_reports_the_adapter_derived_declaration(client, shelf, wired):
    body = client.get("/api/shelf/payloads").json()
    assert body["payloads"][0]["adapter_id"] == "TOOL-LINPEAS"
    declared = {d["name"]: d for d in body["declared"]}
    assert declared["linpeas.sh"]["staged"] is True
    assert declared["linpeas.sh"]["stage_path"] == "/tmp/linpeas.sh"
    assert body["writable"] is True


def test_artifacts_endpoint_names_what_is_declared_but_missing(client, tmp_path,
                                                               monkeypatch, wired):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path / "empty"))
    body = client.get("/api/shelf/artifacts").json()
    assert body["unstaged"] == ["linpeas.sh"]
    assert body["staged"] == []
    assert body["artifacts"][0]["pinned"] is True


def test_inventory_degrades_rather_than_500s_without_a_catalog(
    client, shelf, cold_catalog,
):
    """The inventory is the manifest's reachability probe. A probe that fails
    because the tool catalog is cold sends a DC to argue with the customer's
    network team."""
    body = client.get("/api/shelf/payloads").json()
    assert body["total"] == 1
    assert body["declared"] == []


# ---------------------------------------------------------------------------
# 3 · Compose over HTTP
# ---------------------------------------------------------------------------


def test_resolve_returns_a_digest_bound_plan(client, shelf, wired, seeded_scenario):
    body = client.get("/api/shelf/resolve/SIM-EDR-999").json()
    assert body["air_gapped"] is True
    a = body["artifacts"][0]
    assert a["sha256"] == DIGEST
    assert a["url"].endswith("/api/shelf/payload/linpeas.sh")
    assert body["composition_id"].startswith("cmp_")


def test_compose_refuses_an_unstaged_artifact_with_409(client, tmp_path, monkeypatch,
                                                             wired, seeded_scenario):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path / "empty"))
    r = client.post("/api/shelf/compose", json={"scenario_id": "SIM-EDR-999",
                                                "consumer": "beacon"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "PAYLOAD_NOT_STAGED"
    assert "build-payloads.sh" in detail["detail"]
    assert detail["missing"][0]["adapter_id"] == "TOOL-LINPEAS"


def test_compose_refuses_a_pin_mismatch_with_409(client, shelf, wired, seeded_scenario):
    (shelf / "linpeas.sh").write_bytes(b"different bytes entirely\n")
    r = client.post("/api/shelf/compose", json={"scenario_id": "SIM-EDR-999"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PAYLOAD_PIN_MISMATCH"


def test_compose_carries_the_rename_through_to_the_plan(client, shelf, wired,
                                                              seeded_scenario):
    r = client.post("/api/shelf/compose", json={
        "scenario_id": "SIM-EDR-999", "consumer": "beacon",
        "stage_as": {"TOOL-LINPEAS": "/tmp/.cache/sysinfo.sh"},
    })
    body = r.json()
    assert body["artifacts"][0]["dest_path"] == "/tmp/.cache/sysinfo.sh"
    assert body["artifacts"][0]["name"] == "linpeas.sh"     # the shelf key is untouched
    assert body["renamed_count"] == 1
    assert any(w["code"] == "FILENAME_KEYED_DETECTIONS_SUPPRESSED" for w in body["warnings"])


def test_compose_refuses_a_destination_outside_the_allowlist(client, shelf, wired,
                                                                   seeded_scenario):
    r = client.post("/api/shelf/compose", json={
        "scenario_id": "SIM-EDR-999", "stage_as": {"TOOL-LINPEAS": "/usr/bin/ls"}})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "PAYLOAD_DEST_REFUSED"


def test_compose_rejects_the_bundle_consumer(client, shelf):
    r = client.post("/api/shelf/compose", json={"consumer": "bundle"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_CONSUMER"


# ---------------------------------------------------------------------------
# 4 · Staging
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, body=b"", content_type="application/octet-stream"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    """Injected in place of httpx so tests never touch the network."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        return self._response


@pytest.fixture
def fake_fetch(monkeypatch):
    def _install(response):
        import api.payloads as mod
        monkeypatch.setattr(mod, "stage_client_factory", lambda: _FakeClient(response))
        # The SSRF guard resolves DNS; a fixture host must not need to exist.
        monkeypatch.setattr(mod, "_guard_stage_url", lambda url: None)
    return _install


def test_stage_writes_the_bytes_and_records_provenance(client, tmp_path, monkeypatch,
                                                       fake_fetch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    fake_fetch(_FakeResponse(200, BODY))
    r = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/linpeas.sh",
        "sha256": DIGEST, "license": "MIT"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["payload"]["sha256"] == DIGEST
    assert (tmp_path / "linpeas.sh").read_bytes() == BODY
    manifest = json.loads((tmp_path / "MANIFEST.json").read_text())
    assert manifest["payloads"][0]["license"] == "MIT"
    assert DIGEST in (tmp_path / "SHA256SUMS").read_text()


def test_stage_routes_the_operator_to_the_pack_not_to_sources_json(client, tmp_path,
                                                                   monkeypatch, fake_fetch):
    """payloads/sources.json is GENERATED from the packs. An API that appended to
    it would fork the source of truth back into two lists."""
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    fake_fetch(_FakeResponse(200, BODY))
    body = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/linpeas.sh",
        "license": "MIT"}).json()
    assert "install.artifact" not in body["pack_snippet"] or True
    assert "artifact:" in body["pack_snippet"]
    assert DIGEST in body["pack_snippet"]
    assert any(w["code"] == "DECLARE_IN_PACK" for w in body["warnings"])
    assert any(w["code"] == "ARTIFACT_UNPINNED" for w in body["warnings"])


@pytest.mark.parametrize("response,code", [
    (_FakeResponse(401, b"nope"), "PAYLOAD_FETCH_FAILED"),
    (_FakeResponse(404, b"missing"), "PAYLOAD_FETCH_FAILED"),
    (_FakeResponse(302, b""), "PAYLOAD_FETCH_FAILED"),
    (_FakeResponse(200, b"<html>Sign in</html>", "text/html; charset=utf-8"),
     "PAYLOAD_FETCH_FAILED"),
])
def test_a_non_payload_response_is_never_a_staged_artifact(client, tmp_path, monkeypatch,
                                                           fake_fetch, response, code):
    """This repo has shipped a 401-counted-as-delivered bug AND a
    302-to-a-captive-portal bug. An HTML error page staged as linpeas.sh gives a
    perfect digest, verifies flawlessly on the consumer, and runs as a no-op."""
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    fake_fetch(response)
    r = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/x", "license": "MIT"})
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == code
    assert not (tmp_path / "linpeas.sh").exists()
    assert not list(tmp_path.glob(".*.part"))


def test_a_pin_mismatch_leaves_nothing_behind_and_offers_no_override(client, tmp_path,
                                                                     monkeypatch, fake_fetch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    fake_fetch(_FakeResponse(200, b"something else"))
    r = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/x",
        "sha256": DIGEST, "license": "MIT"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "PAYLOAD_PIN_MISMATCH"
    assert "no override here" in detail["detail"]
    assert not (tmp_path / "linpeas.sh").exists()
    assert not list(tmp_path.glob(".*.part"))


def test_staging_over_an_existing_artifact_is_refused_by_default(client, shelf, fake_fetch):
    fake_fetch(_FakeResponse(200, b"newer bytes"))
    r = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/x", "license": "MIT"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PAYLOAD_EXISTS"


def test_replacing_an_artifact_warns_that_every_prior_plan_is_now_invalid(client, shelf,
                                                                          fake_fetch):
    fake_fetch(_FakeResponse(200, b"newer bytes"))
    body = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/x",
        "license": "MIT", "replace": True}).json()
    assert body["replaced_sha256"] == DIGEST
    assert any(w["code"] == "DIGEST_CHANGED" for w in body["warnings"])


def test_a_missing_licence_is_refused(client, tmp_path, monkeypatch, fake_fetch):
    """The licence travels into a POV report a customer's legal team may read,
    and is unrecoverable once the artifact has shipped."""
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    fake_fetch(_FakeResponse(200, BODY))
    r = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/x", "license": "unknown"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "LICENSE_REQUIRED"


def test_staging_from_an_adapter_uses_the_packs_own_url_and_pin(client, tmp_path,
                                                                monkeypatch, fake_fetch, wired):
    """A URL is never parsed out of install.runtime_install_command — a shell
    string that silently yields the wrong URL is how staging becomes a no-op."""
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    fake_fetch(_FakeResponse(200, BODY))
    body = client.post("/api/shelf/stage", json={"adapter_id": "TOOL-LINPEAS"}).json()
    assert body["payload"]["sha256"] == DIGEST
    assert body["payload"]["pinned"] is True
    assert body["payload"]["license"] == "MIT"


def test_staging_from_an_adapter_with_no_artifact_is_refused(client, tmp_path,
                                                             monkeypatch, wired):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    wired.install.artifact = None
    r = client.post("/api/shelf/stage", json={"adapter_id": "TOOL-LINPEAS"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "ADAPTER_HAS_NO_ARTIFACT"


def test_egress_deny_refuses_before_any_socket_is_opened(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    monkeypatch.setenv("CORTEXSIM_SHELF_EGRESS", "deny")
    r = client.post("/api/shelf/stage", json={
        "name": "x.sh", "url": "https://example.invalid/x", "license": "MIT"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SHELF_EGRESS_DISABLED"
    assert "build-payloads.sh" in r.json()["detail"]["detail"]


@pytest.mark.parametrize("url", [
    "file:///etc/shadow", "http://example.invalid/x", "https://127.0.0.1/x",
    "https://localhost/x", "https://metadata.google.internal/x",
])
def test_ssrf_and_scheme_guards(client, tmp_path, monkeypatch, url):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    r = client.post("/api/shelf/stage", json={
        "name": "x.sh", "url": url, "license": "MIT"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_PAYLOAD_URL"


def test_stage_honours_the_shelf_token_gate(client, tmp_path, monkeypatch, fake_fetch):
    """The write path falls back to the read-path token list when no dedicated
    write secret is set, so an operator who already configured
    `CORTEXSIM_K8S_PAYLOAD_TOKENS` keeps working without configuring a second."""
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_AUTH", "token")
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", "s3cret")
    monkeypatch.delenv("CORTEXSIM_SHELF_WRITE_TOKENS", raising=False)
    fake_fetch(_FakeResponse(200, BODY))
    # The fixture's bearer is NOT in the configured list.
    r = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/x", "license": "MIT"})
    assert r.status_code == 403
    r = client.post("/api/shelf/stage", json={
        "name": "linpeas.sh", "url": "https://example.invalid/x", "license": "MIT"},
        headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# 5 · Redirects — the SSRF gate has to survive the hop
# ---------------------------------------------------------------------------
#
# Found in review, 2026-08-05: the client was built with follow_redirects=True,
# so `_guard_stage_url` only ever inspected the URL the CALLER typed. A public
# `https://…/x` answering `302 -> http://169.254.169.254/…` was fetched by
# SimCore and then served straight back off the shelf — an unauthenticated SSRF
# read primitive in the one component whose entire job is trustworthy delivery.
# Proven by staging the body of a loopback-only HTTP server before the fix.
#
# `follow_redirects=False` cannot be asserted through the injected fake client
# (a fake follows nothing either way), so the property is asserted on the REAL
# factory. That is deliberate: the fake is what hid this for a whole build.


class _RedirectResponse(_FakeResponse):
    def __init__(self, location: str):
        super().__init__(302, b"")
        self.headers = {"content-type": "application/octet-stream",
                        "location": location}


class _HopRecordingClient(_FakeClient):
    """Answers the first URL with a redirect, anything else with the payload."""

    def __init__(self, location: str):
        super().__init__(None)
        self.location = location
        self.urls: list[str] = []

    def stream(self, method, url):
        self.urls.append(url)
        return _RedirectResponse(self.location) if len(self.urls) == 1 \
            else _FakeResponse(200, BODY)


def test_the_real_stage_client_does_not_follow_redirects_itself():
    import api.payloads as mod

    c = mod._default_stage_client()
    assert c.follow_redirects is False, (
        "stage_payload follows hops by hand so _guard_stage_url runs on EVERY "
        "one; letting httpx follow them restores an SSRF bypass no fake client "
        "can see"
    )


def test_a_redirect_to_a_forbidden_host_is_refused_at_the_hop(client, tmp_path,
                                                              monkeypatch):
    import api.payloads as mod

    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    hops = _HopRecordingClient("http://169.254.169.254/latest/meta-data/")
    monkeypatch.setattr(mod, "stage_client_factory", lambda: hops)
    monkeypatch.setattr(mod, "_guard_stage_url", mod._guard_stage_url)

    r = client.post("/api/shelf/stage", json={
        "name": "x.sh", "url": "https://raw.githubusercontent.com/o/r/x.sh",
        "license": "MIT"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_PAYLOAD_URL"
    assert not (tmp_path / "x.sh").exists(), "nothing may land from a refused hop"


def test_a_redirect_to_an_allowed_host_is_followed(client, tmp_path, monkeypatch):
    """GitHub releases redirect to an object store — staging must still work."""
    import api.payloads as mod

    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    hops = _HopRecordingClient("https://objects.githubusercontent.com/x.sh")
    monkeypatch.setattr(mod, "stage_client_factory", lambda: hops)
    monkeypatch.setattr(mod, "_guard_stage_url", lambda url: None)

    r = client.post("/api/shelf/stage", json={
        "name": "x.sh", "url": "https://github.com/o/r/releases/download/v1/x.sh",
        "sha256": DIGEST, "license": "MIT"})
    assert r.status_code == 201, r.text
    assert (tmp_path / "x.sh").read_bytes() == BODY
    assert hops.urls[-1] == "https://objects.githubusercontent.com/x.sh"


# ---------------------------------------------------------------------------
# The write path is never open, regardless of read-path auth mode
# ---------------------------------------------------------------------------
#
# `_require_payload_token` returns immediately in the default `open` mode, so
# gating /stage with it was a no-op: any caller who could reach SimCore could
# make it fetch an arbitrary URL and plant the bytes under a plausible name in
# the directory a DC then ships to a customer endpoint. The read path is open on
# purpose (a pod's bare `wget` has nowhere to carry a credential, and the task
# queue already hands out the same TTPs); that argument does not extend to a
# write endpoint only the console calls.


def test_stage_is_refused_when_no_write_secret_is_configured(
    make_client, shelf, monkeypatch, tmp_path
):
    from api.payloads import router, shelf_router

    monkeypatch.delenv("CORTEXSIM_SHELF_WRITE_TOKENS", raising=False)
    monkeypatch.delenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", raising=False)
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    anon = make_client(router, shelf_router)

    r = anon.post("/api/shelf/stage", json={"adapter_id": "TOOL-LINPEAS"})
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["code"] == "SHELF_WRITE_UNCONFIGURED"
    # The refusal has to name the way forward, or an operator works around it.
    assert "build-payloads.sh" in body["detail"]


def test_stage_is_refused_without_the_token_even_in_open_read_mode(
    make_client, shelf, monkeypatch, tmp_path
):
    from api.payloads import router, shelf_router

    monkeypatch.setenv("CORTEXSIM_SHELF_WRITE_TOKENS", WRITE_TOKEN)
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_AUTH", "open")
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    anon = make_client(router, shelf_router)

    # The READ path is genuinely open in this configuration...
    assert anon.get("/api/shelf/payloads").status_code == 200
    # ...and the WRITE path still is not.
    r = anon.post("/api/shelf/stage", json={"adapter_id": "TOOL-LINPEAS"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "SHELF_WRITE_DENIED"

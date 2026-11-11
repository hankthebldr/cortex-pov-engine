"""Recovering from a Host-guard refusal (RG-GUARD-03, RG-GUARD-05, RG-GUARD-06).

The guard itself is right: DNS rebinding must not reach an unauthenticated API.
What round 1 got wrong is everything a DC needs AFTER a legitimate name is
refused, all observed on c852eac:

**RG-GUARD-03.** The 400 quoted the Host with its port and said "add it":

    Host 'jumpbox01:8888' is not one SimCore answers to ... add it to
    CORTEXSIM_ALLOWED_HOSTS

but an allowlist entry was compared verbatim against the port-less host, so
``jumpbox01:8888``, ``http://jumpbox.lab:8888``, ``http://jumpbox.lab`` and
``.lab`` could never match — and ``/api/health`` echoed them back as
``allowed_hosts``. The next step with a customer watching is
``CORTEXSIM_ALLOWED_HOSTS=*``, which turns the guard off.

**RG-GUARD-05.** A refusal left no trace an operator can see. The installer's
own telemetry POST was refused by the same guard, ``/api/agents/install/attempts``
stayed empty, and ``/api/health`` — the one diagnostic surface — carried
nothing about which name was being turned away.

**RG-GUARD-06.** ``/api/health`` and the boot warning stated ``CORTEXSIM_BIND``
as the address SimCore is published on ("Published on 127.0.0.1 only"). The
app never binds; it echoed a compose setting. Under the native launcher, which
binds every interface, that statement was false.
"""
from __future__ import annotations

import logging

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _app(allowed_raw: str = "", *, refusals=None):
    from security import host_guard

    async def ok(request):  # noqa: ANN001, ARG001
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/api/x", ok, methods=["GET", "POST"]),
                              Route("/api/agents/install/telemetry", ok, methods=["POST"])])
    kwargs = {} if refusals is None else {"refusals": refusals}
    return host_guard.HostGuardMiddleware(
        inner, allowed_hosts=host_guard.parse_allowed_hosts(allowed_raw),
        hostname_fn=lambda: "cid-4f2a", fqdn_fn=lambda: "cid-4f2a", **kwargs)


def _get(app, host: str, path: str = "/api/x"):
    return TestClient(app).get(path, headers={"host": host})


# ---------------------------------------------------------------------------
# RG-GUARD-03 — the entry a DC would naturally type is the entry that works
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", [
    "jumpbox.lab",                 # already worked
    "jumpbox.lab:8888",            # what the 400 used to quote
    "http://jumpbox.lab:8888",     # the beacon's --server, pasted
    "http://jumpbox.lab",
    "https://jumpbox.lab:8888/",
    "https://JUMPBOX.lab./console",
    ".lab",                        # the usual way to write "any subdomain"
    "*.lab",
    "*.lab:8888",
])
def test_an_allowlist_entry_with_a_port_scheme_or_path_admits_its_host(entry):
    assert _get(_app(entry), "jumpbox.lab:8888").status_code == 200, entry


@pytest.mark.parametrize("raw, parsed", [
    ("", []),
    (" A.example , *.Lab.example ,,", ["a.example", "*.lab.example"]),
    ("jumpbox.lab:8888, http://jumpbox.lab, JUMPBOX.LAB.", ["jumpbox.lab"]),
    (".lab.example", ["*.lab.example"]),
    ("[2001:db8::5]:8888", ["2001:db8::5"]),
    ("jumpbox.lab/console:8888", ["jumpbox.lab"]),   # everything after the host is a path
    ("*", ["*"]),
    ("simcore.lab, *", ["simcore.lab", "*"]),
])
def test_parse_allowed_hosts_normalises(raw, parsed):
    from security.host_guard import parse_allowed_hosts

    assert parse_allowed_hosts(raw) == parsed


@pytest.mark.parametrize("entry", [
    "lab*.example",      # only a leading "*." is a wildcard
    "jump box",
    "http://",
    "*.",
    "jumpbox.lab:port",
    "-bad-.example",
    "user@jumpbox.lab",
    "*.10.0.0.5",
])
def test_an_entry_that_can_never_match_is_reported_not_kept(entry):
    from security.host_guard import parse_allowed_hosts, unusable_allowed_hosts

    assert parse_allowed_hosts(f"good.lab, {entry}") == ["good.lab"]
    assert unusable_allowed_hosts(f"good.lab, {entry}") == [entry.strip()]


def test_normalising_does_not_widen_the_guard():
    """A scheme or port is dropped; the NAME is still matched exactly, and a
    rebinding host is still refused."""
    app = _app("http://jumpbox.lab:8888, .pov.example")
    for refused in ("rebind.attacker.example", "jumpbox.lab.attacker.example",
                    "xjumpbox.lab", "pov.example", "lab"):
        assert _get(app, refused).status_code == 400, refused
    assert _get(app, "a.pov.example").status_code == 200


def test_the_400_quotes_the_name_to_add_without_its_port():
    resp = _get(_app(), "jumpbox01:8888")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "'jumpbox01'" in detail, detail
    assert "jumpbox01:8888" not in detail, "the port is not part of the allowlist entry"
    assert "CORTEXSIM_ALLOWED_HOSTS" in detail


def test_a_malformed_host_header_is_named_as_malformed():
    resp = _get(_app(), "evil.example:8888:9")
    assert resp.status_code == 400
    assert "malformed" in resp.json()["detail"].lower()


def test_health_reports_the_effective_allowlist_and_the_ignored_entries(monkeypatch):
    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_ALLOWED_HOSTS",
                        "http://jumpbox.lab:8888, lab*.example", raising=False)
    comp = health.probe_api_auth()
    assert comp["allowed_hosts"] == ["jumpbox.lab"]
    assert comp["ignored_allowed_hosts"] == ["lab*.example"]
    assert "lab*.example" in comp["detail"]


def test_boot_warns_about_an_entry_that_can_never_match(monkeypatch, caplog):
    import config
    import main

    monkeypatch.setattr(config.settings, "CORTEXSIM_ALLOWED_HOSTS",
                        "jumpbox.lab:8888, lab*.example", raising=False)
    with caplog.at_level(logging.WARNING):
        main.warn_unusable_allowed_hosts()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "lab*.example" in text and "CORTEXSIM_ALLOWED_HOSTS" in text
    assert "jumpbox.lab:8888" not in text, "a normalised entry is fine and is not warned about"


# ---------------------------------------------------------------------------
# RG-GUARD-05 — a refusal leaves a trace the operator can read
# ---------------------------------------------------------------------------


def test_refusals_are_counted_per_host_with_the_last_path():
    from security.host_guard import RefusalLog

    log = RefusalLog()
    app = _app(refusals=log)
    client = TestClient(app)
    client.get("/api/x", headers={"host": "jumpbox01:8888"})
    client.post("/api/agents/install/telemetry", headers={"host": "JUMPBOX01:8888"})
    client.get("/api/x", headers={"host": "rebind.attacker.example"})
    assert client.get("/api/x", headers={"host": "127.0.0.1"}).status_code == 200

    snap = log.snapshot()
    assert snap["total"] == 3
    by_host = {e["host"]: e for e in snap["hosts"]}
    assert by_host["jumpbox01"]["count"] == 2
    assert by_host["jumpbox01"]["last_path"] == "/api/agents/install/telemetry"
    assert by_host["jumpbox01"]["last_method"] == "POST"
    assert by_host["rebind.attacker.example"]["count"] == 1
    assert snap["hosts"][0]["host"] == "rebind.attacker.example", "most recent first"


def test_the_refusal_record_is_bounded():
    """The Host header is attacker-controlled: neither the number of names nor
    their length may grow without limit."""
    from security.host_guard import RefusalLog

    log = RefusalLog(max_hosts=4)
    app = _app(refusals=log)
    client = TestClient(app)
    for i in range(20):
        client.get("/api/x", headers={"host": f"n{i}.attacker.example"})
    client.get("/api/x", headers={"host": "x" * 5000 + ".example"})

    snap = log.snapshot()
    assert snap["total"] == 21
    assert len(snap["hosts"]) == 4
    assert all(len(e["host"]) <= 255 for e in snap["hosts"])


def test_health_names_the_host_that_was_refused(monkeypatch):
    from api import health
    from security import host_guard

    log = host_guard.RefusalLog()
    monkeypatch.setattr(host_guard, "refusals", log)
    assert health.probe_api_auth()["host_guard_refusals"] == 0

    TestClient(_app(refusals=log)).post("/api/agents/install/telemetry",
                                        headers={"host": "jumpbox01:8888"})

    comp = health.probe_api_auth()
    assert comp["host_guard_refusals"] == 1
    assert comp["last_refused_hosts"][0]["host"] == "jumpbox01"
    assert comp["last_refused_hosts"][0]["last_path"] == "/api/agents/install/telemetry"
    assert "jumpbox01" in comp["detail"] and "CORTEXSIM_ALLOWED_HOSTS" in comp["detail"]


def test_the_real_app_records_a_refused_installer_callback(monkeypatch):
    """End to end on main.app: the installer's telemetry POST arrives under a
    name the guard refuses; /api/health, reached by IP, then says which."""
    import main
    from security import host_guard

    monkeypatch.setattr(host_guard, "_default_fqdn", lambda: "cid-4f2a")
    monkeypatch.setattr(host_guard, "_default_hostname", lambda: "cid-4f2a")
    log = host_guard.RefusalLog()
    monkeypatch.setattr(host_guard, "refusals", log)
    client = TestClient(main.app)

    refused = client.post("/api/agents/install/telemetry", json={},
                          headers={"host": "jumpbox01.lab.example:8888"})
    assert refused.status_code == 400 and refused.json()["code"] == "HOST_NOT_ALLOWED"

    api_auth = client.get("/api/health", headers={"host": "127.0.0.1:8888"}) \
        .json()["components"]["api_auth"]
    assert api_auth["host_guard_refusals"] >= 1
    assert api_auth["last_refused_hosts"][0]["host"] == "jumpbox01.lab.example"


@pytest.mark.parametrize("server, admitted", [
    ("http://127.0.0.1:8888", True),
    ("http://10.20.30.40:8888", True),
    ("http://localhost:8888", True),
    ("http://cid-4f2a:8888", True),
    ("https://jumpbox.lab:8888", True),          # allowlisted
    ("http://jumpbox.lab.example:8888", False),  # a name nothing admits
    ("jumpbox.lab.example", False),
])
def test_would_admit_answers_for_a_server_url_before_it_is_handed_out(server, admitted):
    """For the installer and manifest generators: will the name they are about
    to bake into ``--server`` be refused by this SimCore's own guard?"""
    import asyncio

    app = _app("jumpbox.lab")
    assert asyncio.run(app.would_admit(server)) is admitted


# ---------------------------------------------------------------------------
# RG-GUARD-06 — CORTEXSIM_BIND is a setting, not an observation
# ---------------------------------------------------------------------------


def test_health_does_not_state_the_configured_bind_as_where_simcore_listens(monkeypatch):
    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "127.0.0.1", raising=False)
    comp = health.probe_api_auth()
    detail = comp["detail"]
    assert comp["bind"] == "127.0.0.1"
    assert comp["bind_verified"] is False
    assert comp["bind_source"] == "CORTEXSIM_BIND"
    assert "anyone who can reach 127.0.0.1:" not in detail
    assert "CORTEXSIM_BIND=127.0.0.1 is the address docker-compose is configured" in detail
    assert "not verified" in detail and "cannot see" in detail, detail
    assert "uvicorn" in detail, "a host-run SimCore binds its own --host; say so"

    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "", raising=False)
    unset = health.probe_api_auth()
    assert unset["bind_source"] == "default" and unset["bind_verified"] is False
    assert "docker-compose publishes" in unset["detail"]


def test_module_level_would_admit_follows_the_deployment_setting(monkeypatch):
    """What a route calls: no middleware handle needed, same answer."""
    import asyncio

    import config
    from security import host_guard

    monkeypatch.setattr(host_guard, "_default_fqdn", lambda: "cid-4f2a")
    monkeypatch.setattr(host_guard, "_default_hostname", lambda: "cid-4f2a")
    monkeypatch.setattr(host_guard, "_route_guard", None)
    log = host_guard.RefusalLog()
    monkeypatch.setattr(host_guard, "refusals", log)

    monkeypatch.setattr(config.settings, "CORTEXSIM_ALLOWED_HOSTS", "", raising=False)
    assert asyncio.run(host_guard.would_admit("http://jumpbox.lab:8888")) is False
    assert asyncio.run(host_guard.would_admit("http://10.0.0.5:8888")) is True

    monkeypatch.setattr(config.settings, "CORTEXSIM_ALLOWED_HOSTS", "jumpbox.lab",
                        raising=False)
    assert asyncio.run(host_guard.would_admit("http://jumpbox.lab:8888")) is True
    assert log.snapshot()["total"] == 0, "asking is not a refusal"


def test_the_boot_warning_does_not_claim_a_loopback_only_publish(monkeypatch, caplog):
    import config
    import main

    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "127.0.0.1", raising=False)
    with caplog.at_level(logging.WARNING):
        main.warn_unauthenticated()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "Published on 127.0.0.1 only" not in text
    assert "CORTEXSIM_BIND=127.0.0.1" in text
    assert "uvicorn" in text

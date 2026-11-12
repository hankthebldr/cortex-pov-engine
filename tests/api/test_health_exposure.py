"""SEC-10 / SEC-01 — an unauthenticated deployment must never read ``ok``.

SimCore has no API authentication. Every operator route (launch a run, read
the credential inventory, start a tool) and every agent route (enroll, poll
tasks, post output) answers anyone who can reach the published port, and
``docker-compose.yml`` published that port on every host interface. Before
this pass ``/api/health`` said nothing about it: a DC could stand SimCore up on
a customer lab network, see ``{"status": "ok"}``, and reasonably conclude the
deployment was fine.

The rule this file pins is the repo's own (Gate A5): *a zero is degraded, not
ok*. Zero authentication is a component, ``api_auth``, permanently ``degraded``
with code ``API_UNAUTHENTICATED`` until an auth mechanism exists — so the
overall status of every current deployment is ``degraded``, by design. Its
detail names the bind so the DC can see what is exposed and where.

RED on base cc26ddf: ``api_auth`` was not a component at all.
"""
from __future__ import annotations

import asyncio
import inspect
import logging

import pytest


def test_api_auth_is_a_declared_component():
    from api import health

    assert "api_auth" in health.COMPONENT_KEYS


def test_api_auth_is_degraded_and_names_the_exposure(monkeypatch):
    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "10.9.8.7", raising=False)
    comp = health.probe_api_auth()
    assert comp["status"] == "degraded"
    assert comp["code"] == "API_UNAUTHENTICATED"
    assert comp["authenticated"] is False
    detail = comp["detail"]
    assert "unauthenticated" in detail.lower()
    assert "operator" in detail and "agent" in detail
    assert "10.9.8.7" in detail, "the detail must name the bind that is exposed"
    assert "Fix:" in detail
    assert comp["bind"] == "10.9.8.7"


def test_an_unset_bind_is_reported_as_every_interface(monkeypatch):
    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "", raising=False)
    comp = health.probe_api_auth()
    assert comp["bind"] == "0.0.0.0"
    assert "every interface" in comp["detail"]


def test_overall_health_is_degraded_while_unauthenticated():
    from api import health

    body = asyncio.run(health.health_payload())
    assert body["components"]["api_auth"]["code"] == "API_UNAUTHENTICATED"
    assert "api_auth" in body["degraded_components"]
    assert body["status"] == "degraded", (
        "a deployment with zero API authentication reported overall 'ok'"
    )


def test_api_auth_reports_the_host_guard_configuration(monkeypatch):
    """When a beacon or browser is refused HOST_NOT_ALLOWED, this is where a
    DC looks to see what the guard admits."""
    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_ALLOWED_HOSTS",
                        "simcore.lab, *.pov.example", raising=False)
    monkeypatch.setattr(config.settings, "CORTEXSIM_CORS_ORIGINS", "", raising=False)
    comp = health.probe_api_auth()
    assert comp["allowed_hosts"] == ["simcore.lab", "*.pov.example"]
    assert comp["host_guard"] == "enforced"
    assert comp["cors_origins"] == []

    monkeypatch.setattr(config.settings, "CORTEXSIM_ALLOWED_HOSTS", "*", raising=False)
    assert health.probe_api_auth()["host_guard"] == "disabled"


# ---------------------------------------------------------------------------
# Boot warning
# ---------------------------------------------------------------------------


def test_boot_warns_loudly_when_unauthenticated(caplog, monkeypatch):
    import config
    import main

    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "", raising=False)
    caplog.set_level(logging.WARNING, logger="cortexsim.main")
    main.warn_unauthenticated()
    warnings = [r for r in caplog.records
                if r.levelno >= logging.WARNING and r.name == "cortexsim.main"]
    assert warnings, "no boot warning for an unauthenticated API"
    text = " ".join(r.getMessage() for r in warnings)
    assert "UNAUTHENTICATED" in text
    assert "0.0.0.0" in text


def test_lifespan_calls_the_boot_warning():
    """``warn_unauthenticated`` is useless if boot never calls it."""
    import main

    assert "warn_unauthenticated()" in inspect.getsource(main.lifespan)

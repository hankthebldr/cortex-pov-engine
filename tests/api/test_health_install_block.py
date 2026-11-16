"""The ``install`` block of ``GET /api/health``: set only when the setup
program started SimCore. Top-level key, never a component."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import api.agents as agents_mod
    import config

    monkeypatch.setattr(agents_mod, "ignored_forwarders", lambda: [])
    monkeypatch.setattr(config.settings, "CORTEXSIM_INSTALLED_BY", "")
    monkeypatch.setattr(config.settings, "CORTEXSIM_INSTALL_ADDRESS", "")
    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "")


def _set(monkeypatch, installed_by, address="192.0.2.10:8888"):
    import config

    monkeypatch.setattr(config.settings, "CORTEXSIM_INSTALLED_BY", installed_by)
    monkeypatch.setattr(config.settings, "CORTEXSIM_INSTALL_ADDRESS", address)


def _payload():
    from api import health

    return asyncio.run(health.health_payload())


def test_install_block_absent_on_compose():
    body = _payload()
    assert "install" in body
    assert body["install"] is None


def test_install_block_present_when_installed(monkeypatch):
    _set(monkeypatch, "povengine-setup/1.3.0+linux")
    assert _payload()["install"] == {
        "installed_by": "povengine-setup/1.3.0",
        "address": "192.0.2.10:8888",
        "listen": "all",
        "platform": "linux",
    }


def test_install_block_listen_address_when_bind_set(monkeypatch):
    import config

    _set(monkeypatch, "povengine-setup/1.3.0+windows")
    monkeypatch.setattr(config.settings, "CORTEXSIM_BIND", "192.0.2.10")
    block = _payload()["install"]
    assert block["listen"] == "address"
    assert block["platform"] == "windows"


def test_install_block_never_carries_secret(monkeypatch):
    import config

    _set(monkeypatch, "povengine-setup/1.3.0+linux")
    block = _payload()["install"]
    secret = config.settings.CORTEXSIM_SECRET
    assert secret and all(v != secret for v in block.values())


def test_install_block_is_not_a_component(monkeypatch):
    _set(monkeypatch, "povengine-setup/1.3.0+linux")
    base = _payload()
    assert "install" not in base["components"]
    assert "install" not in base["checked"]
    assert "install" not in base["degraded_components"]


def test_install_block_does_not_change_status(monkeypatch):
    without = _payload()["status"]
    _set(monkeypatch, "povengine-setup/1.3.0+linux")
    assert _payload()["status"] == without


@pytest.mark.parametrize("raw", ["manual", "povengine-setup/1.3.0", "povengine-setup/1.3.0+macos", "povengine-setup/+linux"])
def test_unrecognised_installed_by_is_reported_raw_not_as_success(monkeypatch, raw):
    _set(monkeypatch, raw)
    block = _payload()["install"]
    assert block["installed_by"] == raw
    assert block["platform"] is None

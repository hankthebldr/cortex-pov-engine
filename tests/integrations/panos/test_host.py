"""Management-host validation and config defaults for the panos_ngfw kind."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from integrations.panos import PANOS_KIND
from integrations.panos import codes
from integrations.panos.exceptions import (
    PanosApiError, PanosConfigError, PanosShapeError, PanosTransportError, PanosWriteRefused,
)
from integrations.panos.host import DEFAULT_PROBES, normalize_mgmt_host, validate_panos_config

CORE = Path(__file__).resolve().parents[3] / "core"


def test_kind_constant():
    assert PANOS_KIND == "panos_ngfw"


@pytest.mark.parametrize("raw,expected", [
    ("192.0.2.1", "192.0.2.1"),
    ("fw.example.test", "fw.example.test"),
    ("FW.Example.Test:4443", "fw.example.test:4443"),
    ("[2001:db8::1]", "[2001:db8::1]"),
    ("2001:db8::1", "[2001:db8::1]"),
    ("[2001:db8::1]:8443", "[2001:db8::1]:8443"),
])
def test_accepts(raw, expected):
    assert normalize_mgmt_host(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "https://192.0.2.1", "192.0.2.1/api", "admin@192.0.2.1", "192.0.2.1?x=1",
    "192.0.2.1:0", "192.0.2.1:70000", "a b",
])
def test_rejects(raw):
    with pytest.raises(PanosConfigError):
        normalize_mgmt_host(raw)


def test_defaults_filled():
    cfg = validate_panos_config({"host": "192.0.2.1"})
    assert cfg["verify_tls"] is True
    assert cfg["vsys"] == "vsys1"
    assert cfg["device_name"] == "localhost.localdomain"
    assert cfg["forwarding_path"] == "auto"
    assert cfg["routing_instance"] == "default"
    assert cfg["probe_destinations"] == DEFAULT_PROBES
    assert cfg["observed_sources"] == {} and cfg["agent_addresses"] == {}


def test_defaults_not_aliased():
    a = validate_panos_config({"host": "192.0.2.1"})
    a["probe_destinations"].append({"x": 1})
    assert len(DEFAULT_PROBES) == 3


@pytest.mark.parametrize("patch", [
    {"vsys": "vsys"},
    {"forwarding_path": "ftp"},
    {"probe_destinations": [{"ip": "1.1.1.1", "port": 53, "protocol": "icmp", "application": "dns"}]},
    {"observed_sources": {"a": "not-an-ip"}},
])
def test_config_rejects(patch):
    with pytest.raises(PanosConfigError):
        validate_panos_config({"host": "192.0.2.1", **patch})


def test_exceptions_carry_code():
    assert issubclass(PanosConfigError, ValueError)
    for cls in (PanosShapeError, PanosWriteRefused, PanosTransportError):
        assert issubclass(cls, RuntimeError)
        assert cls("x", code="PN_UNREACHABLE").code == "PN_UNREACHABLE"
    assert issubclass(PanosApiError, PanosShapeError)
    e = PanosApiError("boom", code="13")
    assert e.code == "13" and e.message == "boom"


def test_codes():
    for c in ("PN_OK", "PN_KEYGEN_REJECTED", "PN_CALL_BUDGET", "PN_SKIPPED_UNREACHABLE",
              "PN_SKIPPED_AUTH", "PN_PATH_NOT_SELECTED", "PN_NO_FORWARDING_PATH",
              "PN_LFP_WRONG_TARGET", "PN_SYSLOG_DELIVERY_UNVERIFIED", "PN_SYSLOG_UDP",
              "PN_SYSLOG_NO_SERVERS", "PN_OBJECT_NOT_FOUND", "PN_PROFILE_SIGNATURE_DEFAULT",
              "PN_ZONE_UNRESOLVED", "PN_UNREACHABLE", "PN_SHAPE_UNRECOGNISED"):
        assert getattr(codes, c) == c
        assert c in codes.ALL_CODES
    assert all(c.startswith("PN_") for c in codes.ALL_CODES)


def test_normalize_mgmt_host_defined_once():
    hits = []
    for p in CORE.rglob("*.py"):
        if ".venv" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        hits += [str(p) for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "normalize_mgmt_host"]
    assert len(hits) == 1, hits

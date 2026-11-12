"""Guards for the collector-target resolver (core/eal_simulator/targets.py).

One function decides where every in-process collector step POSTs. These tests
pin its precedence (explicit > binding > default > inline > none), the two
refusal codes, the allowlist pin, and that a key never enters an overlay the
caller did not ask to carry secrets.
"""
from __future__ import annotations

import copy

import pytest

from eal_simulator import get_default_registry
from eal_simulator.targets import (
    UNRESOLVED_COLLECTOR_URL,
    TargetSpec,
    TargetSpecError,
    apply_targets,
    collector_plugin_names,
    check_verdict,
)

REG = get_default_registry()


def _target(name, *, plugins=(), is_default=False, url=None, has_token=True,
            kind="xsiam_http_collector", last_check=None):
    return TargetSpec(
        name=name, kind=kind,
        url=url or f"https://api-{name}.example.test/logs/v1/event",
        dataset=None, auth_header="Authorization", auth_scheme="",
        plugins=tuple(plugins), is_default=is_default, has_token=has_token,
        last_check=last_check,
    )


def _spec(*steps, allowlist=None):
    return {
        "campaign_id": "CMP-TEST-001", "name": "t", "dry_run": True,
        "target_allowlist": list(allowlist or []),
        "steps": [
            {"step_id": f"step-{i + 1:02d}", "plugin": p, "params": dict(params)}
            for i, (p, params) in enumerate(steps)
        ],
    }


def test_collector_plugins_are_the_ones_whose_params_declare_collector_url():
    names = collector_plugin_names(REG)
    assert {"cloud_audit_emitter", "idp_signin_emulator", "email_emitter",
            "ngfw_eal_emitter"} <= names
    assert "c2_http_beacon" not in names
    assert "dns_tunnel_exfil" not in names


def test_binding_wins_over_default():
    targets = [_target("okta", plugins=["idp_signin_emulator"]),
               _target("catchall", is_default=True)]
    out, res = apply_targets(_spec(("idp_signin_emulator", {})), REG, targets)
    assert res[0].source == "binding" and res[0].target == "okta"
    assert out["steps"][0]["params"]["collector_url"] == targets[0].url


def test_explicit_name_wins_over_binding():
    targets = [_target("okta", plugins=["idp_signin_emulator"]),
               _target("other")]
    out, res = apply_targets(
        _spec(("idp_signin_emulator", {"collector_target": "other"})), REG, targets)
    assert res[0].source == "explicit" and res[0].target == "other"
    # The routing key is consumed: plugin params models never see it.
    assert "collector_target" not in out["steps"][0]["params"]


def test_default_serves_an_unbound_plugin():
    targets = [_target("catchall", is_default=True)]
    _, res = apply_targets(_spec(("cloud_audit_emitter", {})), REG, targets)
    assert res[0].source == "default" and res[0].target == "catchall"


def test_resolved_target_replaces_an_inline_url_and_says_so():
    inline = "https://old.example.test/logs/v1/event"
    targets = [_target("catchall", is_default=True)]
    out, res = apply_targets(
        _spec(("cloud_audit_emitter", {"collector_url": inline})), REG, targets)
    assert out["steps"][0]["params"]["collector_url"] == targets[0].url
    assert res[0].replaced_inline_url == inline


def test_inline_url_is_used_and_labelled_when_no_target_resolves():
    inline = "https://old.example.test/logs/v1/event"
    out, res = apply_targets(
        _spec(("cloud_audit_emitter", {"collector_url": inline})), REG, [])
    assert res[0].source == "inline" and res[0].code == "COLLECTOR_TARGET_INLINE"
    assert out["steps"][0]["params"]["collector_url"] == inline


def test_nothing_configured_is_reported_not_silently_filled():
    out, res = apply_targets(_spec(("cloud_audit_emitter", {})), REG, [])
    assert res[0].source == "none"
    assert res[0].code == "COLLECTOR_TARGET_NOT_CONFIGURED"
    # The placeholder can never resolve in DNS, so a stray live send fails closed.
    assert out["steps"][0]["params"]["collector_url"] == UNRESOLVED_COLLECTOR_URL
    assert UNRESOLVED_COLLECTOR_URL.split("/")[2].endswith(".invalid")


def test_two_targets_binding_one_plugin_is_ambiguous():
    targets = [_target("okta-a", plugins=["idp_signin_emulator"]),
               _target("okta-b", plugins=["idp_signin_emulator"])]
    _, res = apply_targets(_spec(("idp_signin_emulator", {})), REG, targets)
    assert res[0].code == "COLLECTOR_TARGET_AMBIGUOUS"
    assert res[0].target is None


def test_unknown_explicit_name_is_an_error_not_a_fallback():
    targets = [_target("catchall", is_default=True)]
    _, res = apply_targets(
        _spec(("idp_signin_emulator", {"collector_target": "typo"})), REG, targets)
    assert res[0].code == "COLLECTOR_TARGET_UNKNOWN"
    assert res[0].target is None


def test_non_collector_steps_are_untouched():
    spec = _spec(("c2_http_beacon", {"target_url": "http://testmynids.org/x"}))
    out, res = apply_targets(spec, REG, [_target("catchall", is_default=True)])
    assert res == []
    assert out["steps"] == spec["steps"]


def test_target_host_port_is_pinned_into_the_allowlist():
    targets = [_target("catchall", is_default=True,
                       url="https://collector.example.test:8443/logs/v1/event")]
    out, _ = apply_targets(_spec(("cloud_audit_emitter", {}), allowlist=["10.0.0.0/24"]),
                           REG, targets)
    assert out["target_allowlist"] == ["10.0.0.0/24", "collector.example.test:8443"]


def test_key_enters_the_overlay_only_when_the_caller_passes_tokens():
    targets = [_target("catchall", is_default=True)]
    spec = _spec(("cloud_audit_emitter", {}))
    without, _ = apply_targets(spec, REG, targets)
    assert "auth_token" not in without["steps"][0]["params"]
    with_key, _ = apply_targets(spec, REG, targets, tokens={"catchall": "k-123"})
    assert with_key["steps"][0]["params"]["auth_token"] == "k-123"


def test_input_spec_is_never_mutated():
    spec = _spec(("idp_signin_emulator", {"collector_target": "okta"}))
    before = copy.deepcopy(spec)
    apply_targets(spec, REG, [_target("okta")], tokens={"okta": "k"})
    assert spec == before


def test_resolved_spec_validates_against_the_plugin_params():
    targets = [_target("catchall", is_default=True)]
    out, _ = apply_targets(_spec(("cloud_audit_emitter", {})), REG, targets,
                           tokens={"catchall": "k"})
    params = REG.get("cloud_audit_emitter").validate_params(out["steps"][0]["params"])
    assert params.collector_url == targets[0].url
    assert params.auth_token.get_secret_value() == "k"


def test_unknown_kind_raises_rather_than_reading_as_a_default():
    with pytest.raises(TargetSpecError):
        _target("x", kind="syslog_udp")


def test_unrecognised_check_verdict_raises():
    assert check_verdict(None) == "unchecked"
    assert check_verdict({"verdict": "accepted"}) == "accepted"
    with pytest.raises(TargetSpecError):
        check_verdict({"verdict": "looks-fine"})

"""S-17 — step identities must be declared in spec/identity_harness.json.

This guard exists because of a real, shipped defect. 80 steps declared
``identity: svc-account``, which was in neither the spec's ``service_accounts``
nor the beacon's Go allowlist — both declare ``svc-backup``. Nothing caught it:
``harness_spec_test.go`` compares the Go allowlist to the spec, and
``identity_spec.py`` mirrors the spec, but no check ever compared the CORPUS to
either. So the drift was invisible to CI and visible only on a customer's host,
where ``runuser -l svc-account`` exits non-zero, no detection fires, and the POV
report attributes the miss to the customer's stack.

The guard is deliberately structural — NOT gated by ``CORTEXSIM_STRICT_REFS``.
The S-10..S-15 family is gated because an incomplete UC/TC crosswalk is a
content gap that must not brick boot. An undeclared identity is different in
kind: it is a step that cannot run correctly anywhere, and shipping it is
strictly worse than refusing it.
"""
from __future__ import annotations

import pytest


def _schema(**over):
    """Minimal valid ScenarioSchema; mirrors tests/engine/test_uctc_registry.py."""
    from engine.scenario_loader import ScenarioSchema  # noqa: PLC0415

    base = {
        "scenario_id": "SIM-TEST-017",
        "name": "identity-guard fixture",
        "version": "1.0",
        "status": "active",
        "plane": "EDR",
        "detection_types": ["BIOC"],
        "uc_ref": "UCS-IR-01",
        "tc_ref": "TC-IR-01",
        "uc_name": "Incident Response",
        "tc_name": "Analyst Fatigue",
        "mitre_tactic": "TA0006",
        "mitre_tactic_name": "Credential Access",
        "mitre_technique": "T1003",
        "mitre_technique_name": "OS Credential Dumping",
        "execution_identity": {"default": "www-data", "options": ["www-data"]},
        "push_supported": True,
        "pull_supported": True,
        "steps": [
            {"id": "s1", "name": "one", "command": "true", "identity": "www-data",
             "mitre_technique": "T1003",
             "expected_detections": [{"type": "BIOC", "name": "x", "plane": "EDR",
                                      "description": "d", "detection_id": "d1"}]},
            {"id": "s2", "name": "two", "command": "true", "identity": "www-data",
             "mitre_technique": "T1003",
             "expected_detections": [{"type": "BIOC", "name": "y", "plane": "EDR",
                                      "description": "d", "detection_id": "d2"}]},
        ],
    }
    base.update(over)
    return ScenarioSchema(**base)


def _step(identity, platforms=None, sid="s1"):
    s = {"id": sid, "name": "n", "command": "true", "identity": identity,
         "mitre_technique": "T1003",
         "expected_detections": [{"type": "BIOC", "name": "x", "plane": "EDR",
                                  "description": "d", "detection_id": "d1"}]}
    if platforms is not None:
        s["platforms"] = platforms
    return s


def _check(schema):
    from engine.scenario_loader import _check_step_identities  # noqa: PLC0415
    return _check_step_identities(schema, "test.yml")


# --------------------------------------------------------------------------
# accepted
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ident", [
    "www-data", "postgres", "mysql", "node", "python3", "nobody", "svc-backup",
    "app", "developer", "runner", "vertex-agent",
])
def test_declared_service_accounts_are_accepted(ident):
    assert _check(_schema(steps=[_step(ident)])) is None


@pytest.mark.parametrize("ident", ["root", "container-runtime", "direct"])
def test_direct_identities_are_accepted(ident):
    assert _check(_schema(steps=[_step(ident)])) is None


def test_identity_is_a_required_field_so_it_can_never_be_omitted():
    """S-17 tolerates an empty identity defensively, but the schema makes that
    state unreachable — a step cannot silently opt out of the check by leaving
    the field off."""
    import pydantic  # noqa: PLC0415

    step = _step("www-data")
    step.pop("identity")
    with pytest.raises(pydantic.ValidationError, match="identity"):
        _schema(steps=[step])


@pytest.mark.parametrize("ident", ["administrator", "user"])
def test_windows_identities_are_accepted_on_windows_only_steps(ident):
    assert _check(_schema(steps=[_step(ident, platforms=["windows"])])) is None


# --------------------------------------------------------------------------
# rejected — the whole point
# --------------------------------------------------------------------------

def test_the_original_defect_is_now_rejected():
    """`svc-account` is exactly what shipped on 80 steps."""
    err = _check(_schema(steps=[_step("svc-account")]))
    assert err is not None, "S-17 did not reject the identity that caused this guard to exist"
    assert "S-17" in err and "svc-account" in err
    assert "not declared" in err


def test_an_arbitrary_undeclared_identity_is_rejected():
    err = _check(_schema(steps=[_step("totally-made-up")]))
    assert err is not None and "totally-made-up" in err


@pytest.mark.parametrize("platforms", [["linux"], ["linux", "windows"], ["container", "k8s"]])
def test_a_windows_identity_off_a_windows_only_step_is_rejected(platforms):
    """`runuser -l administrator` on Linux is an authoring error, not a
    best-effort. Allowing windows identities everywhere would defeat S-17."""
    err = _check(_schema(steps=[_step("administrator", platforms=platforms)]))
    assert err is not None, f"administrator accepted on platforms={platforms}"
    assert "windows-only identity" in err


def test_a_windows_identity_with_no_declared_platform_is_rejected():
    """An undeclared platform cannot prove the step is windows-only."""
    err = _check(_schema(steps=[_step("administrator")]))
    assert err is not None and "no declared platform" in err


def test_every_offending_step_is_named_not_just_the_first():
    """An operator fixing this needs the whole list, not one round trip each."""
    err = _check(_schema(steps=[
        _step("svc-account", sid="s1"),
        _step("www-data", sid="s2"),
        _step("nope", sid="s3"),
    ]))
    assert err is not None
    assert "s1" in err and "s3" in err
    assert "s2" not in err.split("—")[0]


# --------------------------------------------------------------------------
# the corpus itself
# --------------------------------------------------------------------------

def test_the_real_corpus_declares_only_declared_identities():
    """The regression this guard was written for: catch corpus drift in CI
    rather than on a customer's jumpbox."""
    import glob  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    from engine.identity_spec import (  # noqa: PLC0415
        DIRECT_IDENTITIES, SERVICE_ACCOUNTS, windows_identities,
    )

    allowed = set(DIRECT_IDENTITIES) | set(SERVICE_ACCOUNTS)
    win_only = set(windows_identities())
    offenders = []

    for path in sorted(glob.glob("scenarios/**/*.yml", recursive=True)):
        try:
            doc = yaml.safe_load(open(path))
        except Exception:  # noqa: BLE001 - non-scenario YAML in the tree
            continue
        if not isinstance(doc, dict) or "steps" not in doc:
            continue
        for step in doc.get("steps") or []:
            if not isinstance(step, dict):
                continue
            ident = (step.get("identity") or "").strip()
            if not ident or ident in allowed:
                continue
            plats = {str(p).lower() for p in (step.get("platforms") or [])}
            if ident in win_only and plats and plats <= {"windows"}:
                continue
            offenders.append(f"{doc.get('scenario_id')}/{step.get('id')}={ident}")

    assert not offenders, (
        "corpus declares identities that spec/identity_harness.json does not: "
        + ", ".join(sorted(set(offenders)))
    )

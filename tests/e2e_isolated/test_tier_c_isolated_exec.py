"""Tier-C increment 2 — isolated-execution assertion tests.

Two layers:

  1. **Pure assertion-engine tests (always run, no docker).** Drive
     ``deploy/tier-c/tier_c_assert.py`` with synthetic ``observed-signals.json``
     fixtures — passing, failing, and degraded-mode — to prove the
     expectation → verdict logic for the three reference scenarios
     (SIM-EDR-001, SIM-CDR-001, SIM-MP-004) is correct. These are the formal
     version of the README's "How the audit output maps to expected_detections"
     table and guard it on every PR.

  2. **Docker-gated end-to-end test (skips without docker).** Builds + runs the
     Tier-C stack against a generated push bundle and applies the same
     assertion engine to the real ``observed-signals.json``. Skips gracefully
     when docker is absent (CI default) so the suite stays green everywhere.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TIER_C = REPO_ROOT / "deploy" / "tier-c"
SCENARIOS = REPO_ROOT / "scenarios"


# Import the stdlib-only assertion module by path (it lives under deploy/, not
# on sys.path) so these tests exercise the exact file that ships in the image.
def _load_assert_module():
    import sys

    spec = importlib.util.spec_from_file_location(
        "tier_c_assert", TIER_C / "tier_c_assert.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass annotation resolution (frozenset[str])
    # can find the module in sys.modules.
    sys.modules["tier_c_assert"] = mod
    spec.loader.exec_module(mod)
    return mod


tc = _load_assert_module()


# ─── Fixtures: synthetic observed-signals.json shapes ────────────────────

def _auditd_signals(*, uids, exec_list, creds, net, sinkhole=0, exit_code=0):
    return {
        "status": "ok",
        "audit_mode": "auditd",
        "bundle": "bundle.sh",
        "bundle_exit_code": exit_code,
        "signals": {
            "exec": {"count": len(exec_list), "executables": list(exec_list)},
            "setid": {"count": len(uids), "uids": list(uids)},
            "net": {"count": net},
            "creds": {"count": creds},
        },
        "sinkhole": {"http_request_count": sinkhole},
        "harness_identities_from_stdout": list(uids),
    }


def _degraded_signals(*, stdout_ids, sinkhole=0, exit_code=0):
    return {
        "status": "ok",
        "audit_mode": "degraded",
        "bundle_exit_code": exit_code,
        "signals": {
            "exec": {"count": 0, "executables": []},
            "setid": {"count": 0, "uids": []},
            "net": {"count": 0},
            "creds": {"count": 0},
        },
        "sinkhole": {"http_request_count": sinkhole},
        "harness_identities_from_stdout": list(stdout_ids),
    }


# ─── Reference expectation integrity ─────────────────────────────────────

REFERENCE_IDS = ["SIM-EDR-001", "SIM-CDR-001", "SIM-MP-004"]


@pytest.mark.parametrize("sid", REFERENCE_IDS)
def test_reference_expectation_exists(sid):
    """Every reference scenario has a hand-authored expectation."""
    assert sid in tc.REFERENCE_EXPECTATIONS, f"missing reference expectation {sid}"
    exp = tc.REFERENCE_EXPECTATIONS[sid]
    assert exp.scenario_id == sid


def _find_scenario_file(sid: str) -> pathlib.Path:
    target = sid.lower().replace("sim-", "")
    # e.g. SIM-EDR-001 -> edr/edr-001-*, SIM-MP-004 -> multi_plane/mp-004-*
    for path in SCENARIOS.rglob("*.yml"):
        doc = yaml.safe_load(path.read_text())
        if isinstance(doc, dict) and doc.get("scenario_id") == sid:
            return path
    raise AssertionError(f"scenario file for {sid} not found (looked for {target})")


@pytest.mark.parametrize("sid", REFERENCE_IDS)
def test_reference_identities_match_scenario(sid):
    """A reference expectation's service-account identities must be a subset of
    the identities the scenario actually declares — otherwise the assertion
    would demand an identity the run never forks under."""
    path = _find_scenario_file(sid)
    doc = yaml.safe_load(path.read_text())
    declared = set()
    for step in doc.get("steps", []) or []:
        ident = (step.get("identity") or "").strip()
        if ident and ident not in tc.DIRECT_IDENTITIES:
            declared.add(ident)
    exp = tc.REFERENCE_EXPECTATIONS[sid]
    assert exp.identities <= declared, (
        f"{sid} expectation demands identities {exp.identities - declared} "
        f"the scenario never declares (declared: {declared})"
    )


# ─── Passing path ────────────────────────────────────────────────────────

def test_edr001_passes_with_full_signals():
    obs = _auditd_signals(
        uids=["www-data", "root"], exec_list=["/usr/bin/cat", "/usr/bin/grep", "/usr/bin/curl"],
        creds=3, net=1, sinkhole=1,
    )
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], obs)
    assert report.passed, report.to_dict()
    names = {c.name: c.status for c in report.checks}
    assert names["identities"] == "pass"
    assert names["credential_access"] == "pass"
    assert names["network_egress"] == "pass"
    assert names["executables"] == "pass"


def test_cdr001_passes_without_identity_check():
    # container-runtime is a direct identity → no identity assertion.
    obs = _auditd_signals(
        uids=[], exec_list=["/usr/bin/curl", "/bin/bash"], creds=1, net=2, sinkhole=2,
    )
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-CDR-001"], obs)
    assert report.passed, report.to_dict()
    assert not any(c.name == "identities" for c in report.checks)


# ─── Failing path ────────────────────────────────────────────────────────

def test_edr001_fails_when_identity_missing():
    """The headline Tier-C regression: a step ran as root when the YAML said
    www-data — the harness silently regressed."""
    obs = _auditd_signals(
        uids=["root"], exec_list=["/usr/bin/cat", "/usr/bin/grep"], creds=3, net=1,
    )
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], obs)
    assert not report.passed
    failed = {c.name for c in report.failed}
    assert "identities" in failed


def test_edr001_fails_when_credential_read_absent():
    obs = _auditd_signals(
        uids=["www-data"], exec_list=["/usr/bin/cat", "/usr/bin/grep"], creds=0, net=1,
    )
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], obs)
    assert not report.passed
    assert "credential_access" in {c.name for c in report.failed}


def test_fails_on_nonzero_bundle_exit():
    obs = _auditd_signals(
        uids=["www-data"], exec_list=["/usr/bin/cat", "/usr/bin/grep"],
        creds=3, net=1, exit_code=7,
    )
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], obs)
    assert not report.passed
    assert "bundle_executed" in {c.name for c in report.failed}


# ─── Degraded mode ───────────────────────────────────────────────────────

def test_degraded_mode_skips_count_checks_but_asserts_identity():
    """In degraded mode the identity check still runs (stdout-derived) while the
    count-based checks become skips, not failures."""
    obs = _degraded_signals(stdout_ids=["www-data", "root"])
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], obs)
    by_name = {c.name: c.status for c in report.checks}
    assert by_name["identities"] == "pass"
    assert by_name["credential_access"] == "skip"
    assert by_name["executables"] == "skip"
    # network can still pass via sinkhole even in degraded mode; here no hits → skip
    assert by_name["network_egress"] == "skip"
    assert report.passed  # skips do not fail the run


def test_degraded_mode_network_passes_via_sinkhole():
    """The sinkhole http log is independent of auditd, so network egress can be
    confirmed even in degraded mode."""
    obs = _degraded_signals(stdout_ids=["www-data"], sinkhole=3)
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], obs)
    by_name = {c.name: c.status for c in report.checks}
    assert by_name["network_egress"] == "pass"


def test_degraded_mode_still_fails_missing_identity():
    obs = _degraded_signals(stdout_ids=["root"])  # www-data never forked
    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], obs)
    assert not report.passed
    assert "identities" in {c.name for c in report.failed}


# ─── Auto-derivation for non-reference scenarios ─────────────────────────

def test_derive_expectation_picks_up_service_account_and_creds():
    scenario = {
        "scenario_id": "SIM-EDR-001",
        "steps": [
            {"identity": "www-data", "command": "cat /etc/shadow"},
            {"identity": "root", "command": "curl https://example.invalid/x"},
        ],
    }
    exp = tc.derive_expectation(scenario)
    assert exp.identities == frozenset({"www-data"})  # root is a direct identity
    assert exp.expect_credential_access is True
    assert exp.expect_network is True


def test_expectation_for_prefers_reference_over_derived():
    scenario = {"scenario_id": "SIM-EDR-001", "steps": []}
    exp = tc.expectation_for(scenario)
    # Reference wins even though the derived one would be empty.
    assert exp is tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"]


# ─── Docker-gated end-to-end ─────────────────────────────────────────────

def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _docker_available(),
                    reason="docker not available — Tier-C e2e is opt-in")
def test_tier_c_e2e_edr001(tmp_path):
    """Build + run the Tier-C stack against a SIM-EDR-001 push bundle and assert
    the observed signals satisfy the reference expectation.

    Opt-in: requires docker AND a generated bundle. Set CORTEXSIM_TIERC_BUNDLE
    to a pre-generated bundle path, or CORTEXSIM_SMOKE_URL to a live SimCore the
    runner can pull from; otherwise skip (we do not silently fabricate signals).
    """
    import json
    import os

    bundle = os.environ.get("CORTEXSIM_TIERC_BUNDLE")
    simcore = os.environ.get("CORTEXSIM_SMOKE_URL")
    if not bundle and not simcore:
        pytest.skip("set CORTEXSIM_TIERC_BUNDLE or CORTEXSIM_SMOKE_URL to run the "
                    "Tier-C e2e")

    results_dir = tmp_path / "tierc-results"
    cmd = [str(TIER_C / "run-tier-c.sh"), "--results", str(results_dir)]
    if bundle:
        cmd += ["--bundle", bundle]
    else:
        cmd += ["--scenario", "SIM-EDR-001", "--simcore", simcore]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, f"run-tier-c.sh failed:\n{proc.stderr[-2000:]}"

    summaries = list(results_dir.rglob("observed-signals.json"))
    assert summaries, "Tier-C run produced no observed-signals.json"
    observed = json.loads(summaries[0].read_text())

    report = tc.evaluate(tc.REFERENCE_EXPECTATIONS["SIM-EDR-001"], observed)
    assert report.passed, json.dumps(report.to_dict(), indent=2)

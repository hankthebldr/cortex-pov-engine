"""Tier-D — pull-mode agent-execution-path tests.

Tier C detonates a PUSH BUNDLE in an audited container. Tier D exercises the
other execution mode: mint token -> one-liner -> sha256-verified beacon ->
server-assigned agent id -> enrol -> poll -> execute under the identity
harness -> POST output -> complete -> classify.

Two layers, mirroring ``test_tier_c_isolated_exec.py``:

  1. **Pure classifier tests (always run, no docker).** Drive
     ``deploy/tier-d/classify.py`` as a subprocess against synthetic
     ``run.json`` fixtures covering all three verdict classes — OK,
     ENVIRONMENT, and ENGINE — so the ENGINE/ENVIRONMENT/TTP taxonomy (the
     entire point of this harness) is pinned by a test, not just eyeballed on
     a terminal once. In particular this formalises the ENGINE-class negative
     control: a classifier that can only ever return PASS proves nothing, and
     this repo has shipped exactly that mistake before.

  2. **Docker + SimCore-gated end-to-end test (skips cleanly otherwise).**
     Runs the real ``deploy/tier-d/run-tier-d.sh`` against a live SimCore and
     a freshly provisioned target container, and asserts the harness reports
     a genuine pull-mode PASS with zero ENGINE-class steps — and, pinning the
     specific regression this harness exists to catch, that the identity
     harness actually executed (`runuser -l www-data` did not die with
     "This account is currently not available").
"""
from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
import urllib.error

import pytest

REPO_ROOT_MARKERS = ("deploy", "tests", "scenarios")


def _find_repo_root() -> "object":
    import pathlib

    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if all((parent / m).exists() for m in REPO_ROOT_MARKERS):
            return parent
    raise AssertionError("could not locate repo root from " + str(here))


REPO_ROOT = _find_repo_root()
TIER_D = REPO_ROOT / "deploy" / "tier-d"
CLASSIFY = TIER_D / "classify.py"
RUN_TIER_D = TIER_D / "run-tier-d.sh"


# ─── Fixture run.json builders ─────────────────────────────────────────────

def _step(n, total, step_id, tech, identity, body, exit_code):
    header = f"=== STEP {n}/{total} · {step_id} · {tech} · identity={identity} ==="
    return f"{header}\n{body}\n--- exit_code={exit_code} duration=7ms ---"


def _run_json(*, status, tc_verdict, steps_text):
    return {
        "run_id": "fixture-run",
        "scenario_id": "SIM-EDR-001",
        "status": status,
        "tc_verdict": tc_verdict,
        "output": "".join(steps_text),
    }


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def _classify(run_path, out_path, scenario="SIM-EDR-001"):
    """Invoke the real classify.py CLI (subprocess — exercises the exact file
    that ships, same pattern as importing tier_c_assert.py by path)."""
    proc = subprocess.run(
        ["python3", str(CLASSIFY),
         "--run", str(run_path), "--scenario", scenario, "--out", str(out_path)],
        capture_output=True, text=True, timeout=30,
    )
    verdict = json.loads(out_path.read_text()) if out_path.exists() else None
    return proc.returncode, verdict, proc.stdout


# ─── Pure classifier tests (always run) ────────────────────────────────────

def test_classify_all_ok_passes(tmp_path):
    steps = [
        _step(1, 2, "step-01", "T1087.001", "www-data", "--- STDOUT ---\n[*] ok", 0),
        _step(2, 2, "step-02", "T1003.008", "www-data", "--- STDOUT ---\n[*] ok", 0),
    ]
    run_path = tmp_path / "run.json"
    _write(run_path, _run_json(status="complete", tc_verdict="pending", steps_text=steps))

    rc, verdict, _ = _classify(run_path, tmp_path / "verdict.json")
    assert rc == 0
    assert verdict["harness_verdict"] == "PASS"
    assert verdict["counts"] == {"OK": 2, "ENGINE": 0, "ENVIRONMENT": 0, "TTP": 0}
    assert verdict["steps_unreported"] == 0


def test_classify_nologin_is_environment_not_engine(tmp_path):
    """The exact defect that motivated this harness: www-data shipped with
    nologin + no /var/www, so `runuser -l www-data` died in 7ms and the TTP
    never ran. That must classify as ENVIRONMENT (never happened) — NOT
    ENGINE (CortexSim is broken) and NOT a silent OK."""
    body = (
        "--- STDOUT ---\n"
        "This account is currently not available.\n"
        "runuser: warning: cannot change directory to /var/www: No such file or directory\n"
    )
    steps = [_step(1, 5, "step-01", "T1087.001", "www-data", body, 1)]
    run_path = tmp_path / "run.json"
    _write(run_path, _run_json(status="failed", tc_verdict="pending", steps_text=steps))

    rc, verdict, _ = _classify(run_path, tmp_path / "verdict.json")
    assert rc == 0, "ENVIRONMENT-only failures must NOT fail the harness"
    assert verdict["harness_verdict"] == "PASS"
    assert verdict["counts"]["ENVIRONMENT"] == 1
    assert verdict["counts"]["ENGINE"] == 0
    assert verdict["steps"][0]["class"] == "ENVIRONMENT"
    assert verdict["steps_unreported"] == 4


def test_classify_engine_negative_control_fails(tmp_path):
    """A classifier that only ever returns PASS proves nothing. This fixture
    is a genuine ENGINE-class signature (an unhandled beacon-side exception)
    and must both (a) classify as ENGINE and (b) fail the harness — exit 1 —
    without touching any real run or the real beacon."""
    body = (
        "--- STDOUT ---\n"
        "Traceback (most recent call last):\n"
        "  File \"beacon/client.go\", line 905, in resolveIdentity\n"
        "    panic: identity harness: nil pointer dereference resolving spec\n"
    )
    steps = [
        _step(1, 5, "step-01", "T1087.001", "www-data", "--- STDOUT ---\n[*] ok", 0),
        _step(2, 5, "step-02", "T1003.008", "www-data", body, 1),
    ]
    run_path = tmp_path / "run.json"
    _write(run_path, _run_json(status="failed", tc_verdict="pending", steps_text=steps))

    rc, verdict, stdout = _classify(run_path, tmp_path / "verdict.json")
    assert rc == 1, "an ENGINE-class step must fail the harness (exit 1)"
    assert verdict["harness_verdict"] == "FAIL"
    assert verdict["counts"]["ENGINE"] == 1
    assert verdict["steps"][1]["class"] == "ENGINE"
    assert "HARNESS FAIL" in stdout


def test_classify_payload_pin_mismatch_is_engine(tmp_path):
    """A tampered/mismatched staged payload is an integrity failure in
    CortexSim's own supply chain, not a target-environment gap."""
    body = "--- STDOUT ---\nPAYLOAD_PIN_MISMATCH: staged digest does not match declared pin\n"
    steps = [_step(1, 1, "step-01", "T1003", "root", body, 1)]
    run_path = tmp_path / "run.json"
    _write(run_path, _run_json(status="failed", tc_verdict="pending", steps_text=steps))

    rc, verdict, _ = _classify(run_path, tmp_path / "verdict.json")
    assert rc == 1
    assert verdict["counts"]["ENGINE"] == 1
    assert verdict["steps"][0]["reason"].startswith("staged payload digest")


def test_classify_missing_tool_is_environment(tmp_path):
    body = "--- STDOUT ---\nbash: mimipenguin.sh: command not found\n"
    steps = [_step(1, 1, "step-05", "T1003", "root", body, 127)]
    run_path = tmp_path / "run.json"
    _write(run_path, _run_json(status="failed", tc_verdict="pending", steps_text=steps))

    rc, verdict, _ = _classify(run_path, tmp_path / "verdict.json")
    assert rc == 0
    assert verdict["counts"]["ENVIRONMENT"] == 1
    assert verdict["counts"]["ENGINE"] == 0


def test_classify_runtime_dependency_missing_is_environment_not_ok(tmp_path):
    """The defect this repo shipped, made permanent as a regression guard:
    SIM-EDR-001 step-05 downloads mimipenguin.sh, which shells out to python.
    On a target with no python, the OLD code let the step's own
    `|| echo '... complete'` fallback report exit_code=0/OK. The fix
    (agent/beacon/client.go::resolveRuntimeDeps) refuses to run the step's
    real command at all and reports RUNTIME_DEPENDENCY_MISSING with exit 127
    instead — this must classify ENVIRONMENT (the TTP never ran), never OK,
    and must NOT fail the harness (CortexSim itself is not broken)."""
    body = (
        "--- STDERR ---\n"
        "!! RUNTIME_DEPENDENCY_MISSING: python (no interpreter path and no "
        "authorized runtime install)\n"
    )
    steps = [_step(1, 1, "step-05", "T1003", "root", body, 127)]
    run_path = tmp_path / "run.json"
    _write(run_path, _run_json(status="failed", tc_verdict="pending", steps_text=steps))

    rc, verdict, _ = _classify(run_path, tmp_path / "verdict.json")
    assert rc == 0, "an ENVIRONMENT-only gap must not fail the harness"
    assert verdict["counts"] == {"OK": 0, "ENGINE": 0, "ENVIRONMENT": 1, "TTP": 0}
    assert verdict["steps"][0]["class"] == "ENVIRONMENT"
    assert "NEVER executed" in verdict["steps"][0]["reason"]


# ─── Docker + SimCore-gated end-to-end ─────────────────────────────────────

def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _simcore_url() -> str:
    import os

    return os.environ.get("CORTEXSIM_SERVER", "http://localhost:8888")


def _simcore_available() -> bool:
    try:
        with urllib.request.urlopen(f"{_simcore_url()}/api/health", timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


_SKIP_REASON = None
if not _docker_available():
    _SKIP_REASON = "docker not available — Tier-D e2e is opt-in"
elif not _simcore_available():
    _SKIP_REASON = f"SimCore unreachable at {_simcore_url()} — Tier-D e2e is opt-in"


@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
def test_tier_d_e2e_edr001_pull_mode(tmp_path):
    """Build the provisioned target, enrol a real beacon via the real
    installer one-liner, launch SIM-EDR-001 in pull mode, and assert a clean
    PASS with zero ENGINE-class steps and the identity-harness regression
    (www-data/nologin) genuinely fixed on the target."""
    results_dir = tmp_path / "tierd-results"
    proc = subprocess.run(
        [str(RUN_TIER_D), "--scenario", "SIM-EDR-001", "--results", str(results_dir)],
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, (
        f"run-tier-d.sh exited {proc.returncode} (expected 0 = no ENGINE-class "
        f"failure)\n--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
    )

    verdict_path = results_dir / "verdict.json"
    assert verdict_path.exists(), "run-tier-d.sh did not produce verdict.json"
    verdict = json.loads(verdict_path.read_text())

    assert verdict["harness_verdict"] == "PASS", json.dumps(verdict, indent=2)
    assert verdict["counts"]["ENGINE"] == 0
    assert verdict["steps_reported"] == verdict["steps_declared"]
    assert verdict["steps_unreported"] == 0

    # Pin the specific regression this harness exists to catch: step-01 runs
    # under www-data and must NOT die with the nologin/no-home-dir signature.
    run_json = json.loads((results_dir / "run.json").read_text())
    output = run_json.get("output") or ""
    assert "This account is currently not available" not in output, (
        "www-data/nologin regression reproduced — the provisioned target did "
        "not fix the identity-harness defect this harness exists to catch"
    )
    step1 = next(s for s in verdict["steps"] if s["n"] == 1)
    assert step1["identity"] == "www-data"
    assert step1["class"] == "OK"

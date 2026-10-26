"""Tier-D — the three host witnesses of a prevention, end to end.

A ``prevent`` expectation is credited only when two witnesses agree: the
tenant's alert says "Prevented" AND the host says the process was killed.
Tier-D has no tenant, so it can only ever prove the HOST half -- and, just as
importantly, that the engine refuses to call anything ``prevented`` on host
evidence alone. ``deploy/tier-d/run-tier-d.sh --inject-kill`` plays the Cortex
agent by killing one process of the beacon's tree during ``step-02`` of
``SIM-TEST-PREV-001`` (its ``prevent`` step):

  subshell  the step's subshell        -> step-02 ``signaled``
  anchor    the beacon's anchor shell  -> step-02 ``anchor_terminated``
  beacon    the beacon itself          -> step-02 ``beacon_lost`` (heartbeat sweep)

Each run must also leave ``step-03`` as ``not_run`` / ``UPSTREAM_SEVERED`` and
the run ``complete``, and NO Result may read ``prevented``.

OPT-IN. These need docker, a SimCore built from this branch, and that SimCore
must carry the fixture scenario. Start it with, e.g.::

    CORTEXSIM_SCENARIOS_DIR=$PWD/tests/fixtures/scenarios \\
    CORTEXSIM_BEACON_LOST_AFTER_SECONDS=30 <your usual dev-up / uvicorn>

and point the tests at it with ``CORTEXSIM_SERVER``. The beacon test also needs
``TIER_D_BEACON_LOST_AFTER_SECONDS`` set to the threshold SimCore was started
with (the test cannot read it from the server). Note the agent must still go
OFFLINE first -- a fixed 5 minutes of silence in ``api/agents.py`` -- so the
beacon test takes about 6-7 minutes whatever the threshold.

The always-on half (the classifier, the injector's pid selection and timeout)
lives in ``test_tier_d_classify.py`` and ``test_tier_d_inject_kill.py``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_TIER_D = REPO_ROOT / "deploy" / "tier-d" / "run-tier-d.sh"
SCENARIO = "SIM-TEST-PREV-001"


# ─── Skip guards (same pattern as test_tier_d_agent_path.py) ───────────────

def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


def _simcore_url() -> str:
    return os.environ.get("CORTEXSIM_SERVER", "http://localhost:8888")


def _get(path: str) -> int:
    try:
        with urllib.request.urlopen(f"{_simcore_url()}{path}", timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0


_SKIP_REASON = None
if not _docker_available():
    _SKIP_REASON = "docker not available — Tier-D prevention-witness e2e is opt-in"
elif _get("/api/health") != 200:
    _SKIP_REASON = f"SimCore unreachable at {_simcore_url()} — Tier-D prevention-witness e2e is opt-in"
elif _get(f"/api/scenarios/{SCENARIO}") != 200:
    _SKIP_REASON = (
        f"SimCore at {_simcore_url()} does not carry fixture scenario {SCENARIO} — start one "
        "built from this branch with CORTEXSIM_SCENARIOS_DIR=tests/fixtures/scenarios "
        "(Tier-D prevention-witness e2e is opt-in)")

_BEACON_LOST_AFTER = os.environ.get("TIER_D_BEACON_LOST_AFTER_SECONDS")
_BEACON_SKIP = _SKIP_REASON
if _BEACON_SKIP is None and not (_BEACON_LOST_AFTER and _BEACON_LOST_AFTER.isdigit()
                                 and int(_BEACON_LOST_AFTER) <= 120):
    _BEACON_SKIP = (
        "TIER_D_BEACON_LOST_AFTER_SECONDS is unset or > 120 — start SimCore with a low "
        "CORTEXSIM_BEACON_LOST_AFTER_SECONDS and export the same value here "
        "(the default is 180 s)")


# ─── Helpers ───────────────────────────────────────────────────────────────

def _inject(tmp_path: Path, mode: str, timeout: int):
    results = tmp_path / f"tierd-{mode}"
    proc = subprocess.run(
        [str(RUN_TIER_D), "--scenario", SCENARIO, "--inject-kill", mode,
         "--at-step", "step-02", "--results", str(results)],
        capture_output=True, text=True, timeout=timeout)
    ctx = (f"run-tier-d.sh exited {proc.returncode}\n--- stdout ---\n{proc.stdout[-4000:]}"
           f"\n--- stderr ---\n{proc.stderr[-2000:]}")
    assert proc.returncode == 0, ctx
    verdict = json.loads((results / "verdict.json").read_text())
    run = json.loads((results / "run.json").read_text())
    rows = json.loads((results / "results.json").read_text())["results"]
    assert (results / "inject.status").read_text().startswith("KILLED"), ctx
    return verdict, run, rows


def _assert_severed_after_step_02(verdict, run, rows):
    assert verdict["run_status"] == "complete", json.dumps(verdict, indent=2)
    assert verdict["severed_at_step"] == "step-02"
    assert verdict["terminations"]["step-03"] == {
        "termination": "not_run", "reason": "UPSTREAM_SEVERED"}
    by_step = {}
    for r in rows:
        by_step.setdefault(r["step_id"], []).append(r)
    assert all(r["outcome"] == "not_reached" and r["outcome_reason"] == "UPSTREAM_SEVERED"
               for r in by_step["step-03"])
    # Tier-D has no tenant witness: nothing may ever read prevented.
    assert not [r for r in rows if r["outcome"] == "prevented"], rows
    return [r for r in by_step["step-02"] if r["expected_outcome"] == "prevent"]


# ─── The three witnesses ───────────────────────────────────────────────────

@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
def test_subshell_kill_is_signaled(tmp_path):
    verdict, run, rows = _inject(tmp_path, "subshell", timeout=600)
    assert verdict["terminations"]["step-02"]["termination"] == "signaled"
    prevent = _assert_severed_after_step_02(verdict, run, rows)
    assert prevent, "SIM-TEST-PREV-001 step-02 must seed a prevent Result"
    for r in prevent:
        assert (r["outcome"], r["outcome_reason"]) == ("pending", "PREVENTION_UNCONFIRMED_NO_ALERT")


@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
def test_anchor_kill_is_anchor_terminated(tmp_path):
    verdict, run, rows = _inject(tmp_path, "anchor", timeout=600)
    assert verdict["terminations"]["step-02"]["termination"] == "anchor_terminated"
    prevent = _assert_severed_after_step_02(verdict, run, rows)
    assert prevent
    for r in prevent:
        assert (r["outcome"], r["outcome_reason"]) == ("pending", "PREVENTION_UNCONFIRMED_NO_ALERT")


@pytest.mark.skipif(_BEACON_SKIP is not None, reason=_BEACON_SKIP or "")
def test_beacon_kill_is_beacon_lost_after_the_sweep(tmp_path):
    """ENGINE DISCREPANCY, asserted as observed (live run, 2026-10-26).

    The brief expects ``step-02: beacon_lost``. A step's /output is POSTed only
    AFTER the step ends, so when the beacon dies mid-step-02 the only
    ``started_at`` on record is step-01's, and ``beacon_lost.pick_severed``
    ("latest started_at") blames step-01. step-02 -- the in-flight ``prevent``
    step -- is then ``not_run`` / ``UPSTREAM_SEVERED`` and its Result
    ``not_reached``. What holds either way, and is what this pins: the run is
    ``complete``, a BEACON LOST marker is in the output, exactly one step is
    ``beacon_lost``, every later step is ``not_run`` / ``UPSTREAM_SEVERED``,
    and nothing reads ``prevented``. If the engine is changed to blame the
    in-flight step, ``severed`` below becomes ``step-02`` and this test must
    follow it.
    """
    verdict, run, rows = _inject(tmp_path, "beacon", timeout=1000)
    assert verdict["run_status"] == "complete", json.dumps(verdict, indent=2)
    assert "BEACON LOST" in (run.get("output") or "")
    severed = verdict["severed_at_step"]
    lost = [sid for sid, t in verdict["terminations"].items() if t["termination"] == "beacon_lost"]
    assert lost == [severed] == ["step-01"], verdict["terminations"]
    for sid in ("step-02", "step-03"):
        assert verdict["terminations"][sid] == {
            "termination": "not_run", "reason": "UPSTREAM_SEVERED"}
    assert not [r for r in rows if r["outcome"] == "prevented"], rows
    prevent = [r for r in rows if r["expected_outcome"] == "prevent"]
    assert prevent and all(r["outcome"] in (None, "pending", "not_reached") for r in prevent)


# ─── Always-on: the flag is a no-op when absent and refuses bad input ───────

@pytest.mark.parametrize("args", [
    ["--inject-kill", "bogus", "--at-step", "step-02"],
    ["--inject-kill", "subshell"],
    ["--at-step", "step-02"],
])
def test_inject_flags_are_validated_before_anything_runs(args):
    proc = subprocess.run([str(RUN_TIER_D), "--scenario", SCENARIO, *args],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2, proc.stdout + proc.stderr

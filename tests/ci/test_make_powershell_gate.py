"""``make validate`` and ``make ci`` run the PowerShell 5.1 floor gate.

``make check-powershell`` renders every PowerShell artifact the repo hands to
Windows and parses it the way Windows PowerShell 5.1 reads it. The refs CI job
ran it; the two make aggregates did not, so `make ci` could be green on a tree
whose refs job would go red on push.

It is in both aggregates now, and three things about it are pinned here:

* With no pwsh the gate FAILS and says why. A gate that quietly does not run
  reads exactly like one that passed.
* There is exactly one opt-out, ``CORTEXSIM_SKIP_POWERSHELL_GATE=1``, for a host
  that cannot have PowerShell 7. It applies to the aggregates only, it is said
  where the gate would have run and again as the aggregate's last line, and any
  other value runs the gate.
* No workflow may set it.

pwsh 7 is not Windows PowerShell 5.1. The gate is necessary, not sufficient,
and nothing here is evidence that anything executes on Windows.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.ci._wiring import REPO, SKIP_VAR, make_env, make_n, require_make, workflow_files


@pytest.mark.parametrize("aggregate", ["validate", "ci"])
def test_make_aggregate_runs_the_powershell_floor_gate(aggregate):
    out = make_n(aggregate)
    assert "scripts/ci/render_powershell.py" in out, f"`make {aggregate}` never renders the PowerShell artifacts"
    assert "scripts/ci/ps51-gate.ps1" in out, f"`make {aggregate}` never runs the 5.1 floor gate"
    assert "SKIPPED" not in out


def test_the_powershell_gate_runs_after_the_older_validate_gates():
    """It is the only gate in `validate` with host prerequisites beyond docker
    and python (pwsh, Go). In front, a host without them would never reach the
    gates that were there first."""
    out = make_n("validate")
    gate = out.find("scripts/ci/render_powershell.py")
    for older in ("detection_scanner/scripts/validate.py",
                  "tests/engine/test_corpus_refs_strict.py",
                  "scripts/check-adapter-sources.sh",
                  "scripts/generate_ground_truth.py"):
        assert 0 <= out.find(older) < gate, older


@pytest.mark.parametrize("aggregate", ["validate", "ci"])
def test_the_named_skip_variable_is_the_only_way_past_the_gate_and_it_is_loud(aggregate):
    out = make_n(aggregate, f"{SKIP_VAR}=1")
    assert "scripts/ci/ps51-gate.ps1" not in out
    # Said where the gate would have run AND again as the aggregate's last word,
    # so the final line on screen is never a clean-looking pass.
    assert out.count("SKIPPED") >= 2, out
    assert SKIP_VAR in out
    last = [ln for ln in out.splitlines() if ln.strip()][-1]
    assert "SKIPPED" in last, last
    # Any other value is not the opt-out.
    for value in ("0", "true", "yes", ""):
        assert "scripts/ci/ps51-gate.ps1" in make_n(aggregate, f"{SKIP_VAR}={value}"), value


def test_check_powershell_fails_loudly_without_pwsh_even_with_the_skip_variable():
    """The opt-out belongs to the aggregates. `make check-powershell` itself (what
    the refs job runs) never skips: with no pwsh it fails and says why. This one
    EXECUTES the target; it stops at its first line."""
    make = require_make()
    for extra in ([], [f"{SKIP_VAR}=1"]):
        proc = subprocess.run(
            [make, "-C", str(REPO), "check-powershell", "PWSH=cortexsim-no-such-pwsh", *extra],
            capture_output=True, text=True, timeout=120, env=make_env())
        said = proc.stdout + proc.stderr
        assert proc.returncode != 0, said
        assert "no 'cortexsim-no-such-pwsh' on PATH" in said
        assert "a skipped gate reads as a pass" in said


def test_no_workflow_sets_the_powershell_skip_variable():
    for path in workflow_files():
        assert SKIP_VAR not in path.read_text(encoding="utf-8"), (
            f"{path.name} sets {SKIP_VAR}: a CI job that skips the gate reads exactly like one that passed it")

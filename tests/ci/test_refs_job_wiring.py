"""The ``refs`` CI job: several gates, none of which may mask another (RG-CI-04).

The job runs the PowerShell 5.1 floor gate, the strict UC/TC ref walk and the
determinism gates for the generated reference files.

The PowerShell gate was inserted in front of the UC/TC and ground-truth gates
with no ``if:`` on anything behind it, so one red PowerShell finding (or a
runner image without a usable Go) skipped them: a dangling ``tc_ref`` in the
same push went unreported until the PowerShell problem was fixed. It also
rendered the beacon's wrapper with whatever Go the runner image shipped,
because the job had no ``actions/setup-go``.

Every gate behind the dependency install now carries ``!cancelled()`` plus the
outcome of the step it actually needs, so a gate neither hides behind an
unrelated failure nor runs vacuously after its own prerequisite failed.
"""
from __future__ import annotations

from tests.ci._wiring import ci_jobs, condition, step_index


def test_refs_job_renders_with_the_go_the_agent_job_pins():
    """The PowerShell gate renders the beacon's wrapper with `go test`. Without
    setup-go it used whatever Go the runner image shipped that week."""
    jobs = ci_jobs()
    refs, agent = jobs["refs"], jobs["agent"]

    def _setup_go(job: dict) -> list[dict]:
        return [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-go@")]

    refs_go, agent_go = _setup_go(refs), _setup_go(agent)
    assert len(agent_go) == 1
    assert len(refs_go) == 1, "refs has no actions/setup-go step: the render uses the runner image's Go"
    # The same expression, not merely a compatible one: CI-11 (which Go ships)
    # is undecided, and until it is every job must agree.
    assert str(refs_go[0]["with"]["go-version"]) == str(agent_go[0]["with"]["go-version"])
    assert step_index(refs, "actions/setup-go@") < step_index(refs, "make check-powershell")


def test_a_red_powershell_gate_cannot_skip_the_gates_behind_it():
    refs = ci_jobs()["refs"]
    gate = step_index(refs, "make check-powershell")
    later = refs["steps"][gate + 1:]
    assert later, "the refs job has no gate after the PowerShell one -- did the UC/TC gate move?"
    masked = [s.get("name") or s.get("run") for s in later
              if "!cancelled()" not in condition(s) and "always()" not in condition(s)]
    assert masked == [], (
        "these refs steps are skipped whenever `make check-powershell` fails, so a "
        f"dangling tc_ref or a drifted inventory in the same push goes unreported: {masked}")


def test_refs_gates_do_not_run_vacuously_after_a_failed_prerequisite():
    """`!cancelled()` alone would run the strict-refs pytest after a failed pip
    install, and the ground-truth diff after a failed regeneration (where it is
    trivially clean). Each gate names the step it depends on."""
    refs = ci_jobs()["refs"]
    steps = refs["steps"]
    deps = steps[step_index(refs, "pip install -r core/requirements.txt")]
    assert deps.get("id"), "the dependency-install step needs an id the gates can reference"
    dep_ok = f"steps.{deps['id']}.outcome == 'success'"
    for needle in ("tests/engine/test_corpus_refs_strict.py", "scripts/generate_ground_truth.py"):
        assert dep_ok in condition(steps[step_index(refs, needle)]), needle

    regen = steps[step_index(refs, "scripts/generate_ground_truth.py")]
    assert regen.get("id"), "the regenerate step needs an id the diff gate can reference"
    diff = steps[step_index(refs, "git diff --exit-code docs/reference/ground-truth.json")]
    assert f"steps.{regen['id']}.outcome == 'success'" in condition(diff), (
        "a ground-truth diff that runs after a FAILED regeneration compares the "
        "committed files with themselves and reads green")


def test_refs_job_has_no_soft_failing_step():
    refs = ci_jobs()["refs"]
    assert "continue-on-error" not in refs
    assert all("continue-on-error" not in s for s in refs["steps"])

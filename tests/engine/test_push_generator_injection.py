"""The push generators must not let a step's id/identity inject shell code.

``generate_bash`` escapes the step COMMAND before embedding it in the
``run_as '<identity>' '<cmd>' '<step_id>'`` call, but left ``identity`` and
``step_id`` unescaped. A single quote in either breaks out of its single-quoted
argument and injects a top-level command into the bundle a DC runs on a
customer host — and ``bash -n`` stays clean, so the injection is invisible to
the emittability/syntax checks. ``generate_powershell`` has the same shape in
its ``Invoke-CsStep -StepId '…' -Identity '…'`` call.

Step ids/identities come from scenario YAML, which the loader is the trust
boundary for; the generator must still not emit an injectable artifact.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from engine.push_generator import generate_bash, generate_powershell


def test_bash_step_id_cannot_inject_a_command():
    marker = os.path.join(tempfile.gettempdir(), "cortexsim-inject-marker-bash")
    if os.path.exists(marker):
        os.remove(marker)
    # A step id crafted to break out of the single-quoted run_as argument.
    evil_id = f"step-01'; touch {marker} #"
    scenario = {
        "scenario_id": "SIM-INJ-BASH",
        "name": "injection probe",
        "steps": [{"id": evil_id, "name": "n", "identity": "direct", "command": "true"}],
    }
    script = generate_bash(scenario)
    try:
        subprocess.run(["bash", "-c", script], text=True, capture_output=True, timeout=60)
        assert not os.path.exists(marker), (
            "an unescaped step id broke out of its run_as argument and executed "
            "injected shell in the generated bundle"
        )
    finally:
        if os.path.exists(marker):
            os.remove(marker)


def test_bash_identity_is_escaped_in_the_run_as_call():
    # Asserted at the string level, not by execution: a quote in the identity is
    # an injection vector just like the step id, but a payload that also breaks
    # run_as's argument count is neutralised by set -u/-e for the wrong reason.
    # The contract is simply that the quote never survives un-escaped.
    evil_identity = "direct'; touch /tmp/x #"
    scenario = {
        "scenario_id": "SIM-INJ-IDENT",
        "name": "injection probe",
        "steps": [{"id": "step-01", "name": "n", "identity": evil_identity, "command": "true"}],
    }
    script = generate_bash(scenario)
    run_as_line = next(l for l in script.splitlines() if l.startswith("run_as "))
    assert f"run_as '{evil_identity}'" not in run_as_line, (
        "the identity was embedded in the run_as call without escaping its quote"
    )
    # The bash single-quote escape is '\'' — the identity's quote must be so escaped.
    assert "direct'\\''; touch /tmp/x #" in run_as_line


def test_powershell_step_id_is_escaped_in_the_invoke_call():
    # A Windows-emittable scenario with a quote in the step id. The generated
    # PowerShell must not let that quote close the -StepId '…' literal: the quote
    # has to be doubled (PowerShell's single-quote escape).
    evil_id = "step-01'; Remove-Item x #"
    scenario = {
        "scenario_id": "SIM-INJ-PS",
        "name": "injection probe",
        "steps": [{
            "id": evil_id, "name": "n", "identity": "direct",
            "platforms": ["windows"],
            "platform_variants": {"windows": "Get-Process"},
        }],
    }
    script = generate_powershell(scenario)
    # The raw, un-doubled quote sequence must never appear in the invoke call.
    assert f"-StepId '{evil_id}'" not in script, (
        "the step id was interpolated into the PowerShell -StepId literal without "
        "escaping its single quote"
    )
    # The escaped form (quote doubled) must be present instead.
    assert "step-01''; Remove-Item x #" in script

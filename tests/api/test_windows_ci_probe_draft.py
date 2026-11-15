"""Linux-side guard for the ``windows`` CI job (lane: windows-native-ci).

The ``windows`` job in ``.github/workflows/ci.yml`` runs on a windows-2022
runner and drives ``scripts/ci/windows-native-e2e.ps1`` phase by phase: native
``go test``, a Windows PowerShell 5.1 parse + PSScriptAnalyzer pass, a real
SimCore boot, the console's enrollment one-liner, and a pull / abort / push /
uninstall round trip against a beacon installed as a Windows service.

None of that can execute on Linux or macOS. What CAN be proven here, and is,
is everything the Windows job silently depends on being true before it starts:

  * the three CI draft fixtures it POSTs are legal drafts
    (``DraftScenarioSchema``), are LAUNCHABLE (bound to a real FY27 index test
    case, every step carries the expected detection the launch gate requires),
    declare ``platforms: [windows]`` on every step, and resolve to a Windows
    push bundle for EVERY step with the dialect the job asserts on;
  * the probe's step ORDER is the one the job's assertions are written against
    (fail-fast would otherwise skip the stderr step and the BEACON-05 question
    would go unanswered);
  * the job exists, cannot be skipped (no ``if:``, no ``paths:`` filter), cannot
    soft-fail (no ``continue-on-error``), and runs every phase the script
    declares;
  * the script is ASCII and parses, and uses none of the PowerShell 7 syntax
    that Windows PowerShell 5.1 (the product floor) cannot parse.

A green run of this file is NOT Windows proof. Nothing here has executed on
Windows; the windows CI job is the only place that can, and it has not run
until Henry pushes. tenant-verified stays 0: no draft here is ever launched
against a Cortex tenant, and the expected detections in the fixtures are
placeholders the launch gate demands, not claims about coverage.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCRIPT = REPO_ROOT / "scripts" / "ci" / "windows-native-e2e.ps1"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
TEST_YML = REPO_ROOT / ".github" / "workflows" / "test.yml"

#: role -> fixture file. The script reads each one by this exact role name.
DRAFT_FIXTURES = {
    "probe": FIXTURES / "windows_ci_probe_draft.json",
    "cmdlet_error": FIXTURES / "windows_ci_cmdlet_error_draft.json",
    "abort": FIXTURES / "windows_ci_abort_draft.json",
}

#: The probe order the job's pull assertions are written against. The stderr
#: step (step-04) runs BEFORE the first non-zero step (step-02): the beacon is
#: fail-fast, so a stderr step placed after step-02 would be not_run and
#: BEACON-05 (does a native writing stderr and exiting 0 read as rc 0 on 5.1?)
#: would never be asked. step-03 is last so its not_run/UPSTREAM_FAILED row is
#: the POSIX fail-fast parity check; its own rc is proven by cmdlet_error.
PROBE_ORDER = ["step-01", "step-04", "step-02", "step-03"]

#: The Windows dialect the push bundle must pick per probe step. The job runs
#: the bundle and asserts on both Invoke-CsShell branches, so a drift here (a
#: cmd step silently re-classified as PowerShell) would change what the job
#: proves without changing what it reports.
PROBE_SHELLS = {
    "step-01": "powershell",
    "step-04": "cmd",
    "step-02": "cmd",
    "step-03": "powershell",
}

#: Every phase the job must run, in order. Kept here AND in the script's
#: ValidateSet AND in ci.yml; the tests below hold the three equal.
PHASES = [
    "floor", "go", "build", "render", "parse", "pssa", "boot",
    "install", "pull", "abort", "push", "uninstall", "collect",
]


def _load_fixture(role: str) -> dict:
    path = DRAFT_FIXTURES[role]
    assert path.is_file(), f"missing CI draft fixture for role {role!r}: {path}"
    raw = path.read_bytes()
    # The job POSTs these bytes verbatim from Windows PowerShell 5.1. ASCII
    # means no code-page question can arise between the file, 5.1 and SimCore.
    non_ascii = [i for i, b in enumerate(raw) if b > 0x7E or (b < 0x20 and b not in (0x0A, 0x0D))]
    assert not non_ascii, f"{path.name} must be printable ASCII; first offending byte at {non_ascii[0]}"
    return json.loads(raw.decode("ascii"))


# ---------------------------------------------------------------------------
# The draft fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", sorted(DRAFT_FIXTURES))
def test_fixture_is_a_valid_draft(role):
    from engine.composer_draft_schema import DraftScenarioSchema  # noqa: PLC0415

    body = _load_fixture(role)
    draft = DraftScenarioSchema.model_validate(body)
    # No unknown keys: DraftScenarioSchema ignores extras, so a typo'd field in
    # a fixture would be silently dropped and the job would test something
    # other than what the fixture says. Fail on it instead.
    known = set(DraftScenarioSchema.model_fields)
    assert set(body) <= known, f"{role}: unknown draft keys {sorted(set(body) - known)}"
    step_known = set(type(draft.steps[0]).model_fields)
    for step in body["steps"]:
        assert set(step) <= step_known, f"{role}/{step['id']}: unknown step keys {sorted(set(step) - step_known)}"


@pytest.mark.parametrize("role", sorted(DRAFT_FIXTURES))
def test_every_step_is_windows_and_carries_the_launch_gate_detection(role):
    body = _load_fixture(role)
    assert body["steps"], role
    for step in body["steps"]:
        assert step.get("platforms") == ["windows"], (role, step["id"], step.get("platforms"))
        dets = step.get("expected_detections") or []
        # The D5 launch gate (_chain_valid) refuses a step with no expected
        # detection, so every step needs one. They are placeholders and must
        # say so: a fixture must never read as a coverage claim.
        assert dets, f"{role}/{step['id']}: the launch gate needs >=1 expected detection per step"
        for det in dets:
            assert "placeholder" in det["description"].lower(), (role, step["id"], det)
        # Pull runs every non-direct identity as direct on Windows anyway; the
        # fixtures declare nothing so the run record carries no degradation.
        assert "identity" not in step, (role, step["id"])


def test_probe_step_order_is_the_one_the_job_asserts_on():
    body = _load_fixture("probe")
    assert [s["id"] for s in body["steps"]] == PROBE_ORDER


def test_probe_commands_are_the_ones_the_job_asserts_on():
    steps = {s["id"]: s["command"] for s in _load_fixture("probe")["steps"]}
    assert steps["step-01"] == (
        'Set-Content -LiteralPath "$env:ProgramData\\CortexSim\\ci-marker.txt" '
        "-Value ok; Write-Output cortexsim-ci-marker"
    )
    assert steps["step-02"] == "cmd.exe /c exit 3"
    assert steps["step-03"] == "Get-Process -Name cortexsim-no-such-process"
    assert steps["step-04"] == 'cmd.exe /c "echo progress 1>&2 & exit 0"'
    cmdlet = _load_fixture("cmdlet_error")["steps"]
    assert [s["command"] for s in cmdlet] == [steps["step-03"]]
    abort = _load_fixture("abort")["steps"]
    assert len(abort) == 1 and "ping -n 600 127.0.0.1" in abort[0]["command"]


@pytest.mark.parametrize("role", sorted(DRAFT_FIXTURES))
def test_every_step_resolves_to_a_windows_push_bundle(role):
    from engine.composer_draft_schema import DraftScenarioSchema, draft_to_orm_kwargs  # noqa: PLC0415
    from engine.push_generator import emittable_targets, resolve_target  # noqa: PLC0415

    kwargs = draft_to_orm_kwargs(DraftScenarioSchema.model_validate(_load_fixture(role)))
    doc = {"scenario_id": kwargs["scenario_id"], "steps": kwargs["steps"], "cleanup": kwargs["cleanup"]}
    res = resolve_target(doc, "windows")
    assert res.emittable, f"{role}: unresolved for windows: {res.unresolved}"
    assert res.unresolved == ()
    assert [r.step_id for r in res.steps] == [s["id"] for s in kwargs["steps"]]
    # Primary command text, never the shell-neutral fallback: the job is about
    # the Windows dialects, and a neutral echo would prove neither.
    assert all(r.source == "primary" for r in res.steps), [(r.step_id, r.source) for r in res.steps]
    assert "windows" in emittable_targets(doc)
    # No step is POSIX-runnable: a probe a Linux bundle could also carry would
    # not be a Windows probe.
    assert not resolve_target(doc, "posix").emittable, role
    if role == "probe":
        assert {r.step_id: r.shell for r in res.steps} == PROBE_SHELLS


@pytest.mark.parametrize("role", sorted(DRAFT_FIXTURES))
def test_fixture_saves_and_is_launchable_through_the_real_drafts_route(role, make_client):
    """POST the fixture through the drafts router exactly as the job does. A
    201 that is not launchable would 409 DRAFT_NOT_TC_BOUND / CHAIN_INVALID at
    POST /api/runs on the Windows runner, after the beacon install had already
    spent five minutes, and read as a beacon failure."""
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415
    from api.drafts import router  # noqa: PLC0415

    n = registry.load(default_index_dir(os.environ.get("CORTEXSIM_BASE_DIR", str(REPO_ROOT))))
    # The snapshot is committed to this repo. If it does not load, the binding
    # cannot be proven and that is a failure here, never a skip: a skipped
    # guard reads exactly like a passing one.
    assert registry.loaded and n > 0, "UC/TC index snapshot did not load; launchability is unprovable"

    client = make_client(router)
    resp = client.post(
        "/api/scenarios/drafts",
        content=DRAFT_FIXTURES[role].read_bytes(),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["status"] == "draft"
    assert doc["launchable"]["launchable"] is True, doc["launchable"]
    assert doc["launchable"]["tc_bound"] is True, doc["launchable"]
    assert doc["push_supported"] is True
    assert doc["pull_supported"] is True


# ---------------------------------------------------------------------------
# The script
# ---------------------------------------------------------------------------


def _script_text() -> str:
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    raw = SCRIPT.read_bytes()
    bad = [i for i, b in enumerate(raw) if b > 0x7E or (b < 0x20 and b not in (0x09, 0x0A, 0x0D))]
    # ASCII, so -File (BOM-less => ANSI code page on 5.1) and a UTF-8 reader see
    # the same bytes: a UTF-8 em dash read as cp1252 contains 0x94, which
    # PowerShell treats as a closing double quote.
    assert not bad, f"{SCRIPT.name} must be ASCII; first offending byte at offset {bad[0]}"
    return raw.decode("ascii")


def test_script_phase_set_matches_the_lane_contract():
    text = _script_text()
    m = re.search(r"\[ValidateSet\(([^)]*)\)\]\s*\[string\]\$Phase", text)
    assert m, "the script must declare [ValidateSet(...)][string]$Phase"
    declared = re.findall(r"'([a-z]+)'", m.group(1))
    assert declared == PHASES


def test_script_reads_exactly_the_fixture_roles():
    text = _script_text()
    roles = set(re.findall(r"Get-CsFixtureDraftPath\s+-Role\s+'([a-z_]+)'", text))
    assert roles == set(DRAFT_FIXTURES), roles
    for path in DRAFT_FIXTURES.values():
        assert path.name in text, f"script does not name {path.name}"


_PS7_ONLY_CHECK = r"""
$ErrorActionPreference = 'Stop'
$path = $args[0]
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
$out = New-Object System.Collections.Generic.List[string]
foreach ($e in $errors) { $out.Add('PARSE: ' + $e.Message + ' @' + $e.Extent.StartLineNumber) }
$types = @('PipelineChainAst', 'TernaryExpressionAst')
foreach ($n in $types) {
  $t = [type]('System.Management.Automation.Language.' + $n)
  foreach ($hit in $ast.FindAll({ param($a) $t.IsInstanceOfType($a) }.GetNewClosure(), $true)) {
    $out.Add('PS7-SYNTAX: ' + $n + ' @' + $hit.Extent.StartLineNumber)
  }
}
foreach ($tok in $tokens) {
  if ($tok.Kind -in @('QuestionQuestion', 'QuestionQuestionEquals', 'QuestionDot', 'QuestionLBracket', 'AndAnd', 'OrOr')) {
    $out.Add('PS7-TOKEN: ' + $tok.Kind + ' @' + $tok.Extent.StartLineNumber)
  }
}
$ps7Vars = @('IsWindows', 'IsLinux', 'IsMacOS', 'IsCoreCLR')
foreach ($v in $ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
  if ($ps7Vars -contains $v.VariablePath.UserPath) { $out.Add('PS7-VAR: $' + $v.VariablePath.UserPath + ' @' + $v.Extent.StartLineNumber) }
}
$ps7Params = @{
  'ConvertFrom-Json' = @('AsHashtable', 'Depth', 'NoEnumerate');
  'Invoke-WebRequest' = @('SkipHttpErrorCheck', 'StatusCodeVariable', 'SkipCertificateCheck', 'Resume', 'Authentication');
  'Invoke-RestMethod' = @('SkipHttpErrorCheck', 'StatusCodeVariable', 'SkipCertificateCheck', 'Resume', 'Authentication');
  'Get-Content' = @('AsByteStream');
  'Set-Content' = @('AsByteStream');
  'Join-Path' = @('AdditionalChildPath');
  'ForEach-Object' = @('Parallel', 'ThrottleLimit');
  'Start-Process' = @('Environment');
}
foreach ($c in $ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.CommandAst] }, $true)) {
  $name = $c.GetCommandName()
  if (-not $name -or -not $ps7Params.ContainsKey($name)) { continue }
  foreach ($el in $c.CommandElements) {
    if ($el -is [System.Management.Automation.Language.CommandParameterAst] -and ($ps7Params[$name] -contains $el.ParameterName)) {
      $out.Add('PS7-PARAM: ' + $name + ' -' + $el.ParameterName + ' @' + $el.Extent.StartLineNumber)
    }
  }
  foreach ($el in $c.CommandElements) {
    if ($el -is [System.Management.Automation.Language.StringConstantExpressionAst] -and $el.Value -match '^(utf8NoBOM|utf8BOM)$') {
      $out.Add('PS7-ENCODING: ' + $el.Value + ' @' + $el.Extent.StartLineNumber)
    }
  }
}
$out | ForEach-Object { Write-Output $_ }
Write-Output ('CHECKED tokens=' + $tokens.Count)
"""


def test_script_parses_and_uses_no_powershell7_only_syntax(tmp_path):
    """pwsh is NOT Windows PowerShell 5.1, so a pwsh parse alone is necessary
    and not sufficient: 5.1 rejects ``&&``, ``??``, ``?.`` and ternaries that
    pwsh accepts. This walks the AST pwsh produced for the constructs 5.1
    cannot parse and the PS7-only parameters 5.1 does not have."""
    _script_text()
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        if os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1":
            pytest.fail("CORTEXSIM_REQUIRE_POWERSHELL=1 but pwsh is not on PATH")
        pytest.skip("pwsh not on PATH (set CORTEXSIM_REQUIRE_POWERSHELL=1 to make this a failure)")
    checker = tmp_path / "check.ps1"
    checker.write_text(_PS7_ONLY_CHECK, encoding="ascii")
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(checker), str(SCRIPT)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines and lines[-1].startswith("CHECKED tokens="), proc.stdout + proc.stderr
    # A walk over zero tokens proves nothing.
    assert int(lines[-1].split("=", 1)[1]) > 1000, lines[-1]
    findings = lines[:-1]
    assert findings == [], "\n".join(findings)


# ---------------------------------------------------------------------------
# The workflows
# ---------------------------------------------------------------------------


def _wf(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # PyYAML (YAML 1.1) reads the bare key `on` as boolean True.
    return doc.get("on", doc.get(True))


def _runs(job: dict) -> list[str]:
    return [s.get("run", "") for s in job.get("steps", [])]


def _step_index(job: dict, needle: str) -> int:
    for i, s in enumerate(job.get("steps", [])):
        if needle in (s.get("run") or "") or needle in (s.get("name") or ""):
            return i
    return -1


def test_windows_job_exists_and_cannot_be_skipped_or_soft_fail():
    ci = _wf(CI_YML)
    job = ci["jobs"].get("windows")
    assert job is not None, "ci.yml has no `windows` job"
    assert job["runs-on"] == "windows-2022"
    assert job["timeout-minutes"] == 40
    # Unconditional and hard: a skippable or soft-failing job reads exactly
    # like a passing one.
    assert "if" not in job
    assert "continue-on-error" not in job
    assert "needs" not in job, "a failed upstream job would SKIP the windows job"
    assert all("continue-on-error" not in s for s in job["steps"])
    assert job["defaults"]["run"]["shell"] == "powershell"  # Windows PowerShell 5.1
    env = job["env"]
    assert env["CGO_ENABLED"] == "0"
    assert env["PYTHONUTF8"] == "1"
    assert env["CORTEXSIM_ENV"] == "development"
    assert env["CORTEXSIM_REQUIRE_POWERSHELL"] == "1"
    # No paths: filter on any trigger of the workflow that hosts it.
    for event, spec in (_triggers(ci) or {}).items():
        if isinstance(spec, dict):
            assert "paths" not in spec and "paths-ignore" not in spec, event


def test_windows_job_runs_every_phase_through_the_script():
    job = _wf(CI_YML)["jobs"]["windows"]
    used = []
    for run in _runs(job):
        if "windows-native-e2e.ps1" in run:
            assert "powershell.exe" in run and "-File" in run, run
            used += re.findall(r"-Phase\s+([a-z]+)", run)
    assert used == PHASES, used
    collect = next(s for s in job["steps"] if "-Phase collect" in (s.get("run") or ""))
    assert "always()" in str(collect.get("if", "")), "collect must run on failure"
    upload = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["with"]["name"] == "windows-proof"
    assert "always()" in str(upload.get("if", ""))


def test_windows_job_toolchain_matches_the_agent_job():
    jobs = _wf(CI_YML)["jobs"]
    win, agent = jobs["windows"], jobs["agent"]

    def _setup_go(job):
        return next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-go@"))

    # CI-11 (which Go) is undecided; until it is, the two jobs must agree.
    assert str(_setup_go(win)["with"]["go-version"]) == str(_setup_go(agent)["with"]["go-version"])
    assert _setup_go(win)["with"]["cache"] is False
    checkout = next(s for s in win["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert (checkout.get("with") or {}).get("submodules") in (None, False)
    py = next(s for s in win["steps"] if str(s.get("uses", "")).startswith("actions/setup-python@"))
    assert str(py["with"]["python-version"]) == "3.11"
    # The beacon is built by the SHIPPED recipe, under bash, not re-derived.
    build = next(s for s in win["steps"] if "build-agent-dist.sh" in (s.get("run") or ""))
    assert build.get("shell") == "bash"
    assert 'OUT_DIR="$RUNNER_TEMP/agent-dist"' in build["run"]


def test_pwsh_is_required_on_the_native_linux_jobs():
    """CI-08: a test that skips when pwsh is absent is only a guard on a runner
    that has pwsh. These jobs assert it is there and tell the suites so."""
    ci, test = _wf(CI_YML)["jobs"], _wf(TEST_YML)["jobs"]
    for name, job in (("agent", ci["agent"]), ("refs", ci["refs"]),
                      ("python-tests", test["python-tests"]),
                      ("ttp-static", test["ttp-static"]),
                      ("go-tests", test["go-tests"])):
        assert (job.get("env") or {}).get("CORTEXSIM_REQUIRE_POWERSHELL") == "1", name
        assert any("pwsh --version || exit 1" in r for r in _runs(job)), name
    # The backend suite runs INSIDE the prod image, which has no pwsh; the flag
    # must not be forwarded into it.
    backend = ci["backend"]
    docker_run = next(r for r in _runs(backend) if "docker run" in r)
    assert "CORTEXSIM_REQUIRE_POWERSHELL" not in docker_run
    assert "-e CI" not in docker_run
    assert "CORTEXSIM_REQUIRE_POWERSHELL" not in (backend.get("env") or {})


def test_refs_job_runs_the_powershell_floor_gate_after_deps():
    refs = _wf(CI_YML)["jobs"]["refs"]
    gate = _step_index(refs, "PowerShell 5.1 floor gate")
    deps = _step_index(refs, "pip install -r core/requirements.txt")
    assert gate > deps >= 0, (deps, gate)
    assert refs["steps"][gate]["run"].strip() == "make check-powershell"


def test_backend_job_checks_the_agent_shelf_after_the_ui_shelf():
    backend = _wf(CI_YML)["jobs"]["backend"]
    ui = _step_index(backend, "scripts/check-ui-shelf.sh")
    agent = _step_index(backend, "check-agent-shelf")
    assert agent > ui >= 0, (ui, agent)
    assert backend["steps"][agent]["run"].strip() == "make IMAGE=cortexsim:ci check-agent-shelf"

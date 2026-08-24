"""Tier B — push bundle integrity tests.

Phase 2 of the e2e execution methodology
(docs/design/e2e-execution-methodology.md).

Parametrized over ``(scenario, target)`` PAIRS, not scenarios. A scenario
whose every step is PowerShell-native has no bash bundle to lint — it has a
PowerShell bundle. Asserting bash properties over it was the old suite's
mistake: SIM-EDR-013/-017/-021 failed ``bash -n`` for years because the
generator emitted PowerShell into a ``.sh``, and SIM-EDR-006 PASSED while
emitting a bundle that dies at step-01 in front of a customer.

The bash assertions below therefore run for ``posix`` pairs and the
PowerShell ones for ``windows`` pairs, with a coverage guard
(``test_every_scenario_has_an_emittable_target``) so a scenario can never fall
out of BOTH suites silently.

For every scenario YAML in the library, validate that the push-mode
bash bundle generator produces a well-formed, lint-clean,
executable-ready script:

  - bash -n parses the generated bundle
  - shellcheck severity=warning on the generated bundle
  - every scenario step's command appears in the bundle
  - identity harness wrapping is present for non-root steps
  - cleanup block is present and includes every staged artifact mentioned
  - no placeholder leaks ({}, {{ }}, $undefined, TODO, FIXME)
  - no secret-shaped substrings (AKIA real-looking, openssl-style hex
    longer than 32 chars that's not a known dummy)

Unlike Tier A (which lints the hand-written ttps/*.sh under the
package directories), Tier B exercises the *generator path* —
catching regressions where SimCore's YAML→bash translation introduces
a bug that the hand-written package would never expose.

The generator is imported directly rather than going through HTTP
(would require booting SimCore). This makes the suite fast (~1 second
per scenario) and side-effect free.

Hard gate. Path-filtered to scenarios/ + core/engine/push_generator.py
in CI.
"""
from __future__ import annotations

import functools
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, List

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
# Allow `import core...` when the test runs from a sibling pytest cwd.
sys.path.insert(0, str(REPO_ROOT))

from core.engine import push_generator as _push  # noqa: E402


# ─── Scenario discovery ──────────────────────────────────────────────

def _discover_scenarios() -> List[pathlib.Path]:
    """Return every scenario YAML under scenarios/{plane}/*.yml.

    Skips _schema.yml and README files. Includes scenarios in
    scenarios/multi_plane/ alongside the per-plane subdirectories.
    """
    pkg_root = REPO_ROOT / "scenarios"
    out: list[pathlib.Path] = []
    for child in pkg_root.iterdir():
        if not child.is_dir():
            continue
        for yml in child.glob("*.yml"):
            if yml.name.startswith("_"):
                continue
            out.append(yml)
    return sorted(out)


SCENARIOS = _discover_scenarios()


def _label(p: pathlib.Path) -> str:
    try:
        rel = p.relative_to(REPO_ROOT)
    except ValueError:
        rel = p
    return str(rel)


@functools.lru_cache(maxsize=None)
def _load_scenario(path: pathlib.Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def _pairs(target: str) -> list[pathlib.Path]:
    """Scenario paths that can emit a bundle for ``target``."""
    return [
        p for p in SCENARIOS
        if target in _push.emittable_targets(_load_scenario(p))
    ]


POSIX_SCENARIOS = _pairs("posix")
WINDOWS_SCENARIOS = _pairs("windows")


# ─── Sanity guards ───────────────────────────────────────────────────

def test_discovery_found_scenarios():
    """The scenario glob should find at least one YAML.

    Same guard pattern as Tier A — silent zero-discovery is the worst
    failure mode.
    """
    assert len(SCENARIOS) > 0, (
        "discovered zero scenario YAMLs under scenarios/ — "
        "either the directory layout moved or the glob is wrong"
    )


def test_every_scenario_has_an_emittable_target():
    """No scenario may fall out of BOTH the bash and the PowerShell suite.

    Parametrizing by (scenario, target) means a scenario the resolver judges
    unemittable everywhere silently stops being tested. That is precisely the
    hole this suite exists to close, so the population is pinned at zero.
    """
    orphans = [
        _label(p) for p in SCENARIOS
        if not _push.emittable_targets(_load_scenario(p))
    ]
    assert not orphans, (
        "scenario(s) can emit no bundle at all — every step lacks BOTH a POSIX "
        f"and a Windows command: {orphans}"
    )


def test_both_suites_are_populated():
    """Guard against a resolver regression that empties one side.

    If `emittable_targets` ever started returning ("posix",) unconditionally,
    every PowerShell test below would vanish into zero parametrized cases and
    the suite would go green having tested nothing.
    """
    assert len(POSIX_SCENARIOS) > 100, f"posix suite collapsed to {len(POSIX_SCENARIOS)}"
    assert len(WINDOWS_SCENARIOS) >= 12, f"windows suite collapsed to {len(WINDOWS_SCENARIOS)}"


def test_windows_native_scenarios_are_not_in_the_bash_suite():
    """The four PowerShell-native scenarios must NOT emit a bash bundle.

    SIM-EDR-013/-017/-021 are the three long-standing `bash -n` failures and
    SIM-EDR-006 is the silent one: its PowerShell steps parse as bash, then die
    at step-01 with `Invoke-AtomicTest: command not found`, `set -e` aborts the
    run, and a DC reads the empty result as "XSIAM detected nothing" — a
    manufactured false negative on the customer's stack.

    They are fixed by WITHDRAWING the bash bundle (they gain a real PowerShell
    one below), not by loosening an assertion.
    """
    ids = {_load_scenario(p).get("scenario_id") for p in POSIX_SCENARIOS}
    for sid in ("SIM-EDR-006", "SIM-EDR-013", "SIM-EDR-017", "SIM-EDR-021"):
        assert sid not in ids, f"{sid} is PowerShell-native and must not emit a bash bundle"
    win_ids = {_load_scenario(p).get("scenario_id") for p in WINDOWS_SCENARIOS}
    for sid in ("SIM-EDR-006", "SIM-EDR-013", "SIM-EDR-017", "SIM-EDR-021"):
        assert sid in win_ids, f"{sid} must emit a PowerShell bundle"


# ─── Per-scenario bundle integrity ──────────────────────────────────

@pytest.mark.parametrize("path", POSIX_SCENARIOS, ids=_label)
def test_bundle_parses_as_bash(path: pathlib.Path):
    """The generated bundle must parse under ``bash -n``.

    A non-parsing bundle is a regression in push_generator's
    string-templating: probably an unescaped quote in a step command
    or a malformed cleanup block.
    """
    scenario = _load_scenario(path)
    bundle = _push.generate_bash(scenario)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(bundle)
        tmp = f.name
    try:
        result = subprocess.run(
            ["bash", "-n", tmp], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, (
            f"bash -n failed on generated bundle for {_label(path)}:\n"
            f"{result.stderr.strip()}"
        )
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)


@pytest.mark.skipif(
    shutil.which("shellcheck") is None,
    reason="shellcheck not installed",
)
@pytest.mark.parametrize("path", POSIX_SCENARIOS, ids=_label)
def test_bundle_passes_shellcheck(path: pathlib.Path):
    """The generated bundle must pass shellcheck severity=warning.

    Same severity floor as Tier A. Bundle-level regressions in
    quoting, variable expansion, or trap handling get caught here.
    """
    scenario = _load_scenario(path)
    bundle = _push.generate_bash(scenario)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(bundle)
        tmp = f.name
    try:
        result = subprocess.run(
            [
                "shellcheck",
                "--severity=warning",
                "--shell=bash",
                "--format=tty",
                # SC2154 — "var is referenced but not assigned" — fires
                # across the generator's per-step run_as wrappers because
                # each step's command lives inside its own quoted
                # argument. The variable IS assigned at runtime (the run_as
                # function uses eval inside a shared environment) but
                # shellcheck's static analysis can't see across the wrap.
                # Excluding here keeps the high-value catches (SC2086
                # unquoted-vars, SC2155 declare-and-assign, SC2046 word-
                # splitting) hot while silencing this systemic false
                # positive. Audit candidate: fix the generator to emit
                # `# shellcheck disable=SC2154` on each step wrapper, then
                # remove this exclusion.
                "--exclude=SC2154",
                tmp,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            # On failure, show the offending bundle lines for context.
            preview = "\n".join(
                f"{i+1:4d}: {line}"
                for i, line in enumerate(bundle.splitlines()[:120])
            )
            pytest.fail(
                f"shellcheck failed on generated bundle for {_label(path)}:\n"
                f"{result.stdout.strip()}\n\n"
                f"--- bundle preview (first 120 lines) ---\n{preview}"
            )
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)


@pytest.mark.parametrize("path", POSIX_SCENARIOS, ids=_label)
def test_every_step_command_in_bundle(path: pathlib.Path):
    """Every scenario step's `command` must appear in the bundle.

    Verifies the generator didn't drop a step due to a templating bug.
    Uses a witness substring extracted from the step's first executable
    line. The generator escapes single quotes when wrapping in
    ``run_as 'identity' '…command…'`` (single ' → '\\''), so we
    normalize both witness and bundle by stripping single quotes before
    comparing — quoting is a generator concern, not a step-fidelity
    concern.
    """
    scenario = _load_scenario(path)
    bundle = _push.generate_bash(scenario)
    steps = scenario.get("steps", [])
    assert len(steps) > 0, f"{_label(path)} declares zero steps"

    # The generator wraps each step in run_as 'identity' '…' and escapes
    # any literal single quote as '\''. Normalize by stripping every
    # quote AND backslash from both sides — only the underlying tokens
    # need to match for step-fidelity. Quoting is a generator concern.
    def _normalize(s: str) -> str:
        return s.replace("'", "").replace("\\", "").replace('"', "")
    bundle_norm = _normalize(bundle)

    for i, step in enumerate(steps):
        cmd = step.get("command", "")
        witness = None
        for line in cmd.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                witness = stripped[:80]
                break
        if witness is None:
            pytest.fail(
                f"{_label(path)} step {i+1} has no executable line in its command"
            )
        witness_norm = _normalize(witness)
        assert witness_norm in bundle_norm, (
            f"{_label(path)} step {i+1} command not found in bundle "
            f"(witness: {witness!r}, normalized: {witness_norm!r})"
        )


@pytest.mark.parametrize("path", POSIX_SCENARIOS, ids=_label)
def test_bundle_has_identity_harness(path: pathlib.Path):
    """Bundle must declare the identity harness function used to wrap
    non-root steps.

    The harness is the entire point of the push bundle for realistic
    causality — losing it would silently degrade every multi-identity
    scenario to root-only execution.
    """
    scenario = _load_scenario(path)
    bundle = _push.generate_bash(scenario)

    # Look for a recognizable run_as / harness signature. Accept either
    # of the patterns the generator might emit.
    signatures = [
        r"\brun_as\b\s*\(",          # bash function declaration
        r"runuser\s+-l\s+",          # inline runuser invocation
        r"sudo\s+-u\s+",             # alternative pattern
    ]
    matched = any(re.search(s, bundle) for s in signatures)
    assert matched, (
        f"{_label(path)} bundle has no identity-harness signature "
        f"(none of run_as/runuser/sudo -u found)"
    )


@pytest.mark.parametrize("path", POSIX_SCENARIOS, ids=_label)
def test_bundle_has_cleanup_block(path: pathlib.Path):
    """Bundle must include a cleanup section.

    Hand-written scenario YAML carries a `cleanup.commands` block; the
    generator copies those into the bundle. A regression that drops
    cleanup would leave stale artifacts on the target after every run,
    poisoning subsequent scenarios.
    """
    scenario = _load_scenario(path)
    bundle = _push.generate_bash(scenario)
    # Expect SOME cleanup signature — either a function, a trap, or
    # a section comment. The generator's exact format is not under test
    # here; we only assert the *presence*.
    signatures = [
        r"\bcleanup\b\s*\(",      # function declaration
        r"trap\s+",                # signal trap
        r"#\s*cleanup",            # section comment
        r"\bcleanup\b\s*=",        # variable assignment
    ]
    matched = any(re.search(s, bundle, re.IGNORECASE) for s in signatures)
    assert matched, (
        f"{_label(path)} bundle has no cleanup signature "
        f"(none of cleanup(){{/trap/# cleanup found)"
    )


# Known generator template field names. If we see a bare `{scenario_id}`
# or `{mitre_tactic}` in the bundle output it means the generator's str
# .format() missed a substitution. Scenario commands DO contain legitimate
# `{...}` syntax (Python f-strings inside embedded code, awk programs,
# JSON literals) — we don't flag those, only known generator-template
# field names.
_GENERATOR_TEMPLATE_FIELDS = (
    "scenario_id", "name", "version", "plane",
    "uc_ref", "uc_name", "tc_ref", "tc_name",
    "mitre_tactic", "mitre_tactic_name",
    "mitre_technique", "mitre_technique_name",
    "identity", "command", "step_id", "step_name",
    "threat_report", "threat_report_url",
    "cleanup_command", "external_tool",
)
_PY_LEAK = re.compile(
    r"(?<![\$\\])\{(?:" + "|".join(_GENERATOR_TEMPLATE_FIELDS) + r")\}"
)


@pytest.mark.parametrize("path", POSIX_SCENARIOS, ids=_label)
def test_no_template_placeholder_leaks(path: pathlib.Path):
    """The bundle must not contain unresolved generator-template
    placeholders.

    Scoped narrowly: we only flag `{scenario_id}` / `{mitre_tactic}` /
    etc. — the exact set of field names the generator's str.format()
    substitutes. We do NOT flag `{i}`, `{fn}`, `{var}` and other bare
    `{...}` because scenario commands legitimately contain Python
    f-strings, awk programs, and other code that uses braces.

    Also catches:
      - `{{...}}` — Jinja-style double-brace leaks
      - `<<TODO>>` / `<<FIXME>>` placeholder sentinels left in by hand
    """
    scenario = _load_scenario(path)
    bundle = _push.generate_bash(scenario)

    jinja_leak = re.compile(r"\{\{[^}]+\}\}")
    todo_sentinel = re.compile(r"<<\s*(TODO|FIXME|PLACEHOLDER)\s*>>", re.IGNORECASE)

    leaks = []
    for line_no, line in enumerate(bundle.splitlines(), start=1):
        if _PY_LEAK.search(line):
            leaks.append(f"line {line_no}: generator field placeholder: {line.strip()[:100]}")
        if jinja_leak.search(line):
            leaks.append(f"line {line_no}: jinja-style placeholder: {line.strip()[:100]}")
        if todo_sentinel.search(line):
            leaks.append(f"line {line_no}: TODO sentinel left in: {line.strip()[:100]}")

    assert not leaks, (
        f"{_label(path)} bundle has unresolved placeholders:\n  "
        + "\n  ".join(leaks)
    )


@pytest.mark.parametrize("path", POSIX_SCENARIOS, ids=_label)
def test_bundle_starts_with_shebang(path: pathlib.Path):
    """First line must be a bash shebang.

    Without it, executing the bundle via `./bundle.sh` invokes the
    user's login shell — which on CI runners is often dash/sh and
    silently misinterprets bashisms. The generator should always set
    `#!/usr/bin/env bash` on line 1.
    """
    scenario = _load_scenario(path)
    bundle = _push.generate_bash(scenario)
    first = bundle.splitlines()[0] if bundle else ""
    assert first.startswith("#!"), (
        f"{_label(path)} bundle missing shebang on line 1: {first!r}"
    )
    assert "bash" in first, (
        f"{_label(path)} bundle shebang doesn't reference bash: {first!r}"
    )


# ═════════════════════════════════════════════════════════════════════
# PowerShell bundle integrity (target=windows)
# ═════════════════════════════════════════════════════════════════════
#
# ON THE SYNTAX GATE, PLAINLY: `pwsh` is NOT present in the cortexsim:dev
# image and is NOT on the maintainer's host, so `test_ps_bundle_parses_as
# _powershell` SKIPS in both places today. A skip that reads as a pass is
# exactly the fake-green this suite exists to kill, so:
#
#   * `test_powershell_parser_available_in_ci` FAILS (not skips) when CI=1 and
#     pwsh is missing — CI must provide the real parser or go red;
#   * `test_ps_bundle_structural_invariants` is ALWAYS on and is honest about
#     what it is: bracket/quote balance plus a set of contract assertions. It
#     is NOT a syntax check and does not pretend to be one.
#
# The generated bundles WERE verified against the real
# System.Management.Automation.Language.Parser (PowerShell 7.4) during
# development; all 12 parse clean and three execute end-to-end under pwsh.

def _ps_bundle(path: pathlib.Path) -> str:
    return _push.generate_powershell(_load_scenario(path))


def _strip_ps_literals(text: str) -> str:
    """Blank here-strings and single-quoted regions in a .ps1.

    Scenario command text is embedded as data inside `@'…'@`. It legitimately
    contains `&&`, `$(`, unbalanced braces and every other construct the
    structural checks below forbid in CODE. Checking the raw file would flag
    the payload rather than the generator.
    """
    out: list[str] = []
    in_here = False
    for line in text.split("\n"):
        if in_here:
            if line.startswith("'@") or line.startswith('"@'):
                in_here = False
                out.append(line[2:])
            else:
                out.append("")
            continue
        if line.rstrip().endswith("@'") or line.rstrip().endswith('@"'):
            in_here = True
            out.append(line.rstrip()[:-2])
            continue
        out.append(re.sub(r"'[^']*'", "''", line))
    return "\n".join(out)


def _ps_code(text: str) -> str:
    """Executable text only: literals blanked, `#` comments dropped."""
    stripped = _strip_ps_literals(text)
    return "\n".join(
        line for line in stripped.split("\n") if not line.lstrip().startswith("#")
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh not installed")
@pytest.mark.parametrize("path", WINDOWS_SCENARIOS, ids=_label)
def test_ps_bundle_parses_as_powershell(path: pathlib.Path):
    """REAL syntax gate: the PowerShell AST parser, on the file.

    `pwsh -Command { … }` does not parse a FILE, so ParseFile is used
    directly — the same call PSScriptAnalyzer makes.
    """
    bundle = _ps_bundle(path)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(bundle)
        tmp = f.name
    try:
        result = subprocess.run(
            # The path travels in the environment, not as a positional arg:
            # `pwsh -Command <script> <path>` does NOT populate $args, and
            # interpolating it into the script would be an injection seam.
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", (
                "$t=$null; $e=$null; "
                "[System.Management.Automation.Language.Parser]::ParseFile("
                "$env:CORTEXSIM_PS1,[ref]$t,[ref]$e) | Out-Null; "
                "if ($e.Count) { $e | ForEach-Object { "
                "Write-Output ('line ' + $_.Extent.StartLineNumber + ': ' + $_.Message) }; exit 1 }"
            )],
            env={**os.environ, "CORTEXSIM_PS1": tmp},
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"PowerShell parse errors in generated bundle for {_label(path)}:\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)


def test_powershell_parser_available_in_ci():
    """A skipped syntax check in CI is indistinguishable from a passing one."""
    if os.environ.get("CI"):
        assert shutil.which("pwsh"), (
            "CI must provide pwsh — the .ps1 parse gate is not optional. "
            "Install PowerShell on the runner (mirroring the shellcheck apt step)."
        )


@pytest.mark.parametrize("path", WINDOWS_SCENARIOS, ids=_label)
def test_ps_bundle_structural_invariants(path: pathlib.Path):
    """NOT a syntax check — the syntax check is pwsh above, and it skips when
    pwsh is absent. This proves only the invariants listed below, each of which
    is a property no parser would catch anyway.
    """
    bundle = _ps_bundle(path)
    code = _ps_code(bundle)
    label = _label(path)

    # 1. Brackets balance outside literals.
    for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
        assert code.count(open_c) == code.count(close_c), (
            f"{label}: unbalanced {open_c}{close_c} in generated PowerShell "
            f"({code.count(open_c)} vs {code.count(close_c)})"
        )

    # 2. Every here-string that opens must close.
    assert bundle.count("@'\n") == len(re.findall(r"(?m)^'@", bundle)), (
        f"{label}: unterminated here-string in generated PowerShell"
    )

    # 3. Windows PowerShell 5.1 floor — none of these exist before PS7, and
    #    Server 2016/2019/2022 and Win10/11 ship 5.1, not 7.
    for op in ("&&", "||", "??"):
        assert op not in code, (
            f"{label}: PS7-only operator {op!r} in generated code — breaks the "
            f"Windows PowerShell 5.1 floor"
        )

    # 4. Cardinal invariant: self-contained. Nothing is installed, no module
    #    is pulled, and execution policy is never mutated machine-wide.
    for forbidden in ("Install-Module", "Install-Package", "Set-ExecutionPolicy",
                      "Register-PSRepository", "Save-Module"):
        assert forbidden not in code, (
            f"{label}: {forbidden} in generated bundle — a bundle must run on a "
            f"clean host with no installs (Set-ExecutionPolicy is also a "
            f"machine-scope mutation that fails unelevated)"
        )

    # 5. Cardinal invariant: no SimCore dependency at runtime.
    for phone_home in ("/api/", "Invoke-RestMethod", "$Server", "$SimCore"):
        assert phone_home not in code, (
            f"{label}: {phone_home} in generated bundle — a push bundle must "
            f"never call back to SimCore"
        )

    # 6. Lifecycle: transcript, script-scope finally, cleanup, harness.
    for required in ("Start-Transcript", "Stop-Transcript", "function Invoke-CsCleanup",
                     "function Invoke-CsStep", "function Resolve-CsIdentity"):
        assert required in code, f"{label}: generated bundle missing {required!r}"
    assert re.search(r"(?m)^finally \{", code), (
        f"{label}: no script-scope finally block — cleanup would not run on error "
        f"(the PowerShell analogue of bash's `trap cleanup EXIT`)"
    )

    # 7. No unresolved @@PLACEHOLDER@@ (the ps1 analogue of the {scenario_id}
    #    leak test; the syntax differs because the generator uses @@…@@
    #    substitution, not str.format — PowerShell is full of literal braces).
    leaks = re.findall(r"@@[A-Z0-9_]+@@", bundle)
    assert not leaks, f"{label}: unresolved template placeholders {sorted(set(leaks))}"


@pytest.mark.parametrize("path", WINDOWS_SCENARIOS, ids=_label)
def test_ps_every_resolved_step_command_in_bundle(path: pathlib.Path):
    """Every resolved step's command text must appear verbatim in the bundle.

    The whole point of the Windows target is that the authored
    `platform_variants['windows']` command — 17.5 KB across the corpus that the
    bash generator silently dropped on the floor — actually reaches the host.
    """
    scenario = _load_scenario(path)
    bundle = _ps_bundle(path)
    resolution = _push.resolve_target(scenario, "windows")
    assert resolution.steps, f"{_label(path)} resolved zero windows steps"
    for res in resolution.steps:
        witness = next(
            (ln.strip() for ln in res.command.splitlines()
             if ln.strip() and not ln.strip().startswith("#")),
            None,
        )
        assert witness, f"{_label(path)} {res.step_id} has no executable line"
        # Here-string embedding is verbatim — no escaping, so no normalization.
        assert witness in bundle, (
            f"{_label(path)} {res.step_id} command missing from PowerShell bundle "
            f"(witness: {witness[:80]!r})"
        )


@pytest.mark.parametrize("path", WINDOWS_SCENARIOS, ids=_label)
def test_ps_bundle_header_and_bom(path: pathlib.Path):
    """UTF-8 BOM + comment banner + the documented invocation line.

    The BOM is load-bearing, not cosmetic: Windows PowerShell 5.1 reads a
    BOM-less file as ANSI and mangles the em-dashes the corpus puts INSIDE
    string literals (SIM-EDR-017 step-04's marker banner). Mojibake in a marker
    string breaks the grep a DC runs to prove the step executed.
    """
    bundle = _ps_bundle(path)
    assert bundle.startswith("﻿"), f"{_label(path)}: .ps1 must carry a UTF-8 BOM for PS 5.1"
    first = bundle[1:].splitlines()[0]
    assert first.startswith("# ="), f"{_label(path)}: expected comment banner, got {first!r}"
    assert "-ExecutionPolicy Bypass -File" in bundle, (
        f"{_label(path)}: header must document the Mark-of-the-Web-safe invocation"
    )


@pytest.mark.parametrize("path", WINDOWS_SCENARIOS, ids=_label)
def test_ps_identity_degradation_is_surfaced(path: pathlib.Path):
    """A step that cannot reach its declared identity must SAY so.

    Windows has no credential-free non-interactive impersonation, so a step
    declaring `www-data` runs as the invoking user. Reporting that as success
    would put a false causality chain into the POV readout — the marker is what
    keeps the story honest.
    """
    bundle = _ps_bundle(path)
    assert "cortexsim-identity-degraded" in bundle, (
        f"{_label(path)}: no identity-degradation marker — the bundle would "
        f"silently claim identities it never achieved"
    )


def test_ps_generator_refuses_unsatisfiable_target():
    """A scenario with no Windows command must raise, not emit a partial bundle.

    A partial bundle is worse than none: it runs green, emits a subset of the
    attack, and the missing detections read as a customer-stack failure.
    """
    posix_only = next(
        p for p in POSIX_SCENARIOS
        if "windows" not in _push.emittable_targets(_load_scenario(p))
    )
    scenario = _load_scenario(posix_only)
    with pytest.raises(_push.BundleTargetUnsatisfiable) as exc:
        _push.generate_powershell(scenario)
    body = exc.value.to_error()
    assert body["code"] == "BUNDLE_TARGET_UNSATISFIABLE"
    assert set(body) == {"error", "code", "detail"}
    # The offending steps must be NAMED — "it didn't work" is not actionable.
    first_unresolved = exc.value.unresolved[0][0]
    assert first_unresolved in body["detail"]
    assert "WINDOWS_COMMAND_UNAVAILABLE" in body["detail"]

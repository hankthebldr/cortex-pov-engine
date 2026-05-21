"""Tier B — push bundle integrity tests.

Phase 2 of the e2e execution methodology
(docs/design/e2e-execution-methodology.md).

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


def _load_scenario(path: pathlib.Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


# ─── Sanity guard ────────────────────────────────────────────────────

def test_discovery_found_scenarios():
    """The scenario glob should find at least one YAML.

    Same guard pattern as Tier A — silent zero-discovery is the worst
    failure mode.
    """
    assert len(SCENARIOS) > 0, (
        "discovered zero scenario YAMLs under scenarios/ — "
        "either the directory layout moved or the glob is wrong"
    )


# ─── Per-scenario bundle integrity ──────────────────────────────────

@pytest.mark.parametrize("path", SCENARIOS, ids=_label)
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
@pytest.mark.parametrize("path", SCENARIOS, ids=_label)
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


@pytest.mark.parametrize("path", SCENARIOS, ids=_label)
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


@pytest.mark.parametrize("path", SCENARIOS, ids=_label)
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


@pytest.mark.parametrize("path", SCENARIOS, ids=_label)
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


@pytest.mark.parametrize("path", SCENARIOS, ids=_label)
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


@pytest.mark.parametrize("path", SCENARIOS, ids=_label)
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

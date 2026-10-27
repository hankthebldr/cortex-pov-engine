"""The .claude/hooks/lint-scenario.py edit hook must AGREE with the boot loader.

The hook's docstring promises it mirrors the loader's REJECT contract. Two
divergences made it block edits the loader accepts cleanly:

  * its plane set omitted ``DLP`` (which ``scenario_loader.VALID_PLANES`` has), so
    a loader-valid ``plane: DLP`` scenario was rejected with an ERROR; and
  * it treated a bare ``cgo_anchor`` (no step wiring ``causality``) as a declared
    spine and errored "more than one root step", while the loader's
    ``validate_causality_spine`` returns early for a zero-causality collection
    and ACCEPTS it — leaving the hook's own warning branch dead.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "lint-scenario.py"
SAMPLE = REPO_ROOT / "scenarios" / "edr" / "edr-006-lsass-memory-dump.yml"


def _load_hook():
    spec = importlib.util.spec_from_file_location("lint_scenario_hook", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_planes_mirror_loader_valid_planes():
    """The hook's PLANES must equal the loader's VALID_PLANES exactly."""
    from engine.scenario_loader import VALID_PLANES  # noqa: PLC0415

    hook = _load_hook()
    assert hook.PLANES == set(VALID_PLANES), (
        "lint hook PLANES diverged from loader VALID_PLANES: "
        f"missing={set(VALID_PLANES) - hook.PLANES} extra={hook.PLANES - set(VALID_PLANES)}"
    )


def test_hook_accepts_cgo_anchor_without_step_causality(tmp_path):
    """cgo_anchor + >=2 steps, none wiring causality: loader ACCEPTS → hook must not error."""
    base = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    assert isinstance(base.get("cgo_anchor"), dict), "sample must carry a cgo_anchor"
    base["steps"] = base["steps"][:2]
    for step in base["steps"]:
        step.pop("causality", None)

    # The loader's own spine validator accepts this (the behaviour the hook must mirror).
    from engine.scenario_loader import validate_causality_spine  # noqa: PLC0415

    class _Caus:
        def __init__(self, parent):
            self.parent_step = parent

    class _Step:
        def __init__(self, sid):
            self.id = sid
            self.causality = None

    validate_causality_spine([_Step("s1"), _Step("s2")])  # must not raise

    crafted = tmp_path / "cgo_no_causality.yml"
    crafted.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(HOOK), str(crafted), str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "hook rejected a cgo-anchor-without-step-causality scenario the loader accepts\n"
        f"stdout:\n{result.stdout}"
    )
    assert "more than one root step" not in result.stdout
    # the previously-dead warning branch should now fire
    assert "no step wires a causality" in result.stdout


# ── Indicators S-22..S-27 + marker-only warning ──────────────────────────────
import pytest  # noqa: E402


def test_hook_indicator_vocab_mirrors_loader():
    from engine.scenario_loader import INDICATOR_TYPES, INDICATOR_SAFE_FORMS  # noqa: PLC0415
    hook = _load_hook()
    assert hook.INDICATOR_TYPES == INDICATOR_TYPES and hook.INDICATOR_SAFE_FORMS == INDICATOR_SAFE_FORMS


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["indicators"][0].update(type="hash"), "S-22"),
    (lambda d: d["indicators"][0].update(source_url="https://example.com/"), "S-23"),
    (lambda d: d["indicators"][0].update(surfaces_in=["step-99"]), "S-24"),
    (lambda d: d["steps"][0].update(command="curl https://rn-stage.example/"), "S-25"),
    (lambda d: d["indicators"][0].update(type="sha256", value="c" * 64, safe_form="hash_only")
        or d["steps"][0].update(command="wget https://x/" + "c" * 64), "S-26"),
    (lambda d: d["indicators"][0].update(type="sha256", value="c" * 64), "S-27"),
])
def test_hook_rejects_each_indicator_code(tmp_path, mutate, code):
    d = yaml.safe_load(SAMPLE.read_text())
    d["indicators"] = [dict(id="ind-01", type="domain", value="rn-stage.example", safe_form="dns_lookup",
                            source_url="https://unit42.paloaltonetworks.com/x/",
                            surfaces_in=[d["steps"][0]["id"]])]
    d["steps"][0]["command"] = "dig +short rn-stage.example"
    mutate(d)
    p = tmp_path / "s.yml"
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    r = subprocess.run([sys.executable, str(HOOK), str(p), str(REPO_ROOT)], capture_output=True, text=True)
    assert r.returncode != 0 and code in r.stdout


def test_hook_accepts_clean_indicator(tmp_path):
    d = yaml.safe_load(SAMPLE.read_text())
    d["indicators"] = [dict(id="ind-01", type="domain", value="rn-stage.example", safe_form="dns_lookup",
                            source_url="https://unit42.paloaltonetworks.com/x/",
                            surfaces_in=[d["steps"][0]["id"]])]
    d["steps"][0]["command"] = "dig +short rn-stage.example"
    p = tmp_path / "s.yml"
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    r = subprocess.run([sys.executable, str(HOOK), str(p), str(REPO_ROOT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_hook_marker_only_step_is_a_warning_not_an_error(tmp_path):
    d = yaml.safe_load(SAMPLE.read_text())
    d["steps"][0]["command"] = "echo marker || true"
    p = tmp_path / "s.yml"
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    r = subprocess.run([sys.executable, str(HOOK), str(p), str(REPO_ROOT)], capture_output=True, text=True)
    assert r.returncode == 0 and "marker-only" in r.stdout and d["steps"][0]["id"] in r.stdout


@pytest.mark.parametrize("cmd,expected", [
    ("echo hi", True), ("printf 'x'\n: nothing", True), ("Write-Host 'x'", True),
    ("echo m; curl -s http://127.0.0.1:8080/ || true", True),
    ("echo m; dig +short rn-stage.example", False),
    ("python3 -m scripts.eal_simulator.cli run /tmp/c.yml --live", False),
])
def test_is_marker_only(cmd, expected):
    assert _load_hook().is_marker_only(cmd) is expected


# ── Fix round 1: loader-parity tightening + canonical marker rule ────────────
def _ind(**kw):
    base = dict(id="ind-01", type="domain", value="rn-stage.example", safe_form="dns_lookup",
                source_url="https://unit42.paloaltonetworks.com/x/", surfaces_in=["S"])
    base.update(kw)
    return base


def _violations(indicators, command="echo ok", variants=None, cleanup=None):
    hook = _load_hook()
    step = {"id": "S", "command": command}
    if variants:
        step["platform_variants"] = variants
    return hook.unsafe_indicator_lines(indicators, [step], cleanup)


def _hook_out(tmp_path, mutate):
    d = yaml.safe_load(SAMPLE.read_text())
    d["indicators"] = [_ind(surfaces_in=[d["steps"][0]["id"]])]
    d["steps"][0]["command"] = "dig +short rn-stage.example"
    mutate(d)
    p = tmp_path / "s.yml"
    p.write_text(yaml.safe_dump(d, sort_keys=False))
    return subprocess.run([sys.executable, str(HOOK), str(p), str(REPO_ROOT)], capture_output=True, text=True)


def test_fix_scans_platform_variants():
    v = _violations([_ind()], variants={"linux": "curl https://rn-stage.example/"})
    assert any("S-25" in x and "platform_variants[linux]" in x for x in v)


def test_fix_scans_cleanup_commands():
    v = _violations([_ind()], cleanup=["curl https://rn-stage.example/"])
    assert any("S-25" in x and "cleanup" in x for x in v)


def test_fix_cleanup_via_subprocess(tmp_path):
    r = _hook_out(tmp_path, lambda d: d.update(cleanup={"commands": ["wget rn-stage.example"]}))
    assert r.returncode != 0 and "S-25" in r.stdout and "cleanup" in r.stdout


def test_fix_subdomain_counts_but_prefixed_label_does_not():
    assert _violations([_ind()], "curl https://www.rn-stage.example/")
    assert not _violations([_ind()], "curl https://x-rn-stage.example/")


@pytest.mark.parametrize("cmd,fires", [
    ("dig +short rn-stage.example @${LAB_RESOLVER:-1.1.1.1}", False),
    ("dig +short rn-stage.example", False),
    ("dig +short rn-stage.example | xargs curl", True),
    ("dig rn-stage.example; nc rn-stage.example 443", True),
    ("dig rn-stage.example && nc rn-stage.example 443", True),
    ("dig rn-stage.example || nc rn-stage.example 443", True),
    ("dig $(curl rn-stage.example)", True),
    ("dig `echo rn-stage.example`", True),
])
def test_fix_dns_exemption_forbids_chaining(cmd, fires):
    assert bool(_violations([_ind()], cmd)) is fires


def test_fix_id_is_exactly_two_digits_and_unique(tmp_path):
    r = _hook_out(tmp_path, lambda d: d["indicators"][0].update(id="ind-001"))
    assert r.returncode != 0 and "S-22" in r.stdout
    r = _hook_out(tmp_path, lambda d: d["indicators"].append(dict(d["indicators"][0])))
    assert r.returncode != 0 and "S-22" in r.stdout and "duplicate" in r.stdout


def test_fix_url_scheme_and_host_case_insensitive():
    ind = _ind(type="url", value="https://Rn-Stage.example/Path", safe_form="log_field")
    assert _violations([ind], "curl HTTPS://rn-stage.EXAMPLE/Path")
    assert not _violations([ind], "curl https://rn-stage.example/path")


@pytest.mark.parametrize("cmd,expected", [
    (None, True), ("", True),
    ("echo a | cat", False),
    ("echo a | true", True),
    ("echo a && echo b; : x", True),
    ("# comment\ntrue", True),
    ("echo m; dig +short d", False),
    ("echo m; curl -s http://localhost/ || true", True),
    ("cortexsim-eal run", False),
])
def test_fix_canonical_marker_rule(cmd, expected):
    assert _load_hook().is_marker_only(cmd) is expected

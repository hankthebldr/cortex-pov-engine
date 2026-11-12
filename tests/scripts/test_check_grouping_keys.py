"""scripts/check_grouping_keys.py: the `make check-grouping` corpus gate.

Exit 1 only on a G-03 (a correlation rule maps the declared stitching_key to
a custom destination XSIAM does not group on); every other code is reported.
"""
from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_grouping_keys.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, cwd=str(REPO_ROOT), timeout=300)


def _g03_corpus(base: Path) -> None:
    (base / "scenarios" / "multi_plane").mkdir(parents=True)
    (base / "detection_scanner" / "ttps").mkdir(parents=True)
    card = {"id": "TTP-T-0001", "status": "active", "detections": {"correlation_rules": [
        {"rule_id": "CR-T-0001", "name": "custom dest", "severity": "medium",
         "alert_fields": {"Suspicious_User": "user_principal"}}]}}
    (base / "detection_scanner" / "ttps" / "TTP-T-0001.json").write_text(json.dumps(card))
    (base / "scenarios" / "multi_plane" / "mp-t.yml").write_text(
        "scenario_id: SIM-T-001\n"
        "stitching_key: user_principal\n"
        "steps:\n"
        "  - id: step-01\n"
        "    expected_detections:\n"
        "      - plane: ITDR\n"
        "        type: Correlation\n"
        "        description: custom destination\n"
        "        ttp_ref: TTP-T-0001\n"
        "        detection_id: CR-T-0001\n")
    # Mirrors the ground-truth walk: _schema.yml and probe dirs are not scenarios.
    (base / "scenarios" / "_schema.yml").write_text("not: a scenario\n")


def test_exit_0_on_corpus_without_g03():
    proc = run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "G-04" in proc.stdout


def test_exit_1_when_g03_present(tmp_path):
    _g03_corpus(tmp_path)
    proc = run("--base", str(tmp_path))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "SIM-T-001" in proc.stdout and "Suspicious_User" in proc.stdout


def test_json_counts_match_findings():
    proc = run("--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    tally = collections.Counter(f["code"] for f in doc["findings"])
    assert doc["counts_by_code"] == {c: tally.get(c, 0)
                                     for c in ("G-01", "G-02", "G-03", "G-04", "G-05")}
    assert doc["counts_by_code"]["G-04"] > 0


def test_ground_truth_has_grouping_block():
    gt = json.loads((REPO_ROOT / "docs" / "reference" / "ground-truth.json").read_text())
    block = gt["grouping"]
    assert set(block["counts_by_code"]) == {"G-01", "G-02", "G-03", "G-04", "G-05"}
    assert block["step_detections_analyzed"] == gt["corpus"]["step_detections"]
    assert block["scenarios_analyzed"] == gt["corpus"]["scenarios_loadable"]

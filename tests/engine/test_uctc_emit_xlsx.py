# tests/engine/test_uctc_emit_xlsx.py
import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "uctc_crosswalk_v2.2.py")


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True, cwd=REPO)


def test_emit_xlsx_writes_csv_and_scoreboard(tmp_path, monkeypatch):
    # Generate into the real tree, then assert the artifacts exist and --check passes.
    r = _run("--emit-xlsx")
    assert r.returncode == 0, r.stderr
    csv_path = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source", "engine_coverage_v2.3.csv")
    board = os.path.join(REPO, "docs", "uc_tc_mapping", "scoreboard.md")
    assert os.path.exists(csv_path)
    assert os.path.exists(board)
    # 266 data rows + header
    assert len(open(csv_path).read().splitlines()) == 267


def test_check_passes_when_in_sync():
    _run("--emit-xlsx")                    # regenerate
    r = _run("--emit-xlsx", "--check")     # then check
    assert r.returncode == 0, r.stderr


def test_check_fails_when_stale(tmp_path):
    csv_path = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source", "engine_coverage_v2.3.csv")
    _run("--emit-xlsx")
    original = open(csv_path).read()
    try:
        open(csv_path, "w").write(original + "TC-BOGUS,,,,,,,,,,\n")
        r = _run("--emit-xlsx", "--check")
        assert r.returncode == 1
    finally:
        open(csv_path, "w").write(original)   # restore

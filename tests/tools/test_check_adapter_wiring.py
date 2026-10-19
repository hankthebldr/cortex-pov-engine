"""Tests for scripts/check-adapter-wiring.py — the de-hand-rolling lint.

The gate must fail CLOSED: a run that scanned nothing (wrong CORTEXSIM_BASE_DIR,
missing packs or scenarios dir) or that hit an unparseable file must NOT report
PASS. "0 candidate · 0 redundant · 0 generic → PASS" on an empty corpus is the
"a zero is degraded, not ok" anti-pattern — a green that proves nothing.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-adapter-wiring.py"


def _run(base: Path | str) -> subprocess.CompletedProcess:
    env = dict(os.environ, CORTEXSIM_BASE_DIR=str(base))
    return subprocess.run(
        ["python3", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def _verdict_passed(stdout: str) -> bool:
    """True iff the script printed its PASS verdict (a line that is exactly PASS)."""
    return any(line.strip() == "PASS" for line in stdout.splitlines())


def test_nonexistent_base_dir_fails_closed(tmp_path):
    """A base dir with no packs/scenarios must NOT read as PASS."""
    missing = tmp_path / "nope"
    result = _run(missing)
    assert result.returncode != 0, (
        "scanning an empty/missing corpus must fail closed, not PASS\n"
        f"stdout:\n{result.stdout}"
    )
    assert not _verdict_passed(result.stdout)


def test_unparseable_scenario_fails_closed(tmp_path):
    """A scenario the YAML parser chokes on (loader would REJECT it) must fail."""
    (tmp_path / "tools" / "packs").mkdir(parents=True)
    (tmp_path / "tools" / "packs" / "p.yml").write_text(
        "adapter_id: TOOL-X\nname: x\ntier: 4\n", encoding="utf-8"
    )
    sc = tmp_path / "scenarios" / "edr"
    sc.mkdir(parents=True)
    (sc / "good.yml").write_text("scenario_id: SIM-EDR-001\n", encoding="utf-8")
    (sc / "bad.yml").write_text("scenario_id: [unclosed\n  bad: :\n", encoding="utf-8")

    result = _run(tmp_path)
    assert result.returncode != 0, (
        "an unparseable scenario must fail the gate, not be silently skipped\n"
        f"stdout:\n{result.stdout}"
    )
    assert not _verdict_passed(result.stdout)


def test_real_corpus_passes():
    """Guard against over-correction: the real tree still passes cleanly."""
    result = _run(REPO_ROOT)
    assert result.returncode == 0, (
        f"real corpus should PASS\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert _verdict_passed(result.stdout)

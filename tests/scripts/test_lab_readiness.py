"""Guard tests for scripts/lab_readiness.py — the lab-readiness classifier.

The classifier's whole value is telling a DC which scenarios will actually EMIT
signal in a lab, so its failure mode is a false GREEN: a pure-narration scenario
scored as runnable, whose seeded Result rows then read in a POV as "Cortex missed
it". These tests pin the three parsing subtleties that produced exactly that
false GREEN during development (a quote-blind, comment-blind, probe-blind split),
each with RED/GREEN evidence, plus the corpus-level invariant.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
os.environ.setdefault("CORTEXSIM_BASE_DIR", str(REPO_ROOT))

import lab_readiness as lr  # noqa: E402


# --- clause splitting: quotes and comments must not be mistaken for operators ---

def test_delimiter_inside_quotes_is_not_a_clause_boundary():
    # RED before the quote-aware split: `esxcli syslog reload'` was a separate
    # "real" clause. GREEN after: the whole echo is one no-signal clause.
    cmd = "echo '[SAFE-MODE] esxcli syslog config set && esxcli syslog reload'"
    assert lr._clauses(cmd) == [cmd]
    assert lr._step_produces_signal(cmd) is False


def test_semicolon_inside_a_comment_is_not_a_clause_boundary():
    # RED before comment-awareness: `CR-ASM-0006 fuses...` (after a `;` in a
    # comment) scored as a real command.
    cmd = "# tradecraft; CR-ASM-0006 fuses the exposure\necho '[SAFE-MODE] stitched'"
    assert lr._step_produces_signal(cmd) is False


def test_trailing_comment_after_a_real_command_still_signals():
    assert lr._step_produces_signal("nmap -sV 10.0.0.1 # recon scan") is True


# --- availability probes are not attack actions ---

def test_guarded_version_probe_is_not_signal():
    cmd = "echo '[SAFE-MODE] esxcli shell' && (esxcli --version 2>/dev/null || true)"
    assert lr._step_produces_signal(cmd) is False


def test_presence_probe_is_not_signal():
    assert lr._step_produces_signal("command -v hydra >/dev/null 2>&1 || true") is False


def test_real_commands_are_signal():
    for c in ("cat /etc/shadow", "find /home -name id_rsa", "mkdir -p /tmp/x",
              "nmap -sV 10.0.0.1", "python3 -m scripts.eal_simulator.cli run"):
        assert lr._step_produces_signal(c) is True, c


def test_a_scan_flag_is_not_mistaken_for_a_version_flag():
    # `-sV` must not be swallowed by the `-V` version-probe rule.
    assert lr._step_produces_signal("nmap -sV target") is True


# --- corpus-level invariant ---

def test_known_narration_scenarios_are_red_and_real_ones_are_not():
    rows = {r["scenario_id"]: r for r in lr.build()}
    # SIM-EDR-019 is marketed as a causality-strong flagship but every step is
    # `echo '[SAFE-MODE ...]'` — it MUST classify RED so it is never demoed as a
    # detection scenario.
    for sid in ("SIM-EDR-019", "SIM-TIM-005", "SIM-ASM-005",
                "SIM-ASM-006", "SIM-MP-020", "SIM-ITDR-016"):
        assert rows[sid]["tier"] == "RED", (sid, rows[sid]["reason"])
        assert rows[sid]["steps_real_signal"] == 0
    # Scenarios that run real binaries must never be RED.
    for sid in ("SIM-EDR-001", "SIM-ITDR-015", "SIM-MP-019", "SIM-CDR-009"):
        assert rows[sid]["tier"] != "RED", sid
        assert rows[sid]["steps_real_signal"] > 0


def test_build_is_deterministic():
    a = lr.build()
    b = lr.build()
    assert [r["scenario_id"] for r in a] == [r["scenario_id"] for r in b]


def test_summary_tier_counts_cover_every_scenario():
    rows = lr.build()
    s = lr.summarize(rows)
    assert sum(s["by_tier"].values()) == s["scenarios_total"] == len(rows)
    # every row lands in exactly one of the three tiers
    assert set(r["tier"] for r in rows) <= {"GREEN", "YELLOW", "RED"}

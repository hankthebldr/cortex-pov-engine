"""Attempt dataclass tests."""
from __future__ import annotations

from cortex_prompt_attacker.attempt import Attempt, run_meta


def test_default_attempt_is_new():
    a = Attempt()
    assert a.status == "NEW"
    assert a.outcome == "unknown"
    assert a.duration_seconds is None


def test_lifecycle_start_and_complete_set_timestamps():
    a = Attempt()
    a.start()
    assert a.status == "STARTED"
    assert a.started_at is not None
    a.complete("vuln")
    assert a.status == "COMPLETE"
    assert a.outcome == "vuln"
    assert a.completed_at is not None
    assert a.duration_seconds is not None and a.duration_seconds >= 0


def test_as_dict_round_trip_carries_all_fields():
    a = Attempt(
        probe_classname="my_probe",
        prompt="hi",
        owasp_id="LLM01",
    )
    d = a.as_dict()
    assert d["probe_classname"] == "my_probe"
    assert d["owasp_id"] == "LLM01"
    assert d["entry_type"] == "attempt"


def test_run_meta_has_distinct_entry_type():
    meta = run_meta(probes=10, target_url="http://x", mutators=["noop"], scorers=["x"])
    assert meta["entry_type"] == "run_meta"
    assert meta["probes_total"] == 10
    assert meta["mutators"] == ["noop"]

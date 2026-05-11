"""ActionResult + run_meta tests."""
from __future__ import annotations

from cortex_browser_attacker.attempt import ActionResult, run_meta


def test_default_is_new_status():
    r = ActionResult()
    assert r.status == "NEW"
    assert r.outcome == "unknown"
    assert r.duration_seconds is None


def test_lifecycle_start_and_complete():
    r = ActionResult()
    r.start()
    assert r.status == "STARTED" and r.started_at is not None
    r.complete("success")
    assert r.status == "COMPLETE"
    assert r.outcome == "success"
    assert r.duration_seconds is not None
    assert r.duration_seconds >= 0


def test_complete_with_error_sets_error_field():
    r = ActionResult()
    r.start()
    r.complete("failure", error="boom")
    assert r.outcome == "failure"
    assert r.error == "boom"


def test_as_dict_carries_extension_fields():
    r = ActionResult(
        action_name="paste",
        cortex_canary="CANARY-1",
        expected_detection="PB DLP — paste DLP rule",
    )
    d = r.as_dict()
    assert d["cortex_canary"] == "CANARY-1"
    assert d["expected_detection"] == "PB DLP — paste DLP rule"
    assert d["action_name"] == "paste"
    assert d["entry_type"] == "action_attempt"


def test_run_meta_shape():
    m = run_meta(
        campaign_id="BC-BROWSER-001",
        name="cred paste",
        action_count=3,
        browser_channel="chromium",
        target_allowlist=["a.invalid"],
    )
    assert m["entry_type"] == "run_meta"
    assert m["campaign_id"] == "BC-BROWSER-001"
    assert m["action_count"] == 3
    assert m["tool"] == "cortex-browser-attacker"

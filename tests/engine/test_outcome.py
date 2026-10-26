from engine import outcome as oc

def test_classify_prefers_pretty_prefix_case_insensitive():
    assert oc.classify_alert_outcome("  prevented (Blocked) ", None) == oc.ALERT_PREVENTED
    assert oc.classify_alert_outcome("Detected (Reported)", "BLOCKED") == oc.ALERT_DETECTED

def test_classify_falls_back_to_raw_action():
    assert oc.classify_alert_outcome(None, "BLOCKED_14") == oc.ALERT_PREVENTED
    assert oc.classify_alert_outcome(None, "PROMPT_ALLOW") == oc.ALERT_DETECTED
    assert oc.classify_alert_outcome("", None) == oc.ALERT_UNKNOWN
    assert oc.classify_alert_outcome(None, "NOT_AVAILABLE") == oc.ALERT_UNKNOWN

def test_prevent_needs_both_witnesses():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="prevented",
                            termination="anchor_terminated", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("prevented", "pass", None)
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="prevented",
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.PREVENTION_ALERT_WITHOUT_TERMINATION)

def test_forged_host_death_without_alert_is_pending_never_pass():   # Review Focus 1
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination="signaled", window_closed=True, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.PREVENTION_UNCONFIRMED_NO_ALERT)

def test_detected_not_prevented_fails_but_unconfirmed_canary_downgrades_to_pending():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="detected",
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("detected", "fail", oc.DETECTED_NOT_PREVENTED)
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="detected",
                            termination="exited", window_closed=False, block_mode_status="block_mode_unconfirmed")
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.BLOCK_MODE_UNCONFIRMED)

def test_missed_only_when_window_closed_and_nothing_fired():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination="exited", window_closed=True, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict) == ("missed", "fail")
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", None)

def test_beacon_lost_and_unknown_action_cap_at_pending():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="prevented",
                            termination="beacon_lost", window_closed=False, block_mode_status=None)
    assert v.reason == oc.PREVENTION_UNCONFIRMED_BEACON_LOST and v.kpi_verdict == "pending"
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome="unknown",
                            termination="signaled", window_closed=False, block_mode_status=None)
    assert v.reason == oc.PREVENTION_ALERT_ACTION_UNKNOWN and v.outcome == "pending"

def test_report_mode_scores_prevent_as_detect_and_flags_contradiction():
    v = oc.evaluate_prevent(agent_profile_mode="report", alert_outcome="detected",
                            termination="exited", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("detected", None, None)
    v = oc.evaluate_prevent(agent_profile_mode="report", alert_outcome="prevented",
                            termination="signaled", window_closed=False, block_mode_status=None)
    assert (v.outcome, v.reason) == ("detected", oc.ATTESTATION_CONTRADICTED)

def test_host_outcomes_and_soak_reasons():
    assert oc.host_outcome_for_step("not_run", oc.UPSTREAM_SEVERED) == "not_reached"
    assert oc.host_outcome_for_step("not_run", oc.TOOL_ABSENT) == "not_executed"
    assert oc.host_outcome_for_step("exited", None) is None
    assert oc.soak_pending_reason(signal_type="ABIOC", soakless_attested=None, first_run_observed=False) == oc.ANALYTICS_ACTIVATION_PERIOD
    assert oc.soak_pending_reason(signal_type="Analytics", soakless_attested=False, first_run_observed=True) == oc.DEDUP_WINDOW
    assert oc.soak_pending_reason(signal_type="Analytics", soakless_attested=True, first_run_observed=False) is None
    assert oc.soak_pending_reason(signal_type="BIOC", soakless_attested=None, first_run_observed=False) is None


import pytest

_OK = dict(agent_profile_mode="block", alert_outcome=None, termination="exited",
           window_closed=False, block_mode_status=None)

@pytest.mark.parametrize("field,bad", [("agent_profile_mode", "bogus"),
                                       ("alert_outcome", "Prevented"),
                                       ("termination", "killed")])
def test_evaluate_prevent_raises_on_out_of_vocabulary(field, bad):
    with pytest.raises(ValueError):
        oc.evaluate_prevent(**{**_OK, field: bad})

def test_host_outcome_raises_on_unrecognised_inputs():
    with pytest.raises(ValueError):
        oc.host_outcome_for_step("killed", None)
    with pytest.raises(ValueError):
        oc.host_outcome_for_step("not_run", "SOMETHING_ELSE")

def test_termination_none_is_no_host_witness():
    v = oc.evaluate_prevent(**{**_OK, "alert_outcome": "prevented", "termination": None})
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.PREVENTION_ALERT_WITHOUT_TERMINATION)

@pytest.mark.parametrize("closed", [True, False])
def test_unknown_alert_without_host_witness_is_never_a_miss(closed):
    v = oc.evaluate_prevent(**{**_OK, "alert_outcome": "unknown", "window_closed": closed})
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.PREVENTION_ALERT_ACTION_UNKNOWN)


def test_upstream_failed_reason_exists_and_maps_to_not_reached():
    assert oc.UPSTREAM_FAILED == "UPSTREAM_FAILED"
    assert oc.host_outcome_for_step("not_run", oc.UPSTREAM_FAILED) == "not_reached"


def test_evidence_incomplete_reason_exists():
    assert isinstance(oc.EVIDENCE_INCOMPLETE, str) and oc.EVIDENCE_INCOMPLETE == "EVIDENCE_INCOMPLETE"


def test_pass_withheld_unscoped_reason_exists():
    assert isinstance(oc.PASS_WITHHELD_UNSCOPED, str) and oc.PASS_WITHHELD_UNSCOPED == "PASS_WITHHELD_UNSCOPED"

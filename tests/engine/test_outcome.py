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


import pytest


@pytest.mark.parametrize("outcome", [None, "prevented", "detected", "missed", "pending",
                                     "not_reached", "not_executed"])
@pytest.mark.parametrize("reason", [None, "OTHER", oc.ANALYTICS_ACTIVATION_PERIOD, oc.DEDUP_WINDOW])
def test_is_in_denominator_matrix(outcome, reason):
    expected = True
    if outcome in ("not_reached", "not_executed"):
        expected = False
    elif outcome == "pending" and reason in (oc.ANALYTICS_ACTIVATION_PERIOD, oc.DEDUP_WINDOW):
        expected = False
    assert oc.is_in_denominator(outcome, reason) is expected


def test_single_definition_of_soak_reasons_and_denominator_rule():
    """AST drift guard: the rule lives once, in engine/outcome.py."""
    import ast
    import pathlib

    core = pathlib.Path(__file__).resolve().parents[2] / "core"
    defs = {"SOAK_REASONS": [], "is_in_denominator": []}
    for path in core.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in defs:
                defs[node.name].append(str(path))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id in defs:
                        defs[t.id].append(str(path))
    for name, where in defs.items():
        assert len(where) == 1 and where[0].endswith("engine/outcome.py"), (name, where)


# ── final review I1 — a miss needs the host's `exited`, not its silence ────

def test_host_evidence_absent_reason_exists():
    assert oc.HOST_EVIDENCE_ABSENT == "HOST_EVIDENCE_ABSENT"


def test_block_prevent_without_host_record_is_pending_never_missed():
    """Spec §5.3: `missed` is `none, window closed | exited (0)`. With no host
    record (push mode, an old beacon, a staging failure, a reconcile before
    /complete) we do not know the step ran, so silence is not a miss."""
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination=None, window_closed=True, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("pending", "pending", oc.HOST_EVIDENCE_ABSENT)


def test_block_prevent_with_exited_on_record_is_still_missed():
    v = oc.evaluate_prevent(agent_profile_mode="block", alert_outcome=None,
                            termination="exited", window_closed=True, block_mode_status=None)
    assert (v.outcome, v.kpi_verdict, v.reason) == ("missed", "fail", None)


# ── final review M2 — attested soakless means no dedup hold either ─────────

def test_soakless_attested_lifts_the_dedup_window_too():
    """Spec §5.5: DEDUP_WINDOW applies on a pair's second run with
    `soakless_attested != true`; with soakless attested there is no hold."""
    assert oc.soak_pending_reason(signal_type="Analytics", soakless_attested=True,
                                  first_run_observed=True) is None
    assert oc.soak_pending_reason(signal_type="ABIOC", soakless_attested=None,
                                  first_run_observed=True) == oc.DEDUP_WINDOW


# --- residual R4: one status rule for a Result row ----------------------------

def test_row_status_outcome_wins_and_an_unjudged_row_is_pending_not_missed():
    assert oc.row_status("missed", False) == "missed"
    assert oc.row_status("prevented", True) == "prevented"
    assert oc.row_status("pending", True) == "pending"
    # Seed value, never reconciled: awaiting judgement, not a miss.
    assert oc.row_status(None, False) == "pending"
    assert oc.row_status(None, None) == "pending"
    # A manual validation (incl. pre-branch runs) with no outcome.
    assert oc.row_status(None, True) == "detected"


def test_row_status_raises_on_an_unknown_outcome():
    with pytest.raises(ValueError):
        oc.row_status("blocked", True)

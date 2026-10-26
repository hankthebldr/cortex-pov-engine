"""Prevention/detection outcome vocabulary and the two-witness verdict rules.

Pure: no I/O, no imports from ``models`` or ``connectors``. A ``prevent``
expectation is only credited when two independent witnesses agree -- the
alert says "Prevented" AND the host says the process was killed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# --- host termination vocabulary -------------------------------------------
TERMINATION_EXITED = "exited"
TERMINATION_SIGNALED = "signaled"
TERMINATION_ANCHOR_TERMINATED = "anchor_terminated"
TERMINATION_BEACON_LOST = "beacon_lost"
TERMINATION_TIMEOUT = "timeout"
TERMINATION_ABORTED = "aborted"
TERMINATION_NOT_RUN = "not_run"
TERMINATIONS = frozenset({
    TERMINATION_EXITED, TERMINATION_SIGNALED, TERMINATION_ANCHOR_TERMINATED,
    TERMINATION_BEACON_LOST, TERMINATION_TIMEOUT, TERMINATION_ABORTED,
    TERMINATION_NOT_RUN,
})
HOST_WITNESS = frozenset({TERMINATION_SIGNALED, TERMINATION_ANCHOR_TERMINATED})

# --- result outcomes -------------------------------------------------------
OUTCOME_PREVENTED = "prevented"
OUTCOME_DETECTED = "detected"
OUTCOME_MISSED = "missed"
OUTCOME_PENDING = "pending"
OUTCOME_NOT_REACHED = "not_reached"
OUTCOME_NOT_EXECUTED = "not_executed"
OUTCOMES = frozenset({
    OUTCOME_PREVENTED, OUTCOME_DETECTED, OUTCOME_MISSED, OUTCOME_PENDING,
    OUTCOME_NOT_REACHED, OUTCOME_NOT_EXECUTED,
})
EXCLUDED_FROM_DENOMINATOR = frozenset({OUTCOME_NOT_REACHED, OUTCOME_NOT_EXECUTED})

# --- alert outcomes --------------------------------------------------------
ALERT_PREVENTED = "prevented"
ALERT_DETECTED = "detected"
ALERT_UNKNOWN = "unknown"

# --- reasons (value == name) ----------------------------------------------
PREVENTION_ALERT_WITHOUT_TERMINATION = "PREVENTION_ALERT_WITHOUT_TERMINATION"
PREVENTION_UNCONFIRMED_NO_ALERT = "PREVENTION_UNCONFIRMED_NO_ALERT"
PREVENTION_UNCONFIRMED_BEACON_LOST = "PREVENTION_UNCONFIRMED_BEACON_LOST"
PREVENTION_ALERT_ACTION_UNKNOWN = "PREVENTION_ALERT_ACTION_UNKNOWN"
DETECTED_NOT_PREVENTED = "DETECTED_NOT_PREVENTED"
BLOCK_MODE_UNCONFIRMED = "BLOCK_MODE_UNCONFIRMED"
ATTESTATION_CONTRADICTED = "ATTESTATION_CONTRADICTED"
ANALYTICS_ACTIVATION_PERIOD = "ANALYTICS_ACTIVATION_PERIOD"
DEDUP_WINDOW = "DEDUP_WINDOW"
UPSTREAM_SEVERED = "UPSTREAM_SEVERED"
UPSTREAM_NOT_EXECUTED = "UPSTREAM_NOT_EXECUTED"
UPSTREAM_FAILED = "UPSTREAM_FAILED"
TOOL_ABSENT = "TOOL_ABSENT"
TOOL_PRESENCE_UNCHECKED = "TOOL_PRESENCE_UNCHECKED"
#: A silent row's window closed, but the pull was unscoped, truncated, lossy or
#: a hand-picked manual import: silence there is not evidence of a miss.
EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
#: The two witnesses agreed, but the pull was unscoped: the alert could have
#: come from any endpoint, so a row-level PASS is withheld.
PASS_WITHHELD_UNSCOPED = "PASS_WITHHELD_UNSCOPED"

#: Soak reasons: a row silent for a reason that says nothing about the detector.
SOAK_REASONS = frozenset({ANALYTICS_ACTIVATION_PERIOD, DEDUP_WINDOW})


def is_in_denominator(outcome: Optional[str], outcome_reason: Optional[str]) -> bool:
    """The one efficacy-denominator rule: False for rows whose step never ran
    (``EXCLUDED_FROM_DENOMINATOR``) and for soak-pending rows; True otherwise."""
    if outcome in EXCLUDED_FROM_DENOMINATOR:
        return False
    if outcome == "pending" and outcome_reason in SOAK_REASONS:
        return False
    return True


# --- agent profile / block-mode status ------------------------------------
PROFILE_MODES = frozenset({"report", "block", "unknown"})
BLOCK_MODE_CONFIRMED = "block_mode_confirmed"
BLOCK_MODE_UNCONFIRMED_STATUS = "block_mode_unconfirmed"
CANARY_NOT_AVAILABLE = "canary_not_available"

_ALERT_OUTCOMES = frozenset({ALERT_PREVENTED, ALERT_DETECTED, ALERT_UNKNOWN})
_RAW_PREVENTED = ("BLOCKED", "PROMPT_BLOCK")
_RAW_DETECTED = ("REPORTED", "DETECTED", "POST_DETECTED", "SCANNED", "DOWNLOAD", "PROMPT_ALLOW")


def classify_alert_outcome(action_pretty: Optional[str], action: Optional[str]) -> str:
    """Classify an alert as prevented/detected/unknown (pretty prefix, then raw action)."""
    pretty = (action_pretty or "").strip().lower()
    if pretty.startswith("prevented"):
        return ALERT_PREVENTED
    if pretty.startswith("detected"):
        return ALERT_DETECTED
    raw = (action or "").strip().upper()
    if raw.startswith(_RAW_PREVENTED):
        return ALERT_PREVENTED
    if raw.startswith(_RAW_DETECTED):
        return ALERT_DETECTED
    return ALERT_UNKNOWN


def host_outcome_for_step(termination: Optional[str], reason: Optional[str]) -> Optional[str]:
    """Host-side override of a step's Results, or None when the host has no say."""
    if termination is not None and termination not in TERMINATIONS:
        raise ValueError(f"unrecognised termination: {termination!r}")
    if termination != TERMINATION_NOT_RUN:
        return None
    if reason in (UPSTREAM_SEVERED, UPSTREAM_FAILED):
        return OUTCOME_NOT_REACHED
    if reason in (TOOL_ABSENT, UPSTREAM_NOT_EXECUTED):
        return OUTCOME_NOT_EXECUTED
    raise ValueError(f"not_run with unrecognised reason: {reason!r}")


@dataclass(frozen=True)
class PreventVerdict:
    outcome: str
    kpi_verdict: Optional[str]
    reason: Optional[str]


def _pending(reason: Optional[str] = None) -> PreventVerdict:
    return PreventVerdict(OUTCOME_PENDING, "pending", reason)


def evaluate_prevent(*, agent_profile_mode: str, alert_outcome: Optional[str],
                     termination: Optional[str], window_closed: bool,
                     block_mode_status: Optional[str]) -> PreventVerdict:
    """Evaluate a ``prevent`` expectation (spec section 5.3)."""
    if agent_profile_mode not in PROFILE_MODES:
        raise ValueError(f"unrecognised agent_profile_mode: {agent_profile_mode!r}")
    if alert_outcome is not None and alert_outcome not in _ALERT_OUTCOMES:
        raise ValueError(f"unrecognised alert_outcome: {alert_outcome!r}")
    if termination is not None and termination not in TERMINATIONS:
        raise ValueError(f"unrecognised termination: {termination!r}")

    if agent_profile_mode != "block":
        # Report/unknown mode: scored as a detect; never graded as prevention.
        if alert_outcome is not None:
            reason = ATTESTATION_CONTRADICTED if alert_outcome == ALERT_PREVENTED else None
            return PreventVerdict(OUTCOME_DETECTED, None, reason)
        return PreventVerdict(OUTCOME_MISSED if window_closed else OUTCOME_PENDING, None, None)

    # termination None = no host record yet: no host witness.
    host_witness = termination in HOST_WITNESS

    if alert_outcome == ALERT_PREVENTED:
        if termination == TERMINATION_BEACON_LOST:
            v = _pending(PREVENTION_UNCONFIRMED_BEACON_LOST)
        elif host_witness:
            v = PreventVerdict(OUTCOME_PREVENTED, "pass", None)
        else:
            v = _pending(PREVENTION_ALERT_WITHOUT_TERMINATION)
    elif alert_outcome == ALERT_DETECTED:
        if termination == TERMINATION_BEACON_LOST:
            v = _pending(PREVENTION_UNCONFIRMED_BEACON_LOST)
        else:
            v = PreventVerdict(OUTCOME_DETECTED, "fail", DETECTED_NOT_PREVENTED)
    elif alert_outcome == ALERT_UNKNOWN:
        # An alert fired; "missed" would be false.
        v = _pending(PREVENTION_ALERT_ACTION_UNKNOWN)
    else:  # no alert
        if host_witness:
            v = _pending(PREVENTION_UNCONFIRMED_NO_ALERT)
        elif termination == TERMINATION_BEACON_LOST:
            v = _pending(PREVENTION_UNCONFIRMED_BEACON_LOST)
        elif window_closed:
            v = PreventVerdict(OUTCOME_MISSED, "fail", None)
        else:
            v = _pending(None)

    if v.kpi_verdict == "fail" and block_mode_status == BLOCK_MODE_UNCONFIRMED_STATUS:
        return _pending(BLOCK_MODE_UNCONFIRMED)
    return v


def soak_pending_reason(*, signal_type: str, soakless_attested: Optional[bool],
                        first_run_observed: bool) -> Optional[str]:
    """Why an Analytics/ABIOC expectation must stay pending, or None."""
    if signal_type not in ("Analytics", "ABIOC"):
        return None
    if first_run_observed:
        return DEDUP_WINDOW
    if soakless_attested is not True:
        return ANALYTICS_ACTIVATION_PERIOD
    return None

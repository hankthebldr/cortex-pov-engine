"""Launch advisory: the SOC Framework's Auto Triage may seal a case mid-run.

Auto Triage, when live, closes New unstarred cases older than ``window_hours``
with ``aggregated_score <= score_threshold`` as ``resolved_other``, which XSIAM
never reopens. A paired run, or a scenario whose ABIOC / Analytics alerts can
match up to 24 h later, can therefore see its chain split across cases.

Pure and advisory: it reads only the stored layer (zero outbound calls), never
refuses a launch, and CortexSim never acts on the tenant. Any problem with the
stored shape degrades to ``SOCFW_RESPONSE_LAYER_UNKNOWN``; nothing raises.

The trigger is ``auto_triage.live is True`` in either ``PRESENT`` or
``PRESENT_UNREAD``: production ``auto_triage_closed`` rows prove triage live
even when the framework's lists cannot be read (spec 4.1). Both codes take the
trigger's level: ``warn`` on a paired run, ``info`` otherwise.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from connectors.base import coerce_utc
from connectors.socfw import provenance_note

SOCFW_TRIAGE_MAY_SEAL = "SOCFW_TRIAGE_MAY_SEAL"
SOCFW_RESPONSE_LAYER_UNKNOWN = "SOCFW_RESPONSE_LAYER_UNKNOWN"
STALE_AFTER = timedelta(hours=24)

# Detection types whose alerts can match up to 24 h after the run.
LATE_DETECTION_TYPES = frozenset({"ABIOC", "Analytics"})
# States in which the framework is installed, so a live Auto Triage can act.
_INSTALLED = frozenset({"PRESENT", "PRESENT_UNREAD"})

_DOC = "docs/lab/2026-11-11-run-alongside-soc-framework.md"
# Pinned to soc-optimization-unified 3.31.4 (secops-framework d222ba87):
#  (1) SOCAutoTriageScoreFilter fetches starred=false only and never closes a
#      starred case;
#  (2) SOCFWConfig command=triage takes score_threshold / window_hours / apply,
#      and every change is a dry run unless apply=yes (SOCFWConfig.yml);
#  (3) Auto Triage's own dry run is the dry_run argument of
#      SOCAutoTriageScoreFilter, the close task of JOB - Auto Triage V3
#      (SOCAutoTriageScoreFilter.yml); the JOB does not pass it by default.
_REMEDIATION = (
    "Tenant-side options, any one is enough: "
    "(1) star the POV's cases (Auto Triage skips starred cases); "
    "(2) run !SOCFWConfig command=triage window_hours=<n> apply=yes with <n> "
    "longer than the POV, since Auto Triage never closes a case younger than "
    "the window; "
    "(3) run the Auto Triage JOB (JOB - Auto Triage V3) with dry_run=true on its "
    "SOCAutoTriageScoreFilter task, so it reports what it would close and closes "
    "nothing. "
    f"See {_DOC}. CortexSim does not change the tenant."
)
_RERUN = "Run preflight on this tenant to refresh it."


def _adv(code: str, level: str, tenant: str, detail: str) -> dict:
    return {"code": code, "level": level, "tenant": tenant,
            "detail": detail, "remediation": _REMEDIATION}


def _unknown(tenant: str, level: str, why: str, tail: str = _RERUN) -> dict:
    return _adv(
        SOCFW_RESPONSE_LAYER_UNKNOWN, level, tenant,
        f"Cannot tell whether the SOC Framework's Auto Triage may close a case "
        f"during this run: {why}." + (f" {tail}" if tail else ""))


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _scope(at: dict) -> str:
    """Which cases Auto Triage may close. A value that was not read says so."""
    window = at.get("window_hours")
    age = (f"older than {window:g} h{provenance_note(at.get('window_source'))}"
           if _num(window) else "older than the Auto Triage window (not read)")
    if at.get("score_off") is True:
        return (f"every New unstarred case {age} is eligible (the triage score is "
                f"switched off){provenance_note(at.get('threshold_source'))}")
    threshold = at.get("score_threshold")
    if _num(threshold):
        return (f"New unstarred cases {age} with aggregated_score <= {threshold:g}"
                f"{provenance_note(at.get('threshold_source'))} are eligible")
    return (f"New unstarred cases {age} at or under the triage score threshold "
            f"(not read) are eligible")


def triage_advisories(
    *,
    tenant: str,
    detection_types: set[str],
    paired: bool,
    response_layer: Optional[dict],
    read_at: Optional[datetime],
    now: datetime,
) -> list[dict]:
    level = "warn" if paired else "info"
    try:
        late = bool(set(detection_types or ()) & LATE_DETECTION_TYPES)
        if not (paired or late):
            return []

        # Staleness comes before every state-based return: a weeks-old ABSENT or
        # live=False says nothing about the tenant today (spec 4.1).
        if response_layer is None and read_at is None:
            return [_unknown(tenant, level,
                             "no SOC Framework probe is stored for this tenant — run preflight",
                             tail="")]
        seen = coerce_utc(read_at)
        if seen is None:
            return [_unknown(tenant, level, "the stored probe has no read time")]
        if (coerce_utc(now) - seen) > STALE_AFTER:
            return [_unknown(tenant, level, "the stored probe is older than 24 hours")]

        if not isinstance(response_layer, dict):
            return [_unknown(tenant, level, "no response-layer probe is stored")]
        state = response_layer.get("state")
        if state == "ABSENT":
            return []
        if state not in _INSTALLED:
            return [_unknown(tenant, level, f"the stored probe state is {state!r}")]

        at = response_layer.get("auto_triage")
        if not isinstance(at, dict):
            return [_unknown(tenant, level, "the stored probe has no Auto Triage block")]
        live = at.get("live")
        if live is False:
            return []
        if live is not True:
            # Re-running preflight gives the same answer: the reads that would
            # settle it (the JOB schedule, unreadable lists) are not made.
            return [_unknown(
                tenant, level,
                f"the framework is installed (state {state}) but the stored probe "
                f"does not establish whether Auto Triage is live",
                tail=("Re-running preflight will not settle it; treat Auto Triage as "
                      "possibly live and protect this run's cases."))]

        why = ("this is a paired run" if paired else
               "this scenario has ABIOC/Analytics detections that can match up to 24 h later")
        detail = (f"Auto Triage is live on this tenant"
                  f"{provenance_note(at.get('live_source'))} and {why}: {_scope(at)}. Auto "
                  f"Triage closes an eligible case as resolved_other, which XSIAM never "
                  f"reopens, so the chain may split across cases.")
        return [_adv(SOCFW_TRIAGE_MAY_SEAL, level, tenant, detail)]
    except Exception as exc:  # advisory only: never fail a launch
        return [_unknown(tenant, level,
                         f"the stored probe was unreadable ({type(exc).__name__})")]

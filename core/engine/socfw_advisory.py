"""Launch advisory: the SOC Framework's Auto Triage may seal a case mid-run.

Auto Triage, when live, closes New unstarred cases older than ``window_hours``
with ``aggregated_score <= score_threshold`` as ``resolved_other``, which XSIAM
never reopens. A paired run, or a scenario whose ABIOC / Analytics alerts can
match up to 24 h later, can therefore see its chain split across cases.

Pure and advisory: it reads only the stored layer (zero outbound calls), never
refuses a launch, and CortexSim never acts on the tenant. Any problem with the
stored shape degrades to ``SOCFW_RESPONSE_LAYER_UNKNOWN``; nothing raises.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from connectors.base import coerce_utc

SOCFW_TRIAGE_MAY_SEAL = "SOCFW_TRIAGE_MAY_SEAL"
SOCFW_RESPONSE_LAYER_UNKNOWN = "SOCFW_RESPONSE_LAYER_UNKNOWN"
STALE_AFTER = timedelta(hours=24)

# Detection types whose alerts can match up to 24 h after the run.
LATE_DETECTION_TYPES = frozenset({"ABIOC", "Analytics"})

_DOC = "docs/lab/2026-11-11-run-alongside-soc-framework.md"
_REMEDIATION = (
    "Tenant-side options, any one is enough: "
    "(1) star the cases this run produces so Auto Triage skips them; "
    "(2) set Auto Triage to dry run for the run with "
    "!SOCFWConfig command=triage (dry_run); "
    "(3) widen the window or lower the score threshold with "
    "!SOCFWConfig command=triage. "
    f"See {_DOC}. CortexSim does not change the tenant."
)


def _adv(code: str, level: str, tenant: str, detail: str, remediation: str) -> dict:
    return {"code": code, "level": level, "tenant": tenant,
            "detail": detail, "remediation": remediation}


def _unknown(tenant: str, why: str) -> dict:
    return _adv(
        SOCFW_RESPONSE_LAYER_UNKNOWN, "info", tenant,
        f"Cannot tell whether the SOC Framework's Auto Triage may close a case "
        f"during this run: {why}. Run preflight on this tenant to refresh it.",
        _REMEDIATION)


def triage_advisories(
    *,
    tenant: str,
    detection_types: set[str],
    paired: bool,
    response_layer: Optional[dict],
    read_at: Optional[datetime],
    now: datetime,
) -> list[dict]:
    try:
        late = bool(set(detection_types or ()) & LATE_DETECTION_TYPES)
        if not (paired or late):
            return []
        level = "warn" if paired else "info"

        if not isinstance(response_layer, dict):
            return [_unknown(tenant, "no response-layer probe is stored")]
        state = response_layer.get("state")
        if state == "ABSENT":
            return []
        if state != "PRESENT":
            return [_unknown(tenant, f"the stored probe state is {state!r}")]

        seen = coerce_utc(read_at)
        if seen is None or (coerce_utc(now) - seen) > STALE_AFTER:
            return [_unknown(tenant, "the stored probe is older than 24 hours")]

        at = response_layer.get("auto_triage")
        if not isinstance(at, dict):
            return [_unknown(tenant, "the stored probe has no Auto Triage block")]
        live = at.get("live")
        if live is False:
            return []
        if live is not True:
            return [_unknown(tenant, "Auto Triage liveness was not determined")]

        window = at.get("window_hours")
        if at.get("score_off") is True:
            scope = (f"every New unstarred case older than {window} h is eligible "
                     f"(the triage score is switched off)")
        else:
            scope = (f"New unstarred cases older than {window} h with "
                     f"aggregated_score <= {at.get('score_threshold')} are eligible")
        why = ("this is a paired run" if paired else
               "this scenario has ABIOC/Analytics detections that can match up to 24 h later")
        detail = (f"Auto Triage is live on this tenant and {why}: {scope}, and are "
                  f"closed as resolved_other, which XSIAM never reopens. The chain "
                  f"may split across cases.")
        return [_adv(SOCFW_TRIAGE_MAY_SEAL, level, tenant, detail, _REMEDIATION)]
    except Exception as exc:  # advisory only: never fail a launch
        return [_unknown(tenant, f"the stored probe was unreadable ({type(exc).__name__})")]

"""
Events — translate one ``Attempt`` (or ``run_meta``) into the ECS-shape the
CortexSim EAL audit pipeline ingests.

Field naming matches ``core/eal_simulator/audit.py`` so the
``airs_prompt_attack`` plugin can forward records straight into the same
audit log other EAL plugins write.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any, Optional


_HOSTNAME = socket.gethostname()


def attempt_to_ecs(
    attempt: dict[str, Any],
    *,
    campaign_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    plugin: str = "airs_prompt_attack",
) -> dict[str, Any]:
    """Translate one Attempt JSONL line into an ECS-shaped event."""
    outcome = {
        "vuln": "success",
        "clean": "success",
        "error": "failure",
    }.get(attempt.get("outcome", ""), "unknown")

    detector_results = attempt.get("detector_results") or {}
    detected_by = sorted(name for name, hit in detector_results.items() if hit)

    return {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "ecs": {"version": "8.11"},
        "event": {
            "kind": "event",
            "category": ["network"],
            "type": ["info"],
            "action": "airs_probe_attempt",
            "outcome": outcome,
            "module": "cortexsim-eal-simulator",
            "dataset": "cortexsim.airs",
        },
        "host": {"name": _HOSTNAME},
        "service": {
            "name": "cortex-prompt-attacker",
            "type": "attacker",
        },
        "message": (
            f"probe={attempt.get('probe_classname')} "
            f"outcome={attempt.get('outcome')} "
            f"detected={','.join(detected_by) if detected_by else '-'}"
        ),
        "cortexsim": _drop_none({
            "campaign_id": campaign_id,
            "run_id": run_id,
            "step_id": step_id,
            "plugin": plugin,
            "probe_classname": attempt.get("probe_classname"),
            "owasp_id": attempt.get("owasp_id"),
            "severity": attempt.get("severity"),
            "outcome": attempt.get("outcome"),
            "mutators_applied": attempt.get("mutators_applied"),
            "duration_seconds": attempt.get("duration_seconds"),
            "detector_results": detector_results,
            "detected_by": detected_by,
            "target_url": (attempt.get("targets") or [None])[0],
        }),
    }


def run_meta_to_ecs(
    meta: dict[str, Any],
    *,
    campaign_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    plugin: str = "airs_prompt_attack",
) -> dict[str, Any]:
    return {
        "@timestamp": meta.get("@timestamp")
            or datetime.now(timezone.utc).isoformat(),
        "ecs": {"version": "8.11"},
        "event": {
            "kind": "event",
            "category": ["process"],
            "type": ["start"],
            "action": "airs_probe_run_started",
            "outcome": "success",
            "module": "cortexsim-eal-simulator",
            "dataset": "cortexsim.airs",
        },
        "host": {"name": _HOSTNAME},
        "service": {"name": "cortex-prompt-attacker", "type": "attacker"},
        "message": (
            f"airs run started: probes={meta.get('probes_total')} "
            f"target={meta.get('target_url')}"
        ),
        "cortexsim": _drop_none({
            "campaign_id": campaign_id,
            "run_id": run_id,
            "step_id": step_id,
            "plugin": plugin,
            "probes_total": meta.get("probes_total"),
            "target_url": meta.get("target_url"),
            "mutators": meta.get("mutators"),
            "scorers": meta.get("scorers"),
        }),
    }


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}

"""
ActionResult / run_meta → ECS event mapper.

Same shape conventions as the cortex-prompt-attacker events module:
``cortexsim.*`` namespace under the ECS envelope, drop-None to keep
events compact.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any, Optional


_HOSTNAME = socket.gethostname()


def action_result_to_ecs(
    result: dict[str, Any],
    *,
    campaign_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    plugin: str = "browser_attack_runner",
) -> dict[str, Any]:
    """Translate one ``action_attempt`` JSONL line into an ECS event."""
    outcome = {
        "success": "success",
        "blocked": "success",   # blocked-by-policy is a *positive* outcome
        "failure": "failure",
    }.get(result.get("outcome", ""), "unknown")

    return {
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "ecs": {"version": "8.11"},
        "event": {
            "kind": "event",
            "category": ["web"],
            "type": ["info"],
            "action": f"browser_{result.get('action_name', 'unknown')}",
            "outcome": outcome,
            "module": "cortexsim-eal-simulator",
            "dataset": "cortexsim.browser",
        },
        "host": {"name": _HOSTNAME},
        "service": {"name": "cortex-browser-attacker", "type": "attacker"},
        "url": _drop_none({
            "full": result.get("page_url") or result.get("target_url"),
            "domain": result.get("target_origin"),
        }) or None,
        "message": (
            f"browser action={result.get('action_name')} "
            f"outcome={result.get('outcome')} "
            f"target={result.get('target_origin') or '-'}"
        ),
        "cortexsim": _drop_none({
            "campaign_id": campaign_id,
            "run_id": run_id,
            "step_id": step_id,
            "plugin": plugin,
            "action_name": result.get("action_name"),
            "target_url": result.get("target_url"),
            "target_origin": result.get("target_origin"),
            "outcome": result.get("outcome"),
            "duration_seconds": result.get("duration_seconds"),
            "page_url": result.get("page_url"),
            "page_title": result.get("page_title"),
            "expected_detection": result.get("expected_detection"),
            "cortex_canary": result.get("cortex_canary"),
            "notes": result.get("notes"),
            "error": result.get("error"),
        }),
    }


def run_meta_to_ecs(
    meta: dict[str, Any],
    *,
    campaign_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    plugin: str = "browser_attack_runner",
) -> dict[str, Any]:
    return {
        "@timestamp": meta.get("@timestamp")
            or datetime.now(timezone.utc).isoformat(),
        "ecs": {"version": "8.11"},
        "event": {
            "kind": "event",
            "category": ["process"],
            "type": ["start"],
            "action": "browser_campaign_started",
            "outcome": "success",
            "module": "cortexsim-eal-simulator",
            "dataset": "cortexsim.browser",
        },
        "host": {"name": _HOSTNAME},
        "service": {"name": "cortex-browser-attacker", "type": "attacker"},
        "message": (
            f"browser campaign started: name={meta.get('name')} "
            f"channel={meta.get('browser_channel')} "
            f"actions={meta.get('action_count')}"
        ),
        "cortexsim": _drop_none({
            "campaign_id": campaign_id or meta.get("campaign_id"),
            "run_id": run_id,
            "step_id": step_id,
            "plugin": plugin,
            "name": meta.get("name"),
            "action_count": meta.get("action_count"),
            "browser_channel": meta.get("browser_channel"),
            "target_allowlist": meta.get("target_allowlist"),
        }),
    }


def _drop_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}

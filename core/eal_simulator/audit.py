"""
ECS-formatted structured audit logger.

Every executed campaign — and every individual plugin step — emits one or more
events conforming to a subset of the Elastic Common Schema (ECS) so that
downstream SIEMs (XSIAM, Elastic, Splunk) can parse simulator activity
without a custom mapping.

We deliberately avoid pulling in the ``ecs-logging`` package as a hard
dependency. The ECS subset we use is small and stable enough to hand-build,
and SimCore tries to keep the requirements list minimal.
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Optional


_HOSTNAME = socket.gethostname()


def ecs_event(
    *,
    action: str,
    outcome: str = "success",
    category: str = "process",
    type_: str = "info",
    message: str = "",
    campaign_id: Optional[str] = None,
    run_id: Optional[str] = None,
    step_id: Optional[str] = None,
    plugin: Optional[str] = None,
    target: Optional[str] = None,
    bytes_sent: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build an ECS-shaped event dict.

    The keys mirror Elastic's recommended core fields so XSIAM's ECS parser
    picks them up natively. ``extra`` is shallow-merged under a ``cortexsim``
    namespace to avoid colliding with reserved ECS fields.
    """
    now = datetime.now(timezone.utc).isoformat()
    event: dict[str, Any] = {
        "@timestamp": now,
        "ecs": {"version": "8.11"},
        "event": {
            "kind": "event",
            "category": [category],
            "type": [type_],
            "action": action,
            "outcome": outcome,
            "module": "cortexsim-eal-simulator",
            "dataset": "cortexsim.eal",
        },
        "host": {"name": _HOSTNAME},
        "service": {"name": "cortexsim-eal-simulator", "type": "simulator"},
        "message": message,
        "cortexsim": {
            "campaign_id": campaign_id,
            "run_id": run_id,
            "step_id": step_id,
            "plugin": plugin,
            "target": target,
            "bytes_sent": bytes_sent,
        },
    }
    if extra:
        event["cortexsim"].update(extra)
    # Drop None values inside the cortexsim namespace to keep events compact.
    event["cortexsim"] = {k: v for k, v in event["cortexsim"].items() if v is not None}
    return event


class AuditLogger:
    """ECS-JSON audit emitter — one log line per event.

    The logger writes both to a configurable file path *and* through Python's
    logging facility (so existing SimCore log aggregation picks it up). Either
    sink can be disabled by passing ``None``.
    """

    def __init__(
        self,
        *,
        file_path: Optional[str] = None,
        python_logger_name: str = "cortexsim.eal.audit",
    ) -> None:
        self.file_path = file_path
        self._py_logger = logging.getLogger(python_logger_name) if python_logger_name else None
        self._fh: Optional[Any] = None
        if file_path:
            # Append-only; rotation is left to the host log shipper (logrotate).
            self._fh = open(file_path, "a", encoding="utf-8", buffering=1)

    def emit(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, separators=(",", ":"), default=str)
        if self._fh is not None:
            self._fh.write(line + "\n")
        if self._py_logger is not None:
            # Use INFO level so dev consoles still see audit lines.
            self._py_logger.info(line)

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            finally:
                self._fh = None

    def __enter__(self) -> "AuditLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

"""
ActionResult — one browser action's outcome.

Field naming intentionally rhymes with cortex-prompt-attacker's Attempt
dataclass and NVIDIA garak's Attempt so downstream tooling that reads
one of those JSONL streams can read this one with a tiny adapter.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


@dataclasses.dataclass
class ActionResult:
    # Lifecycle
    entry_type: str = "action_attempt"   # "action_attempt" | "run_meta"
    uuid: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))
    seq: int = 0
    status: str = "NEW"                  # NEW → STARTED → COMPLETE
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # Identity
    action_name: str = ""                # 'navigate' / 'paste' / etc.
    target_url: Optional[str] = None
    target_origin: Optional[str] = None  # parsed host of target_url
    params: dict[str, Any] = dataclasses.field(default_factory=dict)

    # Browser state at completion
    page_url: Optional[str] = None
    page_title: Optional[str] = None

    # Outcome
    outcome: str = "unknown"             # "success" | "failure" | "blocked"
    error: Optional[str] = None
    notes: dict[str, Any] = dataclasses.field(default_factory=dict)

    # Detection-validation extensions
    cortex_canary: Optional[str] = None  # canary marker the SOC can filter on
    expected_detection: Optional[str] = None  # human description of the BIOC

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.status = "STARTED"
        self.started_at = time.time()

    def complete(self, outcome: str, *, error: Optional[str] = None) -> None:
        self.status = "COMPLETE"
        self.completed_at = time.time()
        self.outcome = outcome
        if error is not None:
            self.error = error

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at is None or self.completed_at is None:
            return None
        return self.completed_at - self.started_at

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_type": self.entry_type,
            "uuid": self.uuid,
            "seq": self.seq,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "action_name": self.action_name,
            "target_url": self.target_url,
            "target_origin": self.target_origin,
            "params": self.params,
            "page_url": self.page_url,
            "page_title": self.page_title,
            "outcome": self.outcome,
            "error": self.error,
            "notes": self.notes,
            "cortex_canary": self.cortex_canary,
            "expected_detection": self.expected_detection,
        }


def run_meta(
    *,
    campaign_id: str,
    name: str,
    action_count: int,
    browser_channel: str,
    target_allowlist: list[str],
) -> dict[str, Any]:
    """Build the run_meta JSONL header — emitted once per run."""
    return {
        "entry_type": "run_meta",
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "campaign_id": campaign_id,
        "name": name,
        "action_count": action_count,
        "browser_channel": browser_channel,
        "target_allowlist": list(target_allowlist),
        "tool": "cortex-browser-attacker",
    }

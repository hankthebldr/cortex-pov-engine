"""
Attempt — one probe×mutator-chain×iteration result.

Field names mirror NVIDIA garak's ``Attempt`` dataclass where possible so
existing garak-aware tooling can read our JSONL without translation.
Garak schema reference (Apache-2.0): we copied field *names* only — schema
field names are not copyrightable. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


@dataclasses.dataclass
class Attempt:
    # Lifecycle
    entry_type: str = "attempt"          # "attempt" | "run_meta"
    uuid: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))
    seq: int = 0
    status: str = "NEW"                   # NEW → STARTED → COMPLETE
    started_at: Optional[float] = None    # epoch seconds, set when STARTED
    completed_at: Optional[float] = None  # epoch seconds, set when COMPLETE

    # Probe identification (mirrors garak)
    probe_classname: str = ""             # "ignore_previous_basic"
    probe_params: dict[str, Any] = dataclasses.field(default_factory=dict)

    # Targets / payloads
    targets: list[str] = dataclasses.field(default_factory=list)  # one URL
    prompt: str = ""
    mutated_prompt: str = ""              # post-mutator-chain
    mutators_applied: list[str] = dataclasses.field(default_factory=list)
    outputs: list[str] = dataclasses.field(default_factory=list)  # response bodies

    # Scorer outcomes
    detector_results: dict[str, bool] = dataclasses.field(default_factory=dict)
    detector_meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    # Roll-up
    outcome: str = "unknown"              # "vuln" | "clean" | "error"
    notes: dict[str, Any] = dataclasses.field(default_factory=dict)
    goal: str = ""                        # the probe's stated goal
    conversations: list[dict[str, str]] = dataclasses.field(default_factory=list)

    # CortexSim extensions
    owasp_id: Optional[str] = None
    severity: str = "low"

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.status = "STARTED"
        self.started_at = time.time()

    def complete(self, outcome: str) -> None:
        self.status = "COMPLETE"
        self.completed_at = time.time()
        self.outcome = outcome

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
            "probe_classname": self.probe_classname,
            "probe_params": self.probe_params,
            "targets": list(self.targets),
            "prompt": self.prompt,
            "mutated_prompt": self.mutated_prompt,
            "mutators_applied": list(self.mutators_applied),
            "outputs": list(self.outputs),
            "detector_results": dict(self.detector_results),
            "detector_meta": dict(self.detector_meta),
            "outcome": self.outcome,
            "notes": dict(self.notes),
            "goal": self.goal,
            "conversations": list(self.conversations),
            "owasp_id": self.owasp_id,
            "severity": self.severity,
        }


def run_meta(
    *,
    probes: int,
    target_url: str,
    mutators: list[str],
    scorers: list[str],
) -> dict[str, Any]:
    """Build a ``run_meta`` JSONL header line — emitted once per run.

    Distinguished from attempts by ``entry_type='run_meta'`` so a single
    JSONL file can mix the two record kinds.
    """
    return {
        "entry_type": "run_meta",
        "@timestamp": datetime.now(timezone.utc).isoformat(),
        "probes_total": probes,
        "target_url": target_url,
        "mutators": mutators,
        "scorers": scorers,
        "tool": "cortex-prompt-attacker",
    }

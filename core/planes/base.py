"""
CortexSim — shared detection-plane descriptor model.

Each active detection plane ships a :class:`PlaneDescriptor` describing its
identity, the Cortex engine it lands on, the detection-engine types it
exercises, the data sources / tooling that feed it, its default execution
identity, and the anchor MITRE ATT&CK techniques it covers. The descriptors are
declarative metadata only — they carry no execution logic and are import-safe
(no side effects, no I/O at import). They are consumed by the plane registry in
``core.planes`` and can be surfaced over the API/UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlaneDescriptor:
    """Declarative description of a single CortexSim detection plane.

    Attributes mirror the columns the API/UI consume for the plane selector and
    coverage views. The dataclass is frozen so registry entries are effectively
    immutable singletons.
    """

    #: Canonical plane id used throughout the codebase (matches scenario
    #: ``plane:`` values and the ``Scenario.plane`` ORM column). Uppercase.
    id: str
    #: Human-readable plane name shown in the UI.
    name: str
    #: The Cortex product / engine this plane's signals land on.
    cortex_engine: str
    #: Cortex detection-engine types this plane exercises. Subset of
    #: ``BIOC | XQL | Analytics | Correlation | IOC`` (the scenario schema
    #: detection-type vocabulary).
    detection_types: list[str] = field(default_factory=list)
    #: Data sources / tools / EAL plugins that feed signal into this plane.
    primary_sources: list[str] = field(default_factory=list)
    #: Anchor MITRE ATT&CK technique ids this plane covers (illustrative, not
    #: exhaustive — the authoritative coverage is the TTP card corpus).
    key_techniques: list[str] = field(default_factory=list)
    #: Default execution-harness identity scenarios on this plane run as.
    default_identity: str = "container-runtime"
    #: One-line summary of what the plane validates.
    summary: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-serialisable representation for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "cortex_engine": self.cortex_engine,
            "detection_types": list(self.detection_types),
            "primary_sources": list(self.primary_sources),
            "key_techniques": list(self.key_techniques),
            "default_identity": self.default_identity,
            "summary": self.summary,
        }

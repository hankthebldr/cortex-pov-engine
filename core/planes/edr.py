"""
CortexSim — EDR detection plane stub.
Phase 2 adds full logic; this module exports the plane identity and capability descriptors.
"""

from dataclasses import dataclass, field

PLANE_NAME: str = "EDR"

DETECTION_TYPES: list[str] = ["BIOC", "IOC", "Analytics"]


@dataclass
class EDRPlane:
    """Describes the EDR plane capabilities available in Phase 1."""

    name: str = PLANE_NAME
    detection_types: list[str] = field(default_factory=lambda: DETECTION_TYPES)
    cortex_engine: str = "Cortex XDR Agent"
    primary_sources: list[str] = field(default_factory=lambda: [
        "signalbench",
        "atomic-red-team",
        "MITRE-Turla-Carbon",
    ])
    key_techniques: list[str] = field(default_factory=lambda: [
        "T1055",   # Process Injection
        "T1053",   # Scheduled Task
        "T1059",   # Command and Scripting Interpreter
        "T1003",   # OS Credential Dumping
        "T1547",   # Boot or Logon Autostart Execution
    ])
    description: str = (
        "Endpoint Detection & Response plane — validates BIOC process/memory/persistence "
        "detections, IOC hash/IP/domain matches, and behavioral analytics via Cortex XDR Agent."
    )


EDR_PLANE = EDRPlane()

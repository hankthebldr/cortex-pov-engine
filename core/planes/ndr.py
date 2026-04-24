"""
CortexSim — NDR (Network Detection & Response) plane stub.
Phase 2 adds full logic; this module exports the plane identity and capability descriptors.
"""

from dataclasses import dataclass, field

PLANE_NAME: str = "NDR"

DETECTION_TYPES: list[str] = ["IOC", "Analytics"]


@dataclass
class NDRPlane:
    """Describes the NDR plane capabilities available in Phase 1."""

    name: str = PLANE_NAME
    detection_types: list[str] = field(default_factory=lambda: DETECTION_TYPES)
    cortex_engine: str = "Network Security / Firewall Analytics"
    primary_sources: list[str] = field(default_factory=lambda: [
        "ackbarx",
        "mocktaxii",
        "Responder",
    ])
    key_techniques: list[str] = field(default_factory=lambda: [
        "T1071",   # Application Layer Protocol (C2)
        "T1021",   # Remote Services (lateral movement)
        "T1557",   # Adversary-in-the-Middle
        "T1046",   # Network Service Discovery
        "T1190",   # Exploit Public-Facing Application
    ])
    description: str = (
        "Network Detection & Response plane — validates C2 traffic, lateral movement, "
        "and protocol anomaly detections via Network Security and Firewall Analytics."
    )


NDR_PLANE = NDRPlane()

"""
CortexSim — CDR (Container/Cloud Detection & Response) plane stub.
Phase 2 adds full logic; this module exports the plane identity and capability descriptors.
"""

from dataclasses import dataclass, field

PLANE_NAME: str = "CDR"

DETECTION_TYPES: list[str] = ["BIOC", "Analytics"]


@dataclass
class CDRPlane:
    """Describes the CDR plane capabilities available in Phase 1."""

    name: str = PLANE_NAME
    detection_types: list[str] = field(default_factory=lambda: DETECTION_TYPES)
    cortex_engine: str = "Cortex Cloud / Prisma Cloud Compute"
    primary_sources: list[str] = field(default_factory=lambda: [
        "hankthebldr/CDR",
        "xsiam-prisma-cdr-lab",
        "DEEPCE",
        "XMRig",
    ])
    key_techniques: list[str] = field(default_factory=lambda: [
        "T1610",   # Deploy Container
        "T1611",   # Escape to Host
        "T1613",   # Container and Resource Discovery
        "T1496",   # Resource Hijacking (cryptomining)
        "T1105",   # Ingress Tool Transfer
    ])
    description: str = (
        "Container Detection & Response plane — validates runtime BIOC, container analytics, "
        "and K8s anomaly detections via Cortex Cloud and Prisma Cloud Compute."
    )


CDR_PLANE = CDRPlane()

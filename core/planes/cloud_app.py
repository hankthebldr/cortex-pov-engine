"""
CortexSim — Cloud App Security plane stub.
Phase 2 adds full logic; this module exports the plane identity and capability descriptors.
"""

from dataclasses import dataclass, field

PLANE_NAME: str = "CLOUD_APP"

DETECTION_TYPES: list[str] = ["Analytics", "IOC"]


@dataclass
class CloudAppPlane:
    """Describes the Cloud App Security plane capabilities available in Phase 1."""

    name: str = PLANE_NAME
    detection_types: list[str] = field(default_factory=lambda: DETECTION_TYPES)
    cortex_engine: str = "Cortex Cloud App Security"
    primary_sources: list[str] = field(default_factory=lambda: [
        "gocortexbrokenbank",
    ])
    key_techniques: list[str] = field(default_factory=lambda: [
        "T1195",   # Supply Chain Compromise
        "T1528",   # Steal Application Access Token (OAuth abuse)
        "T1648",   # Serverless Execution
        "T1552",   # Unsecured Credentials in CI/CD
        "T1078",   # Valid Accounts (shadow IT)
    ])
    description: str = (
        "Cloud App Security plane — validates shadow IT, OAuth abuse, and CI/CD pipeline "
        "attack detections via Cortex Cloud App Security (ASPM scenarios)."
    )


CLOUD_APP_PLANE = CloudAppPlane()

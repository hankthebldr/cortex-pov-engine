"""
CortexSim — ITDR (Identity Threat Detection & Response) plane stub.
Phase 2 adds full logic; this module exports the plane identity and capability descriptors.
"""

from dataclasses import dataclass, field

PLANE_NAME: str = "ITDR"

DETECTION_TYPES: list[str] = ["BIOC", "Analytics"]


@dataclass
class ITDRPlane:
    """Describes the ITDR plane capabilities available in Phase 1."""

    name: str = PLANE_NAME
    detection_types: list[str] = field(default_factory=lambda: DETECTION_TYPES)
    cortex_engine: str = "Cortex ITDR"
    primary_sources: list[str] = field(default_factory=lambda: [
        "Impacket",
        "identity-harness (runuser/sudo-u)",
    ])
    key_techniques: list[str] = field(default_factory=lambda: [
        "T1558",   # Steal or Forge Kerberos Tickets (Kerberoast)
        "T1550",   # Use Alternate Authentication Material (Pass-the-Hash)
        "T1003",   # OS Credential Dumping (DCSync)
        "T1556",   # Modify Authentication Process (MFA bypass)
        "T1078",   # Valid Accounts
    ])
    description: str = (
        "Identity Threat Detection & Response plane — validates Kerberoast, Pass-the-Hash, "
        "DCSync, and MFA bypass detections via Cortex ITDR."
    )


ITDR_PLANE = ITDRPlane()

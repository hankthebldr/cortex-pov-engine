"""
CortexSim — Analytics (XSIAM Correlation Engine) plane stub.
Phase 2 adds full logic; this module exports the plane identity and capability descriptors.
"""

from dataclasses import dataclass, field

PLANE_NAME: str = "ANALYTICS"

DETECTION_TYPES: list[str] = ["Analytics"]


@dataclass
class AnalyticsPlane:
    """Describes the Analytics plane capabilities available in Phase 1."""

    name: str = PLANE_NAME
    detection_types: list[str] = field(default_factory=lambda: DETECTION_TYPES)
    cortex_engine: str = "XSIAM Correlation Engine"
    primary_sources: list[str] = field(default_factory=lambda: [
        "All planes — analytics scenarios span 2+ planes",
    ])
    key_techniques: list[str] = field(default_factory=lambda: [
        "T1071",   # Multi-source C2 stitch
        "T1078",   # Valid Accounts — behavioral baseline deviation
        "T1082",   # System Information Discovery — anomaly correlation
        "T1059",   # Command Execution — grouping patterns
        "T1204",   # User Execution — correlation triggers
    ])
    description: str = (
        "Analytics plane — validates multi-source stitch groups, behavioral baseline deviation, "
        "and correlation engine rule firings in XSIAM. Scenarios span 2+ detection planes."
    )


ANALYTICS_PLANE = AnalyticsPlane()

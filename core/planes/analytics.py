"""CortexSim — Analytics (XSIAM Correlation Engine) plane descriptor."""

from planes.base import PlaneDescriptor

ANALYTICS_PLANE = PlaneDescriptor(
    id="ANALYTICS",
    name="XSIAM Correlation Engine",
    cortex_engine="XSIAM Correlation Engine",
    detection_types=["Analytics", "Correlation"],
    primary_sources=[
        "All planes — multi-plane scenarios span firewall + endpoint + identity signal",
    ],
    key_techniques=[
        "T1071",  # Multi-source C2 stitch
        "T1558",  # Kerberoast -> PtH -> DCSync chain
        "T1048",  # Staged exfiltration (DNS tunnel) stage correlation
        "T1078",  # Behavioral baseline deviation
        "T1059",  # Command execution grouping
    ],
    default_identity="container-runtime",
    summary=(
        "Validates multi-source stitch groups and correlation-rule firings that fuse "
        "signal across 2+ detection planes in the XSIAM correlation engine."
    ),
)

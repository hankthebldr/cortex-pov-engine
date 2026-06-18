"""CortexSim — EDR (Endpoint Detection & Response) plane descriptor."""

from planes.base import PlaneDescriptor

EDR_PLANE = PlaneDescriptor(
    id="EDR",
    name="Endpoint Detection & Response",
    cortex_engine="Cortex XDR Agent",
    detection_types=["BIOC", "XQL", "Analytics", "IOC"],
    primary_sources=[
        "signalbench",
        "atomic-red-team",
        "identity-harness (runuser/sudo-u)",
    ],
    key_techniques=[
        "T1003",  # OS Credential Dumping (incl. LSASS)
        "T1055",  # Process Injection
        "T1053",  # Scheduled Task / Job
        "T1059",  # Command and Scripting Interpreter
        "T1490",  # Inhibit System Recovery (ESXi/ransomware)
    ],
    default_identity="www-data",
    summary=(
        "Validates endpoint BIOC process/memory/persistence detections, IOC "
        "hash/IP/domain matches, and behavioral analytics via the Cortex XDR Agent."
    ),
)

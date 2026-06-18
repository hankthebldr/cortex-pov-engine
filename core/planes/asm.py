"""CortexSim — ASM (Attack Surface Management) plane descriptor."""

from planes.base import PlaneDescriptor

ASM_PLANE = PlaneDescriptor(
    id="ASM",
    name="Attack Surface Management",
    cortex_engine="Cortex Attack Surface Management",
    detection_types=["Analytics", "IOC"],
    primary_sources=[
        "infra/modules/aws/asm (multi-service exposed host)",
        "nmap",
        "nuclei",
    ],
    key_techniques=[
        "T1595",  # Active Scanning
        "T1592",  # Gather Victim Host Information
        "T1190",  # Exploit Public-Facing Application
        "T1133",  # External Remote Services
        "T1046",  # Network Service Discovery
    ],
    default_identity="root",
    summary=(
        "Validates external attack-surface discovery — exposed services, weak TLS, "
        "non-standard SSH, unauthenticated datastores — via Cortex Attack Surface Management."
    ),
)

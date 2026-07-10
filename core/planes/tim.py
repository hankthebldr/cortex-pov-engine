"""CortexSim — TIM (Threat Intel Management) plane descriptor."""

from planes.base import PlaneDescriptor

TIM_PLANE = PlaneDescriptor(
    id="TIM",
    name="Threat Intel Management",
    cortex_engine="Cortex Threat Intel Management",
    detection_types=["IOC", "Correlation"],
    primary_sources=[
        "mocktaxii (STIX/TAXII 2.1 feed)",
        "infra/modules/aws/tim (TAXII + fake C2 + IOC subdomains)",
    ],
    key_techniques=[
        "T1071",  # Application Layer Protocol (C2 IOC match)
        "T1568",  # Dynamic Resolution (DGA IOC)
        "T1041",  # Exfiltration Over C2 Channel
        "T1496",  # Resource Hijacking (cryptominer-pool IOC)
        "T1105",  # Ingress Tool Transfer (payload-delivery IOC)
    ],
    default_identity="container-runtime",
    summary=(
        "Validates TAXII/STIX IOC-feed ingestion and IOC matches against outbound "
        "traffic, stitching the feed to NDR/EDR signal via Cortex Threat Intel Management."
    ),
)

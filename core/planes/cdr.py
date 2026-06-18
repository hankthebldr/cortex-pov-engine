"""CortexSim — CDR (Container/Cloud Detection & Response) plane descriptor."""

from planes.base import PlaneDescriptor

CDR_PLANE = PlaneDescriptor(
    id="CDR",
    name="Container Detection & Response",
    cortex_engine="Cortex Cloud / Prisma Cloud Compute",
    detection_types=["BIOC", "XQL", "Analytics"],
    primary_sources=[
        "DEEPCE",
        "XMRig",
        "infra/modules/aws/cdr (EKS lab)",
    ],
    key_techniques=[
        "T1610",  # Deploy Container
        "T1611",  # Escape to Host
        "T1613",  # Container and Resource Discovery
        "T1496",  # Resource Hijacking (cryptomining)
        "T1105",  # Ingress Tool Transfer
    ],
    default_identity="container-runtime",
    summary=(
        "Validates container runtime BIOC, container/K8s analytics, and anomaly "
        "detections via Cortex Cloud and Prisma Cloud Compute."
    ),
)

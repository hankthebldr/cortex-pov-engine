"""CortexSim — DLP (Data Loss Prevention / Data Security) plane descriptor."""

from planes.base import PlaneDescriptor

DLP_PLANE = PlaneDescriptor(
    id="DLP",
    name="Data Loss Prevention",
    cortex_engine="Cortex Enterprise DLP / XDR Agent",
    detection_types=["BIOC", "Analytics", "Correlation", "XQL"],
    primary_sources=[
        "Cortex XDR Endpoint DLP Sensor",
        "Palo Alto Networks Enterprise DLP Cloud",
        "Prisma Browser Managed DLP",
    ],
    key_techniques=[
        "T1052.001",  # Exfiltration over Physical Medium: USB Removable Media
        "T1560.001",  # Archive Collected Data: Archive via Utility
        "T1567.002",  # Exfiltration Over Web Service: Exfiltration to Cloud Storage
        "T1074.001",  # Data Staged: Local Data Staging
        "T1115",      # Clipboard Data
        "T1530",      # Data from Cloud Storage
    ],
    default_identity="svc-backup",
    summary=(
        "Validates multi-channel Data Loss Prevention and Data Security controls (removable USB media, "
        "password-protected archive staging, web/cloud upload, clipboard interception, and cross-channel correlation)."
    ),
)

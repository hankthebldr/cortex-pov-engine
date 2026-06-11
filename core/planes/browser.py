"""CortexSim — BROWSER (Prisma Browser) plane descriptor."""

from planes.base import PlaneDescriptor

BROWSER_PLANE = PlaneDescriptor(
    id="BROWSER",
    name="Prisma Browser",
    cortex_engine="Prisma Browser",
    detection_types=["Analytics", "IOC"],
    primary_sources=[
        "cortex-browser-attacker (Playwright)",
        "EAL: browser_attack_runner",
    ],
    key_techniques=[
        "T1566",  # Phishing
        "T1189",  # Drive-by Compromise
        "T1176",  # Browser Extensions
        "T1005",  # Data from Local System (copy/paste DLP)
        "T1567",  # Exfiltration Over Web Service
    ],
    default_identity="container-runtime",
    summary=(
        "Validates managed-browser DLP, malicious-extension, and phishing detections via "
        "real Chromium / Prisma Browser action sequences."
    ),
)

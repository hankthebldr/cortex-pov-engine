"""CortexSim — Cloud App Security plane descriptor."""

from planes.base import PlaneDescriptor

CLOUD_APP_PLANE = PlaneDescriptor(
    id="CLOUD_APP",
    name="Cloud App Security",
    cortex_engine="Cortex Cloud App Security",
    detection_types=["Analytics", "IOC"],
    primary_sources=[
        "EAL: oauth_grant_emulator",
        "gocortexbrokenbank",
    ],
    key_techniques=[
        "T1528",  # Steal Application Access Token (OAuth abuse)
        "T1550",  # Use Alternate Authentication Material (OAuth tokens)
        "T1195",  # Supply Chain Compromise
        "T1552",  # Unsecured Credentials
        "T1078",  # Valid Accounts (shadow IT)
    ],
    default_identity="container-runtime",
    summary=(
        "Validates risky OAuth 2.0 grant requests against Okta / Microsoft / Google "
        "and SaaS/shadow-IT detections via Cortex Cloud App Security."
    ),
)

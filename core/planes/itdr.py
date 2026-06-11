"""CortexSim — ITDR (Identity Threat Detection & Response) plane descriptor."""

from planes.base import PlaneDescriptor

ITDR_PLANE = PlaneDescriptor(
    id="ITDR",
    name="Identity Threat Detection & Response",
    cortex_engine="Cortex ITDR",
    detection_types=["Analytics", "Correlation", "IOC"],
    primary_sources=[
        "EAL: idp_signin_emulator",
        "Impacket",
        "infra/modules/aws/itdr (AD lab + roastable accounts)",
    ],
    key_techniques=[
        "T1078",  # Valid Accounts (impossible travel, credential stuffing)
        "T1110",  # Brute Force (MFA fatigue, lockout)
        "T1558",  # Steal or Forge Kerberos Tickets (Kerberoast)
        "T1550",  # Use Alternate Authentication Material (token replay, PtH)
        "T1556",  # Modify Authentication Process (MFA bypass)
    ],
    default_identity="container-runtime",
    summary=(
        "Validates impossible-travel, MFA-fatigue, credential-stuffing, token-replay, "
        "and Kerberoast/PtH/DCSync detections via Cortex ITDR."
    ),
)

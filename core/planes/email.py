"""CortexSim — EMAIL (Email Security) plane descriptor.

Email line of defense modeled as third-party log ingestion + correlation:
Proofpoint TAP (``proofpoint_tap_raw``) and Microsoft 365 / Defender for
Office 365 (``msft_o365``) feeds wired into Cortex XSIAM / NG-SIEM, plus the
correlation that stitches the email signal to the endpoint/identity follow-on.
Not a first-party PANW product surface — the data sources are the customer's
own email-security feeds.
"""

from planes.base import PlaneDescriptor

EMAIL_PLANE = PlaneDescriptor(
    id="EMAIL",
    name="Email Security",
    cortex_engine="Cortex XSIAM / NG-SIEM (Proofpoint TAP + M365 ingestion)",
    detection_types=["XQL", "Analytics", "Correlation", "IOC"],
    primary_sources=[
        "EAL: email_emitter",
        "Proofpoint TAP (proofpoint_tap_raw)",
        "Microsoft 365 / Defender for Office 365 (msft_o365)",
    ],
    key_techniques=[
        "T1566.002",  # Phishing: Spearphishing Link
        "T1566.001",  # Phishing: Spearphishing Attachment
        "T1656",      # Impersonation (BEC)
        "T1534",      # Internal Spearphishing (thread hijack lateral)
        "T1598",      # Phishing for Information
    ],
    default_identity="container-runtime",
    summary=(
        "Validates phishing / BEC / impersonation / malicious-link+attachment / "
        "thread-hijack detection via Proofpoint TAP + M365 log ingestion, stitched "
        "to the endpoint/identity follow-on."
    ),
)

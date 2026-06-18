"""CortexSim — NDR (Network Detection & Response) plane descriptor."""

from planes.base import PlaneDescriptor

NDR_PLANE = PlaneDescriptor(
    id="NDR",
    name="Network Detection & Response",
    cortex_engine="Network Security / Firewall Analytics",
    detection_types=["IOC", "Analytics", "Correlation"],
    primary_sources=[
        "ackbarx",
        "mocktaxii",
        "EAL: c2_http_beacon / dns_tunnel_exfil / stratum_tcp_connect / "
        "smb_rpc_sweep / bulk_https_exfil / ftp_egress / ssh_egress",
    ],
    key_techniques=[
        "T1071",  # Application Layer Protocol (C2)
        "T1021",  # Remote Services (lateral movement)
        "T1048",  # Exfiltration Over Alternative Protocol
        "T1046",  # Network Service Discovery
        "T1572",  # Protocol Tunneling (DNS tunnel)
    ],
    default_identity="container-runtime",
    summary=(
        "Validates C2 beacons, DNS tunneling, lateral movement, and protocol "
        "anomaly detections stitched across Network Security and Firewall Analytics."
    ),
)

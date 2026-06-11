"""CortexSim — KOI (Agentic endpoint / supply-chain risk) plane descriptor."""

from planes.base import PlaneDescriptor

KOI_PLANE = PlaneDescriptor(
    id="KOI",
    name="Agentic Endpoint / Supply-Chain Risk",
    cortex_engine="Cortex (agentic endpoint / supply-chain)",
    detection_types=["BIOC", "Analytics", "IOC"],
    primary_sources=[
        "cortex-malicious-agentic-pack (MCP / skills / extensions / PyPI)",
        "EAL: agentic_egress",
    ],
    key_techniques=[
        "T1195",  # Supply Chain Compromise (backdoored PyPI / MCP)
        "T1059",  # Command and Scripting Interpreter (post-install exec)
        "T1176",  # Extensions (VS Code / Chrome)
        "T1656",  # Impersonation (hidden tool-reply injection)
        "T1041",  # Exfiltration Over C2 Channel (artifact egress)
    ],
    default_identity="container-runtime",
    summary=(
        "Validates agentic / supply-chain risk — malicious MCP servers, skills, IDE & "
        "browser extensions, and backdoored packages — via static artifact egress shape."
    ),
)

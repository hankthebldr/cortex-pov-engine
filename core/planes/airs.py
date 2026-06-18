"""CortexSim — AIRS (Cortex AI Runtime Security) plane descriptor."""

from planes.base import PlaneDescriptor

AIRS_PLANE = PlaneDescriptor(
    id="AIRS",
    name="AI Runtime Security",
    cortex_engine="Cortex AI Runtime Security",
    detection_types=["Analytics", "XQL"],
    primary_sources=[
        "cortex-vulnerable-llm (OWASP LLM01-10 target)",
        "cortex-prompt-attacker",
        "EAL: airs_prompt_attack",
    ],
    key_techniques=[
        "T1059",  # Command and Scripting Interpreter (prompt-driven exec)
        "T1656",  # Impersonation (jailbreak / role confusion)
        "T1499",  # Endpoint Denial of Service (resource exhaustion)
        "T1082",  # System Information Discovery (system-prompt leak)
        "T1567",  # Exfiltration Over Web Service (sensitive-info disclosure)
    ],
    default_identity="container-runtime",
    summary=(
        "Validates deployed-LLM protection — prompt injection, jailbreaks, info leakage, "
        "DoS — against the OWASP LLM Top 10 via Cortex AI Runtime Security."
    ),
)

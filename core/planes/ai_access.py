"""CortexSim — AI_ACCESS (Cortex AI Access Security) plane descriptor."""

from planes.base import PlaneDescriptor

AI_ACCESS_PLANE = PlaneDescriptor(
    id="AI_ACCESS",
    name="AI Access Security",
    cortex_engine="Cortex AI Access Security",
    detection_types=["Analytics", "IOC"],
    primary_sources=[
        "EAL: llm_provider_egress (OpenAI / Gemini / Anthropic)",
    ],
    key_techniques=[
        "T1567",  # Exfiltration Over Web Service (to LLM provider)
        "T1041",  # Exfiltration Over C2 Channel
        "T1552",  # Unsecured Credentials (pasted into prompts)
        "T1090",  # Proxy
        "T1656",  # Impersonation
    ],
    default_identity="container-runtime",
    summary=(
        "Validates employee LLM-usage / DLP detections on outbound traffic to public AI "
        "providers with planted DLP markers via Cortex AI Access Security."
    ),
)

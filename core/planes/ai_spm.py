"""CortexSim — AI_SPM (Cortex AI Security Posture Management) plane descriptor."""

from planes.base import PlaneDescriptor

AI_SPM_PLANE = PlaneDescriptor(
    id="AI_SPM",
    name="AI Security Posture Management",
    cortex_engine="Cortex AI Security Posture Management",
    detection_types=["Analytics"],
    primary_sources=[
        "infra/modules/aws/ai-spm (14 resources, 8 planted posture findings)",
    ],
    key_techniques=[
        "T1580",  # Cloud Infrastructure Discovery (AI asset inventory)
        "T1078",  # Valid Accounts (weak model-endpoint config)
        "T1195",  # Supply Chain Compromise (AI supply-chain risk)
        "T1530",  # Data from Cloud Storage (sensitive-data exposure)
        "T1552",  # Unsecured Credentials
    ],
    default_identity="root",
    summary=(
        "Validates static AI asset inventory and posture findings — exposed AI assets, weak "
        "model-endpoint config, AI supply-chain risk — via Cortex AI Security Posture Management."
    ),
)

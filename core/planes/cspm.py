"""CortexSim — CSPM (Cloud Security Posture Management) plane descriptor."""

from planes.base import PlaneDescriptor

CSPM_PLANE = PlaneDescriptor(
    id="CSPM",
    name="Cloud Security Posture Management",
    cortex_engine="Cortex Cloud Posture Management",
    detection_types=["Analytics"],
    primary_sources=[
        "infra/modules/aws/cspm (9 planted misconfigurations)",
        "Prowler",
        "ScoutSuite",
    ],
    key_techniques=[
        "T1580",  # Cloud Infrastructure Discovery
        "T1078",  # Valid Accounts (over-permissive IAM)
        "T1530",  # Data from Cloud Storage (public S3)
        "T1562",  # Impair Defenses (weak CloudTrail)
        "T1552",  # Unsecured Credentials
    ],
    default_identity="root",
    summary=(
        "Validates cloud posture findings — public/unencrypted storage, over-permissive "
        "IAM, open security groups, weak audit logging — via Cortex Cloud Posture Management."
    ),
)

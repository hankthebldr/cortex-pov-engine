"""
cloud_storage_compute_emitter — analytics log-streamer for the AWS
Cloud Storage & Compute data source (S3 / EBS / EC2 / Lambda / IMDS subset of
the cloud control-plane audit trail -> ``cloud_audit_logs``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true AWS CloudTrail
records to an operator-supplied collector so a customer can validate that their
Cortex XSIAM **Analytics / ABIOC** compute/credential detections fire — without
touching a real AWS tenant, launching a real instance, or reading a real
snapshot. Every record is a JSON blob shaped *like* the CloudTrail event schema
(the same schema the ``cloud_audit_logs`` dataset normalises).

Sibling of ``cloud_audit_emitter`` (IAM / storage-policy / console-login side of
the same dataset); this plugin owns the **compute + instance-credential** side:
IMDS-sourced role tokens, EC2 / service-token remote reuse, dormant-region and
multi-region compute allocation, mining-shaped heavy allocation, and EBS
direct-API snapshot-block download.

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the transport, the ``ctx.authorise``-before-emit
gate, the dry-run short-circuit, budget charging, batching/gzip and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to a real XSIAM
cloud-compute analytics alert:

  ============================ ========================================================
  preset                       analytics / ABIOC alert it exercises
  ============================ ========================================================
  imds_token_usage             ABIOC — "Cloud IMDS access followed by remote token
                                usage" (instance-metadata credential fetched on-box,
                                then the same role session used from an external IP)
                                — T1552.005 / T1078.004
  ec2_token_usage              Analytics — "Suspicious usage of EC2 token" (an EC2
                                instance-profile role session used for an unusual API
                                call from off-instance) — T1552.005 / T1078.004
  service_token_remote_use     Analytics — "Remote usage of an AWS service token" (a
                                Lambda / service-linked role session used from a
                                remote IP) — T1550.001 / T1078.004
  dormant_region_activity      Analytics — "Compute activity in dormant cloud region"
                                (RunInstances in a region with no prior activity)
                                — T1535 / T1578.002
  multi_region_allocation      Analytics — "Abnormal Allocation of compute resources
                                in multiple regions" (RunInstances fanned across many
                                regions in a short window) — T1578.002 / T1496
  heavy_compute_allocation     ABIOC — "Suspicious heavy allocation of compute
                                resources - possible mining activity" (burst of large
                                GPU instances) — T1496 / T1578.002
  ebs_snapshot_download        Analytics — "An EBS snapshot block was downloaded"
                                (CreateSnapshot then an EBS direct-API GetSnapshotBlock
                                read) — T1578.001 / T1530
  ============================ ========================================================

Provider (parameter ``provider``): ``aws`` — these alerts are AWS-specific
(EC2 / EBS direct API / IMDS / AWS STS service tokens). All records normalise to
the ``cloud_audit_logs`` dataset in XSIAM.
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.cloud_storage_compute_emitter")


# The XSIAM dataset the AWS control-plane / EBS-direct-API records normalise to.
_DATASET = "cloud_audit_logs"

# Synthetic, non-resolving identifiers. The reserved account 123456789012, the
# .invalid TLD and the RFC 5737 documentation IP range guarantee nothing here
# touches a real tenant or resolves on a real network. 169.254.169.254 is the
# fixed link-local IMDS endpoint (never routable off-instance).
_CANARY_ACCOUNT = "123456789012"
_CANARY_USER_ARN = "arn:aws:iam::123456789012:user/cortexsim-canary"
_INSTANCE_ID = "i-0abcd1234ef567890"
_EC2_ROLE = "cortexsim-canary-ec2-role"
_LAMBDA_ROLE = "cortexsim-canary-lambda-exec-role"
_SNAPSHOT_ID = "snap-0cafef00d1234abcd"
_VOLUME_ID = "vol-0feedface9876dcba"

# On-instance IMDS endpoint vs an external attacker source (RFC 5737 TEST-NET-3).
_IMDS_IP = "169.254.169.254"
_REMOTE_IP = "203.0.113.201"

_HOME_REGION = "us-east-1"
_DORMANT_REGION = "ap-south-1"
# Region fan-out for the multi-region allocation pattern (home region first).
_MULTI_REGIONS = (
    "us-east-1",
    "eu-west-1",
    "ap-southeast-1",
    "sa-east-1",
    "ap-northeast-1",
    "eu-central-1",
    "ca-central-1",
    "ap-south-1",
)
# GPU / high-core instance types that read as cryptomining fleet allocation.
_MINING_INSTANCE_TYPE = "p4d.24xlarge"


# --------------------------------------------------------------------------
# CloudTrail userIdentity helpers
# --------------------------------------------------------------------------


def _iam_user_identity() -> dict[str, Any]:
    """A plain IAM-user principal (used for operator-initiated compute calls)."""
    return {
        "type": "IAMUser",
        "arn": _CANARY_USER_ARN,
        "accountId": _CANARY_ACCOUNT,
        "userName": "cortexsim-canary",
    }


def _assumed_role_identity(*, role_name: str, session_name: str) -> dict[str, Any]:
    """An STS AssumedRole principal — the shape an instance-profile / service
    role token carries in CloudTrail (``sts:assume-role`` session)."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "type": "AssumedRole",
        "arn": (
            f"arn:aws:sts::{_CANARY_ACCOUNT}:assumed-role/{role_name}/{session_name}"
        ),
        "accountId": _CANARY_ACCOUNT,
        "sessionContext": {
            "sessionIssuer": {
                "type": "Role",
                "arn": f"arn:aws:iam::{_CANARY_ACCOUNT}:role/{role_name}",
                "userName": role_name,
                "accountId": _CANARY_ACCOUNT,
            },
            "attributes": {
                "creationDate": now,
                "mfaAuthenticated": "false",
            },
        },
    }


# --------------------------------------------------------------------------
# Provider event-shape adapter (AWS CloudTrail)
# --------------------------------------------------------------------------


def _aws_cloudtrail_event(
    *,
    event_name: str,
    event_source: str,
    marker: str,
    sim_run_id: str,
    region: str = _HOME_REGION,
    source_ip: str = _REMOTE_IP,
    identity: Optional[dict[str, Any]] = None,
    outcome: str = "success",
    read_only: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape an AWS CloudTrail record (subset of the CloudTrail event schema)."""
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "eventVersion": "1.08",
        "eventTime": now,
        "eventSource": event_source,
        "eventName": event_name,
        "eventType": "AwsApiCall",
        "awsRegion": region,
        "sourceIPAddress": source_ip,
        "userAgent": "aws-cli/2.15.0 Python/3.11",
        "userIdentity": identity or _iam_user_identity(),
        "errorCode": None if outcome == "success" else "AccessDenied",
        "readOnly": read_only,
        "managementEvent": True,
        "recipientAccountId": _CANARY_ACCOUNT,
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


_PROVIDER_BUILDERS = {
    "aws": _aws_cloudtrail_event,
}


def _list_providers() -> list[str]:
    return sorted(_PROVIDER_BUILDERS)


# --------------------------------------------------------------------------
# Event patterns — the analytics alert each preset exercises + its body marker
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _AlertSpec:
    """Static metadata for one preset: the analytics alert it fires + the
    body marker XSIAM can key on."""
    marker: str
    alert: str


_PATTERN_ALERT: dict[str, _AlertSpec] = {
    "imds_token_usage": _AlertSpec(
        marker="imds_token_usage_marker",
        alert="Cloud IMDS access followed by remote token usage",
    ),
    "ec2_token_usage": _AlertSpec(
        marker="ec2_token_usage_marker",
        alert="Suspicious usage of EC2 token",
    ),
    "service_token_remote_use": _AlertSpec(
        marker="service_token_remote_use_marker",
        alert="Remote usage of an AWS service token",
    ),
    "dormant_region_activity": _AlertSpec(
        marker="dormant_region_marker",
        alert="Compute activity in dormant cloud region",
    ),
    "multi_region_allocation": _AlertSpec(
        marker="multi_region_allocation_marker",
        alert="Abnormal Allocation of compute resources in multiple regions",
    ),
    "heavy_compute_allocation": _AlertSpec(
        marker="heavy_compute_allocation_marker",
        alert="Suspicious heavy allocation of compute resources - possible mining activity",
    ),
    "ebs_snapshot_download": _AlertSpec(
        marker="ebs_snapshot_download_marker",
        alert="An EBS snapshot block was downloaded",
    ),
}

_EVENT_PATTERNS = tuple(_PATTERN_ALERT)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only provider + event_pattern.
# --------------------------------------------------------------------------


class CloudStorageComputeEmitterParams(AnalyticsEmitterParams):
    provider: str = Field(
        default="aws",
        description="Cloud control-plane audit shape. Only 'aws' (CloudTrail) is "
                    "supported — these compute/credential alerts are AWS-specific "
                    "(EC2 / EBS direct API / IMDS / AWS service tokens).",
    )
    event_pattern: str = Field(
        default="imds_token_usage",
        description="Analytics alert to exercise: imds_token_usage | "
                    "ec2_token_usage | service_token_remote_use | "
                    "dormant_region_activity | multi_region_allocation | "
                    "heavy_compute_allocation | ebs_snapshot_download.",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset these AWS records normalise to."""
        return _DATASET

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _PROVIDER_BUILDERS:
            raise ValueError(
                f"provider must be one of {sorted(_PROVIDER_BUILDERS)}, got '{v}'"
            )
        return v

    @field_validator("event_pattern")
    @classmethod
    def _pattern_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _EVENT_PATTERNS:
            raise ValueError(
                f"event_pattern must be one of {sorted(_EVENT_PATTERNS)}, got '{v}'"
            )
        return v


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class CloudStorageComputeEmitter(AnalyticsLogEmitter):
    class Meta:
        name = "cloud_storage_compute_emitter"
        version = "1.0.0"
        description = (
            "Emits shape-true AWS CloudTrail records for the compute + "
            "instance-credential side of the cloud_audit_logs dataset (EC2 / "
            "EBS / Lambda / IMDS / S3) into an operator-supplied collector so "
            "Cortex XSIAM exercises its cloud Analytics / ABIOC detections — "
            "IMDS-to-remote token reuse, EC2 / service-token remote usage, "
            "dormant-region and multi-region compute allocation, mining-shaped "
            "heavy allocation, and EBS snapshot-block download — without "
            "touching a real AWS tenant."
        )
        # Drawn from the analytics alerts this plugin fires. None belong to the
        # ATT&CK Command-and-Control tactic, so live campaigns need only
        # simulation_authorized (not c2_authorized) per the SafetyPolicy.
        mitre_techniques = [
            "T1552.005",  # Unsecured Credentials: Cloud Instance Metadata API
            "T1078.004",  # Valid Accounts: Cloud Accounts
            "T1550.001",  # Use Alternate Auth Material: Application Access Token
            "T1535",      # Unused/Unsupported Cloud Regions
            "T1578.002",  # Modify Cloud Compute Infra: Create Cloud Instance
            "T1496",      # Resource Hijacking
            "T1578.001",  # Modify Cloud Compute Infra: Create Snapshot
            "T1530",      # Data from Cloud Storage
        ]
        eal_targets = [
            "ABIOC — Cloud IMDS access followed by remote token usage",
            "Analytics — Suspicious usage of EC2 token",
            "Analytics — Remote usage of an AWS service token",
            "Analytics — Compute activity in dormant cloud region",
            "Analytics — Abnormal Allocation of compute resources in multiple regions",
            "ABIOC — Suspicious heavy allocation of compute resources (mining)",
            "Analytics — An EBS snapshot block was downloaded",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "configuration"
        params_model = CloudStorageComputeEmitterParams

    def build_events(
        self,
        params: CloudStorageComputeEmitterParams,
        *,
        sim_run_id: str,
        iteration: int,
    ) -> list[dict[str, Any]]:
        builder = _PROVIDER_BUILDERS[params.provider]
        marker = _PATTERN_ALERT[params.event_pattern].marker
        pattern = params.event_pattern

        if pattern == "imds_token_usage":
            # (1) instance-metadata credential fetched on-box from the IMDS
            # link-local endpoint, then (2) that same role session used from an
            # external IP — the "IMDS access followed by remote token usage"
            # ABIOC causality chain.
            instance_role = _assumed_role_identity(
                role_name=_EC2_ROLE, session_name=_INSTANCE_ID,
            )
            return [
                builder(
                    event_name="GetInstanceMetadataCredentials",
                    event_source="imds.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    source_ip=_IMDS_IP,
                    identity=instance_role,
                    read_only=True,
                    extra={
                        "imds_endpoint": _IMDS_IP,
                        "imds_path": "/latest/meta-data/iam/security-credentials/",
                        "instance_id": _INSTANCE_ID,
                        "imds_access_marker": True,
                    },
                ),
                builder(
                    event_name="GetCallerIdentity",
                    event_source="sts.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    source_ip=_REMOTE_IP,
                    identity=instance_role,
                    read_only=True,
                    extra={
                        "instance_id": _INSTANCE_ID,
                        "token_used_off_instance": True,
                        "remote_token_use_marker": True,
                    },
                ),
            ]

        if pattern == "ec2_token_usage":
            # An EC2 instance-profile role session used for an unusual off-box
            # API call (IAM policy enumeration) from a remote IP.
            return [
                builder(
                    event_name="ListAttachedRolePolicies",
                    event_source="iam.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    source_ip=_REMOTE_IP,
                    identity=_assumed_role_identity(
                        role_name=_EC2_ROLE, session_name=_INSTANCE_ID,
                    ),
                    read_only=True,
                    extra={
                        "instance_id": _INSTANCE_ID,
                        "credential_source": "ec2-instance-profile",
                        "token_used_off_instance": True,
                    },
                ),
            ]

        if pattern == "service_token_remote_use":
            # A Lambda / service-linked role session used from a remote IP —
            # "Remote usage of an AWS service token".
            return [
                builder(
                    event_name="GetSecretValue",
                    event_source="secretsmanager.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    source_ip=_REMOTE_IP,
                    identity=_assumed_role_identity(
                        role_name=_LAMBDA_ROLE,
                        session_name="cortexsim-canary-fn",
                    ),
                    read_only=True,
                    extra={
                        "role_type": "service-role",
                        "service": "lambda",
                        "token_used_off_instance": True,
                    },
                ),
            ]

        if pattern == "dormant_region_activity":
            # RunInstances in a region with no prior activity.
            return [
                builder(
                    event_name="RunInstances",
                    event_source="ec2.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    region=_DORMANT_REGION,
                    source_ip=_REMOTE_IP,
                    extra={
                        "instance_type": "t3.micro",
                        "instances_requested": 1,
                        "region_dormant_days": 400,
                    },
                ),
            ]

        if pattern == "multi_region_allocation":
            # RunInstances fanned across many regions in a short window.
            events: list[dict[str, Any]] = []
            for idx in range(params.burst_count):
                region = _MULTI_REGIONS[idx % len(_MULTI_REGIONS)]
                events.append(builder(
                    event_name="RunInstances",
                    event_source="ec2.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    region=region,
                    source_ip=_REMOTE_IP,
                    extra={
                        "instance_type": "t3.medium",
                        "instances_requested": 1,
                        "allocation_region": region,
                    },
                ))
            return events

        if pattern == "heavy_compute_allocation":
            # A burst of large GPU instances — mining-shaped heavy allocation.
            events = []
            for _ in range(params.burst_count):
                events.append(builder(
                    event_name="RunInstances",
                    event_source="ec2.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    region=_HOME_REGION,
                    source_ip=_REMOTE_IP,
                    extra={
                        "instance_type": _MINING_INSTANCE_TYPE,
                        "instances_requested": 10,
                        "possible_mining": True,
                    },
                ))
            return events

        if pattern == "ebs_snapshot_download":
            # CreateSnapshot precursor, then an EBS direct-API GetSnapshotBlock
            # read — "An EBS snapshot block was downloaded".
            return [
                builder(
                    event_name="CreateSnapshot",
                    event_source="ec2.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    source_ip=_REMOTE_IP,
                    extra={
                        "volume_id": _VOLUME_ID,
                        "snapshot_id": _SNAPSHOT_ID,
                    },
                ),
                builder(
                    event_name="GetSnapshotBlock",
                    event_source="ebs.amazonaws.com",
                    marker=marker,
                    sim_run_id=sim_run_id,
                    source_ip=_REMOTE_IP,
                    read_only=True,
                    extra={
                        "snapshot_id": _SNAPSHOT_ID,
                        "block_index": 0,
                        "block_token": secrets.token_hex(8),
                        "block_downloaded": True,
                    },
                ),
            ]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {pattern!r}"
        )

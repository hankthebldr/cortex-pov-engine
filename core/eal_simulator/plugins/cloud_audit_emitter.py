"""
cloud_audit_emitter — reference analytics log-streamer for the cloud-audit
data source (AWS CloudTrail / GCP Cloud Audit Logs -> ``cloud_audit_logs``).

This is the first per-data-source plugin built on the ``analytics_emitter``
spine. It POSTs shape-true CloudTrail / GCP-audit records to an
operator-supplied collector so a customer can validate that their Cortex
XSIAM **Analytics / ABIOC** cloud-audit detections fire — without touching a
real AWS/GCP tenant or performing any real cloud mutation. Every record is a
JSON blob shaped *like* the provider's control-plane audit schema.

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the transport, the ``ctx.authorise``-before-emit
gate, the dry-run short-circuit, budget charging, batching/gzip and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to real
XSIAM cloud-audit **Analytics / ABIOC** alerts (the two below-target detection
types). Alerts whose behaviour is provider-symmetric fire on both AWS and GCP;
provider-specific alerts fire on the noted provider only:

  ============================== =================================================================
  preset                         analytics / ABIOC alert(s) it exercises
  ============================== =================================================================
  storage_object_download        Analytics/ABIOC — "Suspicious identity downloaded multiple
                                  objects from a bucket" (aws: CloudTrail GetObject burst) /
                                  "An identity performed a suspicious download of multiple cloud
                                  storage objects" (gcp: storage.objects.get burst) — T1530
  secrets_dump                   Analytics — "Suspicious secrets dump activity"
                                  (aws: Secrets Manager GetSecretValue burst / gcp: Secret
                                  Manager AccessSecretVersion burst) — T1555.006
  ssm_parameter_retrieval        Analytics — "Suspicious AWS SSM parameters retrieval activity"
                                  (aws: ssm GetParameters WithDecryption burst) — T1552.001
  iam_enumeration                Analytics — "IAM Enumeration sequence" (ListUsers/ListRoles/
                                  ListPolicies/GetAccountAuthorizationDetails sequence) — T1087.004
  iam_enumeration_non_user       ABIOC — "Unusual IAM enumeration activity by a non-user Identity"
                                  (same enum sequence under an AssumedRole / service-account
                                  identity) — T1087.004
  s3_bucket_enumeration          Analytics — "AWS S3 Buckets enumeration activity"
                                  (aws: ListBuckets/GetBucketAcl/GetBucketPolicy/ListObjects) — T1619
  bigquery_exfiltration          ABIOC — "BigQuery table or query results exfiltrated to a foreign
                                  project" (gcp: BigQuery InsertJob query + extract to a foreign
                                  destination project) — T1537
  service_account_impersonation  Analytics — "GCP service account impersonation attempt"
                                  (gcp: iamcredentials GenerateAccessToken / aws-analog AssumeRole)
                                  — T1078.004
  iam_privilege_escalation       Analytics — anomalous IAM privilege grant (CreateUser +
                                  AttachUserPolicy AdministratorAccess / GCP SetIamPolicy
                                  roles/owner) — T1098
  s3_public_exposure             Analytics — cloud storage made publicly accessible
                                  (PutBucketPolicy allow-* / GCS IAM allUsers) — T1530
  console_login_anomaly          ABIOC — anomalous console sign-in burst (failed logins then a
                                  success from an unusual geo, no MFA) — T1078.004
  ============================== =================================================================

Providers (parameter ``provider``): ``aws`` (CloudTrail) | ``gcp`` (Cloud
Audit Logs). Both normalise to the ``cloud_audit_logs`` dataset in XSIAM. A
scenario DRIVES this plugin (its step invokes the EAL plugin and its
``expected_detections`` are the analytics alerts above); pick the provider the
alert exists on.
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.cloud_audit_emitter")


# The XSIAM dataset both AWS CloudTrail and GCP Cloud Audit Logs normalise to.
_DATASET = "cloud_audit_logs"

# Synthetic, non-resolving identifiers. The reserved .invalid TLD and the
# documentation IP ranges (RFC 5737) guarantee nothing here touches a real
# tenant or resolves on a real network.
_CANARY_ACCOUNT = "123456789012"
_CANARY_PROJECT = "cortexsim-canary"
_CANARY_PRINCIPAL_AWS = "arn:aws:iam::123456789012:user/cortexsim-canary"
_CANARY_ROLE_ARN = "arn:aws:iam::123456789012:role/cortexsim-canary-role"
_CANARY_ASSUMED_ROLE_ARN = (
    "arn:aws:sts::123456789012:assumed-role/cortexsim-canary-role/cortexsim-session"
)
# A human user (default) vs a non-user service-account principal (GCP).
_CANARY_USER_GCP = "cortexsim-canary@cortexsim-canary.invalid"
_CANARY_PRINCIPAL_GCP = "cortexsim-canary@cortexsim-canary.iam.gserviceaccount.invalid"
_ANOMALOUS_IP = "203.0.113.201"


# --------------------------------------------------------------------------
# Per-pattern control-plane activity (shared by both provider builders)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _CloudActivity:
    """One control-plane API call, expressed for both providers.

    ``read_only`` / ``management_event`` mirror the CloudTrail fields so
    read-only enumeration (ListUsers, GetObject, ...) and data-plane events
    (GetObject / ListObjects) are shaped truthfully rather than always looking
    like a management mutation.
    """
    aws_event_name: str
    aws_event_source: str
    gcp_method_name: str
    gcp_service_name: str
    outcome: str = "success"  # "success" | "failure"
    read_only: bool = False
    management_event: bool = True


# --------------------------------------------------------------------------
# Provider event-shape adapters
# --------------------------------------------------------------------------


def _aws_cloudtrail_event(
    *,
    activity: _CloudActivity,
    marker: str,
    sim_run_id: str,
    source_ip: str = _ANOMALOUS_IP,
    non_user: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape an AWS CloudTrail record (subset of the CloudTrail event schema).

    ``non_user`` swaps the ``userIdentity`` from a human ``IAMUser`` to an
    ``AssumedRole`` session so the "non-user Identity" ABIOC alert has the
    control-plane shape it keys on.
    """
    now = datetime.now(timezone.utc).isoformat()
    if non_user:
        user_identity: dict[str, Any] = {
            "type": "AssumedRole",
            "arn": _CANARY_ASSUMED_ROLE_ARN,
            "accountId": _CANARY_ACCOUNT,
            "principalId": "AROAEXAMPLECANARY:cortexsim-session",
            "sessionContext": {
                "sessionIssuer": {
                    "type": "Role",
                    "arn": _CANARY_ROLE_ARN,
                    "userName": "cortexsim-canary-role",
                    "accountId": _CANARY_ACCOUNT,
                },
            },
        }
    else:
        user_identity = {
            "type": "IAMUser",
            "arn": _CANARY_PRINCIPAL_AWS,
            "accountId": _CANARY_ACCOUNT,
            "userName": "cortexsim-canary",
        }
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "eventVersion": "1.08",
        "eventTime": now,
        "eventSource": activity.aws_event_source,
        "eventName": activity.aws_event_name,
        "eventType": "AwsApiCall",
        "awsRegion": "us-east-1",
        "sourceIPAddress": source_ip,
        "userAgent": "aws-cli/2.15.0 Python/3.11",
        "userIdentity": user_identity,
        "errorCode": None if activity.outcome == "success" else "AccessDenied",
        "readOnly": activity.read_only,
        "managementEvent": activity.management_event,
        "recipientAccountId": _CANARY_ACCOUNT,
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


def _gcp_audit_event(
    *,
    activity: _CloudActivity,
    marker: str,
    sim_run_id: str,
    source_ip: str = _ANOMALOUS_IP,
    non_user: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a GCP Cloud Audit Log record (subset of the AuditLog schema).

    ``non_user`` presents a service-account ``principalEmail`` (the GCP
    non-user identity) instead of a human user email.
    """
    now = datetime.now(timezone.utc).isoformat()
    principal = _CANARY_PRINCIPAL_GCP if non_user else _CANARY_USER_GCP
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "insertId": secrets.token_hex(8),
        "timestamp": now,
        "severity": "NOTICE" if activity.outcome == "success" else "ERROR",
        "resource": {
            "type": "project",
            "labels": {"project_id": _CANARY_PROJECT},
        },
        "protoPayload": {
            "@type": "type.googleapis.com/google.cloud.audit.AuditLog",
            "serviceName": activity.gcp_service_name,
            "methodName": activity.gcp_method_name,
            "resourceName": f"projects/{_CANARY_PROJECT}",
            "authenticationInfo": {"principalEmail": principal},
            "requestMetadata": {
                "callerIp": source_ip,
                "callerSuppliedUserAgent": "google-cloud-sdk gcloud/470.0.0",
            },
            "status": {} if activity.outcome == "success"
                      else {"code": 7, "message": "PERMISSION_DENIED"},
        },
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


_PROVIDER_BUILDERS = {
    "aws": _aws_cloudtrail_event,
    "gcp": _gcp_audit_event,
}


def _list_providers() -> list[str]:
    return sorted(_PROVIDER_BUILDERS)


# --------------------------------------------------------------------------
# Event patterns — the control-plane activity each preset emits + its marker
# --------------------------------------------------------------------------


# Reusable IAM read-only enumeration sequence (ListUsers -> ListRoles ->
# ListPolicies -> GetAccountAuthorizationDetails). Shared by the user and
# non-user enumeration patterns so both fire on the same sequence shape.
_IAM_ENUM_SEQUENCE: list[_CloudActivity] = [
    _CloudActivity(
        aws_event_name="ListUsers",
        aws_event_source="iam.amazonaws.com",
        gcp_method_name="google.iam.admin.v1.ListServiceAccounts",
        gcp_service_name="iam.googleapis.com",
        outcome="success", read_only=True, management_event=True,
    ),
    _CloudActivity(
        aws_event_name="ListRoles",
        aws_event_source="iam.amazonaws.com",
        gcp_method_name="google.iam.admin.v1.ListRoles",
        gcp_service_name="iam.googleapis.com",
        outcome="success", read_only=True, management_event=True,
    ),
    _CloudActivity(
        aws_event_name="ListPolicies",
        aws_event_source="iam.amazonaws.com",
        gcp_method_name="google.iam.admin.v1.QueryGrantableRoles",
        gcp_service_name="iam.googleapis.com",
        outcome="success", read_only=True, management_event=True,
    ),
    _CloudActivity(
        aws_event_name="GetAccountAuthorizationDetails",
        aws_event_source="iam.amazonaws.com",
        gcp_method_name="GetIamPolicy",
        gcp_service_name="cloudresourcemanager.googleapis.com",
        outcome="success", read_only=True, management_event=True,
    ),
]


# Each pattern -> (marker, [activities...]). Single-element activity lists that
# appear in ``_BURST_PATTERNS`` fan out to ``burst_count`` records; multi-element
# lists emit one record per activity (a sequence). The console-login pattern is
# special-cased in build_events (burst of failures + a trailing success).
_PATTERN_ACTIVITY: dict[str, tuple[str, list[_CloudActivity]]] = {
    # -- new analytics/ABIOC alert patterns (this data source's assignment) --
    "storage_object_download": (
        "storage_object_download_marker",
        [
            _CloudActivity(
                aws_event_name="GetObject",
                aws_event_source="s3.amazonaws.com",
                gcp_method_name="storage.objects.get",
                gcp_service_name="storage.googleapis.com",
                outcome="success", read_only=True, management_event=False,
            ),
        ],
    ),
    "secrets_dump": (
        "secrets_dump_marker",
        [
            _CloudActivity(
                aws_event_name="GetSecretValue",
                aws_event_source="secretsmanager.amazonaws.com",
                gcp_method_name=(
                    "google.cloud.secretmanager.v1.SecretManagerService"
                    ".AccessSecretVersion"
                ),
                gcp_service_name="secretmanager.googleapis.com",
                outcome="success", read_only=True, management_event=True,
            ),
        ],
    ),
    "ssm_parameter_retrieval": (
        "ssm_parameter_retrieval_marker",
        [
            _CloudActivity(
                aws_event_name="GetParameters",
                aws_event_source="ssm.amazonaws.com",
                # No native GCP analog; GCP secret retrieval is the closest
                # shape (scenarios pin provider=aws for this alert).
                gcp_method_name=(
                    "google.cloud.secretmanager.v1.SecretManagerService"
                    ".AccessSecretVersion"
                ),
                gcp_service_name="secretmanager.googleapis.com",
                outcome="success", read_only=True, management_event=True,
            ),
        ],
    ),
    "iam_enumeration": ("iam_enumeration_marker", _IAM_ENUM_SEQUENCE),
    "iam_enumeration_non_user": ("iam_enumeration_non_user_marker", _IAM_ENUM_SEQUENCE),
    "s3_bucket_enumeration": (
        "s3_bucket_enumeration_marker",
        [
            _CloudActivity(
                aws_event_name="ListBuckets",
                aws_event_source="s3.amazonaws.com",
                gcp_method_name="storage.buckets.list",
                gcp_service_name="storage.googleapis.com",
                outcome="success", read_only=True, management_event=True,
            ),
            _CloudActivity(
                aws_event_name="GetBucketAcl",
                aws_event_source="s3.amazonaws.com",
                gcp_method_name="storage.buckets.getIamPolicy",
                gcp_service_name="storage.googleapis.com",
                outcome="success", read_only=True, management_event=True,
            ),
            _CloudActivity(
                aws_event_name="GetBucketPolicy",
                aws_event_source="s3.amazonaws.com",
                gcp_method_name="storage.buckets.getIamPolicy",
                gcp_service_name="storage.googleapis.com",
                outcome="success", read_only=True, management_event=True,
            ),
            _CloudActivity(
                aws_event_name="ListObjects",
                aws_event_source="s3.amazonaws.com",
                gcp_method_name="storage.objects.list",
                gcp_service_name="storage.googleapis.com",
                outcome="success", read_only=True, management_event=False,
            ),
        ],
    ),
    "bigquery_exfiltration": (
        "bigquery_exfiltration_marker",
        [
            _CloudActivity(
                aws_event_name="StartQueryExecution",
                aws_event_source="athena.amazonaws.com",
                gcp_method_name="google.cloud.bigquery.v2.JobService.InsertJob",
                gcp_service_name="bigquery.googleapis.com",
                outcome="success", read_only=False, management_event=False,
            ),
            _CloudActivity(
                aws_event_name="CreateExportTask",
                aws_event_source="athena.amazonaws.com",
                gcp_method_name="google.cloud.bigquery.v2.JobService.InsertJob",
                gcp_service_name="bigquery.googleapis.com",
                outcome="success", read_only=False, management_event=False,
            ),
        ],
    ),
    "service_account_impersonation": (
        "service_account_impersonation_marker",
        [
            _CloudActivity(
                aws_event_name="AssumeRole",
                aws_event_source="sts.amazonaws.com",
                gcp_method_name=(
                    "google.iam.credentials.v1.IAMCredentials.GenerateAccessToken"
                ),
                gcp_service_name="iamcredentials.googleapis.com",
                outcome="success", read_only=False, management_event=True,
            ),
        ],
    ),

    # -- original reference patterns (kept for back-compat) --
    "iam_privilege_escalation": (
        "privilege_escalation_marker",
        [
            _CloudActivity(
                aws_event_name="CreateUser",
                aws_event_source="iam.amazonaws.com",
                gcp_method_name="google.iam.admin.v1.CreateServiceAccount",
                gcp_service_name="iam.googleapis.com",
                outcome="success",
            ),
            _CloudActivity(
                aws_event_name="AttachUserPolicy",
                aws_event_source="iam.amazonaws.com",
                gcp_method_name="SetIamPolicy",
                gcp_service_name="cloudresourcemanager.googleapis.com",
                outcome="success",
            ),
        ],
    ),
    "s3_public_exposure": (
        "public_exposure_marker",
        [
            _CloudActivity(
                aws_event_name="PutBucketPolicy",
                aws_event_source="s3.amazonaws.com",
                gcp_method_name="storage.setIamPermissions",
                gcp_service_name="storage.googleapis.com",
                outcome="success",
            ),
        ],
    ),
    "console_login_anomaly": (
        "console_login_anomaly_marker",
        [
            _CloudActivity(
                aws_event_name="ConsoleLogin",
                aws_event_source="signin.amazonaws.com",
                gcp_method_name="google.login.LoginService.loginSuccess",
                gcp_service_name="login.googleapis.com",
                outcome="success",
            ),
        ],
    ),
}

_EVENT_PATTERNS = tuple(_PATTERN_ACTIVITY)


# Patterns whose single activity fans out to ``burst_count`` records (a mass /
# repeated-call behaviour the analytics-ML keys on). console_login_anomaly also
# bursts but is special-cased (mixed failure/success) in build_events.
_BURST_PATTERNS: frozenset[str] = frozenset({
    "storage_object_download",
    "secrets_dump",
    "ssm_parameter_retrieval",
})

# Patterns emitted under a non-user identity (AssumedRole / service account).
_NON_USER_PATTERNS: frozenset[str] = frozenset({
    "iam_enumeration_non_user",
})

# Static, non-secret shape hints merged into every record of a pattern so the
# analytics alert has the field it keys on (e.g. the foreign destination
# project for the BigQuery exfil ABIOC). Never carries a credential.
_PATTERN_EXTRA: dict[str, dict[str, Any]] = {
    "storage_object_download": {"bucket_name": "cortexsim-canary-sensitive-data"},
    "s3_bucket_enumeration": {"enumeration_scope": "account"},
    "secrets_dump": {"secret_scope": "all-secrets"},
    "ssm_parameter_retrieval": {"with_decryption": True, "parameter_path": "/prod/"},
    "bigquery_exfiltration": {
        "destination_project_id": "attacker-project-999",
        "foreign_project": True,
    },
    "service_account_impersonation": {
        "target_service_account": (
            "privileged-sa@cortexsim-canary.iam.gserviceaccount.invalid"
        ),
    },
    "iam_enumeration_non_user": {"non_user_identity": True},
}


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# The failed-login precursor used by the console_login_anomaly burst.
_CONSOLE_LOGIN_FAIL = _CloudActivity(
    aws_event_name="ConsoleLogin",
    aws_event_source="signin.amazonaws.com",
    gcp_method_name="google.login.LoginService.loginFailure",
    gcp_service_name="login.googleapis.com",
    outcome="failure",
)


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only provider + event_pattern.
# --------------------------------------------------------------------------


class CloudAuditEmitterParams(AnalyticsEmitterParams):
    provider: str = Field(
        default="aws",
        description="Cloud control-plane audit shape: aws (CloudTrail) | gcp "
                    "(Cloud Audit Logs).",
    )
    event_pattern: str = Field(
        default="iam_privilege_escalation",
        description="Analytics/ABIOC alert to exercise: storage_object_download | "
                    "secrets_dump | ssm_parameter_retrieval | iam_enumeration | "
                    "iam_enumeration_non_user | s3_bucket_enumeration | "
                    "bigquery_exfiltration | service_account_impersonation | "
                    "iam_privilege_escalation | s3_public_exposure | "
                    "console_login_anomaly.",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset both providers normalise to."""
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


class CloudAuditEmitter(AnalyticsLogEmitter):
    class Meta:
        name = "cloud_audit_emitter"
        version = "1.1.0"
        description = (
            "Emits shape-true cloud control-plane audit records (AWS CloudTrail "
            "/ GCP Cloud Audit Logs shape, cloud_audit_logs dataset) into an "
            "operator-supplied collector so Cortex XSIAM exercises its cloud "
            "Analytics / ABIOC detections — mass object download, secrets dump, "
            "SSM parameter retrieval, IAM / S3 enumeration sequences (user and "
            "non-user), BigQuery foreign-project exfiltration, service-account "
            "impersonation, IAM privilege escalation, storage public-exposure and "
            "anomalous console login — without touching a real AWS/GCP tenant."
        )
        # Drawn from the analytics/ABIOC alerts this plugin fires. None are
        # C2-tactic (TA0011) techniques, so live campaigns need only
        # simulation_authorized (not c2_authorized) per the SafetyPolicy
        # classifier.
        mitre_techniques = [
            "T1098",       # IAM privilege escalation (Account Manipulation)
            "T1530",       # Data from Cloud Storage Object (mass object download)
            "T1078.004",   # Valid Cloud Accounts (console login / SA impersonation)
            "T1552.001",   # Unsecured Credentials: Credentials In Files (SSM params)
            "T1555.006",   # Credentials from Cloud Secrets Management Stores
            "T1087.004",   # Account Discovery: Cloud Account (IAM enumeration)
            "T1069.003",   # Permission Groups Discovery: Cloud Groups
            "T1580",       # Cloud Infrastructure Discovery (S3 bucket enum)
            "T1619",       # Cloud Storage Object Discovery (S3 bucket enum)
            "T1537",       # Transfer Data to Cloud Account (BigQuery foreign exfil)
        ]
        eal_targets = [
            "Analytics — suspicious identity downloaded multiple objects from a "
            "bucket (CloudTrail GetObject burst)",
            "Analytics — suspicious download of multiple cloud storage objects "
            "(GCP storage.objects.get burst)",
            "Analytics — suspicious secrets dump activity (Secrets Manager / "
            "Secret Manager burst)",
            "Analytics — suspicious AWS SSM parameters retrieval activity",
            "Analytics — IAM Enumeration sequence",
            "ABIOC — unusual IAM enumeration activity by a non-user Identity",
            "Analytics — AWS S3 Buckets enumeration activity",
            "ABIOC — BigQuery table or query results exfiltrated to a foreign "
            "project",
            "Analytics — GCP service account impersonation attempt",
            "Analytics — cloud IAM privilege escalation (CloudTrail / GCP audit)",
            "Analytics — cloud storage public exposure (S3 bucket policy / GCS IAM)",
            "ABIOC — anomalous console login burst (unusual geo / no MFA)",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "configuration"
        params_model = CloudAuditEmitterParams

    def build_events(
        self, params: CloudAuditEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        builder = _PROVIDER_BUILDERS[params.provider]
        marker, activities = _PATTERN_ACTIVITY[params.event_pattern]

        events: list[dict[str, Any]] = []

        if params.event_pattern == "console_login_anomaly":
            # A burst of failed console logins from the anomalous IP, then the
            # single successful sign-in that completes the ABIOC anomaly.
            for _ in range(params.burst_count):
                events.append(builder(
                    activity=_CONSOLE_LOGIN_FAIL,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    extra={"mfa_used": False, "login_result": "Failure"},
                ))
            events.append(builder(
                activity=activities[0],
                marker=marker,
                sim_run_id=sim_run_id,
                extra={"mfa_used": False, "login_result": "Success"},
            ))
            return events

        non_user = params.event_pattern in _NON_USER_PATTERNS
        static_extra = _PATTERN_EXTRA.get(params.event_pattern)
        # Burst patterns fan a single activity out to burst_count records
        # (mass / repeated-call behaviour); sequence patterns emit each
        # distinct activity once.
        repeat = params.burst_count if params.event_pattern in _BURST_PATTERNS else 1

        for _ in range(repeat):
            for activity in activities:
                events.append(builder(
                    activity=activity,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    non_user=non_user,
                    extra=dict(static_extra) if static_extra else None,
                ))
        return events

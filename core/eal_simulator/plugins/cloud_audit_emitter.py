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

Logging-service presets (added in 1.2.0) — cloud log tampering and log
re-routing against AWS CloudTrail and Google Cloud Logging. Each preset emits
the VICTIM-side audit events of one technique, for either provider:

  ====================== ============================== ==============================
  preset                 aws (CloudTrail)               gcp (Cloud Audit Logs)
  ====================== ============================== ==============================
  log_stop               StopLogging                    ConfigServiceV2.UpdateSink
                                                        (sink.disabled = true)
  log_destination_delete DeleteObject x burst_count,    ConfigServiceV2.DeleteBucket
                         then DeleteBucket (the         (the Cloud Logging log
                         trail's S3 bucket)             bucket; one call, no burst)
  log_router_delete      DeleteTrail                    ConfigServiceV2.DeleteSink
  log_key_swap           UpdateTrail, kmsKeyId set to   ConfigServiceV2.UpdateBucket,
                         a key in another account       cmekSettings.kmsKeyName set
                                                        to a key in another project
                                                        (regional log bucket, key
                                                        in the same region)
  log_object_overwrite   GetObject then PutObject on    storage.objects.get then
                         the same stored log object     storage.objects.create on the
                                                        same object
  log_router_create      CreateTrail delivering to an   ConfigServiceV2.CreateSink
                         attacker-named bucket          with an attacker destination
  log_router_redirect    UpdateTrail, s3BucketName set  ConfigServiceV2.UpdateSink,
                         to an attacker-named bucket    sink.destination changed
  stream_bucket_delete   DeleteObject x burst_count,    storage.objects.delete x
                         then DeleteBucket (a data      burst_count, then
                         stream's destination bucket)   storage.buckets.delete
  ====================== ============================== ==============================

  T1562.008 log_stop, log_router_delete, log_key_swap, log_router_redirect ·
  T1485 log_destination_delete, stream_bucket_delete · T1565.001
  log_object_overwrite · T1537 log_router_create.

What these records carry beyond the original presets: the call's request
(``requestParameters`` + ``eventCategory`` on aws; ``protoPayload.request``, a
per-call ``protoPayload.resourceName`` and a ``logName`` on gcp) and flat,
top-level hint keys so a query needs no nested-JSON access:
``audit_provider`` (``aws`` / ``gcp``, the ``provider`` param as given),
``api_operation`` (the eventName / methodName), ``principal_id`` (the caller
ARN / principalEmail), ``log_resource_name`` (the trail, sink, log bucket or
storage bucket acted on) and, where the call has one, ``log_object_key``,
``log_destination``, ``kms_key_id``, ``log_disabled``. Every hint restates a
provider-true field of the same record. The original eleven presets set none
of this and render byte-for-byte what they rendered before (pinned by
``TestLegacyPresetsUnchanged``).

Two notes on the hints. The provider hint is not called ``cloud_provider``:
that name belongs to normalised ``cloud_audit_logs``, where existing cards
filter it as ``"AWS"`` / ``"GCP"``; a collector record is not that dataset, so
it gets its own name and the param's own lower-case values. And
``principal_id`` follows a planted canary on gcp only (the plant rewrites
``principalEmail``); on aws the plant rewrites ``userName``, not the ARN, so
join on ``cortexsim_canary`` there.

Builder defaults that no provider page was found to settle, kept as they were
and pinned as unverified in the tests: the ``NOTICE`` severity on gcp Data
Access entries (Google's sample shows it on an Admin Activity entry), and the
project-level ``resource`` block on Cloud Logging configuration calls.

What they deliberately do NOT emit: anything the attacker does in their own
account or project (creating the external key, editing or deleting its policy,
re-creating a deleted bucket name). A victim's audit log does not contain those
events, so a record of one would be a fabricated signal.

Names and field shapes are from provider documentation, not from CLI spellings
(``stop-logging``) or IAM permission names (``logging.sinks.update``):

  * AWS trail calls — CloudTrail API reference (StopLogging, DeleteTrail,
    UpdateTrail, CreateTrail) and the User Guide's "CloudTrail log file
    examples", whose UpdateTrail record shows the lower-camel request keys
    (``name``, ``isMultiRegionTrail``) and ``eventCategory``.
  * AWS S3 calls — S3 User Guide: "Amazon S3 CloudTrail events" (bucket-level
    calls are management events, object-level calls are data events) and
    "Identifying Amazon S3 requests using CloudTrail" (``requestParameters``
    ``bucketName`` / ``key``).
  * Cloud Logging calls — "Cloud Logging audit logging" (method names, all
    ADMIN_WRITE / Admin Activity) and the published ``google.logging.v2``
    request messages (``sinkName``, ``sink``, ``updateMask``,
    ``uniqueWriterIdentity``, ``parent``, ``name``, ``bucket``,
    ``cmekSettings.kmsKeyName``). The log bucket is regional and the external
    key sits in the same region, per "Configure CMEK for log buckets"
    (Limitations: the key's region must match the bucket's, and CMEK cannot
    be configured on a log bucket in the global location).
  * Cloud Storage calls — "Cloud Audit Logs with Cloud Storage" (service name,
    ``gcs_bucket`` resource, Admin Activity vs Data Access, the log names, and
    that request/response are written only under detailed audit logging mode,
    hence no ``request`` block here); method-name literals and the
    ``projects/_/buckets/<bucket>/objects/<object>`` resource name per Google's
    audit-log parser and Eventarc path-pattern references.

Honest limit. The Cortex analytics alerts these calls correspond to on a
natively onboarded account ("AWS CloudTrail has been stopped", "AWS CloudTrail
modification", "CloudTrail logging deletion", "Suspicious activity on logging
bucket", "Logging was impaired via external encryption key", "GCP logging sink
modification", "GCP logging sink deletion", "GCP Logging Bucket Deletion", "GCP
Storage Bucket deletion") each document a 14-day activation and 30-day training
period over the AWS / GCP Audit Log data source. Records POSTed to an HTTP
collector land in the collector's own ``<Vendor>_<Product>_raw`` dataset and do
not arm them, so this plugin declares no ``Meta.detectors`` for them: on a
collector these presets feed XQL and correlation rules, nothing more.
"""

from __future__ import annotations

import copy
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

# -- logging-service presets: the victim's logging resources ----------------
# All synthetic. Nothing here is looked up, resolved or called: the names only
# ever appear as string values inside an emitted JSON record.
_AWS_REGION = "us-east-1"
_CANARY_TRAIL = "cortexsim-canary-trail"
_CANARY_TRAIL_ARN = (
    f"arn:aws:cloudtrail:{_AWS_REGION}:{_CANARY_ACCOUNT}:trail/{_CANARY_TRAIL}"
)
_CANARY_SHADOW_TRAIL = "cortexsim-canary-shadow-trail"
_CANARY_SHADOW_TRAIL_ARN = (
    f"arn:aws:cloudtrail:{_AWS_REGION}:{_CANARY_ACCOUNT}:trail/{_CANARY_SHADOW_TRAIL}"
)
# The S3 bucket the trail delivers to, and a stored log object in it.
_CANARY_TRAIL_BUCKET = "cortexsim-canary-cloudtrail-logs"
# ``{seq}`` is replaced per record (0001, 0002, ...) so a burst names N
# different objects rather than one object N times.
_SEQ = "{seq}"
_CANARY_TRAIL_LOG_KEY = (
    f"AWSLogs/{_CANARY_ACCOUNT}/CloudTrail/{_AWS_REGION}/2026/01/01/"
    f"cortexsim-canary-{_SEQ}.json.gz"
)
# A data stream's destination bucket (S3 replication / Firehose target).
_CANARY_STREAM_BUCKET = "cortexsim-canary-stream-archive"
_CANARY_STREAM_KEY = f"replica/2026/01/01/cortexsim-canary-{_SEQ}.json.gz"

_CANARY_SINK_ID = "cortexsim-canary-sink"
_CANARY_SINK = f"projects/{_CANARY_PROJECT}/sinks/{_CANARY_SINK_ID}"
_CANARY_SHADOW_SINK_ID = "cortexsim-canary-shadow-sink"
_CANARY_SHADOW_SINK = f"projects/{_CANARY_PROJECT}/sinks/{_CANARY_SHADOW_SINK_ID}"
# The Cloud Storage bucket the sink routes to, and a routed log object in it.
_CANARY_SINK_BUCKET = "cortexsim-canary-log-archive"
_CANARY_SINK_DESTINATION = f"storage.googleapis.com/{_CANARY_SINK_BUCKET}"
_CANARY_SINK_BUCKET_RESOURCE = f"projects/_/buckets/{_CANARY_SINK_BUCKET}"
_CANARY_SINK_OBJECT = (
    f"cloudaudit.googleapis.com/activity/2026/01/01/cortexsim-canary-{_SEQ}.json"
)
# The Cloud Logging log bucket (a different thing from a Cloud Storage bucket).
# Regional on purpose. Google, "Configure CMEK for log buckets", Limitations:
# a log bucket's key must be in the bucket's own region, and CMEK cannot be
# configured on a log bucket in the ``global`` location. log_key_swap renders a
# SUCCESSFUL key change, so the bucket and the external key (_ATTACKER_CMEK_KEY)
# share this one region; a global bucket there is a record no tenant can
# produce. log_destination_delete (gcp) deletes the same bucket.
_GCP_LOG_REGION = "us-central1"
_CANARY_LOG_BUCKET = (
    f"projects/{_CANARY_PROJECT}/locations/{_GCP_LOG_REGION}/buckets/"
    "cortexsim-canary-log-bucket"
)

# -- logging-service presets: what the ATTACKER controls ----------------------
# Each is a synthetic canary NAME. The KMS key sits in 444455556666, an account
# id AWS's own documentation examples use, and in the plugin's existing foreign
# project — "external" is the whole point of the key-swap technique, so it has
# to be readable off the value. None is a credential, a real tenant or an IOC.
_ATTACKER_S3_BUCKET = "cortexsim-canary-attacker-logs-999"
_ATTACKER_KMS_KEY = (
    f"arn:aws:kms:{_AWS_REGION}:444455556666:alias/cortexsim-canary-external-key"
)
_ATTACKER_SINK_DESTINATION = "storage.googleapis.com/cortexsim-canary-attacker-log-drop"
# Another project, the SAME region as the log bucket it is applied to (see
# _GCP_LOG_REGION): external by project, valid by location.
_ATTACKER_CMEK_KEY = (
    f"projects/attacker-project-999/locations/{_GCP_LOG_REGION}/keyRings/"
    "cortexsim-canary-ring/cryptoKeys/cortexsim-canary-key"
)

# Monitored-resource block Cloud Storage audit entries carry.
_GCS_RESOURCE = {
    "type": "gcs_bucket",
    "labels": {
        "project_id": _CANARY_PROJECT,
        "bucket_name": _CANARY_SINK_BUCKET,
        "location": "us",
    },
}
# ``@type`` prefix of a google.logging.v2 request message in protoPayload.request.
_GCP_LOGGING_REQUEST = "type.googleapis.com/google.logging.v2."
_GCP_CONFIG_SERVICE = "google.logging.v2.ConfigServiceV2."


# --------------------------------------------------------------------------
# Per-pattern control-plane activity (shared by both provider builders)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _CloudActivity:
    """One control-plane API call, expressed for both providers.

    ``read_only`` / ``management_event`` mirror the CloudTrail fields so
    read-only enumeration (ListUsers, GetObject, ...) and data-plane events
    (GetObject / ListObjects) are shaped truthfully rather than always looking
    like a management mutation. On GCP ``management_event`` is the Admin
    Activity vs Data Access split.

    The fields from ``aws_request_parameters`` down are per-call request
    detail, used by the logging-service presets. Every one defaults to unset,
    and an activity that leaves them unset renders exactly the record it
    rendered before they existed — the builders add a key only when its field
    is set. Any string in them may carry ``{seq}``, filled per record.
    """
    aws_event_name: str
    aws_event_source: str
    gcp_method_name: str
    gcp_service_name: str
    outcome: str = "success"  # "success" | "failure"
    read_only: bool = False
    management_event: bool = True
    # AWS: the call's ``requestParameters`` (lower-camel keys, as CloudTrail
    # records them). Setting it also writes ``eventCategory``.
    aws_request_parameters: Optional[dict[str, Any]] = None
    # GCP: ``protoPayload.request`` (the request message, ``@type`` first).
    gcp_request: Optional[dict[str, Any]] = None
    # GCP: per-call ``protoPayload.resourceName``. Setting it also writes
    # ``logName`` (activity vs data_access, from ``management_event``).
    gcp_resource_name: Optional[str] = None
    # GCP: the monitored-resource block, when the service documents its own
    # (Cloud Storage: gcs_bucket). Unset keeps the project-level block.
    gcp_resource: Optional[dict[str, Any]] = None
    # Flat top-level hint keys for this call, per provider.
    aws_hints: Optional[dict[str, Any]] = None
    gcp_hints: Optional[dict[str, Any]] = None
    # Fan this call out to ``burst_count`` records (each with its own {seq}).
    burst: bool = False
    # Set when the call exists on one provider only; the other skips it.
    only_provider: Optional[str] = None


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
    if activity.aws_request_parameters is not None:
        # Per-call request detail (logging-service presets). CloudTrail's
        # eventCategory is Management for control-plane calls and Data for
        # resource-level ones such as S3 object operations.
        body.update({
            "requestParameters": copy.deepcopy(activity.aws_request_parameters),
            "eventCategory": "Management" if activity.management_event else "Data",
        })
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
    # Per-call request detail (logging-service presets). Each key is written
    # only when its field is set, so the original presets are untouched.
    if activity.gcp_resource_name is not None:
        body["protoPayload"]["resourceName"] = activity.gcp_resource_name
        # Admin Activity entries land in the ``activity`` log, Data Access
        # entries (object reads / writes) in ``data_access``.
        log_id = "activity" if activity.management_event else "data_access"
        body.update({
            "logName": (
                f"projects/{_CANARY_PROJECT}/logs/cloudaudit.googleapis.com%2F{log_id}"
            ),
        })
    if activity.gcp_request is not None:
        body["protoPayload"].update({
            "request": copy.deepcopy(activity.gcp_request),
        })
    if activity.gcp_resource is not None:
        body["resource"] = copy.deepcopy(activity.gcp_resource)
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


# --------------------------------------------------------------------------
# Logging-service presets — cloud log tampering and log re-routing
# --------------------------------------------------------------------------

_AWS_CLOUDTRAIL = "cloudtrail.amazonaws.com"
_AWS_S3 = "s3.amazonaws.com"
_GCP_LOGGING = "logging.googleapis.com"
_GCP_STORAGE = "storage.googleapis.com"


def _s3_call(bucket: str, key: Optional[str] = None) -> dict[str, Any]:
    """The aws_* fields of one S3 call on ``bucket`` (object-level with ``key``)."""
    request: dict[str, Any] = {"bucketName": bucket}
    hints: dict[str, Any] = {"log_resource_name": f"arn:aws:s3:::{bucket}"}
    if key is not None:
        request.update({"key": key})
        hints.update({"log_object_key": key})
    return {"aws_request_parameters": request, "aws_hints": hints}


def _gcs_call(obj: Optional[str] = None) -> dict[str, Any]:
    """The gcp_* fields of one Cloud Storage call on the sink's bucket.

    No ``gcp_request``: Cloud Storage writes request/response into an audit
    entry only under detailed audit logging mode, which is off by default.
    """
    resource_name = _CANARY_SINK_BUCKET_RESOURCE
    hints: dict[str, Any] = {"log_resource_name": _CANARY_SINK_BUCKET_RESOURCE}
    if obj is not None:
        resource_name = f"{resource_name}/objects/{obj}"
        hints.update({"log_object_key": obj})
    return {
        "gcp_resource_name": resource_name,
        "gcp_resource": _GCS_RESOURCE,
        "gcp_hints": hints,
    }


def _gcp_sink_update(sink: dict[str, Any], mask: str, **more: Any) -> dict[str, Any]:
    """An UpdateSinkRequest on the canary sink changing the ``mask`` field."""
    request: dict[str, Any] = {
        "@type": _GCP_LOGGING_REQUEST + "UpdateSinkRequest",
        "sinkName": _CANARY_SINK,
        "sink": {"name": _CANARY_SINK_ID, **sink},
        "updateMask": mask,
    }
    request.update(more)
    return request


# Same shape as _PATTERN_ACTIVITY: pattern -> (marker, [activities...]). The
# difference is in how build_events walks the list (see _build_logging_events):
# an activity may be single-provider, and bursts are per activity, so "empty
# the bucket, then delete it" is burst_count object deletes followed by ONE
# bucket delete.
#
# Only VICTIM-side calls are here. The attacker's own-account steps (creating
# the external key, removing CloudTrail's access to it, re-creating a deleted
# bucket name) never reach the victim's audit log and are not emitted.
_LOGGING_PATTERN_ACTIVITY: dict[str, tuple[str, list[_CloudActivity]]] = {
    # Stop logging: StopLogging on the trail / the sink's disabled flag set.
    "log_stop": (
        "log_stop_marker",
        [
            _CloudActivity(
                aws_event_name="StopLogging",
                aws_event_source=_AWS_CLOUDTRAIL,
                gcp_method_name=_GCP_CONFIG_SERVICE + "UpdateSink",
                gcp_service_name=_GCP_LOGGING,
                aws_request_parameters={"name": _CANARY_TRAIL_ARN},
                aws_hints={"log_resource_name": _CANARY_TRAIL_ARN},
                gcp_request=_gcp_sink_update(
                    {"destination": _CANARY_SINK_DESTINATION, "disabled": True},
                    "disabled",
                ),
                gcp_resource_name=_CANARY_SINK,
                gcp_hints={"log_resource_name": _CANARY_SINK, "log_disabled": True},
            ),
        ],
    ),
    # Delete the log storage destination. AWS: empty the trail's S3 bucket
    # (object deletes are data events), then delete it. GCP: one DeleteBucket
    # on the Cloud Logging log bucket — a log bucket has no object-level API.
    "log_destination_delete": (
        "log_destination_delete_marker",
        [
            _CloudActivity(
                aws_event_name="DeleteObject",
                aws_event_source=_AWS_S3,
                gcp_method_name="",
                gcp_service_name="",
                management_event=False,
                burst=True,
                only_provider="aws",
                **_s3_call(_CANARY_TRAIL_BUCKET, _CANARY_TRAIL_LOG_KEY),
            ),
            _CloudActivity(
                aws_event_name="DeleteBucket",
                aws_event_source=_AWS_S3,
                gcp_method_name=_GCP_CONFIG_SERVICE + "DeleteBucket",
                gcp_service_name=_GCP_LOGGING,
                gcp_request={
                    "@type": _GCP_LOGGING_REQUEST + "DeleteBucketRequest",
                    "name": _CANARY_LOG_BUCKET,
                },
                gcp_resource_name=_CANARY_LOG_BUCKET,
                gcp_hints={"log_resource_name": _CANARY_LOG_BUCKET},
                **_s3_call(_CANARY_TRAIL_BUCKET),
            ),
        ],
    ),
    # Delete the log router: the trail / the sink.
    "log_router_delete": (
        "log_router_delete_marker",
        [
            _CloudActivity(
                aws_event_name="DeleteTrail",
                aws_event_source=_AWS_CLOUDTRAIL,
                gcp_method_name=_GCP_CONFIG_SERVICE + "DeleteSink",
                gcp_service_name=_GCP_LOGGING,
                aws_request_parameters={"name": _CANARY_TRAIL_ARN},
                aws_hints={"log_resource_name": _CANARY_TRAIL_ARN},
                gcp_request={
                    "@type": _GCP_LOGGING_REQUEST + "DeleteSinkRequest",
                    "sinkName": _CANARY_SINK,
                },
                gcp_resource_name=_CANARY_SINK,
                gcp_hints={"log_resource_name": _CANARY_SINK},
            ),
        ],
    ),
    # Impair logging through an external encryption key. Only the repointing
    # call is victim-side; what the attacker then does to the key is not.
    "log_key_swap": (
        "log_key_swap_marker",
        [
            _CloudActivity(
                aws_event_name="UpdateTrail",
                aws_event_source=_AWS_CLOUDTRAIL,
                gcp_method_name=_GCP_CONFIG_SERVICE + "UpdateBucket",
                gcp_service_name=_GCP_LOGGING,
                aws_request_parameters={
                    "name": _CANARY_TRAIL,
                    "kmsKeyId": _ATTACKER_KMS_KEY,
                },
                aws_hints={
                    "log_resource_name": _CANARY_TRAIL_ARN,
                    "kms_key_id": _ATTACKER_KMS_KEY,
                },
                gcp_request={
                    "@type": _GCP_LOGGING_REQUEST + "UpdateBucketRequest",
                    "name": _CANARY_LOG_BUCKET,
                    "bucket": {"cmekSettings": {"kmsKeyName": _ATTACKER_CMEK_KEY}},
                    "updateMask": "cmekSettings",
                },
                gcp_resource_name=_CANARY_LOG_BUCKET,
                gcp_hints={
                    "log_resource_name": _CANARY_LOG_BUCKET,
                    "kms_key_id": _ATTACKER_CMEK_KEY,
                },
            ),
        ],
    ),
    # Log poisoning: download a stored log object, overwrite it. Both calls
    # name the SAME object ({seq} resolves to 0001 for each) under one identity.
    "log_object_overwrite": (
        "log_object_overwrite_marker",
        [
            _CloudActivity(
                aws_event_name="GetObject",
                aws_event_source=_AWS_S3,
                gcp_method_name="storage.objects.get",
                gcp_service_name=_GCP_STORAGE,
                read_only=True,
                management_event=False,
                **_s3_call(_CANARY_TRAIL_BUCKET, _CANARY_TRAIL_LOG_KEY),
                **_gcs_call(_CANARY_SINK_OBJECT),
            ),
            _CloudActivity(
                aws_event_name="PutObject",
                aws_event_source=_AWS_S3,
                gcp_method_name="storage.objects.create",
                gcp_service_name=_GCP_STORAGE,
                read_only=False,
                management_event=False,
                **_s3_call(_CANARY_TRAIL_BUCKET, _CANARY_TRAIL_LOG_KEY),
                **_gcs_call(_CANARY_SINK_OBJECT),
            ),
        ],
    ),
    # Continuous visibility: stand up a second route to attacker storage.
    "log_router_create": (
        "log_router_create_marker",
        [
            _CloudActivity(
                aws_event_name="CreateTrail",
                aws_event_source=_AWS_CLOUDTRAIL,
                gcp_method_name=_GCP_CONFIG_SERVICE + "CreateSink",
                gcp_service_name=_GCP_LOGGING,
                aws_request_parameters={
                    "name": _CANARY_SHADOW_TRAIL,
                    "s3BucketName": _ATTACKER_S3_BUCKET,
                    "isMultiRegionTrail": True,
                },
                aws_hints={
                    "log_resource_name": _CANARY_SHADOW_TRAIL_ARN,
                    "log_destination": _ATTACKER_S3_BUCKET,
                },
                gcp_request={
                    "@type": _GCP_LOGGING_REQUEST + "CreateSinkRequest",
                    "parent": f"projects/{_CANARY_PROJECT}",
                    "sink": {
                        "name": _CANARY_SHADOW_SINK_ID,
                        "destination": _ATTACKER_SINK_DESTINATION,
                    },
                    "uniqueWriterIdentity": True,
                },
                gcp_resource_name=_CANARY_SHADOW_SINK,
                gcp_hints={
                    "log_resource_name": _CANARY_SHADOW_SINK,
                    "log_destination": _ATTACKER_SINK_DESTINATION,
                },
            ),
        ],
    ),
    # Continuous visibility: point the existing route at attacker storage.
    "log_router_redirect": (
        "log_router_redirect_marker",
        [
            _CloudActivity(
                aws_event_name="UpdateTrail",
                aws_event_source=_AWS_CLOUDTRAIL,
                gcp_method_name=_GCP_CONFIG_SERVICE + "UpdateSink",
                gcp_service_name=_GCP_LOGGING,
                aws_request_parameters={
                    "name": _CANARY_TRAIL,
                    "s3BucketName": _ATTACKER_S3_BUCKET,
                },
                aws_hints={
                    "log_resource_name": _CANARY_TRAIL_ARN,
                    "log_destination": _ATTACKER_S3_BUCKET,
                },
                gcp_request=_gcp_sink_update(
                    {"destination": _ATTACKER_SINK_DESTINATION},
                    "destination",
                    uniqueWriterIdentity=True,
                ),
                gcp_resource_name=_CANARY_SINK,
                gcp_hints={
                    "log_resource_name": _CANARY_SINK,
                    "log_destination": _ATTACKER_SINK_DESTINATION,
                },
            ),
        ],
    ),
    # Victim side of a bucket-name hijack: empty the bucket a data stream
    # writes to, then delete it. Whoever re-creates the name does so in their
    # own account or project, which this audit log never sees.
    "stream_bucket_delete": (
        "stream_bucket_delete_marker",
        [
            _CloudActivity(
                aws_event_name="DeleteObject",
                aws_event_source=_AWS_S3,
                gcp_method_name="storage.objects.delete",
                gcp_service_name=_GCP_STORAGE,
                management_event=False,
                burst=True,
                **_s3_call(_CANARY_STREAM_BUCKET, _CANARY_STREAM_KEY),
                **_gcs_call(_CANARY_SINK_OBJECT),
            ),
            _CloudActivity(
                aws_event_name="DeleteBucket",
                aws_event_source=_AWS_S3,
                gcp_method_name="storage.buckets.delete",
                gcp_service_name=_GCP_STORAGE,
                **_s3_call(_CANARY_STREAM_BUCKET),
                **_gcs_call(),
            ),
        ],
    ),
}

_LOGGING_PATTERNS: frozenset[str] = frozenset(_LOGGING_PATTERN_ACTIVITY)
_PATTERN_ACTIVITY.update(_LOGGING_PATTERN_ACTIVITY)


def _fill(value: Any, seq: int) -> Any:
    """Copy ``value`` with every ``{seq}`` replaced by a 4-digit sequence.

    Always returns fresh containers, so a rendered record never shares a dict
    with the preset table (records are mutated in place downstream).
    """
    if isinstance(value, str):
        return value.replace(_SEQ, f"{seq:04d}")
    if isinstance(value, dict):
        return {k: _fill(v, seq) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, seq) for v in value]
    return value


def _operation_of(record: dict[str, Any]) -> str:
    """The API operation a record is about (eventName / methodName)."""
    if "eventName" in record:
        return record["eventName"]
    return record["protoPayload"]["methodName"]


def _principal_of(record: dict[str, Any]) -> str:
    """The caller a record is about (CloudTrail user ARN / principalEmail)."""
    if "userIdentity" in record:
        return record["userIdentity"]["arn"]
    return record["protoPayload"]["authenticationInfo"]["principalEmail"]


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
                    "console_login_anomaly. Logging-service presets (cloud log "
                    "tampering / log re-routing, records carry the call's request "
                    "parameters): log_stop | log_destination_delete | "
                    "log_router_delete | log_key_swap | log_object_overwrite | "
                    "log_router_create | log_router_redirect | "
                    "stream_bucket_delete. burst_count sizes the object-delete "
                    "burst of log_destination_delete (aws) and "
                    "stream_bucket_delete; the bucket delete that follows is "
                    "always a single record. Every iteration reuses the same "
                    "resource names, so with iterations > 1 "
                    "log_destination_delete, log_router_delete, "
                    "log_router_create and stream_bucket_delete repeat a "
                    "delete or create of one named resource, which no tenant "
                    "could produce.",
    )
    source_ip: Optional[str] = Field(
        default=None,
        description="Override the caller IP written on every record "
                    "(sourceIPAddress for aws, requestMetadata.callerIp for gcp), "
                    "e.g. a published threat-intel indicator the tenant can "
                    "enrich. Unset keeps the built-in anomalous documentation IP.",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset both providers normalise to."""
        return _DATASET

    @field_validator("source_ip")
    @classmethod
    def _source_ip_quad(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        parts = v.split(".")
        if len(parts) != 4 or not all(
            p.isascii() and p.isdigit() and 0 <= int(p) <= 255 for p in parts
        ):
            raise ValueError(f"source_ip must be a dotted-quad IPv4 address, got '{v}'")
        return v

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
    # One map spans both providers: the AWS path exists only on CloudTrail
    # records and the GCP path only on AuditLog records, and plant_canary skips
    # whichever is absent.
    CANARY_FIELDS = {
        "userIdentity.userName": "{value}-{account}",
        "protoPayload.authenticationInfo.principalEmail": "{principal}",
    }

    class Meta:
        name = "cloud_audit_emitter"
        # Vendor analytics catalogue join (see analytics_catalogue.py). One
        # emitter, two catalogue sources — AWS + GCP audit logs both normalise
        # to cloud_audit_logs.
        data_sources = ["aws_audit_log", "gcp_audit_log"]
        datasets = ["cloud_audit_logs"]
        version = "1.2.0"
        description = (
            "Emits shape-true cloud control-plane audit records (AWS CloudTrail "
            "/ GCP Cloud Audit Logs shape, cloud_audit_logs dataset) into an "
            "operator-supplied collector so Cortex XSIAM exercises its cloud "
            "Analytics / ABIOC detections — mass object download, secrets dump, "
            "SSM parameter retrieval, IAM / S3 enumeration sequences (user and "
            "non-user), BigQuery foreign-project exfiltration, service-account "
            "impersonation, IAM privilege escalation, storage public-exposure and "
            "anomalous console login — without touching a real AWS/GCP tenant. "
            "Also emits the victim-side records of cloud log tampering and log "
            "re-routing against CloudTrail and Cloud Logging, with each call's "
            "request parameters: stop logging, delete the log destination or "
            "the log router, swap the log encryption key for an external one, "
            "overwrite a stored log object, create or redirect a log route to "
            "attacker-named storage, and empty-then-delete a data stream's "
            "bucket."
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
            "T1537",       # Transfer Data to Cloud Account (BigQuery foreign exfil;
                           # a new log route to attacker storage)
            "T1562.008",   # Impair Defenses: Disable or Modify Cloud Logs
            "T1485",       # Data Destruction (log destination / stream bucket)
            "T1565.001",   # Data Manipulation: Stored Data (log poisoning)
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
            # Logging-service presets. On an HTTP collector these records feed
            # XQL and correlation rules only: the vendor analytics alerts named
            # in the module docstring need a natively onboarded audit source
            # plus a 14-day activation / 30-day training period.
            "XQL / Correlation — logging stopped (CloudTrail StopLogging / "
            "Cloud Logging UpdateSink disabled)",
            "XQL / Correlation — log storage destination deleted (S3 "
            "DeleteObject burst + DeleteBucket / Cloud Logging DeleteBucket)",
            "XQL / Correlation — log router deleted (CloudTrail DeleteTrail / "
            "Cloud Logging DeleteSink)",
            "XQL / Correlation — log encryption key swapped for an external key "
            "(CloudTrail UpdateTrail kmsKeyId / Cloud Logging UpdateBucket "
            "cmekSettings)",
            "XQL / Correlation — stored log object overwritten (S3 GetObject + "
            "PutObject / storage.objects.get + storage.objects.create)",
            "XQL / Correlation — new log route to attacker-named storage "
            "(CloudTrail CreateTrail / Cloud Logging CreateSink)",
            "XQL / Correlation — log route redirected (CloudTrail UpdateTrail "
            "s3BucketName / Cloud Logging UpdateSink destination)",
            "XQL / Correlation — data-stream bucket emptied then deleted (S3 "
            "DeleteObject burst + DeleteBucket / storage.objects.delete burst + "
            "storage.buckets.delete)",
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
        ip_kw: dict[str, Any] = (
            {"source_ip": params.source_ip} if params.source_ip is not None else {}
        )

        if params.event_pattern == "console_login_anomaly":
            # A burst of failed console logins from the anomalous IP, then the
            # single successful sign-in that completes the ABIOC anomaly.
            for _ in range(params.burst_count):
                events.append(builder(
                    activity=_CONSOLE_LOGIN_FAIL,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    extra={"mfa_used": False, "login_result": "Failure"},
                    **ip_kw,
                ))
            events.append(builder(
                activity=activities[0],
                marker=marker,
                sim_run_id=sim_run_id,
                extra={"mfa_used": False, "login_result": "Success"},
                **ip_kw,
            ))
            return events

        if params.event_pattern in _LOGGING_PATTERNS:
            return self._build_logging_events(
                params, builder=builder, marker=marker, activities=activities,
                sim_run_id=sim_run_id, ip_kw=ip_kw,
            )

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
                    **ip_kw,
                ))
        return events

    def _build_logging_events(
        self, params: CloudAuditEmitterParams, *, builder: Any, marker: str,
        activities: list[_CloudActivity], sim_run_id: str, ip_kw: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Records of one logging-service preset, in call order.

        Unlike the original presets, the burst is per ACTIVITY: an activity
        flagged ``burst`` fans out to ``burst_count`` records, each naming its
        own object, and the activities after it are emitted once — so a bucket
        is emptied and then deleted, not deleted ``burst_count`` times. An
        activity that exists on one provider only is skipped on the other.
        """
        events: list[dict[str, Any]] = []
        for activity in activities:
            if activity.only_provider not in (None, params.provider):
                continue
            count = params.burst_count if activity.burst else 1
            for seq in range(1, count + 1):
                call = dataclasses.replace(
                    activity,
                    aws_request_parameters=_fill(activity.aws_request_parameters, seq),
                    gcp_request=_fill(activity.gcp_request, seq),
                    gcp_resource_name=_fill(activity.gcp_resource_name, seq),
                    gcp_resource=_fill(activity.gcp_resource, seq),
                )
                record = builder(
                    activity=call, marker=marker, sim_run_id=sim_run_id, **ip_kw,
                )
                call_hints = (
                    activity.aws_hints if params.provider == "aws"
                    else activity.gcp_hints
                )
                # Flat hints last, each restating a provider-true field of
                # this same record (so XQL needs no nested-JSON access).
                # ``audit_provider`` is the ``provider`` param as given
                # (lower-case). It is deliberately NOT named cloud_provider:
                # that is a field of normalised cloud_audit_logs which existing
                # cards filter (as "AWS" / "GCP"), these records are not that
                # dataset, and check-streamer-fidelity.py reads every
                # dict-literal key in this file as emitted — the name would
                # hide its advisory on cards whose presets write no such field.
                record.update({
                    "audit_provider": params.provider,
                    "api_operation": _operation_of(record),
                    "principal_id": _principal_of(record),
                })
                record.update(_fill(call_hints or {}, seq))
                events.append(record)
        return events

    def records_for(
        self, params: AnalyticsEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        """The shared seam, plus one fix-up for the logging-service presets.

        The canary plant rewrites the record's principal in place (GCP
        ``principalEmail``). ``principal_id`` restates that field, so it is
        re-read after the plant; otherwise the hint would name the pre-plant
        identity. Records of the original presets carry no ``principal_id``
        and pass through untouched.
        """
        events = super().records_for(
            params, sim_run_id=sim_run_id, iteration=iteration,
        )
        for event in events:
            if "principal_id" in event:
                event["principal_id"] = _principal_of(event)
        return events

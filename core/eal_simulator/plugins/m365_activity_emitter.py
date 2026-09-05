"""
m365_activity_emitter — analytics log-streamer for the Microsoft 365 /
Exchange data-plane workloads unified audit log (SharePoint / OneDrive /
Exchange Online -> ``msft_o365_audit``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true Microsoft 365
Management Activity API records (the unified audit log XSIAM ingests as
``msft_o365_audit``) to an operator-supplied collector so a customer can
validate that their Cortex XSIAM **Analytics / ABIOC** Microsoft-365
detections fire — without touching a real tenant, reading a real mailbox, or
downloading a real file. Every record is a JSON blob shaped *like* the
O365 unified-audit schema.

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the transport, the ``ctx.authorise``-before-emit
gate, the dry-run short-circuit, budget charging, batching/gzip and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to a real
XSIAM Microsoft-365 analytics alert:

  ============================== ======================================================
  preset                         analytics / ABIOC alert it exercises
  ============================== ======================================================
  storage_exfiltration           Analytics — "Microsoft 365 storage services
                                  exfiltration activity" — a burst of FileDownloaded /
                                  FileSyncDownloadedFull on SharePoint/OneDrive
                                  (T1530 / T1567.002)
  unusual_storage_access         ABIOC — "Unusual access to Microsoft 365 storage
                                  services" — FileAccessed from an anomalous client IP
                                  (T1530)
  mailbox_enumeration_by_app     Analytics — "Mailbox enumeration activity by Azure
                                  application" — MailItemsAccessed bind-burst by an
                                  OAuth (Azure AD) application (T1114)
  sharepoint_enumeration         Analytics — "Microsoft SharePoint enumeration
                                  activity" — SearchQueryPerformed / PageViewed sweep
                                  across sites (T1213.002)
  onedrive_enumeration           Analytics — "Microsoft OneDrive enumeration activity"
                                  — FileAccessed enumeration across a OneDrive
                                  (T1213.002 / T1530)
  mailbox_rule_creation          Analytics — "Azure mailbox rule creation" — a
                                  New-InboxRule hide/forward rule (T1564.008 / T1114.003)
  ============================== ======================================================

All records carry ``cortexsim_run_id`` in-body and a top-level ``dataset``
hint (``msft_o365_audit``) exactly like the exemplars, plus the
``X-Simulation-Run-ID`` header the shared transport injects.
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.m365_activity_emitter")


# The XSIAM dataset the Microsoft 365 unified audit log normalises to.
_DATASET = "msft_o365_audit"

# Synthetic, non-resolving identifiers. The reserved .invalid TLD and the
# documentation IP ranges (RFC 5737) guarantee nothing here touches a real
# tenant or resolves on a real network.
_CANARY_TENANT_DOMAIN = "cortexsim-canary.invalid"
_CANARY_UPN = "ada.lovelace@cortexsim-canary.invalid"
_CANARY_ORG_ID = "00000000-0000-0000-0000-cortexsimcnry"
_ANOMALOUS_IP = "203.0.113.201"
# A synthetic Azure AD application performing the mailbox enumeration.
_CANARY_APP_ID = "11111111-2222-3333-4444-cortexsimapp0"
_CANARY_APP_NAME = "CortexSim-Canary-Graph-App"

# Canary storage locations (SharePoint site + OneDrive personal site).
_SHAREPOINT_SITE = f"https://{_CANARY_TENANT_DOMAIN.split('.')[0]}.sharepoint.invalid/sites/Finance"
_ONEDRIVE_SITE = (
    f"https://{_CANARY_TENANT_DOMAIN.split('.')[0]}-my.sharepoint.invalid"
    "/personal/ada_lovelace_cortexsim_canary_invalid"
)

# Synthetic bait documents the file-operation patterns act on.
_BAIT_FILES = (
    "Q4-Financials.xlsx",
    "Customer-Master-List.csv",
    "M&A-Due-Diligence.docx",
    "Credentials-Vault-Export.xlsx",
    "Board-Deck-Confidential.pptx",
    "Payroll-Register.xlsx",
    "Source-Code-Bundle.zip",
    "Employee-PII-Dump.csv",
)


# --------------------------------------------------------------------------
# Per-activity descriptor — one O365 unified-audit operation
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _M365Activity:
    """One O365 Management Activity API operation."""
    operation: str
    record_type: int      # O365 RecordType enum value
    workload: str         # SharePoint | OneDrive | Exchange
    item_type: str        # File | Folder | Message | Rule | Page


# RecordType enum values from the O365 Management Activity API schema.
_RT_EXCHANGE_ADMIN = 1
_RT_SHAREPOINT = 4
_RT_SHAREPOINT_FILE_OP = 6
_RT_EXCHANGE_ITEM_AGGREGATED = 50  # MailItemsAccessed lands here


# --------------------------------------------------------------------------
# Event-shape adapter — the O365 unified-audit record builder
# --------------------------------------------------------------------------


def _m365_audit_event(
    *,
    activity: _M365Activity,
    marker: str,
    sim_run_id: str,
    source_ip: str = _ANOMALOUS_IP,
    user_id: str = _CANARY_UPN,
    object_id: Optional[str] = None,
    site_url: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Microsoft 365 unified-audit-log record (subset of the O365
    Management Activity API schema -> ``msft_o365_audit``)."""
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "CreationTime": now,
        "Id": secrets.token_hex(16),
        "Operation": activity.operation,
        "OrganizationId": _CANARY_ORG_ID,
        "RecordType": activity.record_type,
        "UserType": 0,
        "UserKey": user_id,
        "Workload": activity.workload,
        "ResultStatus": "Succeeded",
        "ClientIP": source_ip,
        "UserId": user_id,
        "ObjectId": object_id or activity.operation,
        "ItemType": activity.item_type,
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if site_url is not None:
        body["SiteUrl"] = site_url
    if extra:
        body.update(extra)
    return body


# --------------------------------------------------------------------------
# Event patterns — the activity each preset emits + its marker
# --------------------------------------------------------------------------


_FILE_DOWNLOADED = _M365Activity(
    operation="FileDownloaded", record_type=_RT_SHAREPOINT_FILE_OP,
    workload="OneDrive", item_type="File",
)
_FILE_SYNC_DOWNLOAD = _M365Activity(
    operation="FileSyncDownloadedFull", record_type=_RT_SHAREPOINT_FILE_OP,
    workload="OneDrive", item_type="File",
)
_FILE_ACCESSED = _M365Activity(
    operation="FileAccessed", record_type=_RT_SHAREPOINT_FILE_OP,
    workload="OneDrive", item_type="File",
)
_MAIL_ITEMS_ACCESSED = _M365Activity(
    operation="MailItemsAccessed", record_type=_RT_EXCHANGE_ITEM_AGGREGATED,
    workload="Exchange", item_type="Message",
)
_SEARCH_QUERY = _M365Activity(
    operation="SearchQueryPerformed", record_type=_RT_SHAREPOINT,
    workload="SharePoint", item_type="Page",
)
_PAGE_VIEWED = _M365Activity(
    operation="PageViewed", record_type=_RT_SHAREPOINT,
    workload="SharePoint", item_type="Page",
)
_NEW_INBOX_RULE = _M365Activity(
    operation="New-InboxRule", record_type=_RT_EXCHANGE_ADMIN,
    workload="Exchange", item_type="Rule",
)


# Each pattern -> (marker, workload-hint). The record fan-out is bespoke per
# pattern in build_events (bursts differ), so we only key the marker here.
_PATTERN_MARKERS: dict[str, str] = {
    "storage_exfiltration": "storage_exfiltration_marker",
    "unusual_storage_access": "unusual_storage_access_marker",
    "mailbox_enumeration_by_app": "mailbox_enumeration_marker",
    "sharepoint_enumeration": "sharepoint_enumeration_marker",
    "onedrive_enumeration": "onedrive_enumeration_marker",
    "mailbox_rule_creation": "mailbox_rule_creation_marker",
}

_EVENT_PATTERNS = tuple(_PATTERN_MARKERS)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only event_pattern + target_user.
# --------------------------------------------------------------------------


class M365ActivityEmitterParams(AnalyticsEmitterParams):
    event_pattern: str = Field(
        default="storage_exfiltration",
        description="Analytics alert to exercise: storage_exfiltration | "
                    "unusual_storage_access | mailbox_enumeration_by_app | "
                    "sharepoint_enumeration | onedrive_enumeration | "
                    "mailbox_rule_creation.",
    )
    target_user: str = Field(
        default=_CANARY_UPN,
        description="Canary mailbox / OneDrive owner UPN the synthetic activity "
                    "is attributed to (user@host).",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset the M365 unified audit log normalises to."""
        return _DATASET

    @field_validator("event_pattern")
    @classmethod
    def _pattern_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _EVENT_PATTERNS:
            raise ValueError(
                f"event_pattern must be one of {sorted(_EVENT_PATTERNS)}, got '{v}'"
            )
        return v

    @field_validator("target_user")
    @classmethod
    def _target_user_mailbox(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("target_user must look like a mailbox (user@host)")
        return v


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class M365ActivityEmitter(AnalyticsLogEmitter):
    # UserId/UserKey take the canonical canary PRINCIPAL (not a suffix) so the
    # same human string appears here and in every identity source — that shared
    # value is what a cross-provider entity-resolution read-back joins on.
    CANARY_FIELDS = {
        "UserId": "{principal}",
        "UserKey": "{principal}",
        "ObjectId": "{value}-{account}",
    }

    class Meta:
        name = "m365_activity_emitter"
        # Vendor analytics catalogue join (see analytics_catalogue.py).
        data_sources = ["office_365_audit"]
        datasets = ["msft_o365_audit"]
        version = "1.0.0"
        description = (
            "Emits shape-true Microsoft 365 unified-audit-log records "
            "(SharePoint / OneDrive / Exchange Online data-plane workloads, "
            "msft_o365_audit dataset) into an operator-supplied collector so "
            "Cortex XSIAM exercises its Microsoft-365 Analytics / ABIOC "
            "detections (storage exfiltration, unusual storage access, mailbox "
            "enumeration by an Azure app, SharePoint/OneDrive enumeration, "
            "mailbox-rule creation) without touching a real tenant."
        )
        # Drawn from the analytics alerts this plugin fires. None are
        # Command-and-Control-tactic techniques, so live campaigns need only
        # simulation_authorized (not c2_authorized) per the SafetyPolicy gate.
        mitre_techniques = [
            "T1530",       # Data from Cloud Storage
            "T1567.002",   # Exfiltration to Cloud Storage
            "T1114",       # Email Collection
            "T1114.003",   # Email Forwarding Rule
            "T1213.002",   # Data from Information Repositories: SharePoint
            "T1564.008",   # Hide Artifacts: Email Hiding Rules
        ]
        eal_targets = [
            "Analytics — Microsoft 365 storage services exfiltration activity",
            "ABIOC — unusual access to Microsoft 365 storage services",
            "Analytics — mailbox enumeration activity by an Azure application",
            "Analytics — Microsoft SharePoint enumeration activity",
            "Analytics — Microsoft OneDrive enumeration activity",
            "Analytics — Azure mailbox rule creation (hide/forward inbox rule)",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "web"
        params_model = M365ActivityEmitterParams

    def build_events(
        self, params: M365ActivityEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKERS[params.event_pattern]
        user = params.target_user
        pattern = params.event_pattern
        events: list[dict[str, Any]] = []

        if pattern == "storage_exfiltration":
            # A burst of bulk downloads across the sensitive bait files — the
            # mass-download shape the exfiltration analytic keys on.
            for idx in range(params.burst_count):
                fname = _BAIT_FILES[idx % len(_BAIT_FILES)]
                events.append(_m365_audit_event(
                    activity=_FILE_DOWNLOADED if idx % 2 == 0 else _FILE_SYNC_DOWNLOAD,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    user_id=user,
                    object_id=f"{_ONEDRIVE_SITE}/Documents/{fname}",
                    site_url=_ONEDRIVE_SITE,
                    extra={
                        "SourceFileName": fname,
                        "SourceRelativeUrl": f"Documents/{fname}",
                        "download_count_marker": params.burst_count,
                    },
                ))
            return events

        if pattern == "unusual_storage_access":
            # File access from an anomalous client IP — the ABIOC unusual-access
            # anomaly. A short access run establishes the behavioural baseline
            # break.
            for idx in range(params.burst_count):
                fname = _BAIT_FILES[idx % len(_BAIT_FILES)]
                events.append(_m365_audit_event(
                    activity=_FILE_ACCESSED,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    source_ip=_ANOMALOUS_IP,
                    user_id=user,
                    object_id=f"{_SHAREPOINT_SITE}/Shared Documents/{fname}",
                    site_url=_SHAREPOINT_SITE,
                    extra={
                        "SourceFileName": fname,
                        "anomalous_geo_marker": "unusual-country",
                    },
                ))
            return events

        if pattern == "mailbox_enumeration_by_app":
            # MailItemsAccessed bind-burst attributed to ONE OAuth (Azure AD)
            # application binding across MANY DISTINCT mailboxes — the
            # mailbox-enumeration-by-app analytic gates on
            # `count_distinct(ObjectId) by AppId,... >= 5`, so the enumeration
            # must touch >= 5 distinct mailboxes under the same app (the breadth
            # is the signal). Fan out distinct mailbox UPNs in the target domain.
            domain = user.split("@", 1)[1] if "@" in user else "example.com"
            n_mailboxes = max(params.burst_count, 6)
            mailboxes = [user] + [
                f"enum-target-{i:02d}@{domain}" for i in range(1, n_mailboxes)
            ]
            for mbx in mailboxes:
                events.append(_m365_audit_event(
                    activity=_MAIL_ITEMS_ACCESSED,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    user_id=mbx,
                    object_id=mbx,
                    extra={
                        "MailboxOwnerUPN": mbx,
                        "AppId": _CANARY_APP_ID,
                        "ClientAppId": _CANARY_APP_ID,
                        "ClientInfoString": f"Client=OAuthApp;AppId={_CANARY_APP_ID}",
                        "AppName": _CANARY_APP_NAME,
                        "LogonType": 0,
                        "MailAccessType": "Bind",
                        "OperationCount": len(mailboxes),
                    },
                ))
            return events

        if pattern == "sharepoint_enumeration":
            # A sweep of search queries + page views across a SharePoint site —
            # the SharePoint-enumeration analytic.
            search_terms = ("password", "confidential", "ssn", "salary", "api key")
            for idx in range(params.burst_count):
                term = search_terms[idx % len(search_terms)]
                events.append(_m365_audit_event(
                    activity=_SEARCH_QUERY,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    user_id=user,
                    object_id=f"{_SHAREPOINT_SITE}/_layouts/15/search.aspx",
                    site_url=_SHAREPOINT_SITE,
                    extra={"SearchQueryText": term},
                ))
                events.append(_m365_audit_event(
                    activity=_PAGE_VIEWED,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    user_id=user,
                    object_id=f"{_SHAREPOINT_SITE}/SitePages/Home.aspx",
                    site_url=_SHAREPOINT_SITE,
                ))
            return events

        if pattern == "onedrive_enumeration":
            # Rapid enumeration (FileAccessed sweep) across a OneDrive — the
            # OneDrive-enumeration analytic.
            for idx in range(params.burst_count):
                fname = _BAIT_FILES[idx % len(_BAIT_FILES)]
                events.append(_m365_audit_event(
                    activity=_FILE_ACCESSED,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    user_id=user,
                    object_id=f"{_ONEDRIVE_SITE}/Documents/{fname}",
                    site_url=_ONEDRIVE_SITE,
                    extra={
                        "SourceFileName": fname,
                        "SourceRelativeUrl": f"Documents/{fname}",
                        "enumeration_marker": "onedrive-listing",
                    },
                ))
            return events

        if pattern == "mailbox_rule_creation":
            # A single New-InboxRule that hides + forwards inbound mail — the
            # classic BEC hide-rule the mailbox-rule-creation analytic fires on.
            return [_m365_audit_event(
                activity=_NEW_INBOX_RULE,
                marker=marker,
                sim_run_id=sim_run_id,
                user_id=user,
                object_id="CortexSim-AutoForward-And-Hide",
                extra={
                    "MailboxOwnerUPN": user,
                    "RuleName": "CortexSim-AutoForward-And-Hide",
                    "Parameters": [
                        {"Name": "ForwardTo", "Value": "external.drop@partner-supplier.invalid"},
                        {"Name": "MoveToFolder", "Value": "RSS Subscriptions"},
                        {"Name": "MarkAsRead", "Value": "True"},
                        {"Name": "DeleteMessage", "Value": "False"},
                        {"Name": "SubjectContainsWords", "Value": "invoice;payment;wire"},
                    ],
                    "hide_rule_marker": True,
                    "forwarding_rule_marker": True,
                },
            )]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {pattern!r}"
        )

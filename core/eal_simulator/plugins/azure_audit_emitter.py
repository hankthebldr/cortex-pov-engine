"""
azure_audit_emitter — analytics log-streamer for the Azure Audit Log
(Activity Log control-plane) data source -> ``msft_azure_audit``.

Built on the ``analytics_emitter`` spine. It POSTs shape-true Azure Activity
Log records (the Azure Monitor / Event Hub streaming shape: string
``operationName``, ``category``, ``resultType``, ``callerIpAddress``,
``correlationId``, ``identity.claims``, ``tenantId``, ``properties``) to an
operator-supplied collector so a customer can validate that their Cortex
XSIAM **Analytics / ABIOC** Azure control-plane detections fire — without
touching a real Azure/Entra tenant or performing any real control-plane
mutation. Every record is a JSON blob shaped *like* an Azure Activity Log
control-plane audit record.

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the transport, the ``ctx.authorise``-before-emit
gate, the dry-run short-circuit, budget charging, batching/gzip and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to a real XSIAM
Azure-audit analytics alert:

  ================================ =====================================================
  preset                           analytics / ABIOC alert it exercises
  ================================ =====================================================
  service_principal_token_remote_use  Analytics — "Remote usage of an Azure Service
                                       Principal token" (app-only token used from an
                                       anomalous IP) — T1550.001 / T1078.004
  unknown_tenant_app_access           Analytics — "Attempted Azure application access
                                       from unknown tenant" (foreign tenant claim,
                                       failed access) — T1078.004
  unusual_app_resource_access         ABIOC — "Unusual resource access by Azure
                                       application" (behavioural burst of resource
                                       reads / listKeys by a service principal) — T1526
  conditional_access_policy_change    Analytics — "Azure conditional access policy
                                       creation or modification" — T1556.009 / T1098
  key_vault_modified                  Analytics — "An Azure Key Vault was modified"
                                       (vault write / access-policy change) — T1098 /
                                       T1555
  app_credentials_added               Analytics — "Credentials were added to Azure
                                       application" (secret / certificate added) —
                                       T1098.001
  ================================ =====================================================

All records normalise to the ``msft_azure_audit`` dataset in XSIAM.
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.azure_audit_emitter")


# The XSIAM dataset the Azure Activity Log control-plane source normalises to.
_DATASET = "msft_azure_audit"

# Synthetic, non-resolving identifiers. All GUIDs are fixed canary values and
# the caller IP is from the RFC 5737 documentation range (TEST-NET-3) so
# nothing here touches a real tenant or resolves on a real network.
_CANARY_TENANT = "11111111-1111-1111-1111-111111111111"
_UNKNOWN_TENANT = "99999999-9999-9999-9999-999999999999"
_CANARY_SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
_CANARY_APP_ID = "22222222-2222-2222-2222-222222222222"
_CANARY_SPN_OID = "33333333-3333-3333-3333-333333333333"
_CANARY_RG = "cortexsim-canary-rg"
_ANOMALOUS_IP = "203.0.113.201"


def _guid() -> str:
    """Return a GUID-shaped synthetic correlation id."""
    h = secrets.token_hex(16)
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# --------------------------------------------------------------------------
# Per-pattern control-plane operation spec
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _AzureOp:
    """One Azure Activity Log control-plane operation."""
    operation_name: str          # e.g. "MICROSOFT.KEYVAULT/VAULTS/WRITE"
    category: str                # "Administrative" | "Policy" | ...
    result_type: str             # "Success" | "Failure" | "Start"
    idtyp: str = "app"           # "app" => service-principal (app-only) token
    tenant_id: str = _CANARY_TENANT


# --------------------------------------------------------------------------
# Azure Activity Log record builder (Event Hub streaming shape)
# --------------------------------------------------------------------------


def _azure_activity_event(
    *,
    op: _AzureOp,
    marker: str,
    sim_run_id: str,
    resource_name: str = _CANARY_RG,
    caller_ip: str = _ANOMALOUS_IP,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape an Azure Activity Log control-plane record (subset of the Azure
    Monitor / Event Hub streaming ``records[]`` schema)."""
    now = datetime.now(timezone.utc).isoformat()
    succeeded = op.result_type == "Success"
    resource_id = (
        f"/SUBSCRIPTIONS/{_CANARY_SUBSCRIPTION}"
        f"/RESOURCEGROUPS/{resource_name.upper()}"
    )
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "time": now,
        "resourceId": resource_id,
        "operationName": op.operation_name,
        "category": op.category,
        "resultType": op.result_type,
        "resultSignature": "Succeeded." if succeeded else "Failed.",
        "durationMs": 0,
        "callerIpAddress": caller_ip,
        "correlationId": _guid(),
        "identity": {
            "claims": {
                "appid": _CANARY_APP_ID,
                # app-only token indicator: idtyp "app" + appidacr "2"
                # (client secret / certificate) is what marks a Service
                # Principal token as opposed to a delegated user token.
                "idtyp": op.idtyp,
                "appidacr": "2" if op.idtyp == "app" else "0",
                "http://schemas.microsoft.com/identity/claims/tenantid": op.tenant_id,
                "http://schemas.microsoft.com/identity/claims/objectidentifier": _CANARY_SPN_OID,
            },
        },
        "level": "Information" if succeeded else "Warning",
        "location": "global",
        "tenantId": _CANARY_TENANT,
        "properties": {
            "eventCategory": op.category,
            "entity": resource_id,
            "message": op.operation_name,
        },
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


# --------------------------------------------------------------------------
# Event patterns — the control-plane activity each preset emits + its marker
# --------------------------------------------------------------------------


# Each pattern -> (marker, [operations...]). Multi-op patterns emit one record
# per operation; ``unusual_app_resource_access`` additionally fans out
# ``burst_count`` resource reads (handled in build_events).
_PATTERN_ACTIVITY: dict[str, tuple[str, list[_AzureOp]]] = {
    "service_principal_token_remote_use": (
        "sp_token_remote_use_marker",
        [
            _AzureOp(
                operation_name="MICROSOFT.RESOURCES/SUBSCRIPTIONS/RESOURCEGROUPS/READ",
                category="Administrative",
                result_type="Success",
                idtyp="app",
            ),
            _AzureOp(
                operation_name="MICROSOFT.STORAGE/STORAGEACCOUNTS/LISTKEYS/ACTION",
                category="Administrative",
                result_type="Success",
                idtyp="app",
            ),
        ],
    ),
    "unknown_tenant_app_access": (
        "unknown_tenant_access_marker",
        [
            _AzureOp(
                operation_name="MICROSOFT.RESOURCES/SUBSCRIPTIONS/RESOURCES/READ",
                category="Administrative",
                result_type="Failure",
                idtyp="app",
                tenant_id=_UNKNOWN_TENANT,
            ),
        ],
    ),
    "unusual_app_resource_access": (
        "unusual_app_resource_access_marker",
        [
            _AzureOp(
                operation_name="MICROSOFT.STORAGE/STORAGEACCOUNTS/LISTKEYS/ACTION",
                category="Administrative",
                result_type="Success",
                idtyp="app",
            ),
        ],
    ),
    "conditional_access_policy_change": (
        "conditional_access_change_marker",
        [
            _AzureOp(
                operation_name="MICROSOFT.AADIAM/CONDITIONALACCESS/POLICIES/WRITE",
                category="Administrative",
                result_type="Success",
                idtyp="user",
            ),
        ],
    ),
    "key_vault_modified": (
        "key_vault_modified_marker",
        [
            _AzureOp(
                operation_name="MICROSOFT.KEYVAULT/VAULTS/WRITE",
                category="Administrative",
                result_type="Success",
                idtyp="user",
            ),
            _AzureOp(
                operation_name="MICROSOFT.KEYVAULT/VAULTS/ACCESSPOLICIES/WRITE",
                category="Administrative",
                result_type="Success",
                idtyp="user",
            ),
        ],
    ),
    "app_credentials_added": (
        "app_credentials_added_marker",
        [
            _AzureOp(
                operation_name="MICROSOFT.AADIAM/APPLICATIONS/CREDENTIALS/WRITE",
                category="Administrative",
                result_type="Success",
                idtyp="user",
            ),
        ],
    ),
}

_EVENT_PATTERNS = tuple(_PATTERN_ACTIVITY)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only event_pattern.
# --------------------------------------------------------------------------


class AzureAuditEmitterParams(AnalyticsEmitterParams):
    event_pattern: str = Field(
        default="service_principal_token_remote_use",
        description="Analytics alert to exercise: "
                    "service_principal_token_remote_use | unknown_tenant_app_access | "
                    "unusual_app_resource_access | conditional_access_policy_change | "
                    "key_vault_modified | app_credentials_added.",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset the Azure Activity Log source normalises to."""
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


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class AzureAuditEmitter(AnalyticsLogEmitter):
    class Meta:
        name = "azure_audit_emitter"
        version = "1.0.0"
        description = (
            "Emits shape-true Azure Activity Log control-plane audit records "
            "(msft_azure_audit dataset) into an operator-supplied collector so "
            "Cortex XSIAM exercises its Azure Analytics / ABIOC detections "
            "(service-principal token remote use, unknown-tenant application "
            "access, unusual application resource access, conditional access "
            "policy change, Key Vault modification, application credentials "
            "added) without touching a real Azure/Entra tenant."
        )
        # Drawn from the analytics alerts this plugin fires. None are C2-tactic
        # (TA0011) techniques, so live campaigns need only simulation_authorized
        # (not c2_authorized) per the SafetyPolicy classifier.
        mitre_techniques = [
            "T1550.001",  # Application Access Token
            "T1078.004",  # Valid Accounts: Cloud Accounts
            "T1526",      # Cloud Service Discovery
            "T1556.009",  # Modify Authentication Process: Conditional Access Policies
            "T1098",      # Account Manipulation
            "T1098.001",  # Additional Cloud Credentials
            "T1555",      # Credentials from Password Stores
        ]
        eal_targets = [
            "Analytics — remote usage of an Azure Service Principal token",
            "Analytics — Azure application access attempt from an unknown tenant",
            "ABIOC — unusual resource access by an Azure application (behavioural)",
            "Analytics — Azure conditional access policy creation or modification",
            "Analytics — Azure Key Vault modified (write / access-policy change)",
            "Analytics — credentials added to an Azure application (secret / cert)",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "configuration"
        params_model = AzureAuditEmitterParams

    def build_events(
        self, params: AzureAuditEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker, ops = _PATTERN_ACTIVITY[params.event_pattern]

        events: list[dict[str, Any]] = []

        if params.event_pattern == "unusual_app_resource_access":
            # A behavioural burst of resource reads by the same service
            # principal across many distinct resources — the shape the ABIOC
            # "unusual resource access by Azure application" model keys on.
            base_op = ops[0]
            for n in range(params.burst_count):
                events.append(_azure_activity_event(
                    op=base_op,
                    marker=marker,
                    sim_run_id=sim_run_id,
                    resource_name=f"cortexsim-canary-res-{n:02d}",
                    extra={"accessed_resource_index": n},
                ))
            return events

        for op in ops:
            events.append(_azure_activity_event(
                op=op, marker=marker, sim_run_id=sim_run_id,
            ))
        return events

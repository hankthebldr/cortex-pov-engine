"""
ingestion_doors — the ways data reaches a customer's Cortex tenant.

A *door* is one onboarding path: an XDR agent, an NGFW forwarding logs, a cloud
account onboarded, a SaaS connector. A detection can only fire if the door its
data arrives through is open, so later layers join detections to doors and
record each door's state. This module is only the registry; it holds no state.

Three scopes decide what a door's state is keyed on:

  * ``tenant`` — one state for the whole tenant (an onboarded cloud account).
  * ``agent``  — one state per enrolled beacon/endpoint.
  * ``target`` — one state per configured collector target.

Rules that bind this file, as in ``eal_simulator.ingestion_routes``:

  * **A typo must raise, not vanish.** An unknown door id raises
    ``UnknownDoorError``; a malformed entry raises at import.
  * **A dataset name is copied from the vendor, never invented.** An empty
    ``verify_datasets`` means the door's dataset is not fixed (a target's own
    ``dataset``) or there is none to read; it is not a claim the door is open.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

#: What a door's state is keyed on.
SCOPES = ("tenant", "agent", "target")

#: Doors that share the ``cloud_audit_logs`` dataset and are told apart by the
#: provider filter on its rows.
CLOUD_PROVIDER_DOORS: frozenset[str] = frozenset(
    {"cloud_aws", "cloud_gcp", "cloud_azure"}
)

_CORTEX_DOCS = "https://cortex-docs.paloaltonetworks.com/"
_DS = _CORTEX_DOCS + "cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/"
_VENDOR = _DS + "vendor-specific-data-sources-and-connectors/"
_NGFW_INGEST = (
    _DS + "palo-alto-networks-integrations/next-generation-firewall/"
    "ingest-data-from-next-generation-firewall"
)
_HTTP_COLLECTOR = (
    _VENDOR + "http-log-collector/set-up-an-http-log-collector-to-receive-logs"
)
_OKTA_INGEST = _VENDOR + "okta/ingest-logs-and-data-from-okta"
_XDR_LINUX = (
    _CORTEX_DOCS + "cortex-xdr-agent/9.1/cortex-xdr-agent-for-linux/"
    "cortex-xdr-agent-for-linux-requirements.md"
)
_AWS_AUDIT = (
    _CORTEX_DOCS + "cortex-cloud-runtime-security/onboard-and-configure/"
    "deployment-steps-and-checklist/cloud-service-provider-csp-onboarding/"
    "amazon-web-services-cloud-onboarding/"
    "cortex-cloud-and-aws-audit-log-collection-architecture"
)
_GCP_ONBOARD = (
    _CORTEX_DOCS + "cortex-cloud-runtime-security/onboard-and-configure/"
    "deployment-steps-and-checklist/cloud-service-provider-csp-onboarding/"
    "google-cloud-platform-cloud-onboarding/how-to-onboard-google-cloud-platform"
)
_AZURE_EVENT_HUB = (
    "https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/"
    "Cortex-XDR-5.x-Documentation/Ingest-logs-from-Microsoft-Azure-Event-Hub"
)
_PACKS = "https://github.com/demisto/content/tree/master/Packs/"
_PROOFPOINT = "https://cortex.marketplace.pan.dev/marketplace/details/ProofpointTAP/"
_PRISMA_BROWSER = (
    _DS + "palo-alto-networks-integrations/prisma-access-browser/"
    "ingest-logs-from-prisma-access-browser.md"
)
_XPANSE = (
    _CORTEX_DOCS + "cortex-xsiam/detect-investigate-and-respond-to-threats/"
    "attack-surface-management.md"
)
_TIM = (
    _CORTEX_DOCS + "cortex-xsiam/detect-investigate-and-respond-to-threats/"
    "threat-management/threat-intel-management.md"
)
_AIRS = (
    "https://docs.paloaltonetworks.com/prisma-airs/getting-started/"
    "ai-runtime-security-overview"
)
_KOI = _VENDOR + "koi/koi.md"
_DATASETS = (
    _CORTEX_DOCS + "cortex-xsiam/reference-and-developer-docs/cortex-agentix-xql/"
    "get-started-with-xql/datasets-and-presets"
)


@dataclasses.dataclass(frozen=True)
class Door:
    """One way data reaches a tenant."""

    id: str
    label: str
    scope: str
    verify_datasets: tuple[str, ...] = ()
    provider_filter: Optional[str] = None
    needs_attestation_of: Optional[str] = None
    evidence: str = ""
    wizard: Optional[str] = None


DOORS: tuple[Door, ...] = (
    Door("xdr_agent", "XDR agent", "agent", ("xdr_data",), evidence=_XDR_LINUX),
    Door("xdr_agent_xth", "XDR agent with threat hunting", "agent", ("xdr_data",),
         needs_attestation_of="xth", evidence=_XDR_LINUX),
    Door("ngfw_traffic", "NGFW traffic logs", "tenant", ("panw_ngfw_traffic_raw",),
         evidence=_NGFW_INGEST, wizard="firewall"),
    Door("ngfw_threat", "NGFW threat logs", "tenant", ("panw_ngfw_threat_raw",),
         evidence=_NGFW_INGEST, wizard="firewall"),
    Door("ngfw_url", "NGFW URL logs", "tenant", ("panw_ngfw_url_raw",),
         evidence=_NGFW_INGEST, wizard="firewall"),
    Door("ngfw_globalprotect", "NGFW GlobalProtect logs", "tenant",
         ("panw_ngfw_globalprotect_raw",), evidence=_NGFW_INGEST, wizard="firewall"),
    Door("ngfw_stitch", "NGFW and endpoint stitching", "agent", ("network_story",),
         evidence=_NGFW_INGEST, wizard="firewall"),
    Door("cloud_aws", "AWS audit logs", "tenant", ("cloud_audit_logs",),
         provider_filter="AWS", evidence=_AWS_AUDIT),
    Door("cloud_gcp", "GCP audit logs", "tenant", ("cloud_audit_logs",),
         provider_filter="GCP", evidence=_GCP_ONBOARD),
    Door("cloud_azure", "Azure audit logs", "tenant", ("cloud_audit_logs",),
         provider_filter="AZURE", evidence=_AZURE_EVENT_HUB),
    Door("saas_okta", "Okta", "tenant", ("okta_sso_raw",), evidence=_OKTA_INGEST),
    Door("saas_entra", "Microsoft Entra ID", "tenant", ("msft_azure_ad_raw",),
         evidence=_PACKS + "AzureActiveDirectory"),
    Door("saas_o365", "Microsoft 365", "tenant",
         ("msft_o365_exchange_online_raw", "msft_o365_sharepoint_online_raw"),
         evidence=_PACKS + "Office365"),
    Door("saas_google_workspace", "Google Workspace", "tenant",
         ("google_workspace_login_raw", "google_workspace_admin_console_raw"),
         evidence=_PACKS + "GoogleWorkspace"),
    Door("windows_events", "Windows events", "tenant", ("microsoft_windows_raw",),
         evidence=_PACKS + "MicrosoftWindowsEvents"),
    Door("syslog_cisco_asa", "Cisco ASA syslog", "tenant", ("cisco_asa_raw",),
         evidence=_PACKS + "CiscoASA"),
    Door("syslog_vmware_esxi", "VMware ESXi syslog", "tenant", ("vmware_esxi_raw",),
         evidence=_PACKS + "VMwareESXi"),
    Door("syslog_ivanti_connect_secure", "Ivanti Connect Secure syslog", "tenant",
         ("ivanti_connect_secure_raw",), evidence=_PACKS + "IvantiConnectSecure"),
    Door("syslog_kubernetes_kubernetes", "Kubernetes syslog", "tenant",
         ("kubernetes_kubernetes_raw",), evidence=_PACKS + "Kubernetes"),
    Door("http_collector", "HTTP log collector", "target", (),
         evidence=_HTTP_COLLECTOR, wizard="tenant"),
    Door("proofpoint_tap", "Proofpoint TAP", "tenant", ("proofpoint_tap_raw",),
         evidence=_PROOFPOINT),
    Door("prisma_browser", "Prisma Access Browser", "tenant",
         ("panw_prisma_access_browser_raw",), evidence=_PRISMA_BROWSER),
    Door("xpanse", "Cortex Xpanse", "tenant", (), evidence=_XPANSE),
    Door("tim_feed", "Threat intel feed", "tenant", ("indicators",), evidence=_TIM),
    Door("prisma_airs", "Prisma AIRS", "tenant", ("panw_prisma_airs_raw",),
         evidence=_AIRS),
    Door("koi_aes", "Koi agentic endpoint", "tenant", ("koi_koi_raw",),
         evidence=_KOI),
    Door("tenant_incidents", "Tenant incidents", "tenant", ("xsiam_incidents",),
         evidence=_DATASETS),
)


class UnknownDoorError(ValueError):
    """A door id that is not in the registry."""


def get_door(door_id: str) -> Door:
    for door in DOORS:
        if door.id == door_id:
            return door
    raise UnknownDoorError(
        f"unknown door {door_id!r}; expected one of {sorted(d.id for d in DOORS)}"
    )


def door_key(door_id: str, scope_value: Optional[str]) -> str:
    """The state key for a door: its id, plus the agent or target it is scoped to."""
    door = get_door(door_id)
    if door.scope == "tenant":
        return door.id
    if not scope_value:
        raise ValueError(
            f"door {door.id!r} has {door.scope} scope and needs a {door.scope} id"
        )
    return f"{door.id}:{scope_value}"


def _validate() -> None:
    seen: set[str] = set()
    for d in DOORS:
        where = f"DOORS[{d.id!r}]"
        if not d.id or d.id in seen:
            raise ValueError(f"{where}: door ids must be non-empty and unique")
        seen.add(d.id)
        if d.scope not in SCOPES:
            raise ValueError(f"{where}: scope {d.scope!r} not in {SCOPES}")
        if not isinstance(d.verify_datasets, tuple) or not all(
            isinstance(x, str) and x for x in d.verify_datasets
        ):
            raise ValueError(f"{where}: verify_datasets must be a tuple of names")
        if (d.provider_filter is not None) != (d.id in CLOUD_PROVIDER_DOORS):
            raise ValueError(
                f"{where}: a provider filter belongs on the cloud doors, and only there"
            )
        if not d.evidence.startswith("https://"):
            raise ValueError(f"{where}: evidence must be an https URL")
    if not CLOUD_PROVIDER_DOORS <= seen:
        raise ValueError(f"CLOUD_PROVIDER_DOORS names unknown doors: "
                         f"{sorted(CLOUD_PROVIDER_DOORS - seen)}")


_validate()

"""
ingestion_routes — how each analytics data source actually reaches a tenant,
and whether built-in analytics can run on the data once it lands.

Why this module exists
----------------------
``analytics_catalogue`` says *which* sources Cortex Analytics consumes, and
``analytics_alerts`` says *how long* a detector must train. Neither says whether
a record can reach the detector at all, and the vendor documents that it often
cannot:

  * "Analytics, IOC, and BIOC issues are only generated on normalized logs."
    (Ingest data from Next-Generation Firewall, fetched 2026-11-10.)
  * A custom HTTP log collector writes ``<Vendor>_<Product>_raw``, which an XDM
    modeling rule must map before Analytics can use it. (Set up an HTTP log
    collector; Create data model rules. Quoted in the 2026-11-10 tenant-setup
    research.)
  * On the Cribl receiver, "out-of-the-box (OOTB) analytics are only available
    for data sources using dedicated UUIDs" — never the generic UUID.
    (Data source UUIDs, fetched 2026-11-10.)

Every CortexSim log streamer delivers through a custom HTTP collector. On a
soakless tenant, where no training period stands in the way, the delivery route
is the only thing between a fired threat and a raised alert. A route that can
never feed analytics must therefore read ``not_armable``, never ``armed``.

Facts here come from LogSimv2 (github.com/phreezone/LogSimv2), re-checked
against the vendor's public pages and content packs. No LogSimv2 code is copied:
it ships no licence. A fact that only LogSimv2 states is marked ``reported``, so
it is never quoted as a vendor statement.

Four rules bind this file:

  * **Two evidence fields, never one.** ``dataset_evidence`` says how we know
    the tenant creates that dataset; ``analytics_evidence`` says how we know
    whether analytics runs on it. A documented dataset with an inferred
    analytics claim is common, and collapsing the two would quote the inference
    as fact.
  * **A typo must raise, not vanish.** An unknown source key, pipe, evidence
    value or a malformed Cribl UUID raises at import.
  * **An unlisted route reads as no route.** A source with no documented route
    has an empty tuple, and the coverage report shows that empty tuple.
  * **Ingestion-only is not coverage.** Proofpoint, Infoblox, Apache and ESXi
    have no published analytics, so they sit outside the 34-source coverage
    figure. Correlation rules and XQL are the only things that can read them.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Optional
from urllib.parse import urlparse

from .analytics_catalogue import DATA_SOURCE_CATALOGUE, get_source


# ---------------------------------------------------------------------------
# Vocabulary.
# ---------------------------------------------------------------------------

#: The ways a record can enter a tenant. A route names exactly one.
PIPES: dict[str, str] = {
    "cribl_dedicated": "Cribl receiver under the source's dedicated catalog UUID",
    "cribl_generic": "Cribl receiver under the generic UUID, with Vendor and Product headers",
    "http_collector": "Tenant HTTP log collector (custom dataset from its Vendor and Product)",
    "broker_syslog": "Broker VM Syslog applet, parsed and modelled by the vendor's content pack",
    "broker_wec": "Broker VM Windows Event Collector",
    "native_integration": "The product's native Cortex integration",
}

#: Whether built-in (out-of-the-box) analytics run on data arriving by a route.
#: ``requires_xdm`` means only after an XDM modeling rule maps the dataset — a
#: tenant fact CortexSim cannot see, so it is never assumed.
OOTB_VALUES = ("yes", "no", "requires_xdm")

#: ``documented``: read on ``doc_url`` (a vendor page or the vendor's own
#: content pack). ``inferred``: reasoned from documented facts, with the
#: reasoning in ``note``. ``reported``: stated by LogSimv2 and not re-checked
#: against a vendor source.
EVIDENCE_VALUES = ("documented", "inferred", "reported")

_UUID_RE = re.compile(r"^[0-9a-f]{32}$")

TRANSCRIBED_ON = "2026-11-10"

_CRIBL_UUIDS = (
    "https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/"
    "cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/cribl/"
    "ingest-data-from-cribl/data-souce-uuids"
)
_NGFW_INGEST = (
    "https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/"
    "cortex-xsiam-data-sources/palo-alto-networks-integrations/next-generation-firewall/"
    "ingest-data-from-next-generation-firewall"
)
_HTTP_COLLECTOR = (
    "https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/"
    "cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/"
    "http-log-collector/set-up-an-http-log-collector-to-receive-logs"
)
_OKTA_INGEST = (
    "https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/"
    "cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/okta/"
    "ingest-logs-and-data-from-okta"
)
_ALERTS_BY_SOURCE = (
    "https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-data-source"
)
_PACKS = "https://github.com/demisto/content/tree/master/Packs"
_LOGSIM_TRANSPORTS = (
    "https://github.com/phreezone/LogSimv2/blob/89dcaac/docs/transports.md"
)

# The analytics claim most native routes rest on: the vendor lists the source
# as an analytics data source, and this route is how the source is onboarded.
_NATIVE_ANALYTICS_NOTE = (
    "The vendor lists this source as an analytics data source and this route is "
    "how it is onboarded; no page states that analytics run on this exact route."
)


# ---------------------------------------------------------------------------
# The route model.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class IngestionRoute:
    """One documented path into a tenant dataset."""

    pipe: str
    tenant_dataset: str
    ootb_analytics: str
    dataset_evidence: str
    analytics_evidence: str
    doc_url: str
    cribl_source_uuid: Optional[str] = None
    vendor: Optional[str] = None
    product: Optional[str] = None
    note: str = ""
    transcribed_on: str = TRANSCRIBED_ON

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["pipe_label"] = PIPES[self.pipe]
        return d


def _cribl(uuid: str, dataset: str, *, note: str = "") -> IngestionRoute:
    return IngestionRoute(
        pipe="cribl_dedicated", tenant_dataset=dataset, ootb_analytics="yes",
        dataset_evidence="documented", analytics_evidence="documented",
        doc_url=_CRIBL_UUIDS, cribl_source_uuid=uuid, note=note,
    )


def _ngfw(dataset: str) -> IngestionRoute:
    return IngestionRoute(
        pipe="native_integration", tenant_dataset=dataset, ootb_analytics="yes",
        dataset_evidence="documented", analytics_evidence="documented",
        doc_url=_NGFW_INGEST,
        note="Native NGFW logs are normalised; Analytics runs only on normalised logs.",
    )


def _syslog(vendor: str, product: str, dataset: str, *, dataset_evidence: str,
            doc_url: str, analytics: str = "yes", analytics_evidence: str = "inferred",
            note: str = _NATIVE_ANALYTICS_NOTE) -> IngestionRoute:
    return IngestionRoute(
        pipe="broker_syslog", tenant_dataset=dataset, ootb_analytics=analytics,
        dataset_evidence=dataset_evidence, analytics_evidence=analytics_evidence,
        doc_url=doc_url, vendor=vendor, product=product, note=note,
    )


#: Routes any source can take. They are not listed per source: they apply to
#: every one, and both produce a custom dataset rather than the source's own.
GENERIC_ROUTES: dict[str, IngestionRoute] = {
    "http_collector": IngestionRoute(
        pipe="http_collector", tenant_dataset="<vendor>_<product>_raw",
        ootb_analytics="requires_xdm", dataset_evidence="documented",
        analytics_evidence="documented", doc_url=_HTTP_COLLECTOR,
        note=(
            "The dataset is named from the collector's Vendor and Product, and the "
            "record body's dataset field routes nothing. Analytics runs only on "
            "normalised data, which a custom-collector dataset becomes only once an "
            "XDM modeling rule maps it."
        ),
    ),
    "cribl_generic": IngestionRoute(
        pipe="cribl_generic", tenant_dataset="<vendor>_<product>_raw",
        ootb_analytics="no", dataset_evidence="documented",
        analytics_evidence="documented", doc_url=_CRIBL_UUIDS,
        cribl_source_uuid="af01292940d7426594d3d3e55ae17ee0",
        note=(
            "Parsing and modeling rules apply, but out-of-the-box analytics are only "
            "available for sources using dedicated UUIDs."
        ),
    ),
}


#: Source-specific routes, keyed by catalogue source. An empty tuple means no
#: route is documented — shown as such, never omitted.
ROUTES: dict[str, tuple[IngestionRoute, ...]] = {
    "aws_audit_log": (
        _cribl("c19f87b6262f48259b3d5d2a2c691802", "amazon_aws_raw"),
        IngestionRoute(
            pipe="native_integration", tenant_dataset="cloud_audit_logs",
            ootb_analytics="yes", dataset_evidence="inferred",
            analytics_evidence="inferred", doc_url=_ALERTS_BY_SOURCE,
            note=(
                "cloud_audit_logs is filled by platform normalisation of onboarded "
                "cloud audit data; a POST cannot write it. " + _NATIVE_ANALYTICS_NOTE
            ),
        ),
    ),
    "azure_audit_log": (
        _cribl("fce13a1d51294f84bae4a37851503060", "msft_azure_raw"),
    ),
    "azure_signin_log": (
        _cribl("f56dcfdf6bca43e793a4b6e9290e7b12", "msft_azure_ad_raw"),
    ),
    "azuread": (
        _cribl("c00d6d52e5b141a8baa8db9d9345423d", "msft_azure_ad_raw"),
    ),
    "azuread_audit_log": (
        _cribl("0e076d5abe864bf78e8145ea9e0d749e", "msft_azure_ad_audit_raw"),
    ),
    "box_audit_log": (
        _cribl("3ef05d14ae9349f8bbd48c8a4797334a", "box_admin_logs_raw"),
    ),
    "dropbox": (
        _cribl("a6322b2fd9e545e0a4223ba754c48fb9", "dropbox_events_raw"),
    ),
    "duo": (),
    "gcp_audit_log": (
        _cribl("00a8322c85e14beabfa7ad5f3d62db73", "google_cloud_logging_raw"),
    ),
    "google_workspace_audit_logs": (),
    "google_workspace_authentication": (),
    "health_monitoring_data": (),
    "idira": (),
    "kubernetes_audit_logs": (
        _cribl(
            "fb8a9d4922cb4095b76d71e921d2d999", "amazon_eks_raw",
            note=(
                "EKS control-plane logs via CloudWatch. That the Kubernetes Audit "
                "Logs analytics source reads this dataset is inferred, not stated."
            ),
        ),
    ),
    "microsoft_365_emails": (
        _cribl(
            "9855a03559ce4263b568671e695d1fa8", "msft_o365_mailboxes_raw",
            note=(
                "Which Microsoft 365 email dataset the analytics source reads is not "
                "stated; the 2026-11-10 tenant-setup research names msft_o365_emails_raw."
            ),
        ),
    ),
    "microsoft_graph_logs": (),
    "office_365_audit": (
        _cribl("e1f109f886ea42fbb96be6ec0cc597a9", "msft_o365_azure_ad_raw"),
        _cribl("dee8e85ce7db4573a8bc21b807e1d73a", "msft_o365_exchange_online_raw"),
        _cribl("3a37f519e9094a3f8c4185fa572cd111", "msft_o365_sharepoint_online_raw"),
        _cribl("c7655e83805b4a058e66043a6715156c", "msft_o365_general_raw"),
        _cribl("8f052782739d4b8389644cca23b994ac", "msft_o365_dlp_raw"),
    ),
    "okta": (
        _cribl("5faf4c1fdb8443d9920d6a54815432c1", "okta_sso_raw"),
        IngestionRoute(
            pipe="native_integration", tenant_dataset="okta_sso_raw",
            ootb_analytics="yes", dataset_evidence="documented",
            analytics_evidence="inferred", doc_url=_OKTA_INGEST,
            note=_NATIVE_ANALYTICS_NOTE,
        ),
    ),
    "okta_audit_log": (),
    "onelogin": (
        _cribl("22b23a3f9f1e49998645b683d5dc3a6f", "onelogin_events_raw"),
    ),
    "pan_firewall_eal_logs": (),
    "pan_firewall_threat_logs": (_ngfw("panw_ngfw_threat_raw"),),
    "pan_firewall_traffic_logs": (_ngfw("panw_ngfw_traffic_raw"),),
    "pan_global_protect": (_ngfw("panw_ngfw_globalprotect_raw"),),
    "pan_platform_alerts": (),
    "pan_url_logs": (_ngfw("panw_ngfw_url_raw"),),
    "pingone": (
        _cribl("924951a8394b4605b1725f943292ab4f", "pingid_pingone_raw"),
    ),
    "third_party_alerts": (),
    "third_party_firewalls": (
        _syslog("Cisco", "ASA", "cisco_asa_raw", dataset_evidence="documented",
                doc_url=f"{_PACKS}/CiscoASA"),
        _syslog("Cisco", "Firepower", "cisco_firepower_raw",
                dataset_evidence="reported", doc_url=_LOGSIM_TRANSPORTS),
        _syslog("Fortinet", "Fortigate", "fortinet_fortigate_raw",
                dataset_evidence="reported", doc_url=_LOGSIM_TRANSPORTS),
    ),
    "third_party_vpns": (
        _syslog("Cisco", "ASA", "cisco_asa_raw", dataset_evidence="documented",
                doc_url=f"{_PACKS}/CiscoASA",
                note=("ASA VPN syslog lands in the firewall's own dataset. "
                      + _NATIVE_ANALYTICS_NOTE)),
        _syslog("Fortinet", "Fortigate", "fortinet_fortigate_raw",
                dataset_evidence="reported", doc_url=_LOGSIM_TRANSPORTS),
    ),
    "unspecified": (),
    "windows_event_collector": (
        IngestionRoute(
            pipe="broker_wec", tenant_dataset="microsoft_windows_raw",
            ootb_analytics="yes", dataset_evidence="documented",
            analytics_evidence="inferred", doc_url=f"{_PACKS}/MicrosoftWindowsEvents",
            vendor="microsoft", product="windows", note=_NATIVE_ANALYTICS_NOTE,
        ),
        _cribl("63b0fbeb501e4650896e7064d3412e14", "microsoft_windows_raw"),
    ),
    "xdr_agent": (),
    "xdr_agent_xth": (),
}


# ---------------------------------------------------------------------------
# Ingestion-only sources — real data, no published analytics.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class IngestionOnlySource:
    """A source LogSimv2 feeds that the vendor's analytics list does not carry."""

    key: str
    name: str
    category: str
    routes: tuple[IngestionRoute, ...]
    note: str = (
        "Not in the vendor's analytics data-source list, so no built-in analytics "
        "detector reads it. Correlation rules and XQL are the only readers."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "routes": [r.to_dict() for r in self.routes],
            "note": self.note,
        }


_NO_ANALYTICS = (
    "The vendor publishes no analytics for this source (absent from the "
    "alerts-by-data-source list); only correlation rules and XQL read it."
)

INGESTION_ONLY_SOURCES: tuple[IngestionOnlySource, ...] = (
    IngestionOnlySource(
        "proofpoint_tap", "Proofpoint TAP", "Email",
        routes=(
            IngestionRoute(
                pipe="cribl_dedicated", tenant_dataset="proofpoint_tap_raw",
                ootb_analytics="no", dataset_evidence="documented",
                analytics_evidence="documented", doc_url=_CRIBL_UUIDS,
                cribl_source_uuid="3eefce0f791e4391a3643b8cf860a361",
                vendor="Proofpoint", product="Tap", note=_NO_ANALYTICS,
            ),
        ),
    ),
    IngestionOnlySource(
        "infoblox_nios", "Infoblox NIOS", "DNS & DHCP",
        routes=(
            _syslog("infoblox", "infoblox", "infoblox_infoblox_raw",
                    dataset_evidence="reported", doc_url=_LOGSIM_TRANSPORTS,
                    analytics="no", analytics_evidence="documented",
                    note=_NO_ANALYTICS),
        ),
    ),
    IngestionOnlySource(
        "apache_httpd", "Apache httpd", "Web server",
        routes=(
            _syslog("apache", "httpd", "apache_httpd_raw",
                    dataset_evidence="reported", doc_url=_LOGSIM_TRANSPORTS,
                    analytics="no", analytics_evidence="documented",
                    note=_NO_ANALYTICS),
        ),
    ),
    IngestionOnlySource(
        "vmware_esxi", "VMware ESXi", "Virtualisation",
        routes=(
            _syslog("VMware", "ESXi", "vmware_esxi_raw",
                    dataset_evidence="documented", doc_url=f"{_PACKS}/VMwareESXi",
                    analytics="no", analytics_evidence="documented",
                    note=_NO_ANALYTICS),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Lookups.
# ---------------------------------------------------------------------------


def routes_for(key: str) -> tuple[IngestionRoute, ...]:
    """Source-specific routes for a catalogue source. Raises on an unknown key."""
    get_source(key)
    return ROUTES[key]


def tenant_datasets(key: str) -> set[str]:
    """Datasets the tenant creates for ``key`` on a source-specific route."""
    return {r.tenant_dataset for r in routes_for(key)}


def resolve_route(key: str, pipe: str) -> Optional[IngestionRoute]:
    """The route a record on ``pipe`` takes into source ``key``, or ``None``.

    A source-specific route wins; otherwise the generic route for that pipe.
    ``None`` means this pipe has no documented way into this source.
    """
    if pipe not in PIPES:
        raise ValueError(f"unknown pipe {pipe!r}; expected one of {sorted(PIPES)}")
    for route in routes_for(key):
        if route.pipe == pipe:
            return route
    return GENERIC_ROUTES.get(pipe)


#: What a campaign step's collector URL says about its route. Only the first two
#: are pipes; the others say the route cannot be read from the URL.
COLLECTOR_HTTP = "http_collector"
COLLECTOR_CRIBL = "cribl_receiver"
COLLECTOR_UNKNOWN = "unknown_forwarder"
COLLECTOR_UNDECLARED = "undeclared"


def pipe_for_collector_url(url: Optional[str]) -> str:
    """Classify a campaign step's ``collector_url`` by the tenant door it hits.

    ``/logs/v1/event`` is the tenant HTTP log collector. ``/logs/v1/integrated/
    cribl/data`` is the Cribl receiver, whose dedicated-vs-generic UUID is
    carried in a header, not the URL, so it cannot be resolved here. Anything
    else is a forwarder whose onward route is unknown. Never guesses upward.
    """
    if not url or not isinstance(url, str):
        return COLLECTOR_UNDECLARED
    path = (urlparse(url).path or "").rstrip("/")
    if path.endswith("/logs/v1/event"):
        return COLLECTOR_HTTP
    if path.endswith("/logs/v1/integrated/cribl/data"):
        return COLLECTOR_CRIBL
    return COLLECTOR_UNKNOWN


# ---------------------------------------------------------------------------
# Import-time validation — a typo must raise, not vanish.
# ---------------------------------------------------------------------------


def _validate_route(route: IngestionRoute, where: str) -> None:
    if route.pipe not in PIPES:
        raise ValueError(f"{where}: unknown pipe {route.pipe!r}")
    if route.ootb_analytics not in OOTB_VALUES:
        raise ValueError(f"{where}: ootb_analytics {route.ootb_analytics!r} not in {OOTB_VALUES}")
    for field in ("dataset_evidence", "analytics_evidence"):
        value = getattr(route, field)
        if value not in EVIDENCE_VALUES:
            raise ValueError(f"{where}: {field} {value!r} not in {EVIDENCE_VALUES}")
    if route.cribl_source_uuid is not None and not _UUID_RE.match(route.cribl_source_uuid):
        raise ValueError(f"{where}: malformed Cribl source UUID {route.cribl_source_uuid!r}")
    if route.pipe in ("cribl_dedicated", "cribl_generic") and route.cribl_source_uuid is None:
        raise ValueError(f"{where}: a Cribl route must carry its source UUID")
    if not route.doc_url.startswith("https://"):
        raise ValueError(f"{where}: doc_url must be an https URL")
    if route.analytics_evidence == "inferred" and not route.note:
        raise ValueError(f"{where}: an inferred analytics claim must say why in note")


def _validate() -> None:
    catalogue_keys = {s.key for s in DATA_SOURCE_CATALOGUE}
    missing = catalogue_keys - set(ROUTES)
    extra = set(ROUTES) - catalogue_keys
    if missing or extra:
        raise ValueError(
            f"ROUTES must list every catalogue source exactly once "
            f"(missing={sorted(missing)}, unknown={sorted(extra)})"
        )
    for key, routes in ROUTES.items():
        for i, route in enumerate(routes):
            _validate_route(route, f"ROUTES[{key!r}][{i}]")
    for pipe, route in GENERIC_ROUTES.items():
        if route.pipe != pipe:
            raise ValueError(f"GENERIC_ROUTES[{pipe!r}] carries pipe {route.pipe!r}")
        _validate_route(route, f"GENERIC_ROUTES[{pipe!r}]")
    only_keys = [s.key for s in INGESTION_ONLY_SOURCES]
    if len(only_keys) != len(set(only_keys)):
        raise ValueError("INGESTION_ONLY_SOURCES keys must be unique")
    if set(only_keys) & catalogue_keys:
        raise ValueError(
            "an ingestion-only source is also an analytics catalogue source: "
            f"{sorted(set(only_keys) & catalogue_keys)}"
        )
    for src in INGESTION_ONLY_SOURCES:
        if not src.routes:
            raise ValueError(f"ingestion-only source {src.key!r} lists no route")
        for i, route in enumerate(src.routes):
            _validate_route(route, f"INGESTION_ONLY_SOURCES[{src.key!r}][{i}]")


_validate()

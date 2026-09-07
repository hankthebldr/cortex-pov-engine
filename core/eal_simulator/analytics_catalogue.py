"""
analytics_catalogue — the vendor data-source catalogue and the coverage
reporter for the analytics log-streamer EAL family.

The point of this module, and the reason it is a named deliverable, is stated
in the design brief:

    Coverage must be reported against the vendor catalogue, not against our own
    plugin count — otherwise the number grows while the gap stays.

So the source of truth here is **the vendor's documented list of analytics data
sources** (34 of them, transcribed from
<https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-data-source>
as of 2026-09-04), NOT the set of emitters we happen to have written. The
coverage report walks that catalogue and, for every source, states whether an
emitter covers it — including the sources with **no** emitter, which are
rendered as gaps rather than omitted. An unlisted gap reads as no gap.

Three honesty rules bind this file, all from CLAUDE.md / the brief:

  * **Authored is not proven.** An emitter existing for a source makes that
    source ``authored``; it does NOT make it ``proven``. ``proven`` is only ever
    true once a detector has been observed firing against a live tenant, and
    ``tenant-verified`` is 0, so ``proven`` is 0 here, unconditionally. The two
    are separate fields and must never be reported as one number.
  * **A gap is degraded, not ok.** A source with no emitter is ``gap``; the
    aggregate never collapses "no emitter" into the covered count.
  * **A typo must raise, not vanish.** An emitter that declares a data-source
    key not in this catalogue raises ``UnknownDataSourceError`` — the tolerant
    alternative (dropping the unknown key) would hide the emitter from coverage
    while greening the report.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional


# ---------------------------------------------------------------------------
# The catalogue.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class AnalyticsSource:
    """One row of the vendor's analytics data-source catalogue.

    ``key`` is our stable snake_case identifier (what an emitter's
    ``Meta.data_sources`` references); ``name`` is the vendor's own display
    string. ``dataset`` is the canonical XSIAM dataset the source normalises to
    (``None`` for the non-addressable catch-alls). ``addressable`` is ``False``
    for the sources a log-streamer POST cannot and should not produce — the two
    ``XDR Agent`` endpoint sources (owned by the beacon / identity harness, a
    stated non-goal) and the ``Unspecified`` catch-all bucket.
    """

    key: str
    name: str
    category: str
    dataset: Optional[str]
    addressable: bool = True
    note: str = ""


#: The 34 documented analytics data sources, in the vendor page's order.
#: Transcribed 2026-09-04; ``name`` matches the vendor string verbatim so a
#: DC can cross-reference the doc page. When the vendor list changes, THIS is
#: the thing to update — the coverage number is derived from it, never hardcoded.
DATA_SOURCE_CATALOGUE: tuple[AnalyticsSource, ...] = (
    AnalyticsSource("aws_audit_log", "AWS Audit Log", "Cloud Audit", "cloud_audit_logs"),
    AnalyticsSource("azure_audit_log", "Azure Audit Log", "Cloud Audit", "msft_azure_audit"),
    AnalyticsSource("azure_signin_log", "Azure SignIn Log", "Identity & SSO", "msft_azure_ad_signin"),
    AnalyticsSource("azuread", "AzureAD", "Identity & SSO", "msft_azure_ad_signin",
                    note="Distinct catalogue source; only partially reached by the Entra sign-in emitter."),
    AnalyticsSource("azuread_audit_log", "AzureAD Audit Log", "Identity & SSO", "msft_azure_ad_audit"),
    AnalyticsSource("box_audit_log", "Box Audit Log", "SaaS", "box_audit_raw"),
    AnalyticsSource("dropbox", "DropBox", "SaaS", "dropbox_raw"),
    AnalyticsSource("duo", "Duo", "Identity & SSO", "duo_auth_raw"),
    AnalyticsSource("gcp_audit_log", "Gcp Audit Log", "Cloud Audit", "cloud_audit_logs"),
    AnalyticsSource("google_workspace_audit_logs", "Google Workspace Audit Logs", "SaaS", "google_workspace_audit"),
    AnalyticsSource("google_workspace_authentication", "Google Workspace Authentication", "Identity & SSO", "google_workspace_audit",
                    note="Distinct catalogue source; only partially reached by the Google Workspace sign-in emitter."),
    AnalyticsSource("health_monitoring_data", "Health Monitoring Data", "Platform & Health", "health_monitoring_raw"),
    AnalyticsSource("idira", "Idira", "Identity & SSO", "idira_raw"),
    AnalyticsSource("kubernetes_audit_logs", "Kubernetes Audit Logs", "Cloud Audit", "kubernetes_audit_logs"),
    AnalyticsSource("microsoft_365_emails", "Microsoft 365 Emails", "SaaS", "msft_o365_email"),
    AnalyticsSource("microsoft_graph_logs", "Microsoft Graph Logs", "Identity & SSO", "msft_graph_raw"),
    AnalyticsSource("office_365_audit", "Office 365 Audit", "SaaS", "msft_o365_audit"),
    AnalyticsSource("okta", "Okta", "Identity & SSO", "okta_sso"),
    AnalyticsSource("okta_audit_log", "Okta Audit Log", "Identity & SSO", "okta_audit_raw"),
    AnalyticsSource("onelogin", "OneLogin", "Identity & SSO", "onelogin_raw"),
    AnalyticsSource("pan_firewall_eal_logs", "Palo Alto Networks Firewall EAL Logs", "PAN-OS / Network", "panw_ngfw_traffic_raw"),
    AnalyticsSource("pan_firewall_threat_logs", "Palo Alto Networks Firewall threat Logs", "PAN-OS / Network", "panw_ngfw_threat_raw"),
    AnalyticsSource("pan_firewall_traffic_logs", "Palo Alto Networks Firewall traffic Logs", "PAN-OS / Network", "panw_ngfw_traffic_raw"),
    AnalyticsSource("pan_global_protect", "Palo Alto Networks Global Protect", "PAN-OS / Network", "panw_ngfw_globalprotect_raw"),
    AnalyticsSource("pan_platform_alerts", "Palo Alto Networks Platform Alerts", "Platform & Health", "panw_platform_alerts_raw"),
    AnalyticsSource("pan_url_logs", "Palo Alto Networks Url Logs", "PAN-OS / Network", "panw_ngfw_url_raw"),
    AnalyticsSource("pingone", "PingOne", "Identity & SSO", "pingone_raw"),
    AnalyticsSource("third_party_alerts", "Third-Party Alerts", "Third-Party", "third_party_alerts_raw"),
    AnalyticsSource("third_party_firewalls", "Third-Party Firewalls", "Third-Party", "third_party_firewall_raw"),
    AnalyticsSource("third_party_vpns", "Third-Party VPNs", "Third-Party", "third_party_vpn_raw"),
    AnalyticsSource("unspecified", "Unspecified", "Other", None, addressable=False,
                    note="Catch-all bucket, not a real product source — nothing to emit."),
    AnalyticsSource("windows_event_collector", "Windows Event Collector", "Endpoint", "msft_windows_security"),
    AnalyticsSource("xdr_agent", "XDR Agent", "Endpoint", None, addressable=False,
                    note="Endpoint process telemetry from a real beacon; not a log shape we POST (non-goal)."),
    AnalyticsSource("xdr_agent_xth", "XDR Agent with eXtended Threat Hunting (XTH)", "Endpoint", None, addressable=False,
                    note="Endpoint telemetry from a real beacon; not a log shape we POST (non-goal)."),
)

_CATALOGUE_BY_KEY: dict[str, AnalyticsSource] = {s.key: s for s in DATA_SOURCE_CATALOGUE}

TOTAL_SOURCES = len(DATA_SOURCE_CATALOGUE)
ADDRESSABLE_SOURCES = sum(1 for s in DATA_SOURCE_CATALOGUE if s.addressable)


class UnknownDataSourceError(ValueError):
    """Raised when an emitter declares a data-source key not in the catalogue.

    Failing here is deliberate: silently dropping the unknown key would hide the
    emitter from the coverage report while making the report look complete — the
    exact "tolerance hides bugs" trap CLAUDE.md warns against.
    """


def get_source(key: str) -> AnalyticsSource:
    try:
        return _CATALOGUE_BY_KEY[key]
    except KeyError as exc:
        raise UnknownDataSourceError(
            f"data source key '{key}' is not in the analytics catalogue "
            f"({TOTAL_SOURCES} known sources). Add it to DATA_SOURCE_CATALOGUE "
            f"if it is a real vendor source, or fix the emitter's typo."
        ) from exc


# ---------------------------------------------------------------------------
# Family manifests — the analytics log-streamer plugins in a registry.
# ---------------------------------------------------------------------------


def _plugin_family_manifest(cls: Any) -> Optional[dict[str, Any]]:
    """Return one plugin's family manifest, or ``None`` if it is not a member.

    Membership is declared, not sniffed: a plugin is in the analytics
    log-streamer family iff its ``Meta`` declares a non-empty ``data_sources``
    (or ``data_sources_partial``). That keeps the classification explicit and
    works for both ``AnalyticsLogEmitter`` subclasses (which carry
    ``analytics_manifest``) and the older collector-POST emitters that predate
    the spine (e.g. ``email_emitter``, a plain ``BaseSimulation``).
    """
    meta = getattr(cls, "Meta", None)
    if meta is None:
        return None
    data_sources = list(getattr(meta, "data_sources", []) or [])
    partial = list(getattr(meta, "data_sources_partial", []) or [])
    if not data_sources and not partial:
        return None

    # Prefer the richer manifest when the class provides one (the spine's
    # AnalyticsLogEmitter subclasses do); otherwise synthesise a minimal one so
    # a pre-spine emitter still appears in the catalogue join.
    if hasattr(cls, "analytics_manifest"):
        manifest = dict(cls.analytics_manifest())
    else:
        manifest = {
            "name": meta.name,
            "family": "analytics_log_streamer",
            "data_sources": data_sources,
            "datasets": list(getattr(meta, "datasets", [])),
            "detectors": list(getattr(meta, "detectors", [])),
            "supports_negative_control": bool(
                getattr(cls, "supports_negative_control", False)
            ),
            "mitre_techniques": list(getattr(meta, "mitre_techniques", [])),
        }
    manifest["data_sources_partial"] = partial
    manifest["description"] = getattr(meta, "description", "")
    return manifest


def family_manifests(registry: Any) -> list[dict[str, Any]]:
    """Every analytics log-streamer plugin in ``registry``, catalogue-joined.

    Each manifest gains a ``sources`` list resolving its declared keys to the
    catalogue rows (name / dataset / category), so the Data Streams console can
    render "emitter -> which vendor sources -> which dataset" without re-deriving
    the join. Unknown keys raise (see ``UnknownDataSourceError``).
    """
    out: list[dict[str, Any]] = []
    for cls in registry:
        manifest = _plugin_family_manifest(cls)
        if manifest is None:
            continue
        resolved: list[dict[str, Any]] = []
        for key in manifest["data_sources"]:
            src = get_source(key)
            resolved.append({
                "key": src.key, "name": src.name, "category": src.category,
                "dataset": src.dataset, "coverage": "full",
            })
        for key in manifest["data_sources_partial"]:
            src = get_source(key)
            resolved.append({
                "key": src.key, "name": src.name, "category": src.category,
                "dataset": src.dataset, "coverage": "partial",
            })
        manifest["sources"] = resolved
        out.append(manifest)
    out.sort(key=lambda m: m["name"])
    return out


# ---------------------------------------------------------------------------
# Coverage report — the deliverable.
# ---------------------------------------------------------------------------


def coverage_report(registry: Any) -> dict[str, Any]:
    """Coverage of the analytics log-streamer family against the vendor catalogue.

    Returns a per-source table (ALL 34 sources, gaps included) plus aggregates.
    Every covered source is ``authored`` and, unconditionally, NOT ``proven``:
    ``tenant_verified`` is 0 until a detector is observed firing against a live
    tenant.
    """
    # source_key -> [(emitter_name, supports_negative_control)]
    full_cover: dict[str, list[tuple[str, bool]]] = {}
    partial_cover: dict[str, list[str]] = {}

    for cls in registry:
        manifest = _plugin_family_manifest(cls)
        if manifest is None:
            continue
        name = manifest["name"]
        neg = bool(manifest["supports_negative_control"])
        for key in manifest["data_sources"]:
            get_source(key)  # validate — raises on typo
            full_cover.setdefault(key, []).append((name, neg))
        for key in manifest["data_sources_partial"]:
            get_source(key)
            partial_cover.setdefault(key, []).append(name)

    sources: list[dict[str, Any]] = []
    counts = {
        "total": TOTAL_SOURCES,
        "addressable": ADDRESSABLE_SOURCES,
        "covered": 0,
        "partial": 0,
        "gap": 0,
        "not_addressable": 0,
        "authored": 0,
        "proven": 0,  # tenant-verified is 0 — see module docstring.
        "with_negative_control": 0,
    }

    for src in DATA_SOURCE_CATALOGUE:
        covering = full_cover.get(src.key, [])
        partials = partial_cover.get(src.key, [])
        if not src.addressable:
            state = "not_addressable"
        elif covering:
            state = "covered"
        elif partials:
            state = "partial"
        else:
            state = "gap"

        has_neg = any(neg for _, neg in covering)
        row = {
            "key": src.key,
            "name": src.name,
            "category": src.category,
            "dataset": src.dataset,
            "addressable": src.addressable,
            "state": state,
            # authored != proven, always kept as two fields.
            "authored": state == "covered",
            "proven": False,
            "has_negative_control": has_neg,
            "emitters": sorted({n for n, _ in covering}),
            "partial_emitters": sorted(set(partials)),
            "note": src.note,
        }
        sources.append(row)

        counts[state] += 1
        if state == "covered":
            counts["authored"] += 1
            if has_neg:
                counts["with_negative_control"] += 1

    return {
        "catalogue_source": (
            "https://cortex-docs.paloaltonetworks.com/analytics-alerts/"
            "alerts-by-data-source"
        ),
        "catalogue_version": "2026-09-04",
        "counts": counts,
        "sources": sources,
        # The honesty banner every consumer must surface verbatim.
        "authored_not_proven": (
            f"{counts['authored']} of {counts['addressable']} addressable sources "
            f"are authored; tenant-verified is {counts['proven']}. Authored is not "
            f"proven — no emitter has been observed firing its detector against a "
            f"live Cortex tenant."
        ),
    }

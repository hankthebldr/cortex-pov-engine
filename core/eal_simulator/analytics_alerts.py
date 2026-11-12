"""
analytics_alerts — the vendor analytics-alert profile registry and the detector
readiness gate.

Why this module exists
----------------------
``analytics_catalogue`` answers *which data sources* Cortex Analytics consumes.
It cannot answer the question a DC actually has mid-POV, which is: **"I streamed
the records — why did nothing fire?"**

Walking the vendor's own alert pages settles what can and cannot be answered:

    https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/<slug>

Every alert page publishes Severity, **Activation Period**, **Training Period**,
**Test Period**, **Deduplication Period**, Detection Modules, ATT&CK mapping and
a **Required Data** line naming the data sources that can feed it. No alert page
publishes a numeric threshold, a dataset field name, or the predicate itself.

That asymmetry is the whole design. "Tune the stream to match the detection"
cannot mean matching a predicate, because the predicate is not published. What
*is* published is a **precondition contract**, and a precondition is exactly the
thing whose failure is invisible: a detector with a 30-day training period,
pointed at a dataset the tenant started carrying yesterday, is not *missing* the
attack — it is **not armed**. The records were perfect. The silence still reads
in a POV report as "Cortex missed it", which is the manufactured false negative
this engine exists to avoid, reproduced at the exact layer meant to prove
coverage.

Four rules bind this file
-------------------------
  * **Documented is not derived.** ``activation_days`` and ``training_days`` are
    transcribed verbatim and stay separately quotable. The vendor never says the
    two compose, so the single-number gate — ``max(activation, training)`` — is
    OUR inference and is labelled ``gate_basis="inferred:..."`` in every verdict.
    Same seam as authored-vs-proven: never report the two as one number.
  * **Unknown is degraded, not ok.** An operator who has not declared when the
    tenant began carrying a source gets ``unknown``. ``unknown`` is never
    ``armed``. Defaulting an unknown to "armed" would restore the exact silence
    this module exists to explain.
  * **A typo must raise, not vanish.** An unknown alert slug raises
    ``UnknownAlertError``; a ``required_data`` key that is not a real catalogue
    source raises at import. The tolerant alternative greens the test and keeps
    the bug.
  * **A detector bound to an alert it cannot feed raises.** If an emitter's data
    source is not in the alert's Required Data OR-set, that emitter can never
    fire that alert. Returning a soft verdict would let the coverage report
    carry a claim that is unsatisfiable by construction.

Provenance
----------
Every profile carries ``doc_url`` and ``transcribed_on`` so a stale transcription
is re-checkable rather than merely trusted. When the vendor page changes, THIS is
the thing to update.

Naive datetimes
---------------
``readiness_for`` **rejects** a naive ``onboarded_at`` rather than assuming UTC.
This deliberately differs from ``connectors.base.coerce_utc``, which accepts
naive-as-UTC — and the difference is the input class, not an oversight.
``coerce_utc`` parses *tenant-supplied* alert timestamps, where tolerance beats a
crash. This function reads an *operator-typed configuration date*, where silently
assuming UTC shifts the readiness gate by the DC's own UTC offset and produces a
wrong answer with no error anywhere. Do not "unify" them.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any, Optional

from .analytics_catalogue import get_source


#: How we compose the two documented periods into one gate. Carried in every
#: verdict so the derivation can never be quoted as a vendor statement.
GATE_BASIS = "inferred:max(activation,training)"

_DOC_BASE = "https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name"


class UnknownAlertError(ValueError):
    """Raised when a detector declares an ``alert_ref`` not in this registry.

    Failing here is deliberate. A detector naming an alert the vendor does not
    document for its data source is an unbacked coverage claim; silently
    accepting it would put that claim in a POV report.
    """


@dataclasses.dataclass(frozen=True)
class AlertProfile:
    """One vendor analytics alert's published precondition contract.

    Every field except ``gate_days`` is transcribed verbatim from the vendor
    page at ``doc_url``. ``modules`` is empty when the page does not state one —
    an empty tuple means "the page is silent", never "no module".
    """

    slug: str
    name: str
    severity: str
    activation_days: int
    training_days: int
    test_period: str
    dedup_period: str
    required_data: tuple[str, ...]
    modules: tuple[str, ...]
    mitre_tactics: tuple[str, ...]
    mitre_techniques: tuple[str, ...]
    doc_url: str
    transcribed_on: str

    @property
    def gate_days(self) -> int:
        """Days of data the source must have carried before the alert can fire.

        DERIVED, not documented — see ``GATE_BASIS``.
        """
        return max(self.activation_days, self.training_days)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["gate_days"] = self.gate_days
        d["gate_basis"] = GATE_BASIS
        return d


#: The transcribed alert profiles, one per vendor alert that a shipped emitter
#: binds. Transcribed 2026-09-20 from the per-alert pages; ``name`` matches the
#: vendor string verbatim so a DC can cross-reference the page.
ALERT_PROFILES: tuple[AlertProfile, ...] = (
    AlertProfile(
        slug="port-scan",
        name="Port Scan",
        severity="Informational",
        activation_days=14,
        training_days=30,
        test_period="1 Hour",
        dedup_period="1 Day",
        required_data=("pan_firewall_traffic_logs", "third_party_firewalls"),
        modules=(),
        mitre_tactics=("TA0007",),
        mitre_techniques=("T1046",),
        doc_url=f"{_DOC_BASE}/port-scan.md",
        transcribed_on="2026-09-20",
    ),
    AlertProfile(
        slug="port-sweep",
        name="Port Sweep",
        severity="Informational",
        activation_days=14,
        training_days=30,
        test_period="1 Hour",
        dedup_period="1 Day",
        required_data=("pan_firewall_traffic_logs", "xdr_agent", "third_party_firewalls"),
        modules=(),
        mitre_tactics=("TA0007",),
        mitre_techniques=("T1046",),
        doc_url=f"{_DOC_BASE}/port-sweep.md",
        transcribed_on="2026-09-20",
    ),
    AlertProfile(
        slug="failed-connections",
        name="Failed Connections",
        severity="Low",
        activation_days=14,
        training_days=30,
        test_period="1 Day",
        dedup_period="1 Day",
        required_data=("pan_firewall_traffic_logs", "xdr_agent", "third_party_firewalls"),
        modules=(),
        mitre_tactics=("TA0007",),
        mitre_techniques=("T1018",),
        doc_url=f"{_DOC_BASE}/failed-connections.md",
        transcribed_on="2026-09-20",
    ),
    AlertProfile(
        slug="impossible-traveler-vpn",
        name="Impossible traveler - VPN",
        severity="Low",
        activation_days=14,
        training_days=30,
        test_period="3 Hours",
        dedup_period="7 Days",
        required_data=("pan_global_protect", "third_party_vpns"),
        modules=("Identity Analytics",),
        mitre_tactics=("TA0006", "TA0042"),
        mitre_techniques=("T1586", "T1110.001"),
        doc_url=f"{_DOC_BASE}/impossible-traveler-vpn.md",
        transcribed_on="2026-09-20",
    ),
    AlertProfile(
        slug="vpn-login-brute-force-attempt",
        name="VPN login Brute-Force attempt",
        severity="Informational",
        activation_days=14,
        training_days=30,
        test_period="1 Hour",
        dedup_period="1 Day",
        required_data=("pan_global_protect", "third_party_vpns"),
        modules=("Identity Analytics",),
        mitre_tactics=("TA0006", "TA0042"),
        mitre_techniques=("T1110", "T1586", "T1110.001"),
        doc_url=f"{_DOC_BASE}/vpn-login-brute-force-attempt.md",
        transcribed_on="2026-09-20",
    ),
    AlertProfile(
        slug="a-user-connected-to-a-vpn-from-a-new-country",
        name="A user connected to a VPN from a new country",
        severity="Informational",
        activation_days=14,
        training_days=30,
        test_period="N/A (single event)",
        dedup_period="30 Days",
        required_data=("pan_global_protect", "third_party_vpns"),
        modules=("Identity Analytics",),
        mitre_tactics=("TA0006", "TA0042"),
        mitre_techniques=("T1586", "T1110.001"),
        doc_url=f"{_DOC_BASE}/a-user-connected-to-a-vpn-from-a-new-country.md",
        transcribed_on="2026-09-20",
    ),
    AlertProfile(
        slug="a-user-connected-from-a-new-country",
        name="A user connected from a new country",
        severity="Informational",
        activation_days=14,
        training_days=30,
        test_period="N/A (single event)",
        dedup_period="30 Days",
        required_data=(
            "azuread", "azure_signin_log", "idira", "duo", "okta", "onelogin", "pingone",
        ),
        modules=("Identity Analytics",),
        mitre_tactics=("TA0006", "TA0042"),
        mitre_techniques=("T1586", "T1110.001"),
        doc_url=f"{_DOC_BASE}/a-user-connected-from-a-new-country.md",
        transcribed_on="2026-09-20",
    ),
    AlertProfile(
        slug="abnormal-increase-in-network-related-alerts-on-the-same-host",
        name="Abnormal increase in network-related alerts on the same host",
        severity="Low",
        activation_days=14,
        training_days=30,
        test_period="1 Day",
        dedup_period="1 Day",
        required_data=("pan_platform_alerts", "third_party_alerts"),
        modules=(),
        mitre_tactics=("TA0002",),
        mitre_techniques=("T1204",),
        doc_url=f"{_DOC_BASE}/abnormal-increase-in-network-related-alerts-on-the-same-host.md",
        transcribed_on="2026-09-20",
    ),
)

_PROFILES_BY_SLUG: dict[str, AlertProfile] = {p.slug: p for p in ALERT_PROFILES}


def _validate_registry() -> None:
    """Fail at import if a profile is internally unsatisfiable.

    A duplicate slug silently shadows a profile; a ``required_data`` key that is
    not a real catalogue source makes the emitter-to-alert join impossible to
    satisfy while every individual piece still looks fine.
    """
    if len(_PROFILES_BY_SLUG) != len(ALERT_PROFILES):
        seen: set[str] = set()
        dupes = sorted({p.slug for p in ALERT_PROFILES if p.slug in seen or seen.add(p.slug)})
        raise ValueError(f"duplicate alert slug(s) in ALERT_PROFILES: {dupes}")
    for profile in ALERT_PROFILES:
        if not profile.required_data:
            raise ValueError(
                f"alert '{profile.slug}' declares no Required Data; an alert no "
                f"source can feed cannot be bound by any emitter."
            )
        for key in profile.required_data:
            get_source(key)  # raises UnknownDataSourceError on a typo


_validate_registry()


def get_alert(slug: str) -> AlertProfile:
    """Return one alert profile, or raise ``UnknownAlertError``."""
    try:
        return _PROFILES_BY_SLUG[slug]
    except KeyError as exc:
        raise UnknownAlertError(
            f"alert slug '{slug}' is not in the analytics alert registry "
            f"({len(ALERT_PROFILES)} transcribed alerts). Use the vendor's own "
            f"slug from {_DOC_BASE}/<slug>.md — our invented alert names are not "
            f"vendor alerts and do not join to a documented detector."
        ) from exc


@dataclasses.dataclass(frozen=True)
class Readiness:
    """Whether one alert could possibly fire for one data source, and why not.

    ``activation_days`` / ``training_days`` are the documented values, carried
    so a consumer can quote the vendor without re-fetching. ``gate_days`` and
    ``gate_basis`` are OUR derivation and are deliberately separate fields.
    """

    alert_slug: str
    alert_name: str
    data_source: str
    state: str          # armed | not_armed | unknown | not_armable
    armed: bool
    code: Optional[str]
    days_of_data: Optional[int]
    days_remaining: Optional[int]
    activation_days: int
    training_days: int
    gate_days: int
    gate_basis: str
    detail: str
    #: Why an ``armed`` verdict is armed: ``onboarding_date`` (the source has
    #: carried data past the gate) or ``attested:soakless`` (the operator says
    #: the tenant waives the periods). ``None`` when not armed.
    armed_basis: Optional[str] = None
    #: The delivery route the verdict was judged against, or ``None`` when no
    #: route was supplied (the pure-function back-compat path).
    route: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


#: Route classifications that say the route cannot be read, as opposed to
#: pipes that name one. ``mixed`` is the campaign-level case of one emitter
#: delivering through more than one door.
_UNRESOLVED_ROUTES = {
    "cribl_receiver": (
        "The Cribl receiver URL does not say which source UUID the records carry; "
        "a dedicated UUID feeds analytics and the generic one does not."
    ),
    "unknown_forwarder": (
        "The collector URL is not a tenant door, so the route onward from the "
        "forwarder is unknown."
    ),
    "undeclared": "No collector URL was declared, so the delivery route is unknown.",
    "mixed": (
        "This emitter delivers through more than one door in this campaign; each "
        "door has its own answer."
    ),
}


def _route_gate(
    profile: AlertProfile,
    data_source: str,
    pipe: str,
    xdm_modeled_attested: bool,
    common: dict[str, Any],
) -> tuple[Optional["Readiness"], dict[str, Any]]:
    """Judge the delivery route before anything else.

    Returns ``(verdict, route_info)``. A non-``None`` verdict ends evaluation:
    a route analytics never reads stays unfireable whatever the training state.
    """
    # Imported here: ingestion_routes imports analytics_catalogue, which this
    # module also imports; a module-level import would order them needlessly.
    from .ingestion_routes import PIPES, resolve_route  # noqa: PLC0415

    def _halt(state: str, code: str, detail: str, info: dict[str, Any]) -> "Readiness":
        return Readiness(
            state=state, armed=False, code=code, days_of_data=None,
            days_remaining=None, detail=detail, route=info, **common,
        )

    if pipe in _UNRESOLVED_ROUTES:
        info = {"pipe": pipe, "resolved": False}
        return _halt(
            "unknown", "DETECTOR_ROUTE_UNRESOLVED",
            f"{_UNRESOLVED_ROUTES[pipe]} Whether '{profile.name}' can see these "
            f"records is unknown, so its silence is not evidence of a miss.",
            info,
        ), info
    if pipe not in PIPES:
        raise ValueError(
            f"unknown pipe {pipe!r}; expected one of {sorted(PIPES)} or "
            f"{sorted(_UNRESOLVED_ROUTES)}"
        )

    route = resolve_route(data_source, pipe)
    if route is None:
        info = {"pipe": pipe, "resolved": False}
        return _halt(
            "unknown", "DETECTOR_ROUTE_UNRESOLVED",
            f"No documented route takes '{pipe}' into '{data_source}', so whether "
            f"'{profile.name}' can see these records is unknown.",
            info,
        ), info

    info = {"pipe": pipe, "resolved": True, **route.to_dict()}
    if route.ootb_analytics == "no":
        return _halt(
            "not_armable", "DETECTOR_ROUTE_NO_ANALYTICS",
            f"Records reaching '{data_source}' by {route.pipe} land in "
            f"{route.tenant_dataset}, where built-in analytics do not run "
            f"({route.analytics_evidence}). '{profile.name}' can never fire on "
            f"them; a soakless tenant does not change that. Use a route that "
            f"feeds analytics.",
            info,
        ), info
    if route.ootb_analytics == "requires_xdm" and not xdm_modeled_attested:
        return _halt(
            "unknown", "DETECTOR_ROUTE_REQUIRES_XDM",
            f"Records reaching '{data_source}' by {route.pipe} land in a custom "
            f"dataset that built-in analytics read only after an XDM modeling "
            f"rule maps it. No such rule is attested, so whether '{profile.name}' "
            f"can fire is unknown.",
            info,
        ), info
    if route.ootb_analytics == "requires_xdm":
        info["xdm_modeled_attested"] = True
    return None, info


def readiness_for(
    profile: AlertProfile,
    data_source: str,
    onboarded_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
    soakless_attested: Optional[bool] = None,
    pipe: Optional[str] = None,
    xdm_modeled_attested: bool = False,
) -> Readiness:
    """Can ``profile`` fire for ``data_source``, given when the tenant started
    carrying it?

    ``onboarded_at`` is the operator's declaration of when this data source
    began landing in the tenant — NOT when this campaign ran. ``None`` means the
    operator has not declared it, which yields ``unknown``, never ``armed``.

    Three gates, in this order, the first that fails decides:

    1. ``pipe`` — the delivery route (see ``ingestion_routes``). A route analytics
       never reads is ``not_armable``; one that needs an XDM rule is ``unknown``
       unless ``xdm_modeled_attested``. ``None`` skips the gate (back-compat for
       callers that never had a route); the campaign endpoint always supplies one.
    2. ``soakless_attested`` — the operator says the tenant waives activation,
       training and dedup periods. ``True`` arms the detector with
       ``armed_basis="attested:soakless"``; the documented periods stay in the
       verdict so the vendor's numbers remain quotable.
    3. ``onboarded_at`` against the documented gate.

    Raises ``ValueError`` when ``data_source`` is outside the alert's Required
    Data OR-set (an unsatisfiable binding), when ``onboarded_at`` is naive, or
    when ``pipe`` is not a known route classification.
    """
    get_source(data_source)  # raises on a typo'd source key
    if data_source not in profile.required_data:
        raise ValueError(
            f"data source '{data_source}' cannot feed alert '{profile.slug}': its "
            f"documented Required Data is one of {list(profile.required_data)}. An "
            f"emitter on this source can never fire this alert, so the binding is "
            f"unsatisfiable rather than merely unproven."
        )

    gate = profile.gate_days
    common = {
        "alert_slug": profile.slug,
        "alert_name": profile.name,
        "data_source": data_source,
        "activation_days": profile.activation_days,
        "training_days": profile.training_days,
        "gate_days": gate,
        "gate_basis": GATE_BASIS,
    }

    # Reject a naive declaration before any gate can short-circuit, so the same
    # bad input fails the same way whatever the route or attestation.
    if onboarded_at is not None and onboarded_at.tzinfo is None:
        raise ValueError(
            f"onboarded_at for '{data_source}' is a naive datetime; supply an "
            f"explicit timezone. Assuming UTC would shift the readiness gate by "
            f"the operator's own UTC offset and answer wrongly with no error."
        )

    if pipe is not None:
        halted, route_info = _route_gate(
            profile, data_source, pipe, xdm_modeled_attested, common,
        )
        if halted is not None:
            return halted
        common["route"] = route_info

    now = now or datetime.now(timezone.utc)

    if soakless_attested is True:
        days_of_data = (now - onboarded_at).days if onboarded_at else None
        return Readiness(
            state="armed",
            armed=True,
            code="DETECTOR_ARMED_SOAKLESS",
            days_of_data=days_of_data,
            days_remaining=0,
            detail=(
                f"The operator attested this tenant is soakless, which waives the "
                f"{profile.activation_days}-day activation and "
                f"{profile.training_days}-day training periods (and dedup). "
                f"'{profile.name}' can fire on the first matching record. This is "
                f"an attestation, not something CortexSim checked, and ARMED says "
                f"nothing about whether the detector has been observed firing."
            ),
            armed_basis="attested:soakless",
            **common,
        )

    if onboarded_at is None:
        return Readiness(
            state="unknown",
            armed=False,
            code="DETECTOR_READINESS_UNKNOWN",
            days_of_data=None,
            days_remaining=None,
            detail=(
                f"No onboarding date declared for '{data_source}', so it is not "
                f"known whether '{profile.name}' is armed. The alert needs "
                f"{gate} days of data ({profile.activation_days}d activation, "
                f"{profile.training_days}d training). Silence from this detector "
                f"is NOT evidence of a miss until this is declared."
            ),
            **common,
        )

    days_of_data = (now - onboarded_at).days
    remaining = max(0, gate - days_of_data)

    if days_of_data >= gate:
        return Readiness(
            state="armed",
            armed=True,
            code=None,
            days_of_data=days_of_data,
            days_remaining=0,
            detail=(
                f"'{data_source}' has been carried for {days_of_data} days, at or "
                f"past the {gate}-day gate. '{profile.name}' can fire. This says "
                f"the detector is ARMED — not that it has ever been observed "
                f"firing."
            ),
            armed_basis="onboarding_date",
            **common,
        )

    return Readiness(
        state="not_armed",
        armed=False,
        code="DETECTOR_NOT_ARMED",
        days_of_data=days_of_data,
        days_remaining=remaining,
        detail=(
            f"'{data_source}' has been carried for {days_of_data} days; "
            f"'{profile.name}' needs {gate} ({profile.activation_days}d "
            f"activation, {profile.training_days}d training). {remaining} days "
            f"short. Records streamed now will land, but this detector cannot "
            f"fire — report the silence as NOT ARMED, never as a missed detection."
        ),
        **common,
    )


# ---------------------------------------------------------------------------
# The emitter -> alert binding.
# ---------------------------------------------------------------------------


def _emitter_meta(cls: Any) -> Optional[tuple[str, list[str], list[dict[str, Any]]]]:
    """``(name, data_sources, detectors)`` for a family member, else ``None``."""
    meta = getattr(cls, "Meta", None)
    if meta is None:
        return None
    sources = list(getattr(meta, "data_sources", []) or [])
    partial = list(getattr(meta, "data_sources_partial", []) or [])
    if not sources and not partial:
        return None
    detectors = list(getattr(meta, "detectors", []) or [])
    return getattr(meta, "name", cls.__name__), sources + partial, detectors


def detector_bindings(registry: Any) -> list[dict[str, Any]]:
    """Every declared detector, joined to the vendor alert it claims.

    A detector must declare **exactly one** of ``alert_ref`` (the vendor's own
    slug) or ``undocumented_reason`` (prose saying why no documented analytics
    alert backs this claim). Declaring neither raises; declaring both raises.
    This mirrors the payload shelf's ``TA-13``/``TA-14`` for the same reason: an
    emitter that claims a detector nobody can point at is a coverage claim with
    nothing behind it, and a POV report is where that surfaces.

    Undocumented claims are RETURNED, not dropped. An unlisted gap reads as no
    gap.
    """
    rows: list[dict[str, Any]] = []
    for cls in registry:
        meta = _emitter_meta(cls)
        if meta is None:
            continue
        emitter, sources, detectors = meta
        for det in detectors:
            our_name = det.get("alert")
            alert_ref = det.get("alert_ref")
            reason = det.get("undocumented_reason")

            if bool(alert_ref) == bool(reason):
                raise ValueError(
                    f"detector {our_name!r} on emitter '{emitter}' must declare "
                    f"exactly one of 'alert_ref' or 'undocumented_reason' "
                    f"(got alert_ref={alert_ref!r}, "
                    f"undocumented_reason={reason!r}). A detector naming neither "
                    f"is an unbacked coverage claim; naming both hides which one "
                    f"is true."
                )

            if reason:
                rows.append({
                    "emitter": emitter,
                    "alert": our_name,
                    "alert_ref": None,
                    "documented": False,
                    "undocumented_reason": reason,
                    "data_source": sources[0] if sources else None,
                    "data_sources": list(sources),
                    "profile": None,
                    "dataset": det.get("dataset"),
                    "negative_control": det.get("negative_control"),
                })
                continue

            profile = get_alert(alert_ref)  # raises UnknownAlertError
            feeding = [s for s in sources if s in profile.required_data]
            if not feeding:
                raise ValueError(
                    f"emitter '{emitter}' binds alert '{alert_ref}' but none of "
                    f"its data sources {sources} is in that alert's documented "
                    f"Required Data {list(profile.required_data)}. This emitter "
                    f"can never fire this alert -- the binding is unsatisfiable "
                    f"by construction, not merely unproven."
                )
            rows.append({
                "emitter": emitter,
                "alert": our_name,
                "alert_ref": alert_ref,
                "documented": True,
                "undocumented_reason": None,
                "data_source": feeding[0],
                "data_sources": feeding,
                "profile": profile.to_dict(),
                "dataset": det.get("dataset"),
                "negative_control": det.get("negative_control"),
            })
    return rows


def validate_detector_bindings(registry: Any) -> None:
    """Raise if any detector in ``registry`` is unbound or unsatisfiable."""
    detector_bindings(registry)


def readiness_report(
    registry: Any,
    *,
    plugins: Optional[set[str]] = None,
    onboarded_at: Optional[dict[str, datetime]] = None,
    now: Optional[datetime] = None,
    soakless_attested: Optional[bool] = None,
    pipes: Optional[dict[str, str]] = None,
    xdm_modeled: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Every detector's readiness, optionally scoped to one campaign's plugins.

    Makes **zero outbound calls**: it reads transcribed vendor preconditions
    against the operator's own declaration of when each data source began
    landing in the tenant. It never asks the tenant anything, and ``armed``
    therefore says the preconditions are met — never that the detector has been
    observed firing. ``tenant_verified`` is 0 and stays 0.

    ``ready`` is true only when EVERY detector is ``armed``. An undocumented
    claim can never be armed, so a campaign carrying one is never ready: that
    detector's silence would have no documented explanation, which is precisely
    the reading this gate exists to prevent.

    ``pipes`` maps an emitter name to its delivery route (see
    ``readiness_for``); an emitter absent from it is judged without a route.
    ``xdm_modeled`` is the set of data-source keys whose custom dataset the
    operator attests an XDM modeling rule maps.
    """
    rows = detector_bindings(registry)
    if plugins is not None:
        rows = [r for r in rows if r["emitter"] in plugins]
    declared = onboarded_at or {}
    routes = pipes or {}
    modeled = xdm_modeled or set()

    counts = {
        "total": 0, "armed": 0, "not_armed": 0, "unknown": 0,
        "not_armable": 0, "undocumented": 0,
    }
    out: list[dict[str, Any]] = []

    for row in rows:
        counts["total"] += 1
        if not row["documented"]:
            counts["undocumented"] += 1
            readiness: dict[str, Any] = {
                "state": "undocumented",
                "armed": False,
                "code": "DETECTOR_UNDOCUMENTED",
                "days_of_data": None,
                "days_remaining": None,
                "gate_basis": GATE_BASIS,
                "detail": (
                    f"No documented vendor analytics alert backs "
                    f"{row['alert']!r}: {row['undocumented_reason']} Readiness "
                    f"cannot be evaluated, so silence from this claim has no "
                    f"documented explanation."
                ),
            }
        else:
            profile = get_alert(row["alert_ref"])
            verdict = readiness_for(
                profile, row["data_source"], declared.get(row["data_source"]),
                now=now, soakless_attested=soakless_attested,
                pipe=routes.get(row["emitter"]),
                xdm_modeled_attested=row["data_source"] in modeled,
            )
            readiness = verdict.to_dict()
            counts[verdict.state] += 1
        out.append({**row, "readiness": readiness})

    ready = counts["total"] > 0 and counts["armed"] == counts["total"]
    soakless_note = (
        " The tenant is attested soakless (operator statement, not checked)."
        if soakless_attested is True else ""
    )
    return {
        "gate_basis": GATE_BASIS,
        "tenant_verified": 0,
        "alerts_transcribed": len(ALERT_PROFILES),
        "soakless_attested": soakless_attested,
        "detectors": out,
        "counts": counts,
        "ready": ready,
        "banner": (
            f"{counts['armed']} of {counts['total']} detectors armed "
            f"({counts['not_armed']} not armed, {counts['unknown']} unknown, "
            f"{counts['not_armable']} not armable on their route, "
            f"{counts['undocumented']} undocumented); tenant-verified is 0. "
            f"ARMED means the alert's documented preconditions are met -- it is "
            f"NOT evidence that the detector has ever been observed firing."
            f"{soakless_note}"
        ),
    }

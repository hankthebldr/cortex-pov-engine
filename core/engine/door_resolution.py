"""
door_resolution — which ingestion door each expected detection needs.

A detection can only fire if the data it reads reaches the tenant, so each
expected detection is joined to the doors (``engine.ingestion_doors``) its data
arrives through. The rules are the ingestion-doors spec §5, applied in order:

  1. **A step that POSTs to a collector is judged by its route.** The records
     land in the collector target's dataset whatever the card claims, so the
     door is ``http_collector`` keyed by that target. The route cannot feed a
     BIOC or IOC (the data is not stitched), feeds Analytics and ABIOC only
     with the XDM rule attested, and feeds XQL and Correlation only when the
     card's dataset is the target's dataset.
  2. **Otherwise the card's datasets decide**: AND across every dataset the
     detection reads, OR within each dataset's registry ``doors``.
  3. **The cloud provider doors are split by provider**, read from a
     ``cloud_provider`` filter in the detection's logic, else the card's
     provider tag. A detection naming none is ``underived``, never admitted by
     whichever provider happens to be open.
  5. **A detection with no derivable dataset is ``underived``** with a code.
     It is scored as today and counted, so the gap stays visible.

Rule 4 (agent doors resolve against the step's own beacon) is the caller's: an
agent-scoped door is keyed with the ``agent_id`` it is judged for.

Rules that bind this file:

  * **Tolerance hides bugs.** An unrecognised detection, scenario or card shape
    raises, as do a malformed card and an absent or malformed registry. Only a
    missing card, a missing or unregistered dataset, an unknown door and an
    unnamed provider are ``underived``, each with its own code; nothing else is.
  * **A zero is degraded.** A scenario with no derived detection reads
    ``not_evaluated``, never ``ready``.

Pure: no database, no network, no FastAPI.

Spec: docs/superpowers/specs/2026-11-11-ingestion-doors-design.md
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from typing import Any, Mapping, Optional

from engine.dataset_requirements import DatasetRequirement, walk_card
from engine.ingestion_doors import CLOUD_PROVIDER_DOORS, DOORS, UnknownDoorError, door_key, get_door
from engine.signal_path import signal_of

__all__ = [
    "COLLECTOR_DOOR",
    "DETECTION_TYPES",
    "ROUTE_CANNOT_FEED",
    "ROUTE_COLLECTOR",
    "UNDERIVED_CODES",
    "DoorRequirement",
    "DoorResolutionError",
    "detection_requirement",
    "scenario_readiness",
]

ROUTE_COLLECTOR = "collector"
ROUTE_CANNOT_FEED = "DOOR_ROUTE_CANNOT_FEED"
COLLECTOR_DOOR = "http_collector"

#: Why a detection has no derived door.
#:   CARD_NOT_FOUND  the ``ttp_ref`` names no card in the catalog
#:   NO_DATASET      no ``ttp_ref``, no card object for the ``detection_id``, or an
#:                   object that names no dataset (an IOC, no ``dataset =`` anchor)
#:   DOORS_UNKNOWN   a dataset it reads has a registry ``doors_unknown`` row
#:   NO_PROVIDER     it reads a multi-provider cloud dataset and names no provider
#:   UNREGISTERED_DATASET  a dataset it reads has no registry row (the code
#:                   ``engine.prerequisites`` uses for the same fault)
UNDERIVED_CODES: tuple[str, ...] = (
    "CARD_NOT_FOUND", "NO_DATASET", "DOORS_UNKNOWN", "NO_PROVIDER", "UNREGISTERED_DATASET",
)

DETECTION_TYPES: tuple[str, ...] = ("BIOC", "XQL", "Analytics", "Correlation", "IOC", "ABIOC", "Prevention")
_ANALYTICS_TYPES = frozenset({"Analytics", "ABIOC"})
# A collector dataset is not stitched, so nothing keyed on the causality chain fires on it.
_UNSTITCHED_TYPES = frozenset({"BIOC", "IOC"})

# DetectionCard.kind -> the walk_card kind that holds the object. Kinds absent here
# (ioc, modeling, prevention) name no dataset.
_WALK_KIND = {"xql": "xql_queries", "bioc": "biocs", "abioc": "abiocs", "correlation": "correlation_rules"}

# ``cloud_provider = "X"`` or ``cloud_provider in ("X", "Y")``. ``!=`` does not match.
_PROVIDER_FILTER = re.compile(r'\bcloud_provider\s*(?:=\s*"([^"]*)"|in\s*\(([^)]*)\))', re.IGNORECASE)
_QUOTED = re.compile(r'"([^"]*)"')
_PROVIDER_DOOR = {d.provider_filter: d.id for d in DOORS if d.provider_filter is not None}
_TAG_DOOR = {k.lower(): v for k, v in _PROVIDER_DOOR.items()}


class DoorResolutionError(ValueError):
    """An input this module cannot read: an unrecognised shape, or an absent registry."""


@dataclasses.dataclass(frozen=True)
class DoorRequirement:
    """What one expected detection needs to be scorable.

    ``all_of`` is an AND of OR-sets of door ids. ``route`` is ``"collector"`` when
    rule 1 judged it, and then ``collector_target`` names the target and
    ``route_blocked`` says the route cannot feed it. ``underived`` carries a code
    from :data:`UNDERIVED_CODES` and an empty ``all_of``.
    """

    all_of: tuple[tuple[str, ...], ...] = ()
    route: Optional[str] = None
    collector_target: Optional[str] = None
    route_blocked: Optional[str] = None
    needs_analytics: bool = False
    underived: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.all_of, tuple) or not all(
            isinstance(el, tuple) and el and all(isinstance(d, str) for d in el) for el in self.all_of
        ):
            raise DoorResolutionError(f"all_of must be a tuple of non-empty tuples of door ids, got {self.all_of!r}")
        for el in self.all_of:
            for d in el:
                try:
                    get_door(d)
                except UnknownDoorError as exc:
                    raise DoorResolutionError(str(exc)) from exc
        if self.route not in (None, ROUTE_COLLECTOR):
            raise DoorResolutionError(f"route must be None or {ROUTE_COLLECTOR!r}, got {self.route!r}")
        if self.route_blocked not in (None, ROUTE_CANNOT_FEED):
            raise DoorResolutionError(
                f"route_blocked must be None or {ROUTE_CANNOT_FEED!r}, got {self.route_blocked!r}")
        if self.underived is not None and self.underived not in UNDERIVED_CODES:
            raise DoorResolutionError(f"underived must be one of {UNDERIVED_CODES}, got {self.underived!r}")
        if not isinstance(self.needs_analytics, bool):
            raise DoorResolutionError(f"needs_analytics must be a bool, got {self.needs_analytics!r}")
        if self.collector_target is not None and not isinstance(self.collector_target, str):
            raise DoorResolutionError(f"collector_target must be a string, got {self.collector_target!r}")
        if self.route is None and (self.collector_target is not None or self.route_blocked is not None):
            raise DoorResolutionError("collector_target and route_blocked belong to the collector route only")
        if self.route == ROUTE_COLLECTOR and (self.all_of != ((COLLECTOR_DOOR,),) or self.underived):
            raise DoorResolutionError(f"the collector route needs exactly the {COLLECTOR_DOOR!r} door")
        if self.route is None and bool(self.underived) == bool(self.all_of):
            raise DoorResolutionError("a requirement has either doors or an underived code, not both or neither")

    def to_json(self) -> dict[str, Any]:
        return {
            "all_of": [list(el) for el in self.all_of],
            "route": self.route,
            "collector_target": self.collector_target,
            "route_blocked": self.route_blocked,
            "needs_analytics": self.needs_analytics,
            "underived": self.underived,
        }

    @classmethod
    def from_json(cls, d: Any) -> "DoorRequirement":
        fields = {f.name for f in dataclasses.fields(cls)}
        if not isinstance(d, Mapping) or set(d) != fields:
            got = sorted(d) if isinstance(d, Mapping) else type(d).__name__
            raise DoorResolutionError(f"a door requirement has exactly the keys {sorted(fields)}, got {got}")
        all_of = d["all_of"]
        if not isinstance(all_of, list) or not all(isinstance(el, list) for el in all_of):
            raise DoorResolutionError(f"all_of must be a list of lists, got {all_of!r}")
        return cls(
            all_of=tuple(tuple(el) for el in all_of),
            route=d["route"],
            collector_target=d["collector_target"],
            route_blocked=d["route_blocked"],
            needs_analytics=d["needs_analytics"],
            underived=d["underived"],
        )


def _optional_str(det: Mapping, key: str) -> Optional[str]:
    val = det.get(key)
    if val is not None and not isinstance(val, str):
        raise DoorResolutionError(f"expected detection {key} must be a string, got {val!r}")
    return val or None


def _resolve_object(det: Mapping, catalog: Any):
    """``(underived code or None, card object, raw card, datasets it reads)``."""
    ttp_ref = _optional_str(det, "ttp_ref")
    detection_id = _optional_str(det, "detection_id")
    if ttp_ref is None:
        return "NO_DATASET", None, None, ()
    raw = catalog.raw(ttp_ref)
    if raw is None:
        return "CARD_NOT_FOUND", None, None, ()
    card = catalog.find(ttp_ref, detection_id)
    kind = _WALK_KIND.get(card.kind) if card is not None else None
    if kind is None:
        return "NO_DATASET", card, raw, ()
    datasets: list[str] = []
    for read in walk_card(ttp_ref, raw):
        if read.kind == kind and read.label == card.name and read.dataset and read.dataset not in datasets:
            datasets.append(read.dataset)
    if not datasets:
        return "NO_DATASET", card, raw, ()
    return None, card, raw, tuple(datasets)


def detection_datasets(det: Mapping, catalog: Any) -> tuple[str, ...]:
    """The datasets this one detection object reads; empty when it reads none or cannot be resolved.

    The detection-level answer: a card can hold several detections on different datasets,
    so a card reading a dataset says nothing about this detection.
    """
    return _resolve_object(det, catalog)[3]


def _named_providers(card: Any, raw: Mapping) -> set[str]:
    """Provider door ids the detection names: its logic's filter, else the card's tags."""
    found: set[str] = set()
    for m in _PROVIDER_FILTER.finditer(card.logic or ""):
        values = [m.group(1)] if m.group(1) is not None else _QUOTED.findall(m.group(2))
        found.update(_PROVIDER_DOOR[v.upper()] for v in values if v.upper() in _PROVIDER_DOOR)
    if found:
        return found
    metadata = raw.get("metadata")
    if metadata is None:
        return found
    if not isinstance(metadata, Mapping):
        raise DoorResolutionError(f"card {card.ttp_ref}: metadata must be a mapping, got {type(metadata).__name__}")
    tags = metadata.get("tags")
    if tags is None:
        return found
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise DoorResolutionError(f"card {card.ttp_ref}: metadata.tags must be a list of strings, got {tags!r}")
    return {_TAG_DOOR[t.lower()] for t in tags if t.lower() in _TAG_DOOR}


def detection_requirement(
    step: Mapping,
    detection: Mapping,
    *,
    catalog: Any,
    registry: Optional[Mapping[str, DatasetRequirement]],
    collector_target: Optional[str] = None,
    collector_dataset: Optional[str] = None,
    xdm_attested: bool = False,
) -> DoorRequirement:
    """The doors one expected detection on one step needs (spec §5 rules 1, 2, 3, 5).

    ``catalog`` is a :class:`engine.ttp_catalog.TtpCatalog`. ``registry`` is
    ``dataset_requirements.load()``; ``None`` (absent) raises, because nothing can
    be derived without it and the caller must render that as degraded.
    ``collector_target`` / ``collector_dataset`` are the target a collector step
    resolved to and that target's dataset; ``xdm_attested`` says an XDM rule maps
    that dataset for Analytics.
    """
    if registry is None:
        raise DoorResolutionError("the dataset requirement registry is absent; no door can be derived")
    if not isinstance(detection, Mapping):
        raise DoorResolutionError(f"an expected detection must be a mapping, got {type(detection).__name__}")
    if collector_dataset is not None and not isinstance(collector_dataset, str):
        raise DoorResolutionError(f"collector_dataset must be a string, got {collector_dataset!r}")
    dtype = detection.get("type")
    if dtype not in DETECTION_TYPES:
        raise DoorResolutionError(f"expected detection type {dtype!r} is not one of {DETECTION_TYPES}")
    needs_analytics = dtype in _ANALYTICS_TYPES
    code, card, raw, datasets = _resolve_object(detection, catalog)

    if signal_of(step)["path"] == "COLLECTOR":
        feeds = (
            dtype not in _UNSTITCHED_TYPES
            and (xdm_attested or not needs_analytics)
            and collector_dataset is not None
            and collector_dataset.lower() in {ds.lower() for ds in datasets}
        )
        return DoorRequirement(
            all_of=((COLLECTOR_DOOR,),), route=ROUTE_COLLECTOR, collector_target=collector_target,
            route_blocked=None if feeds else ROUTE_CANNOT_FEED, needs_analytics=needs_analytics,
        )

    if code is not None:
        return DoorRequirement(needs_analytics=needs_analytics, underived=code)
    rows = [registry.get(ds) for ds in datasets]
    if any(row is None for row in rows):
        # Registry drift, counted like the other gaps so one card cannot abort a
        # whole scenario's readiness. CORTEXSIM_STRICT_DATASETS refuses boot on it.
        return DoorRequirement(needs_analytics=needs_analytics, underived="UNREGISTERED_DATASET")
    if any(row.doors_unknown is not None for row in rows):
        return DoorRequirement(needs_analytics=needs_analytics, underived="DOORS_UNKNOWN")

    providers: Optional[set[str]] = None
    all_of: list[tuple[str, ...]] = []
    for row in rows:
        element = row.doors
        if sum(d in CLOUD_PROVIDER_DOORS for d in element) > 1:
            if providers is None:
                providers = _named_providers(card, raw)
            element = tuple(d for d in element if d not in CLOUD_PROVIDER_DOORS or d in providers)
            if not element:
                return DoorRequirement(needs_analytics=needs_analytics, underived="NO_PROVIDER")
        if element not in all_of:
            all_of.append(element)
    return DoorRequirement(all_of=tuple(all_of), needs_analytics=needs_analytics)


def _judge(req: DoorRequirement, open_keys: set[str], agent_id: Optional[str]) -> tuple[str, tuple[str, ...]]:
    """``(verdict, not-open door ids)``; verdict is one of underived, route_blocked,
    scorable, not_open or unevaluated (an agent-scoped OR-set with no agent to key it).

    ``not_open`` means an OR-set has no door in ``open_keys``. It does not say the
    door is closed: a door nobody has checked is not open either."""
    if req.underived:
        return "underived", ()
    if req.route_blocked:
        return "route_blocked", ()
    not_open: list[str] = []
    unevaluated = False
    for element in req.all_of:
        shut: list[str] = []
        skipped = False
        for door_id in element:
            scope = get_door(door_id).scope
            if scope == "agent" and agent_id is None:
                skipped = True
                continue
            if scope == "target" and req.collector_target is None:
                shut.append(door_id)
                continue
            value = {"agent": agent_id, "target": req.collector_target}.get(scope)
            if door_key(door_id, value) in open_keys:
                break
            shut.append(door_id)
        else:
            if skipped:
                unevaluated = True
            else:
                not_open.extend(d for d in shut if d not in not_open)
    if not_open:
        return "not_open", tuple(not_open)
    return ("unevaluated" if unevaluated else "scorable"), ()


def scenario_readiness(
    scenario: Mapping,
    open_keys: set[str],
    *,
    agent_id: Optional[str],
    catalog: Any,
    registry: Optional[Mapping[str, DatasetRequirement]],
) -> dict[str, Any]:
    """Door readiness of one scenario, derived from its expected detections.

    ``open_keys`` are door keys as :func:`engine.ingestion_doors.door_key` builds
    them; agent-scoped doors are keyed with ``agent_id``. With ``agent_id`` None
    an agent-scoped OR-set is not evaluated (``agent_scoped_evaluated: False``).
    No collector target is resolved here, so every collector-route detection
    counts in ``route_blocked``.

    ``state``: ``ready`` (every derived detection scorable), ``partial`` (some),
    ``needs_doors`` (none), ``not_evaluated`` (no derived detection could be
    judged). ``total = scorable + underived + route_blocked + unevaluated`` plus
    the detections held back by a door that is not open.

    ``not_open`` lists each door that is **not in the open set** (``open_keys``)
    and how many detections it holds back, most first. It does **not**
    distinguish a door that was checked and found closed from one nobody has
    checked: both are simply not open. A caller that needs the difference joins
    each door id against the door's own state (``GET /api/doors``). A detection
    held back by two doors counts once under each, so the per-door counts can sum
    to more than the detections held back.

    ``doors`` is every door id any derived detection names, whatever its state,
    the agent and the collector route: the scenario's detection source. It does
    not change with ``open_keys`` or ``agent_id``, so a door that opens never
    drops out of it the way it drops out of ``not_open``. ``[]`` means no
    detection was derived (every one is ``underived``); it never means "no
    door needed".
    """
    if not isinstance(scenario, Mapping):
        raise DoorResolutionError(f"a scenario must be a mapping, got {type(scenario).__name__}")
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        raise DoorResolutionError(f"scenario {scenario.get('scenario_id')!r}: steps must be a list")
    counts: Counter = Counter()
    not_open: Counter = Counter()
    doors: set[str] = set()
    for step in steps:
        if not isinstance(step, Mapping):
            raise DoorResolutionError(
                f"scenario {scenario.get('scenario_id')!r}: a step must be a mapping, got {type(step).__name__}")
        detections = step.get("expected_detections")
        if detections is None:
            continue
        if not isinstance(detections, list):
            raise DoorResolutionError(
                f"scenario {scenario.get('scenario_id')!r} step {step.get('id')!r}: "
                f"expected_detections must be a list")
        for det in detections:
            req = detection_requirement(step, det, catalog=catalog, registry=registry)
            doors.update(door_id for element in req.all_of for door_id in element)
            verdict, held_by = _judge(req, open_keys, agent_id)
            counts["total"] += 1
            counts[verdict] += 1
            not_open.update(held_by)

    derived = counts["total"] - counts["underived"]
    judged = counts["scorable"] + counts["not_open"] + counts["route_blocked"]
    if judged == 0:
        state = "not_evaluated"
    elif counts["scorable"] == derived:
        state = "ready"
    elif counts["scorable"]:
        state = "partial"
    else:
        state = "needs_doors"
    return {
        "scenario_id": scenario.get("scenario_id"),
        "state": state,
        "scorable": counts["scorable"],
        "total": counts["total"],
        "underived": counts["underived"],
        "route_blocked": counts["route_blocked"],
        "unevaluated": counts["unevaluated"],
        "not_open": [{"door_id": d, "detections": n}
                     for d, n in sorted(not_open.items(), key=lambda kv: (-kv[1], kv[0]))],
        "doors": sorted(doors),
        "agent_scoped_evaluated": agent_id is not None,
    }

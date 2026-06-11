"""
CortexSim detection-plane descriptors.

This package exposes one :class:`~planes.base.PlaneDescriptor` per *active*
detection plane plus a registry (:data:`PLANE_REGISTRY`) keyed by canonical
plane id. Descriptors are declarative metadata only — no execution logic, no
import-time I/O — so the package is safe to import anywhere (API, UI data feed,
tests).

Consumers:

    from planes import PLANE_REGISTRY, get_plane, list_planes

    get_plane("EDR").cortex_engine        # -> "Cortex XDR Agent"
    [p.to_dict() for p in list_planes()]  # -> JSON-serialisable plane catalog

The registry order matches the CLAUDE.md plane table.
"""

from __future__ import annotations

from planes.base import PlaneDescriptor
from planes.edr import EDR_PLANE
from planes.cdr import CDR_PLANE
from planes.ndr import NDR_PLANE
from planes.itdr import ITDR_PLANE
from planes.cspm import CSPM_PLANE
from planes.asm import ASM_PLANE
from planes.tim import TIM_PLANE
from planes.cloud_app import CLOUD_APP_PLANE
from planes.analytics import ANALYTICS_PLANE
from planes.ai_access import AI_ACCESS_PLANE
from planes.airs import AIRS_PLANE
from planes.ai_spm import AI_SPM_PLANE
from planes.browser import BROWSER_PLANE
from planes.koi import KOI_PLANE

#: All active plane descriptors in CLAUDE.md plane-table order.
ALL_PLANES: tuple[PlaneDescriptor, ...] = (
    EDR_PLANE,
    CDR_PLANE,
    NDR_PLANE,
    ITDR_PLANE,
    CSPM_PLANE,
    ASM_PLANE,
    TIM_PLANE,
    CLOUD_APP_PLANE,
    ANALYTICS_PLANE,
    AI_ACCESS_PLANE,
    AIRS_PLANE,
    AI_SPM_PLANE,
    BROWSER_PLANE,
    KOI_PLANE,
)

#: Registry of every active plane keyed by canonical plane id (e.g. ``"EDR"``).
PLANE_REGISTRY: dict[str, PlaneDescriptor] = {p.id: p for p in ALL_PLANES}


def get_plane(plane_id: str) -> PlaneDescriptor | None:
    """Return the descriptor for ``plane_id`` (case-insensitive) or ``None``."""
    if not plane_id:
        return None
    return PLANE_REGISTRY.get(plane_id.upper())


def list_planes() -> list[PlaneDescriptor]:
    """Return all active plane descriptors in plane-table order."""
    return list(ALL_PLANES)


__all__ = [
    "PlaneDescriptor",
    "ALL_PLANES",
    "PLANE_REGISTRY",
    "get_plane",
    "list_planes",
    "EDR_PLANE",
    "CDR_PLANE",
    "NDR_PLANE",
    "ITDR_PLANE",
    "CSPM_PLANE",
    "ASM_PLANE",
    "TIM_PLANE",
    "CLOUD_APP_PLANE",
    "ANALYTICS_PLANE",
    "AI_ACCESS_PLANE",
    "AIRS_PLANE",
    "AI_SPM_PLANE",
    "BROWSER_PLANE",
    "KOI_PLANE",
]

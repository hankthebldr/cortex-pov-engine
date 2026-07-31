"""
CortexSim API — /api/pov router. POV scoping against a tenant entitlement set.

A POV must not propose a scenario the prospect cannot license. This router
answers two questions from the same data:

* what can this tenant actually run today?
* what would they have to add to run the rest?

The second answer is the point. It is an upsell list generated from the POV
itself rather than assembled by hand, and every capability it names carries a
real ``PAN-*`` part number out of the price book.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.uctc_registry import registry
from models import Scenario

logger = logging.getLogger("cortexsim.api.pov")

router = APIRouter(prefix="/pov", tags=["pov"])


# Canned tenant profiles, keyed to the price book's base tiers. Entitlement
# inclusion is the price book's, not the docs' — XSIAM Enterprise bundles
# Endpoint Protection; Premium adds XTI, ASM, and extended retention.
_ENTERPRISE = ["Endpoint Protection"]
_PREMIUM = _ENTERPRISE + ["XTI", "ASM", "Retention"]

CANNED_PROFILES: dict[str, dict[str, list[str]]] = {
    "ng-siem-bare": {
        "base_platform": ["Cortex XSIAM"],
        "addons": [],
    },
    "enterprise": {
        "base_platform": ["Cortex XSIAM", "Cortex XDR"],
        "addons": list(_ENTERPRISE),
    },
    "premium": {
        "base_platform": ["Cortex XSIAM", "Cortex XDR"],
        "addons": list(_PREMIUM),
    },
}


class ScopeRequest(BaseModel):
    """A tenant's entitlement set — either a canned profile or an explicit list."""

    profile: Optional[str] = Field(
        None, description="Canned profile: ng-siem-bare | enterprise | premium"
    )
    base_platform: list[str] = Field(default_factory=list)
    addons: list[str] = Field(default_factory=list)
    planes: list[str] = Field(
        default_factory=list, description="Optional plane filter for the POV"
    )


@router.get("/profiles")
async def list_profiles() -> dict[str, Any]:
    """The canned tenant profiles the scoping endpoint understands."""
    return {"profiles": CANNED_PROFILES}


@router.get("/capabilities")
async def list_capabilities() -> dict[str, Any]:
    """Every licensable capability with its part number, from the price book."""
    return {
        "capabilities": [
            {
                "capability": s.capability,
                "sku_part_number": s.sku_part_number,
                "attaches_to": s.attaches_to,
                "meter": s.meter,
                "note": s.note,
            }
            for s in sorted(registry.all_skus(), key=lambda x: x.capability)
        ],
        "total": len(registry.all_skus()),
    }


@router.post("/scope")
async def scope_pov(body: ScopeRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Split the scenario catalog into what this tenant can run and what it cannot.

    The blocked list carries, per scenario, exactly which capabilities are
    missing — and the ``upsell`` roll-up aggregates those into the shortest set
    of additions that would unblock the most scenarios.
    """
    entitled_base, entitled_addons = _resolve_entitlements(body)

    stmt = select(Scenario)
    if body.planes:
        stmt = stmt.where(Scenario.plane.in_([p.upper() for p in body.planes]))
    scenarios = list((await db.execute(stmt)).scalars().all())

    runnable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    missing_counts: dict[str, int] = {}

    for s in scenarios:
        need_base = list(s.required_base_platform or [])
        need_addons = list(s.required_addons or [])

        # Base platform is satisfied by owning ANY of the listed platforms —
        # the index lists the platforms a use case can land on, not a set the
        # tenant must own in full.
        base_ok = (not need_base) or bool(set(need_base) & set(entitled_base))
        missing_addons = [a for a in need_addons if a not in entitled_addons]
        missing_base = [] if base_ok else need_base

        entry = {
            "scenario_id": s.scenario_id,
            "name": s.name,
            "plane": s.plane,
            "tc_ref": s.tc_ref,
            "moat_tier": s.moat_tier,
            "required_base_platform": need_base,
            "required_addons": need_addons,
        }

        if base_ok and not missing_addons:
            runnable.append(entry)
            continue

        entry["missing_base_platform"] = missing_base
        entry["missing_addons"] = missing_addons
        blocked.append(entry)
        for cap in missing_addons + missing_base:
            missing_counts[cap] = missing_counts.get(cap, 0) + 1

    upsell = [
        {
            "capability": cap,
            "unblocks_scenarios": n,
            "sku_part_number": (registry.sku(cap).sku_part_number
                                if registry.sku(cap) else None),
            "meter": registry.sku(cap).meter if registry.sku(cap) else None,
        }
        for cap, n in sorted(missing_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    logger.info(
        "pov scope profile=%s runnable=%d blocked=%d upsell=%d",
        body.profile, len(runnable), len(blocked), len(upsell),
    )
    return {
        "entitlement": {"base_platform": entitled_base, "addons": entitled_addons,
                        "profile": body.profile},
        "total": len(scenarios),
        "runnable_count": len(runnable),
        "blocked_count": len(blocked),
        "runnable": runnable,
        "blocked": blocked,
        "upsell": upsell,
    }


def _resolve_entitlements(body: ScopeRequest) -> tuple[list[str], list[str]]:
    """Merge a canned profile with any explicit additions. Explicit entries are
    additive so an operator can model 'Enterprise plus ITDR' without restating
    the tier."""
    base: list[str] = []
    addons: list[str] = []
    if body.profile:
        prof = CANNED_PROFILES.get(body.profile)
        if prof:
            base.extend(prof["base_platform"])
            addons.extend(prof["addons"])
    base.extend(b for b in body.base_platform if b not in base)
    addons.extend(a for a in body.addons if a not in addons)
    return base, addons

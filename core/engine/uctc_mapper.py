"""
CortexSim UC/TC Mapper.

Provides lookup functions:
  - Given a uc_ref or tc_ref, return matching scenarios.
  - Given a scenario_id, return the full UC/TC chain view.

All lookups query the Scenario table via SQLAlchemy async session.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("cortexsim.uctc_mapper")


async def get_scenarios_by_uc_ref(
    uc_ref: str,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Return all scenarios with a matching uc_ref."""
    from models import Scenario  # noqa: PLC0415

    result = await db.execute(
        select(Scenario).where(Scenario.uc_ref == uc_ref)
    )
    scenarios = result.scalars().all()
    logger.debug("uc_ref=%s matched %d scenarios", uc_ref, len(scenarios))
    return [s.to_dict() for s in scenarios]


async def get_scenarios_by_tc_ref(
    tc_ref: str,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Return all scenarios with a matching tc_ref."""
    from models import Scenario  # noqa: PLC0415

    result = await db.execute(
        select(Scenario).where(Scenario.tc_ref == tc_ref)
    )
    scenarios = result.scalars().all()
    logger.debug("tc_ref=%s matched %d scenarios", tc_ref, len(scenarios))
    return [s.to_dict() for s in scenarios]


async def get_uctc_chain(
    scenario_id: str,
    db: AsyncSession,
) -> Optional[dict[str, Any]]:
    """
    Return the full UC/TC chain view for a scenario.

    The chain view includes:
      - UC name, UC ref
      - TC name, TC ref
      - MITRE tactic + technique
      - All execution steps with their expected detections
    """
    from models import Scenario  # noqa: PLC0415

    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        logger.warning("uctc_chain: scenario_id=%s not found", scenario_id)
        return None

    chain = {
        "scenario_id": scenario.scenario_id,
        "name": scenario.name,
        "uc": {
            "ref": scenario.uc_ref,
            "name": scenario.uc_name,
        },
        "tc": {
            "ref": scenario.tc_ref,
            "name": scenario.tc_name,
        },
        "mitre": {
            "tactic": scenario.mitre_tactic,
            "tactic_name": scenario.mitre_tactic_name,
            "technique": scenario.mitre_technique,
            "technique_name": scenario.mitre_technique_name,
        },
        "steps": [
            {
                "id": step.get("id"),
                "name": step.get("name"),
                "identity": step.get("identity"),
                "mitre_technique": step.get("mitre_technique"),
                "expected_detections": step.get("expected_detections", []),
            }
            for step in (scenario.steps or [])
        ],
        "detection_types": scenario.detection_types,
        "plane": scenario.plane,
    }
    return chain


async def list_all_uc_refs(db: AsyncSession) -> list[dict[str, str]]:
    """Return a deduplicated list of all UC refs and names across scenarios."""
    from models import Scenario  # noqa: PLC0415

    result = await db.execute(select(Scenario.uc_ref, Scenario.uc_name))
    rows = result.all()
    seen: dict[str, str] = {}
    for uc_ref, uc_name in rows:
        seen[uc_ref] = uc_name
    return [{"uc_ref": k, "uc_name": v} for k, v in sorted(seen.items())]


async def list_all_tc_refs(db: AsyncSession) -> list[dict[str, str]]:
    """Return a deduplicated list of all TC refs and names across scenarios."""
    from models import Scenario  # noqa: PLC0415

    result = await db.execute(select(Scenario.tc_ref, Scenario.tc_name))
    rows = result.all()
    seen: dict[str, str] = {}
    for tc_ref, tc_name in rows:
        seen[tc_ref] = tc_name
    return [{"tc_ref": k, "tc_name": v} for k, v in sorted(seen.items())]

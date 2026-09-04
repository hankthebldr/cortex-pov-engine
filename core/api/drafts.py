"""
CortexSim API — /api/scenarios/drafts router (Composer draft persistence).

A DC composes a from-scratch chain in the Composer console and persists it as a
``Scenario`` row with ``status='draft'``. That row is launchable through the
EXISTING orchestrator path (``Orchestrator.launch`` selects the DB row by id),
so a draft needs no new orchestration substrate — only this CRUD surface plus a
draft-shaped schema (``engine.composer_draft_schema.DraftScenarioSchema``) that
accepts a chain the strict corpus loader would reject for lacking a UC/TC
binding, a version, MITRE metadata, etc.

Honesty doctrine (Gate A5) this module upholds:

  * A draft is NOT corpus coverage. Draft rows live only in ``data/cortexsim.db``
    (never written to ``scenarios/``) and are excluded from the Library list,
    the UC/TC evidence join and MITRE coverage. This module is the ONLY list
    surface that returns drafts.
  * A draft binding is not *proven* until it launches. ``launchable`` here is a
    READ-ONLY mirror of the authoritative launch gate: it computes the same
    ``chain_valid`` + ``tc_bound`` predicates the orchestrator enforces so the
    console can enable/disable the Launch button without attempting a launch.
    It mutates nothing and issues no outbound calls. An ``UNBOUND`` draft, or a
    draft whose index snapshot is absent, reports ``tc_bound=False`` — binding
    CANNOT be proven without the index, so the honest verdict is "not bound",
    never a permissive pass.

Route surface (all under ``/api/scenarios/drafts``):

  POST   /                    — create a draft, 201 + doc + launchable block
  GET    /                    — list drafts (summary), optional ?author=
  GET    /{scenario_id}       — one draft (full) + launchable block
  PUT    /{scenario_id}       — full-replace a draft, 200 + doc + launchable
  DELETE /{scenario_id}       — hard-delete a draft, 204
  GET    /{scenario_id}/launchable — read-only launch verdict

``scenario_id`` policy (pinned): ``SIM-DRAFT-<slug>`` where ``<slug>`` is the
lowercased name with non-alphanumerics collapsed to ``-``. On collision the
router appends ``-2``, ``-3`` … up to a bound, and only then returns
``409 DRAFT_ID_EXISTS`` — friendlier than refusing two drafts that happen to
share a name, and still deterministic.

``author`` is client-provided (default ``composer``); Phase 1 has no
current-user concept, so PUT/DELETE are NOT author-scoped — any caller may edit
any draft.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.composer_draft_schema import (
    DraftScenarioSchema,
    _slug,
    draft_to_orm_kwargs,
    orm_sentinels,
)
from engine.scenario_loader import validate_index_refs
from models import Scenario

# Reuse the Library list's summary projection so a draft summary row is the
# SAME shape as a corpus summary row — one projection, no drift.
from api.scenarios import _slim_scenario

logger = logging.getLogger("cortexsim.api.drafts")

router = APIRouter(prefix="/scenarios/drafts", tags=["drafts"])

#: How many ``-N`` suffixes to try before giving up with 409 DRAFT_ID_EXISTS.
_MAX_ID_SUFFIX = 50


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def _err(status: int, code: str, detail: str, error: Optional[str] = None) -> HTTPException:
    """Build an HTTPException carrying the structured JSON error contract
    ``{error, code, detail}`` used across the API."""
    return HTTPException(
        status_code=status,
        detail={"error": error or code, "code": code, "detail": detail},
    )


def _schema_invalid(exc: Exception) -> HTTPException:
    """Map a pydantic/DraftValidationError to 422 DRAFT_SCHEMA_INVALID."""
    return _err(
        422,
        "DRAFT_SCHEMA_INVALID",
        str(exc),
        error="Draft failed structural validation",
    )


# ---------------------------------------------------------------------------
# Launch verdict — read-only mirror of the orchestrator launch gate
# ---------------------------------------------------------------------------


def _chain_valid(row: Scenario) -> tuple[bool, list[str]]:
    """steps non-empty AND every step has a non-empty command AND >=1 expected
    detection. (Causality-spine validity is guaranteed at save time by
    DraftScenarioSchema; this re-asserts only command + detection presence — the
    belt-and-suspenders the launch gate re-runs.)"""
    reasons: list[str] = []
    steps = row.steps or []
    if not steps:
        reasons.append("chain is empty — a draft must declare at least one step")
        return False, reasons
    for step in steps:
        if not isinstance(step, dict):
            reasons.append("a step is malformed (not an object)")
            continue
        sid = step.get("id", "?")
        if not (step.get("command") or "").strip():
            reasons.append(f"step '{sid}' has an empty command")
        dets = step.get("expected_detections") or []
        if not dets:
            reasons.append(
                f"step '{sid}' has no expected detection — it would execute and "
                f"then be reported as a gap"
            )
    return (not reasons), reasons


def _tc_bound(row: Scenario) -> tuple[bool, list[str]]:
    """Bind the draft's ``(uc_ref, tc_ref, tc_refs)`` against the FY27 index via
    the ONE shared validator. tc_bound = a real test case resolved AND no FK
    errors. The UNBOUND sentinel dangles (S-10) → not bound; an absent snapshot
    yields ``tc is None`` → not bound (binding cannot be proven without the
    index). Never gated on ``CORTEXSIM_STRICT_REFS`` — this is an honesty gate,
    not the corpus-boot strictness switch."""
    tc_refs = list(row.tc_refs) if row.tc_refs else [row.tc_ref]
    tc, errors = validate_index_refs(
        uc_ref=row.uc_ref,
        tc_ref=row.tc_ref,
        tc_refs=tc_refs,
        artifact_id=f"draft={row.scenario_id}",
        source="composer-launch",
    )
    if errors:
        return False, list(errors)
    if tc is None:
        return False, [
            "bind tc_ref to a real FY27 index test case "
            "(the UC/TC index binding is unverified — sentinel 'UNBOUND' or the "
            "index snapshot is not loaded)"
        ]
    return True, []


def _launchable(row: Scenario) -> dict[str, Any]:
    """Compute the launch verdict block returned alongside every draft doc and
    by ``GET /{id}/launchable``. Mirrors the orchestrator's DRAFT_NOT_TC_BOUND
    gate exactly. Mutates nothing; makes no outbound calls."""
    chain_valid, chain_reasons = _chain_valid(row)
    tc_bound, tc_reasons = _tc_bound(row)
    launchable = chain_valid and tc_bound
    reasons = [*chain_reasons, *tc_reasons]
    return {
        "launchable": launchable,
        "chain_valid": chain_valid,
        "tc_bound": tc_bound,
        "reasons": reasons,
        "refusal_code": None if launchable else "DRAFT_NOT_TC_BOUND",
    }


def _draft_doc(row: Scenario) -> dict[str, Any]:
    """Full draft document = Scenario.to_dict() + the launchable verdict block."""
    doc = row.to_dict()
    doc["launchable"] = _launchable(row)
    return doc


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def _resolve_draft(db: AsyncSession, scenario_id: str) -> Scenario:
    """Fetch a row that MUST be an existing draft, else 404 DRAFT_NOT_FOUND.

    An active/deprecated corpus id returns 404 here on purpose — those are
    fetched via ``GET /api/scenarios/{id}`` and are never mutated by this router.
    """
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    row: Optional[Scenario] = result.scalar_one_or_none()
    if row is None or row.status != "draft":
        raise _err(
            404,
            "DRAFT_NOT_FOUND",
            f"no draft with scenario_id='{scenario_id}'",
            error="Draft not found",
        )
    return row


async def _pick_scenario_id(db: AsyncSession, name: str) -> str:
    """Derive ``SIM-DRAFT-<slug>`` and, on collision, append ``-2``, ``-3`` …
    up to ``_MAX_ID_SUFFIX``; then 409 DRAFT_ID_EXISTS."""
    base = f"SIM-DRAFT-{_slug(name)}"
    for suffix in range(1, _MAX_ID_SUFFIX + 1):
        candidate = base if suffix == 1 else f"{base}-{suffix}"
        exists = (
            await db.execute(
                select(Scenario.id).where(Scenario.scenario_id == candidate)
            )
        ).first()
        if exists is None:
            return candidate
    raise _err(
        409,
        "DRAFT_ID_EXISTS",
        f"could not derive a free scenario_id from name='{name}' "
        f"(tried {base} .. {base}-{_MAX_ID_SUFFIX})",
        error="Draft id collision",
    )


def _validate_body(body: dict[str, Any]) -> DraftScenarioSchema:
    """Validate a request body through DraftScenarioSchema, mapping any failure
    to 422 DRAFT_SCHEMA_INVALID (bad step/detection shape, broken causality
    spine, duplicate step id, empty steps, empty detection union)."""
    try:
        return DraftScenarioSchema.model_validate(body)
    except ValidationError as exc:
        raise _schema_invalid(exc)
    except ValueError as exc:  # DraftValidationError and friends
        raise _schema_invalid(exc)


# The mutable columns a PUT full-replace overwrites. scenario_id, status,
# created_at and author are immutable (author is stamped at create time).
_MUTABLE_COLUMNS = (
    "name",
    "plane",
    "detection_types",
    "uc_ref",
    "tc_ref",
    "tc_refs",
    "uc_name",
    "tc_name",
    "pov_scenario_id",
    "mitre_tactic",
    "mitre_tactic_name",
    "mitre_technique",
    "mitre_technique_name",
    "additional_techniques",
    "execution_identity",
    "push_supported",
    "pull_supported",
    "external_tools",
    "steps",
    "cleanup",
    "tags",
    "cgo_anchor",
    "validation_methodology",
    "methodology_family",
    "primary_kpi",
    "threshold",
    "success_criteria",
    "moat_tier",
    "correlation_window_seconds",
    "stitching_key",
    "required_planes_in_incident",
    "required_base_platform",
    "required_addons",
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_draft(
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    """Create a draft. Validates the body via DraftScenarioSchema, persists ONE
    Scenario row (status='draft', filling every nullable=False column a
    from-scratch draft lacks with the sentinels in
    ``composer_draft_schema.orm_sentinels``, tags always including
    'composer-draft'), and returns 201 with the full draft doc + launchable
    block."""
    draft = _validate_body(body)
    author = draft.author or (body.get("author") if isinstance(body, dict) else None) or "composer"

    scenario_id = await _pick_scenario_id(db, draft.name)
    kwargs = draft_to_orm_kwargs(draft, author=author, scenario_id=scenario_id)

    row = Scenario(**kwargs)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    logger.info("create_draft scenario_id=%s author=%s", scenario_id, author)
    return _draft_doc(row)


@router.get("")
async def list_drafts(
    author: Optional[str] = Query(None, description="Filter to a single author"),
    db: AsyncSession = Depends(get_db),
):
    """List drafts (summary projection). Optional ?author= filter. This is the
    ONLY list surface that returns drafts — the Library list
    (GET /api/scenarios) excludes them by construction."""
    stmt = select(Scenario).where(Scenario.status == "draft")
    if author:
        stmt = stmt.where(Scenario.author == author)
    rows = (await db.execute(stmt)).scalars().all()
    logger.info("list_drafts author=%s count=%d", author, len(rows))
    return {
        "drafts": [_slim_scenario(r) for r in rows],
        "total": len(rows),
        "projection": "summary",
    }


@router.get("/{scenario_id}")
async def get_draft(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return one draft (full document + launchable block). 404 for a missing
    id or a non-draft row."""
    row = await _resolve_draft(db, scenario_id)
    logger.info("get_draft scenario_id=%s", scenario_id)
    return _draft_doc(row)


@router.put("/{scenario_id}")
async def update_draft(
    scenario_id: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    """Full-replace a draft's mutable columns. scenario_id / status / created_at
    / author are immutable. 404 DRAFT_NOT_FOUND for a missing id; 409
    DRAFT_NOT_EDITABLE if the row exists but is not a draft; 422
    DRAFT_SCHEMA_INVALID on a bad body."""
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    row: Optional[Scenario] = result.scalar_one_or_none()
    if row is None:
        raise _err(
            404,
            "DRAFT_NOT_FOUND",
            f"no draft with scenario_id='{scenario_id}'",
            error="Draft not found",
        )
    if row.status != "draft":
        raise _err(
            409,
            "DRAFT_NOT_EDITABLE",
            f"scenario_id='{scenario_id}' is status='{row.status}', not a draft; "
            f"this route never mutates a corpus row",
            error="Draft not editable",
        )

    draft = _validate_body(body)
    # Re-derive the full ORM kwargs (detection_types, push_supported, sentinels)
    # from the new body, then overwrite ONLY the mutable columns — identity,
    # status and provenance stay put.
    kwargs = draft_to_orm_kwargs(
        draft, author=row.author, scenario_id=scenario_id
    )
    for col in _MUTABLE_COLUMNS:
        if col in kwargs:
            setattr(row, col, kwargs[col])

    await db.commit()
    await db.refresh(row)
    logger.info("update_draft scenario_id=%s", scenario_id)
    return _draft_doc(row)


@router.delete("/{scenario_id}", status_code=204)
async def delete_draft(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a draft row. 404 DRAFT_NOT_FOUND for a missing id; 409
    DRAFT_NOT_DELETABLE if the row exists but is not a draft — this route can
    never delete an active/deprecated corpus row even if its id is passed."""
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    row: Optional[Scenario] = result.scalar_one_or_none()
    if row is None:
        raise _err(
            404,
            "DRAFT_NOT_FOUND",
            f"no draft with scenario_id='{scenario_id}'",
            error="Draft not found",
        )
    if row.status != "draft":
        raise _err(
            409,
            "DRAFT_NOT_DELETABLE",
            f"scenario_id='{scenario_id}' is status='{row.status}', not a draft; "
            f"this route never deletes a corpus row",
            error="Draft not deletable",
        )
    await db.delete(row)
    await db.commit()
    logger.info("delete_draft scenario_id=%s", scenario_id)
    return None


@router.get("/{scenario_id}/launchable")
async def draft_launchable(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Read-only mirror of the authoritative launch gate. Returns the SAME
    chain_valid + tc_bound predicates the orchestrator enforces so the console
    can enable/disable the Launch button without attempting a launch. Mutates
    nothing; issues no outbound calls. 404 for a missing or non-draft id."""
    row = await _resolve_draft(db, scenario_id)
    logger.info("draft_launchable scenario_id=%s", scenario_id)
    return _launchable(row)

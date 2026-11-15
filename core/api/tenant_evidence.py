"""
CortexSim API — /api/runs/{run_id}/tenant-evidence (spec §7.4).

Endpoints:
  POST /api/runs/{run_id}/tenant-evidence[?force=&integration=]
       — read the run's issues, cases and endpoint record from the tenant
         (outbound, read-only, metered) and store the snapshot
  GET  /api/runs/{run_id}/tenant-evidence
       — the latest stored snapshot, the last ten reads and the case ledger;
         no outbound call

Mounted as a second router under the ``/runs`` prefix, the same pattern as the
storyline and reconcile routers. Context only: nothing here feeds a verdict,
an outcome or MTTD. Errors are structured JSON ``{"error", "code", "detail"}``.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Run

logger = logging.getLogger("cortexsim.api.tenant_evidence")

router = APIRouter(prefix="/runs", tags=["tenant-evidence"])


async def _run_or_404(db: AsyncSession, run_id: str) -> Run:
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail={
            "error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"})
    return run


@router.post("/{run_id}/tenant-evidence")
async def read_tenant_evidence(
    run_id: str,
    force: bool = Query(False, description=(
        "Read even inside CORTEXSIM_TENANT_EVIDENCE_MIN_INTERVAL_SECONDS")),
    integration: Optional[str] = Query(None, description=(
        "integration name (defaults to the first that may serve the xsiam connector)")),
    db: AsyncSession = Depends(get_db),
):
    """Read this run's tenant evidence (explicit operator action; read-only).

    200 always carries ``{run_id, read, status, code, detail,
    retry_after_seconds, snapshot, issues}``. No credential: ``pending`` /
    ``NO_TENANT_CONFIGURED`` with ``issues: null``. A run with no executed step:
    ``NO_RUN_WINDOW``, no request. Inside the minimum interval:
    ``read: false`` / ``TENANT_EVIDENCE_READ_RECENTLY`` and the latest snapshot,
    unless ``force=true``. 404 ``RUN_NOT_FOUND``; 404 ``INTEGRATION_NOT_FOUND``;
    500 ``CREDENTIAL_ERROR``.
    """
    from api.connectors import _RECONCILE_ERROR_STATUS  # noqa: PLC0415
    from connectors.service import ReconcileError  # noqa: PLC0415
    from connectors.tenant_evidence import read_run_evidence  # noqa: PLC0415

    run = await _run_or_404(db, run_id)
    try:
        out = await read_run_evidence(db, run, integration_name=integration, force=force)
    except ReconcileError as e:
        raise HTTPException(status_code=_RECONCILE_ERROR_STATUS.get(e.code, 500), detail={
            "error": str(e), "code": e.code, "detail": e.detail})
    logger.info("tenant-evidence run=%s read=%s code=%s queries=%s", run_id, out["read"],
                out["code"], (out.get("snapshot") or {}).get("queries_issued"))
    return out


@router.get("/{run_id}/tenant-evidence")
async def get_tenant_evidence(run_id: str, db: AsyncSession = Depends(get_db)):
    """The latest stored tenant evidence for this run. Makes no outbound call.

    ``{run_id, status, code, snapshot, history[], case_ledger}``; never read:
    ``snapshot: null`` / ``TENANT_EVIDENCE_NOT_READ``. 404 ``RUN_NOT_FOUND``.
    """
    from connectors.tenant_evidence import latest_tenant_evidence  # noqa: PLC0415

    await _run_or_404(db, run_id)
    return await latest_tenant_evidence(db, run_id)

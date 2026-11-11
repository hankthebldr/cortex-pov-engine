"""CortexSim API — /api/collector-targets.

The tenant-side ingestion doors this instance's emitters POST to: the XSIAM
HTTP log collector (``https://api-<tenant>/logs/v1/event``), or a forwarder
in front of it. Broker VM targets are not modelled yet (spec §10). A SimCore
instance serves one POV, so these are entered once and every campaign launch,
campaign preflight, offline bundle and Composer EAL dispatch resolves its
destination from them (``eal_simulator.targets``).

  GET    /api/collector-targets               list + which collector plugins nothing serves
  GET    /api/collector-targets/{name}        one target
  PUT    /api/collector-targets/{name}        create or replace (key write-only)
  DELETE /api/collector-targets/{name}        remove the target and its key
  POST   /api/collector-targets/{name}/preflight   configured -> reachable -> accepted

The key goes to the encrypted vault and is never returned — not by value, not
by tail. The operator API is unauthenticated, so the guarantee is the key's
absence from every response, not access control.

Spec: docs/superpowers/specs/2026-11-10-collector-targets-design.md
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from eal_simulator import get_default_registry
from eal_simulator.target_preflight import preflight_target
from eal_simulator.targets import NAME_RE, TargetSpec, TargetSpecError, collector_plugin_names
from engine import collector_targets as store

router = APIRouter(prefix="/collector-targets", tags=["collector-targets"])


class TargetPut(BaseModel):
    kind: str
    url: str
    dataset: Optional[str] = Field(default=None, max_length=200)
    auth_header: str = Field(default="Authorization", min_length=1, max_length=100)
    auth_scheme: str = Field(default="", max_length=40)
    plugins: list[str] = Field(default_factory=list)
    is_default: bool = False
    token: Optional[SecretStr] = None
    clear_token: bool = False


class PreflightBody(BaseModel):
    write_canary: bool = False


def preflight_transports() -> tuple[Any, Any]:
    """(connect, client_factory) for the preflight ladder. ``(None, None)``
    uses the real network; tests override this dependency."""
    return None, None


def _err(status: int, code: str, error: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status,
                         detail={"error": error, "code": code, "detail": detail})


def _check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise _err(422, "TARGET_NAME_INVALID", "Invalid target name",
                   f"{name!r} must match {NAME_RE.pattern}")


async def _row_or_404(db: AsyncSession, name: str):
    row = await store.get_row(db, name)
    if row is None:
        raise _err(404, "TARGET_NOT_FOUND", "Collector target not found", f"target='{name}'")
    return row


def _public(row) -> dict[str, Any]:
    out = row.to_dict()
    out["warnings"] = store.to_spec(row).warnings()
    return out


@router.get("")
async def list_targets(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    rows = await store.list_rows(db)
    collector_plugins = sorted(collector_plugin_names(get_default_registry()))
    served = {p for r in rows for p in (r.plugins or [])}
    default = next((r.name for r in rows if r.is_default), None)
    unbound = [] if default else [p for p in collector_plugins if p not in served]
    payload: dict[str, Any] = {
        "targets": [_public(r) for r in rows],
        "count": len(rows),
        "default_target": default,
        "collector_plugins": collector_plugins,
        "unbound_plugins": unbound,
        "status": "ok" if rows else "degraded",
        "code": None if rows else "NO_COLLECTOR_TARGETS",
    }
    if not rows:
        payload["detail"] = ("No collector target is configured, so no collector step "
                             "can run live. Add the tenant's HTTP log collector.")
    return payload


@router.get("/{name}")
async def get_target(name: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return _public(await _row_or_404(db, name))


@router.put("/{name}")
async def put_target(name: str, body: TargetPut,
                     db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    _check_name(name)
    if body.token is not None and body.clear_token:
        raise _err(422, "TARGET_INVALID", "Invalid collector target",
                   "send either a new token or clear_token, not both")
    token = body.token.get_secret_value() if body.token is not None else None
    if token is not None and not token.strip():
        raise _err(422, "TARGET_INVALID", "Invalid collector target", "token is empty")

    existing = await store.get_row(db, name)
    will_have_key = token is not None or (
        existing is not None and existing.secret_name is not None and not body.clear_token)
    try:  # validates kind + URL with the same rules the resolver reads them by
        TargetSpec(name=name, kind=body.kind, url=body.url, dataset=body.dataset,
                   auth_header=body.auth_header, auth_scheme=body.auth_scheme,
                   plugins=tuple(body.plugins), is_default=body.is_default,
                   has_token=will_have_key)
    except TargetSpecError as exc:
        raise _err(422, "TARGET_INVALID", "Invalid collector target", str(exc)) from exc

    collector_plugins = collector_plugin_names(get_default_registry())
    bad = sorted(set(body.plugins) - collector_plugins)
    if bad:
        raise _err(422, "TARGET_PLUGIN_NOT_COLLECTOR", "Plugin does not POST to a collector",
                   f"{bad} are not collector plugins; choose from {sorted(collector_plugins)}")
    for other in await store.list_rows(db):
        clash = sorted(set(body.plugins) & set(other.plugins or [])) if other.name != name else []
        if clash:
            raise _err(422, "TARGET_PLUGIN_ALREADY_BOUND", "Plugin already served",
                       f"{clash} already go to target '{other.name}'; unbind them there first")

    row, previous_default = await store.upsert(
        db, name, kind=body.kind, url=body.url, dataset=body.dataset,
        auth_header=body.auth_header, auth_scheme=body.auth_scheme,
        plugins=list(dict.fromkeys(body.plugins)), is_default=body.is_default,
        token=token, clear_token=body.clear_token,
    )
    out = _public(row)
    out["previous_default"] = previous_default
    return out


@router.delete("/{name}", status_code=204)
async def delete_target(name: str, db: AsyncSession = Depends(get_db)) -> Response:
    if not await store.delete(db, name):
        raise _err(404, "TARGET_NOT_FOUND", "Collector target not found", f"target='{name}'")
    return Response(status_code=204)


@router.post("/{name}/preflight")
async def preflight(
    name: str,
    body: PreflightBody,
    db: AsyncSession = Depends(get_db),
    transports: tuple[Any, Any] = Depends(preflight_transports),
) -> dict[str, Any]:
    """Walk the ladder. Writes one record into the tenant only when
    ``write_canary`` is true — opening the page never does."""
    row = await _row_or_404(db, name)
    spec = store.to_spec(row)
    tokens = await store.load_tokens(db, [name]) if row.secret_name else {}
    connect, client_factory = transports
    result = await preflight_target(
        spec=spec, token=tokens.get(name), write_canary=body.write_canary,
        connect=connect, client_factory=client_factory,
    )
    await store.record_check(db, name, result)
    return result

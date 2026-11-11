"""NGFW (PAN-OS) registration, keygen, per-agent source overrides and preflight.

Spec: docs/superpowers/specs/2026-11-10-ngfw-connection-wizard-design.md §4, §5.1.

Read-only toward the firewall in every path. ``write_enabled`` is reported ``false``
unconditionally: ``CORTEXSIM_PANOS_ALLOW_WRITE`` exists in settings but nothing
consumes it, and this surface must not read a flag as a capability it does not have.

Password hygiene (§4.2). ``POST /api/ngfw/keygen`` carries a firewall admin password.
It is parsed inside the handler, not by FastAPI's body binding, because FastAPI's
default 422 body echoes every invalid field's ``input`` (and the request body), which
would put the password in a response. The handler's own 422 names only the field
location and error type. The password is held as ``SecretStr``, sent once in the
keygen form body, never logged, never stored, and the handler drops its reference
after the call. Every failure maps to a fixed message.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.preflight import BLOCKED, SKIPPED, Stage
from database import get_db
from integrations.panos import PANOS_KIND, codes
from integrations.panos.client import PanosClient
from integrations.panos.exceptions import (
    PanosConfigError, PanosShapeError, PanosTransportError, PanosWriteRefused)
from integrations.panos.host import validate_panos_config
from integrations.panos.preflight import (
    FIREWALL_STAGE_IDS, REM_SKIPPED_CONFIG, FirewallPreflightReport, run_panos_preflight)
from integrations.panos.sources import AgentRef
from models import Agent
from security.credentials import CredentialStore

logger = logging.getLogger("cortexsim.api.ngfw")

router = APIRouter(prefix="/ngfw", tags=["ngfw"])

#: Test seam: an ``httpx.AsyncBaseTransport`` (e.g. ``httpx.MockTransport``) used by
#: both keygen and preflight. Production leaves it ``None`` (the real transport).
_transport_override: Optional[httpx.AsyncBaseTransport] = None

_TYPE_HINT = "panos_api_key_credential"

REM_NO_INTEGRATION = (
    "Register the firewall first (keygen in the wizard, or PUT "
    "/api/credentials/integrations with kind panos_ngfw). With none registered nothing "
    "about the firewall's logging or policy path has been checked.")
REM_CREDENTIAL_UNDECRYPTABLE = (
    "CORTEXSIM_SECRET was rotated or the ciphertext is corrupt. Generate a new API key "
    "for this firewall (keygen) or re-enter it.")

_KEYGEN_FAIL: dict[str, tuple[int, str, str]] = {
    codes.PN_KEYGEN_REJECTED: (
        401, "Firewall rejected the credentials",
        "The firewall did not issue an API key for these credentials. Check the "
        "username and password, and that the admin role allows XML API access."),
    codes.PN_UNREACHABLE: (
        502, "Firewall management interface unreachable",
        "No answer from the management host. Check the address, port, routing and "
        "that HTTPS management is enabled on that interface."),
    codes.PN_TLS_FAILED: (
        502, "TLS verification of the management certificate failed",
        "The management certificate did not verify. Install a trusted certificate, or "
        "register with verify_tls=false for a lab firewall with a self-signed one."),
    codes.PN_UPSTREAM_ERROR: (
        502, "Firewall management API returned an error",
        "The management host answered with an HTTP error to the keygen request."),
    codes.PN_SHAPE_UNRECOGNISED: (
        502, "Firewall keygen reply not recognised",
        "The management host answered, but not with a PAN-OS keygen response."),
}


# ── request models ─────────────────────────────────────────────────────────


class KeygenBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(..., min_length=1, max_length=200)
    host: str = Field(..., min_length=1, max_length=300)
    verify_tls: bool = True
    username: str = Field(..., min_length=1, max_length=200)
    password: SecretStr = Field(..., min_length=1)
    vsys: str = Field(default="vsys1", max_length=32)


class SourcesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_source: Optional[str] = None
    agent_addresses: Optional[list[str]] = None


def _err(status: int, error: str, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error, "code": code,
                                                     "detail": detail})


def _invalid_body(errors: list[dict[str, Any]]) -> HTTPException:
    # loc + type only: msg/input/ctx can carry the submitted value.
    fields = [{"loc": [str(p) for p in e.get("loc", ())], "type": str(e.get("type", ""))}
              for e in errors]
    return HTTPException(status_code=422, detail={
        "error": "Request body invalid", "code": "REQUEST_INVALID",
        "detail": "fields: " + ", ".join(".".join(f["loc"]) or "(body)" for f in fields),
        "fields": fields})


# ── GET /api/ngfw ──────────────────────────────────────────────────────────


@router.get("")
async def list_firewalls(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Registered firewalls, metadata only. Never the key."""
    rows = await CredentialStore(db).list_integrations(kind=PANOS_KIND)
    firewalls = []
    for row in rows:
        cfg = row.config or {}
        firewalls.append({
            "name": row.name,
            "host": cfg.get("host"),
            "verify_tls": cfg.get("verify_tls", True),
            "vsys": cfg.get("vsys", "vsys1"),
            "forwarding_path": cfg.get("forwarding_path", "auto"),
            "last_verified_ok": row.last_verified_ok,
            "last_verified_error": row.last_verified_error,
            "last_verified_at": (row.last_verified_at.isoformat()
                                 if row.last_verified_at else None),
        })
    # write_enabled is a constant: nothing here can write, whatever the env says.
    return {"firewalls": firewalls, "write_enabled": False, "tenant_verified": 0}


# ── POST /api/ngfw/keygen ──────────────────────────────────────────────────


@router.post("/keygen")
async def keygen(request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Exchange admin credentials for an API key and register the firewall (§4.2)."""
    try:
        raw = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        raise _invalid_body([{"loc": ("body",), "type": "json_invalid"}]) from None
    try:
        body = KeygenBody.model_validate(raw)
    except ValidationError as exc:
        raise _invalid_body(exc.errors(include_input=False, include_context=False,
                                       include_url=False)) from None
    finally:
        raw = None  # noqa: F841 — drop the plaintext dict

    store = CredentialStore(db)
    existing = await store.get_integration(body.name)
    if existing is not None and existing.kind != PANOS_KIND:
        raise _err(409, "Integration name already in use", "INTEGRATION_KIND_CONFLICT",
                   f"'{body.name}' is registered as kind '{existing.kind}', not "
                   f"'{PANOS_KIND}'; choose another name")
    # Re-keygen of a registered firewall keeps its operator-entered settings
    # (source overrides, probes, zones); host always, and verify_tls / vsys when sent
    # (or on first registration, where the defaults apply), come from here.
    base = dict(existing.config or {}) if existing is not None else {}
    overrides: dict[str, Any] = {"host": body.host}
    for key_name in ("verify_tls", "vsys"):
        if existing is None or key_name in body.model_fields_set:
            overrides[key_name] = getattr(body, key_name)
    try:
        cfg = validate_panos_config({**base, **overrides})
    except PanosConfigError as exc:
        raise _err(400, "NGFW config rejected", "INTEGRATION_CONFIG_INVALID",
                   str(exc)) from None

    name = body.name
    password = body.password
    username = body.username
    body = None  # noqa: F841 — the model holds the password; release it
    client = PanosClient(cfg["host"], None, verify_tls=cfg["verify_tls"],
                         transport=_transport_override, max_calls=1)
    try:
        key = await client.keygen(username, password)
    except (PanosTransportError, PanosShapeError, PanosWriteRefused) as exc:
        code = _keygen_code(exc)
        status, error, detail = _KEYGEN_FAIL[code]
        logger.info("ngfw keygen failed name=%s code=%s", name, code)
        raise _err(status, error, code, detail) from None
    finally:
        password = None  # noqa: F841
        username = None  # noqa: F841
        await client.aclose()

    row = await store.put_integration(name=name, kind=PANOS_KIND, plaintext_secret=key,
                                      config=cfg, secret_type_hint=_TYPE_HINT)
    key = None  # noqa: F841
    await db.commit()
    logger.info("ngfw keygen stored name=%s host=%s", row.name, cfg["host"])
    return row.to_dict()


def _keygen_code(exc: BaseException) -> str:
    if isinstance(exc, PanosTransportError) and exc.code in _KEYGEN_FAIL:
        return exc.code
    if isinstance(exc, PanosShapeError):
        return codes.PN_SHAPE_UNRECOGNISED
    return codes.PN_UPSTREAM_ERROR


# ── PUT /api/ngfw/{name}/sources/{agent_id} ────────────────────────────────


async def _panos_row(store: CredentialStore, name: str):
    row = await store.get_integration(name)
    if row is None or row.kind != PANOS_KIND:
        raise _err(404, "Integration not found", "INTEGRATION_NOT_FOUND",
                   f"kind={PANOS_KIND} name={name}")
    return row


@router.put("/{name}/sources/{agent_id}")
async def put_sources(name: str, agent_id: str, body: SourcesBody,
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Set or clear one agent's firewall-observed source / agent-side addresses (§5.4).

    Per firewall, never global. A field left out is unchanged; ``null`` (or an empty
    address list) removes the override. The merged config is validated before it is
    written, and the secret and ``last_verified_*`` are untouched.
    """
    store = CredentialStore(db)
    row = await _panos_row(store, name)
    cfg = dict(row.config or {})
    observed = dict(cfg.get("observed_sources") or {})
    addresses = dict(cfg.get("agent_addresses") or {})
    sent = body.model_fields_set
    if "observed_source" in sent:
        if body.observed_source is None:
            observed.pop(agent_id, None)
        else:
            observed[agent_id] = body.observed_source
    if "agent_addresses" in sent:
        if not body.agent_addresses:
            addresses.pop(agent_id, None)
        else:
            addresses[agent_id] = list(body.agent_addresses)
    try:
        valid = validate_panos_config({**cfg, "observed_sources": observed,
                                       "agent_addresses": addresses})
    except PanosConfigError as exc:
        raise _err(400, "NGFW source override rejected", "INTEGRATION_CONFIG_INVALID",
                   str(exc)) from None
    await store.update_integration_config(name, {
        "observed_sources": valid["observed_sources"],
        "agent_addresses": valid["agent_addresses"]})
    await db.commit()
    return {"firewall": name, "agent_id": agent_id,
            "observed_source": valid["observed_sources"].get(agent_id),
            "agent_addresses": valid["agent_addresses"].get(agent_id)}


# ── preflight (dispatched from POST /api/connectors/panos_ngfw/preflight) ──


def _blocked_report(firewall: Optional[str], code: str, detail: str,
                    remediation: str) -> FirewallPreflightReport:
    report = FirewallPreflightReport(firewall=firewall, mgmt_host=None)
    report.stages.append(Stage("config", BLOCKED, code, detail, remediation=remediation))
    for sid in FIREWALL_STAGE_IDS[1:]:
        report.stages.append(Stage(sid, SKIPPED, codes.PN_SKIPPED_CONFIG, "not checked",
                                   remediation=REM_SKIPPED_CONFIG))
    report.sources = [{"code": codes.PN_SKIPPED_CONFIG, "status": SKIPPED,
                       "detail": "source checks not run: the config stage is blocked"}]
    return report


def _first_blocked_code(report: FirewallPreflightReport) -> Optional[str]:
    for st in report.stages:
        if st.status == BLOCKED:
            return st.code
    for src in report.sources:
        for st in src.get("stages") or []:
            if (st or {}).get("status") == BLOCKED:
                return st.get("code")
        if src.get("status") == BLOCKED:
            return src.get("code")
    return None


async def _agent_refs(db: AsyncSession, agents: Optional[str]) -> list[AgentRef]:
    rows = (await db.execute(select(Agent).order_by(Agent.agent_id))).scalars().all()
    wanted = {a.strip() for a in (agents or "").split(",") if a.strip()} if agents else None
    return [AgentRef(agent_id=r.agent_id, hostname=r.hostname, last_ip=r.last_ip)
            for r in rows if wanted is None or r.agent_id in wanted]


async def preflight_panos(db: AsyncSession, integration: Optional[str],
                          agents: Optional[str]) -> dict[str, Any]:
    """Resolve the firewall, run the staged preflight, record the verdict."""
    from config import settings  # noqa: PLC0415

    store = CredentialStore(db)
    rows = await store.list_integrations(kind=PANOS_KIND)  # ordered by name
    if not rows:
        return _blocked_report(integration, codes.PN_NO_INTEGRATION,
                               f"no integration of kind '{PANOS_KIND}' is registered",
                               REM_NO_INTEGRATION).to_dict()
    row = (next((r for r in rows if r.name == integration), None)
           if integration else rows[0])
    if row is None:
        raise _err(404, "Integration not found", "INTEGRATION_NOT_FOUND",
                   f"kind={PANOS_KIND} name={integration}")
    credential = {"name": row.name, "kind": row.kind}

    try:
        secret = await store.get_integration_secret(row.name)
    except Exception:  # noqa: BLE001 — a rotated master key is a config answer
        report = _blocked_report(row.name, codes.PN_CREDENTIAL_UNDECRYPTABLE,
                                 "the stored API key could not be decrypted",
                                 REM_CREDENTIAL_UNDECRYPTABLE)
    else:
        report = await run_panos_preflight(
            row.config or {}, secret, firewall=row.name,
            agents=await _agent_refs(db, agents), transport=_transport_override,
            max_calls=settings.CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT)
        secret = None  # noqa: F841

    overall = report.overall
    await store.mark_integration_verified(
        row.name, ok=overall != BLOCKED,
        error=_first_blocked_code(report) if overall == BLOCKED else None)
    await db.commit()
    out = report.to_dict()
    out["credential"] = credential
    return out

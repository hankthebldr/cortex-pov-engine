"""
The payload shelf — `/api/shelf/*`, plus the K8s-namespaced alias `/api/k8s/*`.

WHY THIS EXISTS
---------------
Every tier-4 adapter pack fetches its tool from the PUBLIC INTERNET **on the
target host** at dispatch. The customers who buy Cortex run default-deny
egress, so that is the first thing their network blocks — and a step whose tool
never arrived runs anyway, produces no detection, and the absent detection reads
in a POV report as "Cortex missed it". The shelf moves that dependency onto the
DC's OWN SimCore, where it is already accepted, and makes the bytes
checksummable before they reach a customer.

THE SHELF IS PLANE-AGNOSTIC. Three consumers pull from it:

  * the **Go beacon** (pull mode) — an EDR-plane endpoint runs the tool;
  * the **K8s init container** (served delivery) — a CDR/cloud-EDR pod does;
  * the **console** — preflight and composition.

It was born K8s-namespaced (`/api/k8s/payload/{name}`) and is now served under
`/api/shelf/*`. **The `/api/k8s/*` alias stays mounted forever**: every manifest
this engine has ever emitted hard-codes those paths inside
`k8s_manifest._SERVED_FETCH`, and a manifest is a file that lives in a customer's
GitOps repo. Both prefixes are the SAME handler functions registered twice — not
a redirect (a redirect would make a stale manifest silently keep working) and not
a copy.

It mirrors the agent-binary idiom in `core/api/agents.py` deliberately — one
plural inventory endpoint, one singular `FileResponse` carrying an
`X-CortexSim-*-SHA256` header, one bare-hex `/sha256` endpoint, and a 404 whose
`detail` names the shelf directory AND the command that fills it. There is one
distribution idiom in this codebase, not two:

    /api/agents/binaries          /api/shelf/payloads
    /api/agents/binary            /api/shelf/payload/{name}
    /api/agents/binary/sha256     /api/shelf/payload/{name}/sha256

THE DELIVERY CONTRACT, AND ITS ONE EXCEPTION
--------------------------------------------
A generated push bundle MUST execute on a clean host with no SimCore dependency
at runtime. The bash and PowerShell bundles hold that absolutely, and the K8s
*embedded* delivery (the default) holds it too — the payload travels inline in a
ConfigMap. Only `delivery="served"` relaxes it, and only because staged tool
artifacts (linpeas.sh is ~800 KB) exceed what a ConfigMap can carry. When that
relaxation applies, the manifest says so in four places (`k8s_manifest.py`
§build_header) and these endpoints are what it points at.

INTEGRITY IS ANCHORED IN THE MANIFEST, NOT HERE
-----------------------------------------------
`GET /api/k8s/payload/{name}/sha256` and `.../bootstrap/{id}/sha256` exist for
humans and for `curl` debugging. **The pod must not use them.** A digest fetched
from the same server that served the file proves nothing — a substituted server
serves a matching pair. The expected digests are baked into the manifest at
generation time on the DC's own SimCore and travel with the file in the
`integrity.env` ConfigMap; the manifest is the out-of-band anchor. This diverges
from the agent installer, which legitimately does fetch its digest because it
has no such anchor. Do not "unify" the two — see `k8s-delivery.md` §5.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Scenario

# Reuse the agent shelf's digest helper rather than forking it. The design brief
# wanted the canonical copy lifted into a shared engine module; that file is
# owned by another track, so we import the one implementation instead of growing
# a second. It caches on (path, mtime_ns, size), which invalidates the instant a
# payload is re-staged.
from api.agents import _sha256_of as sha256_of

logger = logging.getLogger(__name__)

#: The K8s-namespaced router. Carries the genuinely Kubernetes-specific
#: endpoints (bootstrap, posture vocabulary) AND the shelf routes, which stay
#: here forever because emitted manifests name them.
router = APIRouter(prefix="/k8s", tags=["k8s"])

#: The plane-agnostic shelf. Same handler functions, second mount.
shelf_router = APIRouter(prefix="/shelf", tags=["shelf"])


# ---------------------------------------------------------------------------
# The shelf
# ---------------------------------------------------------------------------

#: A caller supplies this filename, unlike `/api/agents/binary` which keys on a
#: closed os/arch enum. Traversal is therefore REACHABLE here and has to be
#: closed explicitly — the regex is the first gate, `resolve_payload`'s
#: containment assertion is the second.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Files the shelf carries for its own bookkeeping. They are not payloads and
#: must never be listed or served as one.
_SHELF_METADATA = frozenset({
    "MANIFEST.json", "SHA256SUMS", "README.md", "sources.json", ".gitignore",
    # engine.payload_shelf.STUB_SHELF_MARKER — bookkeeping, never servable.
    ".cortexsim-stub-shelf",
})


def payload_dist_dir() -> Path:
    """Directory holding the staged tool artifacts.

    ``CORTEXSIM_PAYLOAD_DIST`` wins so a DC can point at an scp'd drop on an
    air-gapped jumpbox without rebuilding the image; otherwise it is
    ``<BASE_DIR>/payloads``, which is where `scripts/build-payloads.sh` writes
    and what `core/Dockerfile` bakes in.
    """
    override = os.environ.get("CORTEXSIM_PAYLOAD_DIST")
    if override:
        return Path(override)
    from config import settings  # noqa: PLC0415 — avoid an import cycle at module load

    return Path(settings.CORTEXSIM_BASE_DIR) / "payloads"


def _bad_name(name: str) -> HTTPException:
    return HTTPException(status_code=400, detail={
        "error": "Invalid payload name",
        "code": "BAD_PAYLOAD_NAME",
        "detail": (
            f"'{name[:80]}' is not a payload name. Names match "
            f"{_NAME_RE.pattern} — a bare filename, no path separators and no "
            f"'..' segments."
        ),
    })


def resolve_payload(name: str) -> Path:
    """Map a caller-supplied name onto a path INSIDE the shelf, or 400.

    Two independent gates, because one of them being wrong must not be enough:
    the character class rejects separators and dot-segments outright, and the
    resolved path is then asserted to sit under the resolved shelf root (which
    also catches a symlink pointed out of the tree).
    """
    if not _NAME_RE.match(name or ""):
        raise _bad_name(name or "")
    if name in _SHELF_METADATA:
        raise _bad_name(name)
    root = payload_dist_dir().resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        # Unreachable via _NAME_RE today; kept because the containment check is
        # the property we actually care about, not the regex that implies it.
        raise _bad_name(name)
    return candidate


def _shelf_manifest() -> dict[str, Any]:
    """`MANIFEST.json` written by scripts/build-payloads.sh, or {}.

    Provenance only (source_url / license / pinned). It is NEVER trusted for a
    digest: the endpoints always recompute from the bytes on disk, so a stale or
    hand-edited manifest cannot make a tampered file verify.
    """
    path = payload_dist_dir() / "MANIFEST.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("payload shelf MANIFEST.json is unreadable at %s — serving without provenance", path)
        return {}
    return data if isinstance(data, dict) else {}


def inventory() -> list[dict[str, Any]]:
    """Everything servable on the shelf, sorted by name.

    An empty list is a VALID state — the manifest generator then reports every
    declared tool as unstaged rather than failing, exactly as
    `_available_binaries()` degrades for the beacon shelf.
    """
    dist = payload_dist_dir()
    if not dist.is_dir():
        return []
    provenance = {
        str(p.get("name")): p
        for p in (_shelf_manifest().get("payloads") or [])
        if isinstance(p, dict)
    }
    out: list[dict[str, Any]] = []
    for path in sorted(dist.iterdir(), key=lambda p: p.name):
        if not path.is_file() or path.name in _SHELF_METADATA:
            continue
        if not _NAME_RE.match(path.name):
            # A file we could never serve (the name would 400) must not appear
            # in an inventory a DC reads as "these are available".
            logger.warning("payload shelf holds an unservable filename: %s", path.name)
            continue
        st = path.stat()
        meta = provenance.get(path.name, {})
        out.append({
            "name": path.name,
            "filename": path.name,
            "size_bytes": st.st_size,
            "sha256": sha256_of(path),
            "modified_at": datetime.utcfromtimestamp(st.st_mtime).isoformat(),
            "source_url": meta.get("source_url"),
            "license": meta.get("license"),
            "pinned": meta.get("pinned"),
        })
    return out


def staged_names() -> set[str]:
    return {p["name"] for p in inventory()}


def _resolve_payload_or_404(name: str) -> Path:
    path = resolve_payload(name)
    if not path.is_file():
        have = sorted(staged_names())
        raise HTTPException(status_code=404, detail={
            "error": "Payload not staged",
            "code": "PAYLOAD_UNAVAILABLE",
            "detail": (
                f"'{name}' is not in {payload_dist_dir()}. Available: "
                f"{have or 'none'}. Stage it on the SimCore host with "
                f"`./scripts/build-payloads.sh`, or rebuild the image (core/Dockerfile "
                f"copies payloads/ to /app/payloads), or scp the artifact in and set "
                f"CORTEXSIM_PAYLOAD_DIST."
            ),
        })
    return path


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
#
# The honest position, stated plainly because it is a real divergence from the
# design brief's preference:
#
# The emitted manifest's served-fetch script (k8s_manifest._SERVED_FETCH) sends
# NO Authorization header — it is a plain `wget`. That contract is fixed and
# this track cannot change it. So defaulting these endpoints to token-required
# would 403 every served manifest this engine emits: a manufactured failure, and
# the exact class of outcome this whole design exists to prevent. Default is
# therefore `open`, and the consequence is documented rather than hidden:
#
#   *** THE MANIFEST IS A FILE YOU HAND A CUSTOMER. ***  It names your SimCore
#   URL, it will be committed to a GitOps repo, pasted into a ticket and
#   forwarded. With auth open, anything that can reach that URL can pull the
#   staged offensive tooling off the shelf. Put SimCore on a network the
#   customer's cluster can reach and a stranger cannot, or set
#   CORTEXSIM_K8S_PAYLOAD_AUTH=token for the artifact endpoints once the
#   manifest track can carry a credential.
#
# `token` mode is implemented and tested so the switch exists the moment the
# manifest can send a header. It gates ONLY the artifact endpoints
# (`/payload/{name}`, `/payload/{name}/sha256`), never `/payloads` — the
# inventory is the reachability probe the init container curls, and a probe that
# can fail for two different reasons is a debugging trap.

_AUTH_ENV = "CORTEXSIM_K8S_PAYLOAD_AUTH"
_TOKENS_ENV = "CORTEXSIM_K8S_PAYLOAD_TOKENS"


def _auth_mode() -> str:
    mode = (os.environ.get(_AUTH_ENV) or "open").strip().lower()
    return mode if mode in ("open", "token") else "open"


def _configured_tokens() -> list[str]:
    raw = os.environ.get(_TOKENS_ENV) or ""
    return [t for t in (part.strip() for part in raw.split(",")) if t]


_WRITE_TOKENS_ENV = "CORTEXSIM_SHELF_WRITE_TOKENS"


def _require_write_secret(request: Request) -> None:
    """Gate the shelf's write path, independent of the read-path auth mode.

    Falls back to the read-path token list so an operator who has already
    configured `CORTEXSIM_K8S_PAYLOAD_TOKENS` does not have to configure a
    second secret to keep using the console. With NEITHER set the endpoint
    refuses — fail closed, and say exactly which variable to set.
    """
    tokens = [
        t for t in (
            part.strip()
            for part in os.getenv(_WRITE_TOKENS_ENV, "").split(",")
        ) if t
    ] or _configured_tokens()
    if not tokens:
        raise HTTPException(status_code=403, detail={
            "error": "Shelf writes are not configured",
            "code": "SHELF_WRITE_UNCONFIGURED",
            "detail": (
                f"POST to the shelf makes this SimCore fetch an arbitrary URL and "
                f"write the bytes into the tooling directory it serves to target "
                f"hosts, so it is never open by default. Set {_WRITE_TOKENS_ENV} "
                f"(or {_TOKENS_ENV}) to a secret and send it as "
                f"`Authorization: Bearer <token>`. Staging without an API is "
                f"always available: ./scripts/build-payloads.sh on this host."
            ),
        })
    header = request.headers.get("authorization") or ""
    scheme, _, presented = header.partition(" ")
    ok = False
    if scheme.lower() == "bearer" and presented:
        for candidate in tokens:
            if secrets.compare_digest(presented, candidate):
                ok = True
    if not ok:
        raise HTTPException(status_code=403, detail={
            "error": "Shelf write denied",
            "code": "SHELF_WRITE_DENIED",
            "detail": (
                f"this endpoint requires a bearer token from {_WRITE_TOKENS_ENV} "
                f"(or {_TOKENS_ENV})."
            ),
        })


def _require_payload_token(request: Request, *, write: bool = False) -> None:
    """Enforce `CORTEXSIM_K8S_PAYLOAD_AUTH=token` on the artifact endpoints.

    ``write=True`` enforces UNCONDITIONALLY, ignoring ``open`` mode. The
    argument for defaulting the shelf open is about the READ path — a pod's bare
    ``wget`` has nowhere to carry a credential, and the task queue already hands
    the same TTPs to any caller. None of that extends to a WRITE endpoint that
    only the console calls: ``POST /stage`` makes SimCore fetch an arbitrary URL
    and persist the bytes into the directory of offensive tooling a DC then
    ships to a customer endpoint. Open-by-default there is an unauthenticated
    fetch-and-plant primitive.
    """
    if write:
        _require_write_secret(request)
        return
    if _auth_mode() != "token":
        return
    tokens = _configured_tokens()
    if not tokens:
        # Fail CLOSED and say why. Enabling the gate and leaving it unconfigured
        # must not silently serve the shelf to everyone.
        raise HTTPException(status_code=500, detail={
            "error": "Payload auth misconfigured",
            "code": "PAYLOAD_AUTH_MISCONFIGURED",
            "detail": (
                f"{_AUTH_ENV}=token but {_TOKENS_ENV} is empty, so no request could "
                f"ever be authorised. Set {_TOKENS_ENV} to a comma-separated list of "
                f"secrets, or set {_AUTH_ENV}=open."
            ),
        })
    header = request.headers.get("authorization") or ""
    scheme, _, presented = header.partition(" ")
    ok = False
    if scheme.lower() == "bearer" and presented:
        for candidate in tokens:
            # compare_digest on every candidate (no early break) so the response
            # time does not leak which prefix matched.
            if secrets.compare_digest(presented, candidate):
                ok = True
    if not ok:
        # One opaque message: wrong-vs-expired is not distinguished, same rule
        # the enroll endpoint already follows.
        raise HTTPException(status_code=403, detail={
            "error": "Payload token denied",
            "code": "PAYLOAD_TOKEN_DENIED",
            "detail": (
                "this SimCore requires a bearer token on the payload artifact "
                "endpoints. Send `Authorization: Bearer <token>` with a value from "
                f"{_TOKENS_ENV}."
            ),
        })


def _warn_if_shelf_is_open() -> None:
    """Boot WARN — an unauthenticated shelf with real tooling on it is a fact an
    operator should learn from the log, not from an incident."""
    try:
        if _auth_mode() == "open":
            count = len(inventory())
            if count:
                logger.warning(
                    "payload shelf: %d artifact(s) in %s are served UNAUTHENTICATED "
                    "(%s=open). The generated manifest names this SimCore URL and is a "
                    "file you hand a customer — keep SimCore on a network the cluster "
                    "can reach and a stranger cannot.",
                    count, payload_dist_dir(), _AUTH_ENV,
                )
    except Exception:  # pragma: no cover - a log line must never break boot
        logger.debug("payload shelf warn check failed", exc_info=True)


# ---------------------------------------------------------------------------
# Inventory + reachability probe
# ---------------------------------------------------------------------------


def _declared_index() -> dict[str, Any]:
    """What the adapter packs declare, keyed by shelf filename.

    Derived from `install.artifact`, never from `sources.json` — the generated
    file is a projection of the packs, so reading it here would reintroduce the
    second list this design exists to remove. Degrades to {} when the catalog is
    not loaded (a bare unit-test app), because an inventory that 500s because the
    tool catalog is cold would break the reachability probe.
    """
    try:
        from engine.payload_shelf import declared_artifacts  # noqa: PLC0415

        return {a.name: a for a in declared_artifacts()}
    except Exception:  # noqa: BLE001 - never let derivation break the probe
        logger.debug("payload shelf: adapter-derived declaration unavailable", exc_info=True)
        return {}


async def list_payloads():
    """Inventory of staged tool artifacts, and the manifest's reachability probe.

    ALWAYS unauthenticated (`k8s-delivery.md` §5). The init container's first
    action is `wget -O /dev/null $SERVER/api/k8s/payloads`, and it reports a
    failure here as SIMCORE_UNREACHABLE. If a credential could also fail this
    call, that message would send a DC to argue with the customer's network team
    over an auth problem.

    `declared[]` is the other half of the answer and the reason a console can
    show what is MISSING rather than only what is present: every artifact the
    adapter packs bind, with `staged` telling you whether the bytes are here.
    """
    payloads = inventory()
    declared = _declared_index()
    staged = {p["name"] for p in payloads}
    for p in payloads:
        d = declared.get(p["name"])
        p["adapter_id"] = getattr(d, "adapter_id", None)
    return {
        "payloads": payloads,
        "total": len(payloads),
        "dist_dir": str(payload_dist_dir()),
        "auth": _auth_mode(),
        "writable": os.access(payload_dist_dir(), os.W_OK),
        "declared": [
            {
                "name": d.name,
                "url": d.url,
                "kind": d.kind,
                "sha256": d.sha256,
                "license": d.license,
                "adapter_id": d.adapter_id,
                "pin_type": d.pin_type,
                "stage_path": d.stage_path,
                "mode": d.mode,
                "staged": d.name in staged,
            }
            for d in sorted(declared.values(), key=lambda x: x.name)
        ],
    }


async def download_payload(name: str, request: Request):
    """Serve a staged tool artifact.

    The digest is echoed in `X-CortexSim-Payload-SHA256` so a client streaming
    the body can verify without a second round trip. A CONSUMER must compare
    against the digest it carried in (the manifest's `integrity.env`, or the
    beacon's task payload), not against this header.

    `Cache-Control: no-store` is not decoration: an intermediate proxy caching a
    payload across a re-stage serves stale bytes that then fail the consumer's
    digest check, and the resulting mismatch message points at "an intercepting
    proxy" — a wrong diagnosis manufactured by our own cache headers.
    """
    _require_payload_token(request)
    path = _resolve_payload_or_404(name)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={
            "X-CortexSim-Payload-SHA256": sha256_of(path),
            "X-CortexSim-Payload-Name": path.name,
            "Cache-Control": "no-store",
        },
    )


async def payload_sha256(name: str, request: Request):
    """Bare hex digest of a staged artifact.

    Plain text, not JSON, so `curl … | read` needs no parser. FOR HUMANS AND
    DEBUGGING ONLY — see the module docstring on why a consumer fetching its
    expected digest from the server that served the file verifies nothing.
    """
    _require_payload_token(request)
    path = _resolve_payload_or_404(name)
    return PlainTextResponse(sha256_of(path) + "\n")


def _register_shelf_routes(r: APIRouter) -> None:
    """Register the three artifact routes on a router.

    ONE implementation, two mounts. `/api/k8s/*` is what every emitted manifest
    names and must never move; `/api/shelf/*` is what the beacon and the console
    use. A redirect between them was rejected: it would make a stale manifest
    silently keep working, so the drift would never be discovered.
    """
    r.get("/payloads")(list_payloads)
    r.get("/payload/{name}")(download_payload)
    r.get("/payload/{name}/sha256", response_class=PlainTextResponse)(payload_sha256)


_register_shelf_routes(router)
_register_shelf_routes(shelf_router)


# ---------------------------------------------------------------------------
# The bootstrap — generated, not stored
# ---------------------------------------------------------------------------


async def _scenario_or_404(scenario_id: str, db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(select(Scenario).where(Scenario.scenario_id == scenario_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "Scenario not found",
            "code": "SCENARIO_NOT_FOUND",
            "detail": f"scenario_id='{scenario_id}'",
        })
    return row.to_dict()


def guard_declared_payloads(scenario: dict[str, Any]) -> None:
    """Refuse to serve a bootstrap whose scenario declares an UNSTAGED tool.

    This is the false-negative closer, and it is the reason this endpoint is
    more than a `FileResponse`. `cluster_posture.payloads` names tool artifacts
    the scenario needs; a non-empty list is exactly what forces
    `delivery="served"`. If one of them was never staged onto this shelf, the
    pod would run the attack WITHOUT the tool, every step would look like it
    executed, and the missing detection would read as "Cortex missed it" — a
    manufactured false negative, one layer below a silent no-op.

    Refusing here converts that into a loud, diagnosable pod failure: the init
    container's bootstrap fetch fails and it dies with BOOTSTRAP_FETCH_FAILED /
    "NOTHING RAN" on `/dev/termination-log`, and the DC sees this 409 the moment
    they curl the endpoint by hand.
    """
    from engine.k8s_manifest import parse_posture  # noqa: PLC0415 — heavy import, request-time only

    posture = parse_posture(scenario)
    declared = list(posture.payloads) if posture else []
    if not declared:
        return
    have = staged_names()
    missing = [n for n in declared if n not in have]
    if not missing:
        return
    raise HTTPException(status_code=409, detail={
        "error": "Declared payload not staged",
        "code": "PAYLOAD_NOT_STAGED",
        "detail": (
            f"{scenario.get('scenario_id', '?')} declares cluster_posture.payloads "
            f"{declared} but {missing} are not on the shelf at {payload_dist_dir()} "
            f"(staged: {sorted(have) or 'none'}). Refusing to serve a bootstrap that "
            f"would run the scenario WITHOUT its tooling — every step would look like "
            f"it executed and the absent detection would read as a miss. Stage them "
            f"with `./scripts/build-payloads.sh` (declare them in payloads/sources.json) "
            f"or scp them into CORTEXSIM_PAYLOAD_DIST, then re-apply the manifest."
        ),
        "missing": missing,
        "staged": sorted(have),
    })


def _bootstrap_or_error(scenario: dict[str, Any]) -> str:
    """Render the bootstrap, translating an unsatisfiable target into a 409.

    A pod must never receive a script that parses and then dies in front of a
    customer, so this mirrors what `format=k8s` already does at download time.
    """
    from engine.k8s_manifest import generate_bootstrap  # noqa: PLC0415
    from engine.push_generator import BundleTargetUnsatisfiable  # noqa: PLC0415

    guard_declared_payloads(scenario)
    try:
        return generate_bootstrap(scenario)
    except BundleTargetUnsatisfiable as exc:
        raise HTTPException(status_code=409, detail=exc.to_error()) from exc


@router.get("/bootstrap/{scenario_id}")
async def download_bootstrap(scenario_id: str, db: AsyncSession = Depends(get_db)):
    """The in-pod payload for `scenario_id` — exactly `generate_bootstrap`.

    Deterministic by contract: the same scenario always renders the same bytes,
    because the manifest bakes this script's sha256 at generation time. Never
    add a timestamp, a uuid or unsorted iteration to the body — generation-time
    metadata belongs in the manifest.

    Unauthenticated even in `token` mode, because the emitted served-fetch
    script cannot send a header (see the auth section above). Gating it would
    break every served manifest.
    """
    scenario = await _scenario_or_404(scenario_id, db)
    body = _bootstrap_or_error(scenario)
    from engine.k8s_manifest import bootstrap_digest  # noqa: PLC0415

    return PlainTextResponse(
        body,
        media_type="text/x-shellscript; charset=utf-8",
        headers={
            "X-CortexSim-Bootstrap-SHA256": bootstrap_digest(scenario),
            "Content-Disposition": 'attachment; filename="bootstrap.sh"',
        },
    )


@router.get("/bootstrap/{scenario_id}/sha256", response_class=PlainTextResponse)
async def bootstrap_sha256(scenario_id: str, db: AsyncSession = Depends(get_db)):
    """Bare hex digest of the bootstrap. Humans and `curl` only — the pod
    compares against the digest baked into its own manifest."""
    scenario = await _scenario_or_404(scenario_id, db)
    _bootstrap_or_error(scenario)  # same refusals, so /sha256 can never disagree with the body
    from engine.k8s_manifest import bootstrap_digest  # noqa: PLC0415

    return PlainTextResponse(bootstrap_digest(scenario) + "\n")


# ---------------------------------------------------------------------------
# Posture-finding vocabulary
# ---------------------------------------------------------------------------


@router.get("/posture-findings")
async def posture_findings():
    """The derived posture-finding vocabulary a manifest can plant.

    Served straight from `k8s_manifest.vocabulary()` — derived from each
    finding's `emitted_when` predicate, never hand-listed here, so this endpoint
    cannot drift from what the builder actually emits.
    """
    from engine.k8s_manifest import vocabulary  # noqa: PLC0415

    items = vocabulary()
    return {"findings": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Composition — the compose-time refusal surface
# ---------------------------------------------------------------------------
#
# THE RULE THE OTHER THREE TRACKS BUILD AGAINST: an artifact that cannot be
# resolved is refused HERE, before a Run row exists and before a manifest is
# emitted. A consumer must never discover it. A beacon that skips a step, or
# runs it without the tool, produces "the TTP ran and Cortex detected nothing" —
# a manufactured false negative on the customer's stack, in a report a DC shows
# a customer. That is worse than any crash.


class ComposeRequest(BaseModel):
    scenario_id: Optional[str] = None
    adapter_refs: list[str] = Field(default_factory=list)
    #: adapter_id (or bare shelf filename) -> absolute destination on the target.
    #: THE RENAME CONTROL: stage linpeas as /tmp/.cache/sysinfo.sh and the two
    #: filename-keyed BIOCs in the corpus correctly go dark while the behavioural
    #: ones must still fire.
    stage_as: dict[str, str] = Field(default_factory=dict)
    consumer: str = "beacon"
    server: Optional[str] = None


def _resolution_http_error(exc) -> HTTPException:
    return HTTPException(status_code=getattr(exc, "http_status", 409), detail=exc.to_error())


def _server_for(request: Request, override: Optional[str]) -> str:
    from api.agents import _resolve_server  # noqa: PLC0415 — one resolver, not two

    return _resolve_server(request, override)


async def _compose(
    request: Request,
    *,
    scenario: Optional[dict[str, Any]],
    adapter_refs: list[str],
    stage_as: dict[str, str],
    consumer: str,
    server: Optional[str],
) -> dict[str, Any]:
    from engine.payload_shelf import PayloadResolutionError, compose  # noqa: PLC0415

    try:
        composition = compose(
            scenario=scenario,
            adapter_refs=adapter_refs,
            overrides=stage_as,
            consumer=consumer,
            server_url=_server_for(request, server),
        )
    except PayloadResolutionError as exc:
        raise _resolution_http_error(exc) from exc
    body = composition.to_dict()
    body["shelf_dir"] = str(payload_dist_dir())
    body["staged"] = sorted(staged_names())
    if not composition.air_gapped:
        body["remediation"] = (
            "Declare an install.artifact block on the listed adapter pack(s) and "
            "stage it with ./scripts/build-payloads.sh, or accept that the target "
            "host needs public-internet egress for those tools."
        )
    return body


@shelf_router.post("/compose")
async def compose_payloads(body: ComposeRequest, request: Request,
                           db: AsyncSession = Depends(get_db)):
    """Resolve a digest-bound composition plan, or refuse.

    Every artifact in a 200 carries a NON-EMPTY sha256 — there is no partial
    plan. A blank digest makes a consumer's `[ "$ACT" = "$WANT" ]` compare
    against "" and pass on anything, which is how an integrity check silently
    becomes a no-op.
    """
    scenario = await _scenario_or_404(body.scenario_id, db) if body.scenario_id else None
    return await _compose(
        request, scenario=scenario, adapter_refs=body.adapter_refs,
        stage_as=body.stage_as, consumer=body.consumer, server=body.server,
    )


@shelf_router.get("/resolve/{scenario_id}")
async def resolve_scenario(scenario_id: str, request: Request,
                           consumer: str = "console", server: Optional[str] = None,
                           db: AsyncSession = Depends(get_db)):
    """Console preflight: what would this scenario carry, and can it be air-gapped?

    REPORTS rather than refuses on the egress gap — its job is to tell a DC
    BEFORE they walk into a default-deny network. It still refuses (409) on an
    unstaged or pin-mismatched artifact, because that is not a warning, it is a
    plan that cannot be executed honestly.
    """
    scenario = await _scenario_or_404(scenario_id, db)
    return await _compose(
        request, scenario=scenario, adapter_refs=[], stage_as={},
        consumer=consumer, server=server,
    )


@shelf_router.get("/artifacts")
async def list_declared_artifacts(db: AsyncSession = Depends(get_db)):
    """Everything the adapter packs declare — the DERIVED staging list.

    This is the source of truth `payloads/sources.json` is generated from. The
    `unstaged` list is the honest accounting: an artifact declared by a pack and
    absent from this shelf means any composition naming it will 409.

    ``scenarios=`` is load-bearing and was once omitted here: the CLI passed it
    and this endpoint did not, so the generated sources.json correctly said
    ``linpeas.sh -> SIM-EDR-022`` while the console — which reads THIS — told a
    DC "no scenario references TOOL-LINPEAS yet" for the very scenario built to
    demonstrate the feature. A false claim of no-coverage on the primary surface
    reads as broken wiring and sends the operator hunting for a bug that is not
    there.
    """
    from engine.payload_shelf import declared_artifacts  # noqa: PLC0415
    from models import Scenario  # noqa: PLC0415

    rows_result = await db.execute(select(Scenario.scenario_id, Scenario.external_tools))
    scenarios = [
        {"scenario_id": sid, "external_tools": tools or []}
        for sid, tools in rows_result.all()
    ]
    declared = declared_artifacts(scenarios=scenarios)
    staged = staged_names()
    rows = [{
        "name": d.name,
        "url": d.url,
        "kind": d.kind,
        "sha256": d.sha256,
        "pinned": d.pinned,
        "pin_type": d.pin_type,
        "pin_ref": d.pin_ref,
        "license": d.license,
        "adapter_id": d.adapter_id,
        "stage_path": d.stage_path,
        "mode": d.mode,
        "used_by": d.used_by,
        "staged": d.name in staged,
    } for d in declared]
    return {
        "artifacts": rows,
        "total": len(rows),
        "staged": sorted(n for n in staged),
        "unstaged": sorted(r["name"] for r in rows if not r["staged"]),
        "unpinned": sorted(r["name"] for r in rows if not r["pinned"]),
        "dist_dir": str(payload_dist_dir()),
    }


# ---------------------------------------------------------------------------
# Staging — the console pulls the public tool onto the DC's own host
# ---------------------------------------------------------------------------
#
# THE EGRESS BOUNDARY, stated in the API's own text and not only in a design
# doc: this endpoint makes an outbound HTTP request FROM THE SIMCORE PROCESS, on
# the DC's host. That is the accepted boundary and the entire point of the
# shelf — it moves the public-internet dependency off the customer's
# cluster/endpoint (where default-deny blocks it) onto the DC's host (where it
# is already accepted). SimCore is NOT a proxy: it never fetches on a consumer's
# behalf at execution time.
#
# Synchronous on purpose. A job table would be a second stateful subsystem with
# its own lifecycle, garbage collection and orphan story, for an operation that
# takes ~2 seconds on a 1 MB file. A spinner is cheaper than a queue nobody
# reaps.
#
# It deliberately does NOT write payloads/sources.json. That file is GENERATED
# from the adapter packs; an API that appended to it would fork the source of
# truth back into two lists. Instead the response carries `pack_snippet` — the
# exact `install.artifact` YAML to paste into the pack — which routes the
# operator to the durable declaration rather than around it.

_STAGE_TIMEOUT_SECONDS = 120
#: Hops this endpoint will follow BY HAND, re-running the SSRF gate on each one.
_STAGE_MAX_REDIRECTS = 5
_STAGE_MAX_BYTES = int(os.environ.get("CORTEXSIM_SHELF_MAX_BYTES") or 64 * 1024 * 1024)


class StageRequest(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    sha256: Optional[str] = None
    license: Optional[str] = None
    adapter_id: Optional[str] = None
    replace: bool = False


def _stage_error(status: int, code: str, error: str, detail: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={
        "error": error, "code": code, "detail": detail, **extra,
    })


def _egress_allowed() -> bool:
    return (os.environ.get("CORTEXSIM_SHELF_EGRESS") or "allow").strip().lower() != "deny"


def _guard_stage_url(url: str) -> None:
    """Scheme + SSRF gate. RFC1918 is ALLOWED (an internal Artifactory is a
    legitimate source) but loopback and the cloud metadata addresses are not."""
    import ipaddress  # noqa: PLC0415
    import socket  # noqa: PLC0415
    from urllib.parse import urlparse  # noqa: PLC0415

    parsed = urlparse(url or "")
    allow_http = (os.environ.get("CORTEXSIM_SHELF_ALLOW_HTTP") or "0") == "1"
    if parsed.scheme not in ("https",) and not (allow_http and parsed.scheme == "http"):
        raise _stage_error(400, "BAD_PAYLOAD_URL", "Refusing to stage from this URL", (
            f"scheme {parsed.scheme!r} is not allowed. Use https (or set "
            f"CORTEXSIM_SHELF_ALLOW_HTTP=1 for a lab mirror). file:, ftp:, data: "
            f"and gopher: are never accepted."))
    host = parsed.hostname or ""
    if not host:
        raise _stage_error(400, "BAD_PAYLOAD_URL", "Refusing to stage from this URL",
                           f"{url!r} has no host.")
    if host in ("metadata.google.internal", "metadata"):
        raise _stage_error(400, "BAD_PAYLOAD_URL", "Refusing to stage from this URL",
                           f"{host} is a cloud metadata endpoint.")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise _stage_error(400, "BAD_PAYLOAD_URL", "Refusing to stage from this URL",
                           f"{host} does not resolve: {exc}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_loopback or addr.is_link_local or str(addr) in ("169.254.169.254", "fd00:ec2::254"):
            raise _stage_error(400, "BAD_PAYLOAD_URL", "Refusing to stage from this URL", (
                f"{host} resolves to {addr}, which is loopback/link-local or a "
                f"cloud metadata address. Staging from there would make SimCore "
                f"an SSRF pivot into its own host."))


def _default_stage_client():  # pragma: no cover - replaced in tests
    import httpx  # noqa: PLC0415

    # follow_redirects=False is LOAD-BEARING: `stage_payload` follows hops
    # itself so `_guard_stage_url` runs on every one. Turning this back on
    # silently restores an SSRF bypass that no test can see.
    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(connect=10.0, read=float(_STAGE_TIMEOUT_SECONDS),
                              write=10.0, pool=10.0),
    )


#: Injectable so tests never touch the network — same seam `connectors` uses.
stage_client_factory = _default_stage_client


def _pack_snippet(name: str, url: str, digest: str, license_: Optional[str]) -> str:
    return (
        "install:\n"
        f"  binary: /tmp/{name}\n"
        "  artifact:\n"
        f"    filename: {name}\n"
        f"    url: \"{url}\"\n"
        "    kind: file\n"
        f"    sha256: \"{digest}\"\n"
        "    pin: { type: release-tag, ref: \"<TAG>\" }\n"
        f"    license: {license_ or '<SPDX>'}\n"
        f"    stage_path: /tmp/{name}\n"
        "    mode: \"0755\"\n"
    )


@shelf_router.post("/stage", status_code=201)
async def stage_payload(body: StageRequest, request: Request):
    """Pull a public tool onto THIS SimCore's shelf.

    `{"adapter_id": "TOOL-LINPEAS"}` alone is the preferred form: name, url,
    digest and licence then come from the pack's `install.artifact`, so the
    staged bytes are the bytes the pack was authored against. A URL is never
    parsed out of `install.runtime_install_command` — a shell string that
    silently yields the wrong URL is exactly how staging becomes a no-op.
    """
    _require_payload_token(request, write=True)
    if not _egress_allowed():
        raise _stage_error(409, "SHELF_EGRESS_DISABLED", "Shelf staging is disabled", (
            "CORTEXSIM_SHELF_EGRESS=deny, so this SimCore will not make an "
            "outbound request. This is the air-gapped posture. Stage on a "
            "networked host with ./scripts/build-payloads.sh, copy payloads/ "
            "across, and point CORTEXSIM_PAYLOAD_DIST at it — or rebuild the "
            "image, which COPYs payloads/ in."))

    name, url, pin, license_ = body.name, body.url, body.sha256, body.license
    if body.adapter_id:
        from tools.adapter_catalog import catalog  # noqa: PLC0415

        adapter = catalog.find(body.adapter_id)
        if adapter is None:
            raise _stage_error(404, "ADAPTER_NOT_FOUND", "Unknown adapter",
                               f"{body.adapter_id!r} is not in the tool-adapter catalog.")
        art = adapter.install.artifact
        if art is None:
            raise _stage_error(400, "ADAPTER_HAS_NO_ARTIFACT", "Adapter declares no artifact", (
                f"{body.adapter_id} has no install.artifact block, so there is "
                f"nothing to stage. Add one to its pack under tools/packs/ — the "
                f"URL is never guessed from install.runtime_install_command."))
        name, url = art.filename, art.url
        pin = pin or art.sha256
        license_ = license_ or art.license or adapter.upstream.license

    if not name or not url:
        raise _stage_error(400, "BAD_PAYLOAD_NAME", "name and url are required",
                           "Supply {name, url, license} or {adapter_id}.")
    if not _NAME_RE.match(name) or name in _SHELF_METADATA:
        raise _bad_name(name)
    if not (license_ or "").strip() or (license_ or "").strip().lower() == "unknown":
        raise _stage_error(400, "LICENSE_REQUIRED", "Licence is required", (
            "The licence travels into MANIFEST.json, the inventory endpoint, the "
            "manifest banner and a POV report a customer's legal team may read. "
            "A blank licence there is unrecoverable once the artifact has "
            "shipped. Look it up."))
    _guard_stage_url(url)

    dist = payload_dist_dir()
    dist.mkdir(parents=True, exist_ok=True)
    dest = resolve_payload(name)
    if dest.exists() and not body.replace:
        raise _stage_error(409, "PAYLOAD_EXISTS", "Already staged", (
            f"{name} is already on the shelf at {dist}. Silent replacement is "
            f"refused: it changes the digest under a manifest a DC already "
            f"holds, and the consumer's resulting mismatch would point at 'an "
            f"intercepting proxy' — a wrong diagnosis manufactured by our own "
            f"API. Pass replace=true if you mean it."),
            sha256=sha256_of(dest))
    previous = sha256_of(dest) if dest.exists() else None

    part = dist / f".{name}.part"
    digest = hashlib.sha256()
    written = 0
    client = stage_client_factory()
    current = url
    try:
        async with client:
            # REDIRECTS ARE FOLLOWED BY HAND, and every hop goes back through
            # `_guard_stage_url`. Letting the HTTP client follow them made the
            # SSRF gate decorative: it only ever saw the URL the caller typed,
            # so `https://attacker/x` -> `302 http://169.254.169.254/…` was
            # fetched and then served straight back off the shelf. The beacon
            # refuses redirects outright for the same reason (see
            # agent/beacon/artifact.go::classifyStatus); SimCore cannot, because
            # every GitHub release URL redirects to an object store — so it
            # re-checks instead of trusting.
            for _hop in range(_STAGE_MAX_REDIRECTS + 1):
                async with client.stream("GET", current) as resp:
                    location = (resp.headers.get("location") or "") \
                        if 300 <= resp.status_code < 400 else ""
                    if location:
                        from urllib.parse import urljoin  # noqa: PLC0415

                        current = urljoin(current, location)
                        _guard_stage_url(current)
                        continue
                    if resp.status_code >= 300:
                        raise _stage_error(502, "PAYLOAD_FETCH_FAILED", "Upstream refused", (
                            f"{current} returned HTTP {resp.status_code}. A 401, a 403 or a "
                            f"302 to a captive portal is a FAILURE, never a staged "
                            f"artifact — an HTML error page staged as {name} would give "
                            f"a perfect digest, verify flawlessly on the consumer, and "
                            f"run as a no-op."), upstream_status=resp.status_code)
                    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if ctype.startswith("text/html"):
                        raise _stage_error(502, "PAYLOAD_FETCH_FAILED", "Upstream served HTML", (
                            f"{current} returned Content-Type {ctype!r}. That is an error or "
                            f"login page, not a tool. Staging it would produce an "
                            f"artifact that verifies perfectly and does nothing."))
                    with open(part, "wb") as fh:
                        async for chunk in resp.aiter_bytes():
                            written += len(chunk)
                            if written > _STAGE_MAX_BYTES:
                                raise _stage_error(413, "PAYLOAD_TOO_LARGE", "Artifact too large",
                                                   f"{current} exceeds CORTEXSIM_SHELF_MAX_BYTES "
                                                   f"({_STAGE_MAX_BYTES} bytes).")
                            digest.update(chunk)
                            fh.write(chunk)
                    break
            else:
                raise _stage_error(502, "PAYLOAD_FETCH_FAILED", "Too many redirects", (
                    f"{url} redirected more than {_STAGE_MAX_REDIRECTS} times "
                    f"(last hop {current}). Point at the artifact directly."))
    except HTTPException:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - transport failures
        part.unlink(missing_ok=True)
        raise _stage_error(504, "PAYLOAD_FETCH_TIMEOUT", "Could not reach upstream",
                           f"{url}: {exc}") from exc

    got = digest.hexdigest()
    if pin and pin.strip().lower() != got:
        part.unlink(missing_ok=True)
        raise _stage_error(409, "PAYLOAD_PIN_MISMATCH", "Upstream changed", (
            f"{name} pins {pin} but {url} served {got}. Upstream changed. A build "
            f"must never bless that silently, and there is deliberately no "
            f"override here — repoint the URL at a release tag and update the "
            f"adapter pack's install.artifact.sha256 in git."),
            expected=pin, actual=got)

    # Rename only AFTER the digest is computed and any pin matched. The same
    # structural property the pod's `.part` -> `mv` gives: it is impossible for
    # the download endpoint to serve a torn or unverified file.
    os.chmod(part, 0o644)
    os.replace(part, dest)
    _merge_manifest_entry({
        "name": name, "source_url": url, "sha256": got, "size_bytes": written,
        "license": license_, "pinned": bool(pin),
    })
    logger.info("payload shelf: staged %s (%d bytes, sha256=%s) from %s via API by %s",
                name, written, got, url,
                getattr(request.client, "host", "?") if request.client else "?")

    warnings: list[dict[str, Any]] = []
    if not pin:
        warnings.append({"code": "ARTIFACT_UNPINNED", "detail": (
            f"{name} was staged without a pin, so its digest is whatever was "
            f"downloaded just now. Paste sha256 {got} into the adapter pack.")})
    if previous and previous != got:
        warnings.append({"code": "DIGEST_CHANGED", "detail": (
            f"{name} went {previous[:12]}…→{got[:12]}…. Every manifest and "
            f"composition generated before now carries the old digest and will "
            f"fail integrity verification. Regenerate them.")})
    warnings.append({"code": "DECLARE_IN_PACK", "detail": (
        "payloads/sources.json is GENERATED from tools/packs/*.yml and this "
        "endpoint deliberately does not write to it. Paste pack_snippet into the "
        "adapter pack and run `python3 -m engine.payload_shelf --write` so the "
        "artifact survives the next clean build.")})

    return {
        "status": "staged",
        "payload": {
            "name": name, "sha256": got, "size_bytes": written,
            "license": license_, "pinned": bool(pin), "source_url": url,
            "adapter_id": body.adapter_id,
        },
        "replaced_sha256": previous,
        "pack_snippet": _pack_snippet(name, url, got, license_),
        "warnings": warnings,
    }


def _merge_manifest_entry(entry: dict[str, Any]) -> None:
    """Read-modify-write MANIFEST.json + SHA256SUMS, preserving other entries.

    Merges on `name` and never drops an entry for a file still on disk — an
    API stage must not erase what `scripts/build-payloads.sh` wrote. Single
    uvicorn worker is assumed (what compose ships); a multi-worker deployment
    would need `fcntl.flock` here.
    """
    dist = payload_dist_dir()
    doc = _shelf_manifest() or {}
    rows = [p for p in (doc.get("payloads") or []) if isinstance(p, dict)]
    rows = [p for p in rows if p.get("name") != entry["name"]]
    rows.append(entry)
    rows.sort(key=lambda p: str(p.get("name")))
    doc["payloads"] = rows
    doc.setdefault("generated_at", datetime.utcnow().isoformat() + "Z")
    tmp = dist / ".MANIFEST.json.part"
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, dist / "MANIFEST.json")
    lines = [f"{p['sha256']}  {p['name']}" for p in rows if p.get("sha256")]
    tmp2 = dist / ".SHA256SUMS.part"
    tmp2.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp2, dist / "SHA256SUMS")


def _warn_if_shelf_is_unpinned() -> None:
    """Boot WARN — an unpinned artifact is a silent tool substitution waiting to
    happen, and an operator should learn it from a log rather than an incident."""
    try:
        unpinned = [p["name"] for p in inventory() if p.get("pinned") is False]
        if unpinned:
            logger.warning(
                "payload shelf: %d staged artifact(s) are UNPINNED (%s). Their "
                "digests were recorded from whatever was downloaded, so an "
                "upstream change substitutes a different tool silently. Pin them "
                "in the adapter pack's install.artifact.sha256.",
                len(unpinned), ", ".join(unpinned),
            )
    except Exception:  # pragma: no cover
        logger.debug("payload shelf pin check failed", exc_info=True)


_warn_if_shelf_is_open()
_warn_if_shelf_is_unpinned()

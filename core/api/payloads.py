"""
SimCore payload serving for the K8s delivery mode — `/api/k8s/*`.

WHY THIS EXISTS
---------------
For a cloud-native target THE DEPLOYMENT IS THE AGENT. There is no beacon on a
customer host, no enrollment and no binary to install: `kubectl apply` is the
delivery, the kubelet pulls the image, and the init container pulls the payload
from here. This module is the server half of the contract written down in
`docs/reference/k8s-delivery.md` §5 ("Endpoints the payload track must serve").

It mirrors the agent-binary idiom in `core/api/agents.py` deliberately — one
inventory endpoint, one `FileResponse` carrying an `X-CortexSim-*-SHA256`
header, one bare-hex `/sha256` endpoint, and a 404 whose `detail` names the
shelf directory AND the command that fills it. There is one distribution idiom
in this codebase, not two.

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

import json
import logging
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
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

router = APIRouter(prefix="/k8s", tags=["k8s"])


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
_SHELF_METADATA = frozenset({"MANIFEST.json", "SHA256SUMS", "README.md", "sources.json", ".gitignore"})


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


def _require_payload_token(request: Request) -> None:
    """Enforce `CORTEXSIM_K8S_PAYLOAD_AUTH=token` on the artifact endpoints."""
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


@router.get("/payloads")
async def list_payloads():
    """Inventory of staged tool artifacts, and the manifest's reachability probe.

    ALWAYS unauthenticated (`k8s-delivery.md` §5). The init container's first
    action is `wget -O /dev/null $SERVER/api/k8s/payloads`, and it reports a
    failure here as SIMCORE_UNREACHABLE. If a credential could also fail this
    call, that message would send a DC to argue with the customer's network team
    over an auth problem.
    """
    payloads = inventory()
    return {
        "payloads": payloads,
        "total": len(payloads),
        "dist_dir": str(payload_dist_dir()),
        "auth": _auth_mode(),
    }


@router.get("/payload/{name}")
async def download_payload(name: str, request: Request):
    """Serve a staged tool artifact.

    The digest is echoed in `X-CortexSim-Payload-SHA256` so a client streaming
    the body can verify without a second round trip. A POD must compare against
    the digest baked into its manifest, not against this header.
    """
    _require_payload_token(request)
    path = _resolve_payload_or_404(name)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={"X-CortexSim-Payload-SHA256": sha256_of(path)},
    )


@router.get("/payload/{name}/sha256", response_class=PlainTextResponse)
async def payload_sha256(name: str, request: Request):
    """Bare hex digest of a staged artifact.

    Plain text, not JSON, so `curl … | read` needs no parser. FOR HUMANS AND
    DEBUGGING ONLY — see the module docstring on why a pod fetching its expected
    digest from the server that served the file verifies nothing.
    """
    _require_payload_token(request)
    path = _resolve_payload_or_404(name)
    return PlainTextResponse(sha256_of(path) + "\n")


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


_warn_if_shelf_is_open()

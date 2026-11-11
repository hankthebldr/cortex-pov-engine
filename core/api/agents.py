"""
CortexSim API — /api/agents router.

Endpoints:
  GET  /api/agents                     — list all connected agents
  POST /api/agents/register            — agent registers itself
  GET  /api/agents/{agent_id}/tasks    — agent polls for next task (task or null)
  GET  /api/agents/install             — generate the one-line onboarding script
  GET  /api/agents/binaries            — prebuilt beacon inventory (os/arch/sha256)
  GET  /api/agents/binary              — download a prebuilt beacon
  GET  /api/agents/binary/sha256       — its digest, for the installer's verify step
  POST /api/agents/install/telemetry   — installer reports its stage/failure code
  GET  /api/agents/install/attempts    — recent install attempts (console diagnostics)
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os  # NOTE: shadowed by the `os` query parameter inside agent_installer().
import re
import secrets
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.orchestrator import orchestrator
from models import Agent, EnrollmentToken

logger = logging.getLogger("cortexsim.api.agents")

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    agent_id: str
    hostname: str
    os: str
    capabilities: list[str] = []
    # The beacon's own honest snapshot of which logical interpreters resolve
    # on ITS host right now (executor.AvailableLogicalNames()). Optional/
    # back-compat: an older beacon binary that omits this field registers
    # exactly as before, with an empty roster (advisory-only — see
    # engine.runtime_preflight and docs/design/agent-runtime-dependencies.md).
    interpreters: list[str] = []


class MintTokenRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=80)
    ttl_seconds: int = Field(default=3600, ge=60, le=2_592_000)  # 1 min … 30 days
    max_uses: int = Field(default=1, ge=1, le=1000)


class EnrollRequest(BaseModel):
    token: str
    hostname: str
    os: str
    capabilities: list[str] = []
    # See RegisterRequest.interpreters. In practice the shell installer (not
    # Go code) drives this call, so it is usually empty here and gets its
    # real value moments later when the just-started compiled beacon binary
    # calls Register() for itself — kept here for API symmetry and so a
    # future Go-driven enroll path does not need a schema change.
    interpreters: list[str] = []
    # Optional client-suggested name; sanitised + suffixed for uniqueness.
    desired_name: Optional[str] = Field(default=None, max_length=60)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Prebuilt beacon distribution
# ---------------------------------------------------------------------------
#
# WHY: onboarding used to `go install` a module path from the public Go proxy,
# which meant a Go toolchain plus proxy.golang.org/sum.golang.org/github.com
# egress ON THE CUSTOMER ENDPOINT. Neither survives a hardened network or a
# change-control review, so the advertised one-liner could not actually run on
# the hosts a POV targets. SimCore now ships the beacon itself: the DC's own
# SimCore becomes the single outbound dependency and the endpoint needs no
# compiler. The shelf is filled by `make agent-dist` (scripts/build-agent-dist.sh)
# or by the `agent-builder` stage in core/Dockerfile.

# Canonical GOOS/GOARCH values, plus the `uname` spellings an installer sends.
_OS_ALIASES = {
    "linux": "linux",
    "darwin": "darwin", "macos": "darwin", "mac": "darwin", "osx": "darwin",
    "windows": "windows", "win": "windows",
}
_ARCH_ALIASES = {
    "amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
    "arm64": "arm64", "aarch64": "arm64",
}

# Digest cache keyed by (path, mtime_ns, size) — hashing 5 MB on every install
# poll is wasteful, and the key invalidates the moment the file is rebuilt.
_sha_cache: dict[tuple[str, int, int], str] = {}


def _agent_dist_dir() -> Path:
    """Directory holding the cross-compiled beacons.

    ``CORTEXSIM_AGENT_DIST`` wins so a DC can point at an scp'd drop without
    rebuilding the image; otherwise it is ``<BASE_DIR>/agent-dist``, which is
    where both the Dockerfile stage and the make target write.
    """
    override = os.environ.get("CORTEXSIM_AGENT_DIST")
    if override:
        return Path(override)
    from config import settings  # noqa: PLC0415 — avoid an import cycle at module load

    return Path(settings.CORTEXSIM_BASE_DIR) / "agent-dist"


def _normalize_target(os_raw: str, arch_raw: str) -> tuple[str, str]:
    """Map a caller's os/arch spelling onto GOOS/GOARCH, or 400."""
    os_norm = _OS_ALIASES.get((os_raw or "").strip().lower())
    if os_norm is None:
        raise HTTPException(status_code=400, detail={
            "error": "Unsupported OS", "code": "BAD_OS",
            "detail": f"os must be one of {sorted(set(_OS_ALIASES.values()))}"})
    arch_norm = _ARCH_ALIASES.get((arch_raw or "").strip().lower())
    if arch_norm is None:
        raise HTTPException(status_code=400, detail={
            "error": "Unsupported architecture", "code": "BAD_ARCH",
            "detail": f"arch must be one of {sorted(set(_ARCH_ALIASES.values()))} "
                      f"(uname -m spellings x86_64/aarch64 are accepted)"})
    return os_norm, arch_norm


def _binary_name(goos: str, goarch: str) -> str:
    suffix = ".exe" if goos == "windows" else ""
    return f"cortexsim-agent-{goos}-{goarch}{suffix}"


def _binary_path(goos: str, goarch: str) -> Path:
    return _agent_dist_dir() / _binary_name(goos, goarch)


def _sha256_of(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _sha_cache.get(key)
    if cached:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    _sha_cache[key] = hexdigest
    return hexdigest


def _available_binaries() -> list[dict]:
    """Inventory the dist directory. Empty list is a valid state — the installer
    degrades to a source build and says so."""
    dist = _agent_dist_dir()
    if not dist.is_dir():
        return []
    out: list[dict] = []
    for goos in sorted(set(_OS_ALIASES.values())):
        for goarch in sorted(set(_ARCH_ALIASES.values())):
            path = dist / _binary_name(goos, goarch)
            if not path.is_file():
                continue
            st = path.stat()
            out.append({
                "os": goos,
                "arch": goarch,
                "filename": path.name,
                "size_bytes": st.st_size,
                "sha256": _sha256_of(path),
                "modified_at": datetime.utcfromtimestamp(st.st_mtime).isoformat(),
            })
    return out


def _resolve_binary_or_404(goos: str, goarch: str) -> Path:
    path = _binary_path(goos, goarch)
    if not path.is_file():
        have = [f"{b['os']}/{b['arch']}" for b in _available_binaries()]
        raise HTTPException(status_code=404, detail={
            "error": "No prebuilt beacon for that target",
            "code": "AGENT_BINARY_UNAVAILABLE",
            "detail": (
                f"{goos}/{goarch} is not in {_agent_dist_dir()}. "
                f"Available: {have or 'none'}. Build the matrix on the SimCore host "
                f"with `make agent-dist`, or rebuild the image (core/Dockerfile "
                f"agent-builder stage), or scp a binary in and set CORTEXSIM_AGENT_DIST."
            ),
        })
    return path


@router.get("/binaries")
async def list_agent_binaries():
    """Inventory of prebuilt beacons this SimCore can hand to an endpoint."""
    binaries = _available_binaries()
    return {"binaries": binaries, "total": len(binaries),
            "dist_dir": str(_agent_dist_dir())}


@router.get("/binary")
async def download_agent_binary(os: str = "linux", arch: str = "amd64"):
    """Serve the prebuilt beacon for ``os``/``arch``.

    This is the whole point of the redesign: the customer endpoint downloads a
    static, stdlib-only binary from the DC's SimCore instead of compiling one.
    The digest is echoed in ``X-CortexSim-Agent-SHA256`` so a client that
    streams the body can verify without a second round trip.
    """
    goos, goarch = _normalize_target(os, arch)
    path = _resolve_binary_or_404(goos, goarch)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={"X-CortexSim-Agent-SHA256": _sha256_of(path)},
    )


@router.get("/binary/sha256", response_class=PlainTextResponse)
async def agent_binary_sha256(os: str = "linux", arch: str = "amd64"):
    """Bare hex digest of the prebuilt beacon — what the installer compares
    against. Plain text (not JSON) so `curl … | read` needs no parser on a
    minimal endpoint."""
    goos, goarch = _normalize_target(os, arch)
    path = _resolve_binary_or_404(goos, goarch)
    return PlainTextResponse(_sha256_of(path) + "\n")


# ---------------------------------------------------------------------------
# Install telemetry — make a failed onboarding visible in the console
# ---------------------------------------------------------------------------
#
# Before this, a failed install echoed one line on a host the DC may have
# already left and SimCore showed nothing at all — indistinguishable from "the
# DC hasn't run the command yet". The installer now POSTs a stable code at every
# stage. Deliberately in-memory and bounded: this is operator diagnostics with a
# minutes-long useful life, not an audit trail, and it must never be able to
# grow the DB or fail an install.

_INSTALL_ATTEMPTS_MAX = 100
_install_attempts: deque[dict] = deque(maxlen=_INSTALL_ATTEMPTS_MAX)


class InstallTelemetry(BaseModel):
    stage: str = Field(max_length=40)          # detect | enroll | download | verify | install | run
    code: str = Field(max_length=60)           # OK | ENROLL_DENIED | CHECKSUM_MISMATCH | …
    detail: str = Field(default="", max_length=2000)
    hostname: str = Field(default="", max_length=253)
    os: str = Field(default="", max_length=32)
    arch: str = Field(default="", max_length=32)
    agent_id: Optional[str] = Field(default=None, max_length=120)


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@router.post("/install/telemetry")
async def record_install_telemetry(body: InstallTelemetry):
    """Record an installer stage/outcome reported by a target endpoint.

    Unauthenticated by design — the reporting host has not enrolled yet when the
    interesting failures happen, so requiring a credential would silence exactly
    the cases this exists for. Payload is length-capped by the model and stripped
    of control characters here before it can reach an operator's screen.
    """
    entry = {
        "stage": _CONTROL_CHARS.sub("", body.stage),
        "code": _CONTROL_CHARS.sub("", body.code),
        "detail": _CONTROL_CHARS.sub("", body.detail),
        "hostname": _CONTROL_CHARS.sub("", body.hostname),
        "os": _CONTROL_CHARS.sub("", body.os),
        "arch": _CONTROL_CHARS.sub("", body.arch),
        "agent_id": _CONTROL_CHARS.sub("", body.agent_id or "") or None,
        "reported_at": datetime.utcnow().isoformat(),
    }
    _install_attempts.appendleft(entry)
    logger.info("install telemetry host=%s %s/%s stage=%s code=%s",
                entry["hostname"], entry["os"], entry["arch"], entry["stage"], entry["code"])

    # Best-effort live surfacing on the existing global SSE stream. A bus hiccup
    # must never turn an install report into an error the target sees.
    try:
        from events import event_bus  # noqa: PLC0415

        await event_bus.publish(None, {"type": "agent.install", "run_id": None, "data": entry})
    except Exception:  # pragma: no cover - defensive
        logger.exception("event_bus publish failed for install telemetry")

    return {"status": "recorded", "stage": entry["stage"], "code": entry["code"]}


@router.get("/install/attempts")
async def list_install_attempts(limit: int = 25):
    """Most recent install attempts, newest first. In-memory — cleared on restart."""
    capped = max(1, min(limit, _INSTALL_ATTEMPTS_MAX))
    items = list(_install_attempts)[:capped]
    return {"attempts": items, "total": len(_install_attempts), "retained_max": _INSTALL_ATTEMPTS_MAX}


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


def _is_loopback(url: str) -> bool:
    """True when `url`'s host would resolve to the BEACON's own machine."""
    host = urlparse(url).hostname
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


# ---------------------------------------------------------------------------
# Validation of every value the installers bake into script text
# ---------------------------------------------------------------------------
#
# The installers are TEMPLATES: id / server / token / interval (and, when no
# ?server= is given, the request's Host header) are substituted into bash and
# PowerShell source verbatim. Before these checks, ?id=x'; Write-Host PWNED; '
# produced a script that ran the attacker's text — as SYSTEM, once the Windows
# installer registered it as the service command line. Allow-lists, not escaping:
# every legitimate value fits a narrow charset, and a narrow charset is safe in
# a bash double-quoted string, a PowerShell single-quoted string AND a service
# binPath at once, which no single escaping scheme is.

_SERVER_CHARSET = re.compile(r"[A-Za-z0-9.:/\[\]_~-]+")
_AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# Mirrors the mint below: "cxs_" + secrets.token_urlsafe(32) (43 url-safe chars).
_TOKEN_RE = re.compile(r"cxs_[A-Za-z0-9_-]{20,128}")
_INTERVAL_RE = re.compile(r"[0-9]{1,4}")
_INTERVAL_MIN, _INTERVAL_MAX = 1, 3600


def _bad_request(code: str, error: str, detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": error, "code": code, "detail": detail})


def _server_problem(url: str) -> Optional[str]:
    """Why ``url`` cannot be baked as the beacon's callback, or None if it can."""
    if not url or not _SERVER_CHARSET.fullmatch(url):
        return ("only http(s)://host[:port][/path] using letters, digits and . : / [ ] _ ~ - "
                "is accepted")
    try:
        parsed = urlparse(url)
        port = parsed.port  # raises ValueError for a non-numeric / out-of-range port
    except ValueError as exc:
        return f"not a parseable URL ({exc})"
    if parsed.scheme not in ("http", "https"):
        return "scheme must be http or https"
    if not parsed.hostname:
        return "no host"
    if parsed.username is not None or parsed.password is not None:
        return "userinfo (user:pass@) is not accepted"
    if parsed.query or parsed.fragment or parsed.params:
        return "a query string or fragment is not accepted"
    if port is not None and not 1 <= port <= 65535:
        return "port out of range"
    return None


def _agent_id_problem(agent_id: str) -> Optional[str]:
    if _AGENT_ID_RE.fullmatch(agent_id or ""):
        return None
    return ("1-64 characters: a letter or digit, then letters, digits, '.', '_' or '-'")


def _token_problem(token: str) -> Optional[str]:
    if _TOKEN_RE.fullmatch(token or ""):
        return None
    return ("an enrollment token is 'cxs_' followed by 20-128 URL-safe characters; mint one "
            "with POST /api/agents/enroll/tokens")


def _parse_interval(raw: str) -> int:
    if _INTERVAL_RE.fullmatch((raw or "").strip()):
        value = int(raw.strip())
        if _INTERVAL_MIN <= value <= _INTERVAL_MAX:
            return value
    raise _bad_request("BAD_INTERVAL", "Invalid poll interval",
                       f"interval must be a whole number of seconds, {_INTERVAL_MIN}-{_INTERVAL_MAX}")


def _check_baked(server: str, agent_id: str, interval: int, token: Optional[str]) -> None:
    """Renderer-side re-check (defence in depth): a caller that bypassed the
    route's validation gets an exception, never an injectable script."""
    problems = []
    if _server_problem(server):
        problems.append(f"server {server!r}: {_server_problem(server)}")
    if _agent_id_problem(agent_id):
        problems.append(f"agent_id {agent_id!r}: {_agent_id_problem(agent_id)}")
    if not isinstance(interval, int) or not _INTERVAL_MIN <= interval <= _INTERVAL_MAX:
        problems.append(f"interval {interval!r}: out of range")
    if token and _token_problem(token):
        problems.append(f"token: {_token_problem(token)}")
    if problems:
        raise ValueError("refusing to render an installer with unvalidated values: "
                         + "; ".join(problems))


def _resolve_server(request: Request, override: Optional[str]) -> str:
    """Server URL the agent beacons back to. Prefer an explicit override
    (the DC knows the jumpbox's reachable address); else derive from the
    request so `curl <host>/api/agents/install | bash` just works.

    Either way the result is validated (``_server_problem``) before it reaches
    a template: the derived default comes from the Host header, which is as
    caller-controlled as the query string.

    A loopback address here is NOT refused. Installing the beacon on the
    SimCore host itself is a real flow (local dev, and the installer e2e
    suite, which serves uvicorn on 127.0.0.1 and installs against it), and
    from this side the server cannot tell that case apart from a one-liner
    the DC is about to paste on a jumpbox. Refusing would break the honest
    use to guess at the dishonest one.

    Instead the loopback is carried forward and diagnosed where the answer
    is actually knowable: the console warns before the copy (it knows the
    operator is reading it on the control plane), and the generated script
    names loopback as the likely cause if — and only if — the preflight to
    SERVER fails on the target. See `_loopback_banner`.
    """
    if override:
        resolved, source = override.strip().rstrip("/"), "?server="
    else:
        resolved, source = str(request.base_url).rstrip("/"), "the request's Host header"
    problem = _server_problem(resolved)
    if problem:
        raise _bad_request(
            "BAD_SERVER", "Invalid server URL",
            f"the callback URL from {source} cannot be baked into an installer: {problem}. "
            "Pass ?server=http://<simcore-ip>:8888")
    return resolved


# ---------------------------------------------------------------------------
# One-line installers
# ---------------------------------------------------------------------------
#
# The scripts are templates with @@TOKEN@@-style placeholders rather than
# f-strings: the bodies are dense shell/PowerShell and doubling every literal
# brace to survive `str.format` made them unreadable and easy to break.

_POSIX_INSTALLER = r"""#!/usr/bin/env bash
# ─── CortexSim beacon installer (Linux / macOS) ─────────────────────────────
#
#   curl -fsSL '<server>/api/agents/install?token=<tok>' | bash
#
# No compiler, no public-internet egress: the only host this talks to is the
# SimCore that served it. It detects os/arch, enrols for a server-assigned agent
# id, downloads the prebuilt beacon and verifies its sha256, then installs it as
# a SUPERVISED service (systemd on Linux, launchd on macOS) so it survives your
# SSH session and a reboot — a POV runs for weeks, not for one terminal.
#
# Every failure exits with a stable CODE and is reported back to SimCore
# (GET /api/agents/install/attempts) so the operator sees why onboarding failed
# without being logged into this host.
#
# Env overrides:
#   CORTEXSIM_SERVER / CORTEXSIM_TOKEN / CORTEXSIM_AGENT_ID / CORTEXSIM_INTERVAL
#   CORTEXSIM_MODE=service|foreground   service is the default
#   CORTEXSIM_BIN=/path/to/beacon       pre-staged binary (fully air-gapped host)
#   CORTEXSIM_SRC=/path/to/repo         build from source (needs Go) if no prebuilt
#   CORTEXSIM_BIN_DIR / CORTEXSIM_LOG   install prefix + log path (read-only /usr/local)
#   CORTEXSIM_TELEMETRY=0               suppress the install-status POST
set -euo pipefail

SERVER="${CORTEXSIM_SERVER:-@@SERVER@@}"; SERVER="${SERVER%/}"
AGENT_ID="${CORTEXSIM_AGENT_ID:-@@AGENT_ID@@}"
INTERVAL="${CORTEXSIM_INTERVAL:-@@INTERVAL@@}"
MODE="${CORTEXSIM_MODE:-@@MODE@@}"
TOKEN="${CORTEXSIM_TOKEN:-@@TOKEN@@}"
HOSTN="$(hostname 2>/dev/null || echo unknown)"
# Non-empty only when the baked SERVER is a loopback address. Appended to the
# unreachable-server remediation: on the SimCore host loopback is correct and
# nothing here fires, but on a jumpbox it is the single likeliest cause and
# the generic "check routing / proxy / firewall" sends you the wrong way.
CS_LOOPBACK_HINT="@@LOOPBACK_HINT@@"
OS_ID="unknown"; ARCH_ID="unknown"; BIN_SRC=""
SVC="cortexsim-agent"
PLIST_LABEL="com.paloaltonetworks.cortexsim.agent"

WORKDIR="$(mktemp -d 2>/dev/null || mktemp -d -t cortexsim)"
trap 'rm -rf "$WORKDIR"' EXIT

cs_say() { echo "[cortexsim] $*"; }

# Minimal JSON string escaping — the server length-caps and strips control
# characters, so this only has to survive transport.
cs_esc() { printf '%s' "${1:-}" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\r\t' '   '; }

cs_report() {  # stage code detail
  if [ "${CORTEXSIM_TELEMETRY:-1}" = "0" ]; then return 0; fi
  curl -sS -m 5 -o /dev/null -X POST "$SERVER/api/agents/install/telemetry" \
    -H 'Content-Type: application/json' \
    -d "{\"stage\":\"$(cs_esc "$1")\",\"code\":\"$(cs_esc "$2")\",\"detail\":\"$(cs_esc "$3")\",\"hostname\":\"$(cs_esc "$HOSTN")\",\"os\":\"$OS_ID\",\"arch\":\"$ARCH_ID\",\"agent_id\":\"$(cs_esc "${AGENT_ID:-}")\"}" \
    >/dev/null 2>&1 || true
  return 0
}

cs_fail() {  # stage code what fix
  echo "" >&2
  echo "[cortexsim] FAILED  stage=$1  code=$2" >&2
  echo "[cortexsim]   what : $3" >&2
  if [ -n "${4:-}" ]; then echo "[cortexsim]   fix  : $4" >&2; fi
  cs_report "$1" "$2" "$3"
  exit 1
}

# ── stage: detect ───────────────────────────────────────────────────────────
UNAME_S="$(uname -s 2>/dev/null || echo unknown)"
case "$UNAME_S" in
  Linux)  OS_ID="linux" ;;
  Darwin) OS_ID="darwin" ;;
  *) cs_fail detect UNSUPPORTED_OS "uname -s reported '$UNAME_S'" \
       "supported: Linux, macOS. For Windows, from an elevated Windows PowerShell: iwr '$SERVER/api/agents/install?os=windows' -UseBasicParsing | iex" ;;
esac
UNAME_M="$(uname -m 2>/dev/null || echo unknown)"
case "$UNAME_M" in
  x86_64|amd64)  ARCH_ID="amd64" ;;
  aarch64|arm64) ARCH_ID="arm64" ;;
  *) cs_fail detect UNSUPPORTED_ARCH "uname -m reported '$UNAME_M'" \
       "supported: x86_64/amd64 and aarch64/arm64" ;;
esac
if ! command -v curl >/dev/null 2>&1; then
  cs_fail detect CURL_MISSING "curl is not on PATH" \
    "install curl, or scp the beacon over and re-run with CORTEXSIM_BIN=/path/to/cortexsim-agent"
fi
cs_say "target server : $SERVER"
cs_say "platform      : $OS_ID/$ARCH_ID ($HOSTN)"

# ── stage: enroll ───────────────────────────────────────────────────────────
if [ -n "$TOKEN" ]; then
  cs_say "enrolling with token ...${TOKEN: -6}"
  PAYLOAD="$(printf '{"token":"%s","hostname":"%s","os":"%s","capabilities":["shell","identity-harness"]}' \
    "$(cs_esc "$TOKEN")" "$(cs_esc "$HOSTN")" "$OS_ID")"
  EBODY="$WORKDIR/enroll.json"; EERR="$WORKDIR/enroll.err"
  # `|| true`, not `|| echo 000`: curl's -w writes "000" itself on a transport
  # failure, so an appended fallback produced the nonsense code "000000".
  ECODE="$(curl -sS -m 30 -o "$EBODY" -w '%{http_code}' -X POST "$SERVER/api/agents/enroll" \
    -H 'Content-Type: application/json' -d "$PAYLOAD" 2>"$EERR" || true)"
  if [ -z "$ECODE" ] || [ "$ECODE" = "000" ]; then
    cs_fail enroll SERVER_UNREACHABLE \
      "cannot reach $SERVER — $(head -c 300 "$EERR" 2>/dev/null || true)" \
      "check routing / proxy / firewall from this host to SimCore, then re-run$CS_LOOPBACK_HINT"
  fi
  if [ "$ECODE" != "200" ]; then
    # Surface the server's own structured {error,code,detail} body verbatim —
    # the old installer used `curl -f`, which threw it away and guessed.
    cs_fail enroll ENROLL_DENIED \
      "HTTP $ECODE from /api/agents/enroll — $(head -c 400 "$EBODY" 2>/dev/null || true)" \
      "mint a fresh token: POST $SERVER/api/agents/enroll/tokens"
  fi
  AGENT_ID="$(sed -n 's/.*"agent_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$EBODY")"
  if [ -z "$AGENT_ID" ]; then
    cs_fail enroll NO_AGENT_ID \
      "enroll returned 200 with no agent_id — body: $(head -c 300 "$EBODY" 2>/dev/null || true)" \
      "SimCore/installer version mismatch — re-fetch the one-liner from this SimCore"
  fi
  cs_say "enrolled as   : $AGENT_ID"
else
  cs_say "agent id      : $AGENT_ID (self-asserted — prefer ?token= so SimCore assigns it)"
fi
if [ -z "$AGENT_ID" ]; then
  cs_fail enroll NO_AGENT_ID "no agent id resolved" "pass ?token=<tok> (recommended) or ?id=<name>"
fi

# ── stage: download ─────────────────────────────────────────────────────────
cs_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 "$1" | awk '{print $NF}'
  else echo ""; fi
}

cs_try_download() {
  DL="$WORKDIR/cortexsim-agent"
  URL="$SERVER/api/agents/binary?os=$OS_ID&arch=$ARCH_ID"
  cs_say "fetching prebuilt beacon ($OS_ID/$ARCH_ID)"
  DCODE="$(curl -sS -m 300 -o "$DL" -w '%{http_code}' "$URL" 2>"$WORKDIR/dl.err" || true)"
  if [ -z "$DCODE" ] || [ "$DCODE" = "000" ]; then
    cs_fail download SERVER_UNREACHABLE \
      "cannot reach $SERVER — $(head -c 300 "$WORKDIR/dl.err" 2>/dev/null || true)" \
      "check routing / proxy / firewall from this host to SimCore$CS_LOOPBACK_HINT"
  fi
  if [ "$DCODE" = "404" ]; then
    cs_say "WARN: SimCore serves no beacon for $OS_ID/$ARCH_ID"
    cs_say "      $(head -c 300 "$DL" 2>/dev/null || true)"
    return 1
  fi
  if [ "$DCODE" != "200" ]; then
    cs_fail download BINARY_FETCH_FAILED \
      "HTTP $DCODE — $(head -c 300 "$DL" 2>/dev/null || true)" \
      "GET '$URL' by hand to read the structured error"
  fi
  EXPECT="$(curl -sS -m 30 "$SERVER/api/agents/binary/sha256?os=$OS_ID&arch=$ARCH_ID" 2>/dev/null | tr -d '[:space:]' || true)"
  ACTUAL="$(cs_sha256 "$DL")"
  if [ -z "$ACTUAL" ] || [ -z "$EXPECT" ]; then
    # Never silently skip integrity: warn loudly AND record it server-side.
    cs_say "WARN: integrity NOT verified (no digest tool on host, or none served)"
    cs_report verify CHECKSUM_SKIPPED "expect='$EXPECT' actual_tool_present=$([ -n "$ACTUAL" ] && echo yes || echo no)"
  elif [ "$EXPECT" != "$ACTUAL" ]; then
    cs_fail verify CHECKSUM_MISMATCH "expected $EXPECT, got $ACTUAL" \
      "do NOT run this binary — re-fetch, and look for an intercepting TLS proxy"
  else
    cs_say "checksum OK   : ${ACTUAL:0:16}…"
  fi
  chmod +x "$DL"
  BIN_SRC="$DL"
  return 0
}

cs_try_source() {
  if [ -z "${CORTEXSIM_SRC:-}" ] || [ ! -d "${CORTEXSIM_SRC:-}/agent" ]; then return 1; fi
  if ! command -v go >/dev/null 2>&1; then
    cs_say "WARN: CORTEXSIM_SRC is set but no Go toolchain is present — skipping source build"
    return 1
  fi
  cs_say "building from local source: $CORTEXSIM_SRC/agent"
  ( cd "$CORTEXSIM_SRC/agent" && CGO_ENABLED=0 go build -o "$WORKDIR/cortexsim-agent" . ) \
    || cs_fail install SOURCE_BUILD_FAILED "go build failed in $CORTEXSIM_SRC/agent" \
         "run the build by hand to see the compiler error"
  BIN_SRC="$WORKDIR/cortexsim-agent"
  return 0
}

if [ -n "${CORTEXSIM_BIN:-}" ]; then
  if [ ! -x "${CORTEXSIM_BIN}" ]; then
    cs_fail install BIN_NOT_EXECUTABLE "CORTEXSIM_BIN='$CORTEXSIM_BIN' is not an executable file" \
      "chmod +x it, or unset CORTEXSIM_BIN to download from SimCore"
  fi
  cs_say "using pre-staged binary: $CORTEXSIM_BIN"
  BIN_SRC="$CORTEXSIM_BIN"
elif cs_try_download; then
  :
elif cs_try_source; then
  :
else
  cs_fail install BINARY_UNAVAILABLE \
    "no prebuilt beacon for $OS_ID/$ARCH_ID on $SERVER, and no local source build available" \
    "on the SimCore host run 'make agent-dist' (or rebuild the image) and re-run this one-liner; air-gapped: scp the beacon over and set CORTEXSIM_BIN=/path/to/cortexsim-agent"
fi

# ── stage: install ──────────────────────────────────────────────────────────
# Least privilege by default: we never escalate. As root we install a system
# service (which the identity harness needs anyway, to runuser into www-data et
# al); as a normal user we stay a normal user and say plainly what that costs.
if [ "$(id -u)" = "0" ]; then
  DEFAULT_BIN_DIR="/usr/local/bin"; DEFAULT_LOG="/var/log/cortexsim-agent.log"
else
  DEFAULT_BIN_DIR="$HOME/.local/bin"; DEFAULT_LOG="$HOME/.cortexsim/cortexsim-agent.log"
fi
BIN_DIR="${CORTEXSIM_BIN_DIR:-$DEFAULT_BIN_DIR}"
LOG_FILE="${CORTEXSIM_LOG:-$DEFAULT_LOG}"
BIN="$BIN_DIR/cortexsim-agent"
mkdir -p "$BIN_DIR" "$(dirname "$LOG_FILE")"
if [ "$BIN_SRC" != "$BIN" ]; then
  cp "$BIN_SRC" "$BIN.new" && chmod 0755 "$BIN.new" && mv -f "$BIN.new" "$BIN" \
    || cs_fail install BIN_INSTALL_FAILED "could not write $BIN" "check permissions / disk space on $BIN_DIR"
fi
cs_say "beacon        : $BIN"

if [ "$MODE" = "foreground" ]; then
  cs_report run OK "foreground mode"
  cs_say "running in the FOREGROUND (Ctrl-C to stop) — dies with this session, demo use only"
  exec "$BIN" --server "$SERVER" --id "$AGENT_ID" --interval "$INTERVAL"
fi

cs_install_systemd() {
  command -v systemctl >/dev/null 2>&1 || return 1
  # `systemctl` on PATH does not mean systemd is RUNNING it. Containers, WSL
  # without systemd, LXC and chroots all ship the client with PID 1 something
  # else, and there the unit install dead-ends: binary installed, unit written,
  # zero beacons running, and an enrolled agent row reading `online` while the
  # printed fix line recommends journalctl on a host with no journal. Probe the
  # manager, not the binary, so those hosts take the honest nohup degrade.
  # The disjunction is deliberate: is-system-running returns non-zero for
  # degraded/starting/maintenance, all of which are perfectly usable, and
  # /run/systemd/system exists on every live-systemd host — either arm passing
  # is sufficient, so a working host is never falsely downgraded.
  systemctl is-system-running >/dev/null 2>&1 || \
    [ -d /run/systemd/system ] || return 1
  UNIT_BODY="[Unit]
Description=CortexSim beacon agent ($AGENT_ID)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$BIN --server $SERVER --id $AGENT_ID --interval $INTERVAL
Restart=always
RestartSec=10
"
  if [ "$(id -u)" = "0" ]; then
    UNIT="/etc/systemd/system/$SVC.service"
    # `enable --now` is a no-op on an already-active unit of the SAME name: it
    # rewrites the file on disk but never signals the running process, which
    # keeps whatever --id it was launched with. A re-install (the FIRST thing
    # a stranger does when unsure the first install worked) silently prints
    # success while the OLD process — with the OLD --id — keeps polling, and
    # the NEW --id never checks in. Detect that case before overwriting the
    # unit file and force a restart so the rewritten ExecStart actually takes.
    UNIT_PREEXISTED=0
    [ -f "$UNIT" ] && UNIT_PREEXISTED=1
    printf '%s\n[Install]\nWantedBy=multi-user.target\n' "$UNIT_BODY" > "$UNIT"
    systemctl daemon-reload
    if ! systemctl enable --now "$SVC" >/dev/null 2>&1; then
      # A live-systemd host that still refuses the unit is a real problem, but
      # cs_fail exits 1 — which left the DC with an enrolled agent row, an
      # installed binary and NO beacon running. Report the supervisor problem
      # and return so the caller reaches cs_install_nohup, which emits an honest
      # DEGRADED_NO_SUPERVISOR. A degraded beacon that runs beats a clean
      # failure that does not.
      cs_say "WARN: systemctl enable --now $SVC failed — degrading to unsupervised"
      rm -f "$UNIT"; systemctl daemon-reload >/dev/null 2>&1 || true
      cs_report install SERVICE_START_FAILED "systemctl enable --now $SVC failed; degrading to nohup"
      return 1
    fi
    if [ "$UNIT_PREEXISTED" = "1" ]; then
      cs_say "  unit already existed — restarting so the new --id $AGENT_ID takes over"
      systemctl restart "$SVC" >/dev/null 2>&1 \
        || cs_say "WARN: systemctl restart $SVC failed — a stale process on the old --id may still be running"
    fi
    cs_say "installed system unit: $UNIT"
    cs_say "  status: systemctl status $SVC"
    cs_say "  logs  : journalctl -u $SVC -f"
    return 0
  fi
  # A --user unit needs a live user bus; on a jumpbox reached over ssh without
  # lingering there may not be one, in which case we fall through to nohup.
  systemctl --user show-environment >/dev/null 2>&1 || return 1
  mkdir -p "$HOME/.config/systemd/user"
  UNIT="$HOME/.config/systemd/user/$SVC.service"
  # Same no-op-on-reinstall trap as the root arm above.
  UNIT_PREEXISTED=0
  [ -f "$UNIT" ] && UNIT_PREEXISTED=1
  printf '%s\n[Install]\nWantedBy=default.target\n' "$UNIT_BODY" > "$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SVC" >/dev/null 2>&1 || return 1
  if [ "$UNIT_PREEXISTED" = "1" ]; then
    cs_say "  unit already existed — restarting so the new --id $AGENT_ID takes over"
    systemctl --user restart "$SVC" >/dev/null 2>&1 \
      || cs_say "WARN: systemctl --user restart $SVC failed — a stale process on the old --id may still be running"
  fi
  cs_say "installed USER unit: $UNIT"
  cs_say "  NOTE: running as $(id -un), not root — identity-harness steps that need"
  cs_say "        'runuser -l www-data' will fail. Re-run as root for full fidelity."
  cs_say "  survive logout: sudo loginctl enable-linger $(id -un)"
  cs_say "  logs  : journalctl --user -u $SVC -f"
  return 0
}

cs_install_launchd() {
  command -v launchctl >/dev/null 2>&1 || return 1
  if [ "$(id -u)" = "0" ]; then
    PLIST="/Library/LaunchDaemons/$PLIST_LABEL.plist"; DOMAIN="system"
  else
    mkdir -p "$HOME/Library/LaunchAgents"
    PLIST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"; DOMAIN="gui/$(id -u)"
  fi
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PLIST_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BIN</string>
    <string>--server</string><string>$SERVER</string>
    <string>--id</string><string>$AGENT_ID</string>
    <string>--interval</string><string>$INTERVAL</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_FILE</string>
  <key>StandardErrorPath</key><string>$LOG_FILE</string>
</dict>
</plist>
PLIST_EOF
  launchctl bootout "$DOMAIN/$PLIST_LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "$DOMAIN" "$PLIST" >/dev/null 2>&1 \
    || launchctl load -w "$PLIST" >/dev/null 2>&1 \
    || return 1
  cs_say "installed launchd job: $PLIST"
  cs_say "  logs  : tail -f $LOG_FILE"
  return 0
}

cs_install_nohup() {
  cs_say "no service manager available — detaching the beacon with setsid/nohup"
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup "$BIN" --server "$SERVER" --id "$AGENT_ID" --interval "$INTERVAL" \
      >>"$LOG_FILE" 2>&1 </dev/null &
  else
    nohup "$BIN" --server "$SERVER" --id "$AGENT_ID" --interval "$INTERVAL" \
      >>"$LOG_FILE" 2>&1 </dev/null &
  fi
  BEACON_PID=$!
  disown "$BEACON_PID" 2>/dev/null || true
  cs_say "  pid   : $BEACON_PID"
  cs_say "  logs  : tail -f $LOG_FILE"
  cs_say "  NOTE: unsupervised — no crash restart, no reboot survival."
  cs_report install DEGRADED_NO_SUPERVISOR "no systemd/launchd — running under nohup (pid $BEACON_PID)"
  return 0
}

SUPERVISED=0
case "$OS_ID" in
  linux)  if cs_install_systemd; then SUPERVISED=1; fi ;;
  darwin) if cs_install_launchd; then SUPERVISED=1; fi ;;
esac
if [ "$SUPERVISED" = "0" ]; then cs_install_nohup; fi

# ── stage: verify ────────────────────────────────────────────────────────
# Every earlier stage exits with a stable code that is both printed and
# POSTed to telemetry — this stage used to be the one exception. "done"
# printed unconditionally, whether or not '$AGENT_ID' ever polled SimCore.
# That is this product's own defining failure mode (something that did not
# happen presenting as success) wearing the installer's coat: on a
# re-install, `enable --now` can rewrite the unit file without ever
# restarting the OLD process, which keeps running under its OLD --id while
# the NEW id — the one just printed — never checks in.
#
# Existence of an /api/agents row is NOT proof of a live beacon: enrollment
# itself stamps last_seen the moment the token is redeemed, before the
# binary has even been launched, so a beacon that dies on the spot still
# shows up with a "recent" last_seen if you only check once. The only real
# proof is that last_seen ADVANCES past that enrollment stamp — i.e. the
# beacon polled SimCore again, on its own, after we started watching.
cs_last_seen() {  # -> last_seen ISO string for $AGENT_ID, or empty if not found
  curl -sS -m 10 "$SERVER/api/agents" 2>/dev/null \
    | grep -o "\"agent_id\":\"$AGENT_ID\"[^}]*\"last_seen\":\"[^\"]*\"" \
    | sed -n 's/.*"last_seen":"\([^"]*\)".*/\1/p' || true
}

WAIT_TOTAL=$(( INTERVAL * 3 ))
[ "$WAIT_TOTAL" -lt 6 ] && WAIT_TOTAL=6
POLL_STEP=$INTERVAL
[ "$POLL_STEP" -lt 1 ] && POLL_STEP=1
[ "$POLL_STEP" -gt 5 ] && POLL_STEP=5
cs_say ""
cs_say "verifying '$AGENT_ID' checks in (up to ${WAIT_TOTAL}s)..."
BASELINE_SEEN="$(cs_last_seen)"
CHECKED_IN=0
WAITED=0
while [ "$WAITED" -lt "$WAIT_TOTAL" ]; do
  sleep "$POLL_STEP"
  WAITED=$(( WAITED + POLL_STEP ))
  SEEN="$(cs_last_seen)"
  # ISO-8601 timestamps sort lexicographically in step with time, so a plain
  # string comparison proves an advance without needing a date parser on the
  # target. Empty BASELINE_SEEN (self-asserted id, never enrolled) means any
  # observed value at all is an advance.
  if [ -n "$SEEN" ] && [ "$SEEN" '>' "$BASELINE_SEEN" ]; then
    CHECKED_IN=1
    break
  fi
done

cs_say ""
if [ "$CHECKED_IN" = "1" ]; then
  if [ "$SUPERVISED" = "1" ]; then
    cs_report install OK "supervised on $OS_ID/$ARCH_ID, checked in after ${WAITED}s"
  else
    cs_report install OK "unsupervised on $OS_ID/$ARCH_ID, checked in after ${WAITED}s"
  fi
  cs_say "done — '$AGENT_ID' is live and polling SimCore (confirmed after ${WAITED}s)"
  # Removed the way it was installed: a root install needs root, a user-mode
  # install is removed by the user who made it.
  if [ "$(id -u)" = "0" ]; then
    cs_say "uninstall: curl -fsSL '$SERVER/api/agents/install?uninstall=1' | sudo bash   # installed as root"
  else
    cs_say "uninstall: curl -fsSL '$SERVER/api/agents/install?uninstall=1' | bash   # as $(id -un), who installed it"
  fi
else
  STATUS_HINT="systemctl status $SVC"
  [ "$(id -u)" = "0" ] || STATUS_HINT="systemctl --user status $SVC"
  cs_fail verify AGENT_NEVER_CHECKED_IN \
    "'$AGENT_ID' never polled $SERVER/api/agents again within ${WAIT_TOTAL}s (last_seen never advanced past '$BASELINE_SEEN')" \
    "the process may not be running or unable to reach $SERVER — OR, on a re-install, a STALE process on a DIFFERENT --id may still be holding the service. Check: $STATUS_HINT ; ps aux | grep cortexsim-agent ; tail $LOG_FILE — then re-run this installer"
fi
"""


_POSIX_UNINSTALLER = r"""#!/usr/bin/env bash
# --- CortexSim beacon uninstaller (Linux / macOS) ----------------------------
#
#   curl -fsSL '<server>/api/agents/install?uninstall=1' | bash
#   (a beacon installed as root needs:  ... | sudo bash)
#
# A DC leaving a customer site needs one line that takes the beacon back off the
# endpoint AND an honest answer about whether it did. Every location is checked
# (no `set -e`: one missing item must not stop the rest), and what was FOUND is
# tracked separately from what was REMOVED:
#   - nothing found              -> "nothing to remove", exit 0
#   - everything found removed   -> "done", exit 0
#   - anything found but kept    -> FAILED code=UNINSTALL_INCOMPLETE naming each
#                                   item, exit 1
# A non-root run that can SEE a root install refuses up front (NEEDS_ROOT)
# instead of half-removing it and calling the host clean.
#
# A root run (sudo) also sweeps the invoking user's home ($SUDO_USER) and every
# home under /home and /Users that holds a user-mode install. sudo resets HOME
# to root's, so a user-mode beacon used to survive a run that printed "done",
# and its systemd --user unit (Restart=always) brought it back ten seconds later.
#
# Env: CORTEXSIM_BIN_DIR / CORTEXSIM_LOG -- the same overrides the installer
# honours, so a relocated install is found too. sudo drops the environment, so
# pass them after it:  ... | sudo CORTEXSIM_BIN_DIR=/opt/x bash
set -uo pipefail

SVC="cortexsim-agent"
PLIST_LABEL="com.paloaltonetworks.cortexsim.agent"
PROC_PATTERN='cortexsim-agent --server'

SYS_UNIT="/etc/systemd/system/$SVC.service"
SYS_PLIST="/Library/LaunchDaemons/$PLIST_LABEL.plist"
SYS_BIN="/usr/local/bin/cortexsim-agent"
SYS_LOG="/var/log/cortexsim-agent.log"
# Where other users' homes live. Only a root run looks there.
HOME_ROOTS=("/home" "/Users")
# Where the beacon itself caches and stages tools (agent/beacon/artifact.go:
# getArtifactCacheDir, findExecutableFallback, stageRootFor). The cache holds
# the staged dual-use tools (linpeas, pspy, ...), so an uninstall that skips it
# would call a host clean that still carries them.
SYS_CACHE="/opt/cortexsim/cache"
SYS_TOOLS="/opt/cortexsim/tools"
STAGE_ROOT="/tmp/.cortexsim"
TMP_CACHE="${TMPDIR:-/tmp}"; TMP_CACHE="${TMP_CACHE%/}/cortexsim-cache"
USER_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cortexsim"
EXTRA_DIRS=""
for v in "${CORTEXSIM_CACHE_DIR:-}" "${CORTEXSIM_STAGE_ROOT:-}"; do
  case "$v" in
    /*cortexsim*) EXTRA_DIRS="$EXTRA_DIRS $v" ;;   # never delete a tree we did not name
  esac
done
EXTRA_BIN=""
if [ -n "${CORTEXSIM_BIN_DIR:-}" ]; then EXTRA_BIN="${CORTEXSIM_BIN_DIR%/}/cortexsim-agent"; fi
EXTRA_LOG=""
case "${CORTEXSIM_LOG:-}" in
  *cortexsim*) EXTRA_LOG="$CORTEXSIM_LOG" ;;   # never delete a log we did not name
esac

FOUND=0
REMOVED=0
LEFT=""
IS_ROOT=0
[ "$(id -u)" = "0" ] && IS_ROOT=1

cs_say() { echo "[cortexsim] $*"; }
cs_found() { FOUND=$((FOUND+1)); }
cs_gone() { cs_say "removed: $1"; REMOVED=$((REMOVED+1)); }
cs_left() {  # item why
  cs_say "NOT removed: $1 ($2)" >&2
  LEFT="$LEFT
[cortexsim]          - $1 ($2)"
}
cs_fail() {  # code what fix
  echo "" >&2
  echo "[cortexsim] FAILED  stage=uninstall  code=$1" >&2
  echo "[cortexsim]   what : $2" >&2
  echo "[cortexsim]   fix  : $3" >&2
  cs_say "the agent row stays in SimCore until you delete it: DELETE /api/agents/<agent_id>"
  exit 1
}

cs_rm_file() {  # path label
  [ -e "$1" ] || [ -L "$1" ] || return 0
  cs_found
  if rm -f "$1" 2>/dev/null && [ ! -e "$1" ] && [ ! -L "$1" ]; then
    cs_gone "$2 $1"
  else
    cs_left "$2 $1" "rm failed"
  fi
}

cs_rm_dir() {  # path label
  [ -e "$1" ] || [ -L "$1" ] || return 0
  cs_found
  if rm -rf "$1" 2>/dev/null && [ ! -e "$1" ] && [ ! -L "$1" ]; then
    cs_gone "$2 $1"
  else
    cs_left "$2 $1" "rm -rf failed"
  fi
}

cs_home_of() {  # login -> its home directory, or nothing
  local h=""
  case "$1" in ""|*[!A-Za-z0-9._-]*) return 0 ;; esac   # it reaches eval below
  if command -v getent >/dev/null 2>&1; then h="$(getent passwd "$1" 2>/dev/null | cut -d: -f6)"; fi
  if [ -z "$h" ]; then
    h="$(eval "printf '%s' ~$1" 2>/dev/null)"
    case "$h" in "~"*) h="" ;; esac
  fi
  printf '%s' "$h"
}

cs_owner_of() {  # path -> the login that owns it, or nothing (GNU stat, then BSD)
  stat -c %U "$1" 2>/dev/null || stat -f %Su "$1" 2>/dev/null || true
}

cs_as_user() {  # login uid verb...  -- `systemctl --user <verb>` for another login, from root
  local u="$1" uid="$2"
  shift 2
  systemctl --user --machine="$u@.host" "$@" >/dev/null 2>&1 && return 0
  if command -v runuser >/dev/null 2>&1; then
    # systemd older than 248 has no --machine= for --user. stdin is the script.
    runuser -u "$u" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
      DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
      systemctl --user "$@" >/dev/null 2>&1 </dev/null && return 0
  fi
  return 1
}

# -- whose user-mode install to sweep ------------------------------------------
# This shell's own HOME always. A root run adds the invoking user's and any
# home that holds a user-mode install, because sudo points HOME at root's.
HOMES=("$HOME")
cs_add_home() {  # dir
  local known
  [ -n "${1:-}" ] && [ -d "$1" ] || return 0
  for known in "${HOMES[@]}"; do [ "$known" = "$1" ] && return 0; done
  HOMES+=("$1")
}
if [ "$IS_ROOT" = "1" ]; then
  if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER:-}" != "root" ]; then
    cs_add_home "$(cs_home_of "$SUDO_USER")"
  fi
  for base in "${HOME_ROOTS[@]}"; do
    for cand in "$base"/*; do
      if [ -e "$cand/.config/systemd/user/$SVC.service" ] || [ -e "$cand/Library/LaunchAgents/$PLIST_LABEL.plist" ] \
         || [ -e "$cand/.local/bin/cortexsim-agent" ] || [ -e "$cand/.cortexsim" ]; then
        cs_add_home "$cand"
      fi
    done
  done
fi

# -- preflight: a root install needs root to remove ----------------------------
if [ "$IS_ROOT" != "1" ]; then
  NEED=""
  for p in "$SYS_UNIT" "$SYS_PLIST" "$SYS_BIN" "$SYS_LOG"; do
    if [ -e "$p" ] || [ -L "$p" ]; then NEED="$NEED $p"; fi
  done
  if [ -n "$NEED" ]; then
    cs_fail NEEDS_ROOT \
      "a root-installed beacon is present (${NEED# }) and this shell is $(id -un 2>/dev/null || echo non-root); nothing was removed" \
      "re-run with sudo:  curl -fsSL '<server>/api/agents/install?uninstall=1' | sudo bash"
  fi
fi

# -- supervisors: stop first, so nothing restarts what we remove ---------------
if [ -e "$SYS_UNIT" ] || [ -L "$SYS_UNIT" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now "$SVC" >/dev/null 2>&1 || true
  fi
  cs_rm_file "$SYS_UNIT" "system unit"
  if command -v systemctl >/dev/null 2>&1; then systemctl daemon-reload >/dev/null 2>&1 || true; fi
fi
if [ -e "$SYS_PLIST" ] || [ -L "$SYS_PLIST" ]; then
  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "system/$PLIST_LABEL" >/dev/null 2>&1 || true
    launchctl unload -w "$SYS_PLIST" >/dev/null 2>&1 || true
  fi
  cs_rm_file "$SYS_PLIST" "launchd job"
fi

cs_stop_user_install() {  # home  -- that login's systemd --user unit and launchd agent
  local home="$1" user="" uid="" unit plist stuck=0
  if [ "$IS_ROOT" = "1" ]; then
    # Root acting on another login's home: its user manager is not root's.
    user="$(cs_owner_of "$home")"
    case "$user" in ""|root|UNKNOWN|*[!A-Za-z0-9._-]*) user="" ;; esac
    if [ -n "$user" ]; then uid="$(id -u "$user" 2>/dev/null || true)"; fi
    [ -n "$uid" ] || user=""
  fi
  unit="$home/.config/systemd/user/$SVC.service"
  if [ -e "$unit" ] || [ -L "$unit" ]; then
    if command -v systemctl >/dev/null 2>&1; then
      if [ -z "$user" ]; then
        systemctl --user disable --now "$SVC" >/dev/null 2>&1 || true
      elif ! cs_as_user "$user" "$uid" disable --now "$SVC"; then
        # Nobody answered for that login. With no user manager running, nothing
        # supervises the beacon. With one running that root could not reach,
        # Restart=always brings back whatever is killed below.
        if systemctl is-active --quiet "user@$uid.service" >/dev/null 2>&1; then stuck=1; fi
      fi
    fi
    if [ "$stuck" = "1" ]; then
      # The unit file is kept on purpose: that login's own run needs it there
      # to disable the unit.
      cs_found
      cs_left "user unit $unit" "the systemd user manager of $user is running and root could not reach it, so it restarts the beacon; run this uninstaller as $user, without sudo"
    else
      cs_rm_file "$unit" "user unit"
      rm -f "$home/.config/systemd/user/default.target.wants/$SVC.service" 2>/dev/null || true
      if command -v systemctl >/dev/null 2>&1; then
        if [ -z "$user" ]; then systemctl --user daemon-reload >/dev/null 2>&1 || true
        else cs_as_user "$user" "$uid" daemon-reload || true; fi
      fi
    fi
  fi
  plist="$home/Library/LaunchAgents/$PLIST_LABEL.plist"
  if [ -e "$plist" ] || [ -L "$plist" ]; then
    if command -v launchctl >/dev/null 2>&1; then
      # gui/<uid> for a desktop session, user/<uid> for one reached over ssh.
      [ -n "$uid" ] || uid="$(id -u)"
      launchctl bootout "gui/$uid/$PLIST_LABEL" >/dev/null 2>&1 || true
      launchctl bootout "user/$uid/$PLIST_LABEL" >/dev/null 2>&1 || true
      launchctl unload -w "$plist" >/dev/null 2>&1 || true
    fi
    cs_rm_file "$plist" "launchd job"
  fi
}
for H in "${HOMES[@]}"; do cs_stop_user_install "$H"; done

# -- any beacon still running (the nohup fallback, or a stale one) -------------
# Killing is not the same as gone: verify with pgrep, escalate to SIGKILL once,
# and report survivors (typically owned by another user) instead of claiming them.
if command -v pgrep >/dev/null 2>&1; then
  if pgrep -f "$PROC_PATTERN" >/dev/null 2>&1; then
    cs_found
    pkill -f "$PROC_PATTERN" >/dev/null 2>&1 || true
    sleep 1
    if pgrep -f "$PROC_PATTERN" >/dev/null 2>&1; then
      pkill -9 -f "$PROC_PATTERN" >/dev/null 2>&1 || true
      sleep 1
    fi
    if pgrep -f "$PROC_PATTERN" >/dev/null 2>&1; then
      cs_left "running beacon process(es) $(pgrep -f "$PROC_PATTERN" 2>/dev/null | tr '\n' ' ')" \
        "still running after SIGKILL -- owned by another user?"
    else
      cs_gone "running beacon process(es)"
    fi
  fi
elif command -v pkill >/dev/null 2>&1; then
  # No pgrep to verify with: report the kill as what it is, unverified.
  if pkill -f "$PROC_PATTERN" >/dev/null 2>&1; then
    cs_found; cs_gone "running beacon process(es) (signalled; not verifiable without pgrep)"
  fi
fi

# -- files ----------------------------------------------------------------------
SEEN=""
BINS=("$SYS_BIN")
for H in "${HOMES[@]}"; do BINS+=("$H/.local/bin/cortexsim-agent"); done
BINS+=("$EXTRA_BIN")
for b in "${BINS[@]}"; do
  [ -n "$b" ] || continue
  case " $SEEN " in *" $b "*) continue ;; esac
  SEEN="$SEEN $b"
  cs_rm_file "$b" "binary"
done
for l in "$SYS_LOG" "$EXTRA_LOG"; do
  [ -n "$l" ] || continue
  cs_rm_file "$l" "log"
done
for H in "${HOMES[@]}"; do
  cs_rm_dir "$H/.cortexsim" "state/log directory"
  cs_rm_dir "$H/.cache/cortexsim" "staging cache"
  cs_rm_dir "$H/Library/Caches/cortexsim" "staging cache"
done
for d in "$SYS_CACHE" "$SYS_TOOLS" "$STAGE_ROOT" "$TMP_CACHE" "$USER_CACHE" $EXTRA_DIRS; do
  cs_rm_dir "$d" "staging cache"
done
rmdir "${SYS_CACHE%/cache}" 2>/dev/null || true   # their parent, only once empty

# -- verdict --------------------------------------------------------------------
if [ -n "$LEFT" ]; then
  cs_fail UNINSTALL_INCOMPLETE \
    "found $FOUND item(s), removed $REMOVED; still present:$LEFT" \
    "re-run with sudo:  curl -fsSL '<server>/api/agents/install?uninstall=1' | sudo bash  -- then check the items above by hand"
fi
if [ "$FOUND" = "0" ]; then
  cs_say "nothing to remove -- no CortexSim beacon found on this host"
else
  cs_say "done -- removed $REMOVED of $FOUND item(s)"
fi
cs_say "the agent row stays in SimCore until you delete it: DELETE /api/agents/<agent_id>"
"""


# ---------------------------------------------------------------------------
# Windows (PowerShell 5.1 floor)
# ---------------------------------------------------------------------------
#
# Both Windows templates obey four rules; tests/api/test_windows_installer.py and
# tests/api/test_agents_uninstaller.py hold each one:
#
#   1. Pure ASCII. Windows PowerShell 5.1 reads a BOM-less .ps1 as cp1252, where
#      byte 0x94 of a UTF-8 em-dash is a closing smart quote that ends a string.
#      The downloaded install-cortexsim-agent.ps1 did not parse on the floor.
#   2. The body runs inside `& { ... }` and never calls a bare `exit`: under
#      `iwr | iex` it runs in the operator's own session, and `exit` closed it.
#      Failures `throw`; one top-level catch prints them, then exits only when
#      the script is a FILE ($PSCommandPath set), else returns.
#   3. Every native tool (sc.exe, icacls) runs through Cs-Exec, with
#      $ErrorActionPreference relaxed for that call only (5.1 turns redirected
#      native stderr into a terminating error under 'Stop'), and its
#      $LASTEXITCODE is checked on the very next line. A native tool never throws.
#   4. No PowerShell-7-only syntax (?:, ??, &&, ||, ?.) and no assignment to an
#      automatic variable ($Args was clobbered by the old installer).

_WINDOWS_COMMON_FUNCTIONS = r"""function Cs-Say([string]$Msg) { Write-Host "[cortexsim] $Msg" }

function Cs-Exec([string]$Exe, [string[]]$Argv) {
  # Native call under its own error preference; the CALLER checks $LASTEXITCODE.
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $out = ''
  try {
    $global:LASTEXITCODE = 0
    $out = (& $Exe @Argv 2>&1 | Out-String)
  } catch {
    $global:LASTEXITCODE = 9009
    $out = "$($_.Exception.Message)"
  } finally {
    $ErrorActionPreference = $prev
  }
  return ("$out".Trim() -replace '\s+', ' ')
}
"""

_WINDOWS_INSTALLER = r"""# --- CortexSim beacon installer (Windows / PowerShell 5.1+) ----------------
#
#   From an ELEVATED Windows PowerShell (Run as administrator):
#   iwr '<server>/api/agents/install?os=windows&token=<tok>' -UseBasicParsing | iex
#
#   or save it and run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install-cortexsim-agent.ps1
#
# Same contract as the POSIX installer: no compiler on the endpoint; the beacon
# comes prebuilt and sha256-verified from the SimCore that served this script;
# it is registered as the Windows service CortexSimAgent (LocalSystem,
# automatic start, restart on failure). OK is reported only after the service
# is still Running past the Service Control Manager's start window AND the
# beacon has polled SimCore on its own. Every failure prints a stable code with
# a fix line and is reported to SimCore (GET /api/agents/install/attempts).
#
# Pure ASCII, and the body runs inside "& { ... }" with no bare `exit`: a
# failure under "iwr | iex" leaves your shell, its $ErrorActionPreference and
# its variables exactly as they were.
#
# Env overrides:
#   CORTEXSIM_SERVER / CORTEXSIM_TOKEN / CORTEXSIM_AGENT_ID / CORTEXSIM_INTERVAL
#   CORTEXSIM_BIN=C:\path\cortexsim-agent.exe  pre-staged beacon (air-gapped host)
#   CORTEXSIM_ALLOW_UNSUPERVISED=1  not elevated: run a per-user beacon instead of
#                                   a service -- it dies at logoff and at reboot
#   CORTEXSIM_NO_PROXY=1            reach SimCore directly, bypassing the system proxy
#   CORTEXSIM_TELEMETRY=0           suppress the install-status POSTs
& {
Set-StrictMode -Off
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Cs = @{ Stage = 'detect'; Code = ''; What = ''; Fix = ''; Work = '' }
$UrlPattern = '^https?://[A-Za-z0-9.\[\]:_~-]+(/[A-Za-z0-9.:/\[\]_~-]*)?$'

@@COMMON@@
function Cs-Report([string]$Stage, [string]$Code, [string]$Detail) {
  if ($env:CORTEXSIM_TELEMETRY -eq '0') { return }
  if ("$Server" -notmatch $UrlPattern) { return }
  try {
    $d = "$Detail"
    if ($d.Length -gt 1900) { $d = $d.Substring(0, 1900) }
    $b = @{ stage = $Stage; code = $Code; detail = $d; hostname = $HostN; os = 'windows';
            arch = $Arch; agent_id = $AgentId } | ConvertTo-Json -Compress
    $null = Cs-Http 'POST' "$Server/api/agents/install/telemetry" $b 5000 ''
  } catch { }
}

function Cs-Fail([string]$Stage, [string]$Code, [string]$What, [string]$Fix) {
  $Cs.Stage = $Stage; $Cs.Code = $Code; $Cs.What = $What; $Cs.Fix = $Fix
  Cs-Report $Stage $Code $What
  throw "CORTEXSIM_FAILED:$Code"
}

function Cs-Http([string]$Method, [string]$Uri, [string]$Body, [int]$TimeoutMs, [string]$OutFile) {
  # [Net.HttpWebRequest], not Invoke-WebRequest: one exception shape on 5.1 and
  # 7, no Internet Explorer engine dependency, no progress-bar slowdown, and the
  # status + body of a non-2xx answer are kept instead of thrown away.
  # Status 0 means SimCore was never reached.
  $res = @{ Status = 0; Body = ''; Error = ''; WebStatus = ''; Ok = $false }
  $resp = $null
  try {
    $req = [Net.WebRequest]::Create($Uri)
    $req.Method = $Method
    $req.Timeout = $TimeoutMs
    $req.ReadWriteTimeout = [Math]::Max($TimeoutMs, 30000)
    if ($env:CORTEXSIM_NO_PROXY -eq '1') { $req.Proxy = New-Object Net.WebProxy }
    # Per request, not [Net.ServicePointManager]: under iex that is the
    # operator's own session, and a global would outlive this script.
    try { $req.ServicePoint.Expect100Continue = $false } catch { }
    if ($Body) {
      $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
      $req.ContentType = 'application/json'
      $req.ContentLength = $bytes.Length
      $rs = $req.GetRequestStream()
      try { $rs.Write($bytes, 0, $bytes.Length) } finally { $rs.Close() }
    }
    try {
      $resp = $req.GetResponse()
    } catch {
      $ex = $_.Exception
      while ($ex -and -not ($ex -is [Net.WebException])) { $ex = $ex.InnerException }
      if ($ex -and $ex.Response) { $resp = $ex.Response } else { throw }
    }
    $res.Status = [int]$resp.StatusCode
    $stream = $resp.GetResponseStream()
    if ($OutFile -and $res.Status -eq 200) {
      $fs = [IO.File]::Create($OutFile)
      try { $stream.CopyTo($fs) } finally { $fs.Close() }
    } else {
      $sr = New-Object IO.StreamReader($stream)
      $res.Body = $sr.ReadToEnd()
    }
    $res.Ok = $true
  } catch {
    $ex = $_.Exception
    while ($ex -and -not ($ex -is [Net.WebException])) { $ex = $ex.InnerException }
    if ($ex) { $res.WebStatus = "$($ex.Status)"; $res.Error = "$($ex.Message)" }
    else { $res.Error = "$($_.Exception.Message)" }
  } finally {
    if ($resp) { try { $resp.Close() } catch { } }
  }
  return $res
}

function Cs-Detail([string]$Body) {
  # The server's structured {error, code, detail}, folded onto one line.
  $t = "$Body"
  try {
    $j = $t | ConvertFrom-Json
    $d = $j.detail
    if ($d -and $d.code) { return ("$($d.code): $($d.error) -- $($d.detail)") }
    if ($j.code) { return ("$($j.code): $($j.error)") }
  } catch { }
  $t = ($t -replace '\s+', ' ').Trim()
  if ($t.Length -gt 400) { $t = $t.Substring(0, 400) }
  return $t
}

function Cs-FailUnreachable([string]$Stage, [string]$Code, $Res) {
  if ($Res.WebStatus -eq 'TrustFailure' -or $Res.WebStatus -eq 'SecureChannelFailure') {
    Cs-Fail $Stage 'TLS_UNTRUSTED' "TLS to $Server failed ($($Res.WebStatus)): $($Res.Error)" 'import the SimCore certificate into Cert:\LocalMachine\Root on this host, or fetch the installer over http:// on an isolated lab network'
  }
  Cs-Fail $Stage $Code "cannot reach $Server ($($Res.WebStatus)): $($Res.Error)" ('check routing / proxy / firewall from this host to SimCore (CORTEXSIM_NO_PROXY=1 skips the system proxy), then re-run' + $LoopbackHint)
}

function Cs-Enroll {
  Cs-Say "enrolling with token ...$($Token.Substring([Math]::Max(0, $Token.Length - 6)))"
  $payload = @{ token = $Token; hostname = $HostN; os = 'windows';
                capabilities = @('shell', 'powershell') } | ConvertTo-Json -Compress
  $r = Cs-Http 'POST' "$Server/api/agents/enroll" $payload 30000 ''
  if ($r.Status -eq 0) { Cs-FailUnreachable 'enroll' 'SERVER_UNREACHABLE' $r }
  if ($r.Status -eq 403) {
    Cs-Fail 'enroll' 'ENROLL_DENIED' "HTTP 403 from /api/agents/enroll -- $(Cs-Detail $r.Body)" "the token is unknown, expired, revoked or used up: mint a fresh token (POST $Server/api/agents/enroll/tokens, or the console's Targets view) and re-run"
  }
  if ($r.Status -ne 200) {
    Cs-Fail 'enroll' 'ENROLL_FAILED' "HTTP $($r.Status) from /api/agents/enroll -- $(Cs-Detail $r.Body)" 'SimCore rejected the request for a reason other than the token; read the detail above (and GET /api/health on SimCore), then re-run'
  }
  $id = ''
  try { $id = "$(($r.Body | ConvertFrom-Json).agent_id)" } catch { $id = '' }
  if (-not $id) {
    Cs-Fail 'enroll' 'NO_AGENT_ID' "enroll returned 200 with no agent_id: $(Cs-Detail $r.Body)" 'SimCore/installer version mismatch: re-fetch the one-liner from this SimCore'
  }
  if ($id -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
    Cs-Fail 'enroll' 'NO_AGENT_ID' "enroll returned an agent_id this installer will not put on a service command line: '$id'" 'SimCore/installer version mismatch: re-fetch the one-liner from this SimCore'
  }
  return $id
}

function Cs-FetchBinary([string]$Dest) {
  $url = "$Server/api/agents/binary?os=windows&arch=$Arch"
  Cs-Say "fetching prebuilt beacon (windows/$Arch)"
  $r = Cs-Http 'GET' $url '' 300000 $Dest
  if ($r.Status -eq 0) { Cs-FailUnreachable 'download' 'SERVER_UNREACHABLE' $r }
  if ($r.Status -eq 404) {
    Cs-Fail 'download' 'BINARY_UNAVAILABLE' "SimCore serves no windows/$Arch beacon -- $(Cs-Detail $r.Body)" 'on the SimCore host run make agent-dist (or rebuild the image) and re-run; air-gapped: copy the beacon over and set $env:CORTEXSIM_BIN'
  }
  if ($r.Status -ne 200) {
    Cs-Fail 'download' 'BINARY_FETCH_FAILED' "HTTP $($r.Status) from /api/agents/binary -- $(Cs-Detail $r.Body)" "GET $url by hand to read the structured error; a proxy answering for SimCore looks like this too (CORTEXSIM_NO_PROXY=1)"
  }
  if (-not $r.Ok) {
    Cs-Fail 'download' 'BINARY_FETCH_FAILED' "the download to $Dest did not complete: $($r.Error)" 're-run; if it repeats, check Cortex XDR/Defender alerts for cortexsim-agent.exe (a blocked write looks like this too)'
  }
}

function Cs-FetchDigest {
  $r = Cs-Http 'GET' "$Server/api/agents/binary/sha256?os=windows&arch=$Arch" '' 30000 ''
  if ($r.Status -eq 0) {
    Cs-Fail 'verify' 'DIGEST_FETCH_FAILED' "cannot reach $Server for the beacon's sha256 ($($r.WebStatus)): $($r.Error)" ('the binary was fetched but its digest was not, so it cannot be verified; re-run' + $LoopbackHint)
  }
  if ($r.Status -ne 200) {
    Cs-Fail 'verify' 'DIGEST_FETCH_FAILED' "HTTP $($r.Status) from /api/agents/binary/sha256 -- $(Cs-Detail $r.Body)" 'the binary was fetched but its digest was not, so it cannot be verified; re-run'
  }
  $d = "$($r.Body)".Trim().ToLower()
  if ($d -notmatch '^[0-9a-f]{64}$') {
    Cs-Fail 'verify' 'DIGEST_FETCH_FAILED' "SimCore answered the digest request with something that is not a sha256: '$(Cs-Detail $r.Body)'" 'a proxy or captive portal is answering for SimCore; set CORTEXSIM_NO_PROXY=1 or fix routing, then re-run'
  }
  return $d
}

function Cs-AssertPresent([string]$Path, [string]$Stage) {
  # Endpoint protection removes or locks a fresh beacon between the write and
  # the next read. Name that, instead of failing inside Get-FileHash with a raw
  # .NET error or running a 0-byte file.
  $why = ''
  $hash = ''
  try {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { $why = 'the file is gone' }
    elseif ((Get-Item -LiteralPath $Path -Force).Length -eq 0) { $why = 'the file is 0 bytes' }
    else { $hash = "$((Get-FileHash -Algorithm SHA256 -LiteralPath $Path -ErrorAction Stop).Hash)".ToLower() }
  } catch {
    $why = "the file cannot be read ($($_.Exception.GetType().Name): $($_.Exception.Message))"
  }
  if (-not $hash) {
    if (-not $why) { $why = 'no digest could be computed' }
    Cs-Fail $Stage 'BINARY_QUARANTINED' "$Path : $why -- endpoint protection most likely removed or locked it" 'check Cortex XDR/Defender alerts for cortexsim-agent.exe; add a lab exclusion or allow-list the sha256'
  }
  return $hash
}

function Cs-StopProcesses([string]$OnlyPath) {
  $procs = @(Get-Process -Name 'cortexsim-agent' -ErrorAction SilentlyContinue)
  if ($OnlyPath) { $procs = @($procs | Where-Object { $_.Path -eq $OnlyPath }) }
  if ($procs.Count -eq 0) { return }
  Cs-Say "stopping $($procs.Count) running cortexsim-agent process(es): $(($procs | ForEach-Object { $_.Id }) -join ', ')"
  foreach ($p in $procs) { try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { } }
  try { $procs | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue } catch { }
  $alive = @($procs | Where-Object { $a = $true; try { $a = -not $_.HasExited } catch { $a = $true }; $a })
  if ($alive.Count -gt 0) {
    Cs-Fail 'install' 'STALE_PROCESS' "cortexsim-agent pid(s) $(($alive | ForEach-Object { $_.Id }) -join ', ') would not stop" 'stop them from an elevated shell (Stop-Process -Name cortexsim-agent -Force) or reboot, then re-run'
  }
}

function Cs-Stamp($Value) {
  # last_seen as a [datetime]: 5.1's ConvertFrom-Json hands back the ISO
  # string, 7's hands back a DateTime already. Compare instants, not strings.
  if ($null -eq $Value) { return $null }
  if ($Value -is [datetime]) { return $Value }
  $out = [datetime]::MinValue
  $ok = [datetime]::TryParse("$Value", [Globalization.CultureInfo]::InvariantCulture,
                             [Globalization.DateTimeStyles]::RoundtripKind, [ref]$out)
  if ($ok) { return $out }
  return $null
}

function Cs-LastSeen {
  $r = Cs-Http 'GET' "$Server/api/agents" '' 10000 ''
  if ($r.Status -ne 200) { return $null }
  try { $j = $r.Body | ConvertFrom-Json } catch { return $null }
  foreach ($a in @($j.agents)) {
    if ("$($a.agent_id)" -eq $AgentId) { return (Cs-Stamp $a.last_seen) }
  }
  return $null
}

try {
  # -- stage: detect ------------------------------------------------------------
  try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch { }
  $Server   = if ($env:CORTEXSIM_SERVER)   { "$env:CORTEXSIM_SERVER" }   else { '@@SERVER@@' }
  $AgentId  = if ($env:CORTEXSIM_AGENT_ID) { "$env:CORTEXSIM_AGENT_ID" } else { '@@AGENT_ID@@' }
  $Interval = if ($env:CORTEXSIM_INTERVAL) { "$env:CORTEXSIM_INTERVAL" } else { '@@INTERVAL@@' }
  $Token    = if ($env:CORTEXSIM_TOKEN)    { "$env:CORTEXSIM_TOKEN" }    else { '@@TOKEN@@' }
  $Server   = $Server.Trim().TrimEnd('/')
  $LoopbackHint = '@@LOOPBACK_HINT@@'
  $HostN    = "$env:COMPUTERNAME"
  if (-not $HostN) { $HostN = [Environment]::MachineName }
  $Arch     = 'unknown'
  $Svc      = 'CortexSimAgent'
  $SvcLog   = "$env:ProgramData\CortexSim\agent.log"
  Cs-Say "target server : $Server"

  # Env overrides reach the service command line too, so they get the same
  # allow-list the server applied to the baked values.
  if ($Server -notmatch $UrlPattern) {
    Cs-Fail 'detect' 'BAD_SERVER' "CORTEXSIM_SERVER / baked server '$Server' is not http(s)://host[:port][/path]" 'set CORTEXSIM_SERVER=http://<simcore-ip>:8888, or clear it to use the server this script came from'
  }
  if ($AgentId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
    Cs-Fail 'detect' 'BAD_AGENT_ID' "agent id '$AgentId' is not 1-64 letters, digits, '.', '_' or '-'" 'fix or clear CORTEXSIM_AGENT_ID (with a token, SimCore assigns the id)'
  }
  if ($Interval -notmatch '^[0-9]{1,4}$' -or [int]$Interval -lt 1 -or [int]$Interval -gt 3600) {
    Cs-Fail 'detect' 'BAD_INTERVAL' "interval '$Interval' is not 1-3600 seconds" 'fix or clear CORTEXSIM_INTERVAL'
  }
  $IntervalN = [int]$Interval
  if ($Token -and $Token -notmatch '^cxs_[A-Za-z0-9_-]{20,128}$') {
    Cs-Fail 'detect' 'BAD_TOKEN' 'the enrollment token is not cxs_ followed by 20-128 URL-safe characters' 'copy the token again from the console, or mint a fresh one'
  }
  @@PREFLIGHT@@
  if ($env:OS -ne 'Windows_NT') {
    Cs-Fail 'detect' 'UNSUPPORTED_OS' "this is the Windows installer and this host is not Windows (OS='$env:OS')" "on Linux or macOS run:  curl -fsSL '$Server/api/agents/install?os=linux' | bash"
  }
  if (-not [Environment]::Is64BitOperatingSystem) {
    Cs-Fail 'detect' 'UNSUPPORTED_ARCH' '32-bit Windows is not supported' 'use a 64-bit host'
  }
  $Arch = 'amd64'
  if ("$env:PROCESSOR_ARCHITECTURE" -eq 'ARM64' -or "$env:PROCESSOR_ARCHITEW6432" -eq 'ARM64') {
    Cs-Say 'NOTE: ARM64 Windows -- the amd64 beacon runs under x64 emulation'
  }

  # Elevation is decided BEFORE enrolling: an unelevated run used to enrol
  # first and only then find it could not install a service.
  $IsAdmin = $false
  try {
    $IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch { $IsAdmin = $false }
  $Unsupervised = $false
  if (-not $IsAdmin) {
    if ($env:CORTEXSIM_ALLOW_UNSUPERVISED -ne '1') {
      Cs-Fail 'detect' 'NOT_ELEVATED' 'this PowerShell session is not elevated, so the CortexSimAgent service cannot be installed (nothing was enrolled or changed)' "open Windows PowerShell as Administrator (right-click > Run as administrator) and re-run the one-liner. For a per-user beacon that dies at logoff and at reboot instead, first run:  `$env:CORTEXSIM_ALLOW_UNSUPERVISED='1'"
    }
    $Unsupervised = $true
    Cs-Say 'WARN: not elevated and CORTEXSIM_ALLOW_UNSUPERVISED=1 -- installing a per-user beacon, NOT a service: it dies at logoff and at reboot'
  }

  $PreStaged = "$env:CORTEXSIM_BIN"
  if ($PreStaged -and -not (Test-Path -LiteralPath $PreStaged -PathType Leaf)) {
    Cs-Fail 'detect' 'BIN_NOT_FOUND' "CORTEXSIM_BIN='$PreStaged' is not a file" 'fix the path, or clear $env:CORTEXSIM_BIN to download the beacon from SimCore'
  }
  Cs-Say "platform      : windows/$Arch ($HostN)"

  # -- stage: enroll --------------------------------------------------------------
  $Cs.Stage = 'enroll'
  if ($Token) {
    $AgentId = Cs-Enroll
    Cs-Say "enrolled as   : $AgentId"
  } else {
    Cs-Say "agent id      : $AgentId (self-asserted -- prefer ?token= so SimCore assigns it)"
  }

  # -- stage: download + verify ---------------------------------------------------
  $Cs.Stage = 'download'
  $Work = Join-Path ([IO.Path]::GetTempPath()) ('cortexsim-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Force -Path $Work | Out-Null
  $Cs.Work = $Work
  if ($PreStaged) {
    Cs-Say "using pre-staged binary: $PreStaged"
    $Actual = Cs-AssertPresent $PreStaged 'verify'
    $dr = Cs-Http 'GET' "$Server/api/agents/binary/sha256?os=windows&arch=$Arch" '' 30000 ''
    $Expect = "$($dr.Body)".Trim().ToLower()
    if ($dr.Status -eq 200 -and $Expect -match '^[0-9a-f]{64}$') {
      if ($Expect -ne $Actual) {
        Cs-Fail 'verify' 'CHECKSUM_MISMATCH' "CORTEXSIM_BIN has sha256 $Actual but SimCore serves $Expect" 'do NOT run this binary: copy the beacon from this SimCore again (or clear CORTEXSIM_BIN to download it)'
      }
      Cs-Say "checksum OK   : $($Actual.Substring(0, 16))... (matches SimCore)"
    } elseif ($dr.Status -eq 200) {
      Cs-Fail 'verify' 'DIGEST_FETCH_FAILED' "SimCore answered the digest request with something that is not a sha256: '$(Cs-Detail $dr.Body)'" 'a proxy or captive portal is answering for SimCore; set CORTEXSIM_NO_PROXY=1 or fix routing, then re-run'
    } else {
      Cs-Say "WARN: integrity NOT verified -- SimCore has no digest to compare with (HTTP $($dr.Status)); running the pre-staged binary as-is"
      Cs-Report 'verify' 'CHECKSUM_SKIPPED' "pre-staged CORTEXSIM_BIN sha256=$Actual; digest endpoint HTTP $($dr.Status) $($dr.Error)"
    }
    $Src = $PreStaged
  } else {
    $Dl = Join-Path $Work 'cortexsim-agent.exe'
    Cs-FetchBinary $Dl
    $Actual = Cs-AssertPresent $Dl 'download'
    $Cs.Stage = 'verify'
    $Expect = Cs-FetchDigest
    if ($Expect -ne $Actual) {
      Cs-Fail 'verify' 'CHECKSUM_MISMATCH' "expected $Expect, got $Actual" 'do NOT run this binary: re-fetch, and look for an intercepting TLS proxy'
    }
    Cs-Say "checksum OK   : $($Actual.Substring(0, 16))..."
    $Src = $Dl
  }

  # -- stage: install -------------------------------------------------------------
  $Cs.Stage = 'install'
  if (-not $Unsupervised) {
    $InstallDir = "$env:ProgramData\CortexSim"
    $Bin = "$InstallDir\cortexsim-agent.exe"

    # 1. Stop what is running, so the binary can be replaced and no stale
    #    beacon keeps polling under an old --id.
    $existing = Get-Service -Name $Svc -ErrorAction SilentlyContinue
    if ($existing) {
      Cs-Say "existing service $Svc found ($($existing.Status)) -- stopping it"
      $stopErr = ''
      try {
        if ("$($existing.Status)" -ne 'Stopped') { Stop-Service -Name $Svc -Force -ErrorAction Stop }
        # 90 s: the beacon's service handler may sit in STOP_PENDING for up to
        # its 60 s stop deadline while it tree-kills a step and posts /complete.
        $existing.WaitForStatus('Stopped', '00:01:30')
      } catch { $stopErr = "$($_.Exception.Message)" }
      if ($stopErr) {
        Cs-Fail 'install' 'SERVICE_STOP_FAILED' "could not stop the existing $Svc service: $stopErr" "stop it by hand (Stop-Service $Svc -Force) or reboot, then re-run"
      }
    }
    Cs-StopProcesses ''

    # 2. A directory no other account controls: this binary runs as SYSTEM.
    $dirErr = ''
    if (Test-Path -LiteralPath $InstallDir) {
      try {
        $item = Get-Item -LiteralPath $InstallDir -Force
        if (-not $item.PSIsContainer) { $dirErr = 'it is a file, not a directory' }
        elseif ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $dirErr = 'it is a junction or symbolic link' }
        else {
          $owner = "$((Get-Acl -LiteralPath $InstallDir).GetOwner([Security.Principal.SecurityIdentifier]).Value)"
          if ($owner -ne 'S-1-5-32-544' -and $owner -ne 'S-1-5-18') {
            $dirErr = "it is owned by $owner, not BUILTIN\Administrators (S-1-5-32-544) or SYSTEM (S-1-5-18)"
          }
        }
      } catch { $dirErr = "its owner could not be read: $($_.Exception.Message)" }
      if ($dirErr) {
        Cs-Fail 'install' 'INSTALL_DIR_UNTRUSTED' "$InstallDir already exists and $dirErr; a SYSTEM service will not be installed into a directory another account may control" "inspect it; if it is an earlier CortexSim install, remove it from an elevated shell (iwr '$Server/api/agents/install?os=windows&uninstall=1' -UseBasicParsing | iex), then re-run"
      }
    } else {
      # Created WITH its final DACL and owner, in one call. New-Item would give
      # it ProgramData's inherited ACL (Users may create files, CREATOR OWNER
      # gets full control) until icacls below ran, and in that window a local
      # non-admin could drop cortexsim-agent.exe here with their own explicit
      # ACE that the later Copy-Item would keep: a SYSTEM service binary a
      # non-admin can rewrite.
      try {
        $sec = New-Object System.Security.AccessControl.DirectorySecurity
        $sec.SetAccessRuleProtection($true, $false)
        $inh = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        $none = [Security.AccessControl.PropagationFlags]::None
        foreach ($ace in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'), @('S-1-5-32-545', 'ReadAndExecute'))) {
          $sid = New-Object System.Security.Principal.SecurityIdentifier($ace[0])
          $sec.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid, [Security.AccessControl.FileSystemRights]$ace[1], $inh, $none, 'Allow')))
        }
        $sec.SetOwner((New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')))
        # .NET Framework (Windows PowerShell 5.1) has the two-argument
        # CreateDirectory; .NET Core (PowerShell 7) moved it to an extension.
        if ([IO.Directory].GetMethod('CreateDirectory', [type[]]@([string], [Security.AccessControl.DirectorySecurity]))) {
          [void][IO.Directory]::CreateDirectory($InstallDir, $sec)
        } else {
          [IO.FileSystemAclExtensions]::Create((New-Object IO.DirectoryInfo($InstallDir)), $sec)
        }
        # CreateDirectory on a path that appeared since Test-Path returns it
        # untouched: one created by someone else in that window is not ours.
        $owner = "$((Get-Acl -LiteralPath $InstallDir).GetOwner([Security.Principal.SecurityIdentifier]).Value)"
        if ($owner -ne 'S-1-5-32-544' -and $owner -ne 'S-1-5-18') {
          Cs-Fail 'install' 'INSTALL_DIR_UNTRUSTED' "$InstallDir appeared while the installer was creating it and is owned by $owner" 'inspect it and remove it from an elevated shell, then re-run'
        }
      } catch {
        if ("$($_.Exception.Message)" -like 'CORTEXSIM_FAILED:*') { throw }
        $dirErr = "$($_.Exception.Message)"
      }
      if ($dirErr) {
        Cs-Fail 'install' 'BIN_INSTALL_FAILED' "could not create $InstallDir : $dirErr" 'check free space and permissions under ProgramData'
      }
    }
    $o = Cs-Exec 'icacls.exe' @($InstallDir, '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-32-545:(OI)(CI)RX')
    if ($LASTEXITCODE -ne 0) { Cs-Fail 'install' 'INSTALL_DIR_ACL_FAILED' "icacls $InstallDir exited $LASTEXITCODE : $o" 'run from an elevated Windows PowerShell; check that no policy blocks ACL changes under ProgramData' }
    $o = Cs-Exec 'icacls.exe' @($InstallDir, '/setowner', '*S-1-5-32-544', '/T')
    if ($LASTEXITCODE -ne 0) { Cs-Fail 'install' 'INSTALL_DIR_ACL_FAILED' "icacls /setowner on $InstallDir exited $LASTEXITCODE : $o" 'run from an elevated Windows PowerShell, then re-run' }

    # 3. The binary, re-checked after the copy: AV acts on the new path too.
    $cpErr = ''
    try { Copy-Item -LiteralPath $Src -Destination $Bin -Force -ErrorAction Stop } catch { $cpErr = "$($_.Exception.Message)" }
    if ($cpErr -match 'virus|unwanted software') {
      Cs-Fail 'install' 'BINARY_QUARANTINED' "writing $Bin was blocked: $cpErr" 'check Cortex XDR/Defender alerts for cortexsim-agent.exe; add a lab exclusion or allow-list the sha256'
    }
    if ($cpErr) { Cs-Fail 'install' 'BIN_INSTALL_FAILED' "could not write $Bin : $cpErr" 'check free space; a file held open by a process that would not stop looks like this too (reboot, then re-run)' }
    $Installed = Cs-AssertPresent $Bin 'install'
    if ($Installed -ne $Actual) {
      Cs-Fail 'install' 'BINARY_QUARANTINED' "the installed copy has sha256 $Installed, not the verified $Actual" 'check Cortex XDR/Defender alerts for cortexsim-agent.exe; add a lab exclusion or allow-list the sha256'
    }
    Cs-Say "beacon        : $Bin"

    # 4. The service. A registration left over from a previous install is
    #    deleted first; 1072 means it is marked for deletion and lingers until
    #    every handle to it (services.msc, Event Viewer) is closed.
    $existing = Get-Service -Name $Svc -ErrorAction SilentlyContinue
    if ($existing) {
      $o = Cs-Exec 'sc.exe' @('delete', $Svc)
      if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1072 -and $LASTEXITCODE -ne 1060) { Cs-Fail 'install' 'SERVICE_DELETE_FAILED' "sc.exe delete $Svc exited $LASTEXITCODE : $o" 'remove it by hand from an elevated shell (sc.exe delete CortexSimAgent), then re-run' }
      $gone = $false
      for ($i = 0; $i -lt 30; $i++) {
        if (-not (Get-Service -Name $Svc -ErrorAction SilentlyContinue)) { $gone = $true; break }
        Start-Sleep -Seconds 1
      }
      if (-not $gone) {
        Cs-Fail 'install' 'SERVICE_PENDING_DELETE' "the previous $Svc registration is marked for deletion but still present after 30 s" 'close services.msc, Event Viewer and any tool holding the service open (or reboot), then re-run'
      }
    }
    # New-Service, not sc.exe create: 5.1 re-quotes native arguments, and
    # binPath= "`"$Bin`" ..." reached the SCM mangled. A cmdlet takes the
    # string as-is. Every value in it was validated above.
    $BinPath = '"' + $Bin + '" --server ' + $Server + ' --id ' + $AgentId + ' --interval ' + $IntervalN
    $svcErr = ''
    try {
      New-Service -Name $Svc -BinaryPathName $BinPath -DisplayName 'CortexSim beacon agent' -Description "CortexSim detection-simulation beacon for $Server. Remove: iwr '$Server/api/agents/install?os=windows&uninstall=1' -UseBasicParsing | iex" -StartupType Automatic -ErrorAction Stop | Out-Null
    } catch { $svcErr = "$($_.Exception.Message)" }
    if ($svcErr) {
      Cs-Fail 'install' 'SERVICE_CREATE_FAILED' "New-Service $Svc failed: $svcErr" 'run from an elevated Windows PowerShell; if a policy blocks service creation, use PUSH mode from the console instead'
    }
    $o = Cs-Exec 'sc.exe' @('failure', $Svc, 'reset=', '86400', 'actions=', 'restart/10000/restart/10000/restart/60000')
    if ($LASTEXITCODE -ne 0) { Cs-Fail 'install' 'SERVICE_CONFIG_FAILED' "sc.exe failure $Svc exited $LASTEXITCODE : $o" 'the service exists but will not be restarted after a crash; set it by hand (sc.exe failure CortexSimAgent reset= 86400 actions= restart/10000) and re-run' }
    $o = Cs-Exec 'sc.exe' @('failureflag', $Svc, '1')
    if ($LASTEXITCODE -ne 0) { Cs-Fail 'install' 'SERVICE_CONFIG_FAILED' "sc.exe failureflag $Svc exited $LASTEXITCODE : $o" 'the service exists but a stop with an error code will not trigger a restart; re-run' }

    # 5. Running is not enough: a binary without a service handler is killed by
    #    the SCM after ~30 s (error 1053), so hold past that window and look again.
    $StartFix = "error 1053 ('did not respond to the start or control request in a timely fashion') means this beacon build has no Windows service handler -- rebuild SimCore's agent-dist from a current tree. Otherwise read $SvcLog and: Get-WinEvent -LogName System -MaxEvents 20 | Where-Object ProviderName -eq 'Service Control Manager'"
    $startErr = ''
    try {
      Start-Service -Name $Svc -ErrorAction Stop
      (Get-Service -Name $Svc).WaitForStatus('Running', '00:01:00')
    } catch { $startErr = "$($_.Exception.Message)" }
    if ($startErr) {
      Cs-Fail 'install' 'SERVICE_START_FAILED' "$Svc did not reach Running: $startErr" $StartFix
    }
    Cs-Say "service $Svc is Running -- holding 35 s past the SCM start window (a 1053 kill shows up here)"
    Start-Sleep -Seconds 35
    $now = Get-Service -Name $Svc -ErrorAction SilentlyContinue
    if (-not $now -or "$($now.Status)" -ne 'Running') {
      Cs-Fail 'install' 'SERVICE_START_FAILED' "$Svc was Running but is '$($now.Status)' 35 s later: it is crashing or being stopped" $StartFix
    }
  } else {
    $InstallDir = "$env:LOCALAPPDATA\CortexSim"
    $Bin = "$InstallDir\cortexsim-agent.exe"
    $LogOut = "$InstallDir\cortexsim-agent.log"
    $LogErr = "$InstallDir\cortexsim-agent.err.log"
    if (-not $env:LOCALAPPDATA) {
      Cs-Fail 'install' 'BIN_INSTALL_FAILED' 'LOCALAPPDATA is not set for this user' 'run from a normal interactive user session, or elevate and install the service instead'
    }
    Cs-StopProcesses $Bin
    $cpErr = ''
    try {
      New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
      Copy-Item -LiteralPath $Src -Destination $Bin -Force -ErrorAction Stop
    } catch { $cpErr = "$($_.Exception.Message)" }
    if ($cpErr -match 'virus|unwanted software') {
      Cs-Fail 'install' 'BINARY_QUARANTINED' "writing $Bin was blocked: $cpErr" 'check Cortex XDR/Defender alerts for cortexsim-agent.exe; add a lab exclusion or allow-list the sha256'
    }
    if ($cpErr) { Cs-Fail 'install' 'BIN_INSTALL_FAILED' "could not write $Bin : $cpErr" 'check free space and permissions on LOCALAPPDATA' }
    $Installed = Cs-AssertPresent $Bin 'install'
    if ($Installed -ne $Actual) {
      Cs-Fail 'install' 'BINARY_QUARANTINED' "the installed copy has sha256 $Installed, not the verified $Actual" 'check Cortex XDR/Defender alerts for cortexsim-agent.exe; add a lab exclusion or allow-list the sha256'
    }
    Cs-Say "beacon        : $Bin"
    $proc = $null
    $spErr = ''
    $spArgs = @{ FilePath = $Bin; ArgumentList = @('--server', $Server, '--id', $AgentId, '--interval', "$IntervalN");
                 RedirectStandardOutput = $LogOut; RedirectStandardError = $LogErr; PassThru = $true; ErrorAction = 'Stop' }
    # -WindowStyle exists only on Windows (pwsh elsewhere rejects the parameter).
    if ("$([Environment]::OSVersion.Platform)" -eq 'Win32NT') { $spArgs.WindowStyle = 'Hidden' }
    try {
      $proc = Start-Process @spArgs
    } catch { $spErr = "$($_.Exception.Message)" }
    if ($spErr -or -not $proc) {
      Cs-Fail 'install' 'PROCESS_START_BLOCKED' "could not start $Bin : $spErr" 'check Cortex XDR/Defender alerts for cortexsim-agent.exe (a blocked launch looks like this), or an AppLocker / WDAC rule on LOCALAPPDATA'
    }
    Start-Sleep -Seconds 3
    if ($proc.HasExited) {
      Cs-Fail 'install' 'PROCESS_START_BLOCKED' "$Bin stopped at once with code $($proc.ExitCode)" "read $LogErr; endpoint protection killing it on launch looks like this too (check Cortex XDR/Defender alerts for cortexsim-agent.exe)"
    }
    Cs-Say "  pid   : $($proc.Id)"
  }

  # -- stage: verify ----------------------------------------------------------------
  # An /api/agents row is NOT proof of a live beacon: enrollment itself stamps
  # last_seen. The proof is last_seen ADVANCING past a baseline sampled after
  # the SCM window -- the beacon polled SimCore again, on its own.
  $Cs.Stage = 'verify'
  $WaitTotal = [Math]::Max(3 * $IntervalN, 6)
  $Step = [Math]::Min([Math]::Max($IntervalN, 1), 5)
  Cs-Say ''
  Cs-Say "verifying '$AgentId' checks in (up to $WaitTotal s)..."
  $Baseline = Cs-LastSeen
  $Checked = $false
  $Waited = 0
  while ($Waited -lt $WaitTotal) {
    Start-Sleep -Seconds $Step
    $Waited += $Step
    $Seen = Cs-LastSeen
    if ($Seen -and ((-not $Baseline) -or ($Seen -gt $Baseline))) { $Checked = $true; break }
  }
  if (-not $Checked) {
    if ($Unsupervised) { $Where = "Get-Process -Id $($proc.Id); Get-Content '$LogErr' -Tail 50" }
    else { $Where = "Get-Service $Svc; Get-Content '$SvcLog' -Tail 50" }
    Cs-Fail 'verify' 'AGENT_NEVER_CHECKED_IN' "'$AgentId' never polled $Server/api/agents again within $WaitTotal s (last_seen never advanced past '$Baseline')" "the beacon is running but cannot reach SimCore, or a stale process on another --id holds the service. It is the BEACON, not this shell, that must reach $Server -- a service runs as SYSTEM without your proxy settings. Check: $Where"
  }

  # -- verdict ---------------------------------------------------------------------
  $Cs.Stage = 'install'
  Cs-Say ''
  if ($Unsupervised) {
    Cs-Report 'install' 'DEGRADED_NO_SUPERVISOR' "per-user process pid $($proc.Id), not a service; checked in after $Waited s"
    Cs-Say "DEGRADED -- '$AgentId' is live (pid $($proc.Id), confirmed after $Waited s) but UNSUPERVISED: no restart after a crash, and it dies at logoff and at reboot"
    Cs-Say "  logs     : $LogOut  (stderr: $LogErr)"
    Cs-Say '  service  : re-run from an elevated Windows PowerShell to install CortexSimAgent'
  } else {
    Cs-Report 'install' 'OK' "service $Svc on windows/$Arch, Running past the SCM window, checked in after $Waited s"
    Cs-Say "done -- '$AgentId' is live and polling SimCore (service $Svc, confirmed after $Waited s)"
    Cs-Say "  status   : Get-Service $Svc"
    Cs-Say "  logs     : $SvcLog"
  }
  Cs-Say "  uninstall: iwr '$Server/api/agents/install?os=windows&uninstall=1' -UseBasicParsing | iex"
} catch {
  $msg = "$($_.Exception.Message)"
  if ($msg -notlike 'CORTEXSIM_FAILED:*') {
    # Nothing anticipated this. Report it under its own message rather than
    # dressing it up as one of the known codes.
    $Cs.Code = 'UNEXPECTED_ERROR'
    $Cs.What = "$msg (script line $($_.InvocationInfo.ScriptLineNumber))"
    $Cs.Fix = 're-run; if it repeats, send this output and GET /api/agents/install/attempts to the CortexSim maintainers'
    Cs-Report $Cs.Stage $Cs.Code $Cs.What
  }
  Write-Host ''
  Write-Host "[cortexsim] FAILED  stage=$($Cs.Stage)  code=$($Cs.Code)" -ForegroundColor Red
  Write-Host "[cortexsim]   what : $($Cs.What)"
  if ($Cs.Fix) { Write-Host "[cortexsim]   fix  : $($Cs.Fix)" }
  if ($PSCommandPath) { exit 1 } else { return }
} finally {
  if ($Cs.Work -and (Test-Path -LiteralPath $Cs.Work)) {
    Remove-Item -LiteralPath $Cs.Work -Recurse -Force -ErrorAction SilentlyContinue
  }
}
}
""".replace("@@COMMON@@", _WINDOWS_COMMON_FUNCTIONS)


_WINDOWS_UNINSTALLER = r"""# --- CortexSim beacon uninstaller (Windows / PowerShell 5.1+) --------------
#
#   From an ELEVATED Windows PowerShell (Run as administrator):
#   iwr '<server>/api/agents/install?os=windows&uninstall=1' -UseBasicParsing | iex
#
# Idempotent: run it twice, the second run finds nothing. What was FOUND is
# tracked separately from what was REMOVED:
#   - nothing found              -> "nothing to remove"
#   - everything found removed   -> "done"
#   - anything found but kept    -> FAILED code=UNINSTALL_INCOMPLETE naming each item
# Same rules as the installer: pure ASCII, the body inside "& { ... }", no bare
# `exit`, every native exit code checked.
& {
Set-StrictMode -Off
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Svc = 'CortexSimAgent'
$Found = New-Object System.Collections.ArrayList
$Removed = New-Object System.Collections.ArrayList
$Left = New-Object System.Collections.ArrayList
$Cs = @{ Code = ''; What = ''; Fix = ''; Hint = '' }

@@COMMON@@
function Cs-Fail([string]$Code, [string]$What, [string]$Fix) {
  $Cs.Code = $Code; $Cs.What = $What; $Cs.Fix = $Fix
  throw "CORTEXSIM_FAILED:$Code"
}
function Cs-Found([string]$Item) { [void]$Found.Add($Item) }
function Cs-Gone([string]$Item) { [void]$Removed.Add($Item); Cs-Say "removed: $Item" }
function Cs-Left([string]$Item, [string]$Why) { [void]$Left.Add("$Item ($Why)"); Cs-Say "NOT removed: $Item ($Why)" }
function Cs-RemovePath([string]$Path, [string]$Label) {
  if (-not $Path) { return }
  if (-not (Test-Path -LiteralPath $Path)) { return }
  Cs-Found "$Label $Path"
  $err = ''
  try { Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop } catch { $err = "$($_.Exception.Message)" }
  if (Test-Path -LiteralPath $Path) {
    if (-not $err) { $err = 'still present after Remove-Item' }
    Cs-Left "$Label $Path" $err
  } else {
    Cs-Gone "$Label $Path"
  }
}
function Cs-DeleteNote {
  if ($Cs.Hint) { Cs-Say "the agent row stays in SimCore until you delete it: $($Cs.Hint)" }
  else { Cs-Say 'the agent row stays in SimCore until you delete it: DELETE /api/agents/<agent_id>' }
}

try {
  if ($env:OS -ne 'Windows_NT') {
    Cs-Fail 'UNSUPPORTED_OS' "this is the Windows uninstaller and this host is not Windows (OS='$env:OS')" "on Linux or macOS run:  curl -fsSL '<server>/api/agents/install?uninstall=1' | bash"
  }
  $IsAdmin = $false
  try {
    $IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch { $IsAdmin = $false }
  $SysDir = "$env:ProgramData\CortexSim"
  $SvcObj = Get-Service -Name $Svc -ErrorAction SilentlyContinue
  if (-not $IsAdmin -and ($SvcObj -or (Test-Path -LiteralPath $SysDir))) {
    Cs-Fail 'NOT_ELEVATED' "the $Svc service or $SysDir is present and this session is not elevated; nothing was removed" 'open Windows PowerShell as Administrator (right-click > Run as administrator) and re-run the one-liner'
  }

  # Read the registration before deleting it, so the agent row can be named.
  if ($SvcObj) {
    try {
      $img = "$((Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\$Svc" -ErrorAction Stop).ImagePath)"
      $srv = ''; $aid = ''
      if ($img -match '--server\s+(\S+)') { $srv = $Matches[1] }
      if ($img -match '--id\s+(\S+)') { $aid = $Matches[1] }
      if ($srv -and $aid) { $Cs.Hint = "Invoke-RestMethod -Method Delete '$srv/api/agents/$aid'" }
    } catch { }
  }

  # 1. The service: 1060 = not installed, 1062 = installed but not running.
  $o = Cs-Exec 'sc.exe' @('stop', $Svc)
  $stopCode = $LASTEXITCODE
  $SvcPresent = [bool]$SvcObj -or ($stopCode -ne 1060)
  if ($SvcPresent) {
    Cs-Found "service $Svc"
    if ($stopCode -ne 0 -and $stopCode -ne 1062 -and $stopCode -ne 1060) { Cs-Say "WARN: sc.exe stop $Svc returned $stopCode : $o" }
    try { (Get-Service -Name $Svc -ErrorAction Stop).WaitForStatus('Stopped', '00:00:30') } catch { }
  }

  # 2. Every beacon process: the service's, a per-user one, or a stale one.
  $procs = @(Get-Process -Name 'cortexsim-agent' -ErrorAction SilentlyContinue)
  if ($procs.Count -gt 0) {
    $ids = ($procs | ForEach-Object { $_.Id }) -join ', '
    Cs-Found "running cortexsim-agent process(es) $ids"
    foreach ($p in $procs) { try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { } }
    try { $procs | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue } catch { }
    $alive = @($procs | Where-Object { $a = $true; try { $a = -not $_.HasExited } catch { $a = $true }; $a })
    if ($alive.Count -gt 0) {
      Cs-Left "cortexsim-agent process(es) $(($alive | ForEach-Object { $_.Id }) -join ', ')" 'still running after Stop-Process -Force'
    } else {
      Cs-Gone "running cortexsim-agent process(es) $ids"
    }
  }

  # 3. The registration. 1072 = already marked for deletion: it lingers until
  #    services.msc / Event Viewer let go of it, or until a reboot.
  if ($SvcPresent) {
    $o = Cs-Exec 'sc.exe' @('delete', $Svc)
    $delCode = $LASTEXITCODE
    if ($delCode -eq 0 -or $delCode -eq 1060) {
      $gone = $false
      for ($i = 0; $i -lt 15; $i++) {
        if (-not (Get-Service -Name $Svc -ErrorAction SilentlyContinue)) { $gone = $true; break }
        Start-Sleep -Seconds 1
      }
      if ($gone) { Cs-Gone "service $Svc" }
      else { Cs-Left "service $Svc" 'marked for deletion; it disappears once services.msc / Event Viewer are closed, or at the next reboot' }
    } elseif ($delCode -eq 1072) {
      Cs-Left "service $Svc" 'already marked for deletion (1072): reboot pending, or close services.msc / Event Viewer'
    } else {
      Cs-Left "service $Svc" "sc.exe delete returned $delCode : $o"
    }
  }

  # 4. Files: the service install (binary + agent.log), a per-user install,
  #    the beacon's staging caches, and any installer scratch left by a kill.
  Cs-RemovePath $SysDir 'install directory'
  if ($env:LOCALAPPDATA) { Cs-RemovePath "$env:LOCALAPPDATA\CortexSim" 'per-user install' }
  Cs-RemovePath (Join-Path ([IO.Path]::GetTempPath()) 'cortexsim-cache') 'staging cache'
  if ($env:SystemRoot) {
    Cs-RemovePath "$env:SystemRoot\Temp\cortexsim-cache" 'staging cache'
    Cs-RemovePath "$env:SystemRoot\SystemTemp\cortexsim-cache" 'staging cache'
  }
  # Where the beacon itself caches and stages (agent/beacon/artifact.go): its
  # cache tries /opt/cortexsim/cache first, which on Windows resolves to
  # <drive>:\opt\cortexsim; stageRootFor is %windir%\Temp\cortexsim; a SYSTEM
  # service's os.UserCacheDir is the systemprofile LocalAppData. The cache holds
  # the staged dual-use tools, so skipping it would call the host clean.
  if ($env:SystemDrive) { Cs-RemovePath "$env:SystemDrive\opt\cortexsim" 'staging cache' }
  if ($env:SystemRoot) {
    Cs-RemovePath "$env:SystemRoot\Temp\cortexsim" 'staging root'
    Cs-RemovePath "$env:SystemRoot\System32\config\systemprofile\AppData\Local\cortexsim" 'staging cache'
  }
  foreach ($v in @($env:CORTEXSIM_CACHE_DIR, $env:CORTEXSIM_STAGE_ROOT)) {
    if ($v -and $v -like '*cortexsim*') { Cs-RemovePath $v 'staging cache' }  # never a tree we did not name
  }
  foreach ($d in @(Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Directory -Filter 'cortexsim-*' -ErrorAction SilentlyContinue)) {
    # Only the installer's own scratch shape (cortexsim-<guid N>), never a
    # directory an operator happened to give the same prefix.
    if ($d.Name -match '^cortexsim-[0-9a-f]{32}$') { Cs-RemovePath $d.FullName 'installer scratch' }
  }

  # -- verdict ---------------------------------------------------------------------
  Cs-Say ''
  if ($Left.Count -gt 0) {
    Cs-Fail 'UNINSTALL_INCOMPLETE' ("found $($Found.Count) item(s), removed $($Removed.Count); still present: " + ($Left -join '; ')) 're-run from an elevated Windows PowerShell; for a service marked for deletion close services.msc / Event Viewer or reboot, then re-run'
  } elseif ($Found.Count -eq 0) {
    Cs-Say 'nothing to remove -- no CortexSim beacon found on this host'
  } else {
    Cs-Say "done -- removed $($Removed.Count) of $($Found.Count) item(s)"
  }
  Cs-DeleteNote
} catch {
  $msg = "$($_.Exception.Message)"
  if ($msg -notlike 'CORTEXSIM_FAILED:*') {
    $Cs.Code = 'UNEXPECTED_ERROR'
    $Cs.What = "$msg (script line $($_.InvocationInfo.ScriptLineNumber))"
    $Cs.Fix = 're-run from an elevated Windows PowerShell; if it repeats, remove the items above by hand'
  }
  Write-Host ''
  Write-Host "[cortexsim] FAILED  stage=uninstall  code=$($Cs.Code)" -ForegroundColor Red
  Write-Host "[cortexsim]   what : $($Cs.What)"
  if ($Cs.Fix) { Write-Host "[cortexsim]   fix  : $($Cs.Fix)" }
  Cs-DeleteNote
  if ($PSCommandPath) { exit 1 } else { return }
}
}
""".replace("@@COMMON@@", _WINDOWS_COMMON_FUNCTIONS)

# Emitted into the Windows installer when this SimCore has no windows beacon to
# serve. It fires BEFORE enrollment so a target that cannot run the beacon never
# creates a phantom agent row -- unless the operator pre-staged one
# (CORTEXSIM_BIN), which is exactly the air-gapped case this must not block.
#
# The executor HAS _windows build-tag variants and the dist matrix includes
# windows/amd64, so this is not a statement about the agent -- it is a statement
# about THIS deployment. A SimCore built without the agent-builder stage, or
# running from a checkout with no agent-dist/, serves nothing, and the DC needs
# to know that is a packaging gap rather than a missing feature.
_WINDOWS_PREFLIGHT_UNAVAILABLE = (
    "if (-not $env:CORTEXSIM_BIN) {\n"
    "    Cs-Fail 'detect' 'WINDOWS_AGENT_UNAVAILABLE' "
    "'this SimCore is not serving a windows/amd64 beacon: the image was built without the "
    "agent-builder stage, or agent-dist/ is missing' "
    "'rebuild with make agent-dist (or docker build -f core/Dockerfile), or use PUSH mode: "
    "generate a bundle from the console and run it on the endpoint. Air-gapped: copy the "
    "beacon over and set $env:CORTEXSIM_BIN'\n"
    "  }"
)


_PLACEHOLDER = re.compile(r"@@[A-Z_]+@@")


def _render(template: str, **subs: str) -> str:
    """Substitute @@NAME@@ placeholders. Deliberately not str.format -- the
    templates are shell/PowerShell and are full of literal braces.

    Strict both ways: a substitution the template does not contain, or a
    placeholder left behind, raises. A silently unrendered ``@@TOKEN@@`` is a
    script that enrols with the literal string "@@TOKEN@@"."""
    out = template
    for key, value in subs.items():
        marker = f"@@{key}@@"
        if marker not in template:
            raise ValueError(f"template has no {marker} placeholder")
        if "@@" in value:
            raise ValueError(f"substitution for {marker} contains '@@'")
        out = out.replace(marker, value)
    left = sorted(set(_PLACEHOLDER.findall(out)))
    if left:
        raise ValueError(f"unrendered placeholders: {left}")
    return out


_LOOPBACK_HINT = (
    " -- NOTE: SERVER is a loopback address, so on THIS host it means this host. "
    "If you copied the one-liner from a console open at localhost, re-fetch it "
    "from the control plane's routable address: "
    "'<control-plane>/api/agents/install?server=http://<control-plane-ip>:8888'"
)
_WINDOWS_LOOPBACK_HINT = (
    " -- NOTE: SERVER is a loopback address, so on THIS host it means this host. "
    "If you copied the one-liner from a console open at localhost, re-fetch it "
    "from the control plane's routable address: "
    "'<control-plane>/api/agents/install?os=windows&server=http://<control-plane-ip>:8888'"
)


def _loopback_hint(server: str) -> str:
    """Remediation suffix, empty unless the baked SERVER is loopback."""
    return _LOOPBACK_HINT if _is_loopback(server) else ""


def _ps_single_quoted(value: str) -> str:
    """Body of a PowerShell single-quoted literal: a quote is written twice."""
    return value.replace("'", "''")


def _posix_installer(server: str, agent_id: str, interval: int,
                     token: Optional[str], mode: str) -> str:
    _check_baked(server, agent_id, interval, token)
    return _render(
        _POSIX_INSTALLER,
        SERVER=server, AGENT_ID=agent_id, INTERVAL=str(interval),
        TOKEN=token or "", MODE=mode,
        LOOPBACK_HINT=_loopback_hint(server),
    )


def _windows_installer(server: str, agent_id: str, interval: int,
                       token: Optional[str]) -> str:
    _check_baked(server, agent_id, interval, token)
    preflight = "" if _binary_path("windows", "amd64").is_file() else _WINDOWS_PREFLIGHT_UNAVAILABLE
    hint = _WINDOWS_LOOPBACK_HINT if _is_loopback(server) else ""
    return _render(
        _WINDOWS_INSTALLER,
        SERVER=server, AGENT_ID=agent_id, INTERVAL=str(interval),
        TOKEN=token or "", PREFLIGHT=preflight,
        LOOPBACK_HINT=_ps_single_quoted(hint),
    )


def _script_response(body: str, *, windows: bool, uninstall: bool) -> PlainTextResponse:
    stem = "uninstall-cortexsim-agent" if uninstall else "install-cortexsim-agent"
    if windows:
        media, fname = "text/plain; charset=utf-8", f"{stem}.ps1"
    else:
        media, fname = "text/x-shellscript; charset=utf-8", f"{stem}.sh"
    return PlainTextResponse(
        body, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/install")
async def agent_installer(
    request: Request,
    os: str = "linux",
    id: str = "jumpbox-01",
    server: Optional[str] = None,
    interval: str = Query(default="10", description="Beacon poll interval, 1-3600 seconds"),
    token: Optional[str] = None,
    mode: Optional[str] = None,
    uninstall: bool = False,
):
    """Generate a ready-to-run beacon installer (or uninstaller) for the chosen OS.

    Linux/macOS -> bash  (`curl -fsSL '<server>/api/agents/install?token=<tok>' | bash`)
    Windows     -> PowerShell 5.1+ (`iwr '<server>/api/agents/install?os=windows&token=<tok>'
                   -UseBasicParsing | iex` from an ELEVATED shell)

    Recommended flow: mint a token (`POST /api/agents/enroll/tokens`) and pass
    `?token=`. The script then ENROLLS to obtain a server-assigned agent id -- the
    DC never invents one. Without a token it falls back to the explicit `?id=`
    (legacy self-asserted path).

    The script needs **no compiler and no public-internet egress**: it downloads
    the prebuilt beacon from this SimCore (`GET /api/agents/binary`) and verifies
    its sha256. `?mode=service` (default) installs a supervised service
    (systemd / launchd / the Windows SCM); `?mode=foreground` keeps the babysat
    behaviour on Linux/macOS and is refused on Windows (400 `BAD_MODE`), which has
    no foreground mode. `?uninstall=1` returns the removal script for the OS:
    bash for Linux/macOS (the default), PowerShell for `os=windows`.

    Every value baked into the script is validated first and refused with a
    structured 400: `BAD_OS`, `BAD_MODE`, `BAD_AGENT_ID`, `BAD_SERVER` (covers the
    Host-header-derived default too), `BAD_TOKEN`, `BAD_INTERVAL`.
    """
    os_norm = (os or "linux").strip().lower()
    if os_norm in {"macos", "mac", "osx"}:
        os_norm = "darwin"
    if os_norm in {"win"}:
        os_norm = "windows"
    if os_norm not in {"linux", "darwin", "windows"}:
        raise _bad_request("BAD_OS", "Unsupported OS",
                           "os must be 'linux', 'darwin' (macos), or 'windows'")
    mode_norm = (mode or "service").strip().lower()
    if mode_norm not in {"service", "foreground"}:
        raise _bad_request("BAD_MODE", "Unsupported install mode",
                           "mode must be 'service' (supervised) or 'foreground'")

    if uninstall:
        if mode_norm == "foreground":
            raise _bad_request(
                "BAD_MODE", "mode does not apply to uninstall",
                "mode selects how to INSTALL; drop it from an ?uninstall=1 request")
        if os_norm == "windows":
            return _script_response(_WINDOWS_UNINSTALLER, windows=True, uninstall=True)
        return _script_response(_POSIX_UNINSTALLER, windows=False, uninstall=True)

    if os_norm == "windows" and mode_norm == "foreground":
        raise _bad_request(
            "BAD_MODE", "Windows has no foreground mode",
            "the Windows installer registers the CortexSimAgent service from an elevated "
            "shell. For a per-user beacon that dies at logoff and at reboot instead, set "
            "$env:CORTEXSIM_ALLOW_UNSUPERVISED='1' before running it.")

    interval_n = _parse_interval(interval)
    problem = _agent_id_problem(id)
    if problem:
        raise _bad_request("BAD_AGENT_ID", "Invalid agent id", f"id {problem}")
    if token:
        problem = _token_problem(token)
        if problem:
            raise _bad_request("BAD_TOKEN", "Invalid enrollment token", problem)
    resolved = _resolve_server(request, server)

    if os_norm == "windows":
        return _script_response(_windows_installer(resolved, id, interval_n, token),
                                windows=True, uninstall=False)
    return _script_response(_posix_installer(resolved, id, interval_n, token, mode_norm),
                            windows=False, uninstall=False)


# Heartbeat staleness thresholds (seconds against utcnow - last_seen).
_ONLINE_WINDOW_S = 30
_STALE_WINDOW_S = 300  # 5 minutes


# Parsed CORTEXSIM_TRUSTED_PROXIES, keyed by the raw string so a changed env
# re-parses and a malformed value is logged once, not on every heartbeat.
_trusted_proxy_cache: dict[str, Optional[tuple]] = {}


def _trusted_proxies() -> Optional[tuple]:
    """The CIDRs whose ``X-Forwarded-For`` is believed, or None if the setting
    is malformed.

    Read from ``os.environ`` on every call (not cached in settings) so an
    operator can fix it without a code change; parsing is memoised per value.
    Empty -- the default -- means no proxy is trusted.
    """
    raw = os.environ.get("CORTEXSIM_TRUSTED_PROXIES", "")
    if raw in _trusted_proxy_cache:
        return _trusted_proxy_cache[raw]
    try:
        parsed: Optional[tuple] = tuple(
            ipaddress.ip_network(part.strip(), strict=False)
            for part in raw.split(",") if part.strip())
    except ValueError as exc:
        logger.error(
            "CORTEXSIM_TRUSTED_PROXIES=%r is not a comma-separated list of CIDRs (%s): "
            "X-Forwarded-For is not evaluated, and a beacon whose request carries it "
            "records NO source IP until this is fixed", raw, exc)
        parsed = None
    _trusted_proxy_cache[raw] = parsed
    return parsed


def _as_ip(value: Optional[str]):
    if not value:
        return None
    try:
        ip = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _client_ip(request: Request) -> Optional[str]:
    """The request source IP, honestly derived -- NEVER invented.

    The socket peer (``request.client.host``) is the observation. A proxy in
    front of SimCore makes the peer the proxy, so ``X-Forwarded-For`` is read --
    but ONLY when that peer is inside a CIDR in ``CORTEXSIM_TRUSTED_PROXIES``
    (comma-separated; empty by default). It used to be believed from anyone, so
    any host that could reach SimCore could stamp an arbitrary address onto an
    agent row, and from there onto the run's ``src_ip``, the field detections
    are stitched on.

    Behind a trusted proxy the hops are walked RIGHT to LEFT, skipping trusted
    proxies: a proxy appends the address it saw, so the left-most entry is
    whatever the client chose to send. A hop that is not an IP address, or a
    malformed trusted-proxy setting, yields None: no observation is better than
    a wrong one (the stitch resolver then keeps a labelled synthetic src_ip).
    A transport with no client address yields None too. See models.Agent.last_ip.
    """
    peer = request.client.host if request.client is not None and request.client.host else None
    xff = request.headers.get("x-forwarded-for")
    if not xff:
        return peer
    nets = _trusted_proxies()
    if nets is None:
        return None

    def trusted(ip) -> bool:
        return ip is not None and any(ip in net for net in nets)

    if not nets or not trusted(_as_ip(peer)):
        return peer
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    for hop in reversed(hops):
        ip = _as_ip(hop)
        if ip is None:
            return None
        if not trusted(ip):
            return str(ip)
    # Every hop is itself a trusted address: the left-most is the origin.
    first = _as_ip(hops[0]) if hops else None
    return str(first) if first is not None else peer


def _derive_status(last_seen: Optional[datetime], now: datetime) -> tuple[str, float]:
    """Derive an agent's liveness status from its last_seen age.

    Returns ``(status, age_seconds)`` where status ∈ online | stale | offline.
    Status is computed at read time — last_seen is the source of truth, the
    stored ``Agent.status`` column is only a cache for the SSE sweep.
    """
    if last_seen is None:
        return "offline", 1e9
    age = (now - last_seen).total_seconds()
    if age < _ONLINE_WINDOW_S:
        return "online", age
    if age < _STALE_WINDOW_S:
        return "stale", age
    return "offline", age


@router.get("")
async def list_agents(db: AsyncSession = Depends(get_db)):
    """Return all registered agents with liveness status derived from
    ``last_seen`` (online < 30s, stale < 5m, offline ≥ 5m)."""
    result = await db.execute(select(Agent).order_by(Agent.last_seen.desc()))
    agents = result.scalars().all()
    now = datetime.utcnow()
    out: list[dict] = []
    for a in agents:
        d = a.to_dict()
        status, age = _derive_status(a.last_seen, now)
        d["status"] = status
        d["last_seen_age_seconds"] = round(age, 1)
        out.append(d)
    logger.info("list_agents count=%d", len(out))
    return {"agents": out, "total": len(out)}


@router.post("/register")
async def register_agent(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a pull-model beacon agent.
    Idempotent — re-registering an existing agent_id updates its metadata.
    """
    result = await db.execute(
        select(Agent).where(Agent.agent_id == body.agent_id)
    )
    existing: Optional[Agent] = result.scalar_one_or_none()
    now = datetime.utcnow()
    # The observed request source — real, or None (never fabricated).
    src_ip = _client_ip(request)

    if existing is None:
        agent = Agent(
            agent_id=body.agent_id,
            hostname=body.hostname,
            os=body.os,
            capabilities=body.capabilities,
            interpreters=body.interpreters,
            registered_at=now,
            last_seen=now,
            status="online",
            last_ip=src_ip,
        )
        db.add(agent)
        logger.info("register_agent NEW agent_id=%s hostname=%s os=%s interpreters=%s",
                     body.agent_id, body.hostname, body.os, body.interpreters)
    else:
        existing.hostname = body.hostname
        existing.os = body.os
        existing.capabilities = body.capabilities
        existing.interpreters = body.interpreters
        existing.last_seen = now
        existing.status = "online"
        # Only overwrite with a real observation — a proxy-stripped None must
        # not clobber a good address captured on a prior call.
        if src_ip is not None:
            existing.last_ip = src_ip
        logger.info("register_agent UPDATED agent_id=%s interpreters=%s", body.agent_id, body.interpreters)

    await db.commit()
    return {
        "status": "registered",
        "agent_id": body.agent_id,
        "message": "Agent registered successfully",
    }


@router.get("/{agent_id}/preflight")
async def agent_runtime_preflight(
    agent_id: str,
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Target-readiness preflight: "can THIS HOST run this scenario?"

    ``POST /api/connectors/{kind}/preflight`` answers "is my TENANT reachable"
    before a POV; nothing answered "can this endpoint run this scenario"
    before this (docs/design/agent-runtime-dependencies.md). Compares the
    scenario's declared per-step ``requires_interpreters`` against this
    agent's advertised interpreter roster.

    ADVISORY ONLY — the agent's roster is a snapshot from its last
    registration. A gap reported here is real evidence a run should not be
    launched yet; a clean report is NOT proof the run will succeed, because
    the beacon re-checks the real target live at execution time regardless
    (that live check, not this endpoint, is what makes "a missing dependency
    never presents as success" true).
    """
    from models import Scenario  # noqa: PLC0415
    from engine.runtime_preflight import evaluate_runtime_readiness  # noqa: PLC0415

    agent_row = (await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )).scalar_one_or_none()
    if agent_row is None:
        raise HTTPException(status_code=404, detail={
            "error": "Agent not found", "code": "AGENT_NOT_FOUND",
            "detail": f"no agent registered with id '{agent_id}'"})

    scenario_row = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )).scalar_one_or_none()
    if scenario_row is None:
        raise HTTPException(status_code=404, detail={
            "error": "Scenario not found", "code": "SCENARIO_NOT_FOUND",
            "detail": f"no scenario with id '{scenario_id}'"})

    readiness = evaluate_runtime_readiness(scenario_row, agent_row.interpreters)
    return {
        "agent_id": agent_id,
        "scenario_id": scenario_id,
        **readiness.to_dict(),
    }


# ---------------------------------------------------------------------------
# Enrollment-token flow — server-assigned identities, one-line onboarding
# ---------------------------------------------------------------------------


def _slugify_name(raw: Optional[str], hostname: str) -> str:
    """Build a safe agent-name stem from a desired name or the hostname."""
    base = (raw or hostname or "agent").strip().lower()
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "agent"
    return base[:40]


@router.post("/enroll/tokens")
async def mint_enrollment_token(
    body: MintTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mint an enrollment token. The full token value is returned EXACTLY once
    here; afterwards only its tail is shown. Hand this to a jumpbox via the
    one-line installer (``/api/agents/install?token=...``)."""
    now = datetime.utcnow()
    token_value = "cxs_" + secrets.token_urlsafe(32)
    row = EnrollmentToken(
        token=token_value,
        label=body.label,
        created_at=now,
        expires_at=now + timedelta(seconds=body.ttl_seconds),
        max_uses=body.max_uses,
        used_count=0,
        revoked=False,
    )
    db.add(row)
    await db.commit()
    logger.info("mint_enrollment_token label=%s max_uses=%d ttl=%ds",
                body.label, body.max_uses, body.ttl_seconds)
    out = row.to_dict(reveal=True)  # reveal once, at mint
    return out


@router.get("/enroll/tokens")
async def list_enrollment_tokens(db: AsyncSession = Depends(get_db)):
    """List enrollment tokens (tails only) with validity derived at read time."""
    rows = (await db.execute(
        select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc())
    )).scalars().all()
    now = datetime.utcnow()
    return {"tokens": [{**t.to_dict(), "valid": t.is_valid(now)} for t in rows]}


@router.delete("/enroll/tokens/{token_id}", status_code=200)
async def revoke_enrollment_token(token_id: int, db: AsyncSession = Depends(get_db)):
    """Revoke an enrollment token so it can no longer be redeemed."""
    row = (await db.execute(
        select(EnrollmentToken).where(EnrollmentToken.id == token_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={
            "error": "Enrollment token not found", "code": "TOKEN_NOT_FOUND",
            "detail": f"id={token_id}"})
    row.revoked = True
    await db.commit()
    return {"status": "revoked", "id": token_id}


@router.post("/enroll")
async def enroll_agent(body: EnrollRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Redeem an enrollment token and register a NEW agent with a
    server-assigned id.

    This is the front door for the rethought deployment UX: the operator never
    invents an agent id (the source of duplicate/typo'd ids in the old
    self-asserted ``/register`` flow) and the token bounds who may onboard.
    Returns the assigned ``agent_id`` and the ``server`` URL to beacon.
    """
    now = datetime.utcnow()
    # Claim one use ATOMICALLY: the validity predicate (EnrollmentToken.is_valid)
    # and the increment are one guarded UPDATE. Checking on a SELECT and writing
    # `used_count + 1` from Python let N concurrent installers all pass the check
    # on a single-use token, enrol N agents, and leave the counter reading 1/1.
    claim = await db.execute(
        update(EnrollmentToken)
        .where(
            EnrollmentToken.token == body.token,
            EnrollmentToken.revoked.is_(False),
            EnrollmentToken.used_count < EnrollmentToken.max_uses,
            or_(EnrollmentToken.expires_at.is_(None), EnrollmentToken.expires_at > now),
        )
        .values(used_count=EnrollmentToken.used_count + 1)
        .execution_options(synchronize_session=False)
    )
    if claim.rowcount == 0:
        await db.rollback()
        # Single opaque error — don't distinguish "wrong" from "expired/used".
        raise HTTPException(status_code=403, detail={
            "error": "Invalid or expired enrollment token", "code": "ENROLL_DENIED",
            "detail": "mint a fresh token via POST /api/agents/enroll/tokens"})
    # Same write transaction as the claim, so this reads exactly our increment.
    token_row = (await db.execute(
        select(EnrollmentToken)
        .where(EnrollmentToken.token == body.token)
        .execution_options(populate_existing=True)
    )).scalar_one()

    # Assign a unique agent id from the name stem + a short random suffix.
    stem = _slugify_name(body.desired_name, body.hostname)
    agent_id = f"{stem}-{secrets.token_hex(3)}"
    # Vanishingly unlikely collision guard.
    while (await db.execute(select(Agent).where(Agent.agent_id == agent_id))).scalar_one_or_none():
        agent_id = f"{stem}-{secrets.token_hex(3)}"

    db.add(Agent(
        agent_id=agent_id, hostname=body.hostname, os=body.os,
        capabilities=body.capabilities or ["shell", "identity-harness"],
        interpreters=body.interpreters,
        registered_at=now, last_seen=now, status="online",
        # The installer runs ON the jumpbox, so the request source IS the target.
        last_ip=_client_ip(request),
    ))
    await db.commit()
    logger.info("enroll_agent assigned agent_id=%s (token tail=...%s, use %d/%d)",
                agent_id, body.token[-6:], token_row.used_count, token_row.max_uses)
    return {
        "status": "enrolled",
        "agent_id": agent_id,
        "remaining_uses": max(0, token_row.max_uses - token_row.used_count),
    }


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Remove a registered agent. Idempotent-friendly: 404 if it never existed.

    Used by the Targets management UI to prune stale/old beacons. Does not touch
    run history — only the agent registry row."""
    result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
    agent: Optional[Agent] = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Agent not found", "code": "AGENT_NOT_FOUND",
                    "detail": f"agent_id='{agent_id}'"},
        )
    await db.delete(agent)
    await db.commit()
    logger.info("delete_agent agent_id=%s", agent_id)
    return {"status": "deleted", "agent_id": agent_id}


@router.get("/{agent_id}/tasks")
async def poll_tasks(
    agent_id: str,
    request: Request,
    wait: int = Query(default=0, ge=0, le=60, description="Long-poll duration in seconds (0 = immediate)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Agent polls for its next pending task.
    Returns the task dict if one is available, or {"task": null} if the queue is empty.
    Also updates agent last_seen timestamp.
    Supports HTTP long-polling via wait parameter.
    """
    # Update last_seen
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent: Optional[Agent] = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Agent not found", "code": "AGENT_NOT_FOUND", "detail": f"agent_id='{agent_id}' — register first via POST /api/agents/register"},
        )

    agent.last_seen = datetime.utcnow()
    agent.status = "online"
    # Refresh the observed source IP on every heartbeat (a real value, or leave
    # the prior one — never overwrite a good address with a proxy-stripped None).
    src_ip = _client_ip(request)
    if src_ip is not None:
        agent.last_ip = src_ip
    await db.commit()

    # DB-aware dequeue so the durable queued_tasks row is removed on delivery
    # (GAP-API-005). A restart will not re-deliver an already-dispatched task.
    task = await orchestrator.dequeue_for_agent(agent_id, db)
    if task is None and wait > 0:
        await orchestrator.wait_for_task(agent_id, timeout=float(wait))
        task = await orchestrator.dequeue_for_agent(agent_id, db)

    if task is None:
        logger.debug("poll_tasks agent=%s no tasks", agent_id)
        return {"task": None}

    # --- The artifact-staging capability gate -------------------------------
    #
    # An old beacon decodes an unknown `artifacts` key with encoding/json, which
    # silently drops it. It would then run every step WITHOUT its tooling, exit 0
    # on all of them, and the absent detections would read in a POV report as
    # gaps in the customer's coverage. So a task that needs staged artifacts is
    # never handed to an agent that cannot stage them: the run is failed LOUDLY
    # here rather than executing and looking green.
    if task.requires_artifact_fetch():
        gate = await _refuse_unstageable_task(agent, task, db)
        if gate is not None:
            raise gate

    logger.info("poll_tasks agent=%s dispatching task_id=%s run_id=%s", agent_id, task.task_id, task.run_id)
    return {"task": task.to_dict()}


async def _refuse_unstageable_task(
    agent: Agent, task, db: AsyncSession
) -> Optional[HTTPException]:
    """Refuse to deliver an artifact-carrying task to a beacon that cannot stage.

    Returns the HTTPException to raise, or ``None`` when the agent is capable.

    The task has already been dequeued (its durable row is gone), so the run
    would otherwise hang in ``running`` forever. It is marked **failed** with the
    same vocabulary the beacon itself uses — a run that could not execute must
    never be mistaken for a run that executed and found nothing.
    """
    from models import Result, Run  # noqa: PLC0415
    from engine.orchestrator import (  # noqa: PLC0415
        ARTIFACT_FETCH_CAPABILITY,
        REASON_AGENT_CANNOT_STAGE_ARTIFACTS,
        _publish_run_status,
        stamp_not_executed,
    )

    caps = list(agent.capabilities or [])
    if ARTIFACT_FETCH_CAPABILITY in caps:
        return None

    names = ", ".join(str(a.get("name", "?")) for a in task.artifacts)
    detail = (
        f"{task.scenario_id} requires {len(task.artifacts)} staged artifact(s) "
        f"({names}) but agent '{agent.agent_id}' advertises [{', '.join(caps) or 'none'}] — "
        "it predates artifact staging and would run every step WITHOUT its tooling, "
        "producing a run that looks green and proves nothing. Re-install the beacon: "
        "curl -fsSL '<server>/api/agents/install?os=linux' | CORTEXSIM_TOKEN='cxs_…' bash"
    )

    from api.runs import _in_flight_clause  # noqa: PLC0415

    # One guarded UPDATE, the marker appended in SQL. Reading the run and
    # writing it back let the fan-out's other endpoints lose their /output and
    # /complete summary, and let a racing abort be overwritten with `failed`.
    # `_in_flight_clause`, not a status list: a multi-endpoint run whose first
    # endpoint already failed reads `failed` while still owed this endpoint, and
    # skipping it left the run waiting forever on a task that was just refused.
    res = await db.execute(
        update(Run)
        .where(Run.run_id == task.run_id, _in_flight_clause())
        .values(
            status="failed",
            completed_at=datetime.utcnow(),
            output=func.coalesce(Run.output, "") + (
                "\n--- RUN FAILED: AGENT_CANNOT_STAGE_ARTIFACTS ---\n"
                f"ARTIFACT STAGING FAILED: {detail}\n"
                "NO STEPS RAN. This run proves NOTHING about the customer's detection coverage.\n"
            ),
        )
        .execution_options(synchronize_session=False)
    )
    # Never executed is never matched (OPS-01). The dequeue above already
    # stamped delivered_at, so without this the refused steps stayed pending
    # and the next alert from the host was credited to them, with an MTTD.
    # Scoped to THIS task's steps and independent of the UPDATE's rowcount:
    # another endpoint's partition (or a composed run's EAL steps) may really
    # have run, and a second refused endpoint finds the run already failed.
    refused = {s.get("id") for s in task.steps if isinstance(s, dict)}
    seeded = set((await db.execute(
        select(Result.step_id).where(Result.run_id == task.run_id))).scalars().all())
    await stamp_not_executed(db, task.run_id, REASON_AGENT_CANNOT_STAGE_ARTIFACTS,
                             commit=False, exclude_steps=seeded - refused)
    await db.commit()
    # Only the request that failed the run announces it; a run that was
    # already terminal (or is missing) keeps the verdict it has.
    if res.rowcount == 1:
        await _publish_run_status(task.run_id, "failed")

    logger.warning(
        "poll_tasks REFUSED agent=%s run_id=%s scenario=%s reason=AGENT_CANNOT_STAGE_ARTIFACTS",
        agent.agent_id, task.run_id, task.scenario_id,
    )
    return HTTPException(
        status_code=409,
        detail={
            "error": "Agent cannot stage artifacts",
            "code": "AGENT_CANNOT_STAGE_ARTIFACTS",
            "detail": detail,
        },
    )


# ---------------------------------------------------------------------------
# Phase 2 — background heartbeat sweep (SSE emitter)
# ---------------------------------------------------------------------------
#
# list_agents already derives online/stale/offline at read time, so the sweep
# is belt-and-suspenders: its only job is to write status transitions to the
# DB cache and emit `agent.status` events on the GLOBAL bus so the UI sees an
# agent go offline without re-listing. Started from main.py's lifespan.


async def sweep_agents_once() -> int:
    """Single pass of the staleness sweep. Recomputes each agent's derived
    status; on a transition, persists it and publishes an ``agent.status``
    event. Returns the number of agents whose status changed.

    Opens its own DB session (it runs outside any request scope).
    """
    from database import AsyncSessionLocal  # noqa: PLC0415
    from events import event_bus  # noqa: PLC0415

    changed = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Agent))
        agents = result.scalars().all()
        now = datetime.utcnow()
        for a in agents:
            new_status, _ = _derive_status(a.last_seen, now)
            if new_status != a.status:
                a.status = new_status
                changed += 1
                try:
                    await event_bus.publish(
                        None,
                        {"type": "agent.status", "run_id": None,
                         "data": {"agent_id": a.agent_id, "status": new_status}},
                    )
                except Exception:  # pragma: no cover - defensive
                    logger.exception("event_bus publish failed agent=%s", a.agent_id)
        if changed:
            await db.commit()
        # Snapshot BEFORE any terminalisation: a rollback for one agent expires
        # every loaded ORM object, so iterating them afterwards would raise
        # MissingGreenlet on the next agent.
        #
        # Gate on the configured beacon-lost threshold, NOT on the `offline`
        # display label (a fixed 5 min): the label is a derivation for the UI;
        # terminalise_lost_runs re-checks the same threshold per run.
        from config import settings  # noqa: PLC0415
        from engine.beacon_lost import terminalise_lost_runs  # noqa: PLC0415

        threshold = settings.CORTEXSIM_BEACON_LOST_AFTER_SECONDS
        offline_ids = [a.agent_id for a in agents
                       if a.last_seen is None
                       or (now - a.last_seen).total_seconds() >= threshold]

        for agent_id in offline_ids:
            try:
                await terminalise_lost_runs(
                    db, agent_id, now,
                    after_seconds=settings.CORTEXSIM_BEACON_LOST_AFTER_SECONDS)
            except Exception:
                await db.rollback()
                logger.exception("beacon_lost terminalisation failed agent=%s", agent_id)
    return changed


async def heartbeat_sweep_loop(interval_seconds: int = 30) -> None:
    """Run :func:`sweep_agents_once` forever on a fixed cadence.

    Cancelled on app shutdown. Transient errors are logged and do not kill
    the loop. This is the SSE emitter for agent liveness transitions.
    """
    import asyncio  # noqa: PLC0415

    logger.info("heartbeat sweep loop started interval=%ds", interval_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                changed = await sweep_agents_once()
                if changed:
                    logger.info("heartbeat sweep: %d agent status transition(s)", changed)
            except Exception:  # pragma: no cover - defensive
                logger.exception("heartbeat sweep pass failed — continuing")
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("heartbeat sweep loop cancelled")
        raise

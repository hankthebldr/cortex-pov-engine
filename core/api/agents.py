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
import logging
import os  # NOTE: shadowed by the `os` query parameter inside agent_installer().
import re
import secrets
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
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


class MintTokenRequest(BaseModel):
    label: Optional[str] = Field(default=None, max_length=80)
    ttl_seconds: int = Field(default=3600, ge=60, le=2_592_000)  # 1 min … 30 days
    max_uses: int = Field(default=1, ge=1, le=1000)


class EnrollRequest(BaseModel):
    token: str
    hostname: str
    os: str
    capabilities: list[str] = []
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


def _resolve_server(request: Request, override: Optional[str]) -> str:
    """Server URL the agent beacons back to. Prefer an explicit override
    (the DC knows the jumpbox's reachable address); else derive from the
    request so `curl <host>/api/agents/install | bash` just works.

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
        return override.rstrip("/")
    return str(request.base_url).rstrip("/")


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
       "supported: Linux, macOS. For Windows targets use PUSH mode (the console's Push bundle)." ;;
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
  printf '%s\n[Install]\nWantedBy=default.target\n' "$UNIT_BODY" > "$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SVC" >/dev/null 2>&1 || return 1
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

if [ "$SUPERVISED" = "1" ]; then cs_report install OK "supervised on $OS_ID/$ARCH_ID"; fi
cs_say ""
cs_say "done — '$AGENT_ID' should appear in the SimCore console within ${INTERVAL}s"
cs_say "uninstall: curl -fsSL '$SERVER/api/agents/install?uninstall=1' | bash"
"""


_POSIX_UNINSTALLER = r"""#!/usr/bin/env bash
# ─── CortexSim beacon uninstaller (Linux / macOS) ───────────────────────────
#
#   curl -fsSL '<server>/api/agents/install?uninstall=1' | bash
#
# A DC leaving a customer site needs one line that takes the beacon back off the
# endpoint. Best-effort by design (no `set -e`): every removal is attempted even
# if an earlier one was never installed.
set -uo pipefail

SVC="cortexsim-agent"
PLIST_LABEL="com.paloaltonetworks.cortexsim.agent"
REMOVED=0
cs_say() { echo "[cortexsim] $*"; }
cs_gone() { cs_say "removed: $1"; REMOVED=$((REMOVED+1)); }

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-enabled "$SVC" >/dev/null 2>&1 || [ -f "/etc/systemd/system/$SVC.service" ]; then
    systemctl disable --now "$SVC" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$SVC.service" && cs_gone "system unit /etc/systemd/system/$SVC.service"
    systemctl daemon-reload >/dev/null 2>&1 || true
  fi
  if [ -f "$HOME/.config/systemd/user/$SVC.service" ]; then
    systemctl --user disable --now "$SVC" >/dev/null 2>&1 || true
    rm -f "$HOME/.config/systemd/user/$SVC.service" && cs_gone "user unit $HOME/.config/systemd/user/$SVC.service"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
  fi
fi

if command -v launchctl >/dev/null 2>&1; then
  for p in "/Library/LaunchDaemons/$PLIST_LABEL.plist" "$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"; do
    if [ -f "$p" ]; then
      launchctl bootout "system/$PLIST_LABEL" >/dev/null 2>&1 || true
      launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1 || true
      launchctl unload -w "$p" >/dev/null 2>&1 || true
      rm -f "$p" && cs_gone "launchd job $p"
    fi
  done
fi

# Anything still running from the nohup fallback.
if command -v pkill >/dev/null 2>&1; then
  if pkill -f 'cortexsim-agent --server' >/dev/null 2>&1; then cs_gone "running beacon process(es)"; fi
fi

for b in /usr/local/bin/cortexsim-agent "$HOME/.local/bin/cortexsim-agent"; do
  if [ -e "$b" ]; then rm -f "$b" && cs_gone "binary $b"; fi
done

if [ "$REMOVED" = "0" ]; then
  cs_say "nothing to remove — no CortexSim beacon found on this host"
else
  cs_say "done — $REMOVED item(s) removed"
fi
cs_say "the agent row stays in SimCore until you delete it: DELETE /api/agents/<agent_id>"
"""


_WINDOWS_INSTALLER = r"""# ─── CortexSim beacon installer (Windows / PowerShell) ──────────────────────
#
#   iwr '<server>/api/agents/install?os=windows&token=<tok>' -UseBasicParsing | iex
#
# Same contract as the POSIX installer: no compiler on the endpoint, the beacon
# comes prebuilt and checksummed from SimCore, and it is registered as a Windows
# service so it survives logoff and reboot. Failures carry a stable code and are
# reported back to SimCore.
$ErrorActionPreference = 'Stop'
$Server   = if ($env:CORTEXSIM_SERVER)   { $env:CORTEXSIM_SERVER }   else { '@@SERVER@@' }
$AgentId  = if ($env:CORTEXSIM_AGENT_ID) { $env:CORTEXSIM_AGENT_ID } else { '@@AGENT_ID@@' }
$Interval = if ($env:CORTEXSIM_INTERVAL) { $env:CORTEXSIM_INTERVAL } else { '@@INTERVAL@@' }
$Token    = if ($env:CORTEXSIM_TOKEN)    { $env:CORTEXSIM_TOKEN }    else { '@@TOKEN@@' }
$Server   = $Server.TrimEnd('/')
$Arch     = if ([Environment]::Is64BitOperatingSystem) { 'amd64' } else { 'unsupported' }

function Cs-Report($Stage, $Code, $Detail) {
  if ($env:CORTEXSIM_TELEMETRY -eq '0') { return }
  try {
    $b = @{ stage = $Stage; code = $Code; detail = "$Detail"; hostname = $env:COMPUTERNAME;
            os = 'windows'; arch = $Arch; agent_id = $AgentId } | ConvertTo-Json -Compress
    Invoke-RestMethod -Method Post -Uri "$Server/api/agents/install/telemetry" `
      -ContentType 'application/json' -Body $b -TimeoutSec 5 | Out-Null
  } catch { }
}

function Cs-Fail($Stage, $Code, $What, $Fix) {
  Write-Host ""
  Write-Host "[cortexsim] FAILED  stage=$Stage  code=$Code"
  Write-Host "[cortexsim]   what : $What"
  if ($Fix) { Write-Host "[cortexsim]   fix  : $Fix" }
  Cs-Report $Stage $Code $What
  exit 1
}

Write-Host "[cortexsim] target server : $Server"
@@PREFLIGHT@@

if ($Arch -eq 'unsupported') {
  Cs-Fail 'detect' 'UNSUPPORTED_ARCH' '32-bit Windows is not supported' 'use a 64-bit host'
}

if ($Token) {
  Write-Host "[cortexsim] enrolling with token ...$($Token.Substring([Math]::Max(0,$Token.Length-6)))"
  $payload = @{ token = $Token; hostname = $env:COMPUTERNAME; os = 'windows';
                capabilities = @('shell') } | ConvertTo-Json -Compress
  try {
    $resp = Invoke-RestMethod -Method Post -Uri "$Server/api/agents/enroll" `
      -ContentType 'application/json' -Body $payload -TimeoutSec 30
  } catch {
    Cs-Fail 'enroll' 'ENROLL_DENIED' "$($_.Exception.Message)" `
      "mint a fresh token: POST $Server/api/agents/enroll/tokens"
  }
  $AgentId = $resp.agent_id
  if (-not $AgentId) { Cs-Fail 'enroll' 'NO_AGENT_ID' 'enroll returned no agent_id' 'SimCore/installer version mismatch' }
  Write-Host "[cortexsim] enrolled as   : $AgentId"
}

$Work = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP ("cortexsim-" + [guid]::NewGuid()))
$Dl   = Join-Path $Work.FullName 'cortexsim-agent.exe'
try {
  Invoke-WebRequest -Uri "$Server/api/agents/binary?os=windows&arch=$Arch" -OutFile $Dl -UseBasicParsing -TimeoutSec 300
} catch {
  Cs-Fail 'download' 'BINARY_FETCH_FAILED' "$($_.Exception.Message)" `
    "on the SimCore host build the windows beacon into agent-dist/, then re-run"
}
$Expect = (Invoke-RestMethod -Uri "$Server/api/agents/binary/sha256?os=windows&arch=$Arch" -TimeoutSec 30).Trim()
$Actual = (Get-FileHash -Algorithm SHA256 -Path $Dl).Hash.ToLower()
if ($Expect -ne $Actual) {
  Cs-Fail 'verify' 'CHECKSUM_MISMATCH' "expected $Expect, got $Actual" `
    'do NOT run this binary — re-fetch and look for an intercepting TLS proxy'
}
Write-Host "[cortexsim] checksum OK   : $($Actual.Substring(0,16))..."

$InstallDir = Join-Path $env:ProgramData 'CortexSim'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$Bin = Join-Path $InstallDir 'cortexsim-agent.exe'
Copy-Item -Force $Dl $Bin

$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$Args = "--server $Server --id $AgentId --interval $Interval"
if ($IsAdmin) {
  sc.exe stop CortexSimAgent   | Out-Null
  sc.exe delete CortexSimAgent | Out-Null
  sc.exe create CortexSimAgent binPath= "`"$Bin`" $Args" start= auto DisplayName= "CortexSim beacon agent" | Out-Null
  sc.exe failure CortexSimAgent reset= 0 actions= restart/10000 | Out-Null
  sc.exe start CortexSimAgent | Out-Null
  Write-Host "[cortexsim] installed service: CortexSimAgent (auto-start, restart on failure)"
  Write-Host "[cortexsim]   status: sc.exe query CortexSimAgent"
} else {
  Cs-Report 'install' 'DEGRADED_NO_SUPERVISOR' 'not elevated — started as a detached process'
  Start-Process -FilePath $Bin -ArgumentList $Args -WindowStyle Hidden
  Write-Host "[cortexsim] NOT elevated — started detached; it will NOT survive a reboot."
  Write-Host "[cortexsim]   re-run this from an elevated shell to install the service."
}
Cs-Report 'install' 'OK' "windows/$Arch"
Write-Host "[cortexsim] done — '$AgentId' should appear in the console within $Interval s"
"""

# Emitted into the Windows installer when this SimCore has no windows beacon to
# serve. It fires BEFORE enrollment so a target that cannot run the beacon never
# creates a phantom agent row.
#
# The executor now HAS _windows build-tag variants and the dist matrix includes
# windows/amd64, so this is no longer a statement about the agent — it is a
# statement about THIS deployment. A SimCore built without the agent-builder
# stage, or running from a checkout with no agent-dist/, still serves nothing,
# and the DC needs to know that is a packaging gap rather than a missing feature.
_WINDOWS_PREFLIGHT_UNAVAILABLE = (
    "Cs-Fail 'detect' 'WINDOWS_AGENT_UNAVAILABLE' `\n"
    "  'this SimCore is not serving a windows/amd64 beacon — the image was built "
    "without the agent-builder stage, or agent-dist/ is missing' `\n"
    "  'rebuild with make agent-dist (or docker build -f core/Dockerfile), or use PUSH "
    "mode: generate a bundle from the console and run it on the endpoint'"
)


def _render(template: str, **subs: str) -> str:
    """Substitute @@NAME@@ placeholders. Deliberately not str.format — the
    templates are shell/PowerShell and are full of literal braces."""
    out = template
    for key, value in subs.items():
        out = out.replace(f"@@{key}@@", value)
    return out


_LOOPBACK_HINT = (
    " -- NOTE: SERVER is a loopback address, so on THIS host it means this host. "
    "If you copied the one-liner from a console open at localhost, re-fetch it "
    "from the control plane's routable address: "
    "'<control-plane>/api/agents/install?server=http://<control-plane-ip>:8888'"
)


def _loopback_hint(server: str) -> str:
    """Remediation suffix, empty unless the baked SERVER is loopback."""
    return _LOOPBACK_HINT if _is_loopback(server) else ""


def _posix_installer(server: str, agent_id: str, interval: int,
                     token: Optional[str], mode: str) -> str:
    return _render(
        _POSIX_INSTALLER,
        SERVER=server, AGENT_ID=agent_id, INTERVAL=str(interval),
        TOKEN=token or "", MODE=mode,
        LOOPBACK_HINT=_loopback_hint(server),
    )


def _windows_installer(server: str, agent_id: str, interval: int,
                       token: Optional[str]) -> str:
    preflight = "" if _binary_path("windows", "amd64").is_file() else _WINDOWS_PREFLIGHT_UNAVAILABLE
    return _render(
        _WINDOWS_INSTALLER,
        SERVER=server, AGENT_ID=agent_id, INTERVAL=str(interval),
        TOKEN=token or "", PREFLIGHT=preflight,
    )


@router.get("/install")
async def agent_installer(
    request: Request,
    os: str = "linux",
    id: str = "jumpbox-01",
    server: Optional[str] = None,
    interval: int = 10,
    token: Optional[str] = None,
    mode: str = "service",
    uninstall: bool = False,
):
    """Generate a ready-to-run beacon installer for the chosen OS.

    Linux/macOS → bash  (`curl -fsSL '<server>/api/agents/install?token=<tok>' | bash`)
    Windows     → PowerShell (.ps1)

    Recommended flow: mint a token (`POST /api/agents/enroll/tokens`) and pass
    `?token=`. The script then ENROLLS to obtain a server-assigned agent id — the
    DC never invents one. Without a token it falls back to the explicit `?id=`
    (legacy self-asserted path).

    The script needs **no compiler and no public-internet egress**: it downloads
    the prebuilt beacon from this SimCore (`GET /api/agents/binary`) and verifies
    its sha256. `?mode=service` (default) installs a supervised systemd/launchd
    service that survives the SSH session and a reboot; `?mode=foreground` keeps
    the old babysat behaviour for a throwaway container. `?uninstall=1` returns
    the removal script instead.
    """
    os_norm = (os or "linux").strip().lower()
    if os_norm in {"macos", "mac", "osx"}:
        os_norm = "darwin"
    if os_norm not in {"linux", "darwin", "windows"}:
        raise HTTPException(
            status_code=400,
            detail={"error": "Unsupported OS", "code": "BAD_OS",
                    "detail": "os must be 'linux', 'darwin' (macos), or 'windows'"},
        )
    mode_norm = (mode or "service").strip().lower()
    if mode_norm not in {"service", "foreground"}:
        raise HTTPException(
            status_code=400,
            detail={"error": "Unsupported install mode", "code": "BAD_MODE",
                    "detail": "mode must be 'service' (supervised) or 'foreground'"},
        )

    if uninstall:
        # One removal script covers Linux and macOS; the Windows path is the
        # documented sc.exe delete, which needs no generated wrapper.
        return PlainTextResponse(
            _POSIX_UNINSTALLER, media_type="text/x-shellscript; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="uninstall-cortexsim-agent.sh"'},
        )

    resolved = _resolve_server(request, server)
    if os_norm == "windows":
        body = _windows_installer(resolved, id, interval, token)
        media, fname = "text/plain; charset=utf-8", "install-cortexsim-agent.ps1"
    else:
        body = _posix_installer(resolved, id, interval, token, mode_norm)
        media, fname = "text/x-shellscript; charset=utf-8", "install-cortexsim-agent.sh"
    return PlainTextResponse(
        body, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# Heartbeat staleness thresholds (seconds against utcnow - last_seen).
_ONLINE_WINDOW_S = 30
_STALE_WINDOW_S = 300  # 5 minutes


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

    if existing is None:
        agent = Agent(
            agent_id=body.agent_id,
            hostname=body.hostname,
            os=body.os,
            capabilities=body.capabilities,
            registered_at=now,
            last_seen=now,
            status="online",
        )
        db.add(agent)
        logger.info("register_agent NEW agent_id=%s hostname=%s os=%s", body.agent_id, body.hostname, body.os)
    else:
        existing.hostname = body.hostname
        existing.os = body.os
        existing.capabilities = body.capabilities
        existing.last_seen = now
        existing.status = "online"
        logger.info("register_agent UPDATED agent_id=%s", body.agent_id)

    await db.commit()
    return {
        "status": "registered",
        "agent_id": body.agent_id,
        "message": "Agent registered successfully",
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
async def enroll_agent(body: EnrollRequest, db: AsyncSession = Depends(get_db)):
    """Redeem an enrollment token and register a NEW agent with a
    server-assigned id.

    This is the front door for the rethought deployment UX: the operator never
    invents an agent id (the source of duplicate/typo'd ids in the old
    self-asserted ``/register`` flow) and the token bounds who may onboard.
    Returns the assigned ``agent_id`` and the ``server`` URL to beacon.
    """
    now = datetime.utcnow()
    token_row = (await db.execute(
        select(EnrollmentToken).where(EnrollmentToken.token == body.token)
    )).scalar_one_or_none()
    if token_row is None or not token_row.is_valid(now):
        # Single opaque error — don't distinguish "wrong" from "expired/used".
        raise HTTPException(status_code=403, detail={
            "error": "Invalid or expired enrollment token", "code": "ENROLL_DENIED",
            "detail": "mint a fresh token via POST /api/agents/enroll/tokens"})

    # Assign a unique agent id from the name stem + a short random suffix.
    stem = _slugify_name(body.desired_name, body.hostname)
    agent_id = f"{stem}-{secrets.token_hex(3)}"
    # Vanishingly unlikely collision guard.
    while (await db.execute(select(Agent).where(Agent.agent_id == agent_id))).scalar_one_or_none():
        agent_id = f"{stem}-{secrets.token_hex(3)}"

    db.add(Agent(
        agent_id=agent_id, hostname=body.hostname, os=body.os,
        capabilities=body.capabilities or ["shell", "identity-harness"],
        registered_at=now, last_seen=now, status="online",
    ))
    token_row.used_count += 1
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
    db: AsyncSession = Depends(get_db),
):
    """
    Agent polls for its next pending task.
    Returns the task dict if one is available, or {"task": null} if the queue is empty.
    Also updates agent last_seen timestamp.
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
    await db.commit()

    # DB-aware dequeue so the durable queued_tasks row is removed on delivery
    # (GAP-API-005). A restart will not re-deliver an already-dispatched task.
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
    from models import Run  # noqa: PLC0415
    from engine.orchestrator import (  # noqa: PLC0415
        ARTIFACT_FETCH_CAPABILITY,
        _publish_run_status,
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

    run_row = await db.execute(select(Run).where(Run.run_id == task.run_id))
    run: Optional[Run] = run_row.scalar_one_or_none()
    if run is not None and run.status not in ("complete", "failed", "aborted"):
        run.status = "failed"
        run.completed_at = datetime.utcnow()
        run.output = (run.output or "") + (
            "\n--- RUN FAILED: AGENT_CANNOT_STAGE_ARTIFACTS ---\n"
            f"ARTIFACT STAGING FAILED: {detail}\n"
            "NO STEPS RAN. This run proves NOTHING about the customer's detection coverage.\n"
        )
        await db.commit()
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

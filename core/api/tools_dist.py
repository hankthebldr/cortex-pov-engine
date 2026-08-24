"""
CortexSim API — /api/tools/binar* router (the Rust tool shelf).

Endpoints:
  GET  /api/tools/binaries              — prebuilt Rust-tool inventory + manifest
  GET  /api/tools/binary/{tool}         — download a prebuilt static tool
  GET  /api/tools/binary/{tool}/sha256  — its digest, for the consumer's verify step

WHY this exists: signalbench, ackbarx and xdrtop are tier-2 SUBMODULE tools, and
the only documented way to get one onto a jumpbox was ``cargo build --release``
ON THAT HOST — a Rust toolchain plus crates.io egress on a customer endpoint,
which is exactly what a default-deny enterprise network blocks. The
``rust-builder`` stage in core/Dockerfile bakes static musl binaries into
/app/rust-dist; this router hands them out.

This is a THIRD MOUNT OF ONE IDIOM, not a third idiom. Compare:

    /api/agents/binaries          /api/shelf/payloads          /api/tools/binaries
    /api/agents/binary            /api/shelf/payload/{name}    /api/tools/binary/{tool}
    /api/agents/binary/sha256     /api/shelf/payload/{name}/sha256
                                                               /api/tools/binary/{tool}/sha256

Plural collection, singular artifact; FileResponse; an ``X-CortexSim-*-SHA256``
header; ``Cache-Control: no-store``; and a refusal that names the directory AND
the command that fills it. The integrity story is identical to the agent
shelf's and deliberately so: the digest is computed HERE, from the bytes on
disk, at request time — never read out of SHA256SUMS — so a stale sums file can
never make a tampered binary verify. The consumer CARRIES that digest and
verifies before execution; a mismatch is a hard fail with no retry.

These tools are NOT on the payload shelf, and that is a decision rather than an
oversight. The shelf's ``install.artifact`` schema describes a *download*: it
requires an upstream ``url`` (TA-03 rejects non-http(s)), a ``pin``, and a
``sha256`` that is knowable BEFORE the fetch. A locally-built binary has no
upstream URL and its digest is a function of the toolchain, not of a pin — it is
only expressible as ``pin.type: none`` + a waiver, which payload-shelf.md calls
a dev-only state that is never valid in an engagement image. TA-01 additionally
hard-rejects ``install.artifact`` on tier != 4. Upstream releases cannot rescue
it either: xdrtop publishes only archives (TA-08 rejects ``kind: archive``
outright) and ackbarx's gitlink is untagged, so no pin could honestly reference
the source we ship.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

logger = logging.getLogger("cortexsim.api.tools_dist")

router = APIRouter(prefix="/tools", tags=["tools"])


# The allowlist is a LITERAL set, never a path join from user input: {tool}
# lands in a filename, and "../../etc/shadow" must fail at the door rather than
# at the filesystem. It mirrors the three crates core/Dockerfile's rust-builder
# stage compiles; a fourth tool means editing both, on purpose.
_KNOWN_TOOLS = frozenset({"ackbarx", "signalbench", "xdrtop"})

# Only linux/amd64 is built. See MANIFEST.json's unsupported_targets[] for the
# per-target reason, which this module quotes verbatim in its refusals — a DC
# asking "where is the arm64 build?" gets the sentence, not silence.
_BUILT_OS = "linux"
_BUILT_ARCH = "amd64"

_OS_ALIASES = {
    "linux": "linux",
    "darwin": "darwin", "macos": "darwin", "osx": "darwin", "mac": "darwin",
    "windows": "windows", "win": "windows",
}
_ARCH_ALIASES = {
    "amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
    "arm64": "arm64", "aarch64": "arm64",
}

_sha_cache: dict[tuple[str, int, int], str] = {}


def _rust_dist_dir() -> Path:
    """Directory holding the prebuilt static Rust tools.

    ``CORTEXSIM_RUST_DIST`` wins so a DC can point at an scp'd drop without
    rebuilding the image; otherwise it is ``<BASE_DIR>/rust-dist``, which is
    where core/Dockerfile's rust-builder stage lands.
    """
    override = os.environ.get("CORTEXSIM_RUST_DIST")
    if override:
        return Path(override)
    from config import settings  # noqa: PLC0415 — avoid an import cycle at module load

    return Path(settings.CORTEXSIM_BASE_DIR) / "rust-dist"


def _binary_name(tool: str) -> str:
    return f"cortexsim-tool-{tool}-{_BUILT_OS}-{_BUILT_ARCH}"


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


def _manifest() -> dict:
    """Read MANIFEST.json, or {} if absent/corrupt.

    A missing manifest is a valid degraded state (someone scp'd bare binaries
    into CORTEXSIM_RUST_DIST); it costs provenance, not service.
    """
    path = _rust_dist_dir() / "MANIFEST.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        logger.warning("rust-dist MANIFEST.json unreadable (%s) — serving without provenance", exc)
        return {}


def _gitlink_commit(tool: str) -> str | None:
    """Current checked-out commit of sources/<tool>, or None if unreadable.

    Deliberately PURE FILE READS, no `git` subprocess: the runtime image is
    python:3.11-slim and does NOT ship git, so a subprocess implementation would
    return None on every deployment and the staleness check would be permanently
    dead while looking implemented. A submodule's ``.git`` is a file containing
    ``gitdir: <path>``; that directory's HEAD is either a detached sha (the
    normal submodule state) or a ref to resolve.

    Returns None inside a stock image anyway — sources/ is not copied in — and
    that must read as "unknown", never as "current". A binary built from a
    different commit than the checkout is the silent-substitution failure the
    shelf's pinning section exists to prevent.
    """
    from config import settings  # noqa: PLC0415

    src = Path(settings.CORTEXSIM_BASE_DIR) / "sources" / tool
    dot_git = src / ".git"
    try:
        if dot_git.is_dir():
            gitdir = dot_git
        elif dot_git.is_file():
            pointer = dot_git.read_text().strip()
            if not pointer.startswith("gitdir:"):
                return None
            gitdir = (src / pointer.split(":", 1)[1].strip()).resolve()
        else:
            return None
        head = (gitdir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head if len(head) == 40 else None
        ref = head.split(":", 1)[1].strip()
        ref_file = gitdir / ref
        if ref_file.is_file():
            sha = ref_file.read_text().strip()
            return sha if len(sha) == 40 else None
        packed = gitdir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
        return None
    except (OSError, ValueError):
        return None


def _staleness(tool: str, built_commit: str | None) -> tuple[bool | None, str | None]:
    """(stale, detail). ``None`` means "cannot be determined" — deliberately not
    ``False``, because an unprovable claim of currency is the thing to avoid."""
    if not built_commit:
        return None, (
            "provenance unrecorded — this image was built without "
            "--build-arg <TOOL>_COMMIT=..., so the commit these bytes came from "
            "is unknown. Rebuild with `make build` to record it."
        )
    live = _gitlink_commit(tool)
    if live is None:
        return None, (
            f"built from {built_commit[:7]}; the sources/{tool} gitlink is not "
            "readable from this deployment (sources/ is deliberately not copied "
            "into the image), so currency cannot be re-checked here."
        )
    if live == built_commit:
        return False, None
    return True, (
        f"built from {built_commit[:7]}, checkout is now {live[:7]} — "
        "rebuild the image so the served tool matches the source tree."
    )


def _available_tools() -> list[dict]:
    """Inventory the dist directory. An empty list is a valid state — the shelf
    is simply not baked, and every caller reports that rather than 500ing."""
    dist = _rust_dist_dir()
    if not dist.is_dir():
        return []
    manifest = _manifest()
    by_tool = {t.get("tool"): t for t in manifest.get("tools", []) if isinstance(t, dict)}
    out: list[dict] = []
    for tool in sorted(_KNOWN_TOOLS):
        path = dist / _binary_name(tool)
        if not path.is_file():
            continue
        st = path.stat()
        entry = by_tool.get(tool, {})
        built_commit = entry.get("submodule_commit")
        stale, stale_detail = _staleness(tool, built_commit)
        out.append({
            "tool": tool,
            "os": _BUILT_OS,
            "arch": _BUILT_ARCH,
            "filename": path.name,
            "size_bytes": st.st_size,
            # Recomputed from the bytes on disk, NOT read from SHA256SUMS.
            "sha256": _sha256_of(path),
            "version": entry.get("version"),
            "static": entry.get("static"),
            "submodule_commit": built_commit,
            "stale": stale,
            "stale_detail": stale_detail,
            "modified_at": datetime.utcfromtimestamp(st.st_mtime).isoformat(),
        })
    return out


def _unsupported_reason(goos: str, goarch: str) -> str | None:
    """The manifest's verbatim reason for a target we did not build, if any."""
    target = f"{goos}/{goarch}"
    for entry in _manifest().get("unsupported_targets", []):
        if not isinstance(entry, dict):
            continue
        declared = [t.strip() for t in str(entry.get("target", "")).split(",")]
        if target in declared:
            return entry.get("reason")
    return None


def _validate_tool(tool: str) -> str:
    norm = (tool or "").strip().lower()
    if norm not in _KNOWN_TOOLS:
        raise HTTPException(status_code=400, detail={
            "error": "Unknown tool",
            "code": "BAD_TOOL",
            "detail": f"tool must be one of {sorted(_KNOWN_TOOLS)}",
        })
    return norm


def _validate_target(os_raw: str, arch_raw: str) -> tuple[str, str]:
    goos = _OS_ALIASES.get((os_raw or "").strip().lower())
    if goos is None:
        raise HTTPException(status_code=400, detail={
            "error": "Unsupported OS", "code": "BAD_OS",
            "detail": f"os must be one of {sorted(set(_OS_ALIASES.values()))}"})
    goarch = _ARCH_ALIASES.get((arch_raw or "").strip().lower())
    if goarch is None:
        raise HTTPException(status_code=400, detail={
            "error": "Unsupported architecture", "code": "BAD_ARCH",
            "detail": f"arch must be one of {sorted(set(_ARCH_ALIASES.values()))} "
                      f"(uname -m spellings x86_64/aarch64 are accepted)"})
    return goos, goarch


def _resolve_or_refuse(tool: str, goos: str, goarch: str) -> Path:
    """Two DIFFERENT refusals, on purpose.

    409 TOOL_TARGET_UNSUPPORTED — this tool is never built for that platform,
    and no amount of rebuilding will change it. It carries the manifest's own
    sentence saying why, so a DC on a Graviton jumpbox reads a reason instead of
    diagnosing a broken deployment. (409 mirrors the push generator's existing
    BUNDLE_TARGET_UNSATISFIABLE for the same class of problem: the request is
    well-formed and the content cannot satisfy it.)

    404 TOOL_BINARY_UNAVAILABLE — the target IS built, but this deployment's
    shelf is empty. That one HAS a fix, and the message names it.
    """
    if (goos, goarch) != (_BUILT_OS, _BUILT_ARCH):
        reason = _unsupported_reason(goos, goarch) or (
            "only linux/amd64 is built; see MANIFEST.json unsupported_targets[]"
        )
        raise HTTPException(status_code=409, detail={
            "error": "Tool not built for that platform",
            "code": "TOOL_TARGET_UNSUPPORTED",
            "detail": (
                f"{tool} is built for {_BUILT_OS}/{_BUILT_ARCH} only, not "
                f"{goos}/{goarch}. Reason: {reason} "
                f"Run {tool} on a {_BUILT_OS}/{_BUILT_ARCH} host, or build it "
                f"from sources/{tool} on the target (needs a Rust toolchain and "
                f"crates.io egress on that host)."
            ),
        })
    path = _rust_dist_dir() / _binary_name(tool)
    if not path.is_file():
        have = [t["tool"] for t in _available_tools()]
        raise HTTPException(status_code=404, detail={
            "error": "No prebuilt binary for that tool",
            "code": "TOOL_BINARY_UNAVAILABLE",
            "detail": (
                f"{_binary_name(tool)} is not in {_rust_dist_dir()}. "
                f"Available: {have or 'none'}. Rebuild the image (core/Dockerfile "
                f"rust-builder stage) so /app/rust-dist is populated, or scp the "
                f"binaries in and set CORTEXSIM_RUST_DIST."
            ),
        })
    return path


@router.get("/binaries")
async def list_tool_binaries():
    """Inventory of prebuilt Rust tools this SimCore can hand to a target."""
    manifest = _manifest()
    tools = _available_tools()
    return {
        "binaries": tools,
        "total": len(tools),
        "dist_dir": str(_rust_dist_dir()),
        "builder_image": manifest.get("builder_image"),
        "target": manifest.get("target"),
        # DERIVED, not trusted: two producers fill this shelf — the Dockerfile's
        # rust-builder stage and scripts/build-rust-dist.sh — and only one emits
        # a `provenance` key. Reading the key would report "unrecorded" for a
        # manifest that plainly carries commits, which is the opposite of the
        # honesty this field exists for.
        "provenance": (
            "recorded"
            if any(t.get("submodule_commit") for t in manifest.get("tools", []))
            else "unrecorded"
        ),
        "unsupported_targets": manifest.get("unsupported_targets", []),
    }


@router.get("/binary/{tool}")
async def download_tool_binary(tool: str, os: str = "linux", arch: str = "amd64"):
    """Serve the prebuilt static tool.

    The digest is echoed in ``X-CortexSim-Tool-SHA256`` so a client that streams
    the body can verify without a second round trip — the same contract the
    beacon shelf uses, verified before execution, hard-fail on mismatch.
    """
    tool = _validate_tool(tool)
    goos, goarch = _validate_target(os, arch)
    path = _resolve_or_refuse(tool, goos, goarch)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={
            "X-CortexSim-Tool-SHA256": _sha256_of(path),
            "X-CortexSim-Tool-Name": tool,
            # A proxy caching this across a rebuild serves stale bytes that then
            # fail the consumer's digest check, and the resulting message blames
            # "an intercepting proxy" — a wrong diagnosis our own headers made.
            "Cache-Control": "no-store",
        },
    )


@router.get("/binary/{tool}/sha256", response_class=PlainTextResponse)
async def tool_binary_sha256(tool: str, os: str = "linux", arch: str = "amd64"):
    """Bare hex digest — what a consumer compares against. Plain text (not JSON)
    so `curl … | read` needs no parser on a minimal target host."""
    tool = _validate_tool(tool)
    goos, goarch = _validate_target(os, arch)
    path = _resolve_or_refuse(tool, goos, goarch)
    return PlainTextResponse(_sha256_of(path) + "\n")

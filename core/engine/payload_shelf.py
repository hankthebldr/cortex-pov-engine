"""
The payload shelf — derivation, resolution and composition.

ONE SENTENCE
------------
A tier-4 adapter pack declares one staged artifact (``install.artifact``);
``payloads/sources.json`` is GENERATED from those declarations; this module
walks ``scenario → adapter_ref → pack → artifact → shelf`` and **refuses at
compose time** when anything is missing or the bytes on the shelf disagree with
the pack's pin; and the destination path — never the shelf key — is overridable
so a scenario can prove behavioural detection instead of a filename match.

WHY IT IS ONE MODULE
--------------------
Three consumers resolve tools: the Go beacon (pull mode), the K8s init
container (served delivery) and the console. Two implementations of an
integrity check drift, and the one that drifts is the one nobody ran that
quarter. So there is exactly one ``compose()``; ``k8s_manifest._resolve_payloads``
is expected to delegate to it (that file is owned by another track — see
``docs/reference/payload-shelf.md`` §7, "Delegation still owed").

THE INTEGRITY MODEL — do not fork it
------------------------------------
The digest is recomputed FROM THE BYTES ON THE SHELF, on the DC's own SimCore,
at compose time, and baked into whatever the consumer carries (the K8s
``integrity.env`` ConfigMap today, a task payload for the beacon). The consumer
therefore verifies against a value it carried IN, never one it fetched from the
same server it is trusting. ``GET .../payload/{name}/sha256`` exists for humans
and ``curl``; a consumer using it verifies nothing, because a substituted server
serves a matching pair.

Two independent digest disagreements are checked, and they mean different things:

* **PAYLOAD_NOT_STAGED** — the artifact is not on this shelf at all. Refused at
  compose time so a consumer never discovers it. A step whose tool never arrived
  runs anyway, produces no detection, and the absent detection reads in a POV
  report as "Cortex missed it" — a manufactured false negative on the customer's
  stack, in a document a DC shows a customer.
* **PAYLOAD_PIN_MISMATCH** — the artifact IS on the shelf but its bytes differ
  from the pin in the adapter pack. This closes the hole the serving endpoints
  cannot: they recompute from bytes, so a file hand-dropped into
  ``CORTEXSIM_PAYLOAD_DIST`` verifies perfectly against itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tools.adapter_loader import (
    _ALLOWED_STAGE_ROOTS,
    _ARTIFACT_FILENAME_RE,
    ToolAdapterSchema,
    _validate_stage_path,
)

logger = logging.getLogger("cortexsim.engine.payload_shelf")

#: Re-exported so the three surfaces that gate a destination path (the pack via
#: TA-09, a scenario's `stage_as` via the loader track's S-19, a compose-request
#: override via PAYLOAD_DEST_REFUSED) all read one tuple.
ALLOWED_STAGE_ROOTS = _ALLOWED_STAGE_ROOTS

#: Where a literal `cluster_posture.payloads` name lands in a pod, unchanged
#: from the K8s harness's own convention.
K8S_DEFAULT_STAGE_DIR = "/cortexsim/tools"

#: Consumers that may carry a composition. `bundle` is deliberately absent —
#: see `_bad_consumer`.
VALID_CONSUMERS: tuple[str, ...] = ("beacon", "k8s", "console")

# Where the shelf is mounted. One constant, because the beacon projection and
# the composed absolute URL disagreeing would put a target host on a 404.
SHELF_PATH_PREFIX = "/api/shelf"
BEACON_PATH_PREFIX = SHELF_PATH_PREFIX


# ---------------------------------------------------------------------------
# Errors — one shape, structured, actionable
# ---------------------------------------------------------------------------


class PayloadResolutionError(Exception):
    """A composition could not be resolved. Always carries a machine code."""

    http_status = 409

    def __init__(self, code: str, error: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.code = code
        self.error = error
        self.detail = detail
        self.extra = extra

    def to_error(self) -> dict[str, Any]:
        return {"error": self.error, "code": self.code, "detail": self.detail, **self.extra}


class PayloadNotStaged(PayloadResolutionError):
    """Declared, but not on this shelf.

    Subclass rather than a distinct type so ``except PayloadResolutionError``
    catches the whole family and a caller that only cares about staging can
    still be specific. ``engine.k8s_manifest`` defines a same-named exception of
    its own today; when that file delegates here, the two collapse.
    """


class PayloadPinMismatch(PayloadResolutionError):
    """On the shelf, but not the bytes the adapter pack was authored against."""


class PayloadDestRefused(PayloadResolutionError):
    """A requested destination is outside the staging allowlist."""

    http_status = 400


class BadConsumer(PayloadResolutionError):
    http_status = 400


def _bad_consumer(consumer: str) -> BadConsumer:
    return BadConsumer(
        "BAD_CONSUMER",
        "Unknown composition consumer",
        (
            f"consumer={consumer!r} is not one of {list(VALID_CONSUMERS)}. "
            f"'bundle' is deliberately NOT a valid consumer: a generated "
            f"bash/PowerShell bundle must execute on a clean host with no "
            f"SimCore dependency at runtime, and "
            f"tests/engine/test_push_generator_invariant.py freezes that "
            f"byte-for-byte. A bundle that needs a tool must EMBED its bytes or "
            f"keep installing from the internet — it must never fetch from the "
            f"shelf."
        ),
        valid_consumers=list(VALID_CONSUMERS),
    )


# ---------------------------------------------------------------------------
# Derivation — the staging list comes FROM the packs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeclaredArtifact:
    """One ``install.artifact`` block, projected out of its adapter pack."""

    name: str
    url: str
    kind: str
    sha256: Optional[str]
    license: Optional[str]
    adapter_id: str
    pin_type: str
    pin_ref: Optional[str]
    stage_path: str
    mode: str
    used_by: str = ""

    @property
    def pinned(self) -> bool:
        return bool(self.sha256) and self.pin_type != "none"


def _catalog():
    from tools.adapter_catalog import catalog  # noqa: PLC0415 — avoids an import cycle

    return catalog


def declared_artifacts(
    adapters: Optional[Iterable[ToolAdapterSchema]] = None,
    *,
    scenarios: Optional[Iterable[dict[str, Any]]] = None,
) -> list[DeclaredArtifact]:
    """Every artifact the adapter packs declare, sorted by shelf filename.

    THIS is the staging list. ``payloads/sources.json`` is a projection of it,
    not a second hand-maintained copy — a hand-copied list is a failure this
    repo has already paid for twice, most recently in the very file this
    replaces: ``sources.json`` declares ``adapter_id: TOOL-LINPEAS`` for a pack
    that has never existed.
    """
    if adapters is None:
        adapters = _catalog().all()

    used_by: dict[str, list[str]] = {}
    for scenario in scenarios or []:
        sid = str(scenario.get("scenario_id") or "")
        for entry in scenario.get("external_tools") or []:
            if not isinstance(entry, dict):
                continue
            ref = entry.get("adapter_ref")
            if ref and sid:
                used_by.setdefault(str(ref), []).append(sid)

    out: list[DeclaredArtifact] = []
    for adapter in adapters:
        art = adapter.install.artifact
        if art is None:
            continue
        scenario_ids = sorted(set(used_by.get(adapter.adapter_id, [])))
        out.append(DeclaredArtifact(
            name=art.filename,
            url=art.url,
            kind=art.kind,
            sha256=art.sha256,
            license=art.license or adapter.upstream.license,
            adapter_id=adapter.adapter_id,
            pin_type=art.pin.type,
            pin_ref=art.pin.ref,
            stage_path=art.stage_path or "",
            mode=art.mode,
            used_by=", ".join(scenario_ids) if scenario_ids
            else "(no scenario references this adapter yet)",
        ))
    return sorted(out, key=lambda a: a.name)


# ---------------------------------------------------------------------------
# payloads/sources.json — generated, with an explicit migration bucket
# ---------------------------------------------------------------------------

_SOURCES_HEADER = {
    "by": "core/engine/payload_shelf.py  (python3 -m engine.payload_shelf --write)",
    "from": "tools/packs/*.yml  ->  install.artifact",
    "do_not_edit": (
        "payloads[] is GENERATED. Edit the adapter pack's install.artifact block, "
        "then run `python3 -m engine.payload_shelf --write`. CI fails on drift."
    ),
    "unbound": (
        "unbound[] is the ONLY hand-authored list here, and it is a MIGRATION "
        "BUCKET that must reach zero. An entry belongs here only while an "
        "artifact is stageable but no adapter pack declares it yet. It is staged "
        "like any other artifact, but nothing can bind a scenario to it, so no "
        "composition can resolve it and no consumer can be handed it. Each entry "
        "names the pack file that would close it in `pack_todo`."
    ),
    "pinning": (
        "sha256 is a PIN. Unpinned, the builder records whatever it downloaded "
        "and every consumer then verifies against THAT and passes — the DC "
        "silently ships a different tool than the one they tested. Pinned to a "
        "moving URL, the build fails loudly the next time upstream ships. "
        "Pinning converts a silent substitution into a loud build failure."
    ),
    "policy": "docs/reference/payload-shelf.md",
}

_SOURCES_KEY_ORDER = (
    "name", "url", "sha256", "kind", "license", "adapter_id",
    "pin_type", "pin_ref", "used_by",
)


def _entry_dict(a: DeclaredArtifact) -> dict[str, Any]:
    return {
        "name": a.name,
        "url": a.url,
        "sha256": a.sha256,
        "kind": a.kind,
        "license": a.license,
        "adapter_id": a.adapter_id,
        "pin_type": a.pin_type,
        "pin_ref": a.pin_ref,
        "used_by": a.used_by,
    }


def read_unbound(path: str) -> list[dict[str, Any]]:
    """The migration bucket from an existing sources.json, preserved verbatim."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    raw = doc.get("unbound")
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict) and e.get("name")]


def render_sources_json(
    artifacts: list[DeclaredArtifact],
    unbound: list[dict[str, Any]],
) -> str:
    """Byte-deterministic rendering. This IS the drift gate's contract.

    No timestamp, no host, no version string — the same rule
    ``generate_bootstrap`` holds, and for the same reason: a generated artifact
    that is committed must be provably re-derivable, or it is just a second
    hand-maintained list wearing a hat.
    """
    doc = {
        "$generated": dict(_SOURCES_HEADER),
        "payloads": [_entry_dict(a) for a in sorted(artifacts, key=lambda x: x.name)],
        "unbound": sorted(unbound, key=lambda e: str(e.get("name") or "")),
    }
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def check_sources_drift(path: str, *, artifacts: Optional[list[DeclaredArtifact]] = None) -> Optional[str]:
    """None when the committed file matches derivation, else an actionable diff."""
    import difflib  # noqa: PLC0415 — only needed on the failure path

    if artifacts is None:
        artifacts = declared_artifacts()
    want = render_sources_json(artifacts, read_unbound(path))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            have = fh.read()
    except OSError as exc:
        return f"cannot read {path}: {exc}"
    if have == want:
        return None
    diff = "".join(difflib.unified_diff(
        have.splitlines(keepends=True), want.splitlines(keepends=True),
        fromfile="committed", tofile="regenerated",
    ))
    return (
        f"FAIL {path} is stale — payloads[] no longer matches tools/packs/*.yml.\n"
        f"     Someone edited the generated section, or added/changed an\n"
        f"     install.artifact without regenerating. Run:\n"
        f"       python3 -m engine.payload_shelf --write\n"
        f"{diff}"
    )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRequest:
    """What a caller needs, BEFORE the shelf is consulted."""

    name: str
    dest_path: str
    mode: str = "0755"
    kind: str = "file"
    adapter_id: str = ""
    origin: str = "adapter_ref"   # adapter_ref | cluster_posture.payloads | request
    source_url: Optional[str] = None
    license: Optional[str] = None
    pin_sha256: Optional[str] = None


@dataclass(frozen=True)
class ComposedArtifact:
    name: str
    sha256: str
    size_bytes: int
    url: str
    dest_path: str
    dest_basename: str
    mode: str
    kind: str
    renamed: bool
    adapter_id: str
    origin: str
    source_url: Optional[str]
    license: Optional[str]
    pinned: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "url": self.url,
            "dest_path": self.dest_path,
            "dest_basename": self.dest_basename,
            "mode": self.mode,
            "kind": self.kind,
            "renamed": self.renamed,
            "adapter_id": self.adapter_id or None,
            "origin": self.origin,
            "source_url": self.source_url,
            "license": self.license,
            "pinned": self.pinned,
        }


@dataclass(frozen=True)
class UnstagedAdapter:
    adapter_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"adapter_id": self.adapter_id, "reason": self.reason}


@dataclass(frozen=True)
class Composition:
    composition_id: str
    scenario_id: Optional[str]
    consumer: str
    server_url: str
    artifacts: tuple[ComposedArtifact, ...]
    unstaged_adapters: tuple[UnstagedAdapter, ...]
    warnings: tuple[dict[str, Any], ...] = field(default=())

    @property
    def air_gapped(self) -> bool:
        """True when NOTHING in this plan needs egress from the TARGET host.

        False does not mean "broken": it means some declared tool still installs
        itself from the public internet at dispatch, which a default-deny
        customer network blocks. Surfacing it is the whole point — a shelf that
        silently covers two of a scenario's five tools is the "green while
        proving nothing" shape.
        """
        return not self.unstaged_adapters

    @property
    def renamed_count(self) -> int:
        return sum(1 for a in self.artifacts if a.renamed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "composition_id": self.composition_id,
            "scenario_id": self.scenario_id,
            "consumer": self.consumer,
            "server_url": self.server_url,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "unstaged_adapters": [u.to_dict() for u in self.unstaged_adapters],
            "air_gapped": self.air_gapped,
            "renamed_count": self.renamed_count,
            "warnings": list(self.warnings),
        }

    def to_beacon_artifacts(self) -> list[dict[str, Any]]:
        """Project onto the Go beacon's wire contract.

        THE ONE projection between this module and ``agent/beacon/artifact.go``.
        The two halves were built by different tracks and disagreed on field
        names — compose emits ``url``/``dest_path``, the beacon's
        ``Artifact`` struct unmarshals ``path``/``dest`` — so feeding
        ``to_dict()`` straight into a task made the beacon refuse every artifact
        with ``ARTIFACT_SPEC_INVALID``. It failed loudly, which is the design
        working, but only one caller joins the two halves and until it existed
        nothing could catch the mismatch.

        ``path`` is deliberately SERVER-RELATIVE: the beacon joins it onto the
        ServerURL it was enrolled against, so a task cannot redirect a target
        host at a server the DC never registered.

        Guarded by ``tests/engine/test_orchestrator_artifacts.py``, which
        asserts these keys equal the Go struct's json tags parsed from source.
        """
        return [
            {
                "name": a.name,
                "sha256": a.sha256,
                "size_bytes": a.size_bytes,
                "path": f"{BEACON_PATH_PREFIX}/payload/{a.name}",
                "dest": a.dest_path,
                "mode": a.mode,
            }
            for a in self.artifacts
        ]


def _shelf_index() -> dict[str, dict[str, Any]]:
    from api.payloads import inventory  # noqa: PLC0415 — avoids an import cycle

    return {p["name"]: p for p in inventory()}


def _shelf_dir() -> str:
    from api.payloads import payload_dist_dir  # noqa: PLC0415

    return str(payload_dist_dir())


# A shelf holding deterministic STUBS rather than the real upstream bytes. The
# test corpus needs one — 170 scenarios must compose without downloading 8 real
# offensive tools — but a stub can never satisfy a pack's sha256 pin, so the pin
# check has to know it is looking at a double.
#
# Three guards, because a pin bypass that could reach a customer's endpoint is
# worse than no pin at all:
#   1. the marker file must exist ON the shelf,
#   2. CORTEXSIM_PAYLOAD_DIST must be set explicitly (so neither the repo's
#      payloads/ nor the image's baked-in shelf can ever be marked), and
#   3. CORTEXSIM_ENV must not be production.
# Every composition off a stub shelf also carries a STUB_SHELF warning, so a
# plan built against doubles is never mistaken for a verified one.
STUB_SHELF_MARKER = ".cortexsim-stub-shelf"


def _is_stub_shelf() -> bool:
    import os  # noqa: PLC0415

    if not os.getenv("CORTEXSIM_PAYLOAD_DIST"):
        return False
    if os.getenv("CORTEXSIM_ENV", "").strip().lower() == "production":
        return False
    return os.path.isfile(os.path.join(_shelf_dir(), STUB_SHELF_MARKER))


def _check_dest(value: str, *, name: str) -> str:
    try:
        return _validate_stage_path(
            value, code="PAYLOAD_DEST_REFUSED", field=f"dest_path for {name!r}"
        )
    except ValueError as exc:
        raise PayloadDestRefused(
            "PAYLOAD_DEST_REFUSED",
            "Destination path refused",
            (
                f"{exc} A destination is a WRITE on a host the DC does not own, "
                f"and the beacon frequently runs as root — an unchecked path "
                f"turns the task queue into an arbitrary-file-write primitive."
            ),
            dest_path=value,
            allowed_roots=list(ALLOWED_STAGE_ROOTS),
        ) from exc


def required_artifacts(
    scenario: Optional[dict[str, Any]] = None,
    *,
    adapter_refs: Optional[list[str]] = None,
    overrides: Optional[dict[str, str]] = None,
    consumer: str = "beacon",
) -> tuple[list[ArtifactRequest], list[UnstagedAdapter]]:
    """Walk a scenario to the artifacts it needs. Shelf is NOT consulted here.

    Resolution order, de-duplicated on the shelf filename:

    1. ``external_tools[].adapter_ref`` on the scenario, plus any explicit
       ``adapter_refs``. A pack with an ``install.artifact`` yields a request;
       a pack without one yields an ``UnstagedAdapter`` (a WARN, not an error —
       ~30 of the 50 tier-4 packs are ``apt``/``pip`` and are not shelf-able at
       all; making that gap LEGIBLE is the deliverable, closing it for apt would
       mean hosting a Debian mirror).
    2. Literal ``cluster_posture.payloads[]`` names (K8s, back-compat). An
       adapter-derived entry wins on collision — it carries kind, mode and a
       destination.

    Destination precedence (§5 of the contract):
        install.artifact.stage_path   ← external_tools[].stage_as   ← overrides
    """
    if consumer not in VALID_CONSUMERS:
        raise _bad_consumer(consumer)

    scenario = scenario or {}
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    catalog = _catalog()

    stage_as: dict[str, str] = {}
    refs: list[str] = []
    for entry in scenario.get("external_tools") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("adapter_ref")
        if not ref:
            continue
        refs.append(str(ref))
        # The scenario-level rename: committed, reviewable, re-runnable. A
        # control that lives in a curl flag is not a control, it is a demo.
        if entry.get("stage_as"):
            stage_as[str(ref)] = str(entry["stage_as"])
    for ref in adapter_refs or []:
        refs.append(str(ref))

    requests: dict[str, ArtifactRequest] = {}
    unstaged: dict[str, UnstagedAdapter] = {}

    for ref in dict.fromkeys(refs):     # preserve order, drop dupes
        adapter = catalog.find(ref)
        if adapter is None:
            # NOT a silent skip. An unloaded catalog is the exact failure this
            # repo has already shipped once — the prod image did not COPY the
            # UC/TC snapshot, so strict validation degraded to a no-op that no
            # test could see — and CLAUDE.md warns `tools/` must be in the image
            # "or the catalog loads empty". Dropping the ref quietly would
            # produce a perfectly green plan that stages nothing: the console
            # would report "air-gapped, 0 tools need egress" while the beacon
            # ran the TTP with no tooling. Surfacing it as unstaged keeps the
            # call non-fatal (a fixture with no catalog still composes) while
            # making air_gapped false and the gap legible.
            logger.warning(
                "payload shelf: adapter_ref %s is not in the catalog", ref
            )
            unstaged[ref] = UnstagedAdapter(
                adapter_id=ref,
                reason=(
                    "ADAPTER_NOT_IN_CATALOG — no pack with this id is loaded, so "
                    "the shelf cannot know what artifact it needs. Either the "
                    "scenario references a nonexistent adapter, or tools/ is "
                    "missing from this deployment (core/Dockerfile must COPY it) "
                    "and the catalog loaded empty. Until this resolves, the "
                    "TARGET host supplies this tool or the step runs without it."
                ),
            )
            continue
        art = adapter.install.artifact
        if art is None:
            unstaged[ref] = UnstagedAdapter(
                adapter_id=ref,
                reason=(
                    "pack declares no install.artifact, so it falls back to "
                    "install.runtime_install_command — the TARGET host fetches "
                    "this tool from the public internet at dispatch, which a "
                    "default-deny egress policy blocks."
                ),
            )
            continue

        dest = overrides.get(ref) or stage_as.get(ref) or art.stage_path or ""
        if not dest:
            # TA-10 guarantees stage_path is populated for every loaded pack, so
            # this is defence in depth against a hand-built schema object.
            dest = f"/tmp/{art.filename}"
        dest = _check_dest(dest, name=art.filename)

        requests[art.filename] = ArtifactRequest(
            name=art.filename,
            dest_path=dest,
            mode=art.mode,
            kind=art.kind,
            adapter_id=adapter.adapter_id,
            origin="adapter_ref",
            source_url=art.url,
            license=art.license or adapter.upstream.license,
            pin_sha256=art.sha256,
        )

    posture = scenario.get("cluster_posture") or {}
    literals = posture.get("payloads") if isinstance(posture, dict) else None
    # A literal name carries no adapter, so it used to carry no pin either — the
    # K8s path (the one delivery vehicle proven on a live cluster, whose manifest
    # ships into a customer's GitOps repo) recomputed the digest from whatever
    # bytes were on the shelf and baked THAT into the manifest. A substituted
    # linpeas.sh then verified perfectly against itself in the pod: exactly the
    # theatre PAYLOAD_PIN_MISMATCH exists to close. Both names SIM-CDR-001
    # declares are pack-declared with real pins, so inherit by filename.
    pins_by_filename: dict[str, tuple[str, str]] = {}
    for adapter in catalog.all():
        art = adapter.install.artifact
        if art is not None and art.sha256:
            pins_by_filename[art.filename] = (art.sha256, adapter.adapter_id)
    for raw in literals or []:
        name = str(raw)
        if name in requests:
            continue        # adapter-derived wins: it carries kind, mode, dest
        if not _ARTIFACT_FILENAME_RE.match(name):
            raise PayloadDestRefused(
                "PAYLOAD_DEST_REFUSED",
                "Unservable payload name",
                f"cluster_posture.payloads entry {name!r} is not a servable "
                f"shelf filename ({_ARTIFACT_FILENAME_RE.pattern}).",
                name=name,
            )
        dest = overrides.get(name) or f"{K8S_DEFAULT_STAGE_DIR}/{name}"
        # The K8s harness owns /cortexsim; it is outside the general staging
        # allowlist by design, so it is accepted only on this back-compat path.
        if not dest.startswith(K8S_DEFAULT_STAGE_DIR + "/"):
            dest = _check_dest(dest, name=name)
        pin, pin_owner = pins_by_filename.get(name, ("", ""))
        requests[name] = ArtifactRequest(
            name=name, dest_path=dest, mode="0755", kind="file",
            adapter_id=pin_owner, origin="cluster_posture.payloads",
            pin_sha256=pin or None,
        )

    ordered = sorted(requests.values(), key=lambda r: r.name)
    return ordered, sorted(unstaged.values(), key=lambda u: u.adapter_id)


def _composition_id(scenario_id: Optional[str], consumer: str,
                    artifacts: list[ComposedArtifact]) -> str:
    """Deterministic: the same plan always yields the same id.

    Excludes ``server_url`` and warnings so a POV report can cite the id and two
    DCs on two SimCores can compare plans. Same discipline as
    ``generate_bootstrap``'s determinism contract.
    """
    canonical = json.dumps(
        {
            "scenario_id": scenario_id,
            "consumer": consumer,
            "artifacts": [
                {"name": a.name, "sha256": a.sha256,
                 "dest_path": a.dest_path, "mode": a.mode}
                for a in artifacts
            ],
        },
        sort_keys=True, separators=(",", ":"),
    )
    return "cmp_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compose(
    *,
    scenario: Optional[dict[str, Any]] = None,
    adapter_refs: Optional[list[str]] = None,
    overrides: Optional[dict[str, str]] = None,
    consumer: str = "beacon",
    server_url: str = "",
    path_prefix: str = "/api/shelf",
) -> Composition:
    """Resolve a digest-bound plan, or REFUSE.

    Every artifact in a successful composition carries a NON-EMPTY sha256, or
    the whole call raises. There is no partial plan: a blank digest makes a
    consumer's ``[ "$ACT" = "$WANT" ]`` compare against ``""`` and pass on
    anything, which is how an integrity check silently becomes a no-op.
    """
    if consumer not in VALID_CONSUMERS:
        raise _bad_consumer(consumer)

    requests, unstaged = required_artifacts(
        scenario, adapter_refs=adapter_refs, overrides=overrides, consumer=consumer
    )
    shelf = _shelf_index()
    scenario_id = str(scenario.get("scenario_id")) if scenario and scenario.get("scenario_id") else None

    missing = [r for r in requests if r.name not in shelf]
    if missing:
        raise PayloadNotStaged(
            "PAYLOAD_NOT_STAGED",
            "Declared tool artifact not staged",
            (
                f"{scenario_id or 'this composition'} needs "
                f"{[m.name for m in missing]} but the shelf at {_shelf_dir()} does "
                f"not carry them. Staged: {sorted(shelf) or 'none'}. Refusing to "
                f"compose a payload that would run this TTP WITHOUT its tool — "
                f"every step would look like it executed and the absent detection "
                f"would read in the POV report as 'Cortex missed it'. Fix: run "
                f"./scripts/build-payloads.sh on this SimCore host and rebuild the "
                f"image (core/Dockerfile COPYs payloads/), or scp the artifact in "
                f"and set CORTEXSIM_PAYLOAD_DIST."
            ),
            missing=[{
                "adapter_id": m.adapter_id or None,
                "filename": m.name,
                "source_url": m.source_url,
                "expected_sha256": m.pin_sha256,
            } for m in missing],
            staged=sorted(shelf),
            shelf_dir=_shelf_dir(),
        )

    stub_shelf = _is_stub_shelf()
    composed: list[ComposedArtifact] = []
    for req in requests:
        entry = shelf[req.name]
        actual = str(entry.get("sha256") or "")
        if not actual:
            raise PayloadNotStaged(
                "PAYLOAD_NOT_STAGED",
                "Staged artifact has no digest",
                f"'{req.name}' is on the shelf at {_shelf_dir()} but its digest "
                f"could not be computed. Refusing to compose a plan whose "
                f"integrity check would compare against an empty string.",
                filename=req.name,
            )
        if req.pin_sha256 and req.pin_sha256 != actual and not stub_shelf:
            raise PayloadPinMismatch(
                "PAYLOAD_PIN_MISMATCH",
                "Staged artifact does not match the adapter's pin",
                (
                    f"'{req.name}' on the shelf at {_shelf_dir()} has "
                    f"sha256={actual} but {req.adapter_id} pins {req.pin_sha256}. "
                    f"The bytes on this host are not the bytes this adapter was "
                    f"authored against. Re-stage with ./scripts/build-payloads.sh, "
                    f"or — if upstream legitimately shipped — update "
                    f"install.artifact.sha256 in the adapter pack, run "
                    f"`python3 -m engine.payload_shelf --write`, and re-stage. "
                    f"Refusing to compose against an unverified tool."
                ),
                adapter_id=req.adapter_id or None,
                filename=req.name,
                expected_sha256=req.pin_sha256,
                actual_sha256=actual,
            )
        basename = req.dest_path.rsplit("/", 1)[-1]
        composed.append(ComposedArtifact(
            name=req.name,
            sha256=actual,
            size_bytes=int(entry.get("size_bytes") or 0),
            url=f"{server_url.rstrip('/')}{path_prefix}/payload/{req.name}",
            dest_path=req.dest_path,
            dest_basename=basename,
            mode=req.mode,
            kind=req.kind,
            renamed=basename != req.name,
            adapter_id=req.adapter_id,
            origin=req.origin,
            source_url=req.source_url or entry.get("source_url"),
            license=req.license or entry.get("license"),
            pinned=bool(req.pin_sha256) or bool(entry.get("pinned")),
        ))

    warnings: list[dict[str, Any]] = []
    if stub_shelf and composed:
        warnings.append({
            "code": "STUB_SHELF",
            "detail": (
                f"The shelf at {_shelf_dir()} is marked as a test double "
                f"({STUB_SHELF_MARKER}); adapter sha256 pins were NOT enforced "
                f"and the digests below are of stub bytes, not the real tools. "
                f"A plan composed here proves the wiring, never the tooling."
            ),
        })
    for a in composed:
        if a.renamed:
            warnings.append({
                "code": "FILENAME_KEYED_DETECTIONS_SUPPRESSED",
                "payload": a.name,
                "detail": (
                    f"{a.name} is staged on disk as '{a.dest_basename}'. Detections "
                    f"keyed on the tool's FILENAME will not fire. This is a "
                    f"deliberate negative control: behavioural detections (process "
                    f"ancestry, enumeration burst, /proc and /etc read fan-out) MUST "
                    f"still fire. If NOTHING fires, the finding is that the "
                    f"customer's coverage is name-keyed — not that the TTP did not "
                    f"run."
                ),
            })
        if not a.pinned:
            warnings.append({
                "code": "ARTIFACT_UNPINNED",
                "payload": a.name,
                "detail": (
                    f"{a.name} has no digest pin. Its digest was recorded from "
                    f"whatever was downloaded, so an upstream change substitutes a "
                    f"different tool silently. Pin it in the adapter pack's "
                    f"install.artifact.sha256."
                ),
            })
    if unstaged:
        warnings.append({
            "code": "TARGET_EGRESS_REQUIRED",
            "detail": (
                f"{len(unstaged)} referenced adapter(s) have no staged artifact "
                f"({', '.join(u.adapter_id for u in unstaged)}). The TARGET host "
                f"will fetch those tools from the public internet at dispatch. A "
                f"default-deny egress policy — which is what the customers who buy "
                f"Cortex run — blocks that."
            ),
        })

    return Composition(
        composition_id=_composition_id(scenario_id, consumer, composed),
        scenario_id=scenario_id,
        consumer=consumer,
        server_url=server_url,
        artifacts=tuple(composed),
        unstaged_adapters=tuple(unstaged),
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# CLI — regenerate / check payloads/sources.json
# ---------------------------------------------------------------------------


def _cli(argv: list[str]) -> int:  # pragma: no cover - exercised via subprocess
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="python3 -m engine.payload_shelf")
    parser.add_argument("--write", action="store_true", help="write payloads/sources.json")
    parser.add_argument("--check", action="store_true", help="exit 1 on drift, print a diff")
    parser.add_argument("--stdout", action="store_true", help="print the rendered file")
    parser.add_argument("--packs-dir", default=None)
    parser.add_argument("--sources", default=None)
    args = parser.parse_args(argv)

    base = os.environ.get("CORTEXSIM_BASE_DIR", os.getcwd())
    packs_dir = args.packs_dir or os.path.join(base, "tools", "packs")
    sources = args.sources or os.path.join(base, "payloads", "sources.json")

    from tools.adapter_loader import load_adapters  # noqa: PLC0415

    # Through the REAL loader, never a regex: a pack that would be rejected at
    # boot must not be able to contribute a shelf entry.
    adapters = load_adapters(packs_dir).all()
    scenarios = _load_scenarios_for_used_by(base)
    artifacts = declared_artifacts(adapters, scenarios=scenarios)
    body = render_sources_json(artifacts, read_unbound(sources))

    if args.stdout:
        print(body, end="")
        return 0
    if args.check:
        problem = check_sources_drift(sources, artifacts=artifacts)
        if problem:
            print(problem)
            return 1
        print(f"OK {sources} matches tools/packs/*.yml "
              f"({len(artifacts)} derived, {len(read_unbound(sources))} unbound)")
        return 0
    if args.write:
        with open(sources, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"wrote {sources} ({len(artifacts)} artifact(s) from "
              f"{len({a.adapter_id for a in artifacts})} adapter pack(s))")
        return 0
    parser.print_help()
    return 2


def _load_scenarios_for_used_by(base: str) -> list[dict[str, Any]]:  # pragma: no cover
    """Best-effort scenario walk purely to populate the `used_by` annotation.

    Deliberately tolerant: `used_by` is a human hint in a generated file, and a
    malformed scenario must not be able to break shelf regeneration.
    """
    import glob  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(base, "scenarios", "**", "*.yml"), recursive=True)):
        if os.path.basename(path).startswith("_"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict) and doc.get("scenario_id"):
            out.append(doc)
    return out


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(_cli(sys.argv[1:]))

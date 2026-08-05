"""
CortexSim Tool Adapter Loader (Phase A).

Validates every ``tools/packs/*.yml`` adapter pack against a Pydantic schema
and exposes the resolved adapters via ``adapter_catalog``. Mirrors the
``scenario_loader`` + ``ttp_catalog`` pattern intentionally so contributors
have one mental model across all corpus loaders.

Validation rules (enforced):

1. ``adapter_id`` matches ``^TOOL-[A-Z0-9-]+$`` and is unique.
2. ``tier`` in {1..5}. Tier 5 forbids an ``invoke`` block. Tier 3 requires
   ``install.iac_module``. Tier 4 requires **at least one of**
   ``install.runtime_install_command`` / ``install.artifact`` (TA-02).
3. ``safety_class == c2-framework`` is annotated as launch-gated (the gate
   itself lives in the orchestrator).
4. ``safety_class == destructive`` requires non-empty ``cleanup.commands``.
5. ``cortex_signal.planes[]`` values are a subset of the plane enum.
6. ``upstream.license`` is required and may not be ``unknown``.
7. ``install.artifact`` (the payload-shelf binding) is validated against
   ``TA-01``..``TA-12`` — see :class:`ArtifactSchema` and
   ``docs/reference/payload-shelf.md``.

Invalid adapter files are rejected with a logged error and excluded from
the catalog — they never crash startup.

WHY ``TA-`` AND NOT ``A-``
-------------------------
``core/engine/assertions.py`` already owns ``A-10``..``A-24`` for assertion
artifacts. Reusing that prefix here would make ``grep -r 'A-14'`` return two
unrelated rules with opposite meanings, which is how a diagnostic code stops
being a diagnostic. Tool-adapter codes are ``TA-NN``.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger("cortexsim.tools.adapter_loader")


_ADAPTER_ID_RE = re.compile(r"^TOOL-[A-Z0-9-]+$")

_VALID_TIERS: set[int] = {1, 2, 3, 4, 5}
_VALID_CATEGORIES: set[str] = {
    "adversary-simulation", "c2-framework", "sandbox", "reverse-engineering",
    "network-scan", "web-app", "identity-credential", "cloud-container",
    "social-engineering", "wireless-iot", "analyst-workbench",
}
_VALID_SAFETY_CLASSES: set[str] = {
    "safe", "dual-use-lab-only", "c2-framework", "destructive",
}
_VALID_PLANES: set[str] = {
    "EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "ANALYTICS",
    "AI_ACCESS", "AIRS", "BROWSER", "KOI",
}
# Identity-harness vocabulary — mirrors the ``identity_required`` enum
# documented in ``tools/packs/_schema.yml`` (the harness wraps every TTP
# step as one of these service accounts to build realistic process causality
# in XSIAM). Scenario step ``identity`` values must come from the same set.
_VALID_IDENTITIES: set[str] = {
    "container-runtime", "root", "www-data", "nobody", "node",
    "postgres", "svc-backup", "administrator",
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class UpstreamSchema(BaseModel):
    repo: str
    license: str
    attribution: str

    @field_validator("license")
    @classmethod
    def _license_required(cls, v: str) -> str:
        if (v or "").strip().lower() in ("", "unknown", "tbd"):
            raise ValueError("upstream.license must be a real SPDX identifier")
        return v


class CortexSignalSchema(BaseModel):
    planes: list[str] = Field(default_factory=list)
    expected_techniques: list[str] = Field(default_factory=list)

    @field_validator("planes")
    @classmethod
    def _plane_subset(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in _VALID_PLANES]
        if bad:
            raise ValueError(f"unknown plane(s): {bad}")
        return v


# ---------------------------------------------------------------------------
# install.artifact — the payload-shelf binding
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS: all 50 tier-4 packs fetch their tool from the PUBLIC INTERNET
# **on the target host** at dispatch time (`command -v hydra || apt-get install
# -y hydra`). The customers who buy Cortex run default-deny egress, so that is
# the first thing their network blocks — and a step whose tool never arrived
# runs anyway, produces no detection, and the absent detection reads in a POV
# report as "Cortex missed it". A staged artifact moves that public-internet
# dependency onto the DC's OWN SimCore, where it is already accepted, and makes
# the bytes checksummable before they reach a customer.
#
# This block is the DECLARATION. `core/engine/payload_shelf.py` derives the
# staging list from it, so `payloads/sources.json` is generated rather than
# hand-maintained beside the packs. A hand-copied second list is a failure this
# repo has already paid for: sources.json today declares
# `adapter_id: TOOL-LINPEAS` for a pack that has never existed.

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Must equal `api.payloads._NAME_RE`. A filename the shelf endpoints would 400
#: on is unservable, so staging it produces an entry no consumer can fetch.
_ARTIFACT_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Bookkeeping files the shelf owns. Must equal `api.payloads._SHELF_METADATA`.
_ARTIFACT_RESERVED_NAMES = frozenset(
    {"MANIFEST.json", "SHA256SUMS", "README.md", "sources.json", ".gitignore"}
)

#: `archive` is deliberately NOT accepted yet: no consumer can unpack one. The
#: beacon is stdlib-only by contract and the K8s init container is a busybox
#: `wget`; accepting the value here would let a pack declare something that
#: silently never lands. The enum has one member on purpose — consumers branch
#: on `kind` today so adding `archive` later is additive, not a wire change.
_VALID_ARTIFACT_KINDS: set[str] = {"file"}
_DEFERRED_ARTIFACT_KINDS: set[str] = {"archive"}

_VALID_PIN_TYPES: set[str] = {"release-tag", "commit", "digest-only", "none"}

#: A staged artifact may only land under a root that is writable, non-system and
#: reapable. Same argument as the K8s hostPath refusal list: a content commit
#: must not be able to drop an executable into /usr/bin on a customer host.
#: Mirrored by `payload_shelf.ALLOWED_STAGE_ROOTS`.
_ALLOWED_STAGE_ROOTS: tuple[str, ...] = (
    "/tmp/", "/var/tmp/", "/dev/shm/", "/opt/cortexsim/",
)

#: URLs whose bytes change under you. NOT a rejection — a WARN. Pinned, one of
#: these fails the build loudly the next time upstream ships, which is correct.
#: Unpinned AND moving is the silent one, and TA-05/TA-06 already govern that.
_MOVING_URL_MARKERS: tuple[str, ...] = (
    "/releases/latest/download/",
    "/raw/main/", "/raw/master/",
    "/archive/refs/heads/",
    "@master", "@latest", "@main",
)


class ArtifactPinSchema(BaseModel):
    """Provenance of the digest pin.

    Two failure modes, and they are different — both sentences belong in the
    authoring doc because operators conflate them:

    * **Unpinned** (`type: none`): the builder records whatever it downloaded.
      Every consumer then verifies against *that* and passes. Nothing breaks and
      the DC silently ships a DIFFERENT tool than the one they tested. This is
      the dangerous one.
    * **Pinned to a moving URL**: the build FAILS loudly the next time upstream
      ships. Loud, diagnosable, correct.

    Pinning converts a silent substitution into a loud build failure.
    """

    type: str
    ref: Optional[str] = None
    waiver_reason: Optional[str] = None

    @field_validator("type")
    @classmethod
    def _pin_type_known(cls, v: str) -> str:
        if v not in _VALID_PIN_TYPES:
            raise ValueError(
                f"TA-07 install.artifact.pin.type must be one of "
                f"{sorted(_VALID_PIN_TYPES)}, got {v!r}"
            )
        return v


class ArtifactSchema(BaseModel):
    """One staged tool artifact this adapter binds to on the payload shelf."""

    filename: str
    url: str
    kind: str = "file"
    sha256: Optional[str] = None
    pin: ArtifactPinSchema
    license: Optional[str] = None
    stage_path: Optional[str] = None
    mode: str = "0755"

    @field_validator("filename")
    @classmethod
    def _filename_servable(cls, v: str) -> str:
        if not _ARTIFACT_FILENAME_RE.match(v or ""):
            raise ValueError(
                f"TA-03 install.artifact.filename must match "
                f"{_ARTIFACT_FILENAME_RE.pattern} (a bare filename, no path "
                f"separators, no '..'), got {v!r} — the shelf endpoints would "
                f"400 on it, so it could be staged but never served"
            )
        if v in _ARTIFACT_RESERVED_NAMES:
            raise ValueError(
                f"TA-03 install.artifact.filename {v!r} is a shelf bookkeeping "
                f"file; it is never listed or served as a payload"
            )
        return v

    @field_validator("url")
    @classmethod
    def _url_fetchable(cls, v: str) -> str:
        if not (v or "").startswith(("https://", "http://")):
            raise ValueError(
                f"TA-03 install.artifact.url must be http(s), got {v!r}"
            )
        return v

    @field_validator("kind")
    @classmethod
    def _kind_supported(cls, v: str) -> str:
        if v in _DEFERRED_ARTIFACT_KINDS:
            raise ValueError(
                f"TA-08 install.artifact.kind={v!r} is declared in the contract "
                f"but NO consumer can unpack it yet (the beacon is stdlib-only, "
                f"the K8s init container is a busybox wget). Accepting it would "
                f"let this pack declare a tool that silently never lands. Ship "
                f"the artifact as kind: file, or extract upstream first."
            )
        if v not in _VALID_ARTIFACT_KINDS:
            raise ValueError(
                f"TA-08 install.artifact.kind must be one of "
                f"{sorted(_VALID_ARTIFACT_KINDS)}, got {v!r}"
            )
        return v

    @field_validator("sha256")
    @classmethod
    def _digest_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not _SHA256_RE.match(v.strip().lower()):
            raise ValueError(
                f"TA-04 install.artifact.sha256 must be 64 lowercase hex chars, "
                f"got {v!r}"
            )
        return v.strip().lower()

    @field_validator("mode")
    @classmethod
    def _mode_octal(cls, v: str) -> str:
        raw = (v or "").strip()
        if not re.match(r"^0?[0-7]{3}$", raw):
            raise ValueError(
                f"TA-09 install.artifact.mode must be a 3-digit octal string "
                f"like '0755', got {v!r}"
            )
        return raw if raw.startswith("0") else "0" + raw

    @field_validator("stage_path")
    @classmethod
    def _stage_path_allowed(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _validate_stage_path(v, code="TA-09", field="install.artifact.stage_path")

    @model_validator(mode="after")
    def _pin_consistency(self) -> "ArtifactSchema":
        if self.pin.type != "none" and not self.sha256:
            raise ValueError(
                f"TA-05 install.artifact.pin.type={self.pin.type!r} claims this "
                f"artifact is pinned but install.artifact.sha256 is empty. "
                f"'Pinned to v1.2.3' with no digest is a claim, not a pin — "
                f"the builder would record whatever it downloaded and every "
                f"consumer would verify against that."
            )
        if self.pin.type == "none" and not (self.pin.waiver_reason or "").strip():
            raise ValueError(
                "TA-06 install.artifact.pin.type='none' requires a non-empty "
                "pin.waiver_reason. Unpinned is allowed; unpinned AND unexplained "
                "is not — an upstream change then substitutes a different tool "
                "silently and nothing anywhere goes red."
            )
        if self.pin.type in ("release-tag", "commit") and not (self.pin.ref or "").strip():
            raise ValueError(
                f"TA-07 install.artifact.pin.type={self.pin.type!r} requires "
                f"pin.ref (the tag or commit sha it is pinned to)"
            )
        return self

    @property
    def pinned(self) -> bool:
        return bool(self.sha256) and self.pin.type != "none"

    @property
    def moving_url(self) -> bool:
        return any(marker in self.url for marker in _MOVING_URL_MARKERS)


def _validate_stage_path(value: str, *, code: str, field: str) -> str:
    """Shared gate for every operator-supplied destination path.

    Three surfaces check this same property — the pack (TA-09), a scenario's
    ``external_tools[].stage_as`` (the loader track's S-19) and a compose-request
    override (``PAYLOAD_DEST_REFUSED``). They all call here so the three can
    never disagree about what ``/usr/bin/ls`` means.
    """
    raw = (value or "").strip()
    if not raw.startswith("/"):
        raise ValueError(f"{code} {field} must be an absolute path, got {value!r}")
    if any(seg == ".." for seg in raw.split("/")):
        raise ValueError(f"{code} {field} must not contain a '..' segment, got {value!r}")
    if "\x00" in raw or "\n" in raw:
        raise ValueError(f"{code} {field} must not contain NUL or newline")
    if not raw.startswith(_ALLOWED_STAGE_ROOTS):
        raise ValueError(
            f"{code} {field}={value!r} is outside the staging allowlist "
            f"{list(_ALLOWED_STAGE_ROOTS)}. CortexSim stages simulation "
            f"artifacts; it does not install into system paths on a host it "
            f"does not own."
        )
    basename = raw.rsplit("/", 1)[-1]
    if not _ARTIFACT_FILENAME_RE.match(basename):
        raise ValueError(
            f"{code} {field} basename {basename!r} must match "
            f"{_ARTIFACT_FILENAME_RE.pattern}"
        )
    return raw


class InstallSchema(BaseModel):
    # All fields optional at the schema level — tier validation in the
    # parent enforces which combinations are valid.
    source_path: Optional[str] = None
    build_cmd: Optional[str] = None
    binary: Optional[str] = None
    iac_module: Optional[str] = None
    content_library_entry: Optional[dict[str, Any]] = None
    runtime_install_command: Optional[str] = None
    #: Tier-4 only. The payload-shelf binding — see ArtifactSchema.
    artifact: Optional[ArtifactSchema] = None


class InvokeSchema(BaseModel):
    target_platform: str
    run_template: str
    default_args: dict[str, Any] = Field(default_factory=dict)
    identity_required: str

    @field_validator("target_platform")
    @classmethod
    def _platform_known(cls, v: str) -> str:
        allowed = {"linux", "windows", "macos", "k8s", "any"}
        if v not in allowed:
            raise ValueError(f"target_platform must be one of {allowed}")
        return v

    @field_validator("identity_required")
    @classmethod
    def _identity_known(cls, v: str) -> str:
        if v not in _VALID_IDENTITIES:
            raise ValueError(
                f"identity_required must be one of {sorted(_VALID_IDENTITIES)}, got {v!r}"
            )
        return v


class CleanupSchema(BaseModel):
    commands: list[str] = Field(default_factory=list)


class ToolAdapterSchema(BaseModel):
    adapter_id: str
    name: str
    version: str
    tier: int
    category: str
    upstream: UpstreamSchema
    cortex_signal: CortexSignalSchema
    safety_class: str
    install: InstallSchema = Field(default_factory=InstallSchema)
    invoke: Optional[InvokeSchema] = None
    cleanup: Optional[CleanupSchema] = None
    ttp_refs: list[str] = Field(default_factory=list)
    equivalents: list[str] = Field(default_factory=list)
    deprecated_by: Optional[str] = None
    author: Optional[str] = None
    created: Optional[str] = None
    last_updated: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("adapter_id")
    @classmethod
    def _adapter_id_format(cls, v: str) -> str:
        if not _ADAPTER_ID_RE.match(v):
            raise ValueError(f"adapter_id must match {_ADAPTER_ID_RE.pattern}, got {v!r}")
        return v

    @field_validator("tier")
    @classmethod
    def _tier_in_range(cls, v: int) -> int:
        if v not in _VALID_TIERS:
            raise ValueError(f"tier must be one of {_VALID_TIERS}, got {v}")
        return v

    @field_validator("category")
    @classmethod
    def _category_known(cls, v: str) -> str:
        if v not in _VALID_CATEGORIES:
            raise ValueError(f"category must be one of {_VALID_CATEGORIES}, got {v!r}")
        return v

    @field_validator("safety_class")
    @classmethod
    def _safety_known(cls, v: str) -> str:
        if v not in _VALID_SAFETY_CLASSES:
            raise ValueError(f"safety_class must be one of {_VALID_SAFETY_CLASSES}, got {v!r}")
        return v

    @property
    def reference_only(self) -> bool:
        """True when this adapter is NOT a scenario-wiring candidate by design —
        so an absence of any ``adapter_ref`` to it is intentional, not a gap
        (GAP-ADAPT-02 honest accounting).

        Two objective signals make a pack reference-only:
          * **tier 5** — external-only reference material the engine never
            executes (the DC brings their own commercial tool).
          * **c2-framework** — never auto-staged from a push bundle by policy;
            it is launch-gated reference content, wired only by explicit,
            consent-gated bespoke scenarios, not catalogued coverage.
        """
        return self.tier == 5 or self.safety_class == "c2-framework"

    @property
    def staged(self) -> bool:
        """True when this pack binds a payload-shelf artifact."""
        return self.install.artifact is not None

    @property
    def effective_stage_path(self) -> Optional[str]:
        """Where this adapter's artifact lands by DEFAULT, after TA-10 back-fill.

        This is the adapter-level default only. A scenario's
        ``external_tools[].stage_as`` and a compose-request override both sit
        above it — see ``docs/reference/payload-shelf.md`` §5.
        """
        art = self.install.artifact
        return art.stage_path if art else None

    @model_validator(mode="after")
    def _artifact_consistency(self) -> "ToolAdapterSchema":
        """TA-01 / TA-02 / TA-10 / TA-11 — the payload-shelf binding."""
        art = self.install.artifact
        if art is None:
            return self

        if self.tier != 4:
            raise ValueError(
                f"TA-01 install.artifact is tier-4 only, but this pack is tier "
                f"{self.tier}. Tier 1/2 are on-disk source trees, tier 3 is "
                f"IaC-provisioned on the target, tier 5 is never executed — none "
                f"of them fetch a tool at dispatch, so none of them has an "
                f"egress problem for the shelf to solve."
            )

        # TA-10 — the destination and what `{binary}` renders to must never
        # disagree. Two-way back-fill, the same discipline the K8s manifest
        # applies to its own paired fields.
        binary = (self.install.binary or "").strip()
        if binary.startswith("/"):
            if art.stage_path is None:
                self.install.artifact = art.model_copy(update={"stage_path": binary})
                art = self.install.artifact
                _validate_stage_path(
                    binary, code="TA-10",
                    field=f"install.binary (back-filled into install.artifact.stage_path)",
                )
            elif art.stage_path != binary:
                raise ValueError(
                    f"TA-10 install.binary={binary!r} and "
                    f"install.artifact.stage_path={art.stage_path!r} disagree. "
                    f"The artifact would land at one path and `{{binary}}` would "
                    f"render the other, so every step invoking this adapter would "
                    f"run a file that is not there. Set them equal, or drop "
                    f"stage_path and let it back-fill from install.binary."
                )
        elif art.stage_path is None:
            raise ValueError(
                f"TA-10 install.binary={binary!r} is a bare name, so there is "
                f"nothing to back-fill install.artifact.stage_path from. A staged "
                f"artifact does not go on PATH — declare the absolute path it "
                f"lands at."
            )

        return self

    @model_validator(mode="after")
    def _tier_install_consistency(self) -> "ToolAdapterSchema":
        """Tier-specific install/invoke requirements."""
        if self.tier == 5:
            if self.invoke is not None:
                raise ValueError(
                    "tier 5 (external-only) adapters must NOT carry an invoke block — "
                    "tier-5 tools are reference material, never executed by the engine"
                )
            return self

        # Tier 1..4 all require an invoke block.
        if self.invoke is None:
            raise ValueError(f"tier {self.tier} adapter must declare an invoke block")

        if self.tier == 3 and not self.install.iac_module:
            raise ValueError(
                "tier 3 (IaC-provisioned) adapter must declare install.iac_module — "
                "the engine needs to know which IaC module includes the install step"
            )
        if self.tier == 4 and not (
            self.install.runtime_install_command or self.install.artifact
        ):
            raise ValueError(
                "TA-02 tier 4 (runtime-fetched) adapter must declare at least one "
                "of install.runtime_install_command (fetched on the TARGET at "
                "dispatch — needs public-internet egress there) or "
                "install.artifact (staged on the DC's SimCore and served to the "
                "target — no target egress). A pack with neither declares a tool "
                "nothing can ever obtain."
            )
        if self.tier in (1, 2) and not self.install.source_path:
            raise ValueError(
                f"tier {self.tier} adapter must declare install.source_path"
            )

        # Destructive tools must declare cleanup. The engine refuses to dispatch
        # a destructive adapter without a non-empty cleanup.commands list.
        if self.safety_class == "destructive":
            if self.cleanup is None or not self.cleanup.commands:
                raise ValueError(
                    "safety_class=destructive requires non-empty cleanup.commands "
                    "(engine refuses to dispatch destructive adapters without cleanup)"
                )

        # run_template must reference every default_args key so substitution
        # at dispatch time produces a well-formed command line.
        for key in self.invoke.default_args.keys():
            placeholder = "{" + key + "}"
            if placeholder not in self.invoke.run_template:
                raise ValueError(
                    f"default_args key {key!r} does not appear in run_template — "
                    f"orphaned default would never substitute"
                )

        return self


# ---------------------------------------------------------------------------
# File walk + parse
# ---------------------------------------------------------------------------


def _find_pack_files(packs_dir: str) -> list[str]:
    """All ``*.yml`` files in packs_dir except ``_schema.yml``."""
    if not os.path.isdir(packs_dir):
        return []
    found: list[str] = []
    for fname in sorted(os.listdir(packs_dir)):
        if not fname.endswith(".yml") or fname.startswith("_"):
            continue
        found.append(os.path.join(packs_dir, fname))
    return found


def _parse_and_validate(filepath: str) -> tuple[Optional[ToolAdapterSchema], Optional[str]]:
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, f"YAML parse error: {exc}"

    if not isinstance(raw, dict):
        return None, "YAML root is not a mapping"

    try:
        return ToolAdapterSchema(**raw), None
    except ValidationError as exc:
        return None, f"Schema validation failed:\n{exc}"


def default_packs_dir(base_dir: str) -> str:
    """Convention: ``<base>/tools/packs``."""
    return os.path.join(base_dir, "tools", "packs")


# ---------------------------------------------------------------------------
# Cross-reference validation (warn-only, never fail — same policy as the
# dangling ttp_refs check documented in tools/packs/_schema.yml).
# ---------------------------------------------------------------------------


def validate_equivalents(adapters: list[ToolAdapterSchema]) -> list[str]:
    """Warn for any ``equivalents[]`` id that does not resolve to a loaded pack.

    Equivalents are intra-corpus cross-references (the "naive / intermediate /
    advanced / apt" rotation set for one TTP). A dead link is a silent picker
    dead-end, so we surface it at load time the same way scenario_loader
    surfaces dangling refs — logged as a warning, never a hard rejection.

    Returns the list of warning strings (also logged) so callers/tests can
    assert on them.
    """
    known: set[str] = {a.adapter_id for a in adapters}
    warnings: list[str] = []
    for adapter in adapters:
        for eq in adapter.equivalents:
            if eq not in known:
                msg = (
                    f"adapter {adapter.adapter_id}: equivalents[] id {eq!r} "
                    f"does not resolve to a known pack (dead cross-reference)"
                )
                warnings.append(msg)
                logger.warning(msg)
    return warnings


def validate_tier2_sources(
    adapters: list[ToolAdapterSchema],
    base_dir: Optional[str] = None,
) -> list[str]:
    """Warn for any tier-2 (submodule) pack whose ``install.source_path`` is
    missing on disk.

    Tier-2 adapters are git submodules provisioned by ``install.sh`` Step 3
    (``git submodule update --init --recursive``). In a fresh checkout — or any
    dev environment where the submodules have not been initialized — the
    declared ``sources/<tool>`` directory will be absent, which means the
    referencing scenarios cannot detonate (e.g. atomic-red-team backs 8 EDR/MP
    scenarios). We surface this at load time the same way
    :func:`validate_equivalents` surfaces dead cross-references — logged as a
    warning, **never** a hard rejection — so the server still boots without
    submodules in dev. The hard PASS/FAIL gate for CI/preflight lives in
    ``scripts/check-adapter-sources.sh``.

    Returns the list of warning strings (also logged) so callers/tests can
    assert on them.
    """
    if base_dir is None:
        base_dir = os.environ.get("CORTEXSIM_BASE_DIR", os.getcwd())

    warnings: list[str] = []
    for adapter in adapters:
        if adapter.tier != 2:
            continue
        source_path = adapter.install.source_path
        if not source_path:
            # Schema already enforces tier-2 has a source_path, but guard anyway.
            continue
        resolved = source_path if os.path.isabs(source_path) else os.path.join(base_dir, source_path)
        if not os.path.isdir(resolved):
            msg = (
                f"adapter {adapter.adapter_id}: tier-2 source_path {source_path!r} "
                f"is missing on disk (resolved {resolved!r}) — run "
                f"'git submodule update --init --recursive' (install.sh Step 3); "
                f"referencing scenarios cannot detonate until it is present"
            )
            warnings.append(msg)
            logger.warning(msg)
    return warnings


def validate_artifact_urls(adapters: list[ToolAdapterSchema]) -> list[str]:
    """TA-11 — WARN for an ``install.artifact.url`` that is a moving target.

    Not a rejection. Pinned, a moving URL fails the build loudly the next time
    upstream ships, which is the correct outcome. It is worth a log line anyway
    because an operator who then sets ``pin.type: none`` to "fix" the failure has
    converted a loud failure into a silent tool substitution.
    """
    warnings: list[str] = []
    for adapter in adapters:
        art = adapter.install.artifact
        if art is None or not art.moving_url:
            continue
        marker = next(m for m in _MOVING_URL_MARKERS if m in art.url)
        msg = (
            f"TA-11 adapter {adapter.adapter_id}: install.artifact.url is a "
            f"moving target ({marker!r}). Pinned, this build FAILS the next time "
            f"upstream ships — which is correct. UNPINNED, it silently "
            f"substitutes a different tool. Repoint at a release tag or a commit "
            f"sha."
        )
        warnings.append(msg)
        logger.warning(msg)
    return warnings


def validate_artifact_uniqueness(adapters: list[ToolAdapterSchema]) -> list[str]:
    """TA-12 — two packs may not claim one shelf filename with different bytes.

    The shelf is keyed by filename. Two packs declaring ``linpeas.sh`` with
    different digests is an unresolvable shelf: whichever staged last wins and
    the other pack's consumers verify against a digest that will never match.
    Same filename + same digest is fine (deduplicated by the shelf).

    Returns the error strings. Callers reject the offending adapters — see
    :func:`load_adapters` and ``AdapterCatalog.load``.
    """
    errors: list[str] = []
    seen: dict[str, tuple[str, Optional[str]]] = {}
    for adapter in adapters:
        art = adapter.install.artifact
        if art is None:
            continue
        prior = seen.get(art.filename)
        if prior is None:
            seen[art.filename] = (adapter.adapter_id, art.sha256)
            continue
        prior_id, prior_sha = prior
        if prior_sha != art.sha256:
            msg = (
                f"TA-12 adapter {adapter.adapter_id}: install.artifact.filename "
                f"{art.filename!r} is already claimed by {prior_id} with a "
                f"DIFFERENT sha256 ({prior_sha} vs {art.sha256}). The shelf is "
                f"keyed by filename, so only one set of bytes can exist — the "
                f"other pack's consumers would verify against a digest that can "
                f"never match."
            )
            errors.append(msg)
            logger.error(msg)
    return errors


def artifact_conflicting_ids(adapters: list[ToolAdapterSchema]) -> set[str]:
    """adapter_ids TA-12 rejects (the *second* claimant of a filename)."""
    conflicting: set[str] = set()
    seen: dict[str, Optional[str]] = {}
    for adapter in adapters:
        art = adapter.install.artifact
        if art is None:
            continue
        if art.filename in seen and seen[art.filename] != art.sha256:
            conflicting.add(adapter.adapter_id)
        else:
            seen.setdefault(art.filename, art.sha256)
    return conflicting


class _LoadedAdapters:
    """Thin read-only view returned by :func:`load_adapters`.

    Intentionally lighter than :class:`tools.adapter_catalog.AdapterCatalog`
    (which is the runtime singleton). This exists so callers — and the
    standalone validation entry point — can load + cross-ref-check packs
    without importing the catalog singleton (avoids a circular import, since
    the catalog imports this module)."""

    def __init__(self, adapters: list[ToolAdapterSchema], rejected: int) -> None:
        self._adapters = adapters
        self.rejected = rejected

    def all(self) -> list[ToolAdapterSchema]:
        return list(self._adapters)

    def __iter__(self):
        return iter(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)


def load_adapters(packs_dir: Optional[str] = None) -> _LoadedAdapters:
    """Parse + validate every pack under ``packs_dir`` and run cross-ref checks.

    Standalone helper (not the runtime singleton path — that is
    ``adapter_catalog.catalog.load``). Mirrors the catalog's per-file
    validation, then runs :func:`validate_equivalents` so dead equivalent
    links surface as warnings. ``packs_dir`` defaults to
    ``<CORTEXSIM_BASE_DIR or cwd>/tools/packs``.
    """
    if packs_dir is None:
        base = os.environ.get("CORTEXSIM_BASE_DIR", os.getcwd())
        packs_dir = default_packs_dir(base)

    adapters: list[ToolAdapterSchema] = []
    rejected = 0
    seen: set[str] = set()
    for filepath in _find_pack_files(packs_dir):
        adapter, error = _parse_and_validate(filepath)
        if error:
            logger.error("REJECTED adapter %s: %s", filepath, error)
            rejected += 1
            continue
        assert adapter is not None
        if adapter.adapter_id in seen:
            logger.error(
                "REJECTED adapter %s: duplicate adapter_id %s",
                filepath, adapter.adapter_id,
            )
            rejected += 1
            continue
        seen.add(adapter.adapter_id)
        adapters.append(adapter)

    # TA-12 is inherently cross-pack, so it runs after the walk. The *second*
    # claimant of a filename is rejected; the first keeps its binding.
    validate_artifact_uniqueness(adapters)
    conflicting = artifact_conflicting_ids(adapters)
    if conflicting:
        adapters = [a for a in adapters if a.adapter_id not in conflicting]
        rejected += len(conflicting)

    validate_equivalents(adapters)
    validate_artifact_urls(adapters)
    # base_dir for on-disk source_path resolution is the parent of packs_dir's
    # `tools/packs` convention (i.e. <base>/tools/packs → <base>).
    source_base = os.path.dirname(os.path.dirname(os.path.abspath(packs_dir)))
    validate_tier2_sources(adapters, base_dir=source_base)
    return _LoadedAdapters(adapters, rejected)

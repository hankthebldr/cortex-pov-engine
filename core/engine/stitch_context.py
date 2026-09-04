"""
CortexSim Stitch Context resolver — the INVERSE of the causality graph's
``_entities()`` coalescer, run at launch to plant one coherent set of shared
entities across every channel of a composed run.

Where ``causality_graph._entities()`` (`:268-294`) reads a run's Results and
observations and *coalesces* three field vocabularies (raw emitter keys,
normalized ``xdr_data`` columns, canonical ``xdm.*`` paths) DOWN to the eight
shared entity keys, this module runs the same mapping in REVERSE: it takes an
authored ``stitch_context`` spec, resolves each entity to one concrete value,
and *projects* that value back UP into each channel's own vocabulary so a
network EAL step, an endpoint beacon step and a modeling-rule query all plant
and read the SAME src_ip / 5-tuple / UPN / host / container / cloud resource.
Feeding :meth:`StitchBinding.as_xdr_columns` straight back into ``_entities()``
recovers the eight keys byte-identically — that round-trip IS the correctness
contract.

Design rules (mirror ``engine.causality_graph`` EXACTLY):

* **Pure.** No DB, no HTTP, no ORM session, no clock. Inputs are a spec dict
  (or a Pydantic ``StitchContextSchema`` exposing ``.model_dump()``) plus a
  seed and an optional launch-target descriptor. Fully unit-testable and
  directly API-servable.
* **Deterministic.** Every resolved value derives from ``seed`` (the run id) by
  a stable ``hashlib.sha256`` digest — NEVER unseeded randomness. The same seed
  always yields the same binding; distinct seeds yield distinct bindings; so a
  run's 5-tuple is reproducible and every step/channel that reads the binding
  gets byte-identical values.
* **Fail-closed (Gate A5 "tolerance hides bugs").** An unknown directive, a
  directive on an incompatible key, an entry that is not exactly one of
  ``{literal|resolve}``, or a key outside the nine RAISES
  :class:`StitchContextValidationError` — it never silently reads as empty. The
  resolver re-checks defensively so it cannot run on an un-validated spec.
* **Honest.** :attr:`StitchBinding.values` holds only the REAL resolved values
  (a literal passes through verbatim); nothing is invented. Persisted on the
  run, it lets the report / Run lens quote the exact entities used. The identity
  leg is DELEGATED to ``analytics_emitter.canary_bindings`` — not forked — so a
  canary principal reads identically wherever the platform already plants it.

The eight coalesced keys are the seed of the Stitch Context; Phase 2 adds a
ninth — ``cloud_resource`` — because cloud/SaaS events stitch by entity, not by a
CGO process tree, and ``_entities()`` (endpoint/network shaped) does not yet
read it. Widening ``_entities()`` to close the cloud round-trip is a follow-up.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from eal_simulator.analytics_emitter import canary_bindings

__all__ = [
    "ENTITY_KEYS",
    "DIRECTIVES",
    "DIRECTIVE_COMPAT",
    "StitchBinding",
    "StitchContextSchema",
    "StitchContextValidationError",
    "resolve_stitch_context",
    "validate_stitch_context_spec",
]

# ---------------------------------------------------------------------------
# Frozen vocabulary — mirrors causality_graph._entities' eight keys + the
# Phase-2 ninth, in this exact order (the UI groups by NICE; the data is flat).
# ---------------------------------------------------------------------------

#: The nine canonical entity keys. The first eight are exactly the keys
#: ``causality_graph._entities()`` returns; ``cloud_resource`` is the Phase-2
#: addition (cloud stitches by entity, no CGO).
ENTITY_KEYS: tuple[str, ...] = (
    "host",
    "src_ip",
    "dst_ip",
    "src_port",
    "dst_port",
    "protocol",
    "container_id",
    "account",
    "cloud_resource",
)

#: The closed six-directive set Phase 2 ships. Anything else → reject.
DIRECTIVES: frozenset[str] = frozenset(
    {
        "auto_ip",
        "auto_port",
        "auto_5tuple",
        "canary_principal",
        "from_agent",
        "auto_container_id",
    }
)

#: Which keys each directive may legally resolve. ``auto_5tuple`` declared on
#: ONE of the five tuple keys is sufficient and fills all five coherently; it is
#: rejected on host/container_id/account/cloud_resource.
DIRECTIVE_COMPAT: dict[str, frozenset[str]] = {
    "auto_ip": frozenset({"src_ip", "dst_ip"}),
    "auto_port": frozenset({"src_port", "dst_port"}),
    "auto_5tuple": frozenset({"src_ip", "src_port", "dst_ip", "dst_port", "protocol"}),
    "canary_principal": frozenset({"account"}),
    "from_agent": frozenset({"host", "src_ip"}),
    "auto_container_id": frozenset({"container_id"}),
}

#: The five keys ``auto_5tuple`` fills together.
_TUPLE_KEYS: tuple[str, ...] = ("src_ip", "src_port", "dst_ip", "dst_port", "protocol")


# ---------------------------------------------------------------------------
# Failure — fail-closed, mapped by the drafts/runs API to 422
# STITCH_CONTEXT_INVALID naming the offending key + directive.
# ---------------------------------------------------------------------------


class StitchContextValidationError(ValueError):
    """An un-loadable ``stitch_context`` spec — raised at parse AND resolve time.

    Carries the offending ``key`` and ``directive`` so the API layer can name
    them in the ``STITCH_CONTEXT_INVALID`` detail rather than emitting a generic
    sentence. ``ValueError`` subclass so existing ``except ValueError`` funnels
    (schema loaders) still catch it.
    """

    code = "STITCH_CONTEXT_INVALID"

    def __init__(
        self,
        message: str,
        *,
        key: Optional[str] = None,
        directive: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.key = key
        self.directive = directive


# ---------------------------------------------------------------------------
# The binding — nine resolved concrete values + three inverse projections.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StitchBinding:
    """One run's resolved shared entities + the three channel projections.

    The nine fields hold the REAL values injected into this run's step commands
    (``.values`` is the 9-key dict persisted on ``runs.stitch_binding``). The
    three projection methods are the INVERSE of ``causality_graph._entities()``:
    each returns a dict keyed in one channel's own vocabulary, emitting only the
    keys that actually resolved (a ``None`` leg is simply absent, exactly as an
    absent column leaves an ``_entities`` edge ``EXPECTED`` rather than raising).
    """

    host: Any = None
    src_ip: Any = None
    dst_ip: Any = None
    src_port: Any = None
    dst_port: Any = None
    protocol: Any = None
    container_id: Any = None
    account: Any = None
    cloud_resource: Any = None
    #: The identity-leg UPN (``{token}@cortexsim-canary.invalid``) when the
    #: ``account`` key resolved via ``canary_principal`` — delegated verbatim
    #: from ``analytics_emitter.canary_bindings``. ``None`` otherwise. Not one
    #: of the nine entity keys; surfaced for the report's identity readout.
    principal: Optional[str] = None

    # -- accessors ----------------------------------------------------------

    @property
    def values(self) -> dict[str, Any]:
        """The 9-key dict of resolved values — the run's persisted binding.

        Every entity key is present; an un-declared key holds ``None``. These
        are the real values used, nothing invented.
        """
        return {k: getattr(self, k) for k in ENTITY_KEYS}

    def get(self, key: str) -> Any:
        """The resolved value for one entity key, or ``None`` (never raises).

        An unknown key returns ``None`` — the injection site leaves an unknown
        ``{stitch:KEY}`` verbatim rather than expanding it, matching the adapter
        placeholder honesty rule.
        """
        if key not in ENTITY_KEYS:
            return None
        return getattr(self, key)

    # -- projections (inverse of causality_graph._entities) -----------------

    def as_raw(self) -> dict[str, Any]:
        """Emitter WIRE keys — drop straight into a plugin record dict.

        Matches the NGFW emitter's own spelling (``src``/``dst``/``proto``;
        ``ngfw_eal_emitter.py:142-147``). The two IP legs use emitter-native
        ``src``/``dst``, which ``_entities()`` does NOT read (it reads IPs only
        under ``src_ip``/``source_ip``/``action_local_ip`` and the dst mirror),
        so the IP round-trip is asserted through :meth:`as_xdr_columns`, not
        this projection — by design.
        """
        return self._project(
            {
                "host": "host",
                "src_ip": "src",
                "dst_ip": "dst",
                "src_port": "src_port",
                "dst_port": "dst_port",
                "protocol": "proto",
                "container_id": "container_id",
                "account": "account",
                "cloud_resource": "cloud_resource",
            }
        )

    def as_xdr_columns(self) -> dict[str, Any]:
        """Normalized ``xdr_data`` columns — THE clean round-trip target.

        Every spelling here is in an ``_entities()`` pick list, so feeding this
        dict to ``_entities(result=..., observation=None)`` recovers all eight
        shared keys byte-identically (``cloud_resource`` → ``resource_name`` is
        carried but ignored by the endpoint/network-shaped ``_entities``).
        """
        return self._project(
            {
                "host": "agent_hostname",
                "src_ip": "source_ip",
                "dst_ip": "dest_ip",
                "src_port": "source_port",
                "dst_port": "dest_port",
                "protocol": "protocol",
                "container_id": "container_id",
                "account": "actor_effective_user_name",
                "cloud_resource": "resource_name",
            }
        )

    def as_xdm(self) -> dict[str, Any]:
        """Canonical ``xdm.*`` paths — the authoritative modeling-rule dictionary.

        Source/target/container spellings are verified present in the in-repo
        modeling-rule exports (spec §4.2). ``protocol`` targets
        ``xdm.network.application_protocol`` — the only in-repo protocol path;
        the 5-tuple leg carries an IP protocol like ``tcp`` (semantically
        ``xdm.network.ip_protocol``), so the exact XDM field is the modeling-rule
        owner's call and ``xdm.network.ip_protocol`` is NOT invented here.
        ``cloud_resource`` → ``xdm.target.resource.name`` is provisional (no
        cloud-resource modeling rule ships yet).
        """
        return self._project(
            {
                "host": "xdm.source.host.hostname",
                "src_ip": "xdm.source.ipv4",
                "dst_ip": "xdm.target.ipv4",
                "src_port": "xdm.source.port",
                "dst_port": "xdm.target.port",
                "protocol": "xdm.network.application_protocol",
                "container_id": "xdm.target.container.id",
                "account": "xdm.source.user.username",
                "cloud_resource": "xdm.target.resource.name",
            }
        )

    def _project(self, mapping: dict[str, str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for entity_key, channel_key in mapping.items():
            value = getattr(self, entity_key)
            if value is not None:
                out[channel_key] = value
        return out


# ---------------------------------------------------------------------------
# Deterministic derivations — every value hangs off sha256(seed:label), never
# unseeded random, so a run's entities are reproducible and unit-testable.
# ---------------------------------------------------------------------------


def _digest(seed: str, label: str) -> bytes:
    return hashlib.sha256(f"{seed}:{label}".encode("utf-8")).digest()


def _auto_ip(seed: str, label: str) -> str:
    """A lab-range ``10.a.b.c`` address; octets avoid ``.0``/``.255``."""
    h = _digest(seed, f"auto_ip:{label}")
    a, b, c = (1 + h[i] % 254 for i in range(3))  # 1..254, never 0 or 255
    return f"10.{a}.{b}.{c}"


def _auto_port(seed: str, label: str) -> int:
    """An ephemeral-range port (49152..65535)."""
    h = _digest(seed, f"auto_port:{label}")
    return 49152 + int.from_bytes(h[:2], "big") % 16384


def _five_tuple_values(seed: str) -> dict[str, Any]:
    """The coherent 5-tuple — derived independently of which key declared it.

    Idempotent: ``auto_5tuple`` on any one of the five keys (or on several)
    yields this same fill, so authoring is unambiguous. Protocol defaults to
    ``tcp`` (an IP protocol, matching the raw ``proto`` leg).
    """
    return {
        "src_ip": _auto_ip(seed, "5tuple:src_ip"),
        "src_port": _auto_port(seed, "5tuple:src_port"),
        "dst_ip": _auto_ip(seed, "5tuple:dst_ip"),
        "dst_port": _auto_port(seed, "5tuple:dst_port"),
        "protocol": "tcp",
    }


def _canary_token(seed: str) -> str:
    """A deterministic canary token satisfying ``analytics_emitter`` shape.

    ``csim-<12 hex>`` — lowercase alphanumerics-and-hyphens, the intersection a
    sAMAccountName, an email local part, a k8s name and an XQL literal all
    accept (matching ``CANARY_TOKEN_RE``).
    """
    return "csim-" + _digest(seed, "canary").hex()[:12]


def _auto_container_id(seed: str) -> str:
    """A full 64-hex container id slug (Docker/containerd id shape)."""
    return _digest(seed, "container_id").hex()


def _target_field(target: Any, field_name: str) -> Any:
    if target is None:
        return None
    if isinstance(target, dict):
        value = target.get(field_name)
    else:
        value = getattr(target, field_name, None)
    return value if value not in (None, "") else None


def _from_agent(seed: str, key: str, target: Any) -> Any:
    """The launch target's hostname (``host``) or ip (``src_ip``).

    Falls back to a clearly-synthetic hashed sentinel when the target is absent
    or lacks the field — NEVER a fabricated real hostname.
    """
    if key == "src_ip":
        ip = _target_field(target, "ip")
        return ip if ip is not None else _auto_ip(seed, "from_agent:src_ip")
    # key == "host"
    hostname = _target_field(target, "hostname")
    if hostname is not None:
        return hostname
    return "cortexsim-target-" + _digest(seed, "from_agent:host").hex()[:8]


# ---------------------------------------------------------------------------
# The resolver.
# ---------------------------------------------------------------------------


def _coerce_spec(spec: Any) -> Optional[dict[str, Any]]:
    """Normalize ``None`` / a Pydantic model / a dict to a plain dict or None.

    An empty spec resolves to ``None`` so injection becomes a no-op and a
    context-less scenario behaves exactly as today.
    """
    if spec is None:
        return None
    if hasattr(spec, "model_dump"):
        spec = spec.model_dump()
    if not isinstance(spec, dict):
        raise StitchContextValidationError(
            f"stitch_context must be a mapping, got {type(spec).__name__}"
        )
    # Drop keys whose entry is None (an un-set Pydantic field) so an all-None
    # model dumps to nothing → a no-op binding.
    trimmed = {k: v for k, v in spec.items() if v is not None}
    return trimmed or None


def _validate_entry(key: str, entry: Any) -> tuple[bool, Any]:
    """Return ``(is_literal, payload)`` after the exactly-one-of check."""
    if not isinstance(entry, dict):
        raise StitchContextValidationError(
            f"stitch_context[{key!r}] must be an object with exactly one of "
            f"'literal' or 'resolve'",
            key=key,
        )
    has_literal = "literal" in entry
    has_resolve = "resolve" in entry
    if has_literal == has_resolve:  # both, or neither
        raise StitchContextValidationError(
            f"stitch_context[{key!r}] must set exactly one of 'literal' or "
            f"'resolve', not "
            + ("both" if has_literal else "neither"),
            key=key,
        )
    if has_literal:
        return True, entry["literal"]
    return False, entry["resolve"]


def resolve_stitch_context(
    spec: Any,
    *,
    seed: str,
    target: Optional[Any] = None,
) -> Optional[StitchBinding]:
    """Resolve an authored ``stitch_context`` spec to a concrete binding.

    Parameters
    ----------
    spec:
        The ``stitch_context`` block — a dict keyed by entity key, a Pydantic
        ``StitchContextSchema`` (anything exposing ``.model_dump()``), or
        ``None``. Each entry is exactly one of ``{literal: <scalar>}`` or
        ``{resolve: <directive>}``. ``None``/empty ⇒ this returns ``None`` and
        injection is a no-op.
    seed:
        The run id. Every ``resolve`` directive derives its value from a stable
        hash of this seed, so the binding is reproducible and unit-testable.
    target:
        The launch-target descriptor for ``from_agent`` — a ``{hostname, ip}``
        dict or an ORM row exposing ``.hostname``/``.ip``. Optional; when absent
        ``from_agent`` degrades to a hashed sentinel.

    Returns
    -------
    StitchBinding | None
        ``None`` for a ``None``/empty spec; otherwise the resolved binding whose
        ``.values`` are the REAL values injected into this run.

    Raises
    ------
    StitchContextValidationError
        On a key outside the nine, an entry that is not exactly one of
        ``{literal|resolve}``, an unknown directive, or a directive on an
        incompatible key. Fail-closed — the resolver cannot run on an
        un-validated spec.
    """
    spec_dict = _coerce_spec(spec)
    if spec_dict is None:
        return None

    resolved: dict[str, Any] = {k: None for k in ENTITY_KEYS}
    principal: Optional[str] = None
    five_tuple_requested = False

    for key, entry in spec_dict.items():
        if key not in ENTITY_KEYS:
            raise StitchContextValidationError(
                f"unknown stitch_context key {key!r} (not one of {ENTITY_KEYS})",
                key=key,
            )

        is_literal, payload = _validate_entry(key, entry)
        if is_literal:
            resolved[key] = payload  # verbatim — honest passthrough
            continue

        directive = payload
        if directive not in DIRECTIVES:
            raise StitchContextValidationError(
                f"unknown resolve directive {directive!r} on key {key!r} "
                f"(closed set: {sorted(DIRECTIVES)})",
                key=key,
                directive=directive,
            )
        if key not in DIRECTIVE_COMPAT[directive]:
            raise StitchContextValidationError(
                f"directive {directive!r} is not valid on key {key!r} "
                f"(valid keys: {sorted(DIRECTIVE_COMPAT[directive])})",
                key=key,
                directive=directive,
            )

        if directive == "auto_5tuple":
            five_tuple_requested = True  # filled after the loop, coherently
        elif directive == "auto_ip":
            resolved[key] = _auto_ip(seed, key)
        elif directive == "auto_port":
            resolved[key] = _auto_port(seed, key)
        elif directive == "auto_container_id":
            resolved[key] = _auto_container_id(seed)
        elif directive == "from_agent":
            resolved[key] = _from_agent(seed, key, target)
        elif directive == "canary_principal":
            # Identity leg DELEGATED — never forked. The token → account/principal
            # mapping is exactly what analytics_emitter already plants.
            bindings = canary_bindings(_canary_token(seed))
            resolved[key] = bindings["account"]
            principal = bindings["principal"]

    if five_tuple_requested:
        tup = _five_tuple_values(seed)
        for tk, tv in tup.items():
            # A key given its own literal/directive wins; auto_5tuple only fills
            # the legs still unresolved — so it is idempotent and composable.
            if resolved[tk] is None:
                resolved[tk] = tv

    return StitchBinding(**resolved, principal=principal)


# ---------------------------------------------------------------------------
# Seedless structural validation — the parse-time guard shared by the Pydantic
# schema (below) and the drafts/runs API. It answers "is this a well-formed
# stitch_context spec?" WITHOUT a seed, so it never resolves a value; the
# resolver re-checks the same rules defensively at resolve time.
# ---------------------------------------------------------------------------


def validate_stitch_context_spec(spec: Any) -> dict[str, Any]:
    """Validate a ``stitch_context`` block's SHAPE and return the declared-only
    dict (``None``/empty ⇒ ``{}``).

    Enforces exactly the four fail-closed rules the resolver enforces, minus the
    resolution itself: every key is one of the nine (:data:`ENTITY_KEYS`); every
    entry is exactly one of ``{literal|resolve}``; every ``resolve`` names a
    directive in the closed set (:data:`DIRECTIVES`); and that directive is
    legal on its key (:data:`DIRECTIVE_COMPAT`). Any violation raises
    :class:`StitchContextValidationError` carrying the offending key (+directive)
    so the API can name it in the ``STITCH_CONTEXT_INVALID`` detail.

    No seed is taken and no value is derived — this is the *parse-time* guard.
    """
    normalized = _coerce_spec(spec)
    if normalized is None:
        return {}

    for key, entry in normalized.items():
        if key not in ENTITY_KEYS:
            raise StitchContextValidationError(
                f"unknown stitch_context key {key!r} (not one of {ENTITY_KEYS})",
                key=key,
            )
        is_literal, payload = _validate_entry(key, entry)
        if is_literal:
            continue
        directive = payload
        if directive not in DIRECTIVES:
            raise StitchContextValidationError(
                f"unknown resolve directive {directive!r} on key {key!r} "
                f"(closed set: {sorted(DIRECTIVES)})",
                key=key,
                directive=directive,
            )
        if key not in DIRECTIVE_COMPAT[directive]:
            raise StitchContextValidationError(
                f"directive {directive!r} is not valid on key {key!r} "
                f"(valid keys: {sorted(DIRECTIVE_COMPAT[directive])})",
                key=key,
                directive=directive,
            )
    return normalized


class StitchContextSchema(BaseModel):
    """Pydantic view over the flat ``stitch_context`` block — the additive,
    optional field on :class:`~engine.composer_draft_schema.DraftScenarioSchema`
    and the shape stored in ``scenarios.stitch_context``.

    The data is a flat object keyed by entity key (the UI groups by NICE; the
    persisted shape does not). Each of the nine keys, when present, is exactly
    one of ``{literal: <scalar>}`` or ``{resolve: <directive>}``. A
    ``model_validator(mode="before")`` runs :func:`validate_stitch_context_spec`
    so a bad shape RAISES :class:`StitchContextValidationError` (Gate A5 —
    tolerance hides bugs) rather than silently reading as empty; the nine fields
    then store the declared entries and ``model_dump(exclude_none=True)`` yields
    the declared-only flat dict :func:`draft_to_orm_kwargs` persists.

    This is a pure authoring/serialization model — it never resolves a value.
    Resolution (the seed-derived concrete binding) is
    :func:`resolve_stitch_context`'s job at launch.
    """

    model_config = ConfigDict(extra="ignore")

    host: Optional[Any] = None
    src_ip: Optional[Any] = None
    dst_ip: Optional[Any] = None
    src_port: Optional[Any] = None
    dst_port: Optional[Any] = None
    protocol: Optional[Any] = None
    container_id: Optional[Any] = None
    account: Optional[Any] = None
    cloud_resource: Optional[Any] = None

    @model_validator(mode="before")
    @classmethod
    def _validate_shape(cls, data: Any) -> Any:
        # Reuse the ONE structural guard; raises StitchContextValidationError
        # (a ValueError subclass) on any malformed entry, which pydantic surfaces
        # as a ValidationError when nested inside DraftScenarioSchema.
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        validate_stitch_context_spec(data)
        return data

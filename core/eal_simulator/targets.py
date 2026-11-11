"""Collector targets: where every in-process collector step POSTs.

A SimCore instance serves one POV against one tenant, so the tenant-side
ingestion doors (the XSIAM HTTP log collector, the Broker VM's HTTP applet, a
forwarder in front of either) are instance configuration, entered once. This
module is the one resolver every consumer calls — campaign create, the
collector view, campaign preflight, launch, the offline bundle and the Composer
in-process dispatch — so they cannot disagree about a destination.

Pure: no database, no network. Callers load ``TargetSpec`` rows and, only when
they are about to send, the keys. Precedence per collector step:

  explicit  ``params.collector_target`` names a target (unknown name = error)
  binding   the one target whose ``plugins`` lists the step's plugin
            (two such targets = ambiguous, an error)
  default   the instance's default target
  inline    no target resolved, the step carries its own ``collector_url``
  none      nothing to send to

A resolved target replaces an inline URL, and the resolution records what it
replaced: the instance configuration is authoritative over a per-launch value.

Spec: docs/superpowers/specs/2026-11-10-collector-targets-design.md
"""

from __future__ import annotations

import copy
import dataclasses
import re
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

#: The kinds a target may declare. The label decides defaults and the
#: configured-rung rules, not the transport — every kind is an HTTP POST.
KINDS: tuple[str, ...] = ("xsiam_http_collector", "broker_vm_http", "http_forwarder")

#: Kinds whose collector refuses a POST without a key.
KINDS_REQUIRING_KEY: frozenset[str] = frozenset({"xsiam_http_collector"})

#: Every verdict a stored preflight may carry. Anything else raises.
CHECK_VERDICTS: tuple[str, ...] = (
    "not_configured", "unreachable", "reachable", "refused", "accepted",
)

#: Verdicts that refuse a live launch outright.
FAILING_VERDICTS: frozenset[str] = frozenset({"not_configured", "unreachable", "refused"})

#: Validation-only stand-in when no target resolves. ``.invalid`` (RFC 2606)
#: never resolves in DNS, so a send that slipped past the launch gate fails
#: closed as ``collector_unreachable`` instead of reaching anybody.
UNRESOLVED_COLLECTOR_URL = "https://collector-target-not-configured.invalid/logs/v1/event"

#: Step param that names a target. Consumed here; plugins never see it.
TARGET_PARAM = "collector_target"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")

_DEFAULT_PORTS = {"http": 80, "https": 443}


class TargetSpecError(ValueError):
    """A stored target or check result has a shape this code does not know."""


@dataclasses.dataclass(frozen=True)
class TargetSpec:
    """The non-secret half of one collector target."""

    name: str
    kind: str
    url: str
    dataset: Optional[str]
    auth_header: str
    auth_scheme: str
    plugins: tuple[str, ...]
    is_default: bool
    has_token: bool
    last_check: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.name or ""):
            raise TargetSpecError(f"target name {self.name!r} is not a slug")
        if self.kind not in KINDS:
            raise TargetSpecError(
                f"target {self.name!r} has kind {self.kind!r}; known kinds are {KINDS}"
            )
        parsed = urlparse(self.url or "")
        if parsed.scheme not in _DEFAULT_PORTS or not parsed.hostname:
            raise TargetSpecError(f"target {self.name!r} url is not an http(s) URL with a host")

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""

    @property
    def port(self) -> int:
        parsed = urlparse(self.url)
        return parsed.port or _DEFAULT_PORTS[parsed.scheme]

    @property
    def allowlist_pin(self) -> str:
        host = self.host
        if ":" in host:  # IPv6 literal
            host = f"[{host}]"
        return f"{host}:{self.port}"

    def warnings(self) -> list[str]:
        out: list[str] = []
        parsed = urlparse(self.url)
        if parsed.path in ("", "/"):
            out.append("url has no path — a collector expects its full ingestion path")
        if self.kind == "xsiam_http_collector" and parsed.path.rstrip("/") != "/logs/v1/event":
            out.append(
                "an XSIAM HTTP log collector's path is normally /logs/v1/event — "
                "check it against the URL the tenant console shows"
            )
        if parsed.scheme == "http" and self.host not in ("localhost", "127.0.0.1", "::1"):
            out.append("plaintext http to a non-local host — records cross the network in the clear")
        if self.kind in KINDS_REQUIRING_KEY and not self.has_token:
            out.append("no key stored — this collector refuses a POST without one")
        return out


def check_verdict(last_check: Optional[Mapping[str, Any]]) -> str:
    """The stored preflight verdict, or ``unchecked``. An unknown verdict raises:
    reading it as healthy is how a broken door would launch."""
    if last_check is None:
        return "unchecked"
    if not isinstance(last_check, Mapping):
        raise TargetSpecError(f"stored check is {type(last_check).__name__}, not a mapping")
    verdict = last_check.get("verdict")
    if verdict not in CHECK_VERDICTS:
        raise TargetSpecError(f"stored check verdict {verdict!r} is not one of {CHECK_VERDICTS}")
    return verdict


@dataclasses.dataclass
class StepResolution:
    """Where one collector step goes, and why."""

    step_id: str
    plugin: str
    source: str                       # explicit | binding | default | inline | none | error
    target: Optional[str] = None
    url: Optional[str] = None
    code: Optional[str] = None        # set for every outcome that is not a clean resolve
    detail: Optional[str] = None
    replaced_inline_url: Optional[str] = None
    has_key: bool = False
    check: str = "n/a"                # the target's stored verdict, or n/a

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def collector_plugin_names(registry: Any) -> set[str]:
    """Plugins whose params model declares ``collector_url`` — the ones that
    POST to an operator-supplied collector rather than a third party."""
    out: set[str] = set()
    for name in registry.names():
        model = getattr(getattr(registry.get(name), "Meta", None), "params_model", None)
        if model is not None and "collector_url" in getattr(model, "model_fields", {}):
            out.add(name)
    return out


def _resolve_one(
    step_id: str, plugin: str, params: Mapping[str, Any], targets: Sequence[TargetSpec],
) -> StepResolution:
    by_name = {t.name: t for t in targets}
    explicit = params.get(TARGET_PARAM)
    inline = params.get("collector_url") or None

    chosen: Optional[TargetSpec] = None
    source = ""
    if explicit:
        chosen = by_name.get(str(explicit))
        if chosen is None:
            return StepResolution(
                step_id, plugin, "error", code="COLLECTOR_TARGET_UNKNOWN",
                detail=f"step names collector_target {explicit!r}; configured targets: "
                       f"{sorted(by_name) or 'none'}",
            )
        source = "explicit"
    else:
        bound = [t for t in targets if plugin in t.plugins]
        if len(bound) > 1:
            return StepResolution(
                step_id, plugin, "error", code="COLLECTOR_TARGET_AMBIGUOUS",
                detail=f"targets {sorted(t.name for t in bound)} all serve {plugin!r}; "
                       "bind it to one, or name one with collector_target",
            )
        if bound:
            chosen, source = bound[0], "binding"
        else:
            defaults = [t for t in targets if t.is_default]
            if len(defaults) > 1:  # the store refuses this; a second one here is corrupt
                raise TargetSpecError(f"more than one default target: {sorted(t.name for t in defaults)}")
            if defaults:
                chosen, source = defaults[0], "default"

    if chosen is None:
        if inline:
            return StepResolution(
                step_id, plugin, "inline", url=str(inline), code="COLLECTOR_TARGET_INLINE",
                detail="the step carries its own collector_url; no instance target serves this plugin",
            )
        return StepResolution(
            step_id, plugin, "none", code="COLLECTOR_TARGET_NOT_CONFIGURED",
            detail=f"no collector target serves {plugin!r} and there is no default target",
        )

    return StepResolution(
        step_id, plugin, source, target=chosen.name, url=chosen.url,
        replaced_inline_url=str(inline) if inline and inline != chosen.url else None,
        has_key=chosen.has_token, check=check_verdict(chosen.last_check),
    )


def apply_targets(
    spec: Mapping[str, Any],
    registry: Any,
    targets: Sequence[TargetSpec],
    *,
    tokens: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, Any], list[StepResolution]]:
    """Return ``(resolved_spec, resolutions)`` for a campaign spec dict.

    The input is never mutated. Collector steps get the target's URL and auth
    header/scheme; ``auth_token`` is set only from ``tokens`` (pass it only on
    the path that is about to send). Each resolved target's exact host:port is
    added to ``target_allowlist``. A step whose plugin is not registered, or
    does not POST to a collector, is left exactly as it was.
    """
    out = copy.deepcopy(dict(spec))
    collector_plugins = collector_plugin_names(registry)
    by_name = {t.name: t for t in targets}
    resolutions: list[StepResolution] = []
    pins: list[str] = []

    for step in out.get("steps") or []:
        plugin = step.get("plugin")
        if plugin not in collector_plugins:
            continue
        params = dict(step.get("params") or {})
        res = _resolve_one(str(step.get("step_id")), plugin, params, targets)
        resolutions.append(res)
        params.pop(TARGET_PARAM, None)

        if res.target is not None:
            target = by_name[res.target]
            params["collector_url"] = target.url
            params["auth_header"] = target.auth_header
            params["auth_scheme"] = target.auth_scheme
            params.pop("auth_token", None)
            if tokens and res.target in tokens:
                params["auth_token"] = tokens[res.target]
            if target.allowlist_pin not in pins:
                pins.append(target.allowlist_pin)
        elif res.source in ("none", "error"):
            params["collector_url"] = UNRESOLVED_COLLECTOR_URL
        step["params"] = params

    allow = list(out.get("target_allowlist") or [])
    out["target_allowlist"] = allow + [p for p in pins if p not in allow]
    return out, resolutions


def blocking(resolutions: Sequence[StepResolution]) -> list[StepResolution]:
    """Resolutions that must refuse a live launch: nothing to send to, or a
    name that does not resolve to exactly one target."""
    return [r for r in resolutions if r.source in ("none", "error")]


def failing(resolutions: Sequence[StepResolution]) -> list[StepResolution]:
    """Resolved to a target whose last check failed."""
    return [r for r in resolutions if r.target and r.check in FAILING_VERDICTS]


def degradations(resolutions: Sequence[StepResolution]) -> list[dict[str, Any]]:
    """What a live launch carries on its record without being refused."""
    out: list[dict[str, Any]] = []
    for r in resolutions:
        if r.source == "inline":
            out.append({"code": "COLLECTOR_TARGET_INLINE", "step_id": r.step_id, "detail": r.detail})
        elif r.target and r.check in ("unchecked", "reachable"):
            out.append({
                "code": "COLLECTOR_TARGET_UNPROVEN", "step_id": r.step_id, "target": r.target,
                "detail": (
                    f"target {r.target!r} has never accepted a test record "
                    f"(last check: {r.check}); a refusal will show up only in the run's "
                    "delivery verdict"
                ),
            })
    return out

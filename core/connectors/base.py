"""CortexSim connector framework — pull observed signal back from external APIs.

CortexSim's long-standing design rule is "no Cortex API connection — it generates
signal INTO the environment, it does not read alerts OUT." That keeps the engine
standalone, but it also means detection efficacy is scored by a human clicking
"I saw it" in the console. This package adds an **optional, opt-in** read-back
path that closes the measurement loop without weakening the standalone default:

  * A :class:`Connector` reads credentials from the existing encrypted
    integration vault (``core/security/credentials.py``) and pulls observed
    alerts/incidents from an external API (XSIAM, a TI feed, ASM, …).
  * It returns a list of normalized :class:`ObservedAlert` objects.
  * The matcher (``core.connectors.matcher``) correlates those observations to a
    run's seeded ``Result`` rows and sets ``observed_at`` — turning the manual
    "mark observed" step into automated, evidence-backed MTTD.

Everything here is **offline-safe**: with no integration configured (the
default), connectors report ``configured=False`` and pull nothing. The HTTP
client is injected (:class:`HttpFetcher`) so the unit tests never touch the
network — and so a future connector can swap transports without touching the
matcher.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("cortexsim.connectors")


# ---------------------------------------------------------------------------
# Normalized observation model
# ---------------------------------------------------------------------------

@dataclass
class ObservedAlert:
    """One alert/incident observed in an external system, normalized.

    Connectors map their native payload onto this shape so the matcher is
    source-agnostic. Only ``source`` and ``observed_at`` are strictly required;
    the rest improve match precision.
    """

    source: str                                  # connector kind, e.g. "xsiam"
    observed_at: datetime                        # when the alert fired
    external_id: Optional[str] = None            # alert/incident id in the source
    name: Optional[str] = None                   # alert/rule name
    severity: Optional[str] = None               # low|medium|high|critical
    techniques: list[str] = field(default_factory=list)  # MITRE technique ids
    host: Optional[str] = None                   # hostname/endpoint, if known
    detection_id: Optional[str] = None           # source rule/BIOC id, if known
    raw: dict[str, Any] = field(default_factory=dict)    # untouched source record

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "external_id": self.external_id,
            "name": self.name,
            "severity": self.severity,
            "techniques": list(self.techniques),
            "host": self.host,
            "detection_id": self.detection_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], *,
                  default_source: str = "manual") -> Optional["ObservedAlert"]:
        """Build from a loose dict (manual batch ingest / exported JSON).

        Accepts ISO-8601 strings or epoch seconds/ms for ``observed_at``;
        tolerates ``technique``/``techniques`` and ``mitre_technique`` aliases.

        Returns **None** when the timestamp cannot be read. It used to fall back
        to ``utcnow()``, which silently turned a formatting problem into
        ``mttd_seconds = now - executed_at`` — minutes or hours — and
        ``score_run`` then reported `fail` against a ``<= 300s`` threshold. A
        schema change in the customer's export became a claim that their
        detection was too slow. Dropping the row and counting it is the honest
        alternative; :meth:`from_dicts` does the counting.
        """
        ts = d.get("observed_at") or d.get("timestamp") or d.get("time")
        observed_at = coerce_utc(ts)
        if observed_at is None:
            return None
        techs = d.get("techniques")
        if techs is None:
            single = d.get("technique") or d.get("mitre_technique")
            techs = [single] if single else []
        if isinstance(techs, str):
            techs = [techs]
        return cls(
            source=d.get("source") or default_source,
            observed_at=observed_at,
            external_id=_str_or_none(d.get("external_id") or d.get("id") or d.get("alert_id")),
            name=_str_or_none(d.get("name") or d.get("alert_name") or d.get("rule")),
            severity=_str_or_none(d.get("severity")),
            techniques=[str(t) for t in techs if t],
            host=_str_or_none(d.get("host") or d.get("hostname") or d.get("endpoint")),
            detection_id=_str_or_none(d.get("detection_id") or d.get("rule_id")),
            raw=d if isinstance(d, dict) else {},
        )

    @classmethod
    def from_dicts(
        cls, items: list[dict[str, Any]], *, default_source: str = "manual"
    ) -> "tuple[list[ObservedAlert], int]":
        """Build a batch, returning ``(alerts, dropped)``.

        ``dropped`` counts observations whose timestamp could not be read. The
        caller MUST surface it: "3 of 40 alerts had an unreadable timestamp and
        were not counted" is actionable; three phantom threshold FAILs are worse
        than useless, and a silently shorter list is invisible.
        """
        alerts: list[ObservedAlert] = []
        dropped = 0
        for item in items:
            alert = cls.from_dict(item, default_source=default_source)
            if alert is None:
                dropped += 1
                continue
            alerts.append(alert)
        return alerts, dropped


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


#: Naive-UTC is the storage contract: ``Result.executed_at`` / ``observed_at``
#: are written with ``datetime.utcnow()``, so every observation must be
#: converted to the same frame before it is subtracted from one.
_UTC = timezone.utc


def coerce_utc(value: Any) -> Optional[datetime]:
    """THE timestamp coercer. Naive-UTC datetime, or None when untrustworthy.

    There were two of these and they disagreed by up to nine hours. Given
    ``2026-06-10T12:00:30+02:00`` (true UTC 10:00:30):

    * ``connectors/base._coerce_dt`` did ``astimezone(tz=None)`` — convert to the
      **host's local time**. On a DC's laptop in ``America/Los_Angeles`` that is
      03:00:30, seven hours *before* ``executed_at``, so the matcher's
      ``[executed_at, executed_at + window]`` never contained it and coverage
      read 0 % with no error anywhere. The Docker image runs UTC, which is
      exactly why no test caught it and why it only appears mid-POV.
    * ``connectors/xsiam._ms_to_dt`` did ``replace(tzinfo=None)`` — **drop** the
      offset and pretend it was UTC. That is 12:00:30, a 2 h MTTD error.

    Neither answer was 10:00:30. An offset must be CONVERTED, never stripped,
    and never converted to host-local time.

    Returns ``None`` rather than ``utcnow()`` on a value it cannot parse. See
    :meth:`ObservedAlert.from_dict` for why the old fallback manufactured FAILs.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(_UTC).replace(tzinfo=None)
    if isinstance(value, bool):
        # bool is an int subclass; `True` is not epoch-1-second.
        return None
    if isinstance(value, (int, float)):
        # Heuristic: > 1e12 → milliseconds. Cortex alert timestamps are ms.
        seconds = value / 1000.0 if abs(value) > 1e12 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=_UTC).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        s = value.strip()
        # Numeric strings are epochs, not ISO — a tenant export that quotes its
        # millisecond timestamps used to fall through to utcnow().
        try:
            return coerce_utc(int(s))
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo is None else dt.astimezone(_UTC).replace(tzinfo=None)
    return None


# ---------------------------------------------------------------------------
# Injectable HTTP transport (stdlib only, mockable)
# ---------------------------------------------------------------------------

#: A fetcher takes (method, url, headers, body_bytes, timeout) and returns
#: (status_code, response_text). Injected so tests never hit the network.
HttpFetcher = Callable[[str, str, dict, Optional[bytes], float], "tuple[int, str]"]


def default_http_fetcher(
    method: str, url: str, headers: dict, body: Optional[bytes], timeout: float
) -> "tuple[int, str]":
    """Real stdlib HTTP fetcher. Raises ``ConnectorError`` on transport failure."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body_text
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # scheme+host only: the full URL can carry query parameters, and this
        # string is returned to API callers and persisted to
        # IntegrationCredential.last_verified_error, which the console and any
        # POV export both read.
        raise ConnectorError(f"transport error reaching {_host_only(url)}: {e}") from e


def _host_only(url: str) -> str:
    """``https://host`` from a full URL, for error strings that leave the process."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "the tenant"
    if not parts.scheme or not parts.hostname:
        return "the tenant"
    return f"{parts.scheme}://{parts.hostname}"


class ConnectorError(Exception):
    """Raised when a connector cannot complete a pull (transport/auth/parse)."""


# ---------------------------------------------------------------------------
# Connector configuration + result types
# ---------------------------------------------------------------------------

@dataclass
class ConnectorConfig:
    """Resolved configuration for one connector invocation.

    ``config`` is the integration's non-sensitive metadata (tenant fqdn, region,
    auth mode, key id); ``secret`` is the decrypted API key. Both come from the
    credential vault via the API layer — connectors never read the DB directly.
    """

    integration_name: str
    config: dict[str, Any]
    secret: str


@dataclass
class PullResult:
    """Outcome of a connector pull.

    ``code`` is drawn from ``integrations.xsiam.codes`` — the SAME vocabulary
    the typed ``XsiamError`` subclasses use. Two carriers, one vocabulary: this
    connector must stay non-raising and offline-safe (it is the stdlib path that
    has to work in a minimal image), so it cannot raise ``XsiamAuthError``; but a
    DC hitting a 401 should read ``XSIAM_AUTH_ERROR`` regardless of which half of
    the product produced it, and one remediation table should serve both.

    ``dropped`` counts observations discarded before matching — today, alerts
    whose timestamp could not be parsed. It is reported, never silently absorbed.
    """

    ok: bool
    connector: str
    observations: list[ObservedAlert] = field(default_factory=list)
    error: Optional[str] = None
    code: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)
    dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        from integrations.xsiam.codes import remediation_for  # noqa: PLC0415

        return {
            "ok": self.ok,
            "connector": self.connector,
            "count": len(self.observations),
            "observations": [o.to_dict() for o in self.observations],
            "error": self.error,
            "code": self.code,
            "remediation": remediation_for(self.code),
            "dropped": self.dropped,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Connector ABC + registry
# ---------------------------------------------------------------------------

class Connector(ABC):
    """Base class for a read-back connector.

    A concrete connector declares its ``kind`` (matching the integration
    ``kind`` it consumes) and implements :meth:`pull`. It must be offline-safe:
    transport/auth/parse failures return ``PullResult(ok=False, error=...)``
    rather than raising out of :meth:`pull`.
    """

    #: Integration kind this connector consumes (e.g. "xsiam").
    kind: str = ""
    #: Human description for the /api/connectors listing.
    description: str = ""

    def __init__(self, fetcher: Optional[HttpFetcher] = None) -> None:
        self._fetch: HttpFetcher = fetcher or default_http_fetcher

    @abstractmethod
    def pull(
        self,
        cfg: ConnectorConfig,
        *,
        since: datetime,
        until: datetime,
        filters: Optional[dict[str, Any]] = None,
    ) -> PullResult:
        """Pull observations in [since, until]. Never raises — wrap failures in
        ``PullResult(ok=False, error=...)``."""
        raise NotImplementedError


_REGISTRY: dict[str, type[Connector]] = {}


def register_connector(cls: type[Connector]) -> type[Connector]:
    """Class decorator — register a connector by its ``kind``."""
    if not cls.kind:
        raise ValueError(f"{cls.__name__} has no kind")
    _REGISTRY[cls.kind] = cls
    return cls


def get_connector(kind: str, fetcher: Optional[HttpFetcher] = None) -> Optional[Connector]:
    """Instantiate a registered connector by kind, or None if unknown."""
    cls = _REGISTRY.get(kind)
    return cls(fetcher=fetcher) if cls else None


def available_connectors() -> list[dict[str, str]]:
    """List registered connector kinds + descriptions (for /api/connectors)."""
    return [
        {"kind": cls.kind, "description": cls.description}
        for cls in sorted(_REGISTRY.values(), key=lambda c: c.kind)
    ]


def _json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")

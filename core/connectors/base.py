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
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
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
    def from_dict(cls, d: dict[str, Any], *, default_source: str = "manual") -> "ObservedAlert":
        """Build from a loose dict (manual batch ingest / exported JSON).

        Accepts ISO-8601 strings or epoch seconds for ``observed_at``; tolerates
        ``technique``/``techniques`` and ``mitre_technique`` aliases.
        """
        ts = d.get("observed_at") or d.get("timestamp") or d.get("time")
        observed_at = _coerce_dt(ts)
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


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _coerce_dt(value: Any) -> datetime:
    """Coerce an ISO string / epoch (s or ms) / datetime to a naive UTC datetime.

    Result.executed_at / observed_at are stored naive-UTC (datetime.utcnow), so
    we normalize to the same so subtraction yields a correct MTTD."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, (int, float)):
        # Heuristic: > 1e12 → milliseconds.
        seconds = value / 1000.0 if value > 1e12 else float(value)
        return datetime.utcfromtimestamp(seconds)
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is not None:
                dt = dt.astimezone(tz=None).replace(tzinfo=None)
            return dt
        except ValueError:
            pass
    # Fall back to "now" so a malformed timestamp doesn't crash ingest; the
    # matcher's window check will simply not match it to anything older.
    return datetime.utcnow()


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
        raise ConnectorError(f"transport error reaching {url}: {e}") from e


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
    """Outcome of a connector pull."""

    ok: bool
    connector: str
    observations: list[ObservedAlert] = field(default_factory=list)
    error: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "connector": self.connector,
            "count": len(self.observations),
            "observations": [o.to_dict() for o in self.observations],
            "error": self.error,
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

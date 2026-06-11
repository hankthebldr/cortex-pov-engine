"""XSIAM / Cortex XDR results connector.

Pulls alerts from a Cortex XSIAM / XDR tenant via the Public API and normalizes
them to :class:`ObservedAlert` so the matcher can auto-validate a run's expected
detections. This is the connector that closes CortexSim's measurement loop: the
engine puts signal IN (agent/EAL), this reads the resulting alerts OUT.

Auth — the connector supports both Cortex API key modes, selected by
``config["auth_mode"]``:

  * ``"standard"`` (default): the API key is sent verbatim in the ``Authorization``
    header alongside the ``x-xdr-auth-id`` key id.
  * ``"advanced"``: per Cortex's advanced-auth scheme, the request sends a random
    nonce, a millisecond timestamp, and an ``Authorization`` value of
    ``sha256(api_key + nonce + timestamp)`` — the api_key itself never leaves the
    process.

Required integration metadata (``config``): ``fqdn`` (api-<tenant>.xdr.<region>.
paloaltonetworks.com) and ``api_key_id``. The secret is the API key.

Offline-safe: any transport/auth/parse failure returns ``PullResult(ok=False)``.
The HTTP transport is injected, so unit tests drive it with a canned tenant
response and never reach the network.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import string
from datetime import datetime
from typing import Any, Optional

from .base import (
    Connector,
    ConnectorConfig,
    ObservedAlert,
    PullResult,
    register_connector,
)

logger = logging.getLogger("cortexsim.connectors.xsiam")

# Cortex maps its alert severity strings to these; normalize to lowercase.
_SEVERITY_MAP = {
    "SEV_010_INFO": "informational",
    "SEV_020_LOW": "low",
    "SEV_030_MEDIUM": "medium",
    "SEV_040_HIGH": "high",
    "SEV_050_CRITICAL": "critical",
}


@register_connector
class XsiamConnector(Connector):
    """Read alerts from a Cortex XSIAM / XDR tenant Public API."""

    kind = "xsiam"
    description = "Cortex XSIAM / XDR — pull alerts to auto-validate detections (read-only)."

    # Public API path for the multi-event alert query.
    _ALERTS_PATH = "/public_api/v1/alerts/get_alerts_multi_events"

    def pull(
        self,
        cfg: ConnectorConfig,
        *,
        since: datetime,
        until: datetime,
        filters: Optional[dict[str, Any]] = None,
    ) -> PullResult:
        fqdn = (cfg.config or {}).get("fqdn") or (cfg.config or {}).get("tenant_url")
        api_key_id = (cfg.config or {}).get("api_key_id") or (cfg.config or {}).get("auth_id")
        if not fqdn or not api_key_id:
            return PullResult(
                ok=False, connector=self.kind,
                error="integration config missing 'fqdn' and/or 'api_key_id'",
            )

        url = self._base_url(fqdn) + self._ALERTS_PATH
        headers = self._auth_headers(cfg.secret, str(api_key_id),
                                     mode=(cfg.config or {}).get("auth_mode", "standard"))
        headers["Content-Type"] = "application/json"

        payload = self._build_request(since, until, filters or {})
        body = json.dumps(payload).encode("utf-8")

        try:
            status, text = self._fetch("POST", url, headers, body, 30.0)
        except Exception as e:  # noqa: BLE001 — offline-safe boundary
            logger.warning("xsiam pull transport error: %s", e)
            return PullResult(ok=False, connector=self.kind, error=str(e))

        if status != 200:
            return PullResult(
                ok=False, connector=self.kind,
                error=f"tenant returned HTTP {status}",
                detail={"status": status, "body": text[:500]},
            )

        try:
            observations = self._parse_alerts(text)
        except Exception as e:  # noqa: BLE001
            return PullResult(ok=False, connector=self.kind,
                              error=f"failed to parse tenant response: {e}")

        return PullResult(ok=True, connector=self.kind, observations=observations,
                          detail={"queried": url, "window": [since.isoformat(), until.isoformat()]})

    # ── auth ────────────────────────────────────────────────────────────

    @staticmethod
    def _base_url(fqdn: str) -> str:
        fqdn = fqdn.strip().rstrip("/")
        if fqdn.startswith("http://") or fqdn.startswith("https://"):
            return fqdn
        return f"https://{fqdn}"

    def _auth_headers(self, api_key: str, api_key_id: str, *, mode: str) -> dict[str, str]:
        """Build Cortex Public API auth headers for the chosen mode."""
        if mode == "advanced":
            nonce = "".join(
                secrets.choice(string.ascii_letters + string.digits) for _ in range(64)
            )
            timestamp = str(self._now_ms())
            digest = hashlib.sha256(
                (api_key + nonce + timestamp).encode("utf-8")
            ).hexdigest()
            return {
                "x-xdr-auth-id": api_key_id,
                "x-xdr-nonce": nonce,
                "x-xdr-timestamp": timestamp,
                "Authorization": digest,
            }
        # standard
        return {
            "x-xdr-auth-id": api_key_id,
            "Authorization": api_key,
        }

    @staticmethod
    def _now_ms() -> int:
        # datetime.utcnow is allowed; avoid time.time() variability in tests by
        # honoring an override env (used nowhere in prod, handy for determinism).
        override = os.environ.get("CORTEXSIM_XSIAM_TS_MS")
        if override:
            return int(override)
        epoch = datetime(1970, 1, 1)
        return int((datetime.utcnow() - epoch).total_seconds() * 1000)

    # ── request / response shaping ──────────────────────────────────────

    def _build_request(self, since: datetime, until: datetime, filters: dict) -> dict:
        """Cortex get_alerts request: time-window filter + sane page size."""
        request_data: dict[str, Any] = {
            "filters": [
                {
                    "field": "creation_time",
                    "operator": "gte",
                    "value": int(since.replace(microsecond=0).timestamp() * 1000),
                },
                {
                    "field": "creation_time",
                    "operator": "lte",
                    "value": int(until.replace(microsecond=0).timestamp() * 1000),
                },
            ],
            "search_from": 0,
            "search_to": int(filters.get("limit", 100)),
            "sort": {"field": "creation_time", "keyword": "asc"},
        }
        return {"request_data": request_data}

    def _parse_alerts(self, text: str) -> list[ObservedAlert]:
        doc = json.loads(text)
        reply = doc.get("reply", doc)
        alerts = reply.get("alerts", reply.get("data", [])) if isinstance(reply, dict) else []
        out: list[ObservedAlert] = []
        for a in alerts:
            if not isinstance(a, dict):
                continue
            out.append(self._normalize_alert(a))
        return out

    def _normalize_alert(self, a: dict[str, Any]) -> ObservedAlert:
        ts = a.get("detection_timestamp") or a.get("creation_time") or a.get("local_insert_ts")
        techs = self._extract_techniques(a)
        sev_raw = str(a.get("severity", ""))
        severity = _SEVERITY_MAP.get(sev_raw, sev_raw.lower() or None)
        host = (a.get("host_name") or a.get("endpoint_name")
                or (a.get("hosts") or [None])[0] if a.get("hosts") else a.get("host_name"))
        return ObservedAlert(
            source=self.kind,
            observed_at=_ms_to_dt(ts),
            external_id=_s(a.get("alert_id") or a.get("internal_id") or a.get("event_id")),
            name=_s(a.get("name") or a.get("alert_name") or a.get("description")),
            severity=severity,
            techniques=techs,
            host=_s(host),
            detection_id=_s(a.get("detector_id") or a.get("bioc_id") or a.get("rule_id")),
            raw=a,
        )

    @staticmethod
    def _extract_techniques(a: dict[str, Any]) -> list[str]:
        """Pull MITRE technique ids from the several shapes Cortex uses."""
        out: list[str] = []
        for key in ("mitre_technique_ids", "mitre_technique_id_and_name", "mitre_techniques"):
            val = a.get(key)
            if isinstance(val, list):
                for item in val:
                    tid = _technique_id(item)
                    if tid:
                        out.append(tid)
            elif isinstance(val, str):
                tid = _technique_id(val)
                if tid:
                    out.append(tid)
        # de-dupe, preserve order
        seen: set[str] = set()
        deduped = []
        for t in out:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        return deduped


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _ms_to_dt(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e12 else float(value)
        return datetime.utcfromtimestamp(seconds)
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt.replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.utcnow()


_TECH_RE = __import__("re").compile(r"T\d{4}(?:\.\d{3})?")


def _technique_id(item: Any) -> Optional[str]:
    """Extract a `Txxxx[.yyy]` id from a string like 'T1059.001 - Command...'."""
    m = _TECH_RE.search(str(item))
    return m.group(0) if m else None

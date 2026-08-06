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
from datetime import datetime
from typing import Any, Optional

from integrations.xsiam import codes
from integrations.xsiam.auth import advanced_auth_headers, standard_auth_headers
from integrations.xsiam.exceptions import XsiamConfigError
from integrations.xsiam.tenant_url import normalize_tenant_base_url

from .base import (
    Connector,
    ConnectorConfig,
    ConnectorError,
    ObservedAlert,
    PullResult,
    coerce_utc,
    register_connector,
    _host_only,
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
        conf = cfg.config or {}
        # `base_url` first so ONE registration (the xsiam_tenant spelling) can
        # serve both call paths; `fqdn`/`tenant_url` stay for back-compat with
        # every reconcile credential already in the field.
        fqdn = conf.get("base_url") or conf.get("fqdn") or conf.get("tenant_url")
        api_key_id = conf.get("api_key_id") or conf.get("auth_id")
        if not fqdn or not api_key_id:
            return PullResult(
                ok=False, connector=self.kind, code=codes.XSIAM_CONFIG_ERROR,
                error="integration config missing 'fqdn'/'base_url' and/or 'api_key_id'",
            )

        try:
            base = normalize_tenant_base_url(str(fqdn))
        except XsiamConfigError as e:
            # Refusing here is the whole point: without it this connector would
            # POST `Authorization: <raw api key>` in cleartext to whatever host
            # the config named. The tenant client has always validated its URL;
            # this one did not, and both send the same key to the same tenant.
            logger.warning("xsiam pull refused: %s", e)
            return PullResult(ok=False, connector=self.kind,
                              code=codes.XSIAM_CONFIG_ERROR, error=str(e))

        url = base + self._ALERTS_PATH
        mode = str(conf.get("auth_mode", "standard")).lower()
        # One signer, shared with the tenant client. Two byte-identical copies of
        # a signature scheme is two edits when PANW changes it, and one of them
        # gets missed.
        headers = (advanced_auth_headers(cfg.secret, str(api_key_id))
                   if mode == "advanced"
                   else standard_auth_headers(cfg.secret, str(api_key_id)))

        payload = self._build_request(since, until, filters or {})
        body = json.dumps(payload).encode("utf-8")

        try:
            status, text = self._fetch("POST", url, headers, body, 30.0)
        except Exception as e:  # noqa: BLE001 — offline-safe boundary
            logger.warning("xsiam pull transport error: %s", e)
            return PullResult(ok=False, connector=self.kind,
                              code=codes.XSIAM_TRANSPORT_ERROR, error=str(e))

        if status != 200:
            # The tenant's response body is attacker-influenceable and unbounded,
            # and this error string is persisted to
            # IntegrationCredential.last_verified_error, which /api/credentials
            # and the console both return. Keep the body in the server log; ship
            # only a status and a digest the operator can correlate with it.
            logger.warning("xsiam pull HTTP %s from %s; body=%s",
                           status, _host_only(url), text[:500])
            return PullResult(
                ok=False, connector=self.kind, code=codes.code_for_status(status),
                error=f"tenant returned HTTP {status}",
                detail={"status": status,
                        "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]},
            )

        try:
            observations, dropped = self._parse_alerts(text)
        except Exception as e:  # noqa: BLE001
            return PullResult(ok=False, connector=self.kind,
                              code=codes.XSIAM_PARSE_ERROR,
                              error=f"failed to parse tenant response: {e}")

        detail: dict[str, Any] = {
            "queried_host": _host_only(url),
            "window": [since.isoformat(), until.isoformat()],
        }
        if dropped:
            # Visible, not absorbed: a dropped alert is one the DC's coverage
            # number will not include, and they must be told why.
            detail["unparseable_timestamps"] = dropped
        return PullResult(ok=True, connector=self.kind, observations=observations,
                          dropped=dropped, detail=detail)

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

    def _parse_alerts(self, text: str) -> "tuple[list[ObservedAlert], int]":
        """Return ``(alerts, dropped)``; ``dropped`` counts unreadable timestamps."""
        doc = json.loads(text)
        # XSIAM signals application errors INSIDE a 200 — {"reply": {"err_code":
        # .., "err_msg": ..}} — so a permissions problem, a bad tenant or an
        # expired key arrives looking exactly like a successful query that
        # observed nothing. Reading that as zero alerts turns an auth failure
        # into "the customer's stack detected nothing", which is the manufactured
        # false negative in its most damaging form: it is the number that goes in
        # the report. Raise instead, so the caller degrades to pending.
        if isinstance(doc, dict):
            err = doc.get("reply") if isinstance(doc.get("reply"), dict) else doc
            if isinstance(err, dict) and (err.get("err_code") or err.get("err_msg")):
                raise ConnectorError(
                    f"XSIAM_APPLICATION_ERROR: tenant returned an error inside a "
                    f"200 response (err_code={err.get('err_code')}, "
                    f"err_msg={str(err.get('err_msg'))[:200]!r}). This is NOT an "
                    f"absence of alerts — no observation was made, so the run "
                    f"stays pending rather than being scored."
                )
        reply = doc.get("reply", doc)
        if isinstance(reply, dict):
            alerts = reply.get("alerts", reply.get("data", []))
        else:
            alerts = []
        if not isinstance(alerts, list):
            # A dict where a list belongs means the envelope is not what we
            # think it is. Guessing is how `len({"data": []}) == 1` happened.
            raise ConnectorError(
                f"XSIAM_ENVELOPE_UNRECOGNISED: expected a list of alerts, got "
                f"{type(alerts).__name__}. Refusing to infer an alert count from "
                f"an envelope this connector does not recognise."
            )
        out: list[ObservedAlert] = []
        dropped = 0
        for a in alerts:
            if not isinstance(a, dict):
                continue
            alert = self._normalize_alert(a)
            if alert is None:
                dropped += 1
                continue
            out.append(alert)
        return out, dropped

    def _normalize_alert(self, a: dict[str, Any]) -> Optional[ObservedAlert]:
        """One alert → :class:`ObservedAlert`, or None if its time is unreadable.

        A ``None`` here used to be ``utcnow()``, which produced an MTTD of
        "however long ago the run executed" and failed the customer's 300 s
        threshold on the strength of a timestamp format CortexSim could not read.
        """
        ts = a.get("detection_timestamp") or a.get("creation_time") or a.get("local_insert_ts")
        observed_at = coerce_utc(ts)
        if observed_at is None:
            logger.warning("xsiam alert %s has an unreadable timestamp %r — dropped, "
                           "not dated to now", a.get("alert_id", "?"), ts)
            return None
        techs = self._extract_techniques(a)
        sev_raw = str(a.get("severity", ""))
        severity = _SEVERITY_MAP.get(sev_raw, sev_raw.lower() or None)
        host = (a.get("host_name") or a.get("endpoint_name")
                or (a.get("hosts") or [None])[0] if a.get("hosts") else a.get("host_name"))
        return ObservedAlert(
            source=self.kind,
            observed_at=observed_at,
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


# `_ms_to_dt` lived here and disagreed with connectors.base._coerce_dt by up to
# nine hours on the same input. There is now exactly one coercer
# (``connectors.base.coerce_utc``) and this module imports it.

_TECH_RE = __import__("re").compile(r"T\d{4}(?:\.\d{3})?")


def _technique_id(item: Any) -> Optional[str]:
    """Extract a `Txxxx[.yyy]` id from a string like 'T1059.001 - Command...'."""
    m = _TECH_RE.search(str(item))
    return m.group(0) if m else None

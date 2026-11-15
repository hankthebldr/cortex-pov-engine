"""Grouping-key analysis: do a scenario's detections share a native XSIAM
case-grouping key? (spec 2026-11-11-socfw-coexistence §5.1-5.3)

XSIAM groups alerts into a case on the canonical entity Alert Fields an alert
populates (host, user, IP, ...). Low-severity alerts never form or join a
case. This module works out, per expected detection, which key *classes* it
groups on and its severity, then reports per scenario:

* ``G-01`` WARN  two or more Medium+ detections share no key class.
* ``G-02`` WARN  a step inside a stitched incident carries only Low detections.
* ``G-03`` ERROR a correlation rule maps the declared ``stitching_key`` to a
  custom (non-native) destination, which XSIAM does not group on.
* ``G-04`` INFO  ``GROUPING_KEY_UNKNOWN``: the key or severity of a detection
  cannot be derived from the corpus. Never skipped silently, never assumed
  Medium+.
* ``G-05`` INFO  ``BRIDGEABLE_IP_HOST``: the Medium+ detections share a key
  only once an IP key is bridged to its host.

The analysis is class-level, never value-level, so it is advisory: two
host-keyed alerts on different hosts still read as sharing ``host``. Only
``G-03`` fails the corpus gate.

``ENTITY_ALERT_FIELDS`` and the ``alert_fields`` shape reader are adapted from
``tools/correlation_rule_grouping_check.py`` in Palo-Cortex/secops-framework
(commit d222ba87), used under its licence:

    MIT License

    Copyright (c) 2025 Palo Alto Networks Cortex Team

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from engine.ttp_catalog import TtpCatalog, _slug

# ---------------------------------------------------------------------------
# Native grouping fields. Every name in the framework's ENTITY_ALERT_FIELDS,
# mapped to the key class it groups on. The framework lists the two causality
# fields separately; a causality chain lives on one endpoint, so they map to
# "host" (SDD Ruling 40). No native field is a file hash, so "file" is only
# ever reached implicitly (Prevention alerts).
# ---------------------------------------------------------------------------

NATIVE_GROUPING_FIELDS: dict[str, str] = {
    # User
    "actor_effective_username": "user", "actor_primary_username": "user",
    "userid": "user", "user_name": "user", "username": "user",
    "user_principal": "user", "usersid": "user", "User name": "user",
    # Host
    "agent_hostname": "host", "agent_id": "host", "hostname": "host",
    "host_name": "host", "agent_device_domain": "host", "domain": "host",
    "Host Name": "host", "Domain": "host",
    # IP
    "action_local_ip": "ip", "action_remote_ip": "ip", "local_ip": "ip",
    "remote_ip": "ip", "remote_ips": "ip", "prenatsourceip": "ip",
    "postnatdestinationip": "ip", "Local IP": "ip", "Remote IP": "ip",
    "Host IP": "ip", "Remote Host": "ip",
    # Causality (Ruling 40)
    "causality_actor_causality_id": "host", "aggregate_id": "host",
}

G01_NO_SHARED_KEY = "G-01"
G02_STITCH_LOW_ONLY = "G-02"
G03_STITCH_KEY_CUSTOM = "G-03"
G04_KEY_UNKNOWN = "G-04"
G05_BRIDGEABLE = "G-05"

LEVELS: dict[str, str] = {
    G01_NO_SHARED_KEY: "WARN",
    G02_STITCH_LOW_ONLY: "WARN",
    G03_STITCH_KEY_CUSTOM: "ERROR",
    G04_KEY_UNKNOWN: "INFO",
    G05_BRIDGEABLE: "INFO",
}

SEVERITIES = ("informational", "low", "medium", "high", "critical")
MEDIUM_PLUS = frozenset({"medium", "high", "critical"})
BELOW_MEDIUM = frozenset({"informational", "low"})

# Implicit key classes by detection type. None: derived from the card's
# correlation rule ``alert_fields``, else unknown.
_IMPLICIT_KEYS: dict[str, Optional[frozenset[str]]] = {
    "BIOC": frozenset({"host", "user"}),
    "ABIOC": frozenset({"host", "user"}),
    "Analytics": frozenset({"host", "user"}),
    "Prevention": frozenset({"host", "user", "file"}),
    "XQL": None,
    "IOC": None,
    "Correlation": None,
}
# Types whose severity is read from the resolved card object. A custom BIOC
# and a correlation rule are deployed from the card, so its severity is the
# alert's. ABIOC / Analytics / Prevention severity is set by the platform.
_CARD_SEVERITY_TYPES = frozenset({"BIOC", "XQL", "IOC", "Correlation"})


@dataclass(frozen=True)
class DetectionKeys:
    step_id: str
    detection_id: str
    detection_type: str
    severity: Optional[str]
    key_classes: frozenset[str]
    keys_known: bool


@dataclass(frozen=True)
class Finding:
    code: str
    level: str
    scenario_id: str
    step_id: Optional[str]
    detail: str


def _finding(code: str, scenario_id: str, step_id: Optional[str], detail: str) -> Finding:
    return Finding(code=code, level=LEVELS[code], scenario_id=scenario_id,
                   step_id=step_id, detail=detail)


def _alert_fields(rule: dict[str, Any]) -> list[tuple[str, Any]]:
    """``[(destination, source), ...]`` from a rule's ``alert_fields``.

    The tenant export and pack YAML use ``{<destination>: <source>}``; the
    destination is the Alert Field grouping happens on, and a null source is
    declared but unmapped. The older list shape with explicit destination /
    source keys is read too. Any other shape raises.
    """
    af = rule.get("alert_fields")
    if af is None:
        return []
    if isinstance(af, dict):
        return [(k, v) for k, v in af.items() if v not in (None, "")]
    if isinstance(af, list):
        out: list[tuple[str, Any]] = []
        for item in af:
            if not isinstance(item, dict):
                raise ValueError(f"alert_fields entry is not an object: {item!r}")
            dest = (item.get("alert_field") or item.get("xdm_field") or item.get("name")
                    or item.get("dest") or item.get("destination"))
            src = (item.get("source_field") or item.get("value") or item.get("source")
                   or item.get("field"))
            if not dest:
                raise ValueError(f"alert_fields entry names no destination: {item!r}")
            out.append((dest, src))
        return out
    raise ValueError(f"alert_fields is neither an object nor a list: {type(af).__name__}")


def _correlation_rule(catalog: TtpCatalog, ttp_ref: Optional[str],
                      detection_id: Optional[str]) -> Optional[dict[str, Any]]:
    """The raw correlation rule a detection id resolves to, the same way the
    catalog derives correlation ids (``rule_id``, ``detection_id``, slug)."""
    if not ttp_ref or not detection_id:
        return None
    raw = catalog.raw(ttp_ref) or {}
    rules = (raw.get("detections") or {}).get("correlation_rules") or []
    for idx, r in enumerate(rules):
        if not isinstance(r, dict):
            continue
        name = r.get("name") or f"correlation-{idx + 1}"
        rid = r.get("rule_id") or r.get("detection_id") or _slug(name, "correlation")
        if rid == detection_id:
            return r
    return None


def _severity(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or value.strip().lower() not in SEVERITIES:
        raise ValueError(f"unrecognised severity {value!r} (expected one of {SEVERITIES})")
    return value.strip().lower()


def detection_keys(step_id: str, det: dict, catalog: TtpCatalog) -> DetectionKeys:
    """Key classes and severity of one expected detection."""
    dtype = det.get("type")
    if dtype not in _IMPLICIT_KEYS:
        raise ValueError(f"step {step_id}: unrecognised detection type {dtype!r}")
    ttp_ref, detection_id = det.get("ttp_ref"), det.get("detection_id")
    card = catalog.find(ttp_ref, detection_id)
    severity = _severity(card.severity) if card is not None and dtype in _CARD_SEVERITY_TYPES else None

    implicit = _IMPLICIT_KEYS[dtype]
    if implicit is not None:
        classes, known = implicit, True
    else:
        rule = _correlation_rule(catalog, ttp_ref, detection_id)
        af = _alert_fields(rule) if rule is not None else []
        if af:
            classes = frozenset(NATIVE_GROUPING_FIELDS[d] for d, _ in af
                                if d in NATIVE_GROUPING_FIELDS)
            known = True
        else:
            classes, known = frozenset(), False
    return DetectionKeys(step_id=step_id, detection_id=str(detection_id or ""),
                         detection_type=dtype, severity=severity,
                         key_classes=classes, keys_known=known)


def _bridged(classes: frozenset[str]) -> frozenset[str]:
    return frozenset("host" if c == "ip" else c for c in classes)


def _describe(k: DetectionKeys) -> str:
    keys = ",".join(sorted(k.key_classes)) or "none"
    return f"{k.step_id}:{k.detection_id or k.detection_type} [{k.severity}; {keys}]"


def analyze_scenario(scenario: dict, catalog: TtpCatalog) -> list[Finding]:
    """Grouping findings for one scenario (raw YAML dict)."""
    sid = str(scenario.get("scenario_id") or scenario.get("id") or "")
    planes = set(scenario.get("required_planes_in_incident") or [])
    stitching_key = scenario.get("stitching_key")
    stitched = bool(planes) or bool(stitching_key)

    findings: list[Finding] = []
    compared: list[DetectionKeys] = []
    for idx, step in enumerate(scenario.get("steps") or []):
        step_id = str(step.get("id") or f"step-{idx + 1:02d}")
        dets = step.get("expected_detections") or []
        step_keys: list[DetectionKeys] = []
        for det in dets:
            k = detection_keys(step_id, det, catalog)
            step_keys.append(k)
            if not k.keys_known or k.severity is None:
                missing = [w for w, gone in (("key", not k.keys_known),
                                             ("severity", k.severity is None)) if gone]
                findings.append(_finding(
                    G04_KEY_UNKNOWN, sid, step_id,
                    f"GROUPING_KEY_UNKNOWN: {k.detection_type} "
                    f"{k.detection_id or '(no detection_id)'} has no derivable "
                    f"{' or '.join(missing)}"))
            elif k.severity in MEDIUM_PLUS:
                compared.append(k)
            if stitching_key and k.detection_type in ("XQL", "IOC", "Correlation"):
                rule = _correlation_rule(catalog, det.get("ttp_ref"), det.get("detection_id"))
                for dest, src in (_alert_fields(rule) if rule is not None else []):
                    if src == stitching_key and dest not in NATIVE_GROUPING_FIELDS:
                        findings.append(_finding(
                            G03_STITCH_KEY_CUSTOM, sid, step_id,
                            f"correlation rule {k.detection_id} maps stitching_key "
                            f"'{stitching_key}' to custom destination '{dest}', which "
                            "XSIAM does not group on"))

        in_incident = stitched and (not planes or any(d.get("plane") in planes for d in dets))
        if (in_incident and step_keys
                and all(k.severity in BELOW_MEDIUM for k in step_keys)):
            findings.append(_finding(
                G02_STITCH_LOW_ONLY, sid, step_id,
                "every detection on this stitched step is below Medium; a Low alert "
                "never forms or joins a case"))

    if len(compared) >= 2:
        shared = frozenset.intersection(*(k.key_classes for k in compared))
        if not shared:
            listed = "; ".join(_describe(k) for k in compared)
            if frozenset.intersection(*(_bridged(k.key_classes) for k in compared)):
                findings.append(_finding(
                    G05_BRIDGEABLE, sid, None,
                    f"BRIDGEABLE_IP_HOST: the Medium+ detections share a key only once an "
                    f"IP key is bridged to its host: {listed}"))
            else:
                findings.append(_finding(
                    G01_NO_SHARED_KEY, sid, None,
                    f"the Medium+ detections share no native grouping key: {listed}"))
    return findings

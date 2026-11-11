"""
Tenant content — what a scenario needs to EXIST IN THE TENANT, derived from the
TTP cards its steps cite.

A scenario's steps are what CortexSim runs. Its expected detections are a mix of
three different things, and until this module they all rendered as one row of
type tags:

    run       XQL                          a query the DC runs to confirm telemetry landed
    deploy    BIOC · IOC · Correlation     must exist in the tenant; this raises the alert
    built-in  ABIOC · Analytics · Prevention   ships with Cortex; nothing to import

This module resolves a scenario against the cards and says, for every piece of
``deploy`` content, what the card actually holds. For correlation rules that is
two axes:

    content   intent-only | deployable | unreadable      (what the CARD holds)
    binding   declared    | card-only                    (what the SCENARIO asks for)

READ-ONLY, AND IT INVENTS NOTHING
---------------------------------
* It never contacts a tenant. There is no network call in this module and no
  path from it to ``OP-CORRELATIONS-INSERT``; ``write_path`` is always False and
  ``tenant_verified`` is always 0.
* A stub is exported as ``deploy: null`` with its status. No default severity,
  no synthesised description.
* An unrecognised rule shape RAISES (``classify_rule``). ``derive`` turns that
  into an ``unreadable`` rule carrying the reason; it is never read as
  intent-only and never dropped.
* No catalog is ``catalog_loaded: false`` with null counts, never "0 rules".

``deployable`` means structurally complete. It does not mean a tenant accepted
the rule. The ``deploy`` field names are CANDIDATES until phase 3 pins them to a
rule exported from a real tenant; the card schema forbids the key today, and
``tests/engine/test_tenant_content.py`` pins the corpus count at zero.

TWO IMPLEMENTATIONS, ONE FIXTURE
--------------------------------
The Composer derives the same facts in the browser so the canvas follows each
edit without a round trip (``ui/src/components/console/composerContent.js``).
Both sides assert ``spec/tenant_content_cases.json``. Change the logic here and
that fixture, and the JS side, change with it.

Design: ``docs/superpowers/specs/2026-11-10-composer-correlation-story-design.md``.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from engine.ttp_catalog import _slug

__all__ = [
    "TenantContentError",
    "build_manifest",
    "canonical_type",
    "classify_rule",
    "derive",
    "ownership_of",
    "parse_logic",
]


class TenantContentError(ValueError):
    """A shape this module does not recognise. Raised rather than read as empty."""


# ---------------------------------------------------------------------------
# Detection-type vocabulary (closed)
# ---------------------------------------------------------------------------

# UPPERCASE type -> (canonical spelling, ownership). The seven ``detection_type``
# values and nothing else.
_TYPES: dict[str, tuple[str, str]] = {
    "XQL": ("XQL", "run"),
    "BIOC": ("BIOC", "deploy"),
    "IOC": ("IOC", "deploy"),
    "CORRELATION": ("Correlation", "deploy"),
    "ABIOC": ("ABIOC", "built-in"),
    "ANALYTICS": ("Analytics", "built-in"),
    "PREVENTION": ("Prevention", "built-in"),
}

# The four characters both implementations strip. Not ``str.strip()``: its
# whitespace set differs from JavaScript's ``trim`` at the edges.
_WS = " \t\r\n"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip(_WS) != ""


def _type_entry(type_: Any) -> tuple[str, str]:
    entry = _TYPES.get(type_.strip(_WS).upper()) if isinstance(type_, str) else None
    if entry is None:
        raise TenantContentError(f"unknown detection type {type_!r}")
    return entry


def canonical_type(type_: Any) -> str:
    """The canonical spelling of a detection type (``xql`` -> ``XQL``). Raises on unknown."""
    return _type_entry(type_)[0]


def ownership_of(type_: Any) -> str:
    """Who owns the work: ``run`` | ``deploy`` | ``built-in``. Raises on unknown."""
    return _type_entry(type_)[1]


# ---------------------------------------------------------------------------
# Content status
# ---------------------------------------------------------------------------

_RULE_KEYS = frozenset({"name", "rule_id", "logic", "deploy"})
_DEPLOY_KEYS = frozenset({
    "xql", "execution", "schedule", "search_window", "severity", "alert",
    "suppression", "mitre", "provenance",
})
_ANCHOR = re.compile(r"(^|[^A-Za-z0-9_])(dataset|preset)[ \t]*=")


def classify_rule(rule: Any) -> str:
    """A rule's content status: ``intent-only`` or ``deployable``.

    Raises :class:`TenantContentError` on any shape it does not recognise: an
    unknown key, a non-string ``logic``, or a ``deploy`` block that is not
    complete. Half a block never downgrades to intent-only.
    """
    if not isinstance(rule, dict):
        raise TenantContentError("rule is not an object")
    for key in rule:
        if key not in _RULE_KEYS:
            raise TenantContentError(f"unknown key '{key}' on the rule")
    if not _is_text(rule.get("name")):
        raise TenantContentError("rule has no name")
    if rule.get("rule_id") is not None and not isinstance(rule["rule_id"], str):
        raise TenantContentError("rule_id is not a string")
    if rule.get("logic") is not None and not isinstance(rule["logic"], str):
        raise TenantContentError("logic is not a string")
    if "deploy" not in rule:
        return "intent-only"

    d = rule["deploy"]
    if not isinstance(d, dict):
        raise TenantContentError("deploy is not an object")
    for key in d:
        if key not in _DEPLOY_KEYS:
            raise TenantContentError(f"unknown key '{key}' in deploy")
    if not _is_text(d.get("xql")):
        raise TenantContentError("deploy has no xql")
    if not _ANCHOR.search(d["xql"]):
        raise TenantContentError("deploy xql has no dataset or preset anchor")
    if d.get("execution") not in ("scheduled", "realtime"):
        raise TenantContentError("deploy execution is not scheduled or realtime")
    if d["execution"] == "scheduled" and not (
        _is_text(d.get("schedule")) and _is_text(d.get("search_window"))
    ):
        raise TenantContentError("a scheduled rule needs a schedule and a search_window")
    if not _is_text(d.get("severity")):
        raise TenantContentError("deploy has no severity")
    alert = d.get("alert")
    if not isinstance(alert, dict) or not _is_text(alert.get("name")):
        raise TenantContentError("deploy has no alert name")
    return "deployable"


# ---------------------------------------------------------------------------
# The logic line
# ---------------------------------------------------------------------------

# An ASCII word followed by "(", not glued to a longer identifier. The word is
# then looked up in _TERM_KINDS: no IGNORECASE, so Python and the browser fold
# case identically.
_TERM = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]+)[ \t]*\(")
_TERM_KINDS = frozenset({"XQL", "BIOC", "ABIOC", "IOC", "ANALYTICS", "CORRELATION", "PREVENTION"})


def parse_logic(logic: Any) -> dict[str, Any]:
    """Read a rule's ``logic`` line as a composition of named detections.

    ``XQL(name) OR BIOC(name)`` yields its ``KIND(name)`` terms; a name may carry
    its own balanced parentheses. A line with no term is ``prose``; a missing or
    blank line is ``none``. Raises on a term that never closes: half a term is
    not a shorter term.
    """
    if logic is None:
        return {"kind": "none", "terms": []}
    if not isinstance(logic, str):
        raise TenantContentError("logic is not a string")
    if logic.strip(_WS) == "":
        return {"kind": "none", "terms": []}

    terms: list[dict[str, str]] = []
    pos = 0
    while True:
        m = _TERM.search(logic, pos)
        if m is None:
            break
        kind = m.group(1).upper()
        if kind not in _TERM_KINDS:
            # Some other word before a parenthesis. Keep scanning INSIDE it: a
            # term may sit in there (``count(XQL(a))``).
            pos = m.end()
            continue
        depth = 1
        i = start = m.end()
        while i < len(logic) and depth > 0:
            ch = logic[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        if depth != 0:
            raise TenantContentError(f"{kind}( has no closing parenthesis")
        name = logic[start:i - 1].strip(_WS)
        if len(name) >= 2 and name[0] in "\"'" and name[-1] == name[0]:
            name = name[1:-1].strip(_WS)
        terms.append({"kind": kind.lower(), "name": name})
        # Resume AFTER the term: a KIND( inside a name is part of that name.
        pos = i
    return {"kind": "composition" if terms else "prose", "terms": terms}


# ---------------------------------------------------------------------------
# Card index
# ---------------------------------------------------------------------------

# Card list -> kind, in the order a term's name is looked up after its own kind.
_KIND_LISTS: tuple[tuple[str, str], ...] = (
    ("xql", "xql_queries"),
    ("bioc", "biocs"),
    ("abioc", "abiocs"),
    ("ioc", "iocs"),
    ("analytics", "analytics_modules"),
    ("correlation", "correlation_rules"),
    ("prevention", "prevention_modules"),
    ("modeling", "modeling_rules"),
)
_KINDS = tuple(kind for kind, _ in _KIND_LISTS)


def _rule_identity(rule: Any, index: int) -> tuple[str, str]:
    """A rule's (id, display name) by the catalog's own fallbacks. Never raises."""
    obj = rule if isinstance(rule, dict) else {}
    name = obj["name"] if _is_text(obj.get("name")) else f"correlation-{index + 1}"
    rule_id = obj.get("rule_id") if isinstance(obj.get("rule_id"), str) and obj.get("rule_id") else None
    det_id = obj.get("detection_id") if isinstance(obj.get("detection_id"), str) and obj.get("detection_id") else None
    return (rule_id or det_id or _slug(name, "correlation")), name


class _CardIndex:
    """Every detection object on one raw card, by id and by (kind, name).

    Ids are synthesised exactly as ``ttp_catalog`` does. Analytics modules are
    bare strings or ``{module}`` objects; they index by name so a term can find
    them.
    """

    def __init__(self, card: Any) -> None:
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_name: dict[str, dict[str, str]] = {kind: {} for kind in _KINDS}
        det = card.get("detections") if isinstance(card, dict) else None
        det = det if isinstance(det, dict) else {}
        for kind, key in _KIND_LISTS:
            items = det.get(key)
            for i, o in enumerate(items if isinstance(items, list) else []):
                if kind == "analytics":
                    name = o if isinstance(o, str) else (
                        (o.get("module") or o.get("name")) if isinstance(o, dict) else None
                    )
                    if _is_text(name):
                        self._put(kind, _slug(name, "analytics"), name, o)
                    continue
                if kind == "correlation":
                    rule_id, name = _rule_identity(o, i)
                    self._put(kind, rule_id, name, o)
                    continue
                if not isinstance(o, dict):
                    continue
                if kind == "ioc":
                    ioc_type = o.get("ioc_type") or "ioc"
                    value = o.get("value") or f"ioc-{i + 1}"
                    self._put(
                        kind, o.get("detection_id") or _slug(f"{ioc_type}-{value}", "ioc"),
                        f"{ioc_type}: {value}", o,
                    )
                    continue
                name = o.get("name") or f"{kind}-{i + 1}"
                self._put(kind, o.get("detection_id") or _slug(name, kind), name, o)

    def _put(self, kind: str, det_id: str, name: str, obj: Any) -> None:
        self.by_id.setdefault(det_id, {"kind": kind, "name": name, "content": obj})
        self.by_name[kind].setdefault(name, det_id)


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _detections(step: Any) -> list[dict[str, Any]]:
    dets = step.get("expected_detections") if isinstance(step, dict) else None
    return [d for d in dets if isinstance(d, dict)] if isinstance(dets, list) else []


def _is_correlation(type_: Any) -> bool:
    return isinstance(type_, str) and type_.strip(_WS).upper() == "CORRELATION"


def derive(steps: Any, cards: Any) -> dict[str, Any]:
    """Everything a chain needs in the tenant, from the cards its steps cite.

    :param steps: scenario steps (``expected_detections[{type, ttp_ref, detection_id}]``)
    :param cards: ``{ttp_id: raw_card}`` — only the cards that are available
    """
    if not isinstance(steps, list):
        raise TenantContentError("steps is not a list")
    step_list = [s for s in steps if isinstance(s, dict)]
    card_map = cards if isinstance(cards, dict) else {}

    cited: list[str] = []
    for s in step_list:
        for d in _detections(s):
            ref = d.get("ttp_ref")
            if ref and ref not in cited:
                cited.append(ref)
    loaded = [cid for cid in cited if isinstance(card_map.get(cid), dict)]
    missing = [cid for cid in cited if not isinstance(card_map.get(cid), dict)]
    index = {cid: _CardIndex(card_map[cid]) for cid in loaded}

    def declaring(ttp_ref: str, detection_id: str) -> list[str]:
        """Step ids, in chain order, that declare (ttp_ref, detection_id)."""
        return [
            s.get("id") for s in step_list
            if any(d.get("ttp_ref") == ttp_ref and d.get("detection_id") == detection_id
                   for d in _detections(s))
        ]

    rules: list[dict[str, Any]] = []
    for ttp_ref in loaded:
        idx = index[ttp_ref]
        raw = (card_map[ttp_ref].get("detections") or {}).get("correlation_rules")
        citing = [
            s.get("id") for s in step_list
            if any(d.get("ttp_ref") == ttp_ref for d in _detections(s))
        ]
        for i, rule in enumerate(raw if isinstance(raw, list) else []):
            rule_id, name = _rule_identity(rule, i)

            content_error: Optional[str] = None
            try:
                content_status = classify_rule(rule)
            except TenantContentError as exc:
                content_status = "unreadable"
                content_error = str(exc)

            # An unreadable rule's logic is not trusted either: no inputs are claimed.
            logic_kind: Optional[str] = None
            logic_error: Optional[str] = None
            inputs: list[dict[str, Any]] = []
            if content_status != "unreadable":
                try:
                    parsed = parse_logic(rule.get("logic"))
                except TenantContentError as exc:
                    logic_kind = "unparsed"
                    logic_error = str(exc)
                else:
                    logic_kind = parsed["kind"]
                    for term in parsed["terms"]:
                        resolved_kind: Optional[str] = None
                        detection_id: Optional[str] = None
                        order = [term["kind"]] + [k for k in _KINDS if k != term["kind"]]
                        for kind in order:
                            hit = idx.by_name.get(kind, {}).get(term["name"])
                            if hit is not None:
                                resolved_kind, detection_id = kind, hit
                                break
                        on = declaring(ttp_ref, detection_id) if detection_id else []
                        inputs.append({
                            "kind": term["kind"],
                            "name": term["name"],
                            "state": "unknown" if detection_id is None else ("step" if on else "card-only"),
                            "resolved_kind": resolved_kind,
                            "detection_id": detection_id,
                            "steps": on,
                        })

            declared_by = declaring(ttp_ref, rule_id)
            feeders: list[dict[str, Any]] = []
            for s in step_list:
                n = sum(1 for inp in inputs if s.get("id") in inp["steps"])
                declares = s.get("id") in declared_by
                if n > 0 or declares:
                    feeders.append({"step": s.get("id"), "inputs": n, "declares": declares})

            rules.append({
                "rule_id": rule_id,
                "ttp_ref": ttp_ref,
                "name": name,
                "logic": rule.get("logic") if isinstance(rule, dict) and isinstance(rule.get("logic"), str) else None,
                "logic_kind": logic_kind,
                "logic_error": logic_error,
                "content_status": content_status,
                "content_error": content_error,
                "binding": "declared" if declared_by else "card-only",
                "declared_by": declared_by,
                "inputs": inputs,
                "feeders": feeders,
                "anchor_step": feeders[-1]["step"] if feeders else (citing[-1] if citing else None),
                "edge_kind": "feeds" if feeders else "card",
                # Exported verbatim when the card holds one, never synthesised.
                "deploy": rule.get("deploy") if content_status == "deployable" else None,
            })

    # A Correlation expectation with no rule behind it. Judged only against a
    # card that is available: an absent card says nothing either way.
    unbound: list[dict[str, Any]] = []
    for s in step_list:
        for k, d in enumerate(_detections(s)):
            if not _is_correlation(d.get("type")):
                continue
            base = {
                "step": s.get("id"), "index": k,
                "ttp_ref": d.get("ttp_ref") or None, "detection_id": d.get("detection_id") or None,
            }
            if not d.get("ttp_ref") or not d.get("detection_id"):
                unbound.append({**base, "reason": "no-reference"})
                continue
            idx = index.get(d["ttp_ref"])
            if idx is None:
                continue
            hit = idx.by_id.get(d["detection_id"])
            if hit is None:
                unbound.append({**base, "reason": "no-rule"})
            elif hit["kind"] != "correlation":
                unbound.append({**base, "reason": "not-a-rule"})

    def count(status_key: str, value: str) -> int:
        return sum(1 for r in rules if r[status_key] == value)

    return {
        "cited_cards": cited,
        "cards_missing": missing,
        "no_card_binding": not cited,
        "rules": rules,
        "unbound": unbound,
        "summary": {
            "total": len(rules),
            "deployable": count("content_status", "deployable"),
            "intent_only": count("content_status", "intent-only"),
            "unreadable": count("content_status", "unreadable"),
            "declared": count("binding", "declared"),
            "card_only": count("binding", "card-only"),
            "unbound": len(unbound),
        },
    }


# ---------------------------------------------------------------------------
# Manifest (the read-only export)
# ---------------------------------------------------------------------------

_SECTION = {"XQL": "queries", "BIOC": "biocs", "IOC": "iocs"}
_NULL_CORRELATION = {
    "total": None, "deployable": None, "intent_only": None, "unreadable": None,
    "declared": None, "card_only": None, "unbound": None,
}


def build_manifest(scenario: Any, cards: Any, *, catalog_loaded: bool) -> dict[str, Any]:
    """The tenant-content manifest for one scenario or draft.

    :param scenario: a scenario document (``scenario_id``, ``name``, ``steps``)
    :param cards: ``{ttp_id: raw_card}``, normally ``TtpCatalog.all_raw()``
    :param catalog_loaded: False when the card corpus is absent from this deploy.
        The manifest is then DEGRADED: null counts and a ``CATALOG_NOT_LOADED``
        note, never an empty "nothing needed".
    """
    if not isinstance(scenario, dict):
        raise TenantContentError("scenario is not an object")
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        raise TenantContentError("scenario steps is not a list")

    manifest: dict[str, Any] = {
        "scenario_id": scenario.get("scenario_id"),
        "name": scenario.get("name"),
        "catalog_loaded": bool(catalog_loaded),
        # Constants, stated in the payload so no reader has to infer them.
        "tenant_verified": 0,
        "write_path": False,
        "summary": {"correlation": dict(_NULL_CORRELATION)},
        "rules": [],
        "unbound": [],
        "biocs": [],
        "iocs": [],
        "queries": [],
        "built_in": [],
        "notes": [],
    }
    notes: list[dict[str, str]] = manifest["notes"]

    if not catalog_loaded:
        notes.append({
            "code": "CATALOG_NOT_LOADED",
            "detail": "The TTP card corpus is not loaded on this SimCore, so no tenant content "
                      "could be derived. This is not 'nothing needed'.",
        })
        return manifest

    content = derive(steps, cards)
    manifest["summary"]["correlation"] = content["summary"]
    manifest["rules"] = content["rules"]
    manifest["unbound"] = content["unbound"]

    if content["no_card_binding"]:
        notes.append({
            "code": "NO_CARD_BINDING",
            "detail": "No step's expected detection cites a TTP card, so nothing could be resolved.",
        })
    for cid in content["cards_missing"]:
        notes.append({"code": "CARD_NOT_FOUND", "detail": f"{cid} is cited by a step and is not in the catalog."})
    for r in content["rules"]:
        if r["content_status"] == "unreadable":
            notes.append({"code": "RULE_UNREADABLE", "detail": f"{r['ttp_ref']} {r['rule_id']}: {r['content_error']}"})
        elif r["logic_kind"] == "unparsed":
            notes.append({"code": "RULE_LOGIC_UNPARSED", "detail": f"{r['ttp_ref']} {r['rule_id']}: {r['logic_error']}"})
    for u in content["unbound"]:
        notes.append({
            "code": "UNBOUND_CORRELATION",
            "detail": f"{u['step']} expects a correlation alert with no rule behind it ({u['reason']}).",
        })

    index = {
        cid: _CardIndex(cards[cid]) for cid in content["cited_cards"]
        if cid not in content["cards_missing"]
    }
    # One entry per (card, detection) however many steps declare it.
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for s in steps:
        if not isinstance(s, dict):
            continue
        for k, d in enumerate(_detections(s)):
            where = f"{s.get('id')} expected_detections[{k}]"
            try:
                declared = canonical_type(d.get("type"))
            except TenantContentError as exc:
                notes.append({"code": "UNKNOWN_DETECTION_TYPE", "detail": f"{where}: {exc}"})
                continue
            if declared == "Correlation":
                continue  # carried by rules[] and unbound[]
            ref, det_id = d.get("ttp_ref"), d.get("detection_id")
            hit = index[ref].by_id.get(det_id) if ref in index and det_id else None

            if ownership_of(declared) == "built-in":
                section = "built_in"
            else:
                section = _SECTION[declared]
                if not ref or not det_id:
                    notes.append({"code": "NO_CARD_REFERENCE", "detail": f"{where}: {declared} with no ttp_ref / detection_id."})
                    continue
                if ref not in index:
                    continue  # already noted once as CARD_NOT_FOUND
                if hit is None:
                    notes.append({"code": "DETECTION_NOT_ON_CARD", "detail": f"{where}: {det_id} is not on {ref}."})
                    continue

            key = (section, ref or "", det_id or f"{where}")
            entry = seen.get(key)
            if entry is None:
                entry = {
                    "declared_type": declared,
                    "ttp_ref": ref or None,
                    "detection_id": det_id or None,
                    "name": hit["name"] if hit else (d.get("description") or None),
                    "resolved_kind": hit["kind"] if hit else None,
                    "steps": [],
                }
                if section != "built_in":
                    # The card object verbatim: the query to run, the body to deploy.
                    entry["content"] = hit["content"]
                seen[key] = entry
                manifest[section].append(entry)
            if s.get("id") not in entry["steps"]:
                entry["steps"].append(s.get("id"))

    return manifest

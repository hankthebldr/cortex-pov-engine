"""
CortexSim TTP Catalog — Phase 1 bridge between scenarios and detection cards.

Loads ``detection_scanner/ttps/*.json`` at startup and exposes a lookup table
of *detection cards* (BIOC + XQL + correlation + scoring) keyed by
``(ttp_ref, detection_id)`` so the scenario loader / orchestrator / report
generator can enrich `Result` rows with deployable Cortex content.

Design rules:

* Pure read path. The catalog never mutates the JSON corpus.
* Fail soft. Missing or malformed corpus files log a warning and are skipped
  — startup does not abort. A scenario referencing a non-existent card
  produces a warning row at validation time, not a 500 at run time.
* No coupling to ``Result`` ORM here. The catalog returns plain dicts; the
  orchestrator owns persistence.

The catalog is a process-level singleton refreshed at boot and re-exposable
via ``reload()`` for tests.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("cortexsim.ttp_catalog")


# ---------------------------------------------------------------------------
# Detection card dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectionCard:
    """One detection inside a TTP entry (BIOC, XQL, correlation, or IOC).

    A TTP entry holds *many* detections — we flatten them into this shape so
    the scenario loader can reference them by a stable composite key.
    """

    ttp_ref: str                 # e.g. "TTP-2026-0002"
    detection_id: str            # e.g. "BIOC-LSASS-001"
    kind: str                    # bioc | xql | correlation | ioc | analytics
    name: str
    description: str
    severity: Optional[str]
    logic: Optional[str]         # XQL / Sigma / correlation body (verbatim from card)
    mitre_techniques: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ttp_ref": self.ttp_ref,
            "detection_id": self.detection_id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "severity": self.severity,
            "logic": self.logic,
            "mitre_techniques": list(self.mitre_techniques),
        }


@dataclass(frozen=True)
class TtpEntry:
    """Subset of the TTP JSON the engine cares about for runtime enrichment."""

    ttp_ref: str
    name: str
    status: str
    safety_class: Optional[str]
    destructive: bool
    score_weights: dict[str, float]   # use_case_id -> sum of expected_score_weight
    detections: list[DetectionCard]
    panw_products: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ttp_ref": self.ttp_ref,
            "name": self.name,
            "status": self.status,
            "safety_class": self.safety_class,
            "destructive": self.destructive,
            "score_weights": dict(self.score_weights),
            "detections": [d.to_dict() for d in self.detections],
            "panw_products": list(self.panw_products),
        }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class TtpCatalog:
    """In-memory catalog of TTP detection cards.

    Lookups by ``(ttp_ref, detection_id)``. Both are optional in scenario
    schema — if either is missing, ``find()`` returns ``None`` and the
    orchestrator falls back to the legacy free-text description.

    The catalog also stashes the raw JSON dict per entry under
    ``_raw_by_ttp`` so the API layer can serve rich card metadata
    (tags, actors, full MITRE chain) without re-reading the corpus
    from disk on every request.
    """

    def __init__(self) -> None:
        self._by_ttp: dict[str, TtpEntry] = {}
        self._by_pair: dict[tuple[str, str], DetectionCard] = {}
        self._raw_by_ttp: dict[str, dict[str, Any]] = {}

    # ---- public API ----------------------------------------------------

    def load(self, ttps_dir: str) -> int:
        """Read every ``*.json`` under ``ttps_dir`` (non-recursive, skipping
        the ``_drafts/`` subdirectory) and replace the catalog. Returns the
        number of detection cards indexed."""
        self._by_ttp.clear()
        self._by_pair.clear()
        self._raw_by_ttp.clear()

        if not os.path.isdir(ttps_dir):
            logger.warning(
                "TTP corpus directory not found at %s — catalog will be empty",
                ttps_dir,
            )
            return 0

        loaded = 0
        rejected = 0
        for fname in sorted(os.listdir(ttps_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(ttps_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("TTP corpus skip %s: %s", path, exc)
                rejected += 1
                continue

            entry = _parse_entry(raw)
            if entry is None:
                rejected += 1
                continue

            if entry.status != "active":
                # Deprecated / withdrawn / draft entries are still indexed
                # (so historical scenarios resolve) but the engine treats
                # them as advisory-only — we surface the status to callers.
                logger.info("TTP corpus indexed non-active entry %s (status=%s)",
                            entry.ttp_ref, entry.status)

            self._by_ttp[entry.ttp_ref] = entry
            self._raw_by_ttp[entry.ttp_ref] = raw
            for det in entry.detections:
                self._by_pair[(det.ttp_ref, det.detection_id)] = det
                loaded += 1

        logger.info(
            "TTP catalog loaded: %d detection cards across %d entries (rejected=%d) from %s",
            loaded, len(self._by_ttp), rejected, ttps_dir,
        )
        return loaded

    def find(self, ttp_ref: Optional[str], detection_id: Optional[str]) -> Optional[DetectionCard]:
        """Resolve a scenario's ``ttp_ref + detection_id`` to a card, or None."""
        if not ttp_ref or not detection_id:
            return None
        return self._by_pair.get((ttp_ref, detection_id))

    def get_entry(self, ttp_ref: str) -> Optional[TtpEntry]:
        return self._by_ttp.get(ttp_ref)

    def all_entries(self) -> list[TtpEntry]:
        return list(self._by_ttp.values())

    def raw(self, ttp_ref: str) -> Optional[dict[str, Any]]:
        """Return the full unparsed TTP JSON for ``ttp_ref``, or None.

        The browser API uses this so card detail panels can render the
        rich metadata (actors, MITRE chain, panw_mapping, references)
        the parsed dataclass deliberately drops.
        """
        return self._raw_by_ttp.get(ttp_ref)

    def all_raw(self) -> dict[str, dict[str, Any]]:
        """Read-only view of every raw TTP JSON keyed by ttp_ref. Used
        by the list endpoint to render card-grid metadata cheaply."""
        return dict(self._raw_by_ttp)

    def count(self) -> int:
        return len(self._by_pair)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_entry(raw: dict[str, Any]) -> Optional[TtpEntry]:
    """Best-effort parse of a TTP entry. Missing optional fields produce an
    entry with empty defaults rather than a rejection — the corpus schema is
    strict on its own, but the engine treats it as advisory data."""
    ttp_ref = raw.get("id")
    if not isinstance(ttp_ref, str) or not ttp_ref:
        logger.warning("TTP entry missing 'id' — skipping")
        return None

    identity = raw.get("identity") or {}
    metadata = raw.get("metadata") or {}
    pov_engine = metadata.get("pov_engine") or {}

    detections_raw = raw.get("detections") or {}
    cards: list[DetectionCard] = []

    cards.extend(_parse_bioc_list(ttp_ref, detections_raw.get("biocs") or []))
    cards.extend(_parse_xql_list(ttp_ref, detections_raw.get("xql_queries") or []))
    cards.extend(_parse_correlation_list(ttp_ref, detections_raw.get("correlation_rules") or []))
    cards.extend(_parse_ioc_list(ttp_ref, detections_raw.get("iocs") or []))

    panw_mapping = raw.get("panw_mapping") or {}
    products: list[str] = []
    for p in panw_mapping.get("products") or []:
        if isinstance(p, dict) and isinstance(p.get("module"), str):
            products.append(p["module"])

    score_weights: dict[str, float] = {}
    for uc in panw_mapping.get("use_cases") or []:
        if not isinstance(uc, dict):
            continue
        uc_id = uc.get("use_case_id")
        if not isinstance(uc_id, str):
            continue
        total = 0.0
        for tc in uc.get("test_cases") or []:
            if not isinstance(tc, dict):
                continue
            w = tc.get("expected_score_weight")
            if isinstance(w, (int, float)):
                total += float(w)
        score_weights[uc_id] = round(total, 4)

    return TtpEntry(
        ttp_ref=ttp_ref,
        name=(identity.get("name") if isinstance(identity, dict) else None) or ttp_ref,
        status=raw.get("status") or "unknown",
        safety_class=pov_engine.get("safety_class") if isinstance(pov_engine, dict) else None,
        destructive=bool(pov_engine.get("destructive")) if isinstance(pov_engine, dict) else False,
        score_weights=score_weights,
        detections=cards,
        panw_products=products,
    )


def _slug(name: str, prefix: str) -> str:
    """Stable slug for synthesizing a detection_id when the corpus entry
    doesn't carry one. Lowercased, hyphenated, prefix-tagged so collisions
    across BIOC/XQL/correlation are impossible."""
    clean = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in clean:
        clean = clean.replace("--", "-")
    return f"{prefix}-{clean}"[:120]


def _parse_bioc_list(ttp_ref: str, biocs: list[Any]) -> list[DetectionCard]:
    out: list[DetectionCard] = []
    for idx, b in enumerate(biocs):
        if not isinstance(b, dict):
            continue
        name = b.get("name") or f"bioc-{idx+1}"
        det_id = b.get("detection_id") or _slug(name, "bioc")
        out.append(DetectionCard(
            ttp_ref=ttp_ref,
            detection_id=det_id,
            kind="bioc",
            name=name,
            description=b.get("description") or "",
            severity=b.get("severity"),
            logic=b.get("logic"),
            mitre_techniques=list(b.get("mitre_technique_ids") or []),
        ))
    return out


def _parse_xql_list(ttp_ref: str, xqls: list[Any]) -> list[DetectionCard]:
    out: list[DetectionCard] = []
    for idx, q in enumerate(xqls):
        if not isinstance(q, dict):
            continue
        name = q.get("name") or f"xql-{idx+1}"
        det_id = q.get("detection_id") or _slug(name, "xql")
        out.append(DetectionCard(
            ttp_ref=ttp_ref,
            detection_id=det_id,
            kind="xql",
            name=name,
            description=q.get("purpose") or "",
            severity=None,
            logic=q.get("query"),
            mitre_techniques=[],
        ))
    return out


def _parse_correlation_list(ttp_ref: str, rules: list[Any]) -> list[DetectionCard]:
    out: list[DetectionCard] = []
    for idx, r in enumerate(rules):
        if not isinstance(r, dict):
            continue
        name = r.get("name") or f"correlation-{idx+1}"
        det_id = r.get("rule_id") or r.get("detection_id") or _slug(name, "correlation")
        out.append(DetectionCard(
            ttp_ref=ttp_ref,
            detection_id=det_id,
            kind="correlation",
            name=name,
            description=r.get("description") or "",
            severity=r.get("severity"),
            logic=r.get("logic"),
            mitre_techniques=[],
        ))
    return out


def _parse_ioc_list(ttp_ref: str, iocs: list[Any]) -> list[DetectionCard]:
    out: list[DetectionCard] = []
    for idx, i in enumerate(iocs):
        if not isinstance(i, dict):
            continue
        ioc_type = i.get("ioc_type") or "ioc"
        value = i.get("value") or f"ioc-{idx+1}"
        det_id = i.get("detection_id") or _slug(f"{ioc_type}-{value}", "ioc")
        out.append(DetectionCard(
            ttp_ref=ttp_ref,
            detection_id=det_id,
            kind="ioc",
            name=f"{ioc_type}: {value}",
            description=i.get("context") or "",
            severity=None,
            logic=value,
            mitre_techniques=[],
        ))
    return out


# ---------------------------------------------------------------------------
# Module-level singleton (mirrors the orchestrator pattern)
# ---------------------------------------------------------------------------


catalog = TtpCatalog()


def default_corpus_dir(base_dir: str) -> str:
    """Convention: ``<base>/detection_scanner/ttps``."""
    return os.path.join(base_dir, "detection_scanner", "ttps")

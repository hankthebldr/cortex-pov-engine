#!/usr/bin/env python3
"""
detection_scanner/scripts/coverage_report.py — corpus coverage & quality analyzer.

A NEW additive detection-engine tool. Defensive quality tooling: it authors no
attack content, makes no network calls, and never mutates the corpus. It reads
the active scenarios (``scenarios/**/*.yml``) and detection cards
(``detection_scanner/ttps/*.json``) and reports where the corpus is thin —
MITRE tactic depth, per-plane floors, detection-type mix, methodology-family
coverage, and near-duplicate detections — plus a ranked "author-next" list.

House style mirrors ``validate.py`` / ``export_artifacts.py``:

  * module-level ``ROOT`` = ``detection_scanner/`` and ``REPO_ROOT`` = repo root
  * ``argparse`` in ``main()``; ``main()`` returns an int exit code
  * a pure ``compute_report(scenarios_dir, ttps_dir, cfg) -> dict`` that ``main()``
    wires argparse into — so tests drive a synthetic corpus with no monkeypatch
  * fail-soft on individual malformed files (WARN + skip), never a crash

Usage::

    python3 detection_scanner/scripts/coverage_report.py            # human table, WARN-only, exit 0
    python3 detection_scanner/scripts/coverage_report.py --json     # machine JSON to stdout, exit 0
    python3 detection_scanner/scripts/coverage_report.py --strict    # exit 1 on any floor/target breach
    python3 detection_scanner/scripts/coverage_report.py --quiet     # WARN lines + summary only

Exit contract:
  * 0 — normal. WARN-only mode ALWAYS returns 0 (breaches print as WARN lines +
        populate ``strict_breaches`` in JSON). ``--strict`` returns 0 when there
        are zero breaches.
  * 1 — ONLY under ``--strict`` when >= 1 floor/target breach exists (the CI gate).
  * 2 — operational error: scenarios-dir or ttps-dir missing/unreadable, or the
        pyyaml import fails. Individual malformed files are fail-soft, NOT exit 2.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only where pyyaml is absent
    yaml = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent.parent          # detection_scanner/
REPO_ROOT = ROOT.parent                                # repo root

# Directories the scenario loader prunes (mirror scenario_loader._find_yaml_files).
_SKIP_SCENARIO_DIRS = {"probes", "packages", "campaigns"}

# The six canonical scenario-side detection types (expected_detections[].type).
_STEP_TYPES = ("BIOC", "XQL", "Correlation", "IOC", "ABIOC", "Analytics")
_STEP_TYPE_BY_LOWER = {t.lower(): t for t in _STEP_TYPES}

# Catalog-side detection kinds (mirror ttp_catalog.card_detection_kind_counts).
_CATALOG_KINDS = ("bioc", "xql", "correlation", "ioc", "analytics", "abioc", "modeling")

# Fixed methodology families F1..F10, labels lifted verbatim from
# scenarios/_schema.yml (the methodology_family enum documentation).
_FAMILY_LABELS = {
    "F1": "Signal Injection & Detection Accuracy",
    "F2": "Causality Stitching Validation",
    "F3": "Asset Discovery & Inventory Coverage",
    "F4": "Posture & Compliance Scoring",
    "F5": "Automation & Workflow",
    "F6": "Response Timing",
    "F7": "AI Triage & Summarization",
    "F8": "Integration & Ingestion Health",
    "F9": "Code-to-Cloud Traceability",
    "F10": "Qualitative Evidence",
}
_FAMILIES = tuple(f"F{i}" for i in range(1, 11))

# Name-token stopwords for the overlap Jaccard (design §OVERLAP).
_NAME_STOPWORDS = frozenset(
    {"the", "a", "an", "via", "and", "or", "of", "for", "on", "in", "to", "with",
     "detection", "rule"}
)

# Same dataset/preset extraction regex validate.py uses (_DATASET_SOURCE_RE).
_DATASET_SOURCE_RE = re.compile(r"(?:dataset|preset)\s*=\s*([a-z0-9_]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def default_config() -> dict[str, Any]:
    """The 2026-07-10 gap-analysis defaults. ``main()`` overlays argparse."""
    return {
        "plane_floor": 3,
        "tactic_floor": 5,
        "abioc_analytics_target": 0.15,
        "correlation_target": 0.10,
        "family_floor": 1,
        "name_similarity": 0.60,
        "top": 15,
        "strict": False,
    }


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def _slug(name: str, prefix: str) -> str:
    """Replicate core.engine.ttp_catalog._slug (import not allowed from a script).

    Lowercase, non-alnum runs collapse to a single '-', strip ends, prefix-tag,
    cap 120. Kept byte-identical so the reported detection_id matches the engine.
    """
    clean = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in clean:
        clean = clean.replace("--", "-")
    return f"{prefix}-{clean}"[:120]


def _base_technique(tid: Optional[str]) -> Optional[str]:
    """Strip a sub-technique to its Txxxx base ('T1003.001' -> 'T1003')."""
    if not isinstance(tid, str) or not tid:
        return None
    return tid.split(".")[0]


def _name_tokens(name: str) -> set[str]:
    """Lowercased alnum word tokens of a detection name, minus stopwords."""
    toks = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {t for t in toks if t and t not in _NAME_STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _exit_code(strict: bool, strict_breaches: list) -> int:
    """Pure (strict, breaches) -> exit code map. compute_report + main share it.

    WARN-only (strict False) is ALWAYS 0. --strict is 1 iff there is >= 1 breach.
    """
    return 1 if (strict and strict_breaches) else 0


def _r4(x: float) -> float:
    return round(float(x), 4)


# ---------------------------------------------------------------------------
# File discovery (fail-soft; mirrors the live loaders)
# ---------------------------------------------------------------------------

def _load_scenarios(scenarios_dir: str, warnings: list[str]) -> list[dict[str, Any]]:
    """os.walk the scenario tree, pruning non-scenario sub-trees, parse each
    *.yml (except _schema.yml), keep status == 'active'. WARN + skip anything
    that fails to parse or is not a dict — never raise."""
    out: list[dict[str, Any]] = []
    for root, dirs, files in os.walk(scenarios_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_SCENARIO_DIRS]
        for fname in sorted(files):
            if not fname.endswith(".yml") or fname == "_schema.yml":
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh)
            except Exception as exc:  # noqa: BLE001 - fail soft like the loader
                warnings.append(f"scenario skip {path}: {exc}")
                continue
            if not isinstance(raw, dict):
                warnings.append(f"scenario skip {path}: not a mapping")
                continue
            if (raw.get("status") or "") != "active":
                continue
            out.append(raw)
    return out


def _load_cards(ttps_dir: str, warnings: list[str]) -> list[dict[str, Any]]:
    """Sorted glob of ttps_dir/*.json (non-recursive — skips _drafts/), keep
    status == 'active'. WARN + skip malformed cards (fail-soft like the catalog)."""
    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(ttps_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"card skip {os.path.basename(path)}: {exc}")
            continue
        if not isinstance(raw, dict):
            warnings.append(f"card skip {os.path.basename(path)}: not a mapping")
            continue
        if (raw.get("status") or "") != "active":
            continue
        out.append(raw)
    return out


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _scenario_techniques(scn: dict[str, Any]) -> list[str]:
    """Every technique id a scenario references (primary + steps + additional).
    Normalizes additional_techniques' dict and bare-string shapes."""
    techs: list[str] = []
    if isinstance(scn.get("mitre_technique"), str):
        techs.append(scn["mitre_technique"])
    for step in scn.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("mitre_technique"), str):
            techs.append(step["mitre_technique"])
    for at in scn.get("additional_techniques") or []:
        if isinstance(at, dict):
            tid = at.get("technique")
            if isinstance(tid, str):
                techs.append(tid)
        elif isinstance(at, str):
            techs.append(at)
    return [t for t in techs if t]


def _card_techniques(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized card techniques: most-specific id + tactic ids/names."""
    mitre = card.get("mitre_attack") or {}
    out: list[dict[str, Any]] = []
    for t in mitre.get("techniques") or []:
        if not isinstance(t, dict):
            continue
        tid = t.get("subtechnique_id") or t.get("technique_id")
        if not isinstance(tid, str) or not tid:
            continue
        out.append({
            "id": tid,
            "tactic_ids": [x for x in (t.get("tactic_ids") or []) if isinstance(x, str)],
            "tactic_names": [x for x in (t.get("tactic_names") or []) if isinstance(x, str)],
        })
    return out


def _card_detection_objects(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a card's detection objects into the shape the overlap finder
    needs: ttp_ref, detection_id (engine slug rules), kind, name, base_technique,
    dataset."""
    ttp_ref = card.get("id") or ""
    det = card.get("detections") or {}
    card_techs = _card_techniques(card)
    card_base0 = _base_technique(card_techs[0]["id"]) if card_techs else None

    def _dataset_from_body(body: Optional[str]) -> Optional[str]:
        if not isinstance(body, str):
            return None
        m = _DATASET_SOURCE_RE.search(body)
        return m.group(1).lower() if m else None

    objs: list[dict[str, Any]] = []

    for b in det.get("biocs") or []:
        if not isinstance(b, dict):
            continue
        name = b.get("name") or "bioc"
        tids = b.get("mitre_technique_ids") or []
        base = _base_technique(tids[0]) if tids else card_base0
        objs.append({
            "ttp_ref": ttp_ref,
            "detection_id": b.get("detection_id") or _slug(name, "bioc"),
            "kind": "bioc", "name": name,
            "base_technique": base, "dataset": _dataset_from_body(b.get("logic")),
        })

    for a in det.get("abiocs") or []:
        if not isinstance(a, dict):
            continue
        name = a.get("name") or "abioc"
        tids = a.get("mitre_technique_ids") or []
        base = _base_technique(tids[0]) if tids else card_base0
        objs.append({
            "ttp_ref": ttp_ref,
            "detection_id": a.get("detection_id") or _slug(name, "abioc"),
            "kind": "abioc", "name": name,
            "base_technique": base, "dataset": _dataset_from_body(a.get("logic")),
        })

    for q in det.get("xql_queries") or []:
        if not isinstance(q, dict):
            continue
        name = q.get("name") or "xql"
        dataset = _dataset_from_body(q.get("query")) or (
            q.get("dataset").lower() if isinstance(q.get("dataset"), str) else None
        )
        objs.append({
            "ttp_ref": ttp_ref,
            "detection_id": q.get("detection_id") or _slug(name, "xql"),
            "kind": "xql", "name": name,
            "base_technique": card_base0, "dataset": dataset,
        })

    for r in det.get("correlation_rules") or []:
        if not isinstance(r, dict):
            continue
        name = r.get("name") or "correlation"
        det_id = r.get("rule_id") or r.get("detection_id") or _slug(name, "correlation")
        objs.append({
            "ttp_ref": ttp_ref,
            "detection_id": det_id,
            "kind": "correlation", "name": name,
            "base_technique": card_base0, "dataset": _dataset_from_body(r.get("logic")),
        })

    for i in det.get("iocs") or []:
        if not isinstance(i, dict):
            continue
        ioc_type = i.get("ioc_type") or "ioc"
        value = i.get("value") or "ioc"
        det_id = i.get("detection_id") or _slug(f"{ioc_type}-{value}", "ioc")
        objs.append({
            "ttp_ref": ttp_ref,
            "detection_id": det_id,
            "kind": "ioc", "name": f"{ioc_type}: {value}",
            "base_technique": card_base0, "dataset": None,
        })

    for m in det.get("modeling_rules") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or "modeling"
        tids = m.get("mitre_technique_ids") or []
        base = _base_technique(tids[0]) if tids else card_base0
        objs.append({
            "ttp_ref": ttp_ref,
            "detection_id": m.get("detection_id") or _slug(name, "modeling"),
            "kind": "modeling", "name": name,
            "base_technique": base, "dataset": _dataset_from_body(m.get("logic")),
        })

    return objs


# ---------------------------------------------------------------------------
# The pure report
# ---------------------------------------------------------------------------

def compute_report(scenarios_dir: str, ttps_dir: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Pure computation of the coverage report. No printing, no sys.exit.

    Returns the deterministic ``json_shape`` dict (incl. ``strict_breaches`` and
    a precomputed ``exit_code`` derived from ``cfg['strict']``). ``main()`` prints
    it and decides the process exit code (which equals ``result['exit_code']``).
    """
    c = {**default_config(), **(cfg or {})}
    plane_floor = int(c["plane_floor"])
    tactic_floor = int(c["tactic_floor"])
    abioc_analytics_target = float(c["abioc_analytics_target"])
    correlation_target = float(c["correlation_target"])
    family_floor = int(c["family_floor"])
    name_similarity = float(c["name_similarity"])
    top = int(c["top"])
    strict = bool(c["strict"])

    warnings: list[str] = []
    scenarios = _load_scenarios(scenarios_dir, warnings)
    cards = _load_cards(ttps_dir, warnings)

    # ---- MITRE (Section 1) ----
    distinct_tech: set[str] = set()
    for scn in scenarios:
        distinct_tech.update(_scenario_techniques(scn))
    for card in cards:
        for t in _card_techniques(card):
            distinct_tech.add(t["id"])
    distinct_base = {b for b in (_base_technique(t) for t in distinct_tech) if b}

    # tactic depth
    scn_count_by_tactic: dict[str, int] = {}
    tactic_name: dict[str, str] = {}
    for scn in scenarios:
        tid = scn.get("mitre_tactic")
        if isinstance(tid, str) and tid:
            scn_count_by_tactic[tid] = scn_count_by_tactic.get(tid, 0) + 1
            nm = scn.get("mitre_tactic_name")
            if isinstance(nm, str) and nm and tid not in tactic_name:
                tactic_name[tid] = nm

    card_tech_by_tactic: dict[str, set[str]] = {}
    for card in cards:
        for t in _card_techniques(card):
            for j, tac in enumerate(t["tactic_ids"]):
                card_tech_by_tactic.setdefault(tac, set()).add(t["id"])
                if tac not in tactic_name and j < len(t["tactic_names"]):
                    if t["tactic_names"][j]:
                        tactic_name.setdefault(tac, t["tactic_names"][j])

    all_tactics = set(scn_count_by_tactic) | set(card_tech_by_tactic)
    tactic_rows: list[dict[str, Any]] = []
    zero_coverage: list[str] = []
    for tid in all_tactics:
        s_count = scn_count_by_tactic.get(tid, 0)
        c_count = len(card_tech_by_tactic.get(tid, set()))
        shallow = s_count < tactic_floor
        tactic_rows.append({
            "tactic_id": tid,
            "tactic_name": tactic_name.get(tid, ""),
            "scenario_count": s_count,
            "card_technique_count": c_count,
            "shallow": shallow,
        })
        if s_count == 0 and c_count > 0:
            zero_coverage.append(tid)
    tactic_rows.sort(key=lambda r: (-r["scenario_count"], r["tactic_id"]))
    zero_coverage.sort()

    # ---- Planes (Section 2) ----
    plane_count_map: dict[str, int] = {}
    for scn in scenarios:
        p = scn.get("plane")
        if isinstance(p, str) and p:
            plane_count_map[p] = plane_count_map.get(p, 0) + 1
    plane_rows: list[dict[str, Any]] = []
    sub_floor_planes: list[str] = []
    for plane, cnt in plane_count_map.items():
        below = cnt < plane_floor
        deficit = max(0, plane_floor - cnt)
        plane_rows.append({
            "plane": plane, "scenario_count": cnt,
            "below_floor": below, "deficit": deficit,
        })
        if below:
            sub_floor_planes.append(plane)
    plane_rows.sort(key=lambda r: (-r["scenario_count"], r["plane"]))
    sub_floor_planes.sort()

    # ---- Detection mix (Section 3) ----
    step_by_type = {t: 0 for t in _STEP_TYPES}
    for scn in scenarios:
        for step in scn.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for ed in step.get("expected_detections") or []:
                if not isinstance(ed, dict):
                    continue
                raw_t = ed.get("type")
                if not isinstance(raw_t, str):
                    continue
                canon = _STEP_TYPE_BY_LOWER.get(raw_t.lower())
                if canon:
                    step_by_type[canon] += 1
    step_total = sum(step_by_type.values())
    step_shares = {
        t: _r4(step_by_type[t] / step_total) if step_total else 0.0
        for t in _STEP_TYPES
    }

    catalog_by_kind = {k: 0 for k in _CATALOG_KINDS}
    for card in cards:
        det = card.get("detections") or {}
        catalog_by_kind["bioc"] += len(det.get("biocs") or [])
        catalog_by_kind["xql"] += len(det.get("xql_queries") or [])
        catalog_by_kind["correlation"] += len(det.get("correlation_rules") or [])
        catalog_by_kind["ioc"] += len(det.get("iocs") or [])
        catalog_by_kind["analytics"] += len(det.get("analytics_modules") or [])
        catalog_by_kind["abioc"] += len(det.get("abiocs") or [])
        catalog_by_kind["modeling"] += len(det.get("modeling_rules") or [])
    catalog_total = sum(catalog_by_kind.values())
    catalog_shares = {
        k: _r4(catalog_by_kind[k] / catalog_total) if catalog_total else 0.0
        for k in _CATALOG_KINDS
    }

    abioc_analytics_share = _r4(
        (step_by_type["ABIOC"] + step_by_type["Analytics"]) / step_total
    ) if step_total else 0.0
    correlation_share = _r4(step_by_type["Correlation"] / step_total) if step_total else 0.0
    modeling_rule_share = _r4(catalog_by_kind["modeling"] / catalog_total) if catalog_total else 0.0
    abioc_analytics_below = abioc_analytics_share < abioc_analytics_target
    correlation_below = correlation_share < correlation_target

    # ---- Methodology (Section 4) ----
    fam_counts = {f: 0 for f in _FAMILIES}
    for scn in scenarios:
        fam = scn.get("methodology_family")
        if isinstance(fam, str) and fam in fam_counts:
            fam_counts[fam] += 1
    empty_families = [f for f in _FAMILIES if fam_counts[f] < family_floor]

    # ---- Overlap (Section 5) ----
    all_objs: list[dict[str, Any]] = []
    for card in cards:
        all_objs.extend(_card_detection_objects(card))
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in all_objs:
        if o["base_technique"] and o["dataset"]:
            groups.setdefault((o["base_technique"], o["dataset"]), []).append(o)

    pairs: list[dict[str, Any]] = []
    for (base, dataset), objs in groups.items():
        for i in range(len(objs)):
            for j in range(i + 1, len(objs)):
                a, b = objs[i], objs[j]
                sim = _jaccard(_name_tokens(a["name"]), _name_tokens(b["name"]))
                if sim >= name_similarity:
                    pairs.append({
                        "base_technique": base,
                        "dataset": dataset,
                        "a": {"ttp_ref": a["ttp_ref"], "detection_id": a["detection_id"],
                              "kind": a["kind"], "name": a["name"]},
                        "b": {"ttp_ref": b["ttp_ref"], "detection_id": b["detection_id"],
                              "kind": b["kind"], "name": b["name"]},
                        "similarity": _r4(sim),
                    })
    pairs.sort(key=lambda p: (-p["similarity"], p["a"]["ttp_ref"], p["b"]["ttp_ref"]))

    # ---- Recommendations (Section 6) ----
    recs: list[dict[str, Any]] = []
    for row in plane_rows:
        if row["below_floor"]:
            deficit = row["deficit"]
            recs.append({
                "kind": "plane", "target": row["plane"],
                "current": row["scenario_count"], "target_value": plane_floor,
                "deficit": deficit, "priority": _r4(3.0 * deficit),
                "message": (f"{row['plane']} plane has {row['scenario_count']}/{plane_floor} "
                            f"scenarios — author {deficit} more"),
            })
    for row in tactic_rows:
        if row["shallow"]:
            deficit = tactic_floor - row["scenario_count"]
            label = row["tactic_name"] or row["tactic_id"]
            recs.append({
                "kind": "tactic", "target": row["tactic_id"],
                "current": row["scenario_count"], "target_value": tactic_floor,
                "deficit": deficit, "priority": _r4(3.0 * deficit),
                "message": (f"{label} ({row['tactic_id']}) has {row['scenario_count']}/"
                            f"{tactic_floor} scenarios — backfill {deficit} more"),
            })
    if abioc_analytics_below:
        deficit = round((abioc_analytics_target - abioc_analytics_share) * step_total)
        recs.append({
            "kind": "detection-type", "target": "abioc_analytics",
            "current": abioc_analytics_share, "target_value": _r4(abioc_analytics_target),
            "deficit": deficit, "priority": _r4(2.5 * deficit),
            "message": (f"ABIOC+Analytics at {abioc_analytics_share * 100:.1f}% vs "
                        f"{abioc_analytics_target * 100:.0f}% target — author ~{deficit} "
                        f"ML-anchored (ABIOC/Analytics) step-detections on EDR/ITDR"),
        })
    if correlation_below:
        deficit = round((correlation_target - correlation_share) * step_total)
        recs.append({
            "kind": "detection-type", "target": "correlation",
            "current": correlation_share, "target_value": _r4(correlation_target),
            "deficit": deficit, "priority": _r4(2.5 * deficit),
            "message": (f"Correlation at {correlation_share * 100:.1f}% vs "
                        f"{correlation_target * 100:.0f}% target — author ~{deficit} "
                        f"cross-plane correlation step-detections"),
        })
    for fam in _FAMILIES:
        if fam_counts[fam] < family_floor:
            deficit = family_floor - fam_counts[fam]
            recs.append({
                "kind": "family", "target": fam,
                "current": fam_counts[fam], "target_value": family_floor,
                "deficit": deficit, "priority": _r4(2.0 * deficit),
                "message": (f"Methodology family {fam} ({_FAMILY_LABELS[fam]}) has "
                            f"no exemplar" if fam_counts[fam] == 0
                            else f"Methodology family {fam} ({_FAMILY_LABELS[fam]}) has "
                                 f"{fam_counts[fam]}/{family_floor}"),
            })
    recs.sort(key=lambda r: (-r["priority"], r["kind"], str(r["target"])))
    recs = recs[:top]
    for rank, r in enumerate(recs, start=1):
        r["rank"] = rank

    # ---- Strict breaches ----
    strict_breaches: list[dict[str, str]] = []
    for plane in sub_floor_planes:
        cnt = plane_count_map[plane]
        strict_breaches.append({"kind": "sub_floor_plane", "target": plane,
                                "detail": f"{cnt}/{plane_floor} scenarios"})
    for row in tactic_rows:
        if row["shallow"]:
            strict_breaches.append({"kind": "shallow_tactic", "target": row["tactic_id"],
                                    "detail": f"{row['scenario_count']}/{tactic_floor} scenarios"})
    for fam in empty_families:
        strict_breaches.append({"kind": "empty_family", "target": fam,
                                "detail": f"{fam_counts[fam]}/{family_floor} scenarios"})
    if abioc_analytics_below:
        strict_breaches.append({"kind": "below_target_detection_type", "target": "abioc_analytics",
                                "detail": f"{abioc_analytics_share} vs {_r4(abioc_analytics_target)}"})
    if correlation_below:
        strict_breaches.append({"kind": "below_target_detection_type", "target": "correlation",
                                "detail": f"{correlation_share} vs {_r4(correlation_target)}"})

    exit_code = _exit_code(strict, strict_breaches)

    return {
        "meta": {
            "scenarios_dir": scenarios_dir,
            "ttps_dir": ttps_dir,
            "scenario_count": len(scenarios),
            "card_count": len(cards),
            "step_detection_count": step_total,
            "catalog_detection_count": catalog_total,
            "plane_count": len(plane_count_map),
            "config": {
                "plane_floor": plane_floor,
                "tactic_floor": tactic_floor,
                "abioc_analytics_target": _r4(abioc_analytics_target),
                "correlation_target": _r4(correlation_target),
                "family_floor": family_floor,
                "name_similarity": _r4(name_similarity),
            },
        },
        "mitre": {
            "distinct_techniques": len(distinct_tech),
            "distinct_base_techniques": len(distinct_base),
            "tactics": tactic_rows,
            "zero_coverage_tactics": zero_coverage,
        },
        "planes": {
            "floor": plane_floor,
            "counts": plane_rows,
            "sub_floor": sub_floor_planes,
        },
        "detection_mix": {
            "step_detections": {"total": step_total, "by_type": step_by_type,
                                "shares": step_shares},
            "catalog_detections": {"total": catalog_total, "by_kind": catalog_by_kind,
                                   "shares": catalog_shares},
            "abioc_analytics_share": abioc_analytics_share,
            "abioc_analytics_target": _r4(abioc_analytics_target),
            "abioc_analytics_below_target": abioc_analytics_below,
            "correlation_share": correlation_share,
            "correlation_target": _r4(correlation_target),
            "correlation_below_target": correlation_below,
            "modeling_rule_share": modeling_rule_share,
        },
        "methodology": {
            "labels": dict(_FAMILY_LABELS),
            "counts": fam_counts,
            "empty_families": empty_families,
        },
        "overlap": {
            "name_similarity": _r4(name_similarity),
            "pairs": pairs,
        },
        "recommendations": recs,
        "strict_breaches": strict_breaches,
        "exit_code": exit_code,
        "_warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Human rendering
# ---------------------------------------------------------------------------

def _priority_band(p: float) -> str:
    if p >= 9:
        return "CRIT"
    if p >= 4.5:
        return "HIGH"
    if p >= 2:
        return "MED"
    return "LOW"


def render_human(result: dict[str, Any], quiet: bool) -> None:
    meta = result["meta"]
    warns = result.get("_warnings", [])
    for w in warns:
        print(f"WARN  {w}")

    # HEADER
    print(f"{meta['scenario_count']} active scenarios · {meta['card_count']} active cards "
          f"· {meta['plane_count']} planes · {meta['step_detection_count']} step-detections "
          f"· {meta['catalog_detection_count']} catalog detections")

    # SECTION 1 — MITRE
    mitre = result["mitre"]
    print()
    print("── SECTION 1 · MITRE COVERAGE ──")
    print(f"Distinct techniques: {mitre['distinct_techniques']} "
          f"({mitre['distinct_base_techniques']} base techniques)")
    if not quiet:
        print(f"  {'Tactic':<8} {'Name':<26} {'Scen':>4} {'CardTech':>8}  flag")
        for row in mitre["tactics"]:
            flag = "SHALLOW" if row["shallow"] else ""
            print(f"  {row['tactic_id']:<8} {row['tactic_name'][:26]:<26} "
                  f"{row['scenario_count']:>4} {row['card_technique_count']:>8}  {flag}")
    if mitre["zero_coverage_tactics"]:
        print(f"  zero scenario coverage (card-only): {', '.join(mitre['zero_coverage_tactics'])}")

    # SECTION 2 — Planes
    planes = result["planes"]
    print()
    print(f"── SECTION 2 · PLANE COVERAGE vs FLOOR ({planes['floor']}) ──")
    if not quiet:
        print(f"  {'Plane':<14} {'Scen':>4}  flag")
        for row in planes["counts"]:
            flag = f"SUB-FLOOR (need +{row['deficit']})" if row["below_floor"] else ""
            print(f"  {row['plane']:<14} {row['scenario_count']:>4}  {flag}")
    if planes["sub_floor"]:
        print(f"  sub-floor planes: {', '.join(planes['sub_floor'])}")

    # SECTION 3 — Detection mix
    dm = result["detection_mix"]
    print()
    print("── SECTION 3 · DETECTION-TYPE MIX ──")
    if not quiet:
        sd = dm["step_detections"]
        print(f"  (a) step-detections (n={sd['total']}):")
        for t in _STEP_TYPES:
            print(f"      {t:<12} {sd['by_type'][t]:>4}  {sd['shares'][t] * 100:5.1f}%")
        cd = dm["catalog_detections"]
        print(f"  (b) catalog-detections (n={cd['total']}):")
        for k in _CATALOG_KINDS:
            extra = "  substrate — informational" if k == "modeling" else ""
            print(f"      {k:<12} {cd['by_kind'][k]:>4}  {cd['shares'][k] * 100:5.1f}%{extra}")
    aa_flag = "BELOW" if dm["abioc_analytics_below_target"] else "OK"
    co_flag = "BELOW" if dm["correlation_below_target"] else "OK"
    print(f"  ABIOC+Analytics {dm['abioc_analytics_share'] * 100:.1f}% vs target "
          f"{dm['abioc_analytics_target'] * 100:.0f}% [{aa_flag}]")
    print(f"  Correlation {dm['correlation_share'] * 100:.1f}% vs target "
          f"{dm['correlation_target'] * 100:.0f}% [{co_flag}]")

    # SECTION 4 — Methodology
    meth = result["methodology"]
    print()
    print("── SECTION 4 · METHODOLOGY-FAMILY COVERAGE (F1..F10) ──")
    if not quiet:
        print(f"  {'Family':<7} {'Label':<40} {'Scen':>4}  flag")
        for fam in _FAMILIES:
            flag = "EMPTY" if fam in meth["empty_families"] else ""
            print(f"  {fam:<7} {meth['labels'][fam][:40]:<40} "
                  f"{meth['counts'][fam]:>4}  {flag}")
    if meth["empty_families"]:
        print(f"  empty families: {', '.join(meth['empty_families'])}")

    # SECTION 5 — Overlap
    ov = result["overlap"]
    print()
    print(f"── SECTION 5 · DETECTION OVERLAP / NEAR-DUPLICATES (Jaccard ≥ "
          f"{ov['name_similarity']}) ──")
    print("  candidate re-skins — verify before authoring net-new")
    if not quiet:
        if ov["pairs"]:
            print(f"  {'Technique':<9} {'Dataset':<22} {'A':<28} {'B':<28} {'Sim':>5}")
            for p in ov["pairs"]:
                a = f"{p['a']['ttp_ref']}:{p['a']['detection_id']}"
                b = f"{p['b']['ttp_ref']}:{p['b']['detection_id']}"
                print(f"  {p['base_technique']:<9} {p['dataset'][:22]:<22} "
                      f"{a[:28]:<28} {b[:28]:<28} {p['similarity']:>5.2f}")
        else:
            print("  (no near-duplicate pairs)")

    # SECTION 6 — Recommendations
    recs = result["recommendations"]
    print()
    print("── SECTION 6 · AUTHOR-NEXT RECOMMENDATIONS ──")
    if recs:
        for r in recs:
            print(f"  {r['rank']}. [{_priority_band(r['priority'])}] {r['message']}")
    else:
        print("  (no gaps against current floors/targets)")
    if ov["pairs"]:
        print(f"  consolidate: {len(ov['pairs'])} near-duplicate detection pair(s) — "
              f"review/merge rather than authoring net-new (see Section 5)")

    # SUMMARY
    n_breach = len(result["strict_breaches"])
    n_warn = len(warns) + n_breach
    print()
    print(f"--- coverage: {n_warn} warn(s), {n_breach} strict-breach(es) "
          f"(WARN-only; use --strict to gate) ---")


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Corpus coverage & quality analyzer (read-only).")
    ap.add_argument("--json", action="store_true", help="emit the JSON object to stdout and nothing else")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any floor/target breach (CI gate)")
    ap.add_argument("--quiet", action="store_true", help="suppress per-section detail lines; keep WARN + summary")
    ap.add_argument("--plane-floor", type=int, default=3, help="min active scenarios per plane (DL-05)")
    ap.add_argument("--tactic-floor", type=int, default=5, help="min scenarios per primary tactic (DL-04)")
    ap.add_argument("--abioc-analytics-target", type=float, default=0.15,
                    help="min ABIOC+Analytics share of step-detections (DL-01)")
    ap.add_argument("--correlation-target", type=float, default=0.10,
                    help="min Correlation share of step-detections (DL-03)")
    ap.add_argument("--family-floor", type=int, default=1, help="min scenarios per methodology family (DL-06)")
    ap.add_argument("--name-similarity", type=float, default=0.60,
                    help="Jaccard threshold on name tokens for the overlap finder")
    ap.add_argument("--top", type=int, default=15, help="max rows in the author-next list")
    ap.add_argument("--scenarios-dir", default=str(REPO_ROOT / "scenarios"),
                    help="override the scenarios directory")
    ap.add_argument("--ttps-dir", default=str(ROOT / "ttps"),
                    help="override the TTP cards directory")
    args = ap.parse_args()

    if yaml is None:
        print("FAIL  env: pyyaml is required (pip install pyyaml)", file=sys.stderr)
        return 2

    for label, path in (("scenarios-dir", args.scenarios_dir), ("ttps-dir", args.ttps_dir)):
        if not os.path.isdir(path):
            print(f"FAIL  {label}: {path} missing or not a directory", file=sys.stderr)
            return 2

    cfg = {
        "plane_floor": args.plane_floor,
        "tactic_floor": args.tactic_floor,
        "abioc_analytics_target": args.abioc_analytics_target,
        "correlation_target": args.correlation_target,
        "family_floor": args.family_floor,
        "name_similarity": args.name_similarity,
        "top": args.top,
        "strict": args.strict,
    }

    result = compute_report(args.scenarios_dir, args.ttps_dir, cfg)

    if args.json:
        emit = {k: v for k, v in result.items() if k != "_warnings"}
        print(json.dumps(emit, sort_keys=True, indent=2))
        return result["exit_code"]

    render_human(result, quiet=args.quiet)
    if args.strict:
        print(f"STRICT: exiting {result['exit_code']}")
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())

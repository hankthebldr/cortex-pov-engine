"""Derive one scenario's prerequisites from the dataset requirement registry.

Pure: no file, network, settings or catalog access. The caller passes the
scenario dict (``Scenario.to_dict()``), the raw cards (``catalog.all_raw()``)
and the registry (``dataset_requirements.load()``, ``None`` when absent).

An unrecognised shape raises ``ValueError``: reading it as "no prerequisites"
would keep the bug and green the test. Absent evidence is reported as a note,
never as an empty answer. Nothing here claims a tenant verified anything.

Note codes: NO_CARD_BINDING, CARD_NOT_FOUND, CARD_DECLARES_NO_QUERY (a cited card
with no xql_queries: its prerequisites cannot be derived), DATASET_UNDECLARED,
UNREGISTERED_DATASET, REGISTRY_ABSENT.
"""
from __future__ import annotations

from engine.dataset_requirements import DatasetRequirement


def _note(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def _cited_cards(scenario: dict) -> list[str]:
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"scenario steps must be a list, got {type(steps).__name__}")
    cited: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{i}] must be a mapping, got {type(step).__name__}")
        dets = step.get("expected_detections", [])
        if not isinstance(dets, list):
            raise ValueError(
                f"steps[{i}].expected_detections must be a list, got {type(dets).__name__}")
        for j, det in enumerate(dets):
            if not isinstance(det, dict):
                raise ValueError(
                    f"steps[{i}].expected_detections[{j}] must be a mapping, got {type(det).__name__}")
            ref = det.get("ttp_ref")
            if ref is None or ref == "":
                continue
            if not isinstance(ref, str):
                raise ValueError(
                    f"steps[{i}].expected_detections[{j}].ttp_ref must be a string, "
                    f"got {type(ref).__name__}")
            if ref.strip():
                cited.add(ref)
    return sorted(cited)


def _card_queries(card_id: str, raw: object) -> list[tuple[str, str | None]]:
    """``[(query name, dataset or None)]`` for one card; ``None`` means undeclared."""
    if not isinstance(raw, dict):
        raise ValueError(f"card {card_id} must be a mapping, got {type(raw).__name__}")
    detections = raw.get("detections")
    if detections is None:
        return []
    if not isinstance(detections, dict):
        raise ValueError(f"card {card_id}: detections must be a mapping, got {type(detections).__name__}")
    queries = detections.get("xql_queries")
    if queries is None:
        return []
    if not isinstance(queries, list):
        raise ValueError(
            f"card {card_id}: detections.xql_queries must be a list, got {type(queries).__name__}")
    out: list[tuple[str, str | None]] = []
    for i, q in enumerate(queries):
        if not isinstance(q, dict):
            raise ValueError(
                f"card {card_id}: detections.xql_queries[{i}] must be a mapping, got {type(q).__name__}")
        name = q.get("name")
        label = name if isinstance(name, str) and name.strip() else f"#{i}"
        ds = q.get("dataset")
        if ds is not None and not isinstance(ds, str):
            raise ValueError(
                f"card {card_id}: detections.xql_queries[{i}].dataset must be a string, "
                f"got {type(ds).__name__}")
        out.append((label, ds.strip() if ds and ds.strip() else None))
    return out


def _registered_row(req: DatasetRequirement, cards: list[str]) -> dict:
    return {
        "dataset": req.dataset,
        "registered": True,
        "produced_by": req.produced_by,
        "cards": cards,
        "cortex_side": [
            {"kind": c.kind, "name": c.name, "state": c.verified.state} for c in req.cortex_side],
        "source_side": [
            {"product": s.product, "requirement": s.requirement, "detail": s.detail,
             "state": s.verified.state} for s in req.source_side],
        "can_be_emulated_by": list(req.emulated_by),
    }


def derive(scenario: dict, cards: dict[str, dict],
           registry: dict[str, DatasetRequirement] | None) -> dict:
    if not isinstance(scenario, dict) or "id" not in scenario:
        raise ValueError("scenario must be a mapping with an id")
    cited = _cited_cards(scenario)

    notes: list[dict] = []
    if registry is None:
        notes.append(_note("REGISTRY_ABSENT",
                           "the dataset requirement registry is not loaded; every dataset is "
                           "listed as unregistered, not as having no requirements"))
    if not cited:
        notes.append(_note("NO_CARD_BINDING",
                           f"scenario {scenario['id']} cites no card (no ttp_ref on any expected "
                           f"detection), so its datasets cannot be derived"))

    by_dataset: dict[str, set[str]] = {}
    for card_id in cited:
        if card_id not in cards:
            notes.append(_note("CARD_NOT_FOUND", f"card {card_id} is cited but not in the catalog"))
            continue
        queries = _card_queries(card_id, cards[card_id])
        if not queries:
            notes.append(_note("CARD_DECLARES_NO_QUERY",
                               f"card {card_id} carries no query, so its prerequisites cannot be "
                               f"derived (a card with only BIOC/IOC content still depends on a "
                               f"data source)"))
        for qname, ds in queries:
            if ds is None:
                notes.append(_note("DATASET_UNDECLARED",
                                   f"card {card_id} query {qname} declares no dataset"))
            else:
                by_dataset.setdefault(ds, set()).add(card_id)

    rows: list[dict] = []
    unregistered = confirmed = documentation = 0
    for ds in sorted(by_dataset):
        ds_cards = sorted(by_dataset[ds])
        req = registry.get(ds) if registry is not None else None
        if req is None:
            unregistered += 1
            rows.append({"dataset": ds, "registered": False, "cards": ds_cards})
            notes.append(_note("UNREGISTERED_DATASET",
                               f"dataset {ds} (cards {', '.join(ds_cards)}) has no registry entry"))
            continue
        row = _registered_row(req, ds_cards)
        rows.append(row)
        for r in (*row["cortex_side"], *row["source_side"]):
            if r["state"] == "confirmed in a tenant":
                confirmed += 1
            else:
                documentation += 1

    return {
        "scenario_id": scenario["id"],
        "registry_loaded": registry is not None,
        "tenant_verified": 0,
        "write_path": False,
        "cards": cited,
        "datasets": rows,
        "summary": {"datasets": len(rows), "unregistered": unregistered,
                    "confirmed_rows": confirmed, "documentation_rows": documentation},
        "notes": notes,
    }

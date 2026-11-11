"""Derive one scenario's prerequisites from the dataset requirement registry.

Pure: no file, network, settings or catalog access. The caller passes the
scenario dict (``Scenario.to_dict()``), the raw cards (``catalog.all_raw()``)
and the registry (``dataset_requirements.load()``, ``None`` when absent).

An unrecognised shape raises ``ValueError`` (card shapes raise its subclass
``DatasetRequirementsError`` from the one walker, ``dataset_requirements.walk_card``):
reading it as "no prerequisites" would keep the bug and green the test. Absent
evidence is reported as a note, never as an empty answer. Nothing here claims a
tenant verified anything. The note codes are :data:`NOTE_CODES`.
"""
from __future__ import annotations

from engine.dataset_requirements import QUERY_KIND, DatasetRequirement, walk_card

# Every note code derive() can emit. docs/reference/dataset-requirements.md prints the same set;
# a test pins the two together.
NOTE_CODES: tuple[str, ...] = (
    "NO_CARD_BINDING",         # no expected detection carries a ttp_ref
    "DETECTION_UNBOUND",       # one expected detection carries no ttp_ref (one note per entry)
    "CARD_NOT_FOUND",          # a cited card is not in the catalog
    "CARD_DECLARES_NO_QUERY",  # a cited card has no xql_queries
    "DATASET_UNDECLARED",      # a card query declares no dataset
    "UNREGISTERED_DATASET",    # a dataset has no registry entry
    "REGISTRY_ABSENT",         # the registry is not loaded
)


def _note(code: str, detail: str) -> dict:
    assert code in NOTE_CODES, code
    return {"code": code, "detail": detail}


def _bindings(scenario: dict) -> tuple[list[str], list[dict]]:
    """``(sorted cited card ids, unbound expected detections)``.

    An unbound entry is ``{"step", "index", "type", "plane"}`` for an expected detection whose
    ``ttp_ref`` is missing, ``None``, empty or whitespace-only. A step without an
    ``expected_detections`` key has none (allowed).
    """
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"scenario steps must be a list, got {type(steps).__name__}")
    cited: set[str] = set()
    unbound: list[dict] = []
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
            if ref is not None and not isinstance(ref, str):
                raise ValueError(
                    f"steps[{i}].expected_detections[{j}].ttp_ref must be a string, "
                    f"got {type(ref).__name__}")
            if ref and ref.strip():
                cited.add(ref)
                continue
            step_id = step.get("id")
            unbound.append({
                "step": step_id if isinstance(step_id, str) and step_id.strip() else f"steps[{i}]",
                "index": j, "type": det.get("type"), "plane": det.get("plane")})
    return sorted(cited), unbound


def cited_cards(scenario: dict) -> list[str]:
    return _bindings(scenario)[0]


def _registered_row(req: DatasetRequirement, cards: list[str], read_by: list[str]) -> dict:
    return {
        "dataset": req.dataset,
        "registered": True,
        "produced_by": req.produced_by,
        "cards": cards,
        "read_by": read_by,
        "cortex_side": [
            {"kind": c.kind, "name": c.name, "state": c.verified.state} for c in req.cortex_side],
        "source_side": [
            {"product": s.product, "requirement": s.requirement, "detail": s.detail,
             "state": s.verified.state} for s in req.source_side],
        "can_be_emulated_by": list(req.emulated_by),
    }


def derive(scenario: dict, cards: dict[str, dict],
           registry: dict[str, DatasetRequirement] | None) -> dict:
    if not isinstance(scenario, dict):
        raise ValueError(f"scenario must be a mapping, got {type(scenario).__name__}")
    sid = scenario.get("scenario_id")
    if not isinstance(sid, str) or not sid.strip():
        # ``id`` is the integer primary key on Scenario.to_dict(); never the scenario's name.
        raise ValueError(f"scenario must carry a non-blank string scenario_id, got {sid!r}")
    cited, unbound = _bindings(scenario)

    notes: list[dict] = []
    if registry is None:
        notes.append(_note("REGISTRY_ABSENT",
                           "the dataset requirement registry is not loaded; every dataset is "
                           "listed as unregistered, not as having no requirements"))
    if not cited:
        notes.append(_note("NO_CARD_BINDING",
                           f"scenario {sid} cites no card (no ttp_ref on any expected "
                           f"detection), so its datasets cannot be derived"))
    for u in unbound:
        what = ", ".join(f"{k} {u[k]}" for k in ("type", "plane") if isinstance(u[k], str) and u[k])
        notes.append(_note("DETECTION_UNBOUND",
                           f"step {u['step']} expected_detections[{u['index']}]"
                           f"{' (' + what + ')' if what else ''} carries no ttp_ref, so the "
                           f"datasets it depends on are not derived"))

    by_dataset: dict[str, set[str]] = {}
    read_by: dict[str, set[str]] = {}
    for card_id in cited:
        if card_id not in cards:
            notes.append(_note("CARD_NOT_FOUND", f"card {card_id} is cited but not in the catalog"))
            continue
        reads = walk_card(card_id, cards[card_id])
        if not any(r.kind == QUERY_KIND for r in reads):
            if reads:
                kinds = ", ".join(sorted({r.kind for r in reads}))
                detail = (f"card {card_id} carries no query; its datasets are read from its "
                          f"{kinds} logic only")
            else:
                detail = (f"card {card_id} carries no query and no dataset anchor in its BIOC, "
                          f"ABIOC or correlation logic, so its prerequisites cannot be derived "
                          f"(a card with only IOC content still depends on a data source)")
            notes.append(_note("CARD_DECLARES_NO_QUERY", detail))
        for r in reads:
            if r.dataset is None:
                notes.append(_note("DATASET_UNDECLARED",
                                   f"card {card_id} query {r.label} declares no dataset"))
            else:
                by_dataset.setdefault(r.dataset, set()).add(card_id)
                read_by.setdefault(r.dataset, set()).add(r.kind)

    rows: list[dict] = []
    unregistered = confirmed = documentation = 0
    for ds in sorted(by_dataset):
        ds_cards = sorted(by_dataset[ds])
        req = registry.get(ds) if registry is not None else None
        if req is None:
            unregistered += 1
            rows.append({"dataset": ds, "registered": False, "cards": ds_cards,
                         "read_by": sorted(read_by[ds])})
            notes.append(_note("UNREGISTERED_DATASET",
                               f"dataset {ds} (cards {', '.join(ds_cards)}) has no registry entry"))
            continue
        row = _registered_row(req, ds_cards, sorted(read_by[ds]))
        rows.append(row)
        for r in (*row["cortex_side"], *row["source_side"]):
            if r["state"] == "confirmed in a tenant":
                confirmed += 1
            else:
                documentation += 1

    return {
        "scenario_id": sid,
        "registry_loaded": registry is not None,
        "tenant_verified": 0,
        "write_path": False,
        "cards": cited,
        "datasets": rows,
        "summary": {"datasets": len(rows), "unregistered": unregistered,
                    "confirmed_rows": confirmed, "documentation_rows": documentation},
        "notes": notes,
    }

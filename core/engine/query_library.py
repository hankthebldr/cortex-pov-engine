"""
A scenario's hunt queries as paste-ready plain text.

Pure: no file, network, settings or catalog access. The caller hands in the scenario dict and the
card catalog. Nothing here claims a query was run anywhere; the header says so.

Cards come in id order, queries in card order, query text verbatim. A malformed card or step
raises ``ValueError`` (the route turns it into ``CARD_SHAPE_UNRECOGNISED``) rather than reading
as "no query".
"""
from __future__ import annotations

from engine.prerequisites import _cited_cards

ALLOWED_PURPOSES = ("hunt", "investigation", "validation", "metric")


def _join(purposes: tuple[str, ...]) -> str:
    if len(purposes) <= 2:
        return " or ".join(purposes)
    return ", ".join(purposes[:-1]) + " or " + purposes[-1]


def _queries(card_id: str, raw: object) -> list[dict]:
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
    for i, q in enumerate(queries):
        if not isinstance(q, dict):
            raise ValueError(
                f"card {card_id}: detections.xql_queries[{i}] must be a mapping, got {type(q).__name__}")
        for field in ("name", "purpose", "dataset", "query"):
            v = q.get(field)
            if v is not None and not isinstance(v, str):
                raise ValueError(
                    f"card {card_id}: detections.xql_queries[{i}].{field} must be a string, "
                    f"got {type(v).__name__}")
    return queries


def render(scenario: dict, cards: dict[str, dict],
           purposes: tuple[str, ...] = ("hunt", "investigation")) -> str:
    bad = [p for p in purposes if p not in ALLOWED_PURPOSES]
    if bad or not purposes:
        raise ValueError(f"purposes must be drawn from {list(ALLOWED_PURPOSES)}, got {list(purposes)}")
    sid = scenario.get("scenario_id")
    lines = [
        f"# {sid} — query library ({', '.join(purposes)})",
        "# Paste into XQL Search. Authored, not run against any tenant.",
    ]
    wanted = set(purposes)
    blocks: list[str] = []
    for card_id in _cited_cards(scenario):
        if card_id not in cards:
            blocks.append(f"# card {card_id}: not found in the card catalog\n")
            continue
        for q in _queries(card_id, cards[card_id]):
            if q.get("purpose") not in wanted:
                continue
            name = q.get("name")
            title = name if name and name.strip() else "(unnamed query)"
            dataset = q.get("dataset")
            text = q.get("query")
            head = (f"## {title}\n# card: {card_id}   purpose: {q['purpose']}   "
                    f"dataset: {dataset if dataset and dataset.strip() else '(undeclared)'}\n")
            if text is None or not text.strip():
                body = "# (query text is empty on the card)\n"
            else:
                body = text if text.endswith("\n") else text + "\n"
            blocks.append(head + body)
    if not any(b.startswith("## ") for b in blocks):
        lines.append(f"# No {_join(purposes)} query on this scenario's cards.")
    out = "\n".join(lines) + "\n"
    if blocks:
        out += "\n" + "\n".join(blocks)
    return out

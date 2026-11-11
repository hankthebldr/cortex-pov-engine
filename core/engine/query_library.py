"""
A scenario's hunt queries as paste-ready plain text.

Pure: no file, network, settings or catalog access. The caller hands in the scenario dict and the
card catalog. Nothing here claims a query was run anywhere; the header says so.

Cards come in id order, queries in card order, query text verbatim. A malformed card or step
raises ``ValueError`` (the route turns it into ``CARD_SHAPE_UNRECOGNISED``) rather than reading
as "no query".
"""
from __future__ import annotations

import re

from engine.dataset_requirements import walk_card
from engine.prerequisites import cited_cards

ALLOWED_PURPOSES = ("hunt", "investigation", "validation", "metric")

# Every character str.splitlines() (and common editors) treat as a line break.
_LINE_BREAK = re.compile("[\n\r\v\f\x1c\x1d\x1e\x85  ]")


def _join(purposes: tuple[str, ...]) -> str:
    if len(purposes) <= 2:
        return " or ".join(purposes)
    return ", ".join(purposes[:-1]) + " or " + purposes[-1]


def _one_line(value: object, what: str) -> None:
    if isinstance(value, str) and _LINE_BREAK.search(value):
        raise ValueError(f"{what} contains a line-break character: {value!r}")


def _items(card_id: str, raw: object) -> list[dict]:
    """Validated query dicts for one card. Base shape rules live in ``walk_card``."""
    walk_card(card_id, raw)  # raises a ValueError subclass on a malformed card
    queries = (raw.get("detections") or {}).get("xql_queries") or []
    for i, q in enumerate(queries):
        where = f"card {card_id} query #{i}"
        for field in ("name", "purpose", "query"):
            v = q.get(field)
            if v is not None and not isinstance(v, str):
                raise ValueError(f"{where}: {field} must be a string, got {type(v).__name__}")
        if q.get("purpose") not in ALLOWED_PURPOSES:
            raise ValueError(
                f"{where}: purpose must be one of {list(ALLOWED_PURPOSES)}, got {q.get('purpose')!r}")
        _one_line(q.get("name"), f"{where}: name")
        _one_line(q.get("dataset"), f"{where}: dataset")
    return queries


def render(scenario: dict, cards: dict[str, dict],
           purposes: tuple[str, ...] = ("hunt", "investigation")) -> str:
    if (not purposes or len(set(purposes)) != len(purposes)
            or any(p not in ALLOWED_PURPOSES for p in purposes)):
        raise ValueError(
            f"purposes must be distinct and drawn from {list(ALLOWED_PURPOSES)}, got {list(purposes)}")
    sid = scenario.get("scenario_id")
    _one_line(sid, "scenario_id")
    lines = [
        f"# {sid} — query library ({', '.join(purposes)})",
        "# Paste into XQL Search. Authored, not run against any tenant.",
    ]
    wanted = set(purposes)
    blocks: list[str] = []
    matched = 0
    others: dict[str, int] = {}
    for card_id in cited_cards(scenario):
        _one_line(card_id, "card id")
        if card_id not in cards:
            blocks.append(f"# card {card_id}: not found in the card catalog\n")
            continue
        for q in _items(card_id, cards[card_id]):
            if q["purpose"] not in wanted:
                others[q["purpose"]] = others.get(q["purpose"], 0) + 1
                continue
            matched += 1
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
    if not matched:
        lines.append(f"# No {_join(purposes)} query on this scenario's cards.")
        if others:
            present = [p for p in ALLOWED_PURPOSES if p in others]
            counts = ", ".join(f"{others[p]} {p}" for p in present)
            lines.append(f"# The cited cards carry {counts} queries: add ?purpose={','.join(present)}")
    out = "\n".join(lines) + "\n"
    if blocks:
        out += "\n" + "\n".join(blocks)
    return out

#!/usr/bin/env python3
"""Rebuild pairs the first packager wrote, by the current rules, into staging.

The first packager cloned a live exemplar pair, so its output carries the
exemplar's subject everywhere it did not overwrite. This reads such pairs from
a tree (a checkout or an extracted archive of the branch that holds them),
recovers each model fragment, and rebuilds the pair with ``library.build_pair``.
Nothing from the old pair's exemplar-owned fields survives.

usage: restage.py TREE CARDS.txt SCENARIOS.txt --origin "PR #229 @ <sha>" [--date YYYY-MM-DD] [--apply]

TREE           root of the tree holding the old pairs
CARDS.txt      old card paths, one per line, relative to TREE
SCENARIOS.txt  old scenario paths, one per line, relative to TREE
Without --apply it reports what it would do and writes nothing.

The 366 pairs under drafts/detection-library/ were rebuilt from PR #229
(origin/feature/detection-library-buildout @ 46df794d) with these inputs:

    B=origin/feature/detection-library-buildout
    mkdir TREE && git archive "$B" detection_scanner/ttps scenarios | tar -x -C TREE
    git diff --diff-filter=A --name-only "origin/dev...${B}" -- 'detection_scanner/ttps/*.json' > CARDS.txt
    git diff --diff-filter=A --name-only "origin/dev...${B}" -- scenarios/ | grep '\\.yml$' > SCENARIOS.txt
    python3 scripts/detdev/restage.py TREE CARDS.txt SCENARIOS.txt --origin "PR #229 @ 46df794d" --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library as lib  # noqa: E402

ORIGIN_FILE = lib.STAGING / "ORIGIN.json"


def _read_list(path: str) -> list[str]:
    return [line.strip() for line in Path(path).read_text().splitlines() if line.strip()]


def pair_up(tree: Path, cards: list[str], scenarios: list[str]) -> list[tuple[str, dict, str, dict]]:
    """Match each old scenario to the old card its rows cite. Raises on any orphan."""
    by_id: dict[str, tuple[str, dict]] = {}
    for rel in cards:
        card = json.loads((tree / rel).read_text())
        if card["id"] in by_id:
            raise SystemExit(f"two old cards share id {card['id']}: {by_id[card['id']][0]} and {rel}")
        by_id[card["id"]] = (rel, card)
    pairs, used = [], set()
    for rel in scenarios:
        scenario = yaml.safe_load((tree / rel).read_text())
        cited = {row.get("ttp_ref") for step in scenario.get("steps") or []
                 for row in step.get("expected_detections") or [] if row.get("ttp_ref")}
        cited &= set(by_id)
        if len(cited) != 1:
            # No row cites a card (a scenario whose steps declare nothing): fall
            # back to the card whose file name carries this scenario's id.
            tail = scenario["scenario_id"].lower()
            cited = {cid for cid, (crel, _) in by_id.items() if crel.endswith(f"-{tail}.json")}
        if len(cited) != 1:
            raise SystemExit(f"{rel}: cannot match exactly one old card (candidates: {sorted(cited)})")
        cid = cited.pop()
        if cid in used:
            raise SystemExit(f"{rel}: old card {cid} is already paired")
        used.add(cid)
        pairs.append((by_id[cid][0], by_id[cid][1], rel, scenario))
    orphans = sorted(set(by_id) - used)
    if orphans:
        raise SystemExit(f"old cards with no scenario: {orphans}")
    return sorted(pairs, key=lambda p: p[1]["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree")
    ap.add_argument("cards")
    ap.add_argument("scenarios")
    ap.add_argument("--origin", required=True, help='where the old pairs came from, e.g. "PR #229 @ 46df794d"')
    ap.add_argument("--date", default="2026-11-12", help="the day the fragments were generated")
    ap.add_argument("--apply", action="store_true", help="write the staged pairs (default: dry run)")
    args = ap.parse_args()

    tree = Path(args.tree)
    pairs = pair_up(tree, _read_list(args.cards), _read_list(args.scenarios))
    names = lib.technique_index()
    origin: dict[str, dict] = {}
    dropped_hashes = 0
    out: list[tuple[Path, str]] = []
    for position, (card_rel, old_card, scn_rel, old_scenario) in enumerate(pairs, 1):
        fragment = lib.fragment_from_pair(old_card, old_scenario)
        _, dropped = lib.filter_iocs(fragment["iocs"])
        dropped_hashes += len(dropped)
        new_card_id, new_scn_id = lib.provisional_ids(position)
        old_sid = old_scenario["scenario_id"]
        for step in fragment["steps"]:  # a marker that names the old id names the new one
            step["command"] = re.sub(rf"\b{re.escape(old_sid)}\b", new_scn_id, step["command"])
        card, scenario, source = lib.build_pair(
            fragment, index_position=position, generated_on=args.date, technique_names=names,
            extra_references=old_card.get("references") or [])
        card_path, scn_path = lib.staged_paths(card, scenario)
        out.append((card_path, lib.dump_card(card)))
        out.append((scn_path, yaml.safe_dump(scenario, sort_keys=False, width=100, allow_unicode=True)))
        origin[new_card_id] = {
            "from": args.origin,
            "card_id": old_card["id"], "card_file": card_rel,
            "scenario_id": old_sid, "scenario_file": scn_rel,
            "hash_iocs_dropped": len(dropped),
        }
    print(f"{len(pairs)} pairs · {dropped_hashes} unsourced hash IOC(s) dropped · "
          f"{'writing' if args.apply else 'dry run, nothing written'}")
    if not args.apply:
        return 0
    lib.STAGED_CARDS.mkdir(parents=True, exist_ok=True)
    lib.STAGED_SCENARIOS.mkdir(parents=True, exist_ok=True)
    for path, text in out:
        path.write_text(text)
    ORIGIN_FILE.write_text(json.dumps(origin, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(out)} files under {lib.STAGING.relative_to(lib.REPO)} and {ORIGIN_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

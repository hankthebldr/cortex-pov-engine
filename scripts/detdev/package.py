#!/usr/bin/env python3
"""Phase 2: package a generator fragment into a staged draft pair.

usage: package.py FRAG.json [--plane EDR] [--date YYYY-MM-DD] [--apply]

Builds the card and the scenario from the fragment and its recorded source only
(``library.build_pair``). It reads no live card or scenario as a template, so a
pair carries nothing but what the fragment and the alert target list say.

It writes only under ``drafts/detection-library/``, at the next free provisional
id (``TTP-0000-NNNN`` / ``SIM-DRAFT-NNN``). It never writes into ``scenarios/``
or ``detection_scanner/ttps/``: a draft reaches the corpus by promotion, which
is a reviewed authoring step (see ``drafts/detection-library/README.md``).

Without --apply it prints what it would write and writes nothing.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library as lib  # noqa: E402


def package(fragment: dict, *, plane: str, generated_on: str) -> tuple[dict, dict, dict, list[dict]]:
    """fragment -> (card, scenario, source, hash IOCs dropped for having no source)."""
    fragment = lib.rows_from_detects({**fragment, "plane": plane.upper()})
    _, dropped = lib.filter_iocs(fragment.get("iocs"))
    card, scenario, source = lib.build_pair(
        fragment, index_position=lib.next_position(), generated_on=generated_on,
        technique_names=lib.technique_index())
    return card, scenario, source, dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fragment", help="fragment JSON written by genfrag.py")
    ap.add_argument("--plane", default="EDR")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="the day the fragment was generated (default: today)")
    ap.add_argument("--apply", action="store_true", help="write the staged pair (default: dry run)")
    args = ap.parse_args(argv)

    fragment = json.loads(Path(args.fragment).read_text())
    if fragment.get("target_platform") not in lib.PLATFORMS:
        sys.exit(f"{args.fragment}: target_platform must be one of {', '.join(lib.PLATFORMS)} "
                 f"(got {fragment.get('target_platform')!r}); pass --platform to genfrag.py")
    if not fragment.get("mitre_technique_ids"):
        sys.exit(f"{args.fragment}: no mitre_technique_ids; a pair needs at least one ATT&CK technique")

    card, scenario, source, dropped = package(fragment, plane=args.plane, generated_on=args.date)
    card_path, scenario_path = lib.staged_paths(card, scenario)
    rows = sum(len(step["expected_detections"]) for step in scenario["steps"])
    print(f"card      {card_path.relative_to(lib.REPO)}")
    print(f"scenario  {scenario_path.relative_to(lib.REPO)}  ({len(scenario['steps'])} steps, {rows} declared rows)")
    print("source    " + (f"{source['name']}  {source['url']}" if source["kind"] == "cortex-analytics-alert"
                          else "not recorded (no analytics_modules entry the target list confirms)"))
    if dropped:
        print(f"dropped   {len(dropped)} hash IOC(s) with no source_url")
    if not args.apply:
        print("dry run: nothing written (pass --apply)")
        return 0
    card_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(lib.dump_card(card))
    scenario_path.write_text(yaml.safe_dump(scenario, sort_keys=False, width=100, allow_unicode=True))
    print("written. Run scripts/detdev/manifest.py --apply to refresh MANIFEST.json and INDEX.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())

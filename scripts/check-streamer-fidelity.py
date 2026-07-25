#!/usr/bin/env python3
"""check-streamer-fidelity.py — prove the analytics log-streamer emitters actually
fire the detections they exist for.

CI validates each side in isolation: validate.py checks a card's XQL is
well-formed; the EAL test suite checks an emitter registers + emits. NEITHER
checks that the emitter's emitted stream would produce the dataset rows the
detection queries. That gap shipped a false-green once (ngfw emitter wrote
panw_ngfw_eal_raw while its card queried panw_ngfw_traffic_raw), so this is the
reconcile gate.

For every analytics-streamer TTP card it:
  1. Extracts the dataset(s) each detection body queries.
  2. Maps each dataset to the emitter(s) that emit it (by the emitter's _DATASET).
  3. DATASET MATCH — FAIL if a card queries a dataset no emitter emits.
  4. FIELD PRESENCE — WARN if a field the card filters/aggregates on is not among
     the keys any emitter for that dataset emits (approximate; top-level keys only).

Stdlib only; runs on the host. Exit non-zero on any dataset-match FAIL.

Usage: python3 scripts/check-streamer-fidelity.py [--quiet]
"""
import ast
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "core" / "eal_simulator" / "plugins"
TTPS = ROOT / "detection_scanner" / "ttps"

# The analytics-streamer card id range (TTP-2026-0154..0167). Kept explicit so
# the check targets streamer cards, not the whole corpus.
STREAMER_IDS = {f"TTP-2026-{n:04d}" for n in range(154, 168)}

_DATASET_RE = re.compile(r"(?:dataset|preset)\s*=\s*([a-z0-9_]+)", re.IGNORECASE)
# PRECISE source-field extraction: only the field on the LHS of a filter
# comparison, and the group keys after `by`. Excludes `comp ... as <alias>`
# outputs and prose — those are not fields the emitter must supply.
_FILTER_FIELD_RE = re.compile(
    r"\bfilter\s+([a-z_][a-z0-9_.]*)\s*(?:=|!=|>=|<=|>|<|\bcontains\b|\bin\b)",
    re.IGNORECASE)
_BY_RE = re.compile(r"\bby\s+([a-z_][a-z0-9_.]*(?:\s*,\s*[a-z_][a-z0-9_.]*)*)", re.IGNORECASE)
_ALIAS_RE = re.compile(r"\bas\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


def _queried_fields(body):
    """The source fields a body actually reads from the dataset: filter LHS +
    `by` group keys, minus any `comp ... as <alias>` outputs (computed, not
    emitted) and the leading enum/dotted namespaces."""
    aliases = {m.group(1).lower() for m in _ALIAS_RE.finditer(body)}
    fields = set()
    for m in _FILTER_FIELD_RE.finditer(body):
        fields.add(m.group(1).split(".")[0].lower())
    for m in _BY_RE.finditer(body):
        for tok in m.group(1).split(","):
            fields.add(tok.strip().split(".")[0].lower())
    # drop computed aliases, synthetic _time, prose stopwords, and tiny tokens
    stop = {"an", "a", "the", "is", "of", "to", "for", "on", "by", "as", "signal"}
    return {f for f in fields
            if f not in aliases and f != "_time" and not f.startswith("enum")
            and len(f) >= 3 and f not in stop}


def emitter_datasets_and_keys():
    """Map dataset -> set(emitted field keys) across all emitter plugins, by
    AST-parsing each plugin for its _DATASET constant + every dict-literal string
    key (an approximation of the record shape it emits)."""
    ds_to_keys = {}
    ds_to_files = {}
    for f in sorted(PLUGINS.glob("*.py")):
        if f.name.startswith("_"):
            continue
        src = f.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        datasets = set()
        keys = set()
        for node in ast.walk(tree):
            # _DATASET = "..." or dataset default "..."
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in ("_DATASET",) and isinstance(node.value, ast.Constant):
                        datasets.add(str(node.value.value))
            # every dict literal string key
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
            # string-literal dataset markers inside the file (e.g. "dataset": _DATASET handled above)
        for ds in datasets:
            ds_to_keys.setdefault(ds, set()).update(keys)
            ds_to_files.setdefault(ds, set()).add(f.name)
    return ds_to_keys, ds_to_files


def card_bodies(card):
    dets = card.get("detections", {}) or {}
    for k, v in dets.items():
        if isinstance(v, list):
            for o in v:
                if isinstance(o, dict):
                    body = o.get("query") or o.get("logic") or ""
                    if body:
                        yield o.get("name", "?"), body


def main():
    quiet = "--quiet" in sys.argv
    ds_to_keys, ds_to_files = emitter_datasets_and_keys()

    fails = []
    warns = []
    checked = 0
    for path in sorted(TTPS.glob("*.json")):
        try:
            card = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if card.get("id") not in STREAMER_IDS:
            continue
        checked += 1
        cid = card["id"]
        card_datasets = set()
        queried_fields = set()
        for name, body in card_bodies(card):
            for m in _DATASET_RE.finditer(body):
                card_datasets.add(m.group(1))
            queried_fields |= _queried_fields(body)

        # 3. dataset match
        for ds in card_datasets:
            if ds not in ds_to_keys:
                fails.append(f"{cid}: queries dataset '{ds}' which NO emitter emits "
                             f"(emitted: {sorted(ds_to_keys)})")

        # 4. field presence (approximate; only for matched datasets)
        emitted_keys = set()
        for ds in card_datasets:
            emitted_keys |= ds_to_keys.get(ds, set())
        if emitted_keys:  # only if we have an emitter to compare against
            emitted_lower = {k.lower() for k in emitted_keys}
            missing = sorted(f for f in queried_fields if f not in emitted_lower)
            if missing:
                warns.append(f"{cid}: source fields queried but not emitted by its "
                             f"dataset's emitter(s): {missing}")

    print(f"streamer-fidelity: checked {checked} streamer cards")
    print("  DATASET match is the HARD gate (a card querying a dataset no emitter")
    print("  emits is a false-green — the streamer can never fire it).")
    print("  Field WARNs are ADVISORY: cards query XDM-normalized snake_case fields")
    print("  that XSIAM's modeling ruleset derives from the emitter's RAW provider")
    print("  fields at ingest, so a WARN is a review prompt (confirm the raw field")
    print("  normalizes to it), not necessarily a defect. Behavioral gate failures")
    print("  (count-distinct/breadth/action) are NOT caught here — reconcile by hand.")
    print()
    for w in warns:
        print(f"WARN  {w}")
    for e in fails:
        print(f"FAIL  {e}")
    print(f"--- {checked} checked, {len(warns)} field-advisory, {len(fails)} dataset-fail ---")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Regenerate docs/reference/xsiam-platform-apis/14-operation-catalog.md from the packs.

The doc mirror always said it was "generated from the loaded catalog"; until
2026-11-02 nothing generated it, so it was hand-edited and its headline count
could drift from the packs. This is that generator.

    python3 scripts/gen_xsiam_operation_catalog.py           # dry run: print the diff
    python3 scripts/gen_xsiam_operation_catalog.py --check   # exit 1 if the doc is stale
    python3 scripts/gen_xsiam_operation_catalog.py --write   # rewrite the doc

The prose above the first category table is kept verbatim except the headline
count line, which is recomputed. Tables are regenerated: categories and rows in
sorted order, one row per loaded op.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "core"))

from integrations.xsiam.operations.catalog import OperationCatalog  # noqa: E402
from integrations.xsiam.operations.loader import default_ops_dir  # noqa: E402

DOC = os.path.join(REPO, "docs", "reference", "xsiam-platform-apis", "14-operation-catalog.md")
BADGE = {"read": "✅ read", "write": "⚠ write", "destructive": "🚫 destructive"}
HEADLINE_RE = re.compile(r"^> \*\*\d+ operations\*\* across \*\*\d+ categories\*\*.*$", re.M)
# Provenance tags shown in the Doc column when an op has no doc_link.
PROVENANCE_TAGS = ("confirm-tail", "cortex-bot-observed", "povkit-observed", "path-unverified")


def _doc_cell(op) -> str:
    if op.doc_link:
        return f"[↗]({op.doc_link})"
    shown = [t for t in PROVENANCE_TAGS if t in op.tags]
    return " ".join(f"`{t}`" for t in shown)


def _allow_cell(op) -> str:
    """A path param restricted to a closed set says so next to the path."""
    return "".join(f" ({p} ∈ {', '.join(f'`{v}`' for v in vals)})"
                   for p, vals in sorted(op.path_param_allowlist.items()))


def render(current: str, cat: OperationCatalog) -> str:
    ops = cat.all()
    n_read = sum(o.access_class == "read" for o in ops)
    n_write = sum(o.access_class == "write" for o in ops)
    n_destr = sum(o.access_class == "destructive" for o in ops)
    headline = (f"> **{len(ops)} operations** across **{len(cat.categories())} categories** "
                f"— {n_read} read · {n_write} write · {n_destr} destructive.")
    if not HEADLINE_RE.search(current):
        raise SystemExit("headline count line not found; refusing to guess where it goes")

    # Preamble = everything before the first category heading after the gating table.
    first_table = None
    for m in re.finditer(r"^## (.+)$", current, re.M):
        if m.group(1) != "Access classes & gating":
            first_table = m.start()
            break
    if first_table is None:
        raise SystemExit("no category section found; refusing to rewrite")
    preamble = HEADLINE_RE.sub(headline, current[:first_table], count=1)

    out = [preamble.rstrip("\n") + "\n"]
    for category in cat.categories():
        out.append(f"\n## {category}\n")
        out.append("| Op ID | Method | Path | Access | Doc |\n")
        out.append("|---|---|---|---|---|\n")
        for op in sorted(cat.list_for_category(category), key=lambda o: o.op_id):
            out.append(f"| `{op.op_id}` | {op.method} | `{op.path}`{_allow_cell(op)} | "
                       f"{BADGE[op.access_class]} | {_doc_cell(op)} |\n")
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite the doc")
    mode.add_argument("--check", action="store_true", help="exit 1 if the doc is stale")
    args = ap.parse_args()

    cat = OperationCatalog()
    cat.load(default_ops_dir(REPO))
    with open(DOC, encoding="utf-8") as fh:
        current = fh.read()
    fresh = render(current, cat)

    if fresh == current:
        print(f"up to date: {cat.count()} operations")
        return 0
    if args.write:
        with open(DOC, "w", encoding="utf-8") as fh:
            fh.write(fresh)
        print(f"wrote {os.path.relpath(DOC, REPO)}: {cat.count()} operations")
        return 0
    sys.stdout.writelines(difflib.unified_diff(
        current.splitlines(True), fresh.splitlines(True), "doc (current)", "doc (generated)"))
    if args.check:
        print("\nstale: run with --write", file=sys.stderr)
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())

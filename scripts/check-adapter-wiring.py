#!/usr/bin/env python3
# ==============================================================================
# check-adapter-wiring.py — de-hand-rolling lint (GAP-ADAPT-02 residual gate)
#
# The Tool Adapter Framework exists so scenarios reference a declarative pack
# (``external_tools[].adapter_ref: TOOL-X``) instead of hand-rolling raw CLI.
# Referencing the pack is what engages the safety/consent gate, the self-install
# machinery in push bundles, and the identity-harness wiring. A scenario that
# names a tool for which an adapter pack EXISTS but never wires that pack quietly
# bypasses all of it.
#
# This lint flags exactly that case and nothing else. For every scenario it
# classifies each ``external_tools[]`` entry that lacks ``adapter_ref`` as:
#
#   * CANDIDATE — a non-reference-only adapter pack matches the tool by name /
#     install-binary / adapter-id, and NO other entry in the same scenario wires
#     that adapter. This is a genuine de-hand-rolling gap → fails --strict.
#   * redundant — the matching adapter IS already wired elsewhere in the same
#     scenario (a duplicate bare listing of an already-wired tool). Informational.
#   * generic  — no adapter pack matches (curl, jq, aws-cli, the EAL CLI, …).
#     Correct as-is; there is nothing to wire.
#
# Reference-only packs (tier 5 external-only tools the DC brings themselves, and
# c2-frameworks that are never auto-staged) are intentionally excluded from the
# CANDIDATE set — a scenario is not required to wire them.
#
# Exit code:
#   0 — no CANDIDATE gaps (or --warn-only)
#   1 — at least one CANDIDATE gap and --strict (the default)
#
# Self-contained: pure Python 3.11 + PyYAML (already a validator/loader dep). No
# FastAPI/app import, so it runs on a clean runner or inside the SimCore image.
#
# Usage:
#   scripts/check-adapter-wiring.py                 # strict gate (default)
#   scripts/check-adapter-wiring.py --warn-only     # report, always exit 0
#   scripts/check-adapter-wiring.py --list          # also list redundant/generic
#   CORTEXSIM_BASE_DIR=/repo scripts/check-adapter-wiring.py
# ==============================================================================
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

import yaml

REPO_ROOT = os.environ.get(
    "CORTEXSIM_BASE_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
PACKS_DIR = os.path.join(REPO_ROOT, "tools", "packs")
SCENARIOS_DIR = os.path.join(REPO_ROOT, "scenarios")

_ISATTY = sys.stdout.isatty()
RED = "\033[0;31m" if _ISATTY else ""
GREEN = "\033[0;32m" if _ISATTY else ""
YELLOW = "\033[1;33m" if _ISATTY else ""
BOLD = "\033[1m" if _ISATTY else ""
NC = "\033[0m" if _ISATTY else ""


def _norm(s: str | None) -> str:
    """Collapse a tool name/binary to a comparable token (lowercase alnum)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load_yaml(path: str, parse_failures: list[str] | None = None) -> dict | None:
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        # A file that cannot be parsed is NOT silently skipped: the boot loader
        # would REJECT it, so a lint that reads it as "nothing to wire" would
        # green a file the engine refuses. Record it so the gate fails closed.
        if parse_failures is not None:
            parse_failures.append(f"{path}: {exc}")
        return None
    return doc if isinstance(doc, dict) else None


def build_adapter_index(parse_failures: list[str] | None = None) -> tuple[dict[str, str], int]:
    """Map every comparable token (name / install-binary / adapter-id suffix) to
    its adapter id, EXCLUDING reference-only packs (tier 5 or c2-framework).
    First writer wins so a canonical name is not shadowed by a suffix collision.

    Returns ``(tok2id, pack_file_count)`` — the count is how many ``*.yml`` were
    globbed under the packs dir, so main() can refuse an empty/missing packs set.
    """
    tok2id: dict[str, str] = {}
    pack_files = 0
    for path in sorted(glob.glob(os.path.join(PACKS_DIR, "*.yml"))):
        if os.path.basename(path).startswith("_"):
            continue  # _schema.yml et al.
        pack_files += 1
        d = _load_yaml(path, parse_failures)
        if not d or not d.get("adapter_id"):
            continue
        # Mirror ToolAdapterSchema.reference_only: tier 5 or c2-framework.
        if d.get("tier") == 5 or d.get("safety_class") == "c2-framework":
            continue
        aid = d["adapter_id"]
        binary = (d.get("install") or {}).get("binary")
        tokens = {
            _norm(d.get("name")),
            _norm(binary),
            _norm(aid.replace("TOOL-", "")),
        }
        for tok in tokens:
            if tok:
                tok2id.setdefault(tok, aid)
    return tok2id, pack_files


def scan() -> tuple[list[tuple[str, str, str]], int, int, dict]:
    """Return (candidates, redundant_count, generic_count, stats).

    candidates: (scenario_relpath, tool_name, matched_adapter_id).
    stats: discovery counts + parse failures so main() can fail closed when the
    lint scanned nothing (a zero is degraded, not ok) or hit an unreadable file.
    """
    parse_failures: list[str] = []
    tok2id, pack_files = build_adapter_index(parse_failures)
    candidates: list[tuple[str, str, str]] = []
    redundant = 0
    generic = 0
    scenario_files = 0

    for path in sorted(glob.glob(os.path.join(SCENARIOS_DIR, "**", "*.yml"), recursive=True)):
        if path.endswith("_schema.yml"):
            continue
        scenario_files += 1
        d = _load_yaml(path, parse_failures)
        if not d:
            continue
        ets = d.get("external_tools") or []
        wired_here = {
            et["adapter_ref"]
            for et in ets
            if isinstance(et, dict) and et.get("adapter_ref")
        }
        rel = os.path.relpath(path, SCENARIOS_DIR)
        for et in ets:
            if not isinstance(et, dict) or et.get("adapter_ref"):
                continue
            aid = tok2id.get(_norm(et.get("name") or et.get("tool")))
            if aid is None:
                generic += 1
            elif aid in wired_here:
                redundant += 1
            else:
                candidates.append((rel, et.get("name") or et.get("tool") or "?", aid))
    stats = {
        "pack_files": pack_files,
        "scenario_files": scenario_files,
        "parse_failures": parse_failures,
    }
    return candidates, redundant, generic, stats


def main() -> int:
    ap = argparse.ArgumentParser(description="De-hand-rolling adapter_ref lint.")
    ap.add_argument("--warn-only", action="store_true",
                    help="report findings but always exit 0")
    ap.add_argument("--list", action="store_true",
                    help="also print redundant/generic tallies")
    args = ap.parse_args()

    candidates, redundant, generic, stats = scan()

    # Fail closed before interpreting any result: a lint that scanned nothing, or
    # that could not read a file the loader would reject, must not read as PASS.
    hard_errors: list[str] = []
    if stats["pack_files"] == 0:
        hard_errors.append(
            f"no adapter packs found under {PACKS_DIR} — is CORTEXSIM_BASE_DIR "
            f"({REPO_ROOT}) the repo root?"
        )
    if stats["scenario_files"] == 0:
        hard_errors.append(
            f"no scenarios found under {SCENARIOS_DIR} — is CORTEXSIM_BASE_DIR "
            f"({REPO_ROOT}) the repo root?"
        )
    for pf in stats["parse_failures"]:
        hard_errors.append(f"unparseable file (the boot loader would REJECT it): {pf}")
    if hard_errors:
        print(f"{BOLD}Adapter-wiring lint — scenarios vs tools/packs{NC}")
        for e in hard_errors:
            print(f"  {RED}✗{NC} {e}")
        print(f"\n{RED}ERROR{NC} — the lint could not run against a complete, "
              f"readable corpus; refusing to report PASS.")
        return 2

    print(f"{BOLD}Adapter-wiring lint — scenarios vs tools/packs{NC}")
    if candidates:
        print(f"{RED}CANDIDATE gaps (adapter exists but scenario never wires it):{NC}")
        for rel, name, aid in candidates:
            print(f"  {RED}✗{NC} {rel}  ::  {name}  →  add  adapter_ref: {aid}")
    else:
        print(f"  {GREEN}✓ no de-hand-rolling gaps — every tool with an adapter is wired{NC}")

    if args.list:
        print(f"\n  {YELLOW}redundant bare entries (adapter already wired in-scenario): {redundant}{NC}")
        print(f"  generic entries (no adapter pack — correct as-is): {generic}")

    print(f"\n{BOLD}Summary:{NC} "
          f"{len(candidates)} candidate · {redundant} redundant · {generic} generic")

    if candidates and not args.warn_only:
        print(f"{RED}FAIL{NC} — wire the adapter(s) above via external_tools[].adapter_ref, "
              f"or run with --warn-only.")
        return 1
    print(f"{GREEN}PASS{NC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

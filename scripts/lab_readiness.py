#!/usr/bin/env python3
"""Lab-readiness classifier — which scenarios will actually emit signal in a lab.

WHY THIS EXISTS
---------------
CortexSim's defining failure mode is *a step that looks like it ran and did
not*, because in a POV report an absent detection reads as "Cortex missed it" —
a manufactured false negative on the customer's own stack. Today the only guard
is operator discipline: `docs/reference/launching-a-simulation.md` tells a DC to
"avoid the 141 echo/printf/touch-only steps" by hand-grepping scenario bodies,
and the payload-shelf / tier-4-egress story (a tool that never arrives under
default-deny) is spread across three subsystems. Neither is a surface a DC can
read before picking a scenario for a customer demo.

This script turns that manual discipline into a computed, ranked manifest. For
every scenario it answers three questions a DC must not get wrong in front of a
customer:

  1. SIGNAL  — do the detection-bearing steps actually produce telemetry a
     sensor could catch, or are they `echo`/`printf`/`touch` narration that
     declares `expected_detections` nothing emits?
  2. TOOLING — does the scenario need a tool fetched from the public internet on
     the target (tier-4 `artifact_exempt` adapter, or an `install_inline` curl),
     which a default-deny customer lab blocks — or is every tool a stock binary
     or a digest-pinned payload-shelf artifact that works offline?
  3. GATING  — does it require launch consent (a c2-framework or a
     `simulation_authorized` scenario), and is its signal delivered by the agent
     (endpoint causality) or by the EAL simulator (network/NGFW egress from
     SimCore's own process, which the NGFW only sees if positioned correctly)?

From those it assigns a lab-readiness tier:

  GREEN  (lab-ready)  — real signal AND every tool is stock/shelf-backed. Runs
                        on a provisioned target under default-deny egress.
  YELLOW (provision)  — real signal, but needs target egress for a tool or a
                        consent gate. Runnable once you pre-stage the tool or
                        grant egress/consent.
  RED    (tabletop)   — signal-free: declares detections but no step invokes any
                        real binary. These MUST NOT be run expecting detections;
                        seeding their Result rows manufactures an all-missed POV.

NOTE ON HONESTY: a GREEN tier means "will emit the authored telemetry on a
correctly provisioned target", NEVER "the detection was observed to fire".
tenant-verified is 0. Authored is not proven.

The "no-signal" step definition matches and extends the one documented in
`docs/reference/lab-runbook.md` §3: a step is no-signal if it has
`expected_detections` and every command clause is echo/printf/touch/:/true or a
comment. All other clauses (cat, find, curl, python3, nmap, runuser, an EAL
plugin invocation, ...) count as real signal.

A `channel: eal` step is judged by its channel, not its command (a descriptive
no-op): SimCore POSTs its records in-process at launch, so it counts as real
signal, makes the scenario EAL-delivered, and is a launch consent gate (live
only with consent, an authorized_by and a keyed collector target).

USAGE
-----
  python3 scripts/lab_readiness.py            # human summary to stdout
  python3 scripts/lab_readiness.py --md FILE  # write markdown report
  python3 scripts/lab_readiness.py --json FILE
  python3 scripts/lab_readiness.py --plane EDR --tier GREEN   # filter
  python3 scripts/lab_readiness.py --strict   # exit 1 if any active scenario is
                                              # signal-free (RED) — advisory gate
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# The classifier is core/engine/lab_readiness.py, so SimCore reads the same tiers
# at runtime (the console's ★); this script only renders the manifest. core/ goes
# first on sys.path so `engine` can never resolve to anything under scripts/.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from engine.lab_readiness import (  # noqa: E402,F401  (re-exported for tests/scripts)
    _clause_is_signal,
    _clauses,
    _iter_scenarios,
    _load_packs,
    _step_produces_signal,
    build,
    classify,
    summarize,
)


def render_md(rows: list[dict], summary: dict) -> str:
    L = []
    L.append("# Lab-readiness manifest")
    L.append("")
    L.append("> **Generated** by `scripts/lab_readiness.py` — do not hand-edit.")
    L.append("> A tier is about whether a scenario will *emit its authored")
    L.append("> telemetry on a correctly provisioned target*, never whether a")
    L.append("> Cortex detection was observed to fire. **tenant-verified is 0;")
    L.append("> authored is not proven.**")
    L.append("")
    L.append("## What the tiers mean")
    L.append("")
    L.append("- **GREEN — lab-ready.** Real signal on every detection-bearing step, and")
    L.append("  every tool is a stock binary or a digest-pinned payload-shelf artifact.")
    L.append("  Runs on a provisioned target under default-deny egress. Start a demo here.")
    L.append("- **YELLOW — provision first.** Real signal, but the scenario needs a tool")
    L.append("  fetched from the internet on the target, or a launch consent gate. Pre-stage")
    L.append("  the tool (or allow egress) / grant consent, then it runs.")
    L.append("- **RED — tabletop only.** Signal-free: it declares detections but no step")
    L.append("  invokes a real binary. **Do not run it expecting detections** — seeding its")
    L.append("  Result rows manufactures an all-missed POV. Convert (drive real telemetry")
    L.append("  via signalbench / telemetry-replay) or present it as a tabletop walk-through.")
    L.append("")
    st = summary
    L.append("## Summary")
    L.append("")
    L.append(f"- **Scenarios:** {st['scenarios_total']}  "
             f"(GREEN {st['by_tier']['GREEN']} · YELLOW {st['by_tier']['YELLOW']} · RED {st['by_tier']['RED']})")
    L.append(f"- **Steps:** {st['total_steps']} total · "
             f"{st['detection_steps_without_signal']} detection-bearing steps produce no real signal")
    L.append(f"- **Need target egress for a tool:** {st['scenarios_needing_target_egress']} scenarios")
    L.append(f"- **Consent-gated:** {st['scenarios_consent_gated']} scenarios")
    L.append(f"- **EAL-delivered (network/NGFW signal from SimCore, not the agent):** {st['eal_delivered']} scenarios")
    if st["signal_free_scenarios"]:
        L.append(f"- **RED / signal-free (tabletop):** {', '.join(st['signal_free_scenarios'])}")
    L.append("")
    for tier in ("GREEN", "YELLOW", "RED"):
        trows = [r for r in rows if r["tier"] == tier]
        if not trows:
            continue
        L.append(f"## {tier} — {len(trows)} scenarios")
        L.append("")
        L.append("| Scenario | Plane | Types | Signal | Delivery | Notes |")
        L.append("|---|---|---|---:|---|---|")
        for r in trows:
            types = "/".join(r["detection_types"])
            sig = f"{r['steps_real_signal']}/{r['steps_total']}"
            notes = []
            if r["needs_target_egress"]:
                eg = ", ".join(r["egress_tools"]) or "install_inline"
                notes.append(f"egress: {eg}")
            if r["shelf_backed_tools"]:
                notes.append(f"shelf: {', '.join(r['shelf_backed_tools'])}")
            if r["consent_gated"]:
                notes.append("consent")
            if r["c2_tools"]:
                notes.append(f"c2: {', '.join(r['c2_tools'])}")
            if r["unknown_adapter_refs"]:
                notes.append(f"UNKNOWN adapter: {', '.join(r['unknown_adapter_refs'])}")
            if tier == "RED":
                notes.append(r["reason"])
            L.append(f"| {r['scenario_id']} | {r['plane']} | {types} | {sig} | "
                     f"{r['delivery']} | {'; '.join(notes)} |")
        L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", metavar="FILE", help="write manifest JSON")
    ap.add_argument("--md", metavar="FILE", help="write markdown report")
    ap.add_argument("--plane", help="filter to one plane")
    ap.add_argument("--tier", choices=["GREEN", "YELLOW", "RED"], help="filter to one tier")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any active scenario is signal-free (RED)")
    args = ap.parse_args(argv)

    rows = build()
    summary = summarize(rows)

    view = rows
    if args.plane:
        view = [r for r in view if (r["plane"] or "").upper() == args.plane.upper()]
    if args.tier:
        view = [r for r in view if r["tier"] == args.tier]

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"summary": summary, "scenarios": rows}, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"wrote {args.json}")
    if args.md:
        with open(args.md, "w") as f:
            f.write(render_md(rows, summary) + "\n")
        print(f"wrote {args.md}")

    if not args.json and not args.md:
        st = summary
        print(f"scenarios {st['scenarios_total']}  "
              f"GREEN {st['by_tier']['GREEN']} · YELLOW {st['by_tier']['YELLOW']} · RED {st['by_tier']['RED']}")
        print(f"steps {st['total_steps']} · detection-steps-without-signal "
              f"{st['detection_steps_without_signal']} · need-egress "
              f"{st['scenarios_needing_target_egress']} · consent-gated "
              f"{st['scenarios_consent_gated']} · eal-delivered {st['eal_delivered']}")
        if st["signal_free_scenarios"]:
            print("RED/signal-free:", ", ".join(st["signal_free_scenarios"]))
        if args.plane or args.tier:
            print()
            for r in view:
                print(f"  {r['tier']:6} {r['scenario_id']:16} sig {r['steps_real_signal']}/{r['steps_total']:2} "
                      f"{r['delivery']:5} {r['reason']}")

    if args.strict:
        active_red = [r["scenario_id"] for r in rows if r["tier"] == "RED" and r["status"] == "active"]
        if active_red:
            sys.stderr.write(
                "STRICT: signal-free scenarios are status: active — they seed "
                "un-fireable detection Results:\n  " + "\n  ".join(active_red) + "\n")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

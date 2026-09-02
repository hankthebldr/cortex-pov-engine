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
import glob
import json
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML required: pip install pyyaml\n")
    raise

BASE = os.environ.get("CORTEXSIM_BASE_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_GLOB = os.path.join(BASE, "scenarios", "**", "*.yml")
PACKS_GLOB = os.path.join(BASE, "tools", "packs", "*.yml")

# A clause counts as "no-signal" (produces no sensor-visible telemetry) when it
# is one of these. `touch` is included to match the corpus's own documented
# echo/printf/touch definition, though it does emit a weak FILE_WRITE event.
_NO_SIGNAL_RE = re.compile(r"^(echo|printf|:|true|touch|sleep|export|cd|set|source)\b|^#")
# A guarded availability probe is NOT an attack action — it only asks "is this
# tool present / what version is it". A SAFE-MODE narration step that pairs an
# `echo` with `(esxcli --version || true)` produces no attack telemetry, so
# these clauses are no-signal too. Presence probes (`command -v`, `which`,
# `type`) and a first-argument `--version`/`-V`/`--help`/`version` probe.
_PROBE_RE = re.compile(
    r"^(command\s+-v|which|type|hash)\b"
    r"|^\S+\s+(--version|-V|--help|-h|-v|version)\b",
    re.IGNORECASE,
)


def _load_packs() -> dict:
    """adapter_id -> {tier, shelf_backed, artifact_exempt, exempt_reason, c2}."""
    packs = {}
    for path in glob.glob(PACKS_GLOB):
        if path.endswith("_schema.yml"):
            continue
        try:
            doc = yaml.safe_load(open(path))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        aid = doc.get("adapter_id")
        if not aid:
            continue
        install = doc.get("install") or {}
        exempt = install.get("artifact_exempt") or {}
        packs[aid] = {
            "tier": doc.get("tier"),
            "shelf_backed": bool(install.get("artifact")),
            "artifact_exempt": bool(exempt),
            "exempt_reason": (exempt or {}).get("reason_code"),
            "c2": doc.get("safety_class") == "c2-framework",
            "safety_class": doc.get("safety_class"),
        }
    return packs


def _clauses(command: str) -> list[str]:
    """Split a command into `&&`/`||`/`;`/newline-delimited clauses, IGNORING
    delimiters that appear inside single- or double-quoted strings.

    This matters: a SAFE-MODE step like `echo 'config set && esxcli reload'`
    carries `&&` inside the narration string — a quote-blind split (like the
    one in docs/reference/lab-runbook.md §3) would treat the quoted text as a
    separate real command and mis-score the step as producing signal.
    """
    out, buf, quote, comment, i = [], [], None, False, 0
    s = command or ""
    while i < len(s):
        ch = s[i]
        if comment:
            # a shell `#` comment runs to end of line; delimiters inside it are
            # narration text, not operators
            if ch == "\n":
                comment = False
                out.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
            i += 1
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "#" and (not buf or buf[-1].isspace()):
            comment = True
            buf.append(ch)
            i += 1
            continue
        if ch in ";\n":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        if ch in "&|" and i + 1 < len(s) and s[i + 1] == ch:  # && or ||
            out.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [c.strip() for c in out if c.strip()]


def _clause_is_signal(clause: str) -> bool:
    """True if this clause performs a real, telemetry-producing action.

    False for echo/printf/touch narration, shell bookkeeping, and *guarded
    availability probes* (`command -v X`, `which X`, `X --version`) — a presence
    or version check is not an attack action, so a SAFE-MODE step that pairs an
    `echo` with `(esxcli --version || true)` still emits no attack telemetry.
    """
    c = clause.strip()
    # strip wrapping parens/subshell + trailing redirections so the probe/echo
    # underneath is matched: "(esxcli --version 2>/dev/null" -> "esxcli --version"
    c = c.lstrip("({ ").rstrip(")} ")
    c = re.sub(r"\s*\d?>{1,2}\s*\S+\s*$", "", c).strip()
    if not c:
        return False
    if _NO_SIGNAL_RE.match(c):
        return False
    if _PROBE_RE.match(c):
        return False
    return True


def _step_produces_signal(command: str) -> bool:
    """True if any clause performs a real, telemetry-producing action."""
    return any(_clause_is_signal(c) for c in _clauses(command))


def _iter_scenarios():
    for path in sorted(glob.glob(SCENARIOS_GLOB, recursive=True)):
        if path.endswith("_schema.yml"):
            continue
        try:
            docs = list(yaml.safe_load_all(open(path)))
        except Exception:
            continue
        for doc in docs:
            if isinstance(doc, dict) and doc.get("scenario_id"):
                yield path, doc


def classify(doc: dict, packs: dict) -> dict:
    steps = [s for s in (doc.get("steps") or []) if isinstance(s, dict)]
    steps_total = len(steps)
    steps_with_det = 0
    steps_real_signal = 0            # any step (whether or not it declares det)
    det_steps_no_signal = 0          # declares detections but emits nothing
    identities = set()
    interpreters = set()

    for s in steps:
        cmd = s.get("command") or ""
        has_det = bool(s.get("expected_detections"))
        produces = _step_produces_signal(cmd)
        if produces:
            steps_real_signal += 1
        if has_det:
            steps_with_det += 1
            if not produces:
                det_steps_no_signal += 1
        ident = s.get("identity")
        if ident and ident not in ("direct", "root"):
            identities.add(ident)
        for it in s.get("requires_interpreters") or []:
            interpreters.add(it)

    # scenario-level execution_identity options also require provisioning
    ex = doc.get("execution_identity") or {}
    for opt in ex.get("options") or []:
        if opt not in ("direct", "root"):
            identities.add(opt)

    # tool dependency
    egress_tools, shelf_tools, c2_tools, unknown_tools = [], [], [], []
    inline_egress = False
    for t in doc.get("external_tools") or []:
        if not isinstance(t, dict):
            continue
        if t.get("install_inline"):
            inline_egress = True
        ref = t.get("adapter_ref")
        if not ref:
            continue
        pk = packs.get(ref)
        if pk is None:
            unknown_tools.append(ref)
            continue
        if pk["c2"]:
            c2_tools.append(ref)
        if pk["tier"] == 4:
            if pk["shelf_backed"]:
                shelf_tools.append(ref)
            elif pk["artifact_exempt"]:
                egress_tools.append(ref)  # installs from internet on target

    consent = doc.get("consent") or {}
    consent_gated = bool(consent.get("simulation_authorized") or consent.get("c2_authorized")) or bool(c2_tools)

    # is signal delivered off-command by an EAL plugin? (real network/log signal
    # even though the shell step may look thin)
    eal_driven = any(
        "eal_simulator" in (s.get("command") or "") or "--live" in (s.get("command") or "")
        for s in steps
    )
    if eal_driven and steps_real_signal == 0:
        # the EAL invocation IS the real signal even if regex under-counted it
        steps_real_signal = max(steps_real_signal, 1)
        det_steps_no_signal = max(0, det_steps_no_signal - 1)

    signal_free = steps_with_det > 0 and steps_real_signal == 0
    needs_egress = bool(egress_tools) or inline_egress

    if signal_free:
        tier = "RED"
        reason = "signal-free: declares detections but no step invokes a real binary (tabletop only)"
    elif needs_egress or consent_gated:
        tier = "YELLOW"
        bits = []
        if needs_egress:
            bits.append("needs target egress for a tool (pre-stage or allow egress)")
        if consent_gated:
            bits.append("launch consent required")
        reason = "; ".join(bits)
    else:
        tier = "GREEN"
        reason = "real signal, all tools stock/shelf-backed — runs offline on a provisioned target"

    signal_ratio = round(steps_real_signal / steps_total, 3) if steps_total else 0.0
    return {
        "scenario_id": doc.get("scenario_id"),
        "name": doc.get("name"),
        "plane": doc.get("plane"),
        "status": doc.get("status"),
        "detection_types": doc.get("detection_types") or [],
        "tier": tier,
        "reason": reason,
        "delivery": "eal" if eal_driven else "agent",
        "steps_total": steps_total,
        "steps_with_detections": steps_with_det,
        "steps_real_signal": steps_real_signal,
        "detection_steps_without_signal": det_steps_no_signal,
        "signal_ratio": signal_ratio,
        "consent_gated": consent_gated,
        "needs_target_egress": needs_egress,
        "egress_tools": sorted(set(egress_tools)),
        "shelf_backed_tools": sorted(set(shelf_tools)),
        "c2_tools": sorted(set(c2_tools)),
        "unknown_adapter_refs": sorted(set(unknown_tools)),
        "service_identities": sorted(identities),
        "requires_interpreters": sorted(interpreters),
    }


def build() -> list[dict]:
    packs = _load_packs()
    rows = [classify(doc, packs) for _, doc in _iter_scenarios()]
    order = {"GREEN": 0, "YELLOW": 1, "RED": 2}
    rows.sort(key=lambda r: (order[r["tier"]], -r["signal_ratio"], r["scenario_id"] or ""))
    return rows


def summarize(rows: list[dict]) -> dict:
    by_tier = {"GREEN": 0, "YELLOW": 0, "RED": 0}
    for r in rows:
        by_tier[r["tier"]] += 1
    total_steps = sum(r["steps_total"] for r in rows)
    no_signal_det_steps = sum(r["detection_steps_without_signal"] for r in rows)
    return {
        "scenarios_total": len(rows),
        "by_tier": by_tier,
        "signal_free_scenarios": sorted(r["scenario_id"] for r in rows if r["tier"] == "RED"),
        "total_steps": total_steps,
        "detection_steps_without_signal": no_signal_det_steps,
        "scenarios_needing_target_egress": sum(1 for r in rows if r["needs_target_egress"]),
        "scenarios_consent_gated": sum(1 for r in rows if r["consent_gated"]),
        "eal_delivered": sum(1 for r in rows if r["delivery"] == "eal"),
    }


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

#!/usr/bin/env python3
"""classify.py — turn a Tier-D run record into an interpretable verdict.

A red run is not information. "Which KIND of red" is.

Three classes, and conflating them is the most damaging thing this product can
do to a customer engagement:

  ENGINE       the beacon, orchestrator or identity harness broke. A real
               CortexSim defect. This is the ONLY class that fails the harness.
  ENVIRONMENT  the target could not support the step — an account with no login
               shell, a missing tool, no egress to fetch one. THE TTP NEVER RAN.
               Left unclassified, this surfaces in a POV report as "Cortex
               missed it" when nothing was ever executed for Cortex to miss.
  TTP          the technique ran and legitimately did not succeed. Real signal.

Exit code: 0 unless an ENGINE-class failure was observed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# --- ENVIRONMENT signatures -------------------------------------------------
# Each is a real message observed from a real failed run, not a guess. The
# canonical example is the one that motivated this whole harness:
#   "This account is currently not available." — www-data shipped with
#   /usr/sbin/nologin, so `runuser -l www-data` died in 7ms and the TTP never
#   executed, while the run simply read "failed".
ENVIRONMENT_PATTERNS = [
    (r"This account is currently not available",
     "identity has no login shell on the target (nologin)"),
    (r"runuser: warning: cannot change directory to (\S+)",
     "identity's home directory is absent on the target"),
    (r"runuser: user (\S+) does not exist",
     "identity account does not exist on the target"),
    (r"su: user (\S+) does not exist",
     "identity account does not exist on the target"),
    (r"(command not found|No such file or directory: )",
     "a tool the step invokes is not installed on the target"),
    (r"(Could not resolve host|Temporary failure in name resolution|"
     r"Connection timed out|Network is unreachable|SSL certificate problem)",
     "step needs public-internet egress the target does not have"),
    (r"PAYLOAD_NOT_STAGED",
     "a shelf-backed tool was never staged on this SimCore"),
    (r"Permission denied",
     "target filesystem/permission state does not support the step"),
]

# --- ENGINE signatures ------------------------------------------------------
# These indicate CortexSim itself misbehaved rather than the target.
ENGINE_PATTERNS = [
    (r"PAYLOAD_PIN_MISMATCH",
     "staged payload digest did not match its pin — integrity failure"),
    (r"IDENTITY NOT HONOURED",
     "the identity harness could not honour a declared identity"),
    (r"(Traceback \(most recent call last\)|panic: |runtime error:)",
     "an unhandled exception in the engine or beacon"),
    (r"RUN FAILED ON RESTART",
     "the run was orphaned by a SimCore restart"),
    (r"ARTIFACT STAGING FAILED",
     "artifact staging failed inside the beacon"),
]

STEP_RE = re.compile(
    r"=== STEP (?P<n>\d+)/(?P<total>\d+) · (?P<sid>\S+) · (?P<tech>\S+) · "
    r"identity=(?P<identity>\S+) ===(?P<body>.*?)(?=(?:=== STEP )|\Z)",
    re.S,
)
EXIT_RE = re.compile(r"exit_code=(-?\d+)")


def classify_step(body: str) -> tuple[str, str]:
    """Return (class, reason) for one step's output body."""
    for pat, reason in ENGINE_PATTERNS:
        if re.search(pat, body):
            return "ENGINE", reason
    for pat, reason in ENVIRONMENT_PATTERNS:
        if re.search(pat, body):
            return "ENVIRONMENT", reason
    return "TTP", "step executed and returned a non-zero exit"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="path to the saved run.json")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", required=True, help="where to write verdict.json")
    args = ap.parse_args()

    with open(args.run, encoding="utf-8") as fh:
        run = json.load(fh)

    output = run.get("output") or ""
    if not isinstance(output, str):
        output = json.dumps(output)

    steps = []
    for m in STEP_RE.finditer(output):
        body = m.group("body")
        exit_m = EXIT_RE.search(body)
        code = int(exit_m.group(1)) if exit_m else None
        if code == 0:
            klass, reason = "OK", "step exited 0"
        else:
            klass, reason = classify_step(body)
        steps.append({
            "n": int(m.group("n")),
            "step_id": m.group("sid"),
            "technique": m.group("tech"),
            "identity": m.group("identity"),
            "exit_code": code,
            "class": klass,
            "reason": reason,
        })

    declared_total = int(STEP_RE.search(output).group("total")) if steps else 0
    observed = len(steps)

    counts = {k: sum(1 for s in steps if s["class"] == k)
              for k in ("OK", "ENGINE", "ENVIRONMENT", "TTP")}

    # A step the beacon never reported at all is its own problem: the run
    # stopped early. Fail-fast is by design, so this is not automatically an
    # engine fault — but it must be visible, not silently absent.
    unreported = max(0, declared_total - observed)

    verdict = {
        "scenario": args.scenario,
        "run_id": run.get("run_id") or run.get("id"),
        "run_status": run.get("status"),
        "tc_verdict": run.get("tc_verdict"),
        "steps_declared": declared_total,
        "steps_reported": observed,
        "steps_unreported": unreported,
        "counts": counts,
        "steps": steps,
        "harness_verdict": "FAIL" if counts["ENGINE"] else "PASS",
        "interpretation": (
            "ENGINE failures mean CortexSim is broken. ENVIRONMENT failures mean "
            "the TTP never ran — do NOT report those as a detection miss. TTP "
            "failures are real technique outcomes."
        ),
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=2)

    # --- operator-readable summary ------------------------------------------
    print()
    print(f"  scenario        {args.scenario}")
    print(f"  run status      {verdict['run_status']}   tc_verdict={verdict['tc_verdict']}")
    print(f"  steps           {observed} reported / {declared_total} declared"
          + (f"   ({unreported} never reported — run stopped early)" if unreported else ""))
    print()
    for s in steps:
        mark = {"OK": "\033[0;32m✓\033[0m", "ENGINE": "\033[0;31m✗\033[0m",
                "ENVIRONMENT": "\033[0;33m~\033[0m", "TTP": "\033[0;34m·\033[0m"}[s["class"]]
        print(f"   {mark} step {s['n']} [{s['identity']}] {s['technique']}"
              f"  exit={s['exit_code']}  {s['class']}")
        if s["class"] != "OK":
            print(f"       └─ {s['reason']}")
    print()
    print(f"  OK {counts['OK']} · ENVIRONMENT {counts['ENVIRONMENT']} "
          f"· TTP {counts['TTP']} · ENGINE {counts['ENGINE']}")
    print()
    if counts["ENGINE"]:
        print("  \033[0;31mHARNESS FAIL\033[0m — an ENGINE-class failure means CortexSim itself is broken.")
        return 1
    if counts["ENVIRONMENT"]:
        print("  \033[0;33mHARNESS PASS, with unrun steps\033[0m — the engine worked; the target could not")
        print("  support every step. Those steps produced NO signal, so their absent detections")
        print("  must NOT be reported as a coverage gap.")
        return 0
    print("  \033[0;32mHARNESS PASS\033[0m — full pull-mode lifecycle exercised end to end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

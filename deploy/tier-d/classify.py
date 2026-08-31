#!/usr/bin/env python3
"""classify.py — turn a Tier-D run record into an interpretable verdict.

A red run is not information. "Which KIND of red" is.

Three classes, and conflating them is the most damaging thing this product can
do to a customer engagement:

  ENGINE       the beacon, orchestrator or identity harness broke. A real
               CortexSim defect. This is the ONLY class that fails the harness
               outright.
  ENVIRONMENT  the target could not support the step — an account with no login
               shell, a missing tool, no egress to fetch one. THE TTP NEVER RAN.
               Left unclassified, this surfaces in a POV report as "Cortex
               missed it" when nothing was ever executed for Cortex to miss.
  TTP          the technique ran and legitimately did not succeed. Real signal.

A fourth outcome, INCONCLUSIVE, exists at the RUN level (not the step level):
the harness refuses to say PASS when it cannot actually prove the lifecycle
ran — zero steps ever reported, some declared steps never reported at all, or
the run never reached a state that means "this is over". Reporting PASS in
any of those cases is the exact defect class this harness exists to eliminate
in the product it is testing, and it must not commit it itself.

Exit code: 0 only for a genuine PASS (including "PASS, with unrun steps" —
ENVIRONMENT-classified steps are an honest, fully-accounted-for outcome).
Non-zero for FAIL (an ENGINE-class failure — a real CortexSim defect) and for
INCONCLUSIVE (the harness cannot prove what happened).
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
#
# NOTE: "Permission denied" is deliberately NOT a blanket entry here — see
# _permission_denied_environment_reason below (I2).
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
    (r"RUNTIME_DEPENDENCY_MISSING",
     "a step-declared interpreter (docs/design/agent-runtime-dependencies.md) "
     "was not found on the target and no authorized install could supply it — "
     "the step's own command was NEVER executed, so any absent detection here "
     "is not a Cortex miss"),
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

# I2 — "Permission denied" is genuinely ambiguous and the two readings are
# opposite in meaning:
#   - the IDENTITY HARNESS or install/staging tooling was denied (runuser/su/
#     sudo could not act, or a package/payload install was denied) -> the TTP
#     never ran -> ENVIRONMENT.
#   - the step's OWN command was denied by a privilege boundary that is doing
#     exactly what it should (e.g. `cat /etc/shadow` as www-data) -> the
#     technique ran and the boundary held -> real, detectable TTP signal.
# Classifying the second case as ENVIRONMENT tells the operator "this
# produced NO signal, do not report it as a coverage gap" — backwards, and
# this corpus is full of privilege-boundary techniques where it matters.
# So "Permission denied" is ENVIRONMENT only when it is co-located (same
# line) with the identity harness or install/staging machinery; otherwise it
# falls through to the normal exit-code-driven TTP classification.
_PERMISSION_DENIED_CONTEXT_RE = re.compile(
    r"\b(runuser|su|sudo|apt-get|dpkg|payload|staging|chmod|mkdir)\b", re.I
)


def _permission_denied_environment_reason(body: str) -> str | None:
    for line in body.splitlines():
        if "Permission denied" not in line:
            continue
        if _PERMISSION_DENIED_CONTEXT_RE.search(line):
            return ("identity harness or install/staging tooling hit a "
                     "permission boundary before the step's own command ran")
    return None


STEP_RE = re.compile(
    r"=== STEP (?P<n>\d+)/(?P<total>\d+) · (?P<sid>\S+) · (?P<tech>\S+) · "
    r"identity=(?P<identity>\S+) ===(?P<body>.*?)(?=(?:=== STEP )|\Z)",
    re.S,
)
EXIT_RE = re.compile(r"exit_code=(-?\d+)")

# Statuses under which classifying the (possibly partial) output at all is
# sound. "aborted" is deliberately excluded — an aborted run's verdict is
# undefined by design (see connectors/service.py's abort_run: it never scores
# a tc_verdict either), not something this classifier should paper over as a
# clean outcome. "running"/"pending"/"queued"/""/None mean the run never
# actually reached a conclusion — classifying partial output as if the run
# were done is exactly the false-PASS bug this fix exists to close.
CLEAN_TERMINAL_STATUSES = {"complete", "failed", "staged"}


def scan(patterns: list[tuple[str, str]], text: str) -> str | None:
    for pat, reason in patterns:
        if re.search(pat, text):
            return reason
    return None


def classify_step(body: str, code: int | None) -> tuple[str, str]:
    """Return (class, reason) for one step's output body.

    I1: ENGINE signatures are checked FIRST and UNCONDITIONALLY — a step that
    exits 0 while its own output contains "!! IDENTITY NOT HONOURED" or
    "PAYLOAD_PIN_MISMATCH" is not OK; it is a defect that happened not to
    crash the shell. The exit-0 shortcut only ever governs the
    ENVIRONMENT/TTP split, never whether ENGINE gets checked.
    """
    engine_reason = scan(ENGINE_PATTERNS, body)
    if engine_reason:
        return "ENGINE", engine_reason
    if code == 0:
        return "OK", "step exited 0"
    perm_reason = _permission_denied_environment_reason(body)
    if perm_reason:
        return "ENVIRONMENT", perm_reason
    env_reason = scan(ENVIRONMENT_PATTERNS, body)
    if env_reason:
        return "ENVIRONMENT", env_reason
    return "TTP", "step executed and returned a non-zero exit"


def build_verdict(run: dict, scenario: str) -> dict:
    """Pure function: run record + scenario id -> verdict dict. No I/O."""
    output = run.get("output") or ""
    if not isinstance(output, str):
        output = json.dumps(output)

    steps = []
    for m in STEP_RE.finditer(output):
        body = m.group("body")
        exit_m = EXIT_RE.search(body)
        code = int(exit_m.group(1)) if exit_m else None
        klass, reason = classify_step(body, code)
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
    # engine fault — but it must be visible, not silently absent, AND (C1) it
    # must actually gate the verdict rather than only being printed.
    unreported = max(0, declared_total - observed)

    # C1 — scan the WHOLE run output for ENGINE signatures, not only the
    # bodies captured inside recognized `=== STEP ... ===` blocks. A run that
    # dies before dispatch (or between steps, outside any step's captured
    # body — e.g. a bare Traceback from the orchestrator) produces steps==[]
    # and would otherwise sail through as "0 of 0 declared, 0 ENGINE" — PASS.
    global_engine_hits = [reason for pat, reason in ENGINE_PATTERNS if re.search(pat, output)]

    run_status = run.get("status")
    no_steps_executed = not steps
    unsound_status = run_status not in CLEAN_TERMINAL_STATUSES

    engine_signal = bool(counts["ENGINE"]) or bool(global_engine_hits)

    if engine_signal:
        harness_verdict = "FAIL"
    elif no_steps_executed or unreported > 0 or unsound_status:
        harness_verdict = "INCONCLUSIVE"
    else:
        harness_verdict = "PASS"

    return {
        "scenario": scenario,
        "run_id": run.get("run_id") or run.get("id"),
        "run_status": run_status,
        "tc_verdict": run.get("tc_verdict"),
        "steps_declared": declared_total,
        "steps_reported": observed,
        "steps_unreported": unreported,
        "counts": counts,
        "steps": steps,
        "global_engine_signatures": global_engine_hits,
        "harness_verdict": harness_verdict,
        "interpretation": (
            "ENGINE failures mean CortexSim is broken (harness FAIL). ENVIRONMENT "
            "failures mean the TTP never ran — do NOT report those as a detection "
            "miss. TTP failures are real technique outcomes. INCONCLUSIVE means "
            "this run does not have enough honest evidence to call PASS or FAIL — "
            "steps never executed, some declared steps never reported, or the run "
            "never reached a state that means 'this is over'. INCONCLUSIVE is not "
            "a pass; treat it exactly like FAIL until it is understood."
        ),
    }


def render_report(verdict: dict) -> str:
    lines: list[str] = []
    p = lines.append
    p("")
    p(f"  scenario        {verdict['scenario']}")
    p(f"  run status      {verdict['run_status']}   tc_verdict={verdict['tc_verdict']}")
    unreported = verdict["steps_unreported"]
    p(f"  steps           {verdict['steps_reported']} reported / {verdict['steps_declared']} declared"
      + (f"   ({unreported} never reported — run stopped early)" if unreported else ""))
    p("")
    for s in verdict["steps"]:
        mark = {"OK": "\033[0;32m✓\033[0m", "ENGINE": "\033[0;31m✗\033[0m",
                "ENVIRONMENT": "\033[0;33m~\033[0m", "TTP": "\033[0;34m·\033[0m"}[s["class"]]
        p(f"   {mark} step {s['n']} [{s['identity']}] {s['technique']}"
          f"  exit={s['exit_code']}  {s['class']}")
        if s["class"] != "OK":
            p(f"       └─ {s['reason']}")
    if not verdict["steps"]:
        p("   (no === STEP ... === blocks found in run output — nothing was classifiable)")
    p("")
    counts = verdict["counts"]
    p(f"  OK {counts['OK']} · ENVIRONMENT {counts['ENVIRONMENT']} "
      f"· TTP {counts['TTP']} · ENGINE {counts['ENGINE']}")
    if verdict["global_engine_signatures"]:
        p("")
        p("  ENGINE signature(s) found in run output outside any parsed step body:")
        for reason in verdict["global_engine_signatures"]:
            p(f"       └─ {reason}")
    p("")

    hv = verdict["harness_verdict"]
    if hv == "FAIL":
        p("  \033[0;31mHARNESS FAIL\033[0m — an ENGINE-class failure means CortexSim itself is broken.")
    elif hv == "INCONCLUSIVE":
        p("  \033[0;31mHARNESS INCONCLUSIVE\033[0m — this run does not prove the pull-mode lifecycle")
        reasons = []
        if not verdict["steps"]:
            reasons.append("zero steps were ever reported")
        if unreported:
            reasons.append(f"{unreported} declared step(s) were never reported")
        if verdict["run_status"] not in CLEAN_TERMINAL_STATUSES:
            reasons.append(f"run_status={verdict['run_status']!r} is not a clean terminal state")
        p("  ran end to end: " + "; ".join(reasons) + ".")
        p("  Treat this exactly like FAIL until the cause is understood — do NOT report it as a pass.")
    elif counts["ENVIRONMENT"]:
        p("  \033[0;33mHARNESS PASS, with unrun steps\033[0m — the engine worked; the target could not")
        p("  support every step. Those steps produced NO signal, so their absent detections")
        p("  must NOT be reported as a coverage gap.")
    else:
        p("  \033[0;32mHARNESS PASS\033[0m — full pull-mode lifecycle exercised end to end.")

    return "\n".join(lines)


def exit_code_for(verdict: dict) -> int:
    return 0 if verdict["harness_verdict"] == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="path to the saved run.json")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--out", required=True, help="where to write verdict.json")
    args = ap.parse_args()

    with open(args.run, encoding="utf-8") as fh:
        run = json.load(fh)

    verdict = build_verdict(run, args.scenario)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=2)

    print(render_report(verdict))
    return exit_code_for(verdict)


if __name__ == "__main__":
    sys.exit(main())

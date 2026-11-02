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
    # Two shell dialects, one meaning. bash writes "<cmd>: command not found";
    # dash/sh (the Tier-D target's /bin/sh) writes "<label>: <lineno>: <cmd>:
    # not found". Matching only bash's wording silently reclassified every
    # dash missing-interpreter failure (python3, jq, dig, ...) as TTP — "the
    # technique ran and failed" — when the step never executed at all. The
    # dash form is anchored to its diagnostic shape (a shell/label prefix, a
    # bare command token, end of line) so a tool's own "... not found" output
    # mid-line is not swept up as ENVIRONMENT.
    (r"(command not found|No such file or directory: )",
     "a tool the step invokes is not installed on the target"),
    (r"(?m)^\S.*:\s*\d+:\s*\S+: not found\s*$",
     "a tool the step invokes is not installed on the target (sh: not found)"),
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


# --- host termination vocabulary ---------------------------------------------
# Mirrors core/engine/outcome.py. This file ships to a jumpbox with no SimCore
# import path, so the values are restated here and pinned by
# tests/e2e_isolated/test_tier_d_classify.py::test_harness_vocabulary_matches_engine.
# Anything outside the vocabulary RAISES: a tolerant reader keeps the bug.
TERMINATIONS = frozenset({
    "exited", "signaled", "anchor_terminated", "beacon_lost", "timeout",
    "aborted", "not_run",
})
HOST_WITNESS = frozenset({"signaled", "anchor_terminated"})
UPSTREAM_SEVERED = "UPSTREAM_SEVERED"
NOT_RUN_REASONS = frozenset({
    "UPSTREAM_SEVERED", "UPSTREAM_NOT_EXECUTED", "UPSTREAM_FAILED", "TOOL_ABSENT",
})


def scan(patterns: list[tuple[str, str]], text: str) -> str | None:
    for pat, reason in patterns:
        if re.search(pat, text):
            return reason
    return None


def classify_step(body: str, code: int | None,
                  termination: str | None = None) -> tuple[str, str]:
    """Return (class, reason) for one step's output body.

    ``termination`` is the step's host-side record (the /complete ``steps[]``
    vocabulary). A host-witnessed kill (signaled / anchor_terminated) is the
    TTP having run and been stopped: real signal, not a CortexSim fault. A
    lost beacon is a witness of nothing and says so. A ``not_run`` step never
    executed -- ENVIRONMENT, so its absent detection is never read as a miss.
    ``None`` keeps the exit-code-driven behaviour unchanged.

    I1: ENGINE signatures are checked FIRST and UNCONDITIONALLY — a step that
    exits 0 while its own output contains "!! IDENTITY NOT HONOURED" or
    "PAYLOAD_PIN_MISMATCH" is not OK; it is a defect that happened not to
    crash the shell. The exit-0 shortcut only ever governs the
    ENVIRONMENT/TTP split, never whether ENGINE gets checked.
    """
    engine_reason = scan(ENGINE_PATTERNS, body)
    if engine_reason:
        return "ENGINE", engine_reason
    if termination is not None and termination not in TERMINATIONS:
        raise ValueError(f"unrecognised termination: {termination!r}")
    if termination in HOST_WITNESS:
        return "TTP", (f"step was terminated from the host ({termination}); the "
                       "technique ran and was stopped -- not a CortexSim fault")
    if termination == "beacon_lost":
        return "UNWITNESSED", ("beacon lost mid-step; the outcome is unwitnessed (a "
                               "lost beacon is evidence of nothing and never counts "
                               "as a TTP result)")
    if termination == "not_run":
        return "ENVIRONMENT", "step never ran (an upstream step or a tool stopped it)"
    if code == 0:
        return "OK", "step exited 0"
    perm_reason = _permission_denied_environment_reason(body)
    if perm_reason:
        return "ENVIRONMENT", perm_reason
    env_reason = scan(ENVIRONMENT_PATTERNS, body)
    if env_reason:
        return "ENVIRONMENT", env_reason
    return "TTP", "step executed and returned a non-zero exit"


class MissingStepOutcomes(ValueError):
    """The run record has no ``step_outcomes`` key: it came from a SimCore that
    does not publish host evidence, so no termination claim can be made."""


def require_step_outcomes(run: dict) -> list[dict]:
    """The host evidence of a run record fetched from ``GET /api/runs/{id}``.

    Absent key -> :class:`MissingStepOutcomes`. There is deliberately no
    reconstruction from output text: a guess at a termination is worse than none.
    """
    if "step_outcomes" not in run:
        raise MissingStepOutcomes(
            "run record has no 'step_outcomes' key -- SimCore predates "
            "GET /api/runs/{id} publishing host evidence, so terminations "
            "cannot be classified (rebuild SimCore from this branch)")
    return run["step_outcomes"]


def build_verdict(run: dict, scenario: str,
                  step_outcomes: list[dict] | None = None) -> dict:
    """Pure function: run record + scenario id -> verdict dict. No I/O.

    ``step_outcomes`` defaults to the run record's own ``step_outcomes`` list
    (``GET /api/runs/{id}``; ``rc`` = exit code). Steps they account for count
    as reported: a severed run is not "stopped early for an unknown reason".
    """
    if step_outcomes is None:
        step_outcomes = run.get("step_outcomes") or []
    by_step: dict[str, dict] = {}
    for rec in step_outcomes or []:
        term = rec.get("termination")
        if term is None:
            # Output seen (a started_at row) but no termination recorded: the
            # step's end was never reported. That is absence of evidence, not
            # a vocabulary violation.
            continue
        if term not in TERMINATIONS:
            raise ValueError(f"unrecognised termination: {term!r}")
        by_step[rec["step_id"]] = rec

    output = run.get("output") or ""
    if not isinstance(output, str):
        output = json.dumps(output)

    steps = []
    for m in STEP_RE.finditer(output):
        body = m.group("body")
        exit_m = EXIT_RE.search(body)
        code = int(exit_m.group(1)) if exit_m else None
        rec = by_step.get(m.group("sid"))
        klass, reason = classify_step(body, code, rec["termination"] if rec else None)
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
    # UNWITNESSED (a lost beacon) is its own outcome: it never feeds the TTP
    # count, and is kept out of `counts` so that dict's shape is unchanged.
    unwitnessed = sum(1 for s in steps if s["class"] == "UNWITNESSED")

    # A step the beacon never reported at all is its own problem: the run
    # stopped early. Fail-fast is by design, so this is not automatically an
    # engine fault — but it must be visible, not silently absent, AND (C1) it
    # must actually gate the verdict rather than only being printed.
    terminations = {
        sid: {"termination": rec["termination"], "reason": rec.get("reason")}
        for sid, rec in by_step.items()
    }
    # A step with no output block is accounted for only when a SEVERANCE
    # explains it: the host killed it (signaled / anchor_terminated), the
    # beacon was lost on it, or it never ran because an upstream step was
    # severed. UPSTREAM_FAILED / UPSTREAM_NOT_EXECUTED / TOOL_ABSENT stay
    # unreported on purpose (C1): fail-fast stopping is a fact about the engine,
    # not proof that every later step would have met the same environment gap.
    reported_ids = {s["step_id"] for s in steps}
    accounted = sum(
        1 for sid, rec in by_step.items()
        if sid not in reported_ids and (
            rec["termination"] in HOST_WITNESS
            or rec["termination"] == "beacon_lost"
            or (rec["termination"] == "not_run" and rec.get("reason") == UPSTREAM_SEVERED)))
    unreported = max(0, declared_total - observed - accounted)

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
        "unwitnessed": unwitnessed,
        "steps": steps,
        "terminations": terminations,
        "severed_at_step": run.get("severed_at_step"),
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
                "ENVIRONMENT": "\033[0;33m~\033[0m", "TTP": "\033[0;34m·\033[0m",
                "UNWITNESSED": "\033[0;33m?\033[0m"}[s["class"]]
        p(f"   {mark} step {s['n']} [{s['identity']}] {s['technique']}"
          f"  exit={s['exit_code']}  {s['class']}")
        if s["class"] != "OK":
            p(f"       └─ {s['reason']}")
    if not verdict["steps"]:
        p("   (no === STEP ... === blocks found in run output — nothing was classifiable)")
    for sid, t in verdict.get("terminations", {}).items():
        p(f"   host record  {sid}: {t['termination']}" + (f" / {t['reason']}" if t["reason"] else ""))
    if verdict.get("severed_at_step"):
        p(f"   severed at   {verdict['severed_at_step']}")
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
    try:
        outcomes = require_step_outcomes(run)
    except MissingStepOutcomes as exc:
        print(f"classify: INCONCLUSIVE -- {exc}", file=sys.stderr)
        return 1

    verdict = build_verdict(run, args.scenario, outcomes)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(verdict, fh, indent=2)

    print(render_report(verdict))
    return exit_code_for(verdict)


if __name__ == "__main__":
    sys.exit(main())

"""Lab-readiness classifier — which scenarios will actually emit signal in a lab.

The one definition of the GREEN / YELLOW / RED tiers. ``scripts/lab_readiness.py``
writes ``docs/reference/lab-readiness.{json,md}`` from it (the ``check-lab-ready``
gate), and SimCore reads it at runtime to star the system's best scenarios
(:func:`favorite_reasons`), so the manifest and the console cannot disagree.
The rationale for each tier is in that script's docstring.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from functools import lru_cache

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML required: pip install pyyaml\n")
    raise

def _base(base: str | None = None) -> str:
    """The checkout (or image) root: ``scenarios/`` and ``tools/packs/`` live here."""
    return (base or os.environ.get("CORTEXSIM_BASE_DIR")
            or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# A clause counts as "no-signal" (produces no sensor-visible telemetry) when it
# is one of these. `touch` is included to match the corpus's own documented
# echo/printf/touch definition, though it does emit a weak FILE_WRITE event.
_NO_SIGNAL_RE = re.compile(r"^(echo|printf|:|true|touch|sleep|export|cd|set|source)\b|^#")
# Shell bookkeeping that is not an attack action: a `[ ... ]` test and `exit`.
# A scratch-directory ownership guard is made only of these, so it must not
# turn a narration step into a "real signal" one. The guard asks `stat` and
# `id` about a path, which is a probe like `command -v`; any other command
# substitution inside a test still counts, because that runs a real command.
_BOOKKEEPING_RE = re.compile(r"^(\[\s.*\]|exit(\s+\d+)?)$")
_PROBE_SUBST_RE = re.compile(r"\$\((?:stat|id)\b[^()]*\)")
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


def _load_packs(base: str | None = None) -> dict:
    """adapter_id -> {tier, shelf_backed, artifact_exempt, exempt_reason, c2}."""
    packs = {}
    for path in glob.glob(os.path.join(_base(base), "tools", "packs", "*.yml")):
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
    bare = _PROBE_SUBST_RE.sub("", c)
    if _BOOKKEEPING_RE.match(bare) and "$(" not in bare and "`" not in bare:
        return False
    return True


def _step_produces_signal(command: str) -> bool:
    """True if any clause performs a real, telemetry-producing action."""
    return any(_clause_is_signal(c) for c in _clauses(command))


def _iter_scenarios(base: str | None = None):
    pattern = os.path.join(_base(base), "scenarios", "**", "*.yml")
    for path in sorted(glob.glob(pattern, recursive=True)):
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
        # A `channel: eal` step's records are POSTed by SimCore itself; its
        # command is a descriptive no-op, so the command text says nothing.
        produces = s.get("channel") == "eal" or _step_produces_signal(cmd)
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
    declared_consent = bool(consent.get("simulation_authorized") or consent.get("c2_authorized")) or bool(c2_tools)
    # `channel: eal` steps run inside SimCore at launch and go live only with
    # launch consent, an authorized_by and a keyed collector target for every
    # eal step (orchestrator._dispatch_eal_in_process); otherwise they run dry
    # and deliver nothing. That is a launch consent gate.
    in_process_eal = any(s.get("channel") == "eal" for s in steps)
    consent_gated = declared_consent or in_process_eal

    # is signal delivered off-command by an EAL plugin? (real network/log signal
    # even though the shell step may look thin)
    eal_driven = in_process_eal or any(
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
        if declared_consent:
            bits.append("launch consent required")
        if in_process_eal:
            bits.append("channel: eal steps POST from SimCore at launch: live only with launch "
                        "consent, an authorized_by and a keyed collector target, otherwise dry-run")
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


def build(base: str | None = None) -> list[dict]:
    packs = _load_packs(base)
    rows = [classify(doc, packs) for _, doc in _iter_scenarios(base)]
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


# ── The system's best scenarios (the ★ in the console) ────────────────────────
#
# Henry, 2026-11-13: "Favorites are the best in the system" — measured, not
# hand-picked. A scenario is starred when every condition below holds, so the
# set moves with the content and needs no list anyone has to keep current.

FAVORITE_MIN_DETECTION_TYPES = 4


def favorite_reasons(row: dict) -> list[str]:
    """Why a classified row is one of the system's best, or ``[]`` when it is not.

    All four must hold:
      * lab-ready (GREEN): runs on a provisioned target with no tool fetched
        from the internet and no launch consent gate;
      * every step makes real telemetry (no narration-only step);
      * it raises a Correlation;
      * it spans at least ``FAVORITE_MIN_DETECTION_TYPES`` detection types.
    The returned lines are the console's tooltip, in the order above.
    """
    types = list(dict.fromkeys(row.get("detection_types") or []))
    if (
        row.get("status") != "active"
        or row.get("tier") != "GREEN"
        or row.get("signal_ratio") != 1.0
        or row.get("detection_steps_without_signal")
        or "Correlation" not in types
        or len(types) < FAVORITE_MIN_DETECTION_TYPES
    ):
        return []
    return [
        "Runs offline on a lab target",
        "Every step makes real telemetry",
        "Raises a Correlation",
        f"{len(types)} detection types: {', '.join(types)}",
    ]


@lru_cache(maxsize=4)
def favorites(base: str | None = None) -> dict[str, list[str]]:
    """``scenario_id -> reasons`` for every starred scenario under ``base``.

    Classifying the corpus parses every scenario file (about 1.3 s on a laptop),
    so it runs once per process: the corpus only changes when SimCore restarts.
    """
    return {r["scenario_id"]: why for r in build(base) if (why := favorite_reasons(r))}

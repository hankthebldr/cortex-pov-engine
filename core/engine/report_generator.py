"""
core/engine/report_generator.py — POV deliverable artifact generator.

Generates the three POV-deliverable artifacts CortexSim Domain Consultants
produce at the end of a run:

  1. ``detection_matrix.csv``          — one row per expected detection
  2. ``attack_navigator_layer.json``   — MITRE ATT&CK Navigator v4.5 layer
  3. ``exec_summary.md``               — exec-level markdown summary

Plus ``build_bundle()`` packages all three into a single ``.tar.gz``.

Shape is exact-match to the worked example in ``lab_cortex_analytics_pov/``
so a DC can hand off the same artifact set the customer expects.

Input is a run-id and the existing SQLAlchemy ``Run`` / ``Result`` / ``Scenario``
rows; no schema changes — every field needed is already on those models.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import logging
import tarfile
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


logger = logging.getLogger("cortexsim.engine.report_generator")


# Navigator layer constants (matches lab_cortex_analytics_pov template).
_NAVIGATOR_VERSION = {
    "attack": "14",
    "navigator": "4.9.1",
    "layer": "4.5",
}
_DETECTED_COLOR = "#e60d0d"
_MISSED_COLOR = "#919191"


@dataclasses.dataclass(frozen=True)
class DetectionMatrixRow:
    alert_name: str
    sources: str
    status: str          # ENABLED | ENHANCED | DISABLED | PENDING
    alert_type: str      # Analytics | BIOC | IOC | ITDR | Correlation
    attack_tid: str
    severity: str        # Low | Medium | High | Critical

    def as_csv_row(self) -> list[str]:
        return [
            self.alert_name, self.sources, self.status,
            self.alert_type, self.attack_tid, self.severity,
        ]


def _severity_from_detection_type(detection_type: str | None,
                                  observed: bool) -> str:
    """Derive a customer-facing severity tag from the result's signal type
    + observation outcome. Conservative defaults; can be overridden later
    by a future ``severity`` column on ``Result``."""
    if detection_type == "Correlation":
        return "Critical"
    if detection_type in ("BIOC", "Analytics", "ITDR"):
        return "High" if observed else "Medium"
    return "Medium"


def _status_from_result(observed: bool, has_observed_at: bool) -> str:
    if observed:
        return "ENABLED"
    if has_observed_at:
        # observed_at was set but observed flag is false → DC marked
        # explicitly missed.
        return "DISABLED"
    return "PENDING"


def build_detection_matrix(
    run_dict: dict[str, Any],
    scenario_dict: Optional[dict[str, Any]],
    results: list[dict[str, Any]],
) -> list[DetectionMatrixRow]:
    """Build the detection-matrix rows from a run + its results.

    One row per ``Result`` entry. Each row's columns mirror the worked
    example in ``lab_cortex_analytics_pov/detection_matrix.csv``.
    """
    out: list[DetectionMatrixRow] = []
    scenario_plane = (scenario_dict or {}).get("plane") or "—"
    scenario_techniques = _scenario_technique_set(scenario_dict)

    for r in results:
        observed = bool(r.get("observed"))
        has_obs_at = bool(r.get("observed_at"))
        # The "Source(s)" column maps roughly to plane (the customer
        # cares which Cortex engine fires). EAL plugins and AIRS land in
        # the ``plane`` field; multi-plane stitching surfaces as
        # ``ANALYTICS``.
        plane = r.get("plane") or scenario_plane
        # Pick the most specific MITRE technique on the result, else
        # fall back to the scenario's primary technique.
        tid = r.get("mitre_technique") or _primary_technique(scenario_dict)
        out.append(DetectionMatrixRow(
            alert_name=r.get("expected_detection") or "—",
            sources=plane,
            status=_status_from_result(observed, has_obs_at),
            alert_type=r.get("signal_type") or "Analytics",
            attack_tid=tid or "—",
            severity=_severity_from_detection_type(r.get("signal_type"), observed),
        ))

    return out


def render_detection_matrix_csv(rows: Iterable[DetectionMatrixRow]) -> str:
    """Render the matrix to a CSV string matching the lab example header."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Alert Name", "Source(s)", "Status", "Alert Type",
                "ATT&CK TID", "Severity"])
    for r in rows:
        w.writerow(r.as_csv_row())
    return buf.getvalue()


def render_attack_navigator_layer(
    run_dict: dict[str, Any],
    scenario_dict: Optional[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the ATT&CK Navigator v4.5 layer JSON.

    Each technique referenced by the run's results gets a colour-coded
    entry. Detected → red (``#e60d0d``), missed → grey (``#919191``),
    matching the lab's convention.
    """
    techniques: dict[str, dict[str, Any]] = {}

    # Track which techniques were observed so the colour reflects the
    # actual customer outcome.
    by_tid: dict[str, dict[str, Any]] = {}
    for r in results:
        tid = r.get("mitre_technique")
        if not tid:
            continue
        observed = bool(r.get("observed"))
        slot = by_tid.setdefault(tid, {"observed": False, "comments": set()})
        slot["observed"] = slot["observed"] or observed
        # Surface the plane / signal-type as the Navigator comment so
        # the customer sees *which* Cortex engine fired (matches the
        # lab's "DETECTED - Agent Analytics" style).
        plane = r.get("plane") or "—"
        signal = r.get("signal_type") or "Analytics"
        marker = "DETECTED" if observed else "MISSED"
        slot["comments"].add(f"{marker} - {plane} {signal}")

    # Also include the scenario's primary + additional techniques even
    # when the run had no matching result (so the layer shows the full
    # scenario surface, not just whatever fired).
    for tid in _scenario_technique_set(scenario_dict):
        by_tid.setdefault(tid, {"observed": False, "comments": {"PENDING - scenario surface"}})

    for tid, slot in by_tid.items():
        techniques[tid] = {
            "techniqueID": tid,
            "color": _DETECTED_COLOR if slot["observed"] else _MISSED_COLOR,
            "comment": " | ".join(sorted(slot["comments"])),
            "enabled": True,
        }

    scenario_name = (scenario_dict or {}).get("name") or run_dict.get("scenario_id", "Cortex POV")
    return {
        "name": f"CortexSim — {scenario_name}",
        "versions": _NAVIGATOR_VERSION,
        "domain": "enterprise-attack",
        "description": (
            f"Coverage layer auto-generated by CortexSim for run "
            f"{run_dict.get('run_id', '—')}."
        ),
        "techniques": sorted(techniques.values(), key=lambda t: t["techniqueID"]),
    }


def render_exec_summary_markdown(
    run_dict: dict[str, Any],
    scenario_dict: Optional[dict[str, Any]],
    results: list[dict[str, Any]],
) -> str:
    """Render the exec-level markdown summary (matches the lab template)."""
    s = scenario_dict or {}
    total = len(results)
    observed = sum(1 for r in results if r.get("observed"))
    coverage_pct = round(observed / total * 100, 1) if total else 0.0
    mttds = [r["mttd_seconds"] for r in results if r.get("mttd_seconds") is not None]
    avg_mttd = round(sum(mttds) / len(mttds), 1) if mttds else None

    findings = _findings_paragraph(results)

    lines: list[str] = []
    lines.append(f"# Cortex Analytics POV Executive Summary")
    lines.append("")
    lines.append(f"_Auto-generated by CortexSim for run `{run_dict.get('run_id', '—')}`._")
    lines.append("")
    lines.append("## Objective")
    lines.append("")
    lines.append(
        f"Validate Cortex detection coverage for the scenario "
        f"**{s.get('name') or run_dict.get('scenario_id', '—')}** "
        f"(plane: `{s.get('plane', '—')}`) and confirm that the customer's "
        f"existing analytics, BIOC, IOC, and stitching rules fire on "
        f"controlled, high-fidelity signal."
    )
    lines.append("")
    lines.append("## Scope of Simulation")
    lines.append("")
    lines.append(
        f"The simulation exercised the following MITRE ATT&CK techniques: "
        f"{_format_technique_inline(scenario_dict, results)}. "
        f"Primary tactic: **{s.get('mitre_tactic_name') or s.get('mitre_tactic') or '—'}**."
    )
    lines.append("")
    lines.append("## Key Findings")
    lines.append("")
    lines.append(f"- **Coverage:** {observed} / {total} expected detections observed ({coverage_pct}%).")
    if avg_mttd is not None:
        lines.append(f"- **MTTD (average across observed):** {avg_mttd}s.")
    lines.append(f"- **Findings:** {findings}")
    if s.get("threat_report"):
        lines.append(f"- **Threat intelligence reference:** {s.get('threat_report')}.")
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    if coverage_pct >= 80:
        verdict = (
            "The simulation confirmed strong Cortex detection coverage for the "
            "scenario surface. Recommend promoting to customer-facing POV report."
        )
    elif coverage_pct >= 50:
        verdict = (
            "The simulation confirmed partial detection coverage. Recommend "
            "reviewing the missed alerts and considering custom-rule "
            "remediation before customer hand-off."
        )
    else:
        verdict = (
            "The simulation revealed a coverage gap. Recommend authoring "
            "BIOC / correlation rules to close the missed-alert delta before "
            "customer hand-off."
        )
    lines.append(verdict)
    lines.append("")
    lines.append(f"_Report generated {datetime.now(timezone.utc).isoformat()} by CortexSim._")
    lines.append("")
    return "\n".join(lines)


def build_bundle(
    run_dict: dict[str, Any],
    scenario_dict: Optional[dict[str, Any]],
    results: list[dict[str, Any]],
) -> bytes:
    """Build the report tar.gz bundle (the three artifacts in one stream)."""
    rows = build_detection_matrix(run_dict, scenario_dict, results)
    csv_blob = render_detection_matrix_csv(rows).encode("utf-8")
    nav_blob = json.dumps(
        render_attack_navigator_layer(run_dict, scenario_dict, results),
        indent=2,
    ).encode("utf-8")
    md_blob = render_exec_summary_markdown(run_dict, scenario_dict, results).encode("utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, blob in [
            ("detection_matrix.csv", csv_blob),
            ("attack_navigator_layer.json", nav_blob),
            ("pov_narrative/exec_summary.md", md_blob),
        ]:
            info = tarfile.TarInfo(name=name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _scenario_technique_set(scenario: Optional[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    if not scenario:
        return out
    if scenario.get("mitre_technique"):
        out.add(scenario["mitre_technique"])
    for extra in scenario.get("additional_techniques", []) or []:
        if isinstance(extra, dict) and extra.get("technique"):
            out.add(extra["technique"])
    # Steps may reference per-step techniques.
    for step in scenario.get("steps", []) or []:
        if isinstance(step, dict) and step.get("mitre_technique"):
            out.add(step["mitre_technique"])
    return out


def _primary_technique(scenario: Optional[dict[str, Any]]) -> Optional[str]:
    if not scenario:
        return None
    return scenario.get("mitre_technique")


def _format_technique_inline(
    scenario: Optional[dict[str, Any]],
    results: list[dict[str, Any]],
) -> str:
    techs = sorted(_scenario_technique_set(scenario)
                   | {r["mitre_technique"] for r in results
                      if r.get("mitre_technique")})
    if not techs:
        return "(none recorded)"
    return ", ".join(f"`{t}`" for t in techs)


def _findings_paragraph(results: list[dict[str, Any]]) -> str:
    """Compose a 1-2 line readable findings string."""
    if not results:
        return "No expected detections were defined for this run."
    n_observed = sum(1 for r in results if r.get("observed"))
    n_missed = sum(1 for r in results if not r.get("observed") and r.get("observed_at"))
    n_pending = len(results) - n_observed - n_missed

    parts = []
    if n_observed:
        parts.append(f"{n_observed} alert(s) fired as expected")
    if n_missed:
        parts.append(f"{n_missed} alert(s) explicitly marked missed")
    if n_pending:
        parts.append(f"{n_pending} pending DC validation")
    return "; ".join(parts) + "."

#!/usr/bin/env python3
"""
detection_scanner/scripts/export_artifacts.py
--------------------------------------------

Generates the deployable detection artifacts customers consume at the end
of a CortexSim POV from every active TTP entry under ``ttps/``.

Outputs (one set per TTP, written under ``detection_scanner/exports/``):

  exports/
    sigma/<TTP-ID>.yml                 — Sigma rule per BIOC (community-portable)
    xql/<TTP-ID>.xql                   — raw XQL block per BIOC + per validation query
    correlation/<TTP-ID>.json          — correlation-rule shape per rule
    xsoar_playbook/<TTP-ID>.yml        — XSOAR playbook stub keyed off the panw_mapping
    README.md                          — index of generated artifacts

Design rules:

* Pure read-then-write. The script never mutates the TTP corpus.
* Deterministic output (sorted keys, no timestamps in artifact bodies) so a
  ``git diff --exit-code detection_scanner/exports/`` works as a CI guard.
* Best-effort: a TTP missing optional structure (no biocs, no correlation,
  no panw_mapping) simply gets fewer files. Never raises.

Usage::

    python3 detection_scanner/scripts/export_artifacts.py
    python3 detection_scanner/scripts/export_artifacts.py --clean
    python3 detection_scanner/scripts/export_artifacts.py --ttp TTP-2026-0004
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional


ROOT = Path(__file__).resolve().parent.parent
TTPS_DIR = ROOT / "ttps"
EXPORTS_DIR = ROOT / "exports"

# Sigma severity is constrained to a small enum; map our richer corpus values.
_SIGMA_SEVERITY = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "informational",
}


def _slug(name: str) -> str:
    """Stable lowercase slug used as a filename suffix when we need one."""
    clean = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return clean or "untitled"


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARN  skip {path.name}: {exc}", file=sys.stderr)
        return None


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-format renderers
# ---------------------------------------------------------------------------


def render_sigma(ttp: dict[str, Any]) -> Optional[str]:
    """Render a Sigma YAML file for a TTP's BIOCs.

    Sigma is the lingua franca for portable detection content. Customers who
    don't run XSIAM (or who run XSIAM alongside Splunk / Sentinel) can still
    consume these. The Cortex XQL stays the canonical body; Sigma's
    ``logsource`` is a best-effort shape derived from the TTP's MITRE data
    sources + execution.target_platform.
    """
    biocs = (ttp.get("detections") or {}).get("biocs") or []
    if not biocs:
        return None

    ttp_id = ttp["id"]
    title_base = (ttp.get("identity") or {}).get("name") or ttp_id
    platform = (ttp.get("execution") or {}).get("target_platform") or "any"
    techniques = [
        t.get("subtechnique_id") or t.get("technique_id")
        for t in (ttp.get("mitre_attack") or {}).get("techniques") or []
        if isinstance(t, dict)
    ]
    techniques = [t for t in techniques if t]

    # Sigma supports multi-doc YAML — emit one doc per BIOC.
    docs: list[str] = []
    for idx, b in enumerate(biocs, start=1):
        name = b.get("name") or f"bioc-{idx}"
        severity = _SIGMA_SEVERITY.get((b.get("severity") or "").lower(), "medium")
        rule_id = f"{ttp_id.lower()}-bioc-{idx:02d}"
        bioc_tids = list(b.get("mitre_technique_ids") or []) or techniques

        tag_block = "\n".join(f"    - attack.{t.lower()}" for t in bioc_tids) or "    - attack.t0000"
        logic_block = (b.get("logic") or "").rstrip("\n")
        # Sigma doesn't natively express XQL — we embed it as a `condition:`
        # plus a free-text `description` so detection engineers can port it.
        # title may contain `:`, `#`, or other YAML-sensitive chars when
        # lifted verbatim from scenario descriptions — emit JSON-style
        # double-quoted scalars so YAML parses cleanly.
        safe_title = json.dumps(f"{title_base} — {name}")
        safe_desc = (b.get("description") or "").strip()
        doc = f"""title: {safe_title}
id: {rule_id}
status: experimental
description: |
  {safe_desc}
  Canonical Cortex XQL body follows (preserve verbatim for XSIAM):
  ----
{textwrap_indent(logic_block, "    ")}
  ----
references:
  - https://attack.mitre.org/techniques/{(bioc_tids[0] if bioc_tids else 'T0000').split('.')[0]}/
author: CortexSim detection_scanner
tags:
{tag_block}
logsource:
  product: {platform}
  category: process_creation
detection:
  selection:
    placeholder: replace_with_target_dialect_translation_of_xql
  condition: selection
falsepositives:
  - Legitimate administrative tooling that mirrors the same telemetry shape.
level: {severity}
"""
        docs.append(doc)

    return "---\n".join(docs)


def render_xql(ttp: dict[str, Any]) -> Optional[str]:
    """Plain-text XQL paste-buffer. One section per BIOC + per validation query."""
    biocs = (ttp.get("detections") or {}).get("biocs") or []
    xqls = (ttp.get("detections") or {}).get("xql_queries") or []
    if not biocs and not xqls:
        return None

    ttp_id = ttp["id"]
    title = (ttp.get("identity") or {}).get("name") or ttp_id
    out: list[str] = [f"# {ttp_id} — {title}", "# XQL paste-buffer generated by detection_scanner/scripts/export_artifacts.py", ""]

    for b in biocs:
        out.append(f"## BIOC — {b.get('name', '(unnamed)')}")
        if b.get("severity"):
            out.append(f"# severity: {b['severity']}")
        if b.get("description"):
            out.append(f"# {b['description']}")
        out.append("")
        out.append((b.get("logic") or "").rstrip("\n"))
        out.append("")

    for q in xqls:
        out.append(f"## VALIDATION — {q.get('name', '(unnamed)')}")
        if q.get("purpose"):
            out.append(f"# purpose: {q['purpose']}")
        if q.get("dataset"):
            out.append(f"# dataset: {q['dataset']}")
        out.append("")
        out.append((q.get("query") or "").rstrip("\n"))
        out.append("")

    return "\n".join(out) + "\n"


def render_correlation(ttp: dict[str, Any]) -> Optional[str]:
    """Correlation-rule JSON in a generic shape XSIAM operators can adapt.

    The shape mirrors the corpus's own ``correlation_rules`` block but adds
    the originating TTP id and MITRE techniques so the operator has full
    context when they paste it into the XSIAM rule editor.
    """
    rules = (ttp.get("detections") or {}).get("correlation_rules") or []
    if not rules:
        return None

    ttp_id = ttp["id"]
    techniques = [
        t.get("subtechnique_id") or t.get("technique_id")
        for t in (ttp.get("mitre_attack") or {}).get("techniques") or []
        if isinstance(t, dict)
    ]
    techniques = [t for t in techniques if t]

    payload = {
        "ttp_ref": ttp_id,
        "mitre_techniques": techniques,
        "rules": [
            {
                "rule_id": r.get("rule_id"),
                "name": r.get("name"),
                "logic": r.get("logic"),
                "severity": r.get("severity") or "high",
                "description": r.get("description"),
            }
            for r in rules
            if isinstance(r, dict)
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_xsoar_playbook(ttp: dict[str, Any]) -> Optional[str]:
    """Render an XSOAR playbook stub keyed off the panw_mapping XSOAR entry.

    The output is a deliberately minimal scaffolding: the customer's XSOAR
    instance already has the tasks; what they need from CortexSim is the
    *trigger* (BIOC name / correlation id) and the recommended *response
    sequence*. We synthesise that from the corpus's panw_mapping and
    response_playbook fields.
    """
    panw = ttp.get("panw_mapping") or {}
    products = [p for p in (panw.get("products") or []) if isinstance(p, dict)]
    xsoar = next((p for p in products if (p.get("module") == "cortex-xsoar")), None)
    if xsoar is None:
        return None

    ttp_id = ttp["id"]
    title = (ttp.get("identity") or {}).get("name") or ttp_id
    rule_ids = xsoar.get("rule_ids") or []
    correlation_ids = [
        r.get("rule_id")
        for r in (ttp.get("detections") or {}).get("correlation_rules") or []
        if isinstance(r, dict) and r.get("rule_id")
    ]
    response_playbook = (ttp.get("remediation_guidance") or {}).get("response_playbook")

    # YAML by hand to keep zero runtime deps (the corpus consumers only
    # require stdlib). The shape is intentionally PB-Yaml-shaped so an
    # operator can ``demisto-sdk init`` against it.
    indent = "  "
    lines: list[str] = []
    lines.append(f"id: cortexsim-{ttp_id.lower()}-response")
    lines.append(f"version: 1")
    lines.append(f"name: \"CortexSim — {title}\"")
    lines.append(f"description: |")
    lines.append(f"{indent}Auto-generated XSOAR playbook stub from detection_scanner TTP {ttp_id}.")
    lines.append(f"{indent}Triggered by Cortex correlation rules: "
                 f"{', '.join(correlation_ids) if correlation_ids else '(none declared)'}.")
    if response_playbook:
        lines.append(f"{indent}Reference playbook in panw_mapping: {response_playbook}")
    lines.append("triggers:")
    for cid in correlation_ids:
        lines.append(f"  - correlation_rule_id: {cid}")
    for rid in rule_ids:
        lines.append(f"  - playbook_rule_id: {rid}")
    if not (correlation_ids or rule_ids):
        lines.append(f"  - TBD: replace with customer-specific trigger id")
    lines.append("tasks:")
    lines.append(f"  isolate_host:")
    lines.append(f"    integration: CortexXDR")
    lines.append(f"    command: xdr-endpoint-isolate")
    lines.append(f"    when: alert.severity in [\"High\", \"Critical\"]")
    lines.append(f"  open_investigation:")
    lines.append(f"    integration: ServiceNow")
    lines.append(f"    command: servicenow-create-record")
    lines.append(f"    record_type: incident")
    lines.append(f"  notify_soc:")
    lines.append(f"    integration: Slack")
    lines.append(f"    command: slack-notify")
    lines.append(f"    channel: \"#cortex-soc\"")
    lines.append("output:")
    lines.append(f"  ttp_ref: {ttp_id}")
    lines.append(f"  cortexsim_version: 1.0")
    return "\n".join(lines) + "\n"


def textwrap_indent(text: str, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" if line.strip() else "" for line in text.splitlines())


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def iter_active_ttps(filter_id: Optional[str]) -> Iterable[dict[str, Any]]:
    for path in sorted(TTPS_DIR.glob("*.json")):
        ttp = _read_json(path)
        if not ttp:
            continue
        if (ttp.get("status") or "") != "active":
            continue
        if filter_id and ttp.get("id") != filter_id:
            continue
        yield ttp


def clean_exports() -> None:
    """Remove generated artifact files (keep the directory + README scaffolding)."""
    if not EXPORTS_DIR.exists():
        return
    for sub in ("sigma", "xql", "correlation", "xsoar_playbook"):
        d = EXPORTS_DIR / sub
        if not d.exists():
            continue
        for p in d.glob("*"):
            if p.is_file():
                p.unlink()


def write_index(generated: list[tuple[str, str, list[str]]]) -> None:
    """Write the exports/README.md index of what was produced."""
    lines: list[str] = []
    lines.append("# detection_scanner — generated detection artifacts")
    lines.append("")
    lines.append(
        "These files are auto-generated by "
        "`detection_scanner/scripts/export_artifacts.py` from the active TTP "
        "entries under `../ttps/`."
    )
    lines.append("")
    lines.append("Do NOT edit them by hand. Edit the TTP entry, then re-run "
                 "the export script.")
    lines.append("")
    lines.append("| TTP | Name | Generated artifacts |")
    lines.append("|---|---|---|")
    for ttp_id, name, artifacts in sorted(generated):
        arts = ", ".join(f"[{a.split('/')[0]}]({a})" for a in artifacts) or "—"
        lines.append(f"| `{ttp_id}` | {name} | {arts} |")
    lines.append("")
    _write(EXPORTS_DIR / "README.md", "\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clean", action="store_true",
                    help="Delete previously generated files before writing")
    ap.add_argument("--ttp", help="Only process this TTP id (e.g. TTP-2026-0004)")
    args = ap.parse_args()

    if args.clean:
        clean_exports()

    generated: list[tuple[str, str, list[str]]] = []

    for ttp in iter_active_ttps(args.ttp):
        ttp_id = ttp["id"]
        name = (ttp.get("identity") or {}).get("name") or ttp_id
        produced: list[str] = []

        sigma = render_sigma(ttp)
        if sigma:
            rel = f"sigma/{ttp_id}.yml"
            _write(EXPORTS_DIR / rel, sigma)
            produced.append(rel)

        xql = render_xql(ttp)
        if xql:
            rel = f"xql/{ttp_id}.xql"
            _write(EXPORTS_DIR / rel, xql)
            produced.append(rel)

        corr = render_correlation(ttp)
        if corr:
            rel = f"correlation/{ttp_id}.json"
            _write(EXPORTS_DIR / rel, corr)
            produced.append(rel)

        pb = render_xsoar_playbook(ttp)
        if pb:
            rel = f"xsoar_playbook/{ttp_id}.yml"
            _write(EXPORTS_DIR / rel, pb)
            produced.append(rel)

        generated.append((ttp_id, name, produced))
        print(f"OK   {ttp_id} — {len(produced)} artifact(s): {', '.join(produced) or '(none)'}")

    write_index(generated)
    print(f"\nIndex written to {EXPORTS_DIR / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

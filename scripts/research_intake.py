#!/usr/bin/env python3
"""research_intake.py — turn a weekly threat report into a scaffolded detection.

The weekly-research → product gap: a Domain Consultant reads a fresh Unit 42 /
CrowdStrike / DFIR-Report writeup on Monday, and by Friday nothing has landed in
the corpus because hand-authoring a schema-valid TTP card *and* a matching
scenario from a blank file is fiddly enough to never happen. This CLI closes
that gap: given a report title + url + one-line summary it scaffolds

  1. a schema-COMPLIANT TTP card skeleton under detection_scanner/ttps/, and
  2. a matching scenario skeleton under scenarios/<plane>/,

both of which pass detection_scanner/scripts/validate.py and
.claude/hooks/lint-scenario.py *unedited*. The DC then fills in the real
detection logic from the research and commits. The scaffold picks the next free
`TTP-2026-NNNN` (never reusing an id) and the next scenario id for the plane, and
wires the scenario's `detection_id` slugs to the card's detection objects so
GAP-4 (every scenario detection_id resolves to a card) holds out of the box.

Modelled on the validated pair:
  detection_scanner/ttps/TTP-2026-0096-sim-edr-010.json
  scenarios/edr/edr-010-hands-on-keyboard-abioc.yml

Stdlib only — runs anywhere, no venv, no pip.

Usage:
  scripts/research_intake.py --title "..." --url "https://..." \
      --summary "one line" --plane EDR
  scripts/research_intake.py --file report.txt --plane CDR
  scripts/research_intake.py --title "..." --url "..." --summary "..." --dry-run

The --file form reads simple `key: value` lines (title / url / summary / plane).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import sys
from pathlib import Path


# ── repo geometry ────────────────────────────────────────────────────────────
# scripts/research_intake.py → repo root is the parent of scripts/.
REPO_ROOT = Path(__file__).resolve().parent.parent
TTPS_DIR = "detection_scanner/ttps"
SCENARIOS_DIR = "scenarios"

TTP_ID_RE = re.compile(r"TTP-(\d{4})-(\d{4})")
SCENARIO_ID_RE = re.compile(r"^SIM-([A-Z0-9_]+)-(\d{3,})$")

# Valid detection planes (mirrors .claude/hooks/lint-scenario.py PLANES).
PLANES = {
    "EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "ANALYTICS", "AI_ACCESS", "AIRS",
    "AI_SPM", "BROWSER", "KOI", "ASM", "CSPM", "TIM", "EMAIL",
}

# Plane → on-disk scenario directory + default id-token + default filename
# prefix. The token/prefix are only *defaults*: when the plane directory already
# has scenarios we auto-detect the real token + prefix from disk so we stay in
# lockstep with the existing corpus (e.g. AI_ACCESS ids are SIM-AIACC-NNN, not
# SIM-AI_ACCESS-NNN).
PLANE_TABLE = {
    "EDR":       {"dir": "edr",        "token": "EDR",     "prefix": "edr"},
    "CDR":       {"dir": "cdr",        "token": "CDR",     "prefix": "cdr"},
    "NDR":       {"dir": "ndr",        "token": "NDR",     "prefix": "ndr"},
    "ITDR":      {"dir": "itdr",       "token": "ITDR",    "prefix": "itdr"},
    "CLOUD_APP": {"dir": "cloud_app",  "token": "CLOUD",   "prefix": "cloud"},
    "ANALYTICS": {"dir": "multi_plane","token": "MP",      "prefix": "mp"},
    "AI_ACCESS": {"dir": "ai_access",  "token": "AIACC",   "prefix": "ai-access"},
    "AIRS":      {"dir": "airs",       "token": "AIRS",    "prefix": "sim-airs"},
    "AI_SPM":    {"dir": "ai_spm",     "token": "AISPM",   "prefix": "ai-spm"},
    "BROWSER":   {"dir": "browser",    "token": "BROWSER", "prefix": "sim-browser"},
    "KOI":       {"dir": "koi",        "token": "KOI",     "prefix": "sim-koi"},
    "ASM":       {"dir": "asm",        "token": "ASM",     "prefix": "asm"},
    "CSPM":      {"dir": "cspm",       "token": "CSPM",    "prefix": "cspm"},
    "TIM":       {"dir": "tim",        "token": "TIM",     "prefix": "tim"},
    "EMAIL":     {"dir": "email",      "token": "EMAIL",   "prefix": "email"},
}

# Plane → schema `pov_engine.simulation_class` (enum in the TTP schema).
SIM_CLASS = {
    "EDR": "endpoint", "CDR": "container", "NDR": "network", "ITDR": "identity",
    "CLOUD_APP": "cloud", "ANALYTICS": "endpoint", "AI_ACCESS": "web",
    "AIRS": "web", "AI_SPM": "cloud", "BROWSER": "web", "KOI": "supply-chain",
    "ASM": "network", "CSPM": "cloud", "TIM": "network", "EMAIL": "email",
}


# ── slug logic (must match core/engine/ttp_catalog.py:_slug) ─────────────────
def slug(name: str, prefix: str) -> str:
    """Stable detection_id slug — byte-for-byte the loader's algorithm so a
    scenario's detection_id resolves to the card's synthesized detection id."""
    clean = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in clean:
        clean = clean.replace("--", "-")
    return f"{prefix}-{clean}"[:120]


def filename_slug(text: str) -> str:
    """Short kebab slug for a filename component."""
    clean = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    while "--" in clean:
        clean = clean.replace("--", "-")
    parts = [p for p in clean.split("-") if p]
    return "-".join(parts[:6]) or "scaffold"


# ── discovery ────────────────────────────────────────────────────────────────
def discover_ttp_numbers(root: Path) -> set[int]:
    """Every TTP-2026-NNNN number already claimed (by id field or filename)."""
    nums: set[int] = set()
    for f in glob.glob(str(root / TTPS_DIR / "*.json")):
        stem = Path(f).name
        for _year, num in TTP_ID_RE.findall(stem):
            nums.add(int(num))
        # also read the id field in case a file was renamed
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
            m = TTP_ID_RE.match(str(data.get("id", "")))
            if m:
                nums.add(int(m.group(2)))
        except (OSError, json.JSONDecodeError):
            continue
    return nums


def next_ttp_id(root: Path, year: int = 2026) -> str:
    """Next free, never-reused TTP id: max(existing) + 1 (monotonic per schema)."""
    nums = discover_ttp_numbers(root)
    nxt = (max(nums) + 1) if nums else 1
    return f"TTP-{year}-{nxt:04d}"


def resolve_plane(plane: str, root: Path) -> dict:
    """Resolve a plane to {dir, token, prefix, next_num, sid} — auto-detecting
    the id-token, filename prefix, and next scenario number from disk when the
    plane directory already has scenarios."""
    plane = plane.upper()
    if plane not in PLANE_TABLE:
        raise ValueError(
            f"unknown plane {plane!r}; valid planes: {', '.join(sorted(PLANES))}"
        )
    info = dict(PLANE_TABLE[plane])
    sdir = root / SCENARIOS_DIR / info["dir"]

    token_counts: dict[str, int] = {}
    max_num = 0
    prefix_counts: dict[str, int] = {}
    if sdir.is_dir():
        for f in sorted(sdir.glob("*.yml")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if line.startswith("scenario_id:"):
                    sid_val = line.split(":", 1)[1].strip().strip('"\'')
                    m = SCENARIO_ID_RE.match(sid_val)
                    if m:
                        token_counts[m.group(1)] = token_counts.get(m.group(1), 0) + 1
                        max_num = max(max_num, int(m.group(2)))
                    break
            # filename prefix, e.g. "edr-010-foo.yml" → "edr"
            fm = re.match(r"^(.*?)-(\d{3,})-.*\.yml$", f.name)
            if fm:
                prefix_counts[fm.group(1)] = prefix_counts.get(fm.group(1), 0) + 1

    if token_counts:
        info["token"] = max(token_counts, key=token_counts.get)
    if prefix_counts:
        info["prefix"] = max(prefix_counts, key=prefix_counts.get)

    info["next_num"] = max_num + 1
    info["nnn"] = f"{info['next_num']:03d}"
    info["sid"] = f"SIM-{info['token']}-{info['nnn']}"
    info["plane"] = plane
    return info


# ── card skeleton ────────────────────────────────────────────────────────────
def _clamp(text: str, lo: int, hi: int, pad: str) -> str:
    text = (text or "").strip()
    if len(text) < lo:
        text = (text + " " + pad).strip()
    return text[:hi]


def build_card(*, ttp_id: str, plane_info: dict, title: str, url: str,
               summary: str, now: str | None = None) -> dict:
    """Build a schema-compliant TTP card dict wired to `plane_info`'s scenario.

    The three detection stubs (one ABIOC, one validation XQL, one correlation)
    carry REAL, lint-clean XQL bodies against xdr_data so validate.py's GAP-12
    grammar lint passes unedited; the DC replaces the bodies with the technique
    from the research. Their names determine the detection_id slugs the matching
    scenario references."""
    now = now or _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    sid = plane_info["sid"]
    token = plane_info["token"]
    nnn = plane_info["nnn"]
    plane = plane_info["plane"]

    abioc_name = f"{sid} Behavioral Anomaly Scaffold"
    xql_name = f"{sid} Validation Query Scaffold"
    corr_name = f"{sid} Correlation Stitch Scaffold"
    corr_rule_id = f"CR-{token}-{int(nnn):04d}"

    plane_tag = plane.lower().replace("_", "-")

    identity_name = _clamp(
        f"{title} — {plane} scaffold ({sid})",
        4, 140, "CortexSim research-intake scaffold",
    )
    identity_summary = _clamp(
        f"[SCAFFOLD] {summary} Replace this summary and the detection bodies "
        f"below with the real technique from the source report before promoting.",
        20, 400, "CortexSim research-intake scaffold — replace with real detail.",
    )

    return {
        "id": ttp_id,
        "schema_version": "1.0.0",
        "entry_version": "0.1.0",
        "status": "draft",
        "metadata": {
            "created_at": now,
            "updated_at": now,
            "authors": [
                {
                    "name": "Henry Reed",
                    "email": "hreed@paloaltonetworks.com",
                    "role": "author",
                }
            ],
            "tags": [
                f"plane-{plane_tag}",
                "research-intake",
                "scaffold",
                "severity-medium",
            ],
            "source_refs": ["SRC-MITRE-ATTACK", "SRC-PANW-TECHDOCS"],
            "pov_engine": {
                "engine_version_min": "0.1.0",
                "auto_load": False,
                "simulation_class": SIM_CLASS.get(plane, "endpoint"),
                "destructive": False,
                "requires_network_egress": False,
                "requires_internet": False,
                "platforms": ["linux"],
                "estimated_runtime_seconds": 300,
                "cleanup_required": True,
                "safety_class": "safe-by-design",
            },
        },
        "identity": {
            "name": identity_name,
            "summary": identity_summary,
            "description": (
                f"Research-intake scaffold generated from '{title}' ({url}). "
                f"This card is a schema-valid starting point: fill in the real "
                f"actors, MITRE techniques, execution payload, and — most "
                f"importantly — the detection logic (ABIOC / XQL / correlation) "
                f"from the source research, then flip status to 'active'."
            ),
            "severity": "medium",
            "confidence": "medium",
        },
        "threat_context": {
            "actors": [],
            "campaigns": [],
            "malware_families": [],
            "industries_targeted": ["all"],
        },
        "mitre_attack": {
            "matrix": "enterprise",
            "techniques": [
                {
                    "technique_id": "T1059",
                    "name": "Command and Scripting Interpreter",
                    "tactic_ids": ["TA0002"],
                    "tactic_names": ["Execution"],
                }
            ],
            "kill_chain_phase": "exploitation",
            "data_sources": [
                "Process: Process Creation",
                "Command: Command Execution",
            ],
        },
        "execution": {
            "target_platform": "linux",
            "execution_framework": "custom-cortex",
            "framework_reference_id": (
                f"{sid} — research-intake scaffold; replace with the real "
                f"technique from the source report."
            ),
            "privilege_required": "user",
            "prerequisites": [
                {
                    "description": (
                        "Cortex XDR / XSIAM agent installed on the target so "
                        "per-process telemetry reaches xdr_data."
                    ),
                    "check_command": "systemctl status cortex-agent || ls /opt/traps",
                }
            ],
            "payload": {
                "interpreter": "bash",
                "code": (
                    "# SCAFFOLD payload — replace with the real technique from "
                    "the research report.\n"
                    "set -euo pipefail\n"
                    "echo '[CortexSim] research-intake scaffold executing "
                    f"{sid}'\n"
                ),
                "input_variables": [
                    {
                        "name": "TARGET_HOST",
                        "type": "string",
                        "default": "127.0.0.1",
                        "description": "Authorised target the scaffolded step acts against.",
                    }
                ],
                "timeout_seconds": 300,
            },
            "expected_artifacts": [
                {
                    "artifact_type": "process",
                    "description": (
                        "Process telemetry the scaffolded step produces; replace "
                        "with the real observable from the research."
                    ),
                }
            ],
            "cleanup": {
                "interpreter": "bash",
                "code": f"echo '[CortexSim] {sid} cleanup complete.'",
            },
        },
        "detections": {
            "iocs": [],
            "biocs": [],
            "abiocs": [
                {
                    "name": abioc_name,
                    "description": (
                        "Scaffolded ABIOC — replace this behavioral logic with "
                        "the real auto-tuned deviation from the source research. "
                        "The body below is a lint-clean placeholder against "
                        "xdr_data so the card validates unedited."
                    ),
                    "logic": (
                        "dataset = xdr_data\n"
                        "| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\n"
                        "| filter actor_effective_username in (\"svc-account\")\n"
                        "| comp count() as observed_events by agent_hostname, actor_effective_username\n"
                        "| filter observed_events >= 1\n"
                        "| fields agent_hostname, actor_effective_username, observed_events"
                    ),
                    "severity": "medium",
                    "mitre_technique_ids": ["T1059"],
                    "behavioral_profile": "machine-learning",
                    "causality_anchor": (
                        "Replace with the learned user/endpoint/network baseline "
                        "the real ABIOC anchors its deviation to."
                    ),
                }
            ],
            "xql_queries": [
                {
                    "purpose": "validation",
                    "name": xql_name,
                    "query": (
                        "dataset = xdr_data\n"
                        "| filter _time > to_timestamp(current_time() - 900)\n"
                        "| filter actor_effective_username = \"svc-account\"\n"
                        "| comp count() as observed_events by agent_hostname\n"
                        "| filter observed_events >= 1\n"
                        "| fields agent_hostname, observed_events"
                    ),
                    "expected_rows_min": 0,
                    "dataset": "xdr_data",
                }
            ],
            "correlation_rules": [
                {
                    "name": corr_name,
                    "rule_id": corr_rule_id,
                    "logic": (
                        f"Fires when abioc `{abioc_name}` and xql `{xql_name}` "
                        f"both observe activity on the same agent_hostname and "
                        f"actor_effective_username within the run window, "
                        f"stitching the scaffolded signals into one incident. "
                        f"Substitute the real correlation from the research before promoting."
                    ),
                }
            ],
            "analytics_modules": [],
        },
        "panw_mapping": {
            "products": [
                {
                    "module": "cortex-xsiam",
                    "submodule": "analytics",
                    "coverage_tier": "detection",
                    "rule_ids": [corr_rule_id],
                    "license_required": "advanced",
                    "evidence_query": (
                        "Replace with the XQL the DC runs in the product UI to "
                        "surface evidence for this technique."
                    ),
                }
            ],
            "threat_scenarios": [
                {
                    "threat_scenario_id": f"TS-{token}-{nnn}",
                    "name": f"{sid} — research-intake scaffold use case",
                    "description": (
                        "Scaffolded POV use case; replace with the real customer "
                        "narrative the DC runs for this technique."
                    ),
                    "threat_steps": [
                        {
                            "threat_step_id": f"TS-{token}-{nnn}A",
                            "objective": (
                                "The scaffolded detection fires when the real "
                                "technique from the research is executed."
                            ),
                            "preconditions": [
                                "Target with the Cortex agent installed."
                            ],
                            "steps": [
                                "Execute the technique from the source research.",
                                "Observe the ABIOC / correlation fire.",
                            ],
                            "success_criteria": [
                                f"Correlation `{corr_rule_id}` raises one incident.",
                            ],
                            "expected_score_weight": 1.0,
                        }
                    ],
                }
            ],
        },
        "remediation_guidance": {
            "preventive_controls": [
                "Replace with the preventive controls from the source research.",
            ],
            "detection_engineering": [
                "Replace with the detection-engineering guidance from the research.",
            ],
        },
        "references": [
            {
                "title": title,
                "url": url,
                "publisher": "Threat Research (research-intake)",
                "primary": True,
            },
            {
                "title": "MITRE ATT&CK — T1059 Command and Scripting Interpreter",
                "url": "https://attack.mitre.org/techniques/T1059/",
                "publisher": "MITRE",
                "publisher_id": "SRC-MITRE-ATTACK",
            },
            {
                "title": "Cortex XSIAM — Analytics / detection reference",
                "url": "https://docs-cortex.paloaltonetworks.com/",
                "publisher": "Palo Alto Networks TechDocs",
                "publisher_id": "SRC-PANW-TECHDOCS",
            },
        ],
    }


# ── scenario skeleton ────────────────────────────────────────────────────────
def build_scenario_yaml(*, ttp_id: str, plane_info: dict, title: str,
                        url: str, summary: str) -> str:
    """Render a lint-clean scenario YAML wired to the card's detection ids.

    Emitted by hand (no pyyaml) so quoting stays under our control. Detection
    ids are computed with the same slug algorithm the loader uses, so every
    detection_id resolves to the card built by build_card()."""
    sid = plane_info["sid"]
    plane = plane_info["plane"]
    token = plane_info["token"]
    nnn = plane_info["nnn"]

    abioc_name = f"{sid} Behavioral Anomaly Scaffold"
    xql_name = f"{sid} Validation Query Scaffold"
    corr_rule_id = f"CR-{token}-{int(nnn):04d}"

    abioc_id = slug(abioc_name, "abioc")
    xql_id = slug(xql_name, "xql")
    corr_id = corr_rule_id  # rule_id wins in the loader's slug resolution

    # Keep the scenario name YAML-safe: strip embedded double-quotes.
    safe_name = title.replace('"', "'")[:120]

    return f"""scenario_id: {sid}
name: "{safe_name} (research-intake scaffold)"
version: "0.1"
status: draft

plane: {plane}
detection_types:
  - ABIOC
  - XQL
  - Correlation

uc_ref: UC-{token}-{nnn}
tc_ref: TC-{token}-{nnn}
uc_name: "Research-intake scaffold — replace with the real use case"
tc_name: "Research-intake scaffold — replace with the real test case"

mitre_tactic: "TA0002"
mitre_tactic_name: "Execution"
mitre_technique: "T1059"
mitre_technique_name: "Command and Scripting Interpreter"

threat_report: "{safe_name}"
threat_report_url: "{url}"

# SCAFFOLD — generated by scripts/research_intake.py from a weekly threat report.
# Fill in the real steps + detection logic from the research, then set
# status: active on this file and on the matching card {ttp_id}.
# Source summary: {summary[:200]}

execution_identity:
  default: svc-account
  options:
    - svc-account
    - www-data
    - root

push_supported: true
pull_supported: true

external_tools: []

steps:
  - id: step-01
    name: "Scaffolded technique step (replace with the real research technique)"
    command: "echo 'cortexsim-{token.lower()}-{nnn}-scaffold-behavioral-step' || true"
    identity: svc-account
    mitre_technique: "T1059"
    expected_detections:
      - plane: {plane}
        type: ABIOC
        description: "Scaffolded ABIOC detection — replace with the real behavioral deviation the source research describes."
        ttp_ref: "{ttp_id}"
        detection_id: "{abioc_id}"
      - plane: {plane}
        type: XQL
        description: "Scaffolded validation query — confirms the scaffolded activity is observable in xdr_data within the run window."
        ttp_ref: "{ttp_id}"
        detection_id: "{xql_id}"

  - id: step-02
    name: "Correlation stitches the scaffolded signals into one incident"
    command: "echo 'cortexsim-{token.lower()}-{nnn}-scaffold-correlation-stitch' || true"
    identity: svc-account
    mitre_technique: "T1059"
    expected_detections:
      - plane: {plane}
        type: Correlation
        description: "Scaffolded correlation rule — replace with the real cross-signal stitch from the research."
        ttp_ref: "{ttp_id}"
        detection_id: "{corr_id}"

cleanup:
  commands:
    - "echo 'cortexsim-{token.lower()}-{nnn}-scaffold-cleanup-complete'"
"""


# ── input plumbing ───────────────────────────────────────────────────────────
def parse_intake_file(path: Path) -> dict:
    """Parse a simple `key: value` intake file (title / url / summary / plane)."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        if key in {"title", "url", "summary", "plane"}:
            out[key] = val.strip()
    return out


def _card_filename(ttp_id: str, plane_info: dict) -> str:
    return f"{ttp_id}-sim-{plane_info['token'].lower()}-{plane_info['nnn']}.json"


def _scenario_filename(plane_info: dict, title: str) -> str:
    return f"{plane_info['prefix']}-{plane_info['nnn']}-{filename_slug(title)}.yml"


def next_steps_message(*, card_path: Path, scenario_path: Path, ttp_id: str,
                       sid: str, root: Path) -> str:
    rel_card = card_path.relative_to(root)
    rel_scn = scenario_path.relative_to(root)
    return (
        "\nNext steps (weekly research → product):\n"
        f"  1. Fill in the REAL detections in {rel_card}\n"
        "       - replace the abioc / xql / correlation logic bodies with the\n"
        "         technique from the research (keep the detection NAMES so the\n"
        "         scenario's detection_id slugs keep resolving), set real actors,\n"
        "         MITRE techniques, and payload, then flip status draft -> active.\n"
        f"  2. Fill in the REAL steps in {rel_scn} and set status: active.\n"
        "  3. Validate:\n"
        "       python3 detection_scanner/scripts/validate.py\n"
        f"       python3 .claude/hooks/lint-scenario.py {rel_scn}\n"
        "  4. Regenerate detection exports if your workflow requires it, then\n"
        f"     commit the pair:  git add {rel_card} {rel_scn}\n"
        f"       git commit -m 'feat({sid.split('-')[1].lower()}): {sid} from weekly research intake'\n"
    )


# ── main ─────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scaffold a schema-valid TTP card + scenario from a threat report.",
    )
    ap.add_argument("--title", help="threat report title")
    ap.add_argument("--url", help="threat report url")
    ap.add_argument("--summary", help="one-line summary of the technique")
    ap.add_argument("--file", help="intake file with key: value lines "
                                    "(title/url/summary/plane)")
    ap.add_argument("--plane", default="EDR",
                    help=f"detection plane (default EDR); one of {', '.join(sorted(PLANES))}")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the scaffolded card + scenario, write nothing")
    ap.add_argument("--root", default=str(REPO_ROOT),
                    help="repo root (default: auto-detected)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()

    fields = {"title": args.title, "url": args.url,
              "summary": args.summary, "plane": args.plane}
    if args.file:
        parsed = parse_intake_file(Path(args.file))
        for key, val in parsed.items():
            # CLI flags win over file values only when explicitly given.
            if key == "plane" and args.plane != "EDR":
                continue
            if not fields.get(key):
                fields[key] = val
        # file plane overrides the EDR default if CLI didn't set one
        if parsed.get("plane") and args.plane == "EDR":
            fields["plane"] = parsed["plane"]

    missing = [k for k in ("title", "url", "summary") if not fields.get(k)]
    if missing:
        ap.error(f"missing required input(s): {', '.join(missing)} "
                 f"(pass via --{'/--'.join(missing)} or --file)")

    try:
        plane_info = resolve_plane(fields["plane"], root)
    except ValueError as exc:
        ap.error(str(exc))

    ttp_id = next_ttp_id(root)

    card = build_card(
        ttp_id=ttp_id, plane_info=plane_info,
        title=fields["title"], url=fields["url"], summary=fields["summary"],
    )
    scenario_yaml = build_scenario_yaml(
        ttp_id=ttp_id, plane_info=plane_info,
        title=fields["title"], url=fields["url"], summary=fields["summary"],
    )

    card_path = root / TTPS_DIR / _card_filename(ttp_id, plane_info)
    scenario_path = (root / SCENARIOS_DIR / plane_info["dir"]
                     / _scenario_filename(plane_info, fields["title"]))
    card_json = json.dumps(card, indent=2, ensure_ascii=False) + "\n"

    print(f"plane            : {plane_info['plane']}")
    print(f"next TTP id      : {ttp_id}")
    print(f"next scenario id : {plane_info['sid']}")
    print(f"card path        : {card_path.relative_to(root)}")
    print(f"scenario path    : {scenario_path.relative_to(root)}")

    if args.dry_run:
        print("\n--- DRY RUN: card JSON ---\n")
        print(card_json)
        print("--- DRY RUN: scenario YAML ---\n")
        print(scenario_yaml)
        print("(dry-run: nothing written)")
        return 0

    if card_path.exists() or scenario_path.exists():
        existing = [str(p.relative_to(root)) for p in (card_path, scenario_path)
                    if p.exists()]
        ap.error(f"refusing to overwrite existing file(s): {', '.join(existing)}")

    card_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(card_json, encoding="utf-8")
    scenario_path.write_text(scenario_yaml, encoding="utf-8")

    print(f"\nwrote {card_path.relative_to(root)}")
    print(f"wrote {scenario_path.relative_to(root)}")
    print(next_steps_message(card_path=card_path, scenario_path=scenario_path,
                             ttp_id=ttp_id, sid=plane_info["sid"], root=root))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
detection_scanner/scripts/generate_card.py
------------------------------------------

Skeleton TTP card generator. Given a CortexSim scenario YAML and a destination
TTP id, produces a schema-valid TTP entry under ``ttps/_drafts/`` with:

* identity (name, summary, description) lifted from the scenario
* mitre_attack block built from the scenario's primary technique +
  additional_techniques
* execution skeleton derived from the first 1-3 steps' command strings
* detections shell: one BIOC stub per scenario expected_detection, with the
  BIOC name + description + logic placeholder filled in
* panw_mapping skeleton with the primary detection plane mapped to its
  Cortex module
* references block referencing the scenario's threat_report_url

The output is **always** a draft (status: draft). A human enriches the BIOC
``logic`` field with real XQL, validates score weights, and promotes the
file to ``ttps/`` after review per detection_scanner/RUNBOOK.md.

Usage::

    python3 detection_scanner/scripts/generate_card.py \\
        scenarios/edr/edr-001-credential-dumping.yml TTP-2026-0007

    python3 detection_scanner/scripts/generate_card.py \\
        --batch scenarios/cdr/cdr-001-container-enum.yml=TTP-2026-0010 \\
        --batch scenarios/cdr/cdr-002-cryptominer.yml=TTP-2026-0011

The companion ``scripts/validate.py`` checks the draft against the schema
before promotion.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


ROOT = Path(__file__).resolve().parent.parent
DRAFTS_DIR = ROOT / "ttps" / "_drafts"


# ---------------------------------------------------------------------------
# Plane → Cortex product mapping (drives panw_mapping.products skeleton)
# ---------------------------------------------------------------------------

_PLANE_TO_PRODUCT: dict[str, list[str]] = {
    "EDR": ["cortex-xdr"],
    "CDR": ["cortex-cloud", "prisma-cloud"],
    "NDR": ["cortex-xsiam", "ngfw-pa-series"],
    "ITDR": ["cortex-xdr", "cortex-xsiam"],
    "CLOUD_APP": ["cortex-xsiam"],
    "ANALYTICS": ["cortex-xsiam", "cortex-xsoar"],
    "AI_ACCESS": ["ai-access-security"],
    "AIRS": ["ai-runtime-security"],
    "BROWSER": ["cortex-xsiam"],
    "KOI": ["cortex-xdr", "cortex-xsiam"],
}

# simulation_class enum per schema:
#   endpoint | identity | cloud | network | email | web | container
#   | kubernetes | ot-iot | data-exfil | ransomware-chain | supply-chain
_PLANE_TO_SIMULATION_CLASS: dict[str, str] = {
    "EDR": "endpoint",
    "CDR": "cloud",
    "NDR": "network",
    "ITDR": "identity",
    "CLOUD_APP": "cloud",
    "ANALYTICS": "ransomware-chain",  # multi-plane stitching is best-aligned here
    "AI_ACCESS": "web",
    "AIRS": "web",
    "BROWSER": "web",
    "KOI": "supply-chain",
}

# metadata.pov_engine.platforms[] enum per schema:
#   windows | linux | macos | android | ios | aws | azure | gcp | oci
#   | kubernetes | saas-m365 | saas-google-workspace | saas-okta
#   | saas-salesforce | network-fabric
# (NB: this is a different enum from execution.target_platform — there's
# no `cross-platform` here. SaaS scenarios pick a concrete SaaS target.)
_PLANE_TO_PLATFORM: dict[str, list[str]] = {
    "EDR": ["linux", "windows"],
    "CDR": ["linux", "kubernetes"],
    "NDR": ["linux", "network-fabric"],
    "ITDR": ["windows"],
    "CLOUD_APP": ["saas-okta", "saas-m365", "saas-google-workspace"],
    "ANALYTICS": ["linux", "windows"],
    "AI_ACCESS": ["aws", "azure", "gcp"],
    "AIRS": ["linux"],
    "BROWSER": ["windows", "macos"],
    "KOI": ["linux", "macos"],
}

# execution.target_platform enum (single string, different from platforms[]):
#   windows | linux | macos | aws | azure | gcp | kubernetes | saas-m365
#   | saas-okta | network-fabric | cross-platform
def _target_platform_for_plane(plane: str) -> str:
    return {
        "EDR": "linux", "CDR": "linux", "NDR": "linux", "ITDR": "windows",
        "CLOUD_APP": "cross-platform", "ANALYTICS": "cross-platform",
        "AI_ACCESS": "cross-platform", "AIRS": "linux",
        "BROWSER": "windows", "KOI": "linux",
    }.get(plane, "linux")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# UC domains permitted by detection_scanner/schema/ttp-entry.schema.json:
# IDENT, CLOUD, RANSOM, INSIDER, SUPPLY, OT, EMAIL, WEB.
# Map every CortexSim plane onto the closest legal UC domain so generated
# drafts pass schema validation; the human reviewer can re-classify during
# promotion to active.
_PLANE_TO_UC_DOMAIN: dict[str, str] = {
    "EDR": "RANSOM",
    "CDR": "CLOUD",
    "NDR": "WEB",
    "ITDR": "IDENT",
    "CLOUD_APP": "CLOUD",
    "ANALYTICS": "RANSOM",
    "AI_ACCESS": "INSIDER",
    "AIRS": "WEB",
    "BROWSER": "WEB",
    "KOI": "SUPPLY",
}


def _uc_domain_for_plane(plane: str) -> str:
    return _PLANE_TO_UC_DOMAIN.get(plane, "RANSOM")


def _bioc_name_from_detection(description: str, scenario_id: str, idx: int) -> str:
    """Lift a BIOC display name from the scenario's expected_detection
    description. The description is human prose; we just trim to a sane
    length and ensure it ends without trailing punctuation."""
    name = description.strip().strip(".").strip()
    if len(name) > 120:
        name = name[:117].rsplit(" ", 1)[0] + "..."
    return name or f"{scenario_id} detection {idx + 1}"


def _bioc_logic_skeleton(plane: str, scenario_technique: str) -> str:
    """Emit a starter XQL skeleton tagged for human enrichment.

    The skeleton is deliberately incomplete — it surfaces the dataset and
    filter scaffolding the human needs but leaves the *specific* predicates
    blank. A draft card with a working skeleton is more useful than one
    with hallucinated XQL.
    """
    dataset_hint = {
        "EDR": "preset = xdr_data\n| filter event_type = ENUM.PROCESS",
        "ITDR": "dataset = msft_windows_security\n| filter event_id IN (4624, 4625, 4662, 4768, 4769)",
        "NDR": "preset = xdr_data\n| filter event_type = ENUM.NETWORK",
        "CDR": "preset = xdr_data\n| filter cloud_provider = \"aws\"",
        "CLOUD_APP": "dataset = okta_audit OR dataset = azure_signin OR dataset = google_workspace_audit",
        "ANALYTICS": "// Multi-plane correlation — combine BIOCs across planes",
        "AI_ACCESS": "dataset = http_proxy\n| filter dst_host IN (\"api.openai.com\", \"api.anthropic.com\", \"generativelanguage.googleapis.com\")",
        "AIRS": "dataset = airs_telemetry\n| filter model_endpoint != null",
        "BROWSER": "dataset = browser_telemetry",
        "KOI": "preset = xdr_data\n| filter event_type = ENUM.PROCESS",
    }.get(plane, "preset = xdr_data")

    return (
        f"// AUTO-GENERATED SKELETON — replace with real XQL before promotion.\n"
        f"// MITRE technique: {scenario_technique}\n"
        f"{dataset_hint}\n"
        f"| filter /* TODO: predicate matching the BIOC name */\n"
        f"| fields _time, agent_hostname, actor_process_image_name, action_process_image_command_line"
    )


def _expected_artifacts_from_commands(steps: list[dict]) -> list[dict]:
    """Best-effort heuristic: scan step commands for well-known indicators
    and emit matching expected_artifacts entries. The point is to seed
    the human's editing — every entry is clearly marked as a heuristic
    derivation in the description."""
    artifacts: list[dict] = []
    seen: set[str] = set()

    for step in steps:
        cmd = (step.get("command") or "").lower()
        if not cmd:
            continue

        # Process artifacts — well-known binaries the scenario invokes.
        for binary in ("mimikatz", "mimipenguin", "impacket", "secretsdump",
                       "rundll32", "cmd.exe", "powershell", "wmic",
                       "psexec", "wmiexec", "curl", "wget", "nc ", "ncat",
                       "rclone", "msbuild", "regsvr32", "certutil",
                       "nmap", "masscan", "sqlmap", "kubectl", "docker"):
            if binary.strip() in cmd and binary not in seen:
                seen.add(binary)
                artifacts.append({
                    "artifact_type": "process",
                    "description": f"Process execution matching {binary.strip()} (heuristic from scenario step {step.get('id', '?')})",
                    "expected_value": f"process_name CONTAINS \"{binary.strip()}\"",
                })

        # File artifacts — common indicator extensions.
        for ext, desc in (
            (".dmp", "memory dump file"),
            (".ps1", "PowerShell script"),
            (".tttt", "BlackSuit-style encryption marker"),
            (".enc", "encrypted-output marker"),
            ("id_rsa", "SSH private key"),
            ("/etc/shadow", "Linux credential file"),
            ("/etc/passwd", "Linux account database"),
        ):
            if ext in cmd and ext not in seen:
                seen.add(ext)
                artifacts.append({
                    "artifact_type": "file",
                    "description": f"File access pattern matching '{ext}' ({desc}) (heuristic from scenario step {step.get('id', '?')})",
                    "expected_value": f"file_path CONTAINS \"{ext}\"",
                })

        # Network artifacts.
        if "https://" in cmd or "http://" in cmd:
            if "outbound-http" not in seen:
                seen.add("outbound-http")
                artifacts.append({
                    "artifact_type": "network-connection",
                    "description": "Outbound HTTP(S) request from the scenario step",
                    "expected_value": "dst_port IN (80, 443) AND src_host = scenario_target",
                })

    return artifacts or [{
        "artifact_type": "process",
        "description": "TODO — author at least one concrete expected artifact for this scenario",
        "expected_value": "TODO",
    }]


def _scenario_to_card(scenario: dict, ttp_id: str) -> dict:
    """Build a complete schema-valid TTP entry from a scenario dict."""
    plane = scenario.get("plane", "EDR")
    primary_tid = scenario.get("mitre_technique", "T1059")
    primary_tid_root = primary_tid.split(".")[0]
    sub_tid = primary_tid if "." in primary_tid else None
    additional = scenario.get("additional_techniques", []) or []

    techniques: list[dict] = [{
        "technique_id": primary_tid_root,
        "name": scenario.get("mitre_technique_name", "TBD"),
        "tactic_ids": [scenario.get("mitre_tactic", "TA0001")],
        "tactic_names": [scenario.get("mitre_tactic_name", "TBD")],
    }]
    if sub_tid:
        techniques[0]["subtechnique_id"] = sub_tid
    for extra in additional:
        if not isinstance(extra, dict):
            continue
        t = extra.get("technique") or ""
        if not t:
            continue
        techniques.append({
            "technique_id": t.split(".")[0],
            "name": extra.get("name", "TBD"),
            "tactic_ids": [scenario.get("mitre_tactic", "TA0001")],
            "tactic_names": [scenario.get("mitre_tactic_name", "TBD")],
            **({"subtechnique_id": t} if "." in t else {}),
        })

    steps = scenario.get("steps", []) or []
    first_step = steps[0] if steps else {}
    interpreter = "bash" if plane in ("EDR", "CDR", "NDR", "AIRS", "KOI", "CLOUD_APP", "AI_ACCESS", "BROWSER") else "powershell"

    payload_code = "\n".join(
        f"# step-{i+1}: {s.get('name', '')}\n{s.get('command', '').strip()}"
        for i, s in enumerate(steps[:3])
    ) or "# TODO — author the canonical exploitation payload"

    # Build one BIOC stub per expected_detection across all steps.
    biocs: list[dict] = []
    for s in steps:
        for idx, det in enumerate(s.get("expected_detections") or []):
            if det.get("type") not in ("BIOC", "Analytics"):
                continue
            biocs.append({
                "name": _bioc_name_from_detection(
                    det.get("description", ""), scenario.get("scenario_id", "?"), idx,
                ),
                "description": det.get("description", "TODO"),
                "logic": _bioc_logic_skeleton(plane, s.get("mitre_technique", primary_tid)),
                "severity": "high",
                "mitre_technique_ids": [s.get("mitre_technique", primary_tid)],
            })

    panw_products: list[dict] = []
    for module in _PLANE_TO_PRODUCT.get(plane, ["cortex-xdr"]):
        panw_products.append({
            "module": module,
            "coverage_tier": "detection",
            "rule_ids": [f"TBD-{plane}-{ttp_id.split('-')[-1]}"],
            "license_required": "pro",
        })

    threat_url = scenario.get("threat_report_url") or "https://attack.mitre.org/techniques/" + primary_tid_root + "/"
    threat_title = scenario.get("threat_report") or f"MITRE ATT&CK Technique {primary_tid}"

    is_unit42 = "attack.mitre.org" not in threat_url
    primary_publisher_id = "SRC-UNIT42" if is_unit42 else "SRC-MITRE-ATTACK"
    references: list[dict] = [
        {
            "title": threat_title,
            "url": threat_url,
            "publisher": "MITRE" if not is_unit42 else (scenario.get("threat_report", "Unit 42").split(" - ")[0] or "Unit 42"),
            "publisher_id": primary_publisher_id,
            "primary": True,
        },
        {
            "title": f"MITRE ATT&CK {primary_tid}",
            "url": f"https://attack.mitre.org/techniques/{primary_tid_root}/",
            "publisher": "MITRE",
            "publisher_id": "SRC-MITRE-ATTACK",
        },
    ]
    # metadata.source_refs must be a superset of every references[].publisher_id
    # (canonical validator enforces this) — build the set lazily so the
    # metadata block below picks it up.
    source_refs = sorted({primary_publisher_id, "SRC-MITRE-ATTACK"})

    summary = (
        f"Auto-generated draft from CortexSim scenario {scenario.get('scenario_id', '?')}. "
        f"Plane: {plane}. Primary technique: {primary_tid}. "
        f"Requires human enrichment of BIOC logic + panw_mapping rule_ids."
    )

    return {
        "id": ttp_id,
        "schema_version": "1.0.0",
        "entry_version": "0.1.0",
        "status": "draft",
        "metadata": {
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "authors": [{"name": "CortexSim generate_card.py", "role": "author"}],
            "tags": [
                # Tag regex is lower-kebab; convert underscores from the
                # plane enum (AI_ACCESS, CLOUD_APP) to hyphens.
                f"plane-{plane.lower().replace('_', '-')}",
                "auto-generated",
                "skeleton-draft",
                "severity-high",
            ],
            "source_refs": source_refs,
            "pov_engine": {
                "engine_version_min": "0.1.0",
                "auto_load": False,  # drafts never auto-load
                "simulation_class": _PLANE_TO_SIMULATION_CLASS.get(plane, "endpoint"),
                "destructive": False,
                "requires_network_egress": True,
                "requires_internet": True,
                "platforms": _PLANE_TO_PLATFORM.get(plane, ["linux"]),
                "estimated_runtime_seconds": 120,
                "cleanup_required": True,
                "safety_class": "lab-only",
            },
        },
        "identity": {
            "name": scenario.get("name", scenario.get("scenario_id", ttp_id)),
            "summary": summary[:400],
            "description": (
                f"{summary}\n\n"
                f"Source scenario: {scenario.get('scenario_id')}.\n"
                f"UC: {scenario.get('uc_name', '?')} / TC: {scenario.get('tc_name', '?')}."
            ),
            "severity": "high",
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
            "techniques": techniques,
            "kill_chain_phase": "actions-on-objectives",
            "data_sources": [],
        },
        "execution": {
            "target_platform": _target_platform_for_plane(plane),
            "execution_framework": "atomic-red-team",
            "framework_reference_id": f"{primary_tid} (TODO — pin atomic test ref)",
            "privilege_required": "user",
            "prerequisites": [{
                "description": f"Lab environment matching scenario {scenario.get('scenario_id', '?')} requirements.",
            }],
            "payload": {
                "interpreter": interpreter,
                "code": payload_code,
                "input_variables": [],
                "timeout_seconds": 120,
            },
            "expected_artifacts": _expected_artifacts_from_commands(steps),
            "cleanup": {
                "interpreter": interpreter,
                "code": "\n".join((scenario.get("cleanup") or {}).get("commands") or ["# TODO"]),
            },
        },
        "detections": {
            "iocs": [],
            "biocs": biocs or [{
                "name": f"{scenario.get('scenario_id', '?')} detection 1",
                "description": "TODO",
                "logic": _bioc_logic_skeleton(plane, primary_tid),
                "severity": "high",
                "mitre_technique_ids": [primary_tid],
            }],
            "xql_queries": [],
            "correlation_rules": [],
            "analytics_modules": [],
        },
        "panw_mapping": {
            "products": panw_products,
            "use_cases": [{
                # UC / TC ids must match ^UC-[A-Z0-9]+-[0-9]{3}$ and
                # ^TC-[A-Z0-9]+-[0-9]{3}[A-Z]?$ per the corpus schema —
                # use the trailing 3 digits of the TTP id.
                "use_case_id": f"UC-{_uc_domain_for_plane(plane)}-{ttp_id.split('-')[-1][-3:]}",
                "name": scenario.get("uc_name", "TBD"),
                "description": scenario.get("uc_name", "TBD"),
                "test_cases": [{
                    "test_case_id": f"TC-{_uc_domain_for_plane(plane)}-{ttp_id.split('-')[-1][-3:]}A",
                    "objective": "TBD — author concrete pass/fail criteria",
                    "success_criteria": ["TBD — author at least one verifiable success criterion"],
                    "expected_score_weight": 1.0,
                }],
            }],
        },
        "references": references,
        "changelog": [{
            "entry_version": "0.1.0",
            "date": _now_iso()[:10],
            "author": "CortexSim generate_card.py",
            "change": f"Initial draft auto-generated from scenario {scenario.get('scenario_id', '?')}.",
        }],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_batch_entry(entry: str) -> tuple[Path, str]:
    if "=" not in entry:
        raise argparse.ArgumentTypeError(f"--batch entry must be PATH=TTP-YYYY-NNNN, got {entry!r}")
    path_str, ttp_id = entry.split("=", 1)
    if not re.match(r"^TTP-\d{4}-\d{4}$", ttp_id):
        raise argparse.ArgumentTypeError(f"bad TTP id format {ttp_id!r}")
    return Path(path_str), ttp_id


def _generate_one(scenario_path: Path, ttp_id: str, dest_dir: Path, force: bool) -> Path:
    if not scenario_path.exists():
        raise FileNotFoundError(scenario_path)
    scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    if not isinstance(scenario, dict):
        raise ValueError(f"{scenario_path}: top-level YAML must be a mapping")
    card = _scenario_to_card(scenario, ttp_id)
    slug = re.sub(r"[^a-z0-9]+", "-", scenario.get("scenario_id", ttp_id).lower()).strip("-")
    out = dest_dir / f"{ttp_id}-{slug}.json"
    if out.exists() and not force:
        raise FileExistsError(f"refusing to overwrite {out} — pass --force to allow")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(card, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", nargs="?", help="Path to scenario YAML")
    ap.add_argument("ttp_id", nargs="?", help="Target TTP id (TTP-YYYY-NNNN)")
    ap.add_argument("--batch", action="append", default=[], type=_parse_batch_entry,
                    help="PATH=TTP-YYYY-NNNN pair; repeatable")
    ap.add_argument("--dest", default=str(DRAFTS_DIR),
                    help="Output directory (default: detection_scanner/ttps/_drafts)")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing draft files")
    args = ap.parse_args()

    pairs: list[tuple[Path, str]] = list(args.batch)
    if args.scenario and args.ttp_id:
        pairs.append((Path(args.scenario), args.ttp_id))
    if not pairs:
        ap.error("supply either positional (SCENARIO TTP-ID) or --batch")

    dest = Path(args.dest)
    for path, ttp_id in pairs:
        out = _generate_one(path, ttp_id, dest, args.force)
        print(f"OK   {ttp_id}  ←  {path.name}  →  {out.relative_to(ROOT)}")

    print(f"\n{len(pairs)} draft(s) written under {dest.relative_to(ROOT)}/")
    print("Next: enrich BIOC logic + panw_mapping.rule_ids, run "
          "`scripts/validate.py`, then move to ttps/ and flip status to active.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

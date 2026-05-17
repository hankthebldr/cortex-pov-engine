#!/usr/bin/env python3
"""
detection_scanner/scripts/build-manifest.py — generates manifest.json.

manifest.json is the cortex-pov-engine's load-time entry point. It enumerates
the active TTP corpus with a small, stable index (id, file, sim class, MITRE
techniques, Cortex modules, use cases) so the engine can build its run plan
without parsing every TTP file up front.

Run after any add/remove/edit of a TTP. CI should fail if manifest.json drifts
from the corpus (check via `git diff --exit-code manifest.json`).

Usage:
  python3 scripts/build-manifest.py
"""

import datetime as dt
import glob
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def git_rev():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def main():
    schema = json.load(open(ROOT / "schema" / "ttp-entry.schema.json"))
    registry = json.load(open(ROOT / "sources" / "source-registry.json"))

    ttps = []
    for f in sorted(glob.glob(str(ROOT / "ttps" / "*.json"))):
        d = json.load(open(f))
        if d.get("status") != "active":
            continue
        ttps.append({
            "id": d["id"],
            "file": str(Path(f).relative_to(ROOT)),
            "sha256": sha256(f),
            "entry_version": d.get("entry_version"),
            "status": d["status"],
            "name": d["identity"]["name"],
            "severity": d["identity"].get("severity"),
            "simulation_class": d["metadata"]["pov_engine"]["simulation_class"],
            "safety_class": d["metadata"]["pov_engine"]["safety_class"],
            "auto_load": d["metadata"]["pov_engine"]["auto_load"],
            "destructive": d["metadata"]["pov_engine"]["destructive"],
            "platforms": d["metadata"]["pov_engine"]["platforms"],
            "engine_version_min": d["metadata"]["pov_engine"].get("engine_version_min"),
            "mitre_techniques": sorted({
                (t.get("subtechnique_id") or t["technique_id"])
                for t in d["mitre_attack"]["techniques"]
            }),
            "mitre_tactics": sorted({
                tid
                for t in d["mitre_attack"]["techniques"]
                for tid in t.get("tactic_ids", [])
            }),
            "panw_modules": sorted({p["module"] for p in d["panw_mapping"]["products"]}),
            "use_cases": [
                {
                    "use_case_id": uc["use_case_id"],
                    "name": uc["name"],
                    "test_case_ids": [tc["test_case_id"] for tc in uc.get("test_cases", [])]
                }
                for uc in d["panw_mapping"].get("use_cases", [])
            ],
            "source_refs": d["metadata"].get("source_refs", []),
            "tags": d["metadata"].get("tags", []),
            "updated_at": d["metadata"]["updated_at"]
        })

    # Aggregate coverage
    all_techniques = sorted({t for entry in ttps for t in entry["mitre_techniques"]})
    all_tactics = sorted({t for entry in ttps for t in entry["mitre_tactics"]})
    all_modules = sorted({m for entry in ttps for m in entry["panw_modules"]})
    all_sim_classes = sorted({entry["simulation_class"] for entry in ttps})

    manifest = {
        "$schema_doc": "schema/ttp-entry.schema.json",
        "manifest_version": "1.0.0",
        "schema_version": schema.get("$id", ""),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "git_revision": git_rev(),
        "corpus": {
            "ttp_count_active": len(ttps),
            "source_count": len(registry["sources"]),
            "source_registry_version": registry.get("registry_version"),
            "mitre_technique_coverage": all_techniques,
            "mitre_tactic_coverage": all_tactics,
            "panw_module_coverage": all_modules,
            "simulation_class_coverage": all_sim_classes
        },
        "ttps": ttps,
        "engine_contract": {
            "loader_order": [
                "manifest.json",
                "schema/ttp-entry.schema.json",
                "sources/source-registry.json",
                "ttps/{id}-{slug}.json (per ttps[] entry)"
            ],
            "auto_load_filter": "metadata.pov_engine.auto_load == true AND status == 'active'",
            "destructive_consent": "Entries with destructive == true MUST require explicit operator confirmation before execution.",
            "scoring": "Per-use-case sum of test_cases[].expected_score_weight should be <= 1.0 (validator allows partial; --strict requires == 1.0).",
            "cleanup": "Entries with safety_class != 'safe-by-design' MUST run execution.cleanup before reporting completion."
        }
    }

    out = ROOT / "manifest.json"
    with open(out, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"Wrote {out.relative_to(ROOT)}")
    print(f"  {len(ttps)} active TTPs")
    print(f"  {len(all_techniques)} MITRE techniques covered")
    print(f"  {len(all_modules)} PANW modules referenced")


if __name__ == "__main__":
    main()

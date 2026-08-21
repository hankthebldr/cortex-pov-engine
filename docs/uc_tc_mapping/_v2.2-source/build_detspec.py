#!/usr/bin/env python3
"""POV-Engine detection list spec — classify every TC by validation class and
emit the detection content inventory that has to be authored + mapped."""
import csv, re, json, hashlib
from collections import Counter, OrderedDict

rows = list(csv.DictReader(open("tc_index.csv")))
remap = {r["uc_id"]: r for r in csv.DictReader(open("uc_product_addon_remap.csv"))}

# ---------------------------------------------------------------- 1. classify
ATTACK = re.compile(r"\b(execute|simulate|trigger|inject|attack|exfil|beacon|"
                    r"credential|replay|escalat|malware|ransom|phish|tunnel|"
                    r"lateral|c2|reverse shell|miner|atomic|stratus|caldera)\b", re.I)
AUTOM  = re.compile(r"\b(playbook|automation|agentic|agentix|soar|remediat|"
                    r"auto-?respond|workflow)\b", re.I)

HUNT = re.compile(r"\b(hunt|xql quer|saved quer|query librar|retro(active)?[- ]?"
                  r"(search|hunt)|hypothesis)\b", re.I)
PLAT = re.compile(r"\b(ingest|normaliz|schema|onboard|rbac|retention|dashboard|"
                  r"report|cost|licen|parser|broker|tenant|migrat|health|coverage|"
                  r"tiering|connector|marketplace|traceab|slas?)\b", re.I)

def classify(r):
    ds, si = r["detection_source"], r["simulation_input"]
    blob = f"{r['test_case_title']} {r['scenario']}"
    if PLAT.search(blob):
        return "PLT"
    if HUNT.search(blob):
        return "HNT"
    if re.search(r"Runtime Alerts|Causality|Incidents|Threat Intel", ds):
        return "DET"
    if re.search(r"Posture Dashboard|Inventory|Policies \+ Alerts|asoc/findings", ds):
        if ATTACK.search(si) and re.search(r"detect|alert|respond", blob, re.I):
            return "DET"
        return "POS"
    if AUTOM.search(blob) and not ATTACK.search(si):
        return "AUT"
    if ATTACK.search(si) and re.search(r"detect|alert|correlat|identif|respond|"
                                       r"stitch|triage|score|block|prevent",
                                       blob, re.I):
        return "DET"
    if re.search(r"compliance|inventor|posture|misconfig|benchmark", blob, re.I):
        return "POS"
    return "DET" if ATTACK.search(si) else "PLT"

# ------------------------------------------------ 2. POV-Engine scenario library
DOMAIN_PREFIX = {"SecOps": "SO", "Cloud": "CL"}
scen_lib, scen_id = OrderedDict(), {}
for r in rows:
    si = (r["simulation_input"] or "TBD — DC authoring").strip()
    scen_lib.setdefault(si, []).append(r["tc_id"])
for i, (si, tcs) in enumerate(sorted(scen_lib.items(), key=lambda kv: -len(kv[1])), 1):
    scen_id[si] = f"POV-SC-{i:03d}"

MITRE = re.compile(r"T\d{4}(?:\.\d{3})?")

# --------------------------------------------------------- 3. detection typing
def det_type(r):
    ds = r["detection_source"]
    ttl = f"{r['test_case_title']} {r['scenario']}"
    if "Threat Intel" in ds:                      return "IOC / Indicator Rule"
    if "Cortex Cloud Runtime Alerts" in ds:       return "Cloud Runtime Policy (CDR)"
    if "Posture Dashboard" in ds or "Policies" in ds: return "Cloud Posture Policy (custom)"
    if "asoc/findings" in ds:                     return "ASPM Finding Rule"
    if re.search(r"multi-stage|chain|stitch|causal|correlat|campaign", ttl, re.I):
        return "Correlation Rule (XQL)"
    if re.search(r"behavio|anomal|baseline|ml|analytic|impossible travel|insider",
                 ttl, re.I):                      return "Analytics BIOC"
    return "BIOC"

def dataset(r):
    ds = r["detection_source"]
    if "xdr_data" in ds:                    return "xdr_data"
    if "dataset=incidents" in ds:           return "incidents"
    if "threat_intel" in ds:                return "threat_intel"
    if "Cortex Cloud Runtime" in ds:        return "cloud_audit_logs · container_events"
    if "Cortex Cloud" in ds:                return "cloud_inventory · posture_findings"
    if "Xpanse" in ds:                      return "asm_assets · asm_issues"
    if "NGFW" in ds:                        return "panw_ngfw_traffic_raw · panw_ngfw_threat_raw"
    return "xdr_data"

PRIO = {"MOAT": "P1", "LEAD": "P2", "EMERGING": "P2", "PARITY": "P3", "": "P3"}

# ----------------------------------------------------------------- 4. generate
per_uc = Counter()
out = []
for r in rows:
    cls = classify(r)
    uc = r["uc_id"]
    si = (r["simulation_input"] or "TBD — DC authoring").strip()
    rm = remap.get(uc, {})
    rec = {
        "validation_class": cls,
        "tc_id": r["tc_id"], "ucs_id": r["ucs_id"], "uc_id": uc,
        "use_case": r["use_case"], "scenario": r["scenario"],
        "test_case_title": r["test_case_title"],
        "tc_sheet": r["tc_sheet"],
        "differentiation_tier": r["differentiation_tier"],
        "moat_classification": r["moat_classification"],
        "priority": PRIO.get(r["differentiation_tier"], "P3"),
        "detection_id": "", "detection_type": "", "target_dataset": "",
        "pov_scenario_id": scen_id[si],
        "pov_scenario_payload": si,
        "mitre_techniques": " ".join(sorted(set(MITRE.findall(si)))) or "TBD",
        "expected_signal": r["expected_signal"],
        "primary_kpi": r["primary_kpi"], "threshold": r["threshold"],
        "detection_source": r["detection_source"],
        "base_platform": rm.get("proposed_base_platform",""),
        "required_addon": rm.get("proposed_addon",""),
        "content_status": "",
        "authoring_gap": "",
    }
    if cls in ("DET", "HNT"):
        per_uc[uc] += 1
        pre = "DET" if cls == "DET" else "HNT"
        rec["detection_id"]   = f"{pre}-{uc.replace('UC-','')}-{per_uc[uc]:02d}"
        rec["detection_type"] = det_type(r) if cls == "DET" else "Saved XQL Hunt Query"
        rec["target_dataset"] = dataset(r)
        rec["content_status"] = "TO AUTHOR"
    else:
        rec["detection_type"] = {"POS": "Posture policy assertion (no detection content)",
                                 "PLT": "Platform-state assertion (no detection content)",
                                 "AUT": "Automation-outcome assertion (no detection content)"}[cls]
        rec["content_status"] = "N/A — assertion, not detection"

    gaps = []
    if si.startswith("TBD") or len(scen_lib[si]) >= 8:
        gaps.append(f"GENERIC SCENARIO (shared by {len(scen_lib[si])} TCs) — needs purpose-built payload")
    if rec["mitre_techniques"] == "TBD" and cls in ("DET","HNT"):
        gaps.append("NO MITRE MAPPING")
    if not r["expected_signal"] or r["expected_signal"] == r["success_criteria"]:
        gaps.append("EXPECTED SIGNAL == SUCCESS CRITERIA (untested distinction)")
    if r["threshold"] in ("", "Qualitative pass"):
        gaps.append("NO MEASURABLE THRESHOLD")
    rec["authoring_gap"] = " · ".join(gaps) or "—"
    out.append(rec)

cols = list(out[0].keys())
with open("pov_engine_detection_spec.csv","w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(out)

# scenario library file
slib = []
for si, tcs in scen_lib.items():
    dets = [o for o in out if o["pov_scenario_payload"] == si and o["validation_class"] in ("DET","HNT")]
    slib.append({
        "pov_scenario_id": scen_id[si],
        "scenario_payload": si,
        "bound_tc_count": len(tcs),
        "bound_detection_count": len(dets),
        "bound_tc_ids": " ".join(tcs),
        "mitre_techniques": " ".join(sorted(set(MITRE.findall(si)))) or "TBD",
        "reuse_flag": "SPLIT REQUIRED" if len(tcs) >= 8 else ("review" if len(tcs) >= 4 else "ok"),
    })
slib.sort(key=lambda x: -x["bound_tc_count"])
with open("pov_engine_scenario_library.csv","w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(slib[0].keys())); w.writeheader(); w.writerows(slib)

print("TOTAL TCs:", len(out))
print("class:", dict(Counter(o["validation_class"] for o in out).most_common()))
d = [o for o in out if o["validation_class"] in ("DET","HNT")]
print("CONTENT ARTIFACTS TO CREATE:", len(d),
      "| detections:", sum(1 for o in d if o["validation_class"]=="DET"),
      "| hunt queries:", sum(1 for o in d if o["validation_class"]=="HNT"))
print("  by priority:", dict(Counter(o["priority"] for o in d).most_common()))
print("  by type:", dict(Counter(o["detection_type"] for o in d).most_common()))
print("  by base:", dict(Counter(o["base_platform"] for o in d).most_common(5)))
print("SCENARIOS:", len(slib), "| split-required:",
      sum(1 for s in slib if s["reuse_flag"]=="SPLIT REQUIRED"),
      "| review:", sum(1 for s in slib if s["reuse_flag"]=="review"))
print("DET rows with gaps:", sum(1 for o in d if o["authoring_gap"]!="—"))

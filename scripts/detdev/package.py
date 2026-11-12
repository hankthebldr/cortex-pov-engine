#!/usr/bin/env python3
"""Phase-2 packager: wrap a local-model raw-content fragment into the library schema.

Clones a VALID plane-matched exemplar card + scenario, then overwrites only the
content fields with the fragment's raw (risky) content. The schema structure comes
from the exemplar (guaranteed valid); the attack payload + detections come from the
local offsec model. Deterministic — no model involved.

usage: package.py FRAG EX_CARD EX_SCN CARD_PATH SCN_PATH NEXT_TTP SIM_ID PLANE
"""
import json, sys, re, copy
import yaml

FRAG, EX_CARD, EX_SCN, CARD_PATH, SCN_PATH, NEXT_TTP, SIM_ID, PLANE = sys.argv[1:9]

# minimal tactic lookup for common ATT&CK techniques (extend as needed)
TACTIC = {
    "T1105": ("TA0011", "Command and Control"),
    "T1059": ("TA0002", "Execution"),
    "T1571": ("TA0011", "Command and Control"),
    "T1610": ("TA0005", "Defense Evasion"),
    "T1611": ("TA0004", "Privilege Escalation"),
    "T1486": ("TA0040", "Impact"),
    "T1496": ("TA0040", "Impact"),
    "T1046": ("TA0007", "Discovery"),
    "T1071": ("TA0011", "Command and Control"),
    "T1203": ("TA0002", "Execution"),
    "T1569": ("TA0002", "Execution"),
}

def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")

frag = json.load(open(FRAG))

# ---- card: clone the valid exemplar, overwrite content -------------------
card = json.load(open(EX_CARD))
card["id"] = NEXT_TTP

ident = card.setdefault("identity", {})
ident["name"] = frag.get("name", ident.get("name", NEXT_TTP))
ident["summary"] = frag.get("description", "")[:200]
ident["description"] = frag.get("description", "")

# mitre_attack.techniques from the fragment's technique ids
techs = []
for tid in frag.get("mitre_technique_ids", []):
    base = tid.split(".")[0]
    ta_id, ta_name = TACTIC.get(base, ("TA0002", "Execution"))
    t = {"technique_id": base,
         "name": tid,
         "tactic_ids": [ta_id],
         "tactic_names": [ta_name]}
    if "." in tid:
        t["subtechnique_id"] = tid
    techs.append(t)
if techs:
    card.setdefault("mitre_attack", {})["techniques"] = techs
    card["mitre_attack"].setdefault("matrix", "enterprise")

ex = card.setdefault("execution", {})
ex["target_platform"] = frag.get("target_platform", "linux")
pay = ex.setdefault("payload", {})
pay["code"] = frag.get("execution_payload", pay.get("code", ""))
pay.setdefault("interpreter", "bash")
pay.setdefault("input_variables", [])
pay.setdefault("timeout_seconds", 120)
if frag.get("expected_artifacts"):
    ex["expected_artifacts"] = frag["expected_artifacts"]

det = card.setdefault("detections", {})
det["biocs"] = frag.get("biocs", [])
det["iocs"] = frag.get("iocs", [])
# coerce xql entries to the required schema (purpose/expected_rows_min/dataset)
xqs = []
for q in frag.get("xql_queries", []):
    xqs.append({
        "purpose": q.get("purpose", "validation"),
        "name": q.get("name", "hunt query"),
        "query": q.get("query", ""),
        "expected_rows_min": q.get("expected_rows_min", 1),
        "dataset": q.get("dataset", "xdr_data"),
    })
det["xql_queries"] = xqs
det["correlation_rules"] = []
if frag.get("abiocs"):
    det["abiocs"] = frag["abiocs"]
elif "abiocs" in det:
    det["abiocs"] = []
det["analytics_modules"] = frag.get("analytics_modules", [])

json.dump(card, open(CARD_PATH, "w"), indent=2)
open(CARD_PATH, "a").write("\n")

# ---- scenario: clone the valid exemplar, overwrite content ---------------
scn = yaml.safe_load(open(EX_SCN))
scn["scenario_id"] = SIM_ID
scn["name"] = frag.get("name", scn.get("name"))
# keep uc_ref/tc_ref/tc_refs/pov_scenario_id (already valid)
# detection_types from what the fragment actually produced
dtypes = []
if frag.get("biocs"): dtypes.append("BIOC")
if frag.get("xql_queries"): dtypes.append("XQL")
if frag.get("iocs"): dtypes.append("IOC")
if frag.get("abiocs"): dtypes.append("ABIOC")
if dtypes: scn["detection_types"] = dtypes

# map a detection NAME -> (type, slug) so steps can wire a resolving detection_id
name_to = {}
for b in frag.get("biocs", []): name_to[b.get("name", "")] = ("BIOC", "bioc-" + slug(b.get("name", "")))
for q in frag.get("xql_queries", []): name_to[q.get("name", "")] = ("XQL", "xql-" + slug(q.get("name", "")))
for a in frag.get("abiocs", []): name_to[a.get("name", "")] = ("ABIOC", "abioc-" + slug(a.get("name", "")))

steps = []
prev = None
for i, s in enumerate(frag.get("steps", []), 1):
    sid = f"step-{i:02d}"
    step = {"id": sid,
            "name": s.get("name", sid),
            "command": s.get("command", "true"),
            "identity": s.get("identity", "www-data"),
            "mitre_technique": s.get("mitre_technique", (frag.get("mitre_technique_ids") or ["T1059"])[0])}
    eds = []
    for dn in s.get("detects", []):
        if dn in name_to:
            typ, did = name_to[dn]
            eds.append({"plane": scn.get("plane", PLANE.upper()),
                        "type": typ,
                        "description": dn,
                        "ttp_ref": NEXT_TTP,
                        "detection_id": did})
    if eds:
        step["expected_detections"] = eds
    if prev:
        step["causality"] = {"parent_step": prev, "pivot": "process_lineage"}
    steps.append(step); prev = sid
if steps:
    scn["steps"] = steps

yaml.safe_dump(scn, open(SCN_PATH, "w"), sort_keys=False, width=100, allow_unicode=True)

print(f"packaged card    -> {CARD_PATH}  ({len(det.get('biocs',[]))} bioc, {len(det.get('iocs',[]))} ioc)")
print(f"packaged scenario-> {SCN_PATH}  ({len(steps)} steps)")

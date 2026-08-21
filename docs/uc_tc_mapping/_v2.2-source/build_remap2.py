#!/usr/bin/env python3
"""v2.3 of the Product/Add-On mapping — rebuilt against the ACTUAL price book
(_RoB/fy27-wwdc/Cortex SKU Library.md) and Henry's base+add-on correction."""
import csv, json

# ---- SKU catalog: capability -> {sku, attaches_to, meter, note}
SKU = {
 # ---------- BASE ----------
 "Cortex XSIAM — NG-SIEM":        ("PAN-XSIAM-BASE-SIEM","base","per employee","No endpoints included."),
 "Cortex XSIAM — Enterprise":     ("PAN-XSIAM-BASE-ENT","base","per employee tier","Includes 1 EP per employee."),
 "Cortex XSIAM — Premium":        ("PAN-XSIAM-BASE-ENT-PREMIUM","base","per employee tier","Includes 1 EP, XTI, ASM, 30d hot retention."),
 "Cortex XDR":                    ("(XDR base — support/uplift SKUs only in library)","base","per endpoint","Base EP SKUs are quoted via XSIAM ADV-EP lines."),
 "Cortex Cloud":                  ("(Cortex Cloud base — PAN-CLOUD-* uplift only in library)","base","per workload","Cloud add-ons below attach here."),
 # ---------- ADD-ON: security ----------
 "Endpoint Protection":           ("PAN-XSIAM-ADV-EP","XSIAM · XDR","per endpoint","Includes 30d retention + standard success."),
 "Cloud Host Protection":         ("PAN-XSIAM-ADV-EP-CLOUD","XSIAM · Cortex Cloud","per host","Includes 30d retention."),
 "Cloud Runtime":                 ("PAN-XSIAM-ADV-EP-CLOUD-CRS","Cortex Cloud · XSIAM","per workload/yr","Cloud runtime security."),
 "Cloud Posture":                 ("PAN-XSIAM-CLOUD-POSTURE","Cortex Cloud · XSIAM","per workload/yr","Price-book text still says 'Prisma Cloud Posture Security' — stale name."),
 "Cloud AppSec":                  ("PAN-XSIAM-APPSEC","Cortex Cloud · XSIAM","per developer/yr","Its own SKU. Covers IaC, SCA, Secrets Security."),
 "ASM":                           ("PAN-XSIAM-ASM","XSIAM · XDR","—","Also inside PAN-XSIAM-ADV-SOC and XSIAM Premium."),
 "EM":                            ("PAN-XSIAM-EM","XSIAM","—","Exposure Management. Separate from ASM."),
 "XTI":                           ("PAN-XSIAM-ADV-SOC","XSIAM","per user/yr","Advanced SOC Data bundle = XTI + ASM. No standalone XTI SKU in the library."),
 "TIM":                           ("PAN-XSIAM-TIM","XSIAM","—","Threat Intelligence Management. Distinct from XTI."),
 "ITDR":                          ("PAN-XSIAM-ITDR","XSIAM · XDR","—","Identity Threat Detection and Response module."),
 "Host Insights":                 ("PAN-XSIAM-HOST-INST","XSIAM · XDR","—",""),
 "Forensics":                     ("PAN-XSIAM-FRNS","XSIAM · XDR","per endpoint/yr","Monthly variant PAN-XSIAM-FRNS-MNT."),
 "XTH":                           ("PAN-XSIAM-XTH","XSIAM · XDR","per endpoint","Extended Threat Hunting. Includes 30d retention."),
 "Email Security":                ("PAN-XSIAM-EMAIL","XSIAM","—",""),
 "Endpoint DLP":                  ("PAN-XSIAM-EP-DLP","XSIAM · XDR","—",""),
 "FIM":                           ("PAN-XSIAM-FIM","XSIAM","—","File Integrity Monitoring. NOT previously in the index."),
 # ---------- ADD-ON: capacity ----------
 "Data Ingestion — GB/day":       ("PAN-XSIAM-BASE-GB","XSIAM","per GB/day","Base ingestion line."),
 "Data Lake Ingestion — GB/day":  ("PAN-XSIAM-BASE-GB-LAKE","XSIAM","per GB/day",""),
 "Compute Units":                 ("PAN-XSIAM-COMP-UNT","XSIAM · AgentiX","per unit",""),
 "Retention":                     ("PAN-XSIAM-DATASET-RTN","XSIAM","per GB","Also EP/GB hot+cold and incident-retention SKUs."),
 "GB Forwarding":                 ("PAN-XSIAM-GB-FRWRD","XSIAM","per GB/day",""),
 "Endpoint Forwarding":           ("PAN-XSIAM-EP-FRWRD","XSIAM","per endpoint/yr",""),
 # ---------- UNCONFIRMED ----------
 "Agentic Endpoint Security (AES) ⚠": ("— NO SKU IN PRICE BOOK","?","?","Absent from the SKU library AND from all public license tables."),
 "Cortex Data Security ⚠":        ("— NO SKU IN PRICE BOOK","?","?","Public docs describe it; no part number in the FY27 library."),
 # ---------- SERVICES (adjacent) ----------
 "Unit 42 MDR":                   ("PAN-XSIAM-U42-MDR-EP / -GB","service","—",""),
 "Unit 42 Managed XSIAM":         ("PAN-XSIAM-U42-MXSIAM / -PRO","service","—","Pro adds MDR + MTH + Extended Response."),
 "Unit 42 MTH":                   ("PAN-XSIAM-MTH / PAN-XDR-MTH","service","—","Managed Threat Hunting service."),
 "Cortex AgentiX":                ("PAN-AGENTIX-BASE / -ENTERPRISE","base product","per user/yr","$150k base (2 users) / $300k ent (4 users + XTI)."),
 "Cortex XSOAR":                  ("PAN-CORTEXXSOAR-ENTERPRISE","base product","per tenant","$250k (XSOAR + TIM, 4 users)."),
 "Cortex Xpanse":                 ("PAN-EXP-EXPNDR + PAN-EXP-EXPNDR-AUM","base product","platform + per asset","$60k platform + $7/asset. Modules: BHV, LINK, ILI."),
 "Prisma AIRS":                   ("(Prisma line — not in the Cortex SKU library)","adjacent","—",""),
 "PAN-OS NGFW":                   ("(Network line)","adjacent","—","Telemetry source only."),
 "Chronosphere":                  ("(3rd party)","adjacent","—",""),
}

# translation from the docs-derived names used in v1 of this pass
T = {
 "Enterprise Runtime Security (XDR)": "Endpoint Protection",
 "Data Ingestion — Analytics tier": "Data Ingestion — GB/day",
 "Data Ingestion — Cortex Data Lake tier": "Data Lake Ingestion — GB/day",
 "Cloud Posture Security": "Cloud Posture",
 "Cloud Runtime Security": "Cloud Runtime",
 "Application Security": "Cloud AppSec",
 "Extended Threat Intelligence (XTI)": "XTI",
 "Threat Intelligence Management": "TIM",
 "Attack Surface Management": "ASM",
 "Exposure Management": "EM",
 "Identity Threat Detection & Response": "ITDR",
 "Extended Threat Hunting": "XTH",
 "Advanced Email Security": "Email Security",
 "DLP": "Endpoint DLP",
 "Cortex Data Security": "Cortex Data Security ⚠",
 "Extended Compute Units": "Compute Units",
 "Data Retention": "Retention",
 "GB Event Forwarding": "GB Forwarding",
 "Forensics": "Forensics",
 "Host Insights": "Host Insights",
 "Agentic Endpoint Security (AES) ⚠": "Agentic Endpoint Security (AES) ⚠",
}

# price book tier inclusion — SUPERSEDES the docs-derived set
INCL_ENT  = {"Endpoint Protection"}
INCL_PREM = {"Endpoint Protection", "XTI", "ASM", "Retention"}

def path(base, addons):
    a = [x for x in addons if x not in ("Compute Units","Retention","GB Forwarding",
                                        "Endpoint Forwarding","Data Ingestion — GB/day",
                                        "Data Lake Ingestion — GB/day")]
    s = set(a)
    if "Cortex XSIAM" not in base:
        if "Cortex Cloud" in base:
            core = [x for x in a if x in ("Cloud Runtime","Cloud Posture","Cloud AppSec","Cloud Host Protection")]
            rest = [x for x in a if x not in core]
            return ("Cortex Cloud + " + " + ".join(core or ["Cloud Posture"]) +
                    (" + " + " + ".join(rest) if rest else ""),
                    "—")
        return (" + ".join(base), "—")
    ala = "XSIAM NG-SIEM" + (" + " + " + ".join(sorted(s)) if s else "")
    if not s:                       bundle = "any tier"
    elif not (s - INCL_ENT):        bundle = "XSIAM Enterprise covers all"
    elif not (s - INCL_PREM):       bundle = "XSIAM Premium covers all"
    else: bundle = "XSIAM Premium + " + " + ".join(sorted(s - INCL_PREM))
    return (ala, bundle)

rows = list(csv.DictReader(open("uc_product_addon_remap.csv")))
out = []
for r in rows:
    base = r["proposed_base_platform"].split(" · ")
    old = [] if r["proposed_addon"] == "—" else r["proposed_addon"].split(" · ")
    new = [T.get(x, x) for x in old]
    # Cortex-Cloud-base UCs: keep Henry's Cloud-family naming
    p, b = path(base, new)
    skus = " · ".join(SKU.get(x, ("?",))[0] for x in new) if new else "—"
    out.append({
        "uc_id": r["uc_id"], "use_case": r["use_case"],
        "fy27_subdomain": r["fy27_subdomain"],
        "current_products": r["current_products"], "current_addon": r["current_addon"],
        "base_platform": r["proposed_base_platform"],
        "addon": " · ".join(new) if new else "—",
        "addon_skus": skus,
        "adjacent": r["proposed_adjacent"],
        "min_license_path": p, "bundle_alternative": b,
        "rationale": r["rationale"],
    })

with open("uc_product_addon_remap_v2.csv","w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)

with open("sku_catalog.csv","w",newline="") as f:
    w = csv.writer(f); w.writerow(["capability","sku_part_number","attaches_to","meter","note"])
    for k,(s,a,m,n) in SKU.items(): w.writerow([k,s,a,m,n])

from collections import Counter
print("rows:", len(out))
print(Counter(o["bundle_alternative"] for o in out).most_common())
print()
for o in out:
    if o["base_platform"].startswith("Cortex Cloud") or "Cloud" in o["addon"]:
        print(f'{o["uc_id"]:<10}{o["base_platform"][:34]:<34}{o["addon"][:44]:<44}{o["min_license_path"][:56]}')

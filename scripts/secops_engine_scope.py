#!/usr/bin/env python3
"""Classify every open SecOps test case by WHY no engine artifact closes it.

Coverage itself is counted by ``scripts/uctc_crosswalk_v2.2.py --report``. This
script answers the different question a DC gets asked in the room: "you say N
are open — how many of those are you actually going to build?"

Collapsing "owed to this corpus" and "owed to a system we do not control" into a
single open count is how a backlog becomes a promise nobody can keep. The two
buckets here are what keep them apart.

The verdicts are hand-authored, exactly as the crosswalk's are. String matching
cannot tell "the engine has no connector for this" from "nobody wrote it yet",
and that distinction is the entire point of the file.

Usage::

    python3 scripts/secops_engine_scope.py           # regenerate the doc + csv
    python3 scripts/secops_engine_scope.py --check   # non-zero if a verdict is stale
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import Counter

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source", "tc_index_v2.2.csv")
# validation_class lives in the detection spec, not the TC index — the index
# predates the POS/PLT/AUT split and still describes every row as a test case.
SPEC = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source", "detection_spec_v2.2.csv")
OUT_MD = os.path.join(REPO, "docs", "uc_tc_mapping", "secops-engine-scope-v2.2.md")
OUT_CSV = os.path.join(REPO, "docs", "uc_tc_mapping", "secops-engine-scope-v2.2.csv")

# ── Reason vocabulary ───────────────────────────────────────────────────────
# Closed set. The first three are BUILDABLE (work is owed to the corpus); the
# rest are NOT (work is owed to something outside the engine, or to nobody).
REASONS = {
    "BUILDABLE_SCENARIO": (
        "buildable", "Scenario YAML + TTP card. The engine can emit the signal "
        "the test case is about."),
    "BUILDABLE_ASSERTION": (
        "buildable", "Assertion YAML. The success criterion has a core that a "
        "read-only XQL probe can measure."),
    "BUILDABLE_BOTH": (
        "buildable", "Scenario supplies the stimulus, assertion measures the "
        "platform's observable response. Two artifacts, one claim."),
    "EXTERNAL_SERVICE_DELIVERY": (
        "external", "Measures a Palo Alto Networks human service (Unit 42 MDR / "
        "MTH / MSIAM), not a platform behaviour. No artifact can prove a "
        "staffed service met an SLA."),
    "EXTERNAL_THIRD_PARTY_SYSTEM": (
        "external", "Requires a system outside Cortex that the engine has no "
        "connector for — ServiceNow/ITSM, an ISAC peer, a customer CI/CD "
        "pipeline, or a competitor EDR."),
    "TENANT_CONFIG_NO_WRITE_PATH": (
        "external", "Tests a configuration a DC performs in the tenant — policy "
        "authoring, rule tuning, model deployment. CORTEXSIM_XSIAM_ALLOW_WRITE "
        "is default-off by design and must stay so."),
    "CONSOLE_ONLY_NO_READ_API": (
        "external", "A console workflow or generated prose with no "
        "API-observable artifact — dashboards, case starring, Copilot answer "
        "quality. Nothing to query, so nothing to assert."),
    "LICENSED_MODULE_REQUIRED": (
        "external", "Needs a licensed module active in the tenant that the "
        "engine can neither provision nor detect (Host Insights, Chronosphere, "
        "federated search, agentless scanning)."),
    "TIME_HORIZON_EXCEEDS_POV": (
        "external", "Requires data aged past what a POV can produce — 12-month "
        "retention, multi-year compliance windows. Not authorable; only a "
        "tenant with real history can answer it."),
    "INDEX_ROW_SELF_CONTRADICTORY": (
        "external", "The row's title and its success_criteria describe DIFFERENT "
        "capabilities, so no artifact can satisfy both: one authored from the "
        "title is scored against the wrong claim, and one authored from the "
        "criteria closes a test case whose title promises something it never "
        "proved. Blocked on the index owner — see "
        "`scripts/check_index_criteria_reuse.py`."),
}

VERDICT = {
    # ── UC-AEPS ──
    "TC-AEPS-03":  ("BUILDABLE_BOTH", "Cross-OS forensic collection: platform_variants already expresses the OS spread; an assertion reads collected-artifact presence per OS."),
    # ── UC-AES ──
    "TC-AES-04":   ("BUILDABLE_BOTH", "email_emitter produces the BEC shape; assertion reads quarantine action on the incident. The 'notifies finance' leg is a notification channel and stays disclaimed."),
    "TC-AES-09":   ("BUILDABLE_BOTH", "The user-report event is emittable as an M365 audit record; assertion reads the analysis verdict written back."),
    # ── UC-AGTX ──
    "TC-AGTX-01":  ("BUILDABLE_BOTH", "The multi-stage chain is SIM-MP-021's existing shape. '1,100+ integrations' is unprovable and must be disclaimed, not counted."),
    "TC-AGTX-02":  ("TENANT_CONFIG_NO_WRITE_PATH", "Playbook versioning and rollback require authoring and then reverting a playbook — both writes."),
    "TC-AGTX-04":  ("BUILDABLE_BOTH", "Seed IOCs via the TIM fixture; assertion reads enrichment fields attached to the resulting incident."),
    "TC-AGTX-05":  ("BUILDABLE_BOTH", "A multi-plane scenario supplies the arc; assertion counts distinct response-action domains on one incident."),
    "TC-AGTX-06":  ("EXTERNAL_THIRD_PARTY_SYSTEM", "The row demands forensics across 'XDR, CrowdStrike and Defender'. SIM-MP-021 already refuses this exact binding — the engine cannot emit competitor EDR telemetry."),
    "TC-AGTX-09":  ("CONSOLE_ONLY_NO_READ_API", "Natural-language investigation is a Copilot UX. The answer's usefulness is not machine-knowable."),
    "TC-AGTX-10":  ("CONSOLE_ONLY_NO_READ_API", "'Copilot assists junior analysts to senior-level' is a human-outcome claim with no queryable artifact."),
    "TC-AGTX-12":  ("TENANT_CONFIG_NO_WRITE_PATH", "Building a custom agent is authoring, which is a write."),
    # ── UC-AIRS ──
    "TC-AIRS-02":  ("BUILDABLE_ASSERTION", "AI SBOM completeness reads from ai_spm_findings; the POS-AISP pack is the precedent."),
    "TC-AIRS-03":  ("BUILDABLE_BOTH", "llm_provider_egress + agentic_egress produce sanctioned and shadow AI traffic; assertion distinct-counts discovered services."),
    "TC-AIRS-04":  ("TENANT_CONFIG_NO_WRITE_PATH", "Governance policy enforcement requires authoring the policy first."),
    "TC-AIRS-05":  ("LICENSED_MODULE_REQUIRED", "'Continuous autonomous red teaming, 500+ attack types' is the product performing its own testing. cortex-prompt-attacker proves the target is attackable, not that AIRS ran 500 types."),
    "TC-AIRS-06":  ("EXTERNAL_THIRD_PARTY_SYSTEM", "Needs the customer's CI/CD pipeline as the integration surface."),
    "TC-AIRS-09":  ("BUILDABLE_ASSERTION", "Training-pipeline exposure is a posture read over ai_spm_findings; POS-AISP-005 is the precedent."),
    "TC-AIRS-10":  ("BUILDABLE_BOTH", "cortex-vulnerable-llm carries a RAG surface; the airs_prompt_attack plugin drives it and an assertion reads the leak verdict."),
    # ── UC-APB ──
    "TC-APB-01":   ("BUILDABLE_BOTH", "Out-of-branch input handling; AUT-APB-002 (low-confidence graceful degradation) is the direct precedent."),
    "TC-APB-03":   ("CONSOLE_ONLY_NO_READ_API", "Judging whether a generated playbook is 'working' from a prose prompt is not machine-knowable, and building it is a write."),
    "TC-APB-04":   ("EXTERNAL_THIRD_PARTY_SYSTEM", "Requires deliberately breaking a live integration's response schema — a third-party system the engine does not own."),
    "TC-APB-07":   ("CONSOLE_ONLY_NO_READ_API", "'Analyst-grade case summary' is a quality judgement on generated prose."),
    "TC-APB-08":   ("BUILDABLE_ASSERTION", "Autonomous close under an approval gate is an xsiam_incidents latency + gate-presence read."),
    "TC-APB-09":   ("EXTERNAL_THIRD_PARTY_SYSTEM", "Needs a customer-representative internal tool behind an MCP endpoint."),
    "TC-APB-10":   ("EXTERNAL_THIRD_PARTY_SYSTEM", "SOC-to-ITSM orchestration requires the ITSM."),
    # ── UC-ASM ──
    "TC-ASM-02":   ("BUILDABLE_BOTH", "The asm IaC module plants the exposure; assertion reads whether a remediation workflow fired against it."),
    "TC-ASM-04":   ("BUILDABLE_BOTH", "asm + tim modules together: discovered surface plus scheduled intel search over it."),
    # ── UC-BYOML ──
    "TC-BYOML-01": ("TENANT_CONFIG_NO_WRITE_PATH", "Deploying a custom ML model into the tenant is a write action."),
    # ── UC-CDR ──
    "TC-CDR-04":   ("INDEX_ROW_SELF_CONTRADICTORY", "Title says CIEM over-permissioned-identity discovery; success_criteria are verbatim TC-ITDR-03/06's multi-IdP correlation claim. The cspm module does plant the IAM findings the TITLE wants, but an assertion measuring them would satisfy a row whose criteria ask about something else."),
    "TC-CDR-05":   ("LICENSED_MODULE_REQUIRED", "Agentless scanning is a licensed Cortex Cloud capability the engine cannot enable or detect."),
    # ── UC-DLP ──
    "TC-DLP-03":   ("TENANT_CONFIG_NO_WRITE_PATH", "DLP policy creation is a write; only enforcement is observable, and enforcement without the authored policy proves nothing."),
    "TC-DLP-06":   ("BUILDABLE_BOTH", "DLP signal plus identity activity under one principal; assertion reads whether both appear on one insider-risk timeline."),
    "TC-DLP-08":   ("CONSOLE_ONLY_NO_READ_API", "Compliance report generation and data-flow maps are console renderings."),
    "TC-DLP-09":   ("BUILDABLE_ASSERTION", "Case auto-creation from a DLP incident, with required context fields present, is a direct xsiam_incidents read."),
    "TC-DLP-10":   ("BUILDABLE_BOTH", "DLP stimulus plus an assertion reading the remediation action and its approval state."),
    # ── UC-EDR ──
    "TC-EDR-01":   ("TENANT_CONFIG_NO_WRITE_PATH", "Endpoint policy creation is a write."),
    "TC-EDR-02":   ("TENANT_CONFIG_NO_WRITE_PATH", "Policy assignment to assets is a write."),
    "TC-EDR-06":   ("TENANT_CONFIG_NO_WRITE_PATH", "Scheduling agent upgrades is a write, and the engine must never move a customer's agent version."),
    # ── UC-ERV ──
    "TC-ERV-03":   ("EXTERNAL_THIRD_PARTY_SYSTEM", "'Automation integrations across the onboarded stack' is defined by the customer's own third-party tools."),
    "TC-ERV-06":   ("BUILDABLE_ASSERTION", "Rolling risk attribution is a scalar read on the asset's score before and after seeded identity risk."),
    "TC-ERV-07":   ("CONSOLE_ONLY_NO_READ_API", "Compliance-framework report generation is a console artifact."),
    "TC-ERV-08":   ("TENANT_CONFIG_NO_WRITE_PATH", "Custom policy configurability requires authoring policies."),
    # ── UC-IR ──
    "TC-IR-06":    ("INDEX_ROW_SELF_CONTRADICTORY", "Title says auto-containment within an SLA; success_criteria measure malware DETECTION pre-execution and false-positive rate, and primary_kpi is 'False Positive Rate' — the KPI agrees with the pasted criteria, not with the title. An xql_latency containment assertion would be scored against a detection claim."),
    "TC-IR-09":    ("BUILDABLE_ASSERTION", "Priority adjustment by asset criticality is a scalar read on incident severity across two hosts of differing criticality."),
    "TC-IR-10":    ("CONSOLE_ONLY_NO_READ_API", "Case starring is a console interaction."),
    "TC-IR-11":    ("BUILDABLE_ASSERTION", "Auto-enrichment presence is a ratio over incidents carrying the enrichment fields."),
    # ── UC-ITDR ──
    "TC-ITDR-04":  ("BUILDABLE_ASSERTION", "PLT-ITDR-006 already proves one identity resolving across AD, Entra and Okta. The Marketplace-connector door itself is unprovable and stays disclaimed."),
    # ── UC-ITPA ──
    "TC-ITPA-01":  ("EXTERNAL_THIRD_PARTY_SYSTEM", "Bidirectional ServiceNow sync. No ServiceNow surface exists anywhere in the tree, and closing it would need a write path the engine forbids."),
    # ── UC-MDR ──
    "TC-MDR-01":   ("EXTERNAL_SERVICE_DELIVERY", "Unit 42 acknowledgement and escalation SLA. The engine can fire detections into off-hours windows; whether a human acknowledged them is not a platform fact."),
    "TC-MDR-02":   ("EXTERNAL_SERVICE_DELIVERY", "The subject is the Unit 42 analyst's investigation and containment quality, not the platform's."),
    # ── UC-MSIAM ──
    "TC-MSIAM-01": ("EXTERNAL_SERVICE_DELIVERY", "Onboarding completeness, tuning cadence and SLA reporting of a managed service engagement."),
    # ── UC-MTH ──
    "TC-MTH-01":   ("BUILDABLE_SCENARIO", "The index's own simulation input is 'pre-seeded hunt dataset, 20-30 known IOCs across credential access, persistence, exfiltration' — that corpus is precisely what the engine builds. Hunt QUALITY stays with Unit 42 and is disclaimed."),
    # ── UC-SIEM ──
    "TC-SIEM-01":  ("TIME_HORIZON_EXCEEDS_POV", "Hot/warm/cold lifecycle transitions need data aged into each tier, and the 'search SLA' half needs query-latency measurement no probe in this substrate performs."),
    "TC-SIEM-02":  ("LICENSED_MODULE_REQUIRED", "Host Insights must be licensed and active; the engine can neither enable it nor tell an unlicensed tenant from an unenriched one."),
    # ── UC-SOAR ──
    "TC-SOAR-01":  ("BUILDABLE_BOTH", "Trigger the alert types, then read playbook completion and enrichment on the incident."),
    "TC-SOAR-03":  ("EXTERNAL_THIRD_PARTY_SYSTEM", "'Top 5 customer tools' is by definition the customer's third-party estate."),
    # ── UC-SOT ──
    "TC-SOT-01":   ("TENANT_CONFIG_NO_WRITE_PATH", "Rule tuning, threshold configuration and escalation paths are all tenant writes."),
    "TC-SOT-03":   ("TENANT_CONFIG_NO_WRITE_PATH", "The detection-tuning half of the lifecycle loop is a write."),
    # ── UC-TH ──
    "TC-TH-04":    ("TENANT_CONFIG_NO_WRITE_PATH", "Marketplace dataset onboarding is a tenant configuration action."),
    "TC-TH-07":    ("LICENSED_MODULE_REQUIRED", "The Forensics add-on must be licensed and active for collection to occur."),
    # ── UC-TIM ──
    "TC-TIM-01":   ("TENANT_CONFIG_NO_WRITE_PATH", "Despite the title's overlap with TC-XTI-01, this row's success criterion is DETECTION lifecycle and tuning feedback — authoring and retiring detections, which are writes. PLT-XTI-001 covers the indicator-lifecycle claim that IS measurable."),
    # ── UC-XDL ──
    "TC-XDL-02":   ("LICENSED_MODULE_REQUIRED", "Chronosphere telemetry pipeline is a separately licensed component."),
    "TC-XDL-03":   ("TENANT_CONFIG_NO_WRITE_PATH", "Tier routing rules are tenant configuration."),
    "TC-XDL-04":   ("TIME_HORIZON_EXCEEDS_POV", "12-month archived data with a <60s response bar. A POV cannot age data a year, and no probe measures query latency."),
    "TC-XDL-05":   ("BUILDABLE_BOTH", "Incident reconstruction across retained data is expressible over the POV's own window; the multi-year framing is what is not."),
    "TC-XDL-06":   ("LICENSED_MODULE_REQUIRED", "Federated search across external sources is a licensed capability with external data sources behind it."),
    "TC-XDL-07":   ("LICENSED_MODULE_REQUIRED", "Same federated-search capability, cost framing."),
    "TC-XDL-08":   ("LICENSED_MODULE_REQUIRED", "Chronosphere observability integration."),
    "TC-XDL-09":   ("CONSOLE_ONLY_NO_READ_API", "A unified dashboard is a console rendering."),
    # ── UC-XTI ──
    "TC-XTI-06":   ("TIME_HORIZON_EXCEEDS_POV", "Hunts 'across the full retention window' need a full retention window to exist."),
    "TC-XTI-08":   ("TENANT_CONFIG_NO_WRITE_PATH", "Detection tuning from investigation outcomes is a write."),
    "TC-XTI-09":   ("BUILDABLE_BOTH", "asm plants the exposed CVE-bearing asset, tim publishes exploitation intel, assertion reads the re-ranked score. Genuinely expressible."),
    "TC-XTI-10":   ("EXTERNAL_THIRD_PARTY_SYSTEM", "Publishing to an ISAC peer requires the peer."),
}


def _secops_rows() -> dict[str, dict]:
    """The SecOps sheet of the v2.2 index, keyed by tc_id, joined to the
    detection spec so every row carries its validation_class."""
    with open(SPEC) as f:
        klass = {r["tc_id"]: r["validation_class"] for r in csv.DictReader(f)}
    with open(IDX) as f:
        rows = {r["tc_id"]: r for r in csv.DictReader(f)
                if r.get("tc_sheet") == "SecOps"}
    for tc, row in rows.items():
        # A row with no class would render as an empty column and read as a
        # data bug rather than as the missing join it is.
        row["validation_class"] = klass.get(tc) or "UNCLASSED"
    return rows


def _bound_test_cases() -> set[str]:
    """Every TC any scenario or assertion binds, read off the tree.

    Deliberately a filesystem walk rather than a loader import: this script must
    run without sqlalchemy, on a jumpbox, against a stripped checkout.
    """
    bound: set[str] = set()
    patterns = (
        os.path.join(REPO, "scenarios", "**", "*.yml"),
        os.path.join(REPO, "assertions", "**", "*.yml"),
    )
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            if os.path.basename(path).startswith("_"):
                continue
            try:
                doc = yaml.safe_load(open(path))
            except yaml.YAMLError:
                continue
            if not isinstance(doc, dict):
                continue
            if "scenario_id" not in doc and "assertion_id" not in doc:
                continue
            if doc.get("tc_ref"):
                bound.add(doc["tc_ref"])
            bound.update(doc.get("tc_refs") or [])
    return bound


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the verdict table has drifted from "
                         "the corpus instead of rewriting the doc")
    args = ap.parse_args()

    rows_by_id = _secops_rows()
    open_ids = sorted(set(rows_by_id) - _bound_test_cases())

    # Drift in BOTH directions is an error worth failing on. A missing verdict
    # means a newly-opened row nobody triaged; a stale one means an artifact
    # landed and the doc still calls it out of scope — the second is worse,
    # because it under-reports coverage the corpus actually has.
    missing = [t for t in open_ids if t not in VERDICT]
    stale = [t for t in VERDICT if t not in open_ids]
    for tc in missing:
        print(f"MISSING VERDICT: {tc} is open and untriaged", file=sys.stderr)
    for tc in stale:
        print(f"STALE VERDICT: {tc} is now closed by an artifact", file=sys.stderr)
    if args.check:
        if missing or stale:
            print(f"\n{len(missing)} missing, {len(stale)} stale — "
                  f"run without --check to regenerate", file=sys.stderr)
            return 1
        print(f"secops-engine-scope: {len(open_ids)} open, all triaged")
        return 0
    if missing:
        return 1

    rows = []
    for tc in open_ids:
        code, why = VERDICT[tc]
        bucket, _ = REASONS[code]
        idx = rows_by_id[tc]
        rows.append({
            "tc_id": tc,
            "uc_id": idx["uc_id"],
            "use_case": idx["use_case"],
            "validation_class": idx["validation_class"],
            "tier": idx["differentiation_tier"],
            "bucket": bucket,
            "reason_code": code,
            "rationale": why,
            "test_case": (idx["test_case_title"] or "")[:140],
        })

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    buildable = [r for r in rows if r["bucket"] == "buildable"]
    external = [r for r in rows if r["bucket"] == "external"]
    by_code = Counter(r["reason_code"] for r in rows)
    total = len(rows_by_id)

    L: list[str] = []
    A = L.append
    A("# SecOps engine scope — which open test cases the engine can close, and why not")
    A("")
    A("> Generated by `scripts/secops_engine_scope.py`. Counted coverage stays")
    A("> `python3 scripts/uctc_crosswalk_v2.2.py --report`; this file answers the")
    A("> different question of **why** a row is still open.")
    A("")
    A(f"The SecOps sheet of the v2.2 index carries **{total} test cases**. "
      f"**{total - len(open_ids)}** are evidenced by a scenario or an assertion. "
      f"The **{len(open_ids)}** below are not, and they are not one population:")
    A("")
    A(f"- **{len(buildable)} are buildable** — the work is owed to this corpus.")
    A(f"- **{len(external)} are not** — the work is owed to something outside the "
      "engine, or to nobody.")
    A("")
    A("Collapsing those two into a single \"open\" number is how a backlog becomes a")
    A("promise nobody can keep. Quote the first as owed work; name the second as out")
    A("of scope.")
    A("")
    A("## Why a test case is not engine-closable")
    A("")
    A("| reason code | bucket | meaning | count |")
    A("|---|---|---|---:|")
    for code, (bucket, meaning) in REASONS.items():
        A(f"| `{code}` | {bucket} | {meaning} | {by_code.get(code, 0)} |")
    A("")
    A("## The buildable set")
    A("")
    A("| TC | class | UC | reason | note |")
    A("|---|---|---|---|---|")
    for r in sorted(buildable, key=lambda r: (r["reason_code"], r["tc_id"])):
        A(f"| `{r['tc_id']}` | {r['validation_class']} | {r['uc_id']} | "
          f"`{r['reason_code']}` | {r['rationale']} |")
    A("")
    A("## The out-of-scope set")
    A("")
    A("These need a system, a licence, a service engagement or a passage of time the")
    A("engine cannot supply. Authoring YAML against them would manufacture a green that")
    A("reads as detection coverage while proving a contract — the Gate A5 failure mode.")
    A("Each is listed so it stops reading as unbuilt backlog.")
    A("")
    A("| TC | class | UC | reason | note |")
    A("|---|---|---|---|---|")
    for r in sorted(external, key=lambda r: (r["reason_code"], r["tc_id"])):
        A(f"| `{r['tc_id']}` | {r['validation_class']} | {r['uc_id']} | "
          f"`{r['reason_code']}` | {r['rationale']} |")
    A("")
    A("## Standing caveat")
    A("")
    A("**`tenant-verified` is 0.** Every artifact counted as evidence here was authored")
    A("and validated against an injected transport. None has executed against a live")
    A("Cortex tenant. Authored is not proven, and the two must never be reported as one")
    A("number.")

    with open(OUT_MD, "w") as f:
        f.write("\n".join(L) + "\n")

    print(f"wrote {os.path.relpath(OUT_MD, REPO)}")
    print(f"wrote {os.path.relpath(OUT_CSV, REPO)}")
    print(f"  {total} SecOps TCs | {total - len(open_ids)} evidenced | "
          f"{len(open_ids)} open = {len(buildable)} buildable + {len(external)} external")
    for code, n in by_code.most_common():
        print(f"    {n:3d}  {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

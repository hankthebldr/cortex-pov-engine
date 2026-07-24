#!/usr/bin/env python3
"""
detection_scanner/scripts/validate.py — canonical validator for the TTP corpus.

Single source of truth for what "valid" means. CI, the cortex-pov-engine loader,
and human contributors all run this. Exits non-zero on any failure.

Checks performed:
  1. JSON well-formedness for every file under schema/, sources/, ttps/.
  2. The schema file itself is valid JSON Schema 2020-12.
  3. Every ttps/*.json validates against schema/ttp-entry.schema.json.
  4. ID uniqueness across ttps/ (no two entries share the same TTP-YYYY-NNNN).
  5. Filename matches id field (filename starts with id).
  6. Every metadata.source_refs[] entry resolves to a source-registry id.
  7. Every references[].publisher_id resolves to a source-registry id.
  8. publisher_id values in references[] are a subset of metadata.source_refs[].
  9. Exactly one references[] entry has primary: true.
 10. MITRE ATT&CK technique IDs follow Txxxx[.xxx] format.
 11. Use case ids match UC-<DOMAIN>-NNN pattern; test case ids match TC-<DOMAIN>-NNN[A-Z].
 12. Within a use case, sum of expected_score_weight is <= 1.0 (allows for unweighted tests).
 13. XQL/BIOC grammar sanity lint (GAP-12): balanced quotes/parens, a dataset/preset
     reference present on every BIOC/XQL body, and no leftover placeholder/skeleton tokens.

Usage:
  python3 scripts/validate.py                 # full check, repo root
  python3 scripts/validate.py --strict        # additionally require weights sum to exactly 1.0
  python3 scripts/validate.py --quiet         # suppress per-file PASS lines
"""

import argparse
import glob
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

ID_PATTERN          = re.compile(r"^TTP-\d{4}-\d{4}$")
TECHNIQUE_PATTERN   = re.compile(r"^T\d{4}(\.\d{3})?$")
UC_PATTERN          = re.compile(r"^UC-[A-Z0-9]+-\d{3}$")
TC_PATTERN          = re.compile(r"^TC-[A-Z0-9]+-\d{3}[A-Z]?$")
SOURCE_ID_PATTERN   = re.compile(r"^SRC-[A-Z0-9-]+$")


class Report:
    def __init__(self, quiet=False):
        self.errors = []
        self.warnings = []
        self.passes = 0
        self.quiet = quiet

    def err(self, where, msg):
        self.errors.append(f"FAIL  {where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"WARN  {where}: {msg}")

    def ok(self, msg):
        self.passes += 1
        if not self.quiet:
            print(f"PASS  {msg}")

    def summary(self):
        print()
        print(f"--- {self.passes} pass, {len(self.warnings)} warn, {len(self.errors)} fail ---")
        for w in self.warnings:
            print(w)
        for e in self.errors:
            print(e)


def load_json(path, report):
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        report.err(str(path), f"invalid JSON: {e}")
        return None


# Placeholder tokens that must never appear in a deployable detection body.
# These are the markers the draft generator (scripts/generate_card.py) emits;
# a promoted card must have replaced all of them with real logic.
PLACEHOLDER_TOKENS = (
    "AUTO-GENERATED SKELETON",
    "TODO",
    "FIXME",
    "XXX",
    "REPLACE_WITH",
    "REPLACE WITH",
    "<placeholder>",
    "PLACEHOLDER_PREDICATE",
    "predicate matching the bioc name",
)

# Tokens that anchor a body to a real Cortex data source. A BIOC/XQL body that
# references none of these is almost certainly not a runnable detection.
DATASET_ANCHORS = ("dataset =", "dataset=", "preset =", "preset=")

# ── XQL dialect knowledge (GAP-12 grammar lint) ──────────────────────────────
# Known XQL stage verbs (the token that opens a `| <verb> ...` stage). Derived
# from the corpus + Cortex XQL Stage reference. Anything outside this set in a
# detection body is flagged as a WARNING (the dialect is broad and evolving, so
# an unknown verb is suspicious but not a hard gate failure).
XQL_STAGE_VERBS = frozenset({
    "filter", "fields", "comp", "sort", "alter", "bin", "join", "union",
    "dedup", "limit", "where", "enrich", "view", "sample", "iploc",
    "arrayexpand", "windowcomp", "call", "target", "timeframe",
})

# Real Cortex/XSIAM dataset + preset identifiers used across the corpus. A body
# whose `dataset =`/`preset =` source is NOT in this registry is flagged WARN
# (likely a typo or an invented dataset). Keep in sync with the corpus; add new
# datasets here when a card legitimately introduces one.
KNOWN_DATASETS = frozenset({
    "xdr_data", "panw_ngfw_traffic_raw", "panw_ngfw_threat_raw", "pan_ngfw_traffic",
    "pan_dns", "network_story", "okta_sso", "saas_okta_raw", "saas_audit_logs",
    "cloud_audit_logs", "cloud_audit", "google_workspace_audit", "msft_o365",
    "msft_azure_ad_signin", "msft_o365_audit", "msft_windows_security", "ad_audit", "esxi_syslog",
    "cortex_cloud_posture", "prisma_browser_events", "asm_findings", "ai_spm_findings",
    "cortexsim_airs_raw", "koi_code_scan_events", "threat_intel", "risky_drive",
    "full_mailbox", "admin_consent", "benign",
    # Plan 02 — XDM modeling-rule raw source (un-normalized container telemetry
    # the modeling rule maps into XDM fields). Registered so the modeling-rule
    # source name does not WARN as an invented dataset.
    "cortexsim_container_raw",
    # Plan 04 — EMAIL plane ingestion sources (Proofpoint TAP / M365 / Defender
    # for Office 365 message + threat telemetry).
    "proofpoint_tap_raw", "msft_o365_email", "msft_defender_o365",
    # Unit 42 2026 expansion (TTP-2026-0106/0116/0118) — edge-appliance and
    # VPN raw sources for the ArcaneDoor (Cisco ASA/FTD), Ivanti Connect Secure,
    # and GlobalProtect cards. Convention-named (<vendor>_<product>_raw); the
    # exact tenant dataset string still needs live-tenant field confirmation per
    # RUNBOOK ("BIOC XQL dialects drift"), but these are real ingested products,
    # not invented sources, so they should not WARN as typos.
    "cisco_asa_raw", "ivanti_ics_raw", "panw_ngfw_globalprotect_raw",
})

# Datasets that carry SaaS/IdP sign-in telemetry ONLY — they do NOT contain
# on-prem Active Directory / Windows Security event data. A body that selects
# one of these but filters on AD/Windows-event content is an incoherent
# dataset↔content pairing (the TTP-2026-0063 class bug): Kerberos 4768/4769,
# servicePrincipalName, DCSync/DRSUAPI, ENUM.ACTIVE_DIRECTORY live in
# xdr_data / msft_windows_security / ad_audit, not in an SSO dataset.
SAAS_SSO_DATASETS = frozenset({"okta_sso", "saas_okta_raw"})
AD_WINDOWS_EVENT_TOKENS = (
    "ENUM.ACTIVE_DIRECTORY", "serviceprincipalname", "drsuapi", "dcsync",
    "krbtgt", "kerberos", "event_id = 4768", "event_id = 4769", "event_id = 4662",
    "event_id=4768", "event_id=4769", "event_id=4662",
)

_DATASET_SOURCE_RE = __import__("re").compile(
    r"(?:dataset|preset)\s*=\s*([a-z0-9_]+)", __import__("re").IGNORECASE
)


def _split_xql_stages(body):
    """Split an XQL body into `|`-delimited stages, ignoring pipe characters
    that appear INSIDE double-quoted string literals (regex alternations like
    ``(?:def|class)`` and command examples like ``/proc/pid/maps|mem`` are data,
    not stage boundaries). Returns the list of stage strings."""
    stages = []
    buf = []
    in_string = False
    for ch in body:
        if ch == '"':
            in_string = not in_string
            buf.append(ch)
        elif ch == "|" and not in_string:
            stages.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    stages.append("".join(buf))
    return stages


def lint_detection_body(report, rel, where, body, *, require_dataset):
    """GAP-12 — lightweight XQL/BIOC grammar sanity lint.

    Catches the cheap, high-signal mistakes that the JSON Schema cannot:
      * unbalanced double-quotes (odd count)
      * unbalanced parentheses
      * leftover placeholder/skeleton tokens from the draft generator
      * (when ``require_dataset``) a missing ``dataset =`` / ``preset =`` anchor

    This is a *grammar sanity* check, not a full XQL parser — it deliberately
    errs toward few false positives. Anything it flags is a hard validator
    error (the corpus must stay lint-clean).
    """
    if not isinstance(body, str) or not body.strip():
        report.err(rel, f"{where}: empty/non-string detection body")
        return

    # 1. Balanced double-quotes.
    if body.count('"') % 2 != 0:
        report.err(rel, f"{where}: unbalanced double-quotes in detection body")

    # 2. Balanced parentheses (running depth must never go negative and must
    #    end at zero). Parens *inside* double-quoted string literals are part of
    #    the matched data (e.g. command-line tokens like ".connect(" or
    #    "connect(") and must NOT count toward structure — only quotes already
    #    confirmed balanced in check 1. We track whether we are inside a string
    #    and skip those characters.
    depth = 0
    ok_parens = True
    in_string = False
    for ch in body:
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                ok_parens = False
                break
    if not ok_parens or depth != 0:
        report.err(rel, f"{where}: unbalanced parentheses in detection body")

    # 3. No placeholder/skeleton tokens.
    upper = body.upper()
    for tok in PLACEHOLDER_TOKENS:
        if tok.upper() in upper:
            report.err(rel, f"{where}: placeholder token {tok!r} in detection body")

    # 4. Dataset/preset anchor (BIOC + XQL only; correlation bodies reference
    #    BIOC(...)/XQL(...) names, not a dataset).
    if require_dataset and not any(a in body for a in DATASET_ANCHORS):
        report.err(rel, f"{where}: no dataset/preset reference in detection body")

    # 5. Dataset registry + stage-verb grammar + dataset↔content coherence.
    if require_dataset:
        _lint_xql_grammar(report, rel, where, body)


def _lint_xql_grammar(report, rel, where, body):
    """GAP-12 — deeper XQL grammar checks for BIOC/XQL bodies.

      * dataset/preset source is a known Cortex dataset (WARN if not)
      * each `| <verb>` stage opens with a known XQL stage verb (WARN if not)
      * dataset↔content coherence: a SaaS-SSO dataset paired with on-prem AD /
        Windows-event content is a hard ERROR (the TTP-2026-0063 class bug)

    Coherence is an ERROR because it is high-confidence and ships a detection
    that will silently return zero rows on a real tenant; the verb/dataset
    registry checks are WARN because the dialect is broad and evolving.
    """
    # (a) dataset source recognised?
    src_match = _DATASET_SOURCE_RE.search(body)
    dataset = src_match.group(1).lower() if src_match else None
    if dataset and dataset not in KNOWN_DATASETS:
        report.warn(rel, f"{where}: unrecognised dataset/preset {dataset!r} "
                         f"(typo or new source — add to KNOWN_DATASETS if real)")

    # (b) dataset↔content coherence (hard error).
    if dataset in SAAS_SSO_DATASETS:
        low = body.lower()
        hit = next((t for t in AD_WINDOWS_EVENT_TOKENS if t.lower() in low), None)
        if hit:
            report.err(
                rel,
                f"{where}: incoherent dataset↔content — source {dataset!r} is a "
                f"SaaS/IdP sign-in dataset but the body references AD/Windows-event "
                f"content ({hit!r}). AD/Kerberos events live in xdr_data / "
                f"msft_windows_security / ad_audit, not an SSO dataset.",
            )

    # (c) stage-verb grammar. Skip the first (source) segment; check the rest.
    #     Pipes inside string literals are NOT stage boundaries (handled by the
    #     string-aware splitter).
    segments = _split_xql_stages(body.strip())
    for seg in segments[1:]:
        seg = seg.strip()
        if not seg:
            continue
        verb = seg.split(None, 1)[0].lower()
        # A stage may open with a `dataset=`/`preset=` (a union sub-source) — skip.
        if verb in ("dataset", "preset") or "=" in verb:
            continue
        if verb not in XQL_STAGE_VERBS:
            report.warn(rel, f"{where}: unknown XQL stage verb {verb!r} "
                             f"(stage: '| {seg[:40]}...')")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="weights must sum to exactly 1.0 per use case")
    ap.add_argument("--quiet", action="store_true", help="suppress per-file PASS output")
    args = ap.parse_args()

    report = Report(quiet=args.quiet)

    # ---- 1. JSON well-formedness ----
    all_jsons = sorted(
        glob.glob(str(ROOT / "schema" / "*.json"))
        + glob.glob(str(ROOT / "sources" / "*.json"))
        + glob.glob(str(ROOT / "ttps" / "*.json"))
    )
    parsed = {}
    for f in all_jsons:
        d = load_json(f, report)
        if d is not None:
            parsed[f] = d
            report.ok(f"json:wellformed {Path(f).relative_to(ROOT)}")

    schema_path = ROOT / "schema" / "ttp-entry.schema.json"
    schema = parsed.get(str(schema_path))
    if schema is None:
        report.err("schema", "schema/ttp-entry.schema.json missing or unparsable")
        report.summary()
        sys.exit(2)

    # ---- 2. Schema itself is valid 2020-12 ----
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        report.err("env", "Install jsonschema>=4.18: pip install 'jsonschema>=4.18'")
        report.summary()
        sys.exit(2)

    try:
        Draft202012Validator.check_schema(schema)
        report.ok("schema:meta-valid Draft 2020-12")
    except Exception as e:
        report.err("schema", f"meta-schema check failed: {e}")
        report.summary()
        sys.exit(2)

    validator = Draft202012Validator(schema)

    # ---- 3. Source registry sanity ----
    registry_path = ROOT / "sources" / "source-registry.json"
    registry = parsed.get(str(registry_path))
    if registry is None:
        report.err("sources", "source-registry.json missing")
        report.summary()
        sys.exit(2)
    registered_ids = {s["id"] for s in registry.get("sources", [])}
    for sid in registered_ids:
        if not SOURCE_ID_PATTERN.match(sid):
            report.err("source-registry", f"source id {sid} does not match {SOURCE_ID_PATTERN.pattern}")
    report.ok(f"source-registry: {len(registered_ids)} sources loaded")

    # ---- 4. TTP-by-TTP checks ----
    ttp_files = sorted(glob.glob(str(ROOT / "ttps" / "*.json")))
    seen_ids = {}

    for f in ttp_files:
        rel = str(Path(f).relative_to(ROOT))
        d = parsed.get(f)
        if d is None:
            continue

        # 4a. Schema validation
        errs = sorted(validator.iter_errors(d), key=lambda e: list(e.absolute_path))
        for e in errs:
            path = "/".join(str(p) for p in e.absolute_path) or "(root)"
            report.err(f"{rel}", f"schema @ {path}: {e.message[:200]}")
        if errs:
            continue

        # 4b. ID format + uniqueness
        ttp_id = d.get("id", "")
        if not ID_PATTERN.match(ttp_id):
            report.err(rel, f"id {ttp_id!r} does not match {ID_PATTERN.pattern}")
            continue
        if ttp_id in seen_ids:
            report.err(rel, f"duplicate id {ttp_id}; also in {seen_ids[ttp_id]}")
        seen_ids[ttp_id] = rel

        # 4c. Filename should start with id
        if not Path(f).name.startswith(ttp_id):
            report.err(rel, f"filename does not start with id {ttp_id}")

        # 4d. Source ref cross-reference
        refs = set(d.get("metadata", {}).get("source_refs", []))
        unknown_refs = refs - registered_ids
        if unknown_refs:
            report.err(rel, f"metadata.source_refs has unknown ids: {sorted(unknown_refs)}")

        pub_ids = {r.get("publisher_id") for r in d.get("references", []) if r.get("publisher_id")}
        unknown_pubs = pub_ids - registered_ids
        if unknown_pubs:
            report.err(rel, f"references[].publisher_id has unknown ids: {sorted(unknown_pubs)}")

        # 4e. references[].publisher_id should be a subset of source_refs[]
        not_in_meta = pub_ids - refs
        if not_in_meta:
            report.warn(rel, f"references[].publisher_id {sorted(not_in_meta)} not also in metadata.source_refs")

        # 4f. Exactly one primary reference
        primaries = [r for r in d.get("references", []) if r.get("primary")]
        if len(primaries) == 0:
            report.warn(rel, "no references entry has primary: true (recommended)")
        elif len(primaries) > 1:
            report.err(rel, f"{len(primaries)} references entries have primary: true (must be exactly 1)")

        # 4g. MITRE technique format
        for t in d.get("mitre_attack", {}).get("techniques", []):
            tid = t.get("technique_id", "")
            sid = t.get("subtechnique_id")
            if not TECHNIQUE_PATTERN.match(tid):
                report.err(rel, f"mitre technique_id {tid!r} does not match Txxxx")
            if sid and not TECHNIQUE_PATTERN.match(sid):
                report.err(rel, f"mitre subtechnique_id {sid!r} does not match Txxxx.xxx")
            if sid and not sid.startswith(tid + "."):
                report.err(rel, f"subtechnique {sid} not under technique {tid}")

        # 4h. Use case / test case IDs + weight sums
        for uc in d.get("panw_mapping", {}).get("use_cases", []):
            uc_id = uc.get("use_case_id", "")
            if not UC_PATTERN.match(uc_id):
                report.err(rel, f"use_case_id {uc_id!r} does not match {UC_PATTERN.pattern}")
            weight_sum = 0.0
            for tc in uc.get("test_cases", []):
                tc_id = tc.get("test_case_id", "")
                if not TC_PATTERN.match(tc_id):
                    report.err(rel, f"test_case_id {tc_id!r} does not match {TC_PATTERN.pattern}")
                weight_sum += float(tc.get("expected_score_weight", 0.0))
            if weight_sum > 1.0001:
                report.err(rel, f"use case {uc_id} weights sum to {weight_sum:.3f} > 1.0")
            elif args.strict and abs(weight_sum - 1.0) > 0.001:
                report.err(rel, f"--strict: use case {uc_id} weights sum to {weight_sum:.3f}, must == 1.0")
            elif weight_sum < 0.999 and not args.strict:
                report.warn(rel, f"use case {uc_id} weights sum to {weight_sum:.3f} (< 1.0)")

        # 4i. pov_engine.platforms intersection sanity
        plats = set(d.get("metadata", {}).get("pov_engine", {}).get("platforms", []))
        target = d.get("execution", {}).get("target_platform")
        if target and target != "cross-platform" and target not in plats:
            report.warn(rel, f"execution.target_platform={target!r} not in metadata.pov_engine.platforms {sorted(plats)}")

        # 4j. GAP-12 — XQL/BIOC grammar sanity lint on every deployable body.
        detections = d.get("detections", {})
        for i, b in enumerate(detections.get("biocs", [])):
            lint_detection_body(report, rel, f"detections.biocs[{i}].logic",
                                b.get("logic"), require_dataset=True)
        for i, q in enumerate(detections.get("xql_queries", [])):
            lint_detection_body(report, rel, f"detections.xql_queries[{i}].query",
                                q.get("query"), require_dataset=True)
        for i, c in enumerate(detections.get("correlation_rules", [])):
            # Correlation logic is optional in the schema; only lint when present.
            logic = c.get("logic")
            if logic:
                lint_detection_body(report, rel, f"detections.correlation_rules[{i}].logic",
                                    logic, require_dataset=False)
        # Plan 01 — ABIOC bodies are XQL-shaped against a real dataset, lint like a BIOC.
        for i, a in enumerate(detections.get("abiocs", [])):
            lint_detection_body(report, rel, f"detections.abiocs[{i}].logic",
                                a.get("logic"), require_dataset=True)
        # Plan 02 — modeling-rule bodies open with `[MODEL: dataset=…]`; the
        # `dataset=` substring satisfies the anchor + dataset-registry checks.
        for i, m in enumerate(detections.get("modeling_rules", [])):
            lint_detection_body(report, rel, f"detections.modeling_rules[{i}].logic",
                                m.get("logic"), require_dataset=True)

        report.ok(f"ttp {ttp_id}")

    report.summary()
    sys.exit(1 if report.errors else 0)


if __name__ == "__main__":
    main()

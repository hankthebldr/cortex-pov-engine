#!/usr/bin/env python3
"""Dependency-light scenario-YAML linter for edit-time feedback.

The authoritative validator is the boot-time loader (core/engine/scenario_loader.py),
but that imports SQLAlchemy/Pydantic and only runs inside the app's Python 3.11
image — too heavy for a per-edit hook on the host. This linter reproduces the
loader's *contract* using only pyyaml, so it runs anywhere:

  - REQUIRED-field presence + enum membership + step structure  → ERROR (blocks),
    mirroring the loader, which REJECTS schema-invalid scenarios at boot.
  - cleanup shape (must be a {commands: [...]} mapping, not a list)  → ERROR,
    the exact CleanupSchema mismatch the loader rejects (Pydantic).
  - dangling ttp_ref / adapter_ref                              → WARNING only,
    mirroring the loader, which logs these as non-fatal warnings.
  - detection_id slug resolution against the referenced card    → WARNING,
    reproducing ttp_catalog._slug so a typo'd slug is caught at edit time.

Usage:  python3 lint-scenario.py <scenario.yml> [repo_root]
Exit:   0 = schema-valid (warnings may be printed), 1 = schema error / not lintable
"""
import glob
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    # No pyyaml → can't lint; don't block the edit, just say so.
    print("lint-scenario: pyyaml unavailable, skipping (boot loader is authoritative)")
    sys.exit(0)

PLANES = {
    "EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "ANALYTICS", "AI_ACCESS", "AIRS",
    "AI_SPM", "BROWSER", "KOI", "ASM", "CSPM", "TIM", "EMAIL",
}
DETECTION_TYPES = {"BIOC", "XQL", "Analytics", "Correlation", "IOC", "ABIOC"}
STATUSES = {"active", "draft", "deprecated"}
SCENARIO_ID_RE = re.compile(r"^SIM-[A-Z_]+-\d{3,}$")

REQUIRED_TOP = [
    "scenario_id", "name", "version", "status", "plane", "detection_types",
    "uc_ref", "tc_ref", "uc_name", "tc_name",
    "mitre_tactic", "mitre_tactic_name", "mitre_technique", "mitre_technique_name",
    "execution_identity", "push_supported", "pull_supported", "steps",
]
REQUIRED_STEP = ["id", "name", "command", "identity", "mitre_technique", "expected_detections"]
REQUIRED_DET = ["plane", "type", "description"]


def _index_ids(pattern: str, id_regex: str) -> set:
    ids = set()
    rx = re.compile(id_regex)
    for path in glob.glob(pattern, recursive=True):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        ids.update(rx.findall(text))
    return ids


def _slug(name: str, prefix: str) -> str:
    """EXACT copy of core/engine/ttp_catalog._slug so resolution never false-warns."""
    clean = "".join(c if c.isalnum() else "-" for c in str(name).lower()).strip("-")
    while "--" in clean:
        clean = clean.replace("--", "-")
    return f"{prefix}-{clean}"[:120]


def _card_detection_ids(card: dict) -> set:
    """Rebuild the set of valid detection_ids a card exposes, mirroring the
    per-kind id derivation in ttp_catalog (explicit detection_id/rule_id wins,
    else the slug of the name/value)."""
    dets = card.get("detections") or {}
    ids: set = set()
    for i, b in enumerate(dets.get("biocs") or []):
        ids.add(b.get("detection_id") or _slug(b.get("name") or f"bioc-{i+1}", "bioc"))
    for i, q in enumerate(dets.get("xql_queries") or []):
        ids.add(q.get("detection_id") or _slug(q.get("name") or f"xql-{i+1}", "xql"))
    for i, a in enumerate(dets.get("abiocs") or []):
        ids.add(a.get("detection_id") or _slug(a.get("name") or f"abioc-{i+1}", "abioc"))
    for i, r in enumerate(dets.get("correlation_rules") or []):
        ids.add(r.get("rule_id") or r.get("detection_id")
                or _slug(r.get("name") or f"correlation-{i+1}", "correlation"))
    for i, m in enumerate(dets.get("modeling_rules") or []):
        ids.add(m.get("detection_id") or _slug(m.get("name") or f"modeling-{i+1}", "modeling"))
    for i, ioc in enumerate(dets.get("iocs") or []):
        if isinstance(ioc, dict):
            val = ioc.get("value") or f"ioc-{i+1}"
            ioc_type = ioc.get("type") or ioc.get("ioc_type") or "ioc"
            ids.add(ioc.get("detection_id") or _slug(f"{ioc_type}-{val}", "ioc"))
        else:
            ids.add(_slug(str(ioc), "ioc"))
    return ids


def _load_cards_by_id(root: Path) -> dict:
    """Map every TTP card id -> its parsed JSON (for slug resolution)."""
    out: dict = {}
    for path in glob.glob(str(root / "detection_scanner" / "ttps" / "*.json")):
        try:
            card = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(card, dict) and card.get("id"):
            out[card["id"]] = card
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("lint-scenario: no file given", file=sys.stderr)
        return 1
    filepath = Path(sys.argv[1])
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()

    try:
        raw = yaml.safe_load(filepath.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: YAML parse error: {exc}")
        return 1
    if not isinstance(raw, dict):
        print("ERROR: YAML root is not a mapping")
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED_TOP:
        if key not in raw or raw[key] in (None, "", []):
            errors.append(f"missing required field: {key}")

    sid = raw.get("scenario_id", "")
    if sid and not SCENARIO_ID_RE.match(str(sid)):
        errors.append(f"scenario_id '{sid}' does not match SIM-{{PLANE}}-{{NNN}}")
    if raw.get("status") and raw["status"] not in STATUSES:
        errors.append(f"status '{raw['status']}' not in {sorted(STATUSES)}")
    if raw.get("plane") and raw["plane"] not in PLANES:
        errors.append(f"plane '{raw['plane']}' not a valid detection plane")

    dts = raw.get("detection_types") or []
    if isinstance(dts, list):
        bad = [d for d in dts if d not in DETECTION_TYPES]
        if bad:
            errors.append(f"detection_types {bad} not in {sorted(DETECTION_TYPES)}")

    # execution_identity + default ∈ options
    ident = raw.get("execution_identity") or {}
    options = set()
    if isinstance(ident, dict):
        options = set(ident.get("options") or [])
        default = ident.get("default")
        if not default:
            errors.append("execution_identity.default missing")
        elif options and default not in options:
            errors.append(f"execution_identity.default '{default}' not in options")
        if not options:
            errors.append("execution_identity.options missing/empty")
    else:
        errors.append("execution_identity must be a mapping")

    # steps
    steps = raw.get("steps") or []
    if isinstance(steps, list):
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                errors.append(f"step[{i}] is not a mapping")
                continue
            for key in REQUIRED_STEP:
                if key not in step or step[key] in (None, "", []):
                    errors.append(f"step[{i}] ({step.get('id', '?')}) missing: {key}")
            sid_ident = step.get("identity")
            if sid_ident and options and sid_ident not in options:
                errors.append(
                    f"step[{i}] identity '{sid_ident}' not in execution_identity.options"
                )
            for j, det in enumerate(step.get("expected_detections") or [], 1):
                if not isinstance(det, dict):
                    errors.append(f"step[{i}].expected_detections[{j}] is not a mapping")
                    continue
                for key in REQUIRED_DET:
                    if key not in det or det[key] in (None, ""):
                        errors.append(
                            f"step[{i}].expected_detections[{j}] missing: {key}"
                        )
                dtype = det.get("type")
                if dtype and dtype not in DETECTION_TYPES:
                    errors.append(
                        f"step[{i}].expected_detections[{j}] type '{dtype}' invalid"
                    )

    # ---- cleanup shape (ERROR — loader's CleanupSchema rejects a list) ----
    cleanup = raw.get("cleanup")
    if cleanup is not None:
        if not isinstance(cleanup, dict):
            errors.append(
                f"cleanup must be a mapping {{commands: [...]}}, not a "
                f"{type(cleanup).__name__} (a YAML list fails the loader's CleanupSchema)"
            )
        elif cleanup.get("commands") is not None and not isinstance(cleanup["commands"], list):
            errors.append("cleanup.commands must be a list of strings")

    # ---- dangling reference WARNINGS (non-fatal, matches loader) ----
    ttp_refs, adapter_refs, detection_ids = set(), set(), set()
    det_pairs: list = []  # (ttp_ref, detection_id) for slug resolution
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        for det in step.get("expected_detections") or []:
            if isinstance(det, dict):
                if det.get("ttp_ref"):
                    ttp_refs.add(det["ttp_ref"])
                if det.get("detection_id"):
                    detection_ids.add(det["detection_id"])
                    det_pairs.append((det.get("ttp_ref"), det["detection_id"]))
    for tool in raw.get("external_tools") or []:
        if isinstance(tool, dict) and tool.get("adapter_ref"):
            adapter_refs.add(tool["adapter_ref"])

    if ttp_refs:
        known = _index_ids(str(root / "detection_scanner" / "ttps" / "*.json"),
                            r"TTP-\d{4}-\d{4}")
        for ref in sorted(ttp_refs - known):
            warnings.append(f"dangling ttp_ref: {ref} (no card in detection_scanner/ttps/)")
    if adapter_refs:
        known = _index_ids(str(root / "tools" / "packs" / "*.yml"),
                           r"TOOL-[A-Z0-9-]+")
        for ref in sorted(adapter_refs - known):
            warnings.append(f"dangling adapter_ref: {ref} (no pack in tools/packs/)")
    if det_pairs:
        cards = _load_cards_by_id(root)
        for ttp_ref, det_id in det_pairs:
            if not ttp_ref:
                warnings.append(f"detection_id '{det_id}' has no ttp_ref to resolve against")
                continue
            card = cards.get(ttp_ref)
            if card is None:
                continue  # already flagged as a dangling ttp_ref above
            valid = _card_detection_ids(card)
            if det_id not in valid:
                warnings.append(
                    f"detection_id '{det_id}' does not resolve in card {ttp_ref} "
                    f"(the boot loader would log this as a dangling detection)"
                )

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")

    if errors:
        print(f"\n{len(errors)} schema error(s) — the boot loader would REJECT this file.")
        return 1
    print(f"OK: {sid or filepath.name} is schema-valid"
          + (f" ({len(warnings)} warning(s))" if warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

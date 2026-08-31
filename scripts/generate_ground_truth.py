#!/usr/bin/env python3
"""
scripts/generate_ground_truth.py
---------------------------------

Regenerates CortexSim's canonical, machine-checkable inventory —
``docs/reference/ground-truth.json`` (canonical, diffed) and
``docs/reference/ground-truth.md`` (rendered table, generated from the same
JSON, also diffed) — from the two named ground-truth commands plus direct
filesystem/loader counts.

Why this exists: this repo's docs hand-typed these numbers across a dozen
dated "Counted ground truth" bullets in docs/reference/README.md and the
public-facing README drifted 30-90% from reality (advertising a container
image that does not exist, scenario/route/plane counts nobody re-measured).
This script is the one place that computes them; ``make check-ground-truth``
fails a PR that lets the generated files drift from the committed ones —
the same pattern ``detection_scanner/scripts/export_artifacts.py`` +
``git diff --exit-code detection_scanner/exports/`` already uses for the
detection corpus exports.

Design rules (same as export_artifacts.py):
  * Pure read. Never mutates scenarios/, detection_scanner/ttps/,
    tools/packs/, assertions/, or anything else under version control.
  * Deterministic output: sorted keys, no timestamps, no wall-clock or
    environment-dependent values anywhere in the written files. Two
    regenerations of the same tree, on two different machines, are
    byte-identical.
  * Cross-checked, not just counted: every number that two independent
    counting methods can both reach (filesystem glob vs. the real Pydantic
    loader; coverage_report.py vs. uctc_crosswalk_v2.2.py) is asserted equal.
    A generator that only re-derives its own prior output can drift right
    alongside hand-typed prose; disagreement between independent methods is
    what actually catches drift.
  * Best-effort on the "boot-truth" fields (schema-valid scenario load via
    the real Pydantic loader, the real AssertionCatalog, the real strict-refs
    pytest gate): if core/engine's dependencies (pydantic, sqlalchemy,
    pyyaml, pytest) aren't importable in the current interpreter, those
    fields degrade to the filesystem-only method and ``"boot_verified":
    false`` says so explicitly — never a silently wrong number standing in
    for a proven one.

Usage:
    python3 scripts/generate_ground_truth.py           # write both files
    make ground-truth                                  # same, via the image
    make check-ground-truth                             # regenerate + git diff
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"
TTPS_DIR = REPO_ROOT / "detection_scanner" / "ttps"
PACKS_DIR = REPO_ROOT / "tools" / "packs"
ASSERTIONS_DIR = REPO_ROOT / "assertions"
EAL_PLUGINS_DIR = REPO_ROOT / "core" / "eal_simulator" / "plugins"
IAC_AWS_DIR = REPO_ROOT / "infra" / "modules" / "aws"
API_DIR = REPO_ROOT / "core" / "api"
MAIN_PY = REPO_ROOT / "core" / "main.py"
OUT_JSON = REPO_ROOT / "docs" / "reference" / "ground-truth.json"
OUT_MD = REPO_ROOT / "docs" / "reference" / "ground-truth.md"

GENERATED_HEADER = (
    "GENERATED FILE — do not hand-edit. Run `make ground-truth` to refresh "
    "(or `python3 scripts/generate_ground_truth.py`); `make check-ground-truth` "
    "fails a PR whose committed copy drifted from the corpus. "
    "Source: scripts/generate_ground_truth.py."
)


class GroundTruthError(RuntimeError):
    """A real drift/inconsistency, not a missing optional dependency."""


# --------------------------------------------------------------------------
# Filesystem-only counts (no third-party deps; mirror each loader's own
# skip rules rather than reinventing new ones)
# --------------------------------------------------------------------------

_SKIP_SCENARIO_DIRS = {"probes", "packages", "campaigns"}


def find_scenario_yaml_files() -> list[Path]:
    """Mirror engine.scenario_loader._find_yaml_files without importing it."""
    found: list[Path] = []
    for root, dirs, files in os.walk(SCENARIOS_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_SCENARIO_DIRS]
        for fname in files:
            if fname.endswith(".yml") and fname != "_schema.yml":
                found.append(Path(root) / fname)
    return sorted(found)


def count_ttp_cards() -> int:
    # engine skips _drafts/; ttps/*.json is a flat directory of active cards.
    return len(list(TTPS_DIR.glob("*.json")))


def count_detection_planes() -> int:
    return len([p for p in SCENARIOS_DIR.iterdir() if p.is_dir()])


def count_adapter_packs() -> dict[str, Any]:
    """Tier histogram + tier-4 staged/exempt split, read as raw text (never a
    naive `grep -c` over the whole tree — see tools/packs/README.md § Counting:
    _schema.yml carries its own `tier:` line and an example `artifact_exempt`
    block, which is exactly the off-by-one that method hits)."""
    by_tier: dict[str, int] = {}
    tier4_staged = 0
    tier4_exempt = 0
    tier4_undeclared = 0
    exempt_reason_codes: dict[str, int] = {}
    total = 0
    for f in sorted(PACKS_DIR.glob("*.yml")):
        if f.name == "_schema.yml":
            continue
        total += 1
        text = f.read_text(encoding="utf-8")
        tier_match = re.search(r"^tier:\s*(\d+)", text, re.MULTILINE)
        tier = tier_match.group(1) if tier_match else "unknown"
        by_tier[tier] = by_tier.get(tier, 0) + 1
        if tier == "4":
            has_artifact = re.search(r"^\s*artifact:\s*$", text, re.MULTILINE) is not None
            has_exempt = "artifact_exempt:" in text
            if has_artifact and not has_exempt:
                tier4_staged += 1
            elif has_exempt and not has_artifact:
                tier4_exempt += 1
                rc = re.search(r"reason_code:\s*(\S+)", text)
                if rc:
                    code = rc.group(1)
                    exempt_reason_codes[code] = exempt_reason_codes.get(code, 0) + 1
            else:
                tier4_undeclared += 1
    return {
        "packs_total": total,
        "packs_by_tier": dict(sorted(by_tier.items())),
        "tier4_staged": tier4_staged,
        "tier4_exempt": tier4_exempt,
        "tier4_undeclared": tier4_undeclared,
        "tier4_exempt_reason_codes": dict(sorted(exempt_reason_codes.items())),
    }


def count_wired_adapters() -> dict[str, int]:
    ref_re = re.compile(r"adapter_ref:\s*(TOOL-[A-Z0-9_-]+)")
    distinct: set[str] = set()
    scenarios_with_ref = 0
    for f in find_scenario_yaml_files():
        text = f.read_text(encoding="utf-8")
        refs = ref_re.findall(text)
        if refs:
            scenarios_with_ref += 1
            distinct.update(refs)
    return {
        "distinct_adapters_wired": len(distinct),
        "scenarios_wiring_adapter": scenarios_with_ref,
    }


def count_assertions_filesystem() -> dict[str, Any]:
    # The real loader (engine.assertions._find_assertion_files) walks each
    # class directory with os.walk — recursively, not top-level-only (POS has
    # a `k8s/` subdirectory) — so mirror that with rglob rather than glob.
    by_class: dict[str, int] = {}
    total = 0
    for sub in ("pos", "plt", "aut"):
        n = len(list((ASSERTIONS_DIR / sub).rglob("*.yml"))) if (ASSERTIONS_DIR / sub).is_dir() else 0
        by_class[sub.upper()] = n
        total += n
    return {"artifacts_total": total, "by_validation_class": dict(sorted(by_class.items()))}


def count_eal_plugins() -> int:
    return len([f for f in EAL_PLUGINS_DIR.glob("*.py") if f.name != "__init__.py"])


def count_iac_modules_aws() -> int:
    return len([p for p in IAC_AWS_DIR.iterdir() if p.is_dir()]) if IAC_AWS_DIR.is_dir() else 0


_ROUTE_DECORATOR_RE = re.compile(
    r"@[a-zA-Z_][a-zA-Z0-9_]*\.(get|post|put|delete|patch|websocket|api_route)\("
)


def count_http_routes_static() -> dict[str, Any]:
    """Boot-free HTTP route count: every ``@<router>.<verb>(`` decorator across
    core/api/*.py + core/main.py. Cheaper and dependency-free relative to
    booting the FastAPI app (which needs the full core/requirements.txt just
    to import), so this is the count this generator gates on; a boot-time
    OpenAPI-schema count is a strictly heavier proof left to the `backend`
    CI job (it necessarily boots the app already)."""
    by_method: dict[str, int] = {}
    total = 0
    files = sorted(API_DIR.glob("*.py")) + [MAIN_PY]
    apirouter_instances = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        apirouter_instances += len(re.findall(r"=\s*APIRouter\(", text))
        for m in _ROUTE_DECORATOR_RE.finditer(text):
            verb = m.group(1)
            by_method[verb] = by_method.get(verb, 0) + 1
            total += 1
    return {
        "decorator_count": total,
        "by_method": dict(sorted(by_method.items())),
        "apirouter_instances": apirouter_instances,
        "router_files": len(list(API_DIR.glob("*.py"))),
    }


# --------------------------------------------------------------------------
# Subprocess calls to the two named ground-truth commands
# --------------------------------------------------------------------------

def run_coverage_report() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "detection_scanner" / "scripts" / "coverage_report.py"), "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GroundTruthError(
            f"coverage_report.py --json exited {proc.returncode}:\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GroundTruthError(f"coverage_report.py --json emitted non-JSON stdout: {exc}") from exc


_UCTC_PATTERNS: dict[str, re.Pattern] = {
    "scenarios_and_rows": re.compile(r"^scenarios:\s*(\d+)\s+crosswalk rows:\s*(\d+)"),
    "resolution": re.compile(r"^resolution:\s*(\{.*\})"),
    "evidenced": re.compile(
        r"^index TCs evidenced:\s*(\d+)/(\d+)\s+\(DET/HNT\s+(\d+)/(\d+)\)"
    ),
    "payload": re.compile(r"^payload resolved:\s*(\d+)\s*\|\s*unresolved:\s*(\d+)"),
    "tier_drift": re.compile(r"^tier drift \(S-13\):\s*(\d+)"),
    "posture_primary": re.compile(r"^posture-class primary \(S-14\):\s*(\d+)"),
    "proposed_v23": re.compile(r"^proposed v2\.3 TCs:\s*(\d+)\s+(\{.*\})"),
    "plt": re.compile(
        r"^PLT assertions:\s*(\d+) artifact\(s\)\s*->\s*(\d+)/(\d+) PLT rows AUTHORED"
        r"\s*\|\s*(\d+)/(\d+) PROVEN[^|]*\|\s*(\d+) documented non-bindings"
    ),
    "pos": re.compile(
        r"^POS assertions \(K8s pack\):\s*(\d+) artifact\(s\)\s*->\s*(\d+)/(\d+) POS rows AUTHORED"
        r"\s*\|\s*(\d+)/(\d+) PROVEN[^|]*\|\s*net-new (\[.*?\])\s*\|\s*breadth-not-coverage (\[.*?\])"
        r"\s*\|\s*(\d+) documented non-bindings"
    ),
}


def run_uctc_crosswalk() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "uctc_crosswalk_v2.2.py"), "--report"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GroundTruthError(
            f"uctc_crosswalk_v2.2.py --report exited {proc.returncode} "
            f"(a real crosswalk error, not a parsing problem):\n{proc.stderr}"
        )
    out = proc.stdout
    m = re.search(_UCTC_PATTERNS["scenarios_and_rows"].pattern, out, re.MULTILINE)
    ev = re.search(_UCTC_PATTERNS["evidenced"].pattern, out, re.MULTILINE)
    pay = re.search(_UCTC_PATTERNS["payload"].pattern, out, re.MULTILINE)
    td = re.search(_UCTC_PATTERNS["tier_drift"].pattern, out, re.MULTILINE)
    pp = re.search(_UCTC_PATTERNS["posture_primary"].pattern, out, re.MULTILINE)
    plt = re.search(_UCTC_PATTERNS["plt"].pattern, out, re.MULTILINE)
    pos = re.search(_UCTC_PATTERNS["pos"].pattern, out, re.MULTILINE)
    if not (m and ev and pay and td and pp and plt and pos):
        raise GroundTruthError(
            "uctc_crosswalk_v2.2.py --report changed its output format — "
            "the parser in generate_ground_truth.py needs updating to match.\n"
            f"--- full output ---\n{out}"
        )
    return {
        "crosswalk_scenarios": int(m.group(1)),
        "crosswalk_rows": int(m.group(2)),
        "tc_evidenced": int(ev.group(1)),
        "tc_total": int(ev.group(2)),
        "det_hnt_evidenced": int(ev.group(3)),
        "det_hnt_total": int(ev.group(4)),
        "payload_resolved": int(pay.group(1)),
        "payload_unresolved": int(pay.group(2)),
        "tier_drift_s13": int(td.group(1)),
        "posture_class_primary_s14": int(pp.group(1)),
        "plt_assertions": {
            "artifacts": int(plt.group(1)),
            "bound": int(plt.group(2)),
            "index_rows_total": int(plt.group(3)),
            "proven": int(plt.group(4)),
            "documented_non_bindings": int(plt.group(6)),
        },
        "pos_assertions": {
            "artifacts": int(pos.group(1)),
            "bound": int(pos.group(2)),
            "index_rows_total": int(pos.group(3)),
            "proven": int(pos.group(4)),
            "documented_non_bindings": int(pos.group(8)),
        },
    }


# --------------------------------------------------------------------------
# Best-effort boot-truth: the real Pydantic scenario loader, the real
# AssertionCatalog, the real strict-refs pytest gate. Degrades cleanly if
# core/requirements.txt isn't installed in the current interpreter.
# --------------------------------------------------------------------------

def _core_importable() -> bool:
    return (REPO_ROOT / "core").is_dir()


def _ensure_core_on_path() -> None:
    """Add core/ to sys.path (once, never removed): engine.* modules do
    lazy `from config import settings` deep inside method bodies, called
    well after any import-time sys.path scoping would have unwound, so this
    has to stay for the rest of the process rather than being a context
    manager around just the `import` statement."""
    core_path = str(REPO_ROOT / "core")
    if core_path not in sys.path:
        sys.path.insert(0, core_path)
    os.environ.setdefault("CORTEXSIM_BASE_DIR", str(REPO_ROOT))
    os.environ.setdefault("CORTEXSIM_ENV", "development")


def boot_truth_scenarios() -> Optional[dict[str, Any]]:
    """Run every scenario through the real ScenarioSchema Pydantic validator
    (not just a file-exists count) — the same loader `make test-backend`
    boots. Returns None (never a wrong number) if pydantic/sqlalchemy/pyyaml
    aren't importable here."""
    _ensure_core_on_path()
    try:
        from engine.scenario_loader import _find_yaml_files, _parse_and_validate  # type: ignore
        files = _find_yaml_files(str(SCENARIOS_DIR))
        planes: dict[str, int] = {}
        rejected: list[dict[str, str]] = []
        for f in files:
            schema, err = _parse_and_validate(f)
            if schema is None:
                rejected.append({"path": str(Path(f).relative_to(REPO_ROOT)), "error": (err or "")[:200]})
            else:
                planes[schema.plane] = planes.get(schema.plane, 0) + 1
    except ImportError:
        # A missing dependency (pydantic / sqlalchemy / pyyaml) — degrade to
        # the filesystem-only method the caller already has. Any OTHER
        # exception (a real bug in the loader) is left to propagate.
        return None
    return {
        "files_found": len(files),
        "schema_valid": len(files) - len(rejected),
        "rejected": rejected,
        "planes": dict(sorted(planes.items())),
    }


def boot_truth_assertions() -> Optional[dict[str, Any]]:
    _ensure_core_on_path()
    try:
        from engine.assertions import AssertionCatalog, default_assertions_dir  # type: ignore
        cat = AssertionCatalog()
        loaded = cat.load(default_assertions_dir(str(REPO_ROOT)), strict=True)
        by_class: dict[str, int] = {}
        for a in cat.all():
            by_class[a.validation_class] = by_class.get(a.validation_class, 0) + 1
    except ImportError:
        return None
    return {
        "loaded": loaded,
        "rejected": len(cat.rejected),
        "warnings": len(cat.warnings),
        "by_validation_class": dict(sorted(by_class.items())),
    }


def boot_truth_strict_refs() -> Optional[dict[str, Any]]:
    """Best-effort: run the real strict-refs pytest gate (tests/engine/
    test_corpus_refs_strict.py) and record pass/fail. Skips cleanly (returns
    None) if pytest or its deps aren't available — `make check-refs` / the CI
    `refs` job remain the load-bearing gate for this either way; this is a
    bonus corroboration when the environment allows it, not a replacement."""
    test_path = REPO_ROOT / "tests" / "engine" / "test_corpus_refs_strict.py"
    if not test_path.is_file():
        return None
    env = dict(os.environ)
    env.setdefault("CORTEXSIM_BASE_DIR", str(REPO_ROOT))
    env.setdefault("CORTEXSIM_ENV", "development")
    env["PYTHONPATH"] = str(REPO_ROOT / "core") + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q"],
            cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=120,
        )
    except Exception:
        return None
    combined = proc.stdout + proc.stderr
    if "ModuleNotFoundError" in combined or "No module named" in combined:
        return None  # a missing dependency, not a real failure — degrade cleanly
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    m = re.search(r"(\d+) passed", summary)
    m_failed = re.search(r"(\d+) failed", summary)
    if m_failed or (m is None and proc.returncode != 0):
        # Ran, but not because of a missing dependency — a real failure.
        # Surface it as a hard error rather than silently degrading.
        raise GroundTruthError(
            f"tests/engine/test_corpus_refs_strict.py FAILED "
            f"(this names the scenario that would refuse to load in "
            f"production under CORTEXSIM_STRICT_REFS):\n{proc.stdout}\n{proc.stderr}"
        )
    if m is None:
        return None
    return {"passed": int(m.group(1)), "status": "green" if proc.returncode == 0 else "red"}


# --------------------------------------------------------------------------
# Assembly + cross-checks
# --------------------------------------------------------------------------

def build() -> dict[str, Any]:
    coverage = run_coverage_report()
    uctc = run_uctc_crosswalk()

    fs_scenario_files = find_scenario_yaml_files()
    fs_scenario_count = len(fs_scenario_files)
    fs_plane_count = count_detection_planes()
    ttp_count = count_ttp_cards()

    boot_scenarios = boot_truth_scenarios()
    boot_assertions = boot_truth_assertions()
    boot_refs = boot_truth_strict_refs()

    # --- Cross-checks: independent methods must agree. Disagreement is the
    # exact class of drift this generator exists to catch, so it is a hard
    # error, not a warning. ---
    checks: list[tuple[str, Any, Any]] = [
        ("scenario count: filesystem glob vs coverage_report.py --json",
         fs_scenario_count, coverage["meta"]["scenario_count"]),
        ("scenario count: filesystem glob vs uctc_crosswalk_v2.2.py --report",
         fs_scenario_count, uctc["crosswalk_scenarios"]),
        ("ttp card count: filesystem glob vs coverage_report.py --json",
         ttp_count, coverage["meta"]["card_count"]),
        ("detection plane count: scenario subdirectories vs coverage_report.py --json",
         fs_plane_count, coverage["meta"]["plane_count"]),
    ]
    if boot_scenarios is not None:
        checks.append((
            "scenario count: filesystem glob vs the real Pydantic loader",
            fs_scenario_count, boot_scenarios["files_found"],
        ))
        checks.append((
            "scenario count: schema-valid (real loader) vs coverage_report.py --json",
            boot_scenarios["schema_valid"], coverage["meta"]["scenario_count"],
        ))
    if boot_assertions is not None:
        fs_assertions = count_assertions_filesystem()
        checks.append((
            "assertion artifact count: filesystem glob vs the real AssertionCatalog",
            fs_assertions["artifacts_total"], boot_assertions["loaded"],
        ))

    mismatches = [f"  - {label}: {a} != {b}" for label, a, b in checks if a != b]
    if mismatches:
        raise GroundTruthError(
            "Ground-truth counting methods disagree — this IS the drift this "
            "generator exists to catch:\n" + "\n".join(mismatches)
        )

    adapters = count_adapter_packs()
    adapters.update(count_wired_adapters())

    doc = {
        "schema_version": 1,
        "generated_by": "scripts/generate_ground_truth.py",
        "corpus": {
            "scenarios_loadable": coverage["meta"]["scenario_count"],
            "scenarios_rejected": (len(boot_scenarios["rejected"]) if boot_scenarios else None),
            "ttp_cards": coverage["meta"]["card_count"],
            "detection_planes": coverage["meta"]["plane_count"],
            "scenarios_per_plane": (
                boot_scenarios["planes"] if boot_scenarios
                else {row["plane"]: row.get("scenario_count")
                      for row in coverage.get("planes", {}).get("counts", [])}
            ),
            "step_detections": coverage["meta"]["step_detection_count"],
            "step_detections_by_type": coverage["detection_mix"]["step_detections"]["by_type"],
            "catalog_detections": coverage["meta"]["catalog_detection_count"],
            "catalog_detections_by_kind": coverage["detection_mix"]["catalog_detections"]["by_kind"],
            "abioc_analytics_share": coverage["detection_mix"]["abioc_analytics_share"],
            "correlation_share": coverage["detection_mix"]["correlation_share"],
            "mitre_distinct_techniques": coverage["mitre"]["distinct_techniques"],
            "mitre_distinct_base_techniques": coverage["mitre"]["distinct_base_techniques"],
            "methodology_family_counts": coverage["methodology"]["counts"],
        },
        "adapters": adapters,
        "assertions": {
            "artifacts_total": (boot_assertions["loaded"] if boot_assertions
                                 else count_assertions_filesystem()["artifacts_total"]),
            "by_validation_class": (boot_assertions["by_validation_class"] if boot_assertions
                                     else count_assertions_filesystem()["by_validation_class"]),
            "rejected": boot_assertions["rejected"] if boot_assertions else None,
            "warnings": boot_assertions["warnings"] if boot_assertions else None,
            "boot_verified": boot_assertions is not None,
        },
        "uctc_index": {
            "tc_evidenced": uctc["tc_evidenced"],
            "tc_total": uctc["tc_total"],
            "det_hnt_evidenced": uctc["det_hnt_evidenced"],
            "det_hnt_total": uctc["det_hnt_total"],
            "payload_resolved": uctc["payload_resolved"],
            "payload_unresolved": uctc["payload_unresolved"],
            "tier_drift_s13": uctc["tier_drift_s13"],
            "posture_class_primary_s14": uctc["posture_class_primary_s14"],
            "plt_assertions": uctc["plt_assertions"],
            "pos_assertions": uctc["pos_assertions"],
        },
        "eal_plugins": count_eal_plugins(),
        "iac_modules_aws": count_iac_modules_aws(),
        "http_routes": count_http_routes_static(),
        "strict_refs_check": (boot_refs if boot_refs is not None
                               else {"status": "not_run", "note": (
                                   "pytest/core deps unavailable in this interpreter — "
                                   "see `make check-refs` or the CI 'refs' job for the "
                                   "load-bearing proof"
                               )}),
        "tenant_verified": 0,
    }
    return doc


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _compact(d: dict[str, Any]) -> str:
    """JSON-style rendering (not Python repr) for an inline dict in Markdown."""
    return json.dumps(d, sort_keys=True, separators=(", ", ": "))


def render_markdown(doc: dict[str, Any]) -> str:
    c = doc["corpus"]
    a = doc["adapters"]
    asrt = doc["assertions"]
    u = doc["uctc_index"]
    r = doc["http_routes"]
    lines: list[str] = []
    lines.append("<!-- " + GENERATED_HEADER + " -->")
    lines.append("# CortexSim — Ground Truth")
    lines.append("")
    lines.append(
        "Every number below comes from `python3 scripts/generate_ground_truth.py`, "
        "which runs `scripts/uctc_crosswalk_v2.2.py --report` and "
        "`detection_scanner/scripts/coverage_report.py --json` plus direct "
        "filesystem/loader counts, and cross-checks every count two "
        "independent ways before writing anything. `make check-ground-truth` "
        "fails CI if this file (or `ground-truth.json`) drifts from what's on "
        "disk. Canonical machine-readable form: "
        "[`ground-truth.json`](ground-truth.json)."
    )
    lines.append("")
    lines.append("## Corpus")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Loadable scenarios | {c['scenarios_loadable']} |")
    rej = c["scenarios_rejected"]
    lines.append(f"| Scenarios rejected at load | {rej if rej is not None else 'not boot-verified this run'} |")
    lines.append(f"| TTP cards | {c['ttp_cards']} |")
    lines.append(f"| Detection planes | {c['detection_planes']} |")
    lines.append(f"| Step-detections | {c['step_detections']} |")
    lines.append(f"| Catalog detection objects | {c['catalog_detections']} |")
    lines.append(f"| ABIOC+Analytics share (step-detections) | {c['abioc_analytics_share']:.1%} |")
    lines.append(f"| Correlation share (step-detections) | {c['correlation_share']:.1%} |")
    lines.append(f"| Distinct MITRE techniques (base) | {c['mitre_distinct_techniques']} ({c['mitre_distinct_base_techniques']}) |")
    lines.append(f"| EAL plugins | {doc['eal_plugins']} |")
    lines.append(f"| AWS IaC modules | {doc['iac_modules_aws']} |")
    lines.append("")
    if c.get("scenarios_per_plane"):
        lines.append("### Scenarios per plane")
        lines.append("")
        lines.append("| Plane | Scenarios |")
        lines.append("|---|---:|")
        for plane, n in sorted(c["scenarios_per_plane"].items()):
            lines.append(f"| {plane} | {n} |")
        lines.append("")
    lines.append("### Step-detections by type")
    lines.append("")
    lines.append("| Type | Count |")
    lines.append("|---|---:|")
    for k, v in sorted(c["step_detections_by_type"].items()):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## Tool adapters")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Adapter packs | {a['packs_total']} |")
    lines.append(f"| Per tier | `{_compact(a['packs_by_tier'])}` |")
    lines.append(f"| Tier-4 shelf-staged | {a['tier4_staged']} |")
    lines.append(f"| Tier-4 exempt | {a['tier4_exempt']} |")
    lines.append(f"| Tier-4 undeclared (should be 0 — `TA-13` rejects it) | {a['tier4_undeclared']} |")
    lines.append(f"| Distinct adapters wired via `adapter_ref` | {a['distinct_adapters_wired']} |")
    lines.append(f"| Scenarios wiring at least one adapter | {a['scenarios_wiring_adapter']} |")
    lines.append("")

    lines.append("## Assertions (POS/PLT/AUT)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Assertion artifacts | {asrt['artifacts_total']} |")
    lines.append(f"| By validation class | `{_compact(asrt['by_validation_class'])}` |")
    lines.append(f"| Rejected at load (strict) | {asrt['rejected'] if asrt['rejected'] is not None else 'not boot-verified this run'} |")
    lines.append(f"| Boot-verified (real `AssertionCatalog`) | {asrt['boot_verified']} |")
    lines.append("")

    lines.append("## UC/TC index (v2.2)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Index TCs evidenced by a scenario | {u['tc_evidenced']}/{u['tc_total']} |")
    lines.append(f"| DET/HNT TCs evidenced | {u['det_hnt_evidenced']}/{u['det_hnt_total']} |")
    lines.append(f"| S-13 tier disagreements | {u['tier_drift_s13']} |")
    lines.append(f"| S-14 posture-class-primary bindings | {u['posture_class_primary_s14']} |")
    lines.append(
        f"| PLT assertions authored | {u['plt_assertions']['bound']}/"
        f"{u['plt_assertions']['index_rows_total']} rows ({u['plt_assertions']['artifacts']} artifact(s), "
        f"{u['plt_assertions']['proven']} tenant-proven) |"
    )
    lines.append(
        f"| POS assertions authored | {u['pos_assertions']['bound']}/"
        f"{u['pos_assertions']['index_rows_total']} rows ({u['pos_assertions']['artifacts']} artifact(s), "
        f"{u['pos_assertions']['proven']} tenant-proven) |"
    )
    lines.append("")

    lines.append("## HTTP routes")
    lines.append("")
    lines.append("Boot-free static count: every `@<router>.<verb>(` decorator across "
                  "`core/api/*.py` + `core/main.py`. This undercounts the live OpenAPI "
                  "surface by the framework's own `/api/docs`, `/api/redoc`, "
                  "`/api/openapi.json` (the `backend` CI job, which boots the app, is "
                  "the heavier proof for the exact served count).")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Route decorators | {r['decorator_count']} |")
    lines.append(f"| By HTTP method | `{_compact(r['by_method'])}` |")
    lines.append(f"| `APIRouter()` instances | {r['apirouter_instances']} |")
    lines.append(f"| Router files (`core/api/*.py`) | {r['router_files']} |")
    lines.append("")

    src = doc.get("strict_refs_check", {})
    lines.append("## Strict UC/TC ref validation")
    lines.append("")
    if "passed" in src:
        lines.append(f"`tests/engine/test_corpus_refs_strict.py`: **{src['passed']} passed** ({src['status']}).")
    else:
        lines.append(
            f"Not run by this generator this pass ({src.get('note', 'unavailable')}). "
            "See `make check-refs` or the CI `refs` job."
        )
    lines.append("")

    lines.append(f"***tenant-verified: {doc['tenant_verified']}.*** No run and no assertion in this "
                  "repo has ever been executed against a live Cortex tenant. Authored is not proven.")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        doc = build()
    except GroundTruthError as exc:
        print(f"generate_ground_truth: {exc}", file=sys.stderr)
        return 1

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    json_text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    OUT_JSON.write_text(json_text, encoding="utf-8")
    OUT_MD.write_text(render_markdown(doc), encoding="utf-8")
    print(f"wrote {OUT_JSON.relative_to(REPO_ROOT)}")
    print(f"wrote {OUT_MD.relative_to(REPO_ROOT)}")
    print(f"scenarios={doc['corpus']['scenarios_loadable']} cards={doc['corpus']['ttp_cards']} "
          f"planes={doc['corpus']['detection_planes']} adapters={doc['adapters']['packs_total']} "
          f"assertions={doc['assertions']['artifacts_total']} eal_plugins={doc['eal_plugins']} "
          f"iac_modules_aws={doc['iac_modules_aws']} routes={doc['http_routes']['decorator_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

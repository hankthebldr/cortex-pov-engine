#!/usr/bin/env python3
"""PostToolUse hook — keep IaC modules formatted and catalog-loadable.

Fires on Edit/Write/MultiEdit for anything under `infra/modules/`. The IaC
surface (27 .tf files across 11 AWS modules, with the GCP/Azure ports still
ahead) has no gate anywhere: CI's five-job matrix has no terraform job, and
guard-paths.py only blocks editing `.terraform/` artifacts after the fact.

Two jobs, deliberately different in severity:

  1. FORMAT (courtesy, never blocks) — `terraform fmt` on the touched module,
     mirroring gofmt-go.py. Silently skipped when the terraform CLI is absent,
     so the hook is useful on a host without the toolchain installed.

  2. CATALOG INTEGRITY (blocks) — core/engine/infra_catalog.py builds the module
     picker by regex-matching YAML frontmatter at byte 0 of each module's
     README.md. When that match fails, `_load_module_metadata` returns None and
     the module *silently disappears* from `/api/infra/modules` and the UI — a
     logged warning, no error, no failing test. A stray blank line above the
     `---` is enough. This hook turns that silent drop into edit-time feedback.

Scaffolding-friendly: a module with no README.md yet is warned about, not
blocked (you're mid-create). A README.md that exists but is malformed blocks —
that is the regression the catalog cannot survive.

Exit codes:
  0  — outside infra/modules/, formatting applied, or integrity intact
  2  — the module would drop out of the IaC catalog; stderr is fed back
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

# Byte-identical to core/engine/infra_catalog.py — the frontmatter must match at
# position 0 or the module is invisible to the catalog.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

CATALOG_REF = "core/engine/infra_catalog.py::_load_module_metadata"


def _module_dir(rel: Path) -> tuple[Path, str, str] | None:
    """Return (module_rel_dir, provider, module) when rel is inside a module."""
    parts = rel.parts
    if len(parts) < 4 or parts[0] != "infra" or parts[1] != "modules":
        return None
    return Path(parts[0], parts[1], parts[2], parts[3]), parts[2], parts[3]


def _check_readme(mod_abs: Path, provider: str, module: str) -> tuple[list[str], list[str]]:
    """Return (blocking_errors, warnings) for the module's README frontmatter."""
    errors: list[str] = []
    warnings: list[str] = []
    readme = mod_abs / "README.md"

    if not readme.is_file():
        warnings.append(
            f"{provider}/{module} has no README.md yet — the IaC catalog skips "
            f"modules without one, so it will not appear in the module picker "
            f"until you add it (with YAML frontmatter at the very first byte)."
        )
        return errors, warnings

    try:
        text = readme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"README.md is unreadable ({exc}).")
        return errors, warnings

    match = _FRONTMATTER_RE.match(text)
    if not match:
        errors.append(
            "README.md has no YAML frontmatter at byte 0. The catalog regex is "
            "anchored (`re.match`), so a leading blank line, BOM, or heading "
            "above the opening `---` drops the module from the picker entirely. "
            "The file must START with `---`."
        )
        return errors, warnings

    try:
        fm = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        errors.append(f"README.md frontmatter is not valid YAML: {exc}")
        return errors, warnings

    if not isinstance(fm, dict):
        errors.append("README.md frontmatter must parse to a mapping.")
        return errors, warnings

    if not str(fm.get("name", "")).strip():
        errors.append(
            "frontmatter `name:` is missing/empty — the catalog falls back to the "
            "directory name, which hides typos and breaks module cross-references."
        )
    if not str(fm.get("description", "")).strip():
        errors.append(
            "frontmatter `description:` is missing/empty — it defaults to \"\" and "
            "renders as a blank card in the IaC module picker."
        )

    providers = fm.get("providers")
    if not isinstance(providers, list) or not providers:
        errors.append("frontmatter `providers:` must be a non-empty list.")
    elif provider not in providers:
        errors.append(
            f"frontmatter `providers: {providers}` does not include '{provider}', "
            f"but the module lives under infra/modules/{provider}/."
        )

    for key in ("required_params", "optional_params", "dependencies"):
        if key in fm and not isinstance(fm[key], list):
            errors.append(f"frontmatter `{key}:` must be a list when present.")

    deps = fm.get("dependencies")
    if isinstance(deps, list) and module != "base" and "base" not in deps:
        warnings.append(
            f"{provider}/{module} does not declare `dependencies: [base]`. The "
            "generator force-includes base anyway (_normalize_modules), but the "
            "declaration is what the picker shows the DC."
        )

    return errors, warnings


def _check_content_yml(mod_abs: Path) -> tuple[list[str], list[str]]:
    """content.yml is optional, but must be well-formed when present."""
    errors: list[str] = []
    warnings: list[str] = []
    path = mod_abs / "content.yml"
    if not path.is_file():
        return errors, warnings

    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        errors.append(
            f"content.yml does not parse ({exc}) — the catalog logs and returns "
            "None, so every declared tool silently vanishes from the bundle."
        )
        return errors, warnings

    tools = manifest.get("tools", {}) or {}
    if not isinstance(tools, dict):
        errors.append("content.yml `tools:` must be a mapping of category -> list.")
        return errors, warnings

    for category, entries in tools.items():
        if entries is None:
            continue
        if not isinstance(entries, list):
            errors.append(f"content.yml `tools.{category}` must be a list.")
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or not entry.get("name"):
                warnings.append(
                    f"content.yml `tools.{category}[{i}]` has no `name:` — "
                    "_flatten_content_tools drops it, so it never installs on the "
                    "jumpbox."
                )

    return errors, warnings


def _check_artifacts(mod_abs: Path, provider: str, module: str) -> list[str]:
    """Terraform state/lock artifacts inside a module pollute generated bundles."""
    warnings: list[str] = []
    if (mod_abs / ".terraform").exists():
        warnings.append(
            f"infra/modules/{provider}/{module}/.terraform/ exists — never commit "
            "it (the generator strips it, but it must not enter git)."
        )
    if (mod_abs / ".terraform.lock.hcl").exists():
        warnings.append(
            f"infra/modules/{provider}/{module}/.terraform.lock.hcl exists — "
            "never commit it (CLAUDE.md IaC rule)."
        )
    return warnings


def _terraform_fmt(mod_abs: Path, project_dir: Path) -> str | None:
    """Format the module in place. Returns a note when files were rewritten."""
    tf = shutil.which("terraform")
    if tf is None:
        return None  # no toolchain on this host — formatting is a courtesy
    proc = subprocess.run(
        [tf, "fmt", str(mod_abs)],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # An HCL parse error is the author's in-progress edit, not a hook finding.
        return None
    changed = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not changed:
        return None
    return (
        "terraform fmt rewrote " + ", ".join(changed) + " in place after this edit. "
        "The change is already on disk — re-read before further edits, and stage "
        "the formatted version."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return 0

    project_dir = Path(
        os.environ.get("CLAUDE_PROJECT_DIR", payload.get("cwd", "."))
    ).resolve()
    try:
        rel = Path(file_path).resolve().relative_to(project_dir)
    except ValueError:
        return 0  # outside the repo — not our concern

    located = _module_dir(rel)
    if located is None:
        return 0
    mod_rel, provider, module = located
    mod_abs = project_dir / mod_rel
    if not mod_abs.is_dir():
        return 0

    errors: list[str] = []
    warnings: list[str] = []

    for check in (_check_readme(mod_abs, provider, module), _check_content_yml(mod_abs)):
        errors.extend(check[0])
        warnings.extend(check[1])
    warnings.extend(_check_artifacts(mod_abs, provider, module))

    if errors:
        print(
            f"IaC module {provider}/{module} would not load into the catalog "
            f"({CATALOG_REF} returns None — a logged warning, not an error, so "
            f"nothing else catches this):\n"
            + "\n".join(f"  - {e}" for e in errors)
            + (
                "\n\nAlso noted:\n" + "\n".join(f"  - {w}" for w in warnings)
                if warnings
                else ""
            ),
            file=sys.stderr,
        )
        return 2

    notes = [n for n in (_terraform_fmt(mod_abs, project_dir),) if n]
    notes.extend(warnings)
    if notes:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": " ".join(notes),
            }
        }))

    return 0


if __name__ == "__main__":
    sys.exit(main())

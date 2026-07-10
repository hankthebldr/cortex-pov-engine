#!/usr/bin/env python3
"""PreToolUse hook — protect files that must not be hand-edited.

Enforces CortexSim's documented rules (see CLAUDE.md) at the tool boundary:
  - sources/**                 git submodules — edit upstream, never in-tree.
  - detection_scanner/exports/ generated — regenerate via export_artifacts.py.
  - **/.terraform/ and *.terraform.lock.hcl  Terraform artifacts — never commit.

Exit codes:
  0  — allowed
  2  — denied; stderr explains why (blocks the tool call, fed back to Claude)
"""
import json
import os
import sys
from pathlib import Path

PROTECTED_PREFIXES = [
    ("sources/",
     "sources/ holds git submodules — edit them in their own repos, not here "
     "(CLAUDE.md: 'do not edit source files in these directories')."),
    ("detection_scanner/exports/",
     "detection_scanner/exports/ is generated — run "
     "`python3 detection_scanner/scripts/export_artifacts.py` to regenerate it "
     "instead of editing by hand (the export-determinism CI gate compares bytes)."),
]


def _is_terraform_artifact(rel_str: str) -> bool:
    return (
        rel_str.startswith(".terraform/")
        or "/.terraform/" in f"/{rel_str}"
        or rel_str.endswith(".terraform.lock.hcl")
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
        rel_str = str(Path(file_path).resolve().relative_to(project_dir))
    except ValueError:
        return 0  # outside the repo — not our concern

    for prefix, reason in PROTECTED_PREFIXES:
        if rel_str.startswith(prefix):
            print(f"Blocked edit to {rel_str}: {reason}", file=sys.stderr)
            return 2

    if _is_terraform_artifact(rel_str):
        print(
            f"Blocked edit to {rel_str}: Terraform state/lock artifacts must never "
            "be committed (CLAUDE.md IaC rule — they pollute generated bundles).",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())

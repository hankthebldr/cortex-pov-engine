#!/usr/bin/env python3
"""PostToolUse hook — keep the Go beacon canonically formatted.

Fires on Edit/Write/MultiEdit. When the edited file is a `*.go` file inside the
repo, run `gofmt -w` on it so the `agent` CI job (build + vet + test -race) never
trips on formatting churn and diffs stay minimal. This mirrors what a Go
developer's editor-on-save would do; SimCore's beacon is stdlib-only Go, so
`gofmt` (which ships with the Go toolchain) is the only dependency.

Non-blocking by design: formatting is a courtesy, not a gate. If `gofmt` is not
on PATH, or the file does not parse yet, the hook exits 0 and stays quiet — it
never blocks an edit. When it *does* rewrite the file, it reports back so the
change is visible.

Exit codes:
  0  — not a Go file, gofmt unavailable, or formatting applied/needed nothing
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".go"):
        return 0

    project_dir = Path(
        os.environ.get("CLAUDE_PROJECT_DIR", payload.get("cwd", "."))
    ).resolve()
    try:
        target = Path(file_path).resolve()
        rel = target.relative_to(project_dir)
    except ValueError:
        return 0  # edit outside the repo — not our concern

    gofmt = shutil.which("gofmt")
    if gofmt is None:
        return 0  # no Go toolchain on the host — silently skip

    # -l lists files whose formatting would change; -w writes them in place.
    proc = subprocess.run(
        [gofmt, "-l", "-w", str(target)],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # gofmt only errors on a genuine parse failure; that is the author's
        # in-progress edit, not something to block on. Stay quiet.
        return 0

    if proc.stdout.strip():
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"gofmt reformatted {rel} in place after this edit. "
                    "The change is already on disk — re-read the file before "
                    "further edits, and stage the formatted version."
                ),
            }
        }))

    return 0


if __name__ == "__main__":
    sys.exit(main())

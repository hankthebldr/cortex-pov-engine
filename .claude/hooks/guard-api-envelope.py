#!/usr/bin/env python3
"""PostToolUse hook — enforce the structured API error envelope.

Fires on Edit/Write/MultiEdit. When the edited file is a router under
`core/api/`, parse it and confirm every `raise HTTPException(...)` carries a
`detail` shaped like the documented envelope:

    {"error": "...", "code": "...", "detail": "..."}

CLAUDE.md, "Key Design Rules": *All API responses are structured JSON —
including errors.* The convention is currently at 100% (66 dict-shaped details,
0 bare strings) and is enforced by nothing else — no CI job, no test, no linter.
FastAPI happily accepts `detail="not found"`, which serialises to
`{"detail": "not found"}` and silently breaks the console's error rendering
(the UI reads `.detail.error` / `.detail.code`). This hook is the regression
guard for that invariant. See `_not_found()` in core/api/credentials.py
(GAP-API-012) for the canonical helper.

What counts as compliant:
  - a dict literal carrying all three keys              → OK
  - a call, name, or attribute (helper/variable)        → OK, not statically
    knowable; `_not_found(...)` is the intended shape
What is flagged:
  - a string literal or f-string detail                 → bare-string envelope
  - a dict literal missing error/code/detail            → partial envelope
  - no detail at all                                    → FastAPI substitutes
    its own bare-string default

Dependency-light on purpose: stdlib `ast` only, so it runs on the host despite
the Python 3.14-vs-3.11 mismatch that keeps the real test suite in Docker.

Exit codes:
  0  — not a router file, unparseable (mid-edit), or every raise is compliant
  2  — envelope violation; stderr is fed back to Claude
"""
import ast
import json
import os
import sys
from pathlib import Path

# The load-bearing keys. The documented envelope is {error, code, detail}, but
# `detail` is de-facto optional context — 16 of the 66 raise sites in core/api/
# omit it, so requiring it would make this hook a noise generator rather than a
# regression guard. `error` and `code` are present at 100% and are what the
# console renders.
REQUIRED_KEYS = ("error", "code")

ENVELOPE_DOC = (
    'Use the documented envelope: detail={"error": "<human summary>", '
    '"code": "<UPPER_SNAKE_CODE>", "detail": "<context, e.g. run_id=abc>"} '
    "— or a helper returning it, like _not_found() in core/api/credentials.py. "
    "`error` and `code` are required; the inner `detail` is optional context."
)


def _callee_name(node: ast.AST) -> str:
    """Best-effort dotted name for a Call's func (HTTPException, fastapi.HTTPException)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _detail_arg(call: ast.Call):
    """Return the `detail` argument node, or None if absent.

    Signature is HTTPException(status_code, detail=None, headers=None), so the
    second positional is also `detail`.
    """
    for kw in call.keywords:
        if kw.arg == "detail":
            return kw.value
        if kw.arg is None:  # **kwargs splat — can't reason, treat as compliant
            return call  # sentinel: any non-None, non-literal node
    if len(call.args) >= 2:
        return call.args[1]
    return None


def _check_detail(detail, line: int) -> str | None:
    """Return a finding string, or None when the detail is compliant."""
    if detail is None:
        return (
            f"L{line}: HTTPException raised with no `detail` — FastAPI then emits "
            f'its own bare-string default (e.g. {{"detail": "Not Found"}}), which '
            f"the console cannot render. {ENVELOPE_DOC}"
        )

    if isinstance(detail, ast.Constant) and isinstance(detail.value, str):
        return (
            f"L{line}: HTTPException(detail={detail.value!r}) is a bare string — it "
            f'serialises to {{"detail": "{detail.value}"}}, not the structured '
            f"envelope. {ENVELOPE_DOC}"
        )

    if isinstance(detail, ast.JoinedStr):  # f-string
        return (
            f"L{line}: HTTPException detail is an f-string — bare-string envelope. "
            f"{ENVELOPE_DOC}"
        )

    if isinstance(detail, ast.Dict):
        keys = {
            k.value
            for k in detail.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        missing = [k for k in REQUIRED_KEYS if k not in keys]
        if missing:
            return (
                f"L{line}: HTTPException detail dict is missing "
                f"{', '.join(repr(m) for m in missing)} — the console reads "
                f"`.detail.error` and `.detail.code`. {ENVELOPE_DOC}"
            )
        return None

    # Call (_not_found(...)), Name (a prebuilt envelope), Attribute, Starred,
    # IfExp — not statically knowable. Assume the author used a helper.
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".py"):
        return 0

    project_dir = Path(
        os.environ.get("CLAUDE_PROJECT_DIR", payload.get("cwd", "."))
    ).resolve()
    try:
        target = Path(file_path).resolve()
        rel = target.relative_to(project_dir)
    except ValueError:
        return 0  # outside the repo — not our concern

    if not str(rel).startswith("core/api/"):
        return 0

    try:
        tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(rel))
    except (SyntaxError, OSError, UnicodeDecodeError):
        # Mid-edit / unreadable — the author's in-progress state, not a finding.
        return 0

    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        if _callee_name(node.exc.func) != "HTTPException":
            continue
        finding = _check_detail(_detail_arg(node.exc), node.exc.lineno)
        if finding:
            findings.append(finding)

    if not findings:
        return 0

    print(
        f"Structured-error-envelope violation in {rel} "
        f"({len(findings)} raise site{'s' if len(findings) > 1 else ''}):\n"
        + "\n".join(f"  - {f}" for f in findings)
        + "\n\nCLAUDE.md Key Design Rules: all API responses are structured JSON, "
        "including errors. Fix the raise site(s) above before moving on.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())

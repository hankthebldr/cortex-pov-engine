"""Guard: ``engine.k8s_manifest`` imports cleanly under ``-W error``.

An invalid escape such as ``"\\$"`` in a non-raw literal compiles with a
DeprecationWarning on 3.11 and a SyntaxWarning on 3.12+. The warning fires
at compile time, so an already-cached ``.pyc`` hides it; the subprocess runs
with ``-B`` and ``PYTHONDONTWRITEBYTECODE`` plus a fresh pycache prefix so the
module is always recompiled from source.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_k8s_manifest_imports_with_warnings_as_errors(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "core")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
    proc = subprocess.run(
        [sys.executable, "-B", "-W", "error", "-c", "import engine.k8s_manifest"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

"""The payload-shelf drift check runs in the ``adapters`` CI job.

``payloads/sources.json`` is GENERATED from every pack's ``install.artifact``
(``python3 -m engine.payload_shelf --write``). The hand-kept list had already
drifted once: it named an adapter that never existed. The docs said CI gated
the drift (``tools/packs/_schema.yml``: "a CI gate fails on drift") and that the
check belonged in the adapters job, not in a new one. It ran in neither, only
in-process in the pytest suite.

Also pinned: no CI step may stage the shelf (``scripts/build-payloads.sh``)
without ``PAYLOAD_ALLOW_UNPINNED=0``, because that script otherwise blesses an
artifact that carries no digest pin with whatever it downloaded. Nothing stages
the shelf in CI today, so that half is a rule for the day something does.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from tests.ci._wiring import REPO, ci_jobs, load_workflow, make_n, step_index, workflow_files


def test_adapters_job_runs_the_payload_shelf_drift_check():
    adapters = ci_jobs()["adapters"]
    i = step_index(adapters, "engine.payload_shelf --check")
    step = adapters["steps"][i]
    assert "python3 -m engine.payload_shelf --check" in step["run"]
    assert "if" not in step and "continue-on-error" not in step, "the drift check must not be skippable"
    env = {**(adapters.get("env") or {}), **(step.get("env") or {})}
    # The module lives under core/ and resolves the repo root from the same
    # variable the other adapter gates in this job use.
    assert env.get("PYTHONPATH") == "${{ github.workspace }}/core"
    assert env.get("CORTEXSIM_BASE_DIR") == "${{ github.workspace }}"
    # The check goes through the REAL pack loader (pydantic + pyyaml). The
    # install in front of it must take both from core/requirements.txt, so CI
    # validates packs with the versions the image ships.
    installs = [s.get("run") or "" for s in adapters["steps"][:i] if "pip install" in (s.get("run") or "")]
    assert any("core/requirements.txt" in r and "pydantic" in r and "pyyaml" in r for r in installs), installs


def test_the_adapters_install_line_resolves_to_real_pins():
    """`pip install $(grep ...)` with an empty grep would install nothing and
    fail on usage; with one match it would install half. Run the same grep."""
    adapters = ci_jobs()["adapters"]
    run = next(s["run"] for s in adapters["steps"] if "pip install" in (s.get("run") or ""))
    m = re.search(r"grep -E '([^']+)' core/requirements\.txt", run)
    assert m, run
    pins = [ln for ln in (REPO / "core" / "requirements.txt").read_text().splitlines() if re.search(m.group(1), ln)]
    assert sorted(p.split("==")[0] for p in pins) == ["pydantic", "pyyaml"], pins
    assert all(re.fullmatch(r"[a-z-]+==[0-9][0-9A-Za-z.]*", p) for p in pins), pins


def test_adapters_job_refuses_an_unpinned_payload_build():
    env = ci_jobs()["adapters"].get("env") or {}
    assert str(env.get("PAYLOAD_ALLOW_UNPINNED")) == "0"


def test_every_ci_use_of_build_payloads_refuses_unpinned_artifacts():
    """Wherever a workflow stages the shelf, an artifact with no digest pin must
    fail the build rather than be blessed with whatever was downloaded."""
    seen_steps = 0
    offenders = []
    for path in workflow_files():
        doc = load_workflow(path)
        wf_env = doc.get("env") or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            job_env = job.get("env") or {}
            for step in job.get("steps", []):
                seen_steps += 1
                run = step.get("run") or ""
                if "build-payloads.sh" not in run:
                    continue
                effective = {**wf_env, **job_env, **(step.get("env") or {})}
                inline = re.search(r"\bPAYLOAD_ALLOW_UNPINNED=(\S+)", run)
                value = inline.group(1).strip("'\"") if inline else str(effective.get("PAYLOAD_ALLOW_UNPINNED"))
                if value != "0":
                    offenders.append(f"{path.name}:{job_name}:{step.get('name') or run[:40]}")
    assert seen_steps > 50, f"walked only {seen_steps} steps -- the workflow walk is broken"
    assert offenders == []


@pytest.mark.parametrize("target", ["check-adapters", "validate", "ci"])
def test_make_runs_the_shelf_drift_check_the_adapters_job_runs(target):
    out = make_n(target)
    assert "-m engine.payload_shelf --check" in out, f"`make {target}` lacks the adapters job's shelf drift check"
    line = next(ln for ln in out.splitlines() if "engine.payload_shelf --check" in ln)
    assert "PYTHONPATH=" in line and "CORTEXSIM_BASE_DIR=" in line, line


def test_the_documented_shelf_check_command_goes_red_on_drift(tmp_path):
    """The step is only a gate if its command can fail. Run it exactly as the
    job does: green on the tree, red on a tampered copy of the staging list."""
    env = {**os.environ, "PYTHONPATH": str(REPO / "core"), "CORTEXSIM_BASE_DIR": str(REPO)}
    cmd = [sys.executable, "-m", "engine.payload_shelf", "--check"]

    ok = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert ok.stdout.startswith("OK "), ok.stdout

    tampered = tmp_path / "sources.json"
    text = (REPO / "payloads" / "sources.json").read_text(encoding="utf-8")
    digests = re.findall(r'"sha256": "([0-9a-f]{64})"', text)
    assert digests, "no pinned digest found to tamper with"
    tampered.write_text(text.replace(digests[0], "0" * 64, 1), encoding="utf-8")
    bad = subprocess.run([*cmd, "--sources", str(tampered)], cwd=REPO, env=env,
                         capture_output=True, text=True, timeout=120)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "is stale" in bad.stdout, bad.stdout

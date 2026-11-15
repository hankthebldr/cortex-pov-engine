"""tests/test_release_workflow_hygiene.py

Guards for .github/workflows/release.yml — the pipeline that publishes what a
DC downloads for a customer lab.

  * CI-12  least privilege. The workflow granted ``contents: write`` and
           ``packages: write`` to every job, including ``lint-shell``, which
           runs ``apt-get install``. Now: nothing at workflow scope, write
           scopes only where used.
  * CI-13  honest Windows assets. ``stage2-windows.zip`` was emitted whenever
           ``installer/stage2/common`` existed, so the release carried a
           "Windows" asset whose only content was one bash file
           (``common/annotate.sh``). A stage2 asset now ships only when its own
           OS directory exists. These tests EXECUTE the packaging step rather
           than grep it.
  * CI-06  the beacon matrix is built from the tag and attached, unsigned, and
           the notes say so.

Gate A red run: point ``CORTEXSIM_RELEASE_WORKFLOW`` at the base file
(``git show cc26ddf:.github/workflows/release.yml``) and every class above
fails. The default is the tree's own file.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_YML = Path(os.environ.get(
    "CORTEXSIM_RELEASE_WORKFLOW", REPO_ROOT / ".github" / "workflows" / "release.yml"))
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPENDABOT_YML = REPO_ROOT / ".github" / "dependabot.yml"

BEACON_MATRIX = [
    "cortexsim-agent-darwin-amd64",
    "cortexsim-agent-darwin-arm64",
    "cortexsim-agent-linux-amd64",
    "cortexsim-agent-linux-arm64",
    "cortexsim-agent-windows-amd64.exe",
]


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(RELEASE_YML.read_text(encoding="utf-8"))


def _step(job: dict, name: str) -> dict:
    hits = [s for s in job.get("steps", []) if s.get("name") == name]
    assert len(hits) == 1, f"expected exactly one step named {name!r}, got {len(hits)}"
    return hits[0]


def _runs(wf: dict):
    for job_name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            if "run" in step:
                yield job_name, step


# --------------------------------------------------------------------------
# CI-12 — least privilege
# --------------------------------------------------------------------------

#: Exactly what each job may hold. A new job must be classified here, which is
#: the point: a write scope is a decision, not a default.
EXPECTED_PERMISSIONS = {
    "resolve-tag": {"contents": "read"},
    "lint-shell": {"contents": "read"},
    "build-image": {"contents": "read", "packages": "write"},
    "build-stage2": {"contents": "read"},
    "build-agent": {"contents": "read"},
    "manifest": {"contents": "read"},
    "release": {"contents": "write"},
}


def test_workflow_scope_grants_nothing(wf):
    assert wf.get("permissions") == {}, (
        "workflow-scope permissions are inherited by every job; grant per job")


def test_each_job_holds_exactly_its_scopes(wf):
    got = {name: job.get("permissions") for name, job in wf["jobs"].items()}
    assert got == EXPECTED_PERMISSIONS


def test_the_apt_get_job_cannot_write(wf):
    lint = wf["jobs"]["lint-shell"]
    assert any("apt-get" in s.get("run", "") for s in lint["steps"])
    # A job without its own block inherits the workflow-scope grant.
    effective = lint["permissions"] if "permissions" in lint else wf.get("permissions")
    assert isinstance(effective, dict), effective
    assert "write" not in effective.values(), effective


def test_no_untrusted_expression_is_interpolated_into_a_script(wf):
    """``${{ inputs.* }}`` / ``${{ github.event.* }}`` inside ``run:`` is
    executed as shell; such values must arrive through ``env:``."""
    bad = [(job, step.get("name") or step.get("id"))
           for job, step in _runs(wf)
           if re.search(r"\$\{\{\s*(inputs|github\.event)\.", step["run"])]
    assert bad == []


# --------------------------------------------------------------------------
# CI-13 — a stage2 asset ships only for an OS directory that exists
# --------------------------------------------------------------------------

def _sandbox(tmp_path: Path, os_dirs: list[str]) -> Path:
    root = tmp_path / "ws"
    stage2 = root / "installer" / "stage2"
    shutil.copytree(REPO_ROOT / "installer" / "stage2" / "common", stage2 / "common")
    for d in os_dirs:
        (stage2 / d).mkdir(parents=True)
        (stage2 / d / "README.txt").write_text(f"{d} placeholder\n", encoding="utf-8")
    (root / "dist").mkdir()
    # A sudo that refuses: the step must never reach apt-get on a test host.
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    sudo = fakebin / "sudo"
    sudo.write_text("#!/bin/sh\necho 'sudo blocked in test' >&2\nexit 1\n", encoding="utf-8")
    sudo.chmod(sudo.stat().st_mode | stat.S_IEXEC)
    return root


def _package(wf: dict, root: Path, tmp_path: Path) -> list[str]:
    job = wf["jobs"]["build-stage2"]
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path / 'fakebin'}{os.pathsep}{env.get('PATH', '')}"
    env["GITHUB_WORKSPACE"] = str(root)
    # macOS bsdtar adds AppleDouble ._* members; the ubuntu runner's GNU tar
    # does not. Make a Mac run match what the release job produces.
    env["COPYFILE_DISABLE"] = "1"
    for name in ("Package stage2-linux.tar.gz", "Package stage2-windows.zip"):
        script = _step(job, name)["run"]
        assert "${{" not in script, f"{name} needs an expression the sandbox cannot supply"
        # The same flags GitHub's default bash shell uses.
        proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                              cwd=root, env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"{name} failed:\n{proc.stdout}\n{proc.stderr}"
    return sorted(p.name for p in (root / "dist").iterdir() if not p.name.startswith("."))


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_common_alone_emits_no_stage2_asset(wf, tmp_path):
    """Today's tree: installer/stage2/common/annotate.sh and nothing else.
    The base workflow attached stage2-windows.zip = [common/annotate.sh]."""
    root = _sandbox(tmp_path, [])
    assert _package(wf, root, tmp_path) == []


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("zip") is None,
                    reason="needs bash and zip")
def test_windows_zip_carries_only_the_windows_dir(wf, tmp_path):
    root = _sandbox(tmp_path, ["windows"])
    assert _package(wf, root, tmp_path) == ["stage2-windows.zip"]
    names = zipfile.ZipFile(root / "dist" / "stage2-windows.zip").namelist()
    assert names and all(n.startswith("windows/") for n in names), names


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("tar") is None,
                    reason="needs bash and tar")
def test_linux_tar_may_carry_common(wf, tmp_path):
    import tarfile
    root = _sandbox(tmp_path, ["linux"])
    assert _package(wf, root, tmp_path) == ["stage2-linux.tar.gz"]
    with tarfile.open(root / "dist" / "stage2-linux.tar.gz") as tf:
        tops = {m.name.split("/")[0] for m in tf.getmembers()}
    assert tops == {"common", "linux"}, tops


# --------------------------------------------------------------------------
# CI-06 — the beacon matrix ships, unsigned, and says so
# --------------------------------------------------------------------------

def _setup_go_version(job: dict) -> str:
    uses = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-go@")]
    assert len(uses) == 1, "expected exactly one actions/setup-go step"
    return str(uses[0]["with"]["go-version"])


def test_build_agent_uses_ci_agent_go_version(wf):
    ci = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert _setup_go_version(wf["jobs"]["build-agent"]) == _setup_go_version(ci["jobs"]["agent"])


def test_build_agent_runs_the_shared_script_and_asserts_the_matrix(wf):
    job = wf["jobs"]["build-agent"]
    scripts = "\n".join(s.get("run", "") for s in job["steps"])
    assert "scripts/build-agent-dist.sh" in scripts
    assert "go version -m" in scripts
    for name in BEACON_MATRIX:
        assert name in scripts, f"matrix assertion does not name {name}"
    upload = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert upload and upload[0]["with"].get("if-no-files-found") == "error"


def test_beacons_reach_the_manifest_and_the_release(wf):
    assert "build-agent" in wf["jobs"]["manifest"]["needs"]
    assert "build-agent" in wf["jobs"]["release"]["needs"]
    manifest_py = _step(wf["jobs"]["manifest"], "Generate manifest.json")["run"]
    assert '"signed": False' in manifest_py
    assert 'manifest["agent"]' in manifest_py


def test_release_notes_say_the_beacons_are_unsigned(wf):
    notes = _step(wf["jobs"]["release"], "Generate release notes")["run"]
    assert "unsigned" in notes and "not code-signed" in notes
    assert "did not execute them on Windows" in notes


# --------------------------------------------------------------------------
# Dependabot tracks the actions so they can be moved to SHA pins
# --------------------------------------------------------------------------

def test_dependabot_tracks_github_actions():
    assert DEPENDABOT_YML.is_file(), "missing .github/dependabot.yml"
    cfg = yaml.safe_load(DEPENDABOT_YML.read_text(encoding="utf-8"))
    assert cfg.get("version") == 2
    gha = [u for u in cfg.get("updates", []) if u.get("package-ecosystem") == "github-actions"]
    assert len(gha) == 1, cfg
    assert gha[0].get("directory") == "/"
    assert gha[0].get("schedule", {}).get("interval") == "weekly"
    # Topic work lands on dev; main only advances by a merge from dev.
    assert gha[0].get("target-branch") == "dev"

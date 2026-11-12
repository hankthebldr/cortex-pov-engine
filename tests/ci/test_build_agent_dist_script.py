"""Behaviour of ``scripts/build-agent-dist.sh``, executed with a stub toolchain.

The script fills the shelf ``GET /api/agents/binary`` serves on a host-run
SimCore, and the release workflow publishes what it writes. Two things about it
were only ever assumed:

* It wrote into ``OUT_DIR`` in place and then summed the ``cortexsim-agent-*``
  glob. A binary from a target that was later dropped from ``TARGETS`` stayed on
  the shelf and in ``SHA256SUMS`` for ever, still served, never rebuilt (CI-16).
* A build that failed half way left a shelf holding a mix of new and old
  binaries under one ``SHA256SUMS`` from the previous run.

* The build runs from inside ``agent/``, so a relative ``OUT_DIR`` was resolved
  there for the binaries and against the caller's directory for the sums.

These tests run the real script with a fake ``go`` first on PATH, so they need
no Go toolchain and compile nothing. The script is copied into a throwaway
``<tmp>/repo/scripts`` beside an empty ``<tmp>/repo/agent`` (it finds the repo
from its own location), so even a regressed script cannot write into the real
tree. They prove what the script does with its outputs, not that a beacon
builds: the ``agent`` CI job and ``make test-agent-cross`` are where the beacon
is compiled.

Gate A red run: point ``CORTEXSIM_BUILD_AGENT_DIST`` at the base file
(``git show c852eac:scripts/build-agent-dist.sh``) and the three tests named
above fail. The default is the tree's own file.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = Path(os.environ.get("CORTEXSIM_BUILD_AGENT_DIST", REPO / "scripts" / "build-agent-dist.sh"))

SHIPPED = [
    "cortexsim-agent-darwin-amd64",
    "cortexsim-agent-darwin-arm64",
    "cortexsim-agent-linux-amd64",
    "cortexsim-agent-linux-arm64",
    "cortexsim-agent-windows-amd64.exe",
]

# A stand-in for `go`: `go version` prints a version; `go build ... -o <path> .`
# writes a file whose bytes name the target, so a test can tell a fresh artifact
# from a stale one. FAKE_GO_FAIL_ON=<goos>/<goarch> makes that one target fail.
FAKE_GO = r"""#!/usr/bin/env bash
set -eu
if [ "${1:-}" = "version" ]; then echo "go version go0.0-fake test/test"; exit 0; fi
if [ "${1:-}" != "build" ]; then echo "fake go: unexpected subcommand: $*" >&2; exit 64; fi
printf '%s\n' "$*" >> "$FAKE_GO_LOG"
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
[ -n "$out" ] || { echo "fake go: no -o" >&2; exit 64; }
if [ "${FAKE_GO_FAIL_ON:-}" = "$GOOS/$GOARCH" ]; then echo "fake go: forced failure for $GOOS/$GOARCH" >&2; exit 1; fi
printf 'fresh %s/%s cgo=%s\n' "$GOOS" "$GOARCH" "${CGO_ENABLED:-unset}" > "$out"
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def run_script(tmp_path):
    assert SCRIPT.is_file(), f"script under test is missing: {SCRIPT}"
    sandbox = tmp_path / "repo"
    (sandbox / "scripts").mkdir(parents=True)
    (sandbox / "agent").mkdir()
    script = sandbox / "scripts" / "build-agent-dist.sh"
    shutil.copyfile(SCRIPT, script)

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    go = fakebin / "go"
    go.write_text(FAKE_GO, encoding="utf-8")
    go.chmod(go.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "go.log"
    log.write_text("", encoding="utf-8")

    def _run(out_dir: Path | str, cwd: Path | None = None, **extra_env: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GOOS", "GOARCH", "GOFLAGS", "CGO_"))}
        env.update({
            "PATH": f"{fakebin}{os.pathsep}{env.get('PATH', '')}",
            "OUT_DIR": str(out_dir),
            "FAKE_GO_LOG": str(log),
            **extra_env,
        })
        return subprocess.run(["bash", str(script)], cwd=cwd or sandbox, env=env,
                              capture_output=True, text=True, timeout=120)

    _run.log = log  # type: ignore[attr-defined]
    _run.sandbox = sandbox  # type: ignore[attr-defined]
    return _run


def _sums(out_dir: Path) -> dict[str, str]:
    rows = {}
    for line in (out_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split()
        rows[name.lstrip("*")] = digest
    return rows


def _listing(out_dir: Path) -> list[str]:
    return sorted(p.name for p in out_dir.iterdir())


def test_a_clean_run_writes_exactly_the_shipped_matrix(run_script, tmp_path):
    out = tmp_path / "dist"
    proc = run_script(out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Nothing but the five beacons and their sums: no staging directory, no
    # partial file left behind for the binary endpoint to trip over.
    assert _listing(out) == sorted(SHIPPED + ["SHA256SUMS"])
    sums = _sums(out)
    assert sorted(sums) == sorted(SHIPPED)
    for name, digest in sums.items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest, name
    # The windows artifact carries the .exe suffix the binary endpoint keys on.
    assert (out / "cortexsim-agent-windows-amd64.exe").read_text() == "fresh windows/amd64 cgo=0\n"


def test_a_binary_from_a_dropped_target_does_not_stay_on_the_shelf(run_script, tmp_path):
    """The failure this file exists for: a target removed from TARGETS kept
    being served, with a digest, from a dev box's stale agent-dist."""
    out = tmp_path / "dist"
    out.mkdir()
    (out / "cortexsim-agent-plan9-amd64").write_text("stale: built for a target no longer shipped\n")
    (out / "cortexsim-agent-linux-amd64").write_text("stale: an older build of a current target\n")
    (out / "SHA256SUMS").write_text("0" * 64 + "  cortexsim-agent-plan9-amd64\n")
    (out / "NOTES.txt").write_text("operator's own file\n")

    proc = run_script(out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (out / "cortexsim-agent-plan9-amd64").exists(), (
        "a beacon for a target the script no longer builds is still on the shelf")
    assert "plan9" not in (out / "SHA256SUMS").read_text()
    assert sorted(_sums(out)) == sorted(SHIPPED)
    assert (out / "cortexsim-agent-linux-amd64").read_text() == "fresh linux/amd64 cgo=0\n"
    # Only its own artifacts are replaced: anything else in OUT_DIR is not the
    # script's to delete.
    assert (out / "NOTES.txt").read_text() == "operator's own file\n"
    assert _listing(out) == sorted(SHIPPED + ["SHA256SUMS", "NOTES.txt"])


def test_a_failed_build_leaves_the_previous_shelf_untouched(run_script, tmp_path):
    """Half a matrix under the previous run's SHA256SUMS is worse than either
    whole one: `sha256sum -c` fails on files that are in fact freshly built."""
    out = tmp_path / "dist"
    first = run_script(out)
    assert first.returncode == 0, first.stdout + first.stderr
    for name in SHIPPED:
        (out / name).write_text(f"previous good build of {name}\n")
    before = {p.name: p.read_bytes() for p in out.iterdir()}

    proc = run_script(out, FAKE_GO_FAIL_ON="darwin/arm64")
    assert proc.returncode != 0, "a failed target must fail the script"
    after = {p.name: p.read_bytes() for p in out.iterdir()}
    assert after == before, (
        "a failed build changed the shelf: "
        f"added={sorted(set(after) - set(before))} removed={sorted(set(before) - set(after))} "
        f"rewritten={sorted(n for n in before if n in after and before[n] != after[n])}")


def test_a_relative_out_dir_is_anchored_to_where_the_caller_stood(run_script, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    proc = run_script("dist", cwd=work)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _listing(work / "dist") == sorted(SHIPPED + ["SHA256SUMS"])
    assert not (run_script.sandbox / "agent" / "dist").exists(), (
        "the build runs from inside agent/ and a relative OUT_DIR followed it there")


def test_every_target_is_built_with_the_shipped_flags(run_script, tmp_path):
    out = tmp_path / "dist"
    proc = run_script(out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = [ln for ln in run_script.log.read_text().splitlines() if ln.strip()]
    assert len(calls) == len(SHIPPED), calls
    for call in calls:
        assert "-trimpath" in call and "-ldflags=-s -w" in call, call
    # CGO_ENABLED=0 reached the toolchain for every target (the stub records it).
    for name in SHIPPED:
        assert (out / name).read_text().endswith("cgo=0\n"), name

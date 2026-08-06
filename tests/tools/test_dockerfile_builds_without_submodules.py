"""A normal `git clone` must still be able to build the image.

The rust-builder stage originally COPYed named files out of the submodule trees
(`COPY sources/signalbench/Cargo.toml ...`). Docker fails the whole build when a
COPY source file is missing, and a submodule that was never initialised leaves
an EMPTY DIRECTORY behind — so the image could not be built at all from a tree
cloned without --recursive.

That is a real regression for any new contributor, and it is what broke the e2e
CI job, which checks out with submodules disabled on purpose. It stayed
invisible because every other job that builds the image checks out
`submodules: recursive`.

The fix is structural: COPY the DIRECTORY (copying an empty directory succeeds),
then guard each build on its Cargo.toml. These tests pin that shape — they are
static because building two images takes ~20 minutes, and the real proof was
done by building both.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO_ROOT / "core" / "Dockerfile").read_text()
RUST_TOOLS = ("ackbarx", "signalbench", "xdrtop")


@pytest.mark.parametrize("tool", RUST_TOOLS)
def test_no_named_file_is_copied_out_of_a_submodule(tool):
    """A missing FILE fails the build; a missing DIRECTORY does not."""
    named = re.findall(rf"^COPY sources/{tool}/(\S+)\s", DOCKERFILE, re.M)
    offenders = [n for n in named if not n.endswith("/")]
    assert not offenders, (
        f"core/Dockerfile COPYs named files out of sources/{tool}: {offenders}. "
        f"Docker fails the entire build when one is absent, so a clone without "
        f"--recursive could not build the image. COPY the directory instead and "
        f"guard the RUN on its Cargo.toml."
    )


@pytest.mark.parametrize("tool", RUST_TOOLS)
def test_each_tool_build_is_guarded_on_its_cargo_toml(tool):
    """The guard is what turns 'absent' into a skip rather than a failure."""
    assert f"SKIP {tool}: submodule not checked out" in DOCKERFILE, (
        f"the {tool} build stage has no not-checked-out guard, so an "
        f"uninitialised submodule fails the image build"
    )


def test_an_empty_build_writes_an_honest_manifest():
    """total: 0 must be reported as not_built, never as a successful empty shelf.

    An empty shelf that looks like a completed build is the shape this repo
    keeps finding: /api/tools/binaries would return 200 with total 0 and nothing
    would say WHY.
    """
    assert '"provenance": "not_built"' in DOCKERFILE
    assert "not_built_reason" in DOCKERFILE
    assert "git submodule update --init --recursive" in DOCKERFILE, (
        "the empty-build message must name the command that fixes it"
    )


def test_the_static_linkage_gate_is_not_skipped_when_tools_exist():
    """The early-exit for an empty /out must not disarm the readelf gate.

    If the guard were written so that ANY path exits 0, a dynamically linked
    binary could ship — which is the portability failure the stage exists to
    remove.
    """
    assert "FATAL: $f has a PT_INTERP segment" in DOCKERFILE
    assert "FATAL: $f declares DT_NEEDED shared libraries" in DOCKERFILE
    # The empty-shelf early exit must be conditional on there being NO tools.
    assert "if ! ls /out/cortexsim-tool-*-linux-amd64" in DOCKERFILE


def test_dockerignore_keeps_host_build_artifacts_out_of_the_context():
    """Directory-level COPY only works because target/ is excluded.

    A host `cargo build` leaves ~300 MB of target/ per tool. Without this the
    directory COPY would drag it into every image build.
    """
    ignore = (REPO_ROOT / ".dockerignore")
    assert ignore.is_file(), (
        ".dockerignore is gone — the rust-builder's directory COPY would now "
        "pull host-built target/ trees into the build context"
    )
    body = ignore.read_text()
    assert "sources/*/target/" in body
    assert "rust-dist/" in body, (
        "a host rust-dist/ must not be inherited by the image; it may be a "
        "different architecture or an older submodule commit"
    )

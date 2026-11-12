"""Every beacon target the shelf ships is compiled and vetted before it ships (CI-17).

``scripts/build-agent-dist.sh`` TARGETS is what SimCore serves and what a release
publishes: five GOOS/GOARCH pairs. The cross-compile gates covered one
architecture per OS family:

* the ``agent`` CI job built windows/amd64 and darwin/arm64, so linux/arm64 and
  darwin/amd64 were compiled for the first time inside the image build, with no
  ``go vet`` at all;
* ``make test-agent-cross`` set GOOS only, so on an arm64 Mac it compiled
  windows/arm64 (not shipped) and never windows/amd64 (the only Windows beacon
  there is).

Both now name the shipped pairs, and these tests hold them equal to TARGETS so a
sixth target cannot ship uncompiled. Compiling for a target is not executing on
it: nothing here says a beacon runs on Windows.
"""
from __future__ import annotations

import re

from tests.ci._wiring import ci_jobs, make_n, shipped_targets


def test_agent_job_cross_compiles_every_target_the_shelf_ships():
    agent = ci_jobs()["agent"]
    assert agent["runs-on"] == "ubuntu-22.04"  # x86-64: linux/amd64 is the native build + test
    cross = set()
    for step in agent["steps"]:
        env = step.get("env") or {}
        if "GOOS" not in env:
            continue
        assert str(env.get("CGO_ENABLED")) == "0", step.get("name")
        assert "go build ./..." in step["run"] and "go vet ./..." in step["run"], step.get("name")
        assert "if" not in step and "continue-on-error" not in step, step.get("name")
        cross.add((str(env["GOOS"]), str(env["GOARCH"])))
    shipped = shipped_targets()
    assert cross | {("linux", "amd64")} == shipped, (
        f"shipped but never compiled in CI: {sorted(shipped - cross - {('linux', 'amd64')})}; "
        f"compiled but not shipped: {sorted(cross - shipped)}")


def test_make_test_agent_cross_builds_the_shipped_matrix_not_the_host_arch():
    out = make_n("test-agent-cross")
    m = re.search(r"for t in (.*?); do", out)
    assert m, f"no `for t in <targets>; do` loop in test-agent-cross:\n{out}"
    listed = {tuple(t.split("/")) for t in m.group(1).split()}
    assert listed == shipped_targets(), (
        f"test-agent-cross builds {sorted(listed)}; the shelf ships {sorted(shipped_targets())}")
    # Both halves of the pair reach the toolchain, for build AND for vet.
    assert out.count("GOOS=$goos GOARCH=$goarch") == 2, out
    assert out.count("CGO_ENABLED=0") == 2, out

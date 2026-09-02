"""The two things that fill the shelf `GET /api/agents/binary` serves must agree.

`scripts/build-agent-dist.sh` fills `./agent-dist/` for a host-run dev SimCore.
The `agent-builder` stage in `core/Dockerfile` fills `/app/agent-dist` for every
DEPLOYED image. They are supposed to be the same matrix, and the Dockerfile
comment says so.

They drifted. The script gained `windows/amd64` when the executor's build-tag
split landed; the Dockerfile loop did not. Every shipped image therefore served
4 targets, `?os=windows` returned a 404 whose remediation text told the DC to
rebuild the image — the one action that provably could not fix it — and the
PowerShell installer refused with WINDOWS_AGENT_UNAVAILABLE before enrolling,
stranding the 71 scenarios that declare `platforms: [windows]`.

Nothing caught it for the length of a release, because three gates each proved a
different thing and none proved the shipped artifact: CI's `agent` job proves the
beacon COMPILES for windows, `make agent-dist` proves the SCRIPT emits it, and
the installer e2e fixture plants its own stub shelf. No test ever looked inside
the image.

This is the cheap static half of the fix — it parses the real files, so it runs
anywhere with no Docker. The other half is `make check-agent-shelf`, which
asserts against a built image's actual `/app/agent-dist` and cannot be fooled by
source-text drift. Static test = fast local signal; image check = truth.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-agent-dist.sh"
DOCKERFILE = REPO_ROOT / "core" / "Dockerfile"

# A target may be dropped from BOTH files and parity would still hold, so parity
# alone is satisfiable by regression. This floor is the load-bearing assertion:
# it is the set a DC is entitled to receive from `GET /api/agents/binary`.
REQUIRED_TARGETS = {
    "linux/amd64",
    "linux/arm64",
    "darwin/amd64",
    "darwin/arm64",
    "windows/amd64",
}


def _script_targets() -> set[str]:
    """Collect the quoted "<goos>/<goarch>" entries in the TARGETS=( ... ) array."""
    text = BUILD_SCRIPT.read_text()
    m = re.search(r"^TARGETS=\((.*?)^\)", text, re.MULTILINE | re.DOTALL)
    assert m, f"could not locate a TARGETS=( ... ) array in {BUILD_SCRIPT}"
    return set(re.findall(r'"([a-z0-9]+/[a-z0-9]+)"', m.group(1)))


def _agent_builder_stage() -> str:
    """The text of the `FROM ... AS agent-builder` stage, up to the next FROM."""
    text = DOCKERFILE.read_text()
    m = re.search(
        r"^FROM\s+(?:--\S+\s+)*\S+\s+AS\s+agent-builder\b(.*?)(?=^FROM\s|\Z)",
        text,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    assert m, f"could not locate a `FROM ... AS agent-builder` stage in {DOCKERFILE}"
    return m.group(1)


def _dockerfile_targets(stage: str) -> set[str]:
    """Split the cross-compile loop's `for t in <targets>; do` list on whitespace."""
    m = re.search(r"for\s+t\s+in\s+(.*?);\s*do", stage, re.DOTALL)
    assert m, "could not locate the `for t in ...; do` cross-compile loop in the agent-builder stage"
    # The loop body is a line-continued shell fragment; strip the backslashes.
    raw = m.group(1).replace("\\", " ")
    return set(re.findall(r"[a-z0-9]+/[a-z0-9]+", raw))


def test_parsers_find_something_at_all():
    """Fail closed.

    A future refactor (TARGETS moved to a file, the Dockerfile templated, a
    switch to `buildx --platform`) breaks these regexes. A parser that silently
    matches nothing would compare two empty sets and report a green parity gate
    — exactly the failure mode this file exists to prevent. Assert non-empty
    BEFORE comparing, so an unparseable file fails loudly instead.
    """
    script = _script_targets()
    docker = _dockerfile_targets(_agent_builder_stage())
    assert script, f"parsed zero targets out of {BUILD_SCRIPT} — the parser is stale, not the matrix"
    assert docker, f"parsed zero targets out of the agent-builder stage in {DOCKERFILE} — the parser is stale, not the matrix"


def test_dockerfile_and_script_build_the_same_matrix():
    script = _script_targets()
    docker = _dockerfile_targets(_agent_builder_stage())
    assert script and docker, "parser returned nothing; see test_parsers_find_something_at_all"

    script_only = sorted(script - docker)
    docker_only = sorted(docker - script)
    assert not script_only and not docker_only, (
        "agent-dist matrix drift between scripts/build-agent-dist.sh and the "
        "core/Dockerfile agent-builder stage.\n"
        f"  in the script but NOT the Dockerfile: {script_only or 'none'}\n"
        f"  in the Dockerfile but NOT the script: {docker_only or 'none'}\n"
        "Targets in the script but not the Dockerfile never reach a deployed "
        "image: GET /api/agents/binary will 404 for them and the installer will "
        "refuse, while `make agent-dist` on a dev box looks perfect."
    )


@pytest.mark.parametrize("target", sorted(REQUIRED_TARGETS))
def test_required_target_is_in_both_matrices(target):
    """Parity can be restored by DELETING a target. This says which ones may not go."""
    assert target in _script_targets(), (
        f"{target} is missing from scripts/build-agent-dist.sh TARGETS"
    )
    assert target in _dockerfile_targets(_agent_builder_stage()), (
        f"{target} is missing from the core/Dockerfile agent-builder loop — "
        "a deployed image would not serve it"
    )


def test_windows_output_path_carries_an_exe_suffix():
    """Without the suffix the file lands extensionless and is invisible to the API.

    `core/api/agents.py::_binary_name()` appends ".exe" for GOOS=windows when it
    resolves a request to a path on the shelf. A Dockerfile that builds
    windows/amd64 to an extensionless name therefore produces a binary that
    exists on disk and still 404s — a failure indistinguishable, from the DC's
    side, from never having been built at all.
    """
    stage = _agent_builder_stage()
    if not any(t.startswith("windows/") for t in _dockerfile_targets(stage)):
        pytest.skip("no windows target in the Dockerfile matrix")

    m = re.search(r"-o\s+\"([^\"]+)\"", stage)
    assert m, "could not locate the `go build -o \"...\"` output expression"
    out_expr = m.group(1)
    assert "${ext}" in out_expr or ".exe" in out_expr, (
        f"the agent-builder output path is {out_expr!r}, which has no .exe "
        "expansion — the windows beacon would be written extensionless and "
        "_binary_name() would never find it (silent 404)"
    )
    assert re.search(r'ext=""\s*;\s*\[\s*"\$goos"\s*=\s*"windows"\s*\]\s*&&\s*ext="\.exe"', stage), (
        "the output path references ${ext} but nothing sets it to .exe for "
        "GOOS=windows; the suffix would expand to empty"
    )

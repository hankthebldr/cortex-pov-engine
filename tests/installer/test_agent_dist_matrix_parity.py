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


# ---------------------------------------------------------------------------
# CI-16 — the two producers must build the SAME WAY, not only the same targets
# ---------------------------------------------------------------------------
#
# Target parity above is satisfiable while the two producers diverge on HOW
# they build: add CGO or a build tag to one and it ships bytes the other never
# produces, with every test in this file still green. The flags matched by
# inspection; nothing asserted it.

MAKEFILE = REPO_ROOT / "Makefile"
WINDOWS_DRIVER = REPO_ROOT / "scripts" / "ci" / "windows-native-e2e.ps1"

_GO_BUILD = re.compile(
    r"(?P<env>(?:[A-Z][A-Z0-9_]*=\S+\s+)+)go\s+build\s+(?P<flags>.*?)\s+-o\s+\"[^\"]+\"\s+\.",
    re.DOTALL,
)


def _build_invocation(text: str, where: str) -> tuple[dict[str, str], list[str]]:
    """(environment, flags) of the one `VAR=... go build <flags> -o "<out>" .` call."""
    import shlex

    joined = re.sub(r"\\\n", " ", text)  # fold shell line continuations
    hits = list(_GO_BUILD.finditer(joined))
    assert len(hits) == 1, f"expected exactly one `go build ... -o` invocation in {where}, found {len(hits)}"
    env = dict(tok.split("=", 1) for tok in shlex.split(hits[0]["env"]))
    flags = shlex.split(hits[0]["flags"])
    return env, flags


def _shipped_names() -> set[str]:
    names = set()
    for target in _script_targets():
        goos, goarch = target.split("/")
        names.add(f"cortexsim-agent-{goos}-{goarch}" + (".exe" if goos == "windows" else ""))
    return names


def test_both_producers_use_the_identical_build_invocation():
    s_env, s_flags = _build_invocation(BUILD_SCRIPT.read_text(), "scripts/build-agent-dist.sh")
    d_env, d_flags = _build_invocation(_agent_builder_stage(), "the core/Dockerfile agent-builder stage")
    assert s_env == d_env, (
        f"build environment differs between the producers:\n  script:     {s_env}\n  Dockerfile: {d_env}")
    assert s_flags == d_flags, (
        f"go build flags differ between the producers:\n  script:     {s_flags}\n  Dockerfile: {d_flags}\n"
        "A flag in one producer only ships beacon bytes the other never builds.")


def test_the_shared_invocation_is_the_static_stripped_one():
    """Parity is satisfiable by weakening BOTH. This is the floor the DC relies
    on: a fully static binary (no libc to smuggle past change control) built
    independently of the checkout path."""
    env, flags = _build_invocation(BUILD_SCRIPT.read_text(), "scripts/build-agent-dist.sh")
    assert env.get("CGO_ENABLED") == "0", env
    assert env.get("GOOS") == "$goos" and env.get("GOARCH") == "$goarch", env
    assert "-trimpath" in flags, flags
    assert "-ldflags=-s -w" in flags, flags


def test_a_flag_in_one_producer_only_is_caught():
    """The guard above, shown going red. Same parser, one producer edited."""
    script = BUILD_SCRIPT.read_text()
    assert "go build -trimpath" in script
    drifted = script.replace("go build -trimpath", "go build -tags netgo -trimpath", 1)
    _, base_flags = _build_invocation(script, "script")
    _, drift_flags = _build_invocation(drifted, "script with an extra flag")
    assert drift_flags != base_flags and drift_flags[:2] == ["-tags", "netgo"]
    cgo = script.replace("CGO_ENABLED=0 GOOS=", "CGO_ENABLED=1 GOOS=", 1)
    assert _build_invocation(cgo, "script with cgo")[0]["CGO_ENABLED"] == "1"


def test_the_script_does_not_disable_vcs_stamping_the_release_reads_back():
    """A decision pinned, not an accident. A host or release build stamps
    vcs.revision / vcs.time / vcs.modified into each beacon; the image stage
    COPYs only agent/ (no .git) and cannot. So the same commit and toolchain
    give DIFFERENT bytes from the two producers. That is harmless because
    nothing pins a digest across them, and release.yml's provenance step reads
    the stamp back from the released bytes (`go version -m`). Passing
    -buildvcs=false here would buy byte parity nobody consumes at the cost of
    the only commit identity an unsigned release beacon carries. Change both
    together or neither."""
    script = BUILD_SCRIPT.read_text()
    code = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    assert "buildvcs" not in code, "build-agent-dist.sh disables VCS stamping; release provenance depends on it"
    release = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "go version -m" in release and "vcs.revision" in release, (
        "release.yml no longer reads the VCS stamp back; revisit this pin with it")
    stage = _agent_builder_stage()
    copies = re.findall(r"^COPY\s+(.*)$", stage, re.MULTILINE)
    assert copies == ["agent/ ./agent/"], (
        f"the agent-builder stage now copies {copies}: if it can see .git it starts stamping too, "
        "and the script header's account of host-vs-image bytes is out of date")
    # The script's own header must say so. It used to claim that the same
    # toolchain over the same source yields byte-identical binaries, full stop.
    header = "\n".join(ln for ln in script.splitlines() if ln.startswith("#"))
    assert "vcs.revision" in header and "vcs.modified" in header, (
        "the header's reproducibility note does not name the VCS stamp as an input")


def test_the_windows_job_and_the_image_gate_name_the_same_matrix():
    """Two more hand-kept copies of the matrix: the windows job's DistTargets
    (what it expects the shipped recipe to have produced) and the loop in
    `make check-agent-shelf` (what a built image must serve). The driver's
    comment already said this file kept it in step; nothing here did."""
    shipped = _shipped_names()

    driver = WINDOWS_DRIVER.read_text(encoding="ascii")
    m = re.search(r"^\$script:DistTargets = @\((.*?)^\)", driver, re.MULTILINE | re.DOTALL)
    assert m, "windows-native-e2e.ps1 no longer declares $script:DistTargets"
    assert set(re.findall(r"'([^']+)'", m.group(1))) == shipped

    makefile = MAKEFILE.read_text()
    m = re.search(r"^check-agent-shelf:.*?for t in (.*?); do", makefile, re.MULTILINE | re.DOTALL)
    assert m, "could not locate the `for t in ...; do` loop in `make check-agent-shelf`"
    assert {f"cortexsim-agent-{t}" for t in m.group(1).split()} == shipped

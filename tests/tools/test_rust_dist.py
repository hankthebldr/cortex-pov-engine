"""Guards on the Rust tool distribution pipeline (scripts/build-rust-dist.sh).

WHY these tests exist
---------------------
`sources/{signalbench,ackbarx,xdrtop}` are tier-2 git **submodules**. Their only
distribution story used to be ``cargo build --release`` **on the customer
jumpbox** — a Rust toolchain plus crates.io egress, i.e. exactly what a
default-deny enterprise network blocks. `scripts/build-rust-dist.sh` replaces
that with a static-musl binary a DC builds once and SimCore serves.

Two of the invariants below encode defects that were live in this repo and that
nothing caught, because nothing cheap ever looked:

1. **signalbench has never been buildable with its declared recipe.**
   ``src/techniques/software.rs`` does
   ``include_bytes!("../../embedded_binaries/pacemaker_helper")`` and
   ``embedded_binaries/`` is *not in the upstream git tree* — it is produced by
   building the separate ``helpers/pacemaker`` crate first. So
   ``STATIC_TOOL_REGISTRY["signalbench"]["build_cmd"] == "cargo build --release"``
   could not ever have worked. `test_signalbench_recipe_builds_the_pacemaker_helper_first`
   is the guard.

2. **xdrtop links OpenSSL dynamically unless told not to.** Its ``Cargo.toml``
   has ``reqwest = { version = "0.12", features = ["json"] }`` with no
   ``default-features = false``, so it resolves ``default-tls -> native-tls ->
   openssl-sys`` and the binary declares ``libssl.so.3``/``libcrypto.so.3``.
   That dies on any host without OpenSSL 3 — the precise portability failure the
   whole pipeline exists to remove. The clean fix (rustls) would mean editing a
   submodule, which is forbidden, so the build passes ``OPENSSL_STATIC=1``.
   `test_xdrtop_recipe_forces_static_openssl` is the guard.

These are text assertions on the script rather than build assertions, on
purpose: a full Rust build is ~4 minutes and belongs in the paths:-filtered
`rust-dist` CI job. What must never regress *silently* is the recipe, and that
is cheap to check on every run of the backend suite.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-rust-dist.sh"
REGISTRY = REPO_ROOT / "core" / "tools" / "registry.py"
DIST_DIR = REPO_ROOT / "rust-dist"
MANIFEST = DIST_DIR / "MANIFEST.json"

# The tools the pipeline claims to cover. Kept here rather than imported so a
# drift in either direction is a test failure rather than a silent agreement.
RUST_TOOLS = ("signalbench", "ackbarx", "xdrtop")


@pytest.fixture(scope="module")
def script_text() -> str:
    assert BUILD_SCRIPT.is_file(), f"missing {BUILD_SCRIPT}"
    return BUILD_SCRIPT.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The script itself
# ---------------------------------------------------------------------------


def test_build_script_is_executable() -> None:
    """A build script the Makefile invokes directly must carry its own +x."""
    assert BUILD_SCRIPT.is_file(), f"missing {BUILD_SCRIPT}"
    assert BUILD_SCRIPT.stat().st_mode & 0o111, (
        f"{BUILD_SCRIPT.relative_to(REPO_ROOT)} is not executable — "
        "`make rust-dist` invokes it directly."
    )


def test_build_script_is_syntactically_valid() -> None:
    """`bash -n` catches the class of typo that only shows up 4 minutes in."""
    proc = subprocess.run(
        ["bash", "-n", str(BUILD_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"bash -n failed:\n{proc.stderr}"


@pytest.mark.parametrize("tool", RUST_TOOLS)
def test_every_rust_tool_has_a_recipe(script_text: str, tool: str) -> None:
    assert tool in script_text, (
        f"{tool} has no recipe in build-rust-dist.sh, so the only way to get it "
        "onto a jumpbox is a host cargo build (rustup + crates.io egress)."
    )


def test_signalbench_recipe_builds_the_pacemaker_helper_first(script_text: str) -> None:
    """Phase 1 is not optional — see this module's docstring, defect 1.

    If someone 'simplifies' the recipe back to a bare ``cargo build --release``
    the build fails with ``couldn't read embedded_binaries/pacemaker_helper``,
    a message that names neither the helper crate nor the fix.
    """
    assert "helpers/pacemaker" in script_text, (
        "signalbench's recipe no longer builds helpers/pacemaker. "
        "src/techniques/software.rs embeds its output via include_bytes! and "
        "embedded_binaries/ is not in the upstream git tree, so the main crate "
        "cannot compile without it."
    )
    assert "embedded_binaries" in script_text, (
        "signalbench's recipe no longer copies the built helper into "
        "embedded_binaries/ — the include_bytes! target."
    )


def test_xdrtop_recipe_forces_static_openssl(script_text: str) -> None:
    """See this module's docstring, defect 2."""
    assert "OPENSSL_STATIC=1" in script_text, (
        "xdrtop's recipe dropped OPENSSL_STATIC=1. reqwest 0.12's default-tls "
        "pulls openssl-sys, so without it the binary dynamically links "
        "libssl.so.3 and dies on any host lacking OpenSSL 3."
    )
    assert "OPENSSL_NO_VENDOR=1" in script_text, (
        "OPENSSL_NO_VENDOR=1 stops openssl-sys attempting a vendored build, "
        "which would need a Cargo.toml feature this repo may not edit "
        "(sources/ are submodules)."
    )


def test_static_linkage_is_required_by_the_build(script_text: str) -> None:
    """A dynamically linked artifact must never reach the shelf.

    An alpine base bump that moves OpenSSL, or an openssl-sys release that
    changes the env-var contract, would otherwise silently revert xdrtop to a
    dynamic link *without failing the build* — reintroducing the exact
    portability failure this pipeline removes.
    """
    assert "NEEDED" in script_text, (
        "the build no longer asserts the absence of DT_NEEDED entries, so a "
        "silent revert to dynamic linking would ship."
    )


def test_binaries_are_executed_before_publication(script_text: str) -> None:
    """A binary that exists is not a binary that runs.

    This repo's recurring failure mode is green-while-proving-nothing. In this
    pass's shape that is a build script emitting a file nobody started.
    """
    assert "--version" in script_text, (
        "the build no longer starts each binary before publishing it."
    )
    assert "Refusing to publish" in script_text, (
        "the build no longer refuses to publish an artifact that failed "
        "verification — it must not fall through to a warning."
    )


def test_submodule_sources_are_mounted_read_only(script_text: str) -> None:
    """CLAUDE.md forbids editing anything under sources/.

    signalbench's build *must* write ``embedded_binaries/`` somewhere, so the
    guarantee cannot be a convention — it is the ``:ro`` mount flag, enforced by
    the kernel, plus copying the tree out before building.
    """
    assert re.search(r"/src:ro", script_text), (
        "sources/ is no longer mounted read-only into the builder. That mount "
        "flag is the mechanical guarantee that no submodule file is written."
    )


def test_docker_absence_fails_loudly_rather_than_falling_back(script_text: str) -> None:
    """A host `cargo build --release` yields a glibc-DYNAMIC binary.

    Falling back to it would hand a DC an artifact that dies on the customer
    jumpbox — worse than an honest refusal, because the DC finds out in front of
    the customer.
    """
    assert "docker not found" in script_text
    assert "refuses to fall back" in script_text, (
        "the missing-docker error no longer states that a host build is "
        "deliberately not attempted."
    )


def test_unsupported_targets_are_named_with_reasons(script_text: str) -> None:
    """An unsupported target must be legible, never a mystery 404."""
    match = re.search(r"^UNSUPPORTED=\((.*?)^\)", script_text, re.MULTILINE | re.DOTALL)
    assert match, "no UNSUPPORTED=( ... ) array in build-rust-dist.sh"
    body = match.group(1)
    entries = [ln.strip() for ln in body.splitlines() if ln.strip().startswith('"')]
    assert entries, "UNSUPPORTED[] is empty — the matrix is linux/amd64 only, so "
    for entry in entries:
        parts = entry.strip('"').split("|")
        assert len(parts) == 3, f"malformed UNSUPPORTED entry: {entry[:80]}"
        target, tools, reason = parts
        assert "/" in target, f"target should be os/arch: {target}"
        assert tools.strip(), f"{target} names no tools"
        assert len(reason) > 40, (
            f"{target}'s reason is too short to be useful to a DC: {reason!r}"
        )


def test_check_recipe_mode_needs_no_compiler_and_no_docker(script_text: str) -> None:
    """The always-on half of the gate must be cheap enough to never be skipped.

    A job that can be skipped reads exactly like a passing one, so the
    assertions that matter most live in the unconditional `adapters` job — which
    is only possible if they cost ~50 ms and need no toolchain.
    """
    assert "--check-recipe" in script_text
    idx = script_text.index("check_recipe() {")
    end = script_text.index("\n}\n", idx)
    body = script_text[idx:end]
    assert "docker run" not in body, (
        "--check-recipe now shells out to docker; it must stay toolchain-free "
        "so it can live in the unconditional CI job."
    )
    # `cargo` appears legitimately inside human-readable FAIL strings, so assert
    # on invocation shapes rather than the bare word.
    for invocation in ("cargo build", "cargo run", "$(cargo", "`cargo"):
        assert invocation not in body, (
            f"--check-recipe appears to invoke cargo ({invocation!r}); it must "
            "stay compiler-free so it can never be too expensive to run."
        )


# ---------------------------------------------------------------------------
# Drift between the registry and the recipes
# ---------------------------------------------------------------------------


def _registry_cargo_tools() -> set[str]:
    """Tool names in STATIC_TOOL_REGISTRY whose build_cmd invokes cargo."""
    if not REGISTRY.is_file():
        pytest.skip("core/tools/registry.py not present in this context")
    text = REGISTRY.read_text(encoding="utf-8")
    found: set[str] = set()
    current: str | None = None
    for line in text.splitlines():
        m = re.match(r'^\s{4}"([A-Za-z0-9_-]+)":\s*\{', line)
        if m:
            current = m.group(1)
        if current and "build_cmd" in line and "cargo" in line:
            found.add(current)
    return found


def test_no_registry_tool_is_left_without_a_recipe(script_text: str) -> None:
    """Drift guard.

    A registry entry that still says ``cargo build --release`` and has no recipe
    here sends a DC down the exact path this pipeline exists to eliminate:
    building Rust on the customer's host.
    """
    missing = sorted(_registry_cargo_tools() - set(RUST_TOOLS))
    assert not missing, (
        f"core/tools/registry.py declares a cargo build_cmd for {missing} but "
        "scripts/build-rust-dist.sh has no recipe for them, so they can only be "
        "built on a jumpbox with rustup + crates.io egress."
    )


def test_check_recipe_actually_runs_and_passes() -> None:
    """Execute the gate rather than only reading it.

    An assertion nobody runs is not an assertion.
    """
    if not (REPO_ROOT / "sources").is_dir():
        pytest.skip("sources/ not present in this context (image build)")
    proc = subprocess.run(
        [str(BUILD_SCRIPT), "--check-recipe"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == 0, (
        f"build-rust-dist.sh --check-recipe failed:\n{proc.stdout}\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# The produced shelf. Skipped when it has not been built — an EMPTY rust-dist/
# is a valid state (`make rust-dist` fills it), exactly as an empty payload
# shelf is.
# ---------------------------------------------------------------------------


def _manifest() -> dict:
    if not MANIFEST.is_file():
        pytest.skip("rust-dist/ not built here — run `make rust-dist`")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_is_valid_json_with_the_expected_shape() -> None:
    data = _manifest()
    assert data["$generated"]["schema"] == 1
    # Pinned, never floating: an alpine bump that moves OpenSSL would silently
    # change xdrtop's linkage, so the manifest records which image produced it.
    assert re.match(r"^rust:\d+\.\d+-alpine$", data["builder_image"]), (
        f"builder_image must be pinned, got {data['builder_image']!r}"
    )
    assert data["target"] == "x86_64-unknown-linux-musl"
    assert {t["tool"] for t in data["tools"]} == set(RUST_TOOLS)


def test_manifest_has_no_nondeterministic_fields() -> None:
    """No timestamp, hostname or username — the file must be re-derivable.

    sha256/size legitimately vary with the toolchain; that is what
    ``builder_image`` pins.
    """
    raw = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else pytest.skip(
        "rust-dist/ not built here"
    )
    for banned in ("timestamp", "built_at", "hostname", "builder_user", "date"):
        assert banned not in raw.lower(), (
            f"MANIFEST.json contains a nondeterministic field {banned!r}; two "
            "builds of the same inputs must be diffable."
        )


def test_every_manifest_tool_is_static_and_was_executed() -> None:
    for entry in _manifest()["tools"]:
        assert entry["static"] is True, f"{entry['tool']} is not static"
        assert entry["verified_exec"] is True, (
            f"{entry['tool']} was published without being started"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry["tool"]
        assert entry["size_bytes"] > 0


def test_manifest_records_provenance_per_tool() -> None:
    """The pin analogue: which submodule commit did these bytes come from?

    Serving a binary built from a different commit than the checkout is the
    silent-substitution failure the payload shelf's pinning exists to prevent.
    """
    for entry in _manifest()["tools"]:
        assert re.fullmatch(r"[0-9a-f]{40}", entry["submodule_commit"] or ""), (
            f"{entry['tool']} has no submodule_commit — provenance is unrecoverable "
            "once the bytes leave this machine."
        )


def test_manifest_declares_unsupported_targets_with_reasons() -> None:
    unsupported = _manifest()["unsupported_targets"]
    assert unsupported, (
        "the matrix is linux/amd64 only, so unsupported_targets[] must not be "
        "empty — the serving 404 quotes these reasons verbatim."
    )
    for entry in unsupported:
        assert entry["target"] and entry["tools"] and entry["reason"]
        assert len(entry["reason"]) > 40


def test_shelf_filenames_follow_the_agent_dist_idiom() -> None:
    """One naming and verification idiom must cover both shelves."""
    for entry in _manifest()["tools"]:
        assert entry["filename"] == f"cortexsim-tool-{entry['tool']}-linux-amd64"
        assert (DIST_DIR / entry["filename"]).is_file()


def test_sha256sums_matches_the_bytes_on_disk() -> None:
    """`sha256sum -c` is the same verification idiom agent-dist/ uses."""
    sums = DIST_DIR / "SHA256SUMS"
    if not sums.is_file():
        pytest.skip("rust-dist/ not built here — run `make rust-dist`")
    proc = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"],
        cwd=str(DIST_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"sha256sum -c failed:\n{proc.stdout}{proc.stderr}"


def test_manifest_digests_agree_with_sha256sums() -> None:
    """Two independently written records of the same bytes must not disagree."""
    sums = DIST_DIR / "SHA256SUMS"
    if not sums.is_file():
        pytest.skip("rust-dist/ not built here")
    on_disk = {
        parts[1].lstrip("*"): parts[0]
        for parts in (ln.split() for ln in sums.read_text().splitlines() if ln.strip())
    }
    for entry in _manifest()["tools"]:
        assert on_disk.get(entry["filename"]) == entry["sha256"], (
            f"{entry['filename']}: MANIFEST.json and SHA256SUMS disagree"
        )


def test_shelf_is_not_stale_relative_to_the_checkout() -> None:
    """A stale shelf serves a different tool than the source tree says.

    Deliberately a *test* rather than a serving refusal: a stale binary that
    runs beats no binary at all, but it must never look current.
    """
    data = _manifest()
    for entry in data["tools"]:
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(REPO_ROOT / "sources" / entry["tool"]),
                    "rev-parse",
                    "HEAD",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError):
            # The prod image has no git; staleness is a checkout-side concern.
            pytest.skip("git unavailable in this context (prod image)")
        if proc.returncode != 0:
            pytest.skip("git or submodule unavailable in this context")
        live = proc.stdout.strip()
        assert entry["submodule_commit"] == live, (
            f"{entry['tool']}: rust-dist/ was built from "
            f"{entry['submodule_commit'][:7]} but the checkout is now {live[:7]} — "
            "run `make rust-dist`."
        )

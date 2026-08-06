"""Guards on the Rust-tool bake — core/Dockerfile's ``rust-builder`` stage.

WHY these exist, specifically:

* ``STATIC_TOOL_REGISTRY["signalbench"]["build_cmd"]`` has been
  ``cargo build --release`` since Phase 1 and **has never worked**:
  ``src/techniques/software.rs`` does
  ``include_bytes!("../../embedded_binaries/pacemaker_helper")`` and that file
  is not in the upstream git tree — it must be produced by the separate
  ``helpers/pacemaker`` crate first. The failure names a file nobody can find
  rather than the crate that produces it, which is how it stayed invisible for
  the whole life of the repo. If a gitlink bump ever moves or removes that
  helper, the *fast* signal has to be a test, not a five-minute image build.
* xdrtop's static build hangs on ``OPENSSL_STATIC``/``OPENSSL_DIR`` resolving
  alpine's ``openssl-libs-static``. Drop them and the build still SUCCEEDS — it
  just links ``libssl.so.3`` and dies on any host without OpenSSL 3. A silent
  revert to the exact portability failure the stage exists to remove is the
  "green while proving nothing" shape this repo keeps finding.
* A stage that emits binaries nobody executed is the same trap one layer up, so
  the in-stage verification gate (no PT_INTERP, no DT_NEEDED, ``--version``
  exits 0) is asserted to still be there.

Every parse below FAILS CLOSED: the extracted text is asserted non-empty before
anything is asserted about it, so a refactor that breaks the parser fails loudly
instead of silently comparing two empty strings to a green pass.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "core" / "Dockerfile"

TOOLS = ("ackbarx", "signalbench", "xdrtop")


def _dockerfile_text() -> str:
    assert DOCKERFILE.is_file(), f"core/Dockerfile missing at {DOCKERFILE}"
    text = DOCKERFILE.read_text()
    assert text.strip(), "core/Dockerfile is empty"
    return text


def _stage(name: str) -> str:
    """Text of one builder stage, from its FROM line to the next FROM."""
    text = _dockerfile_text()
    match = re.search(rf"^FROM\s+\S+\s+AS\s+{re.escape(name)}\s*$", text, re.M)
    assert match, f"core/Dockerfile has no `FROM ... AS {name}` stage"
    rest = text[match.end():]
    nxt = re.search(r"^FROM\s", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    assert body.strip(), f"stage {name} parsed as empty — the parser is broken, not the file"
    return body


@pytest.fixture(scope="module")
def rust_stage() -> str:
    return _stage("rust-builder")


# ---------------------------------------------------------------------------
# The stage exists and builds all three tools under the served filename
# ---------------------------------------------------------------------------


def test_rust_builder_stage_is_musl_native():
    """Alpine is not cosmetic: x86_64-unknown-linux-musl is the HOST triple
    there, so every build is native. On a glibc base the same recipe becomes a
    cross-compile and needs a musl linker that is not installed."""
    text = _dockerfile_text()
    match = re.search(r"^FROM\s+(\S+)\s+AS\s+rust-builder\s*$", text, re.M)
    assert match, "no rust-builder stage"
    base = match.group(1)
    assert "alpine" in base, (
        f"rust-builder base is {base!r}; the recipe assumes a musl-native base "
        "(x86_64-unknown-linux-musl as the host triple). A glibc base turns "
        "every build into a cross-compile with no linker installed."
    )
    assert re.match(r"^rust:\d+\.\d+", base), (
        f"rust-builder base {base!r} is unpinned; MANIFEST.json records "
        "builder_image, and a floating tag makes that record a lie."
    )


@pytest.mark.parametrize("tool", TOOLS)
def test_every_tool_is_built_and_named_for_the_shelf(rust_stage: str, tool: str):
    served = f"/out/cortexsim-tool-{tool}-linux-amd64"
    assert served in rust_stage, (
        f"{tool} is not copied to {served}. That filename IS the shelf key — "
        "core/api/tools_dist.py::_binary_name() builds it from the tool name, so "
        "any other name is a 404 indistinguishable from 'not built'."
    )
    assert f"cd /src/{tool}" in rust_stage, f"no build step for {tool}"
    assert "--target x86_64-unknown-linux-musl" in rust_stage
    assert 'RUSTFLAGS="-C target-feature=+crt-static"' in rust_stage, (
        "crt-static is what makes these run on a host with no matching libc"
    )


def test_runtime_stage_ships_rust_dist():
    """The binaries must be IN THE IMAGE. Building them in a stage nobody copies
    from is the whole bug class this closes — it is exactly how the beacon
    matrix shipped 4 targets while the script built 5."""
    text = _dockerfile_text()
    assert re.search(r"^COPY --from=rust-builder /out/ /app/rust-dist/\s*$", text, re.M), (
        "core/Dockerfile's runtime stage does not COPY --from=rust-builder into "
        "/app/rust-dist — a container-only deploy would serve an empty shelf."
    )


# ---------------------------------------------------------------------------
# The two recipe details that fail SILENTLY when removed
# ---------------------------------------------------------------------------


def test_signalbench_builds_the_pacemaker_helper_first(rust_stage: str):
    assert "helpers/pacemaker" in rust_stage, (
        "signalbench's build no longer builds helpers/pacemaker. Its lib does "
        "include_bytes!(../../embedded_binaries/pacemaker_helper) at compile "
        "time, and that file is NOT in the upstream tree — without phase 1 the "
        "build dies with 'couldn't read embedded_binaries/pacemaker_helper'."
    )
    assert "embedded_binaries" in rust_stage, (
        "the helper is built but never copied into embedded_binaries/ — the "
        "include_bytes! path signalbench compiles against."
    )
    # The copy must land BEFORE the crate build, or the include_bytes! still fails.
    helper_copy = rust_stage.index("embedded_binaries/pacemaker_helper")
    crate_build = rust_stage.index("/out/cortexsim-tool-signalbench-linux-amd64")
    assert helper_copy < crate_build, "the helper is staged after the crate build"


def test_signalbench_helper_crate_still_exists_on_disk():
    """The cheap always-on half of the gate. A gitlink bump that moves or
    removes helpers/pacemaker breaks the image build five minutes in, with a
    message naming a file rather than the crate. This fails in milliseconds and
    names the crate."""
    helper = REPO_ROOT / "sources" / "signalbench" / "helpers" / "pacemaker" / "Cargo.toml"
    if not (REPO_ROOT / "sources" / "signalbench").exists():
        pytest.skip("sources/signalbench submodule not checked out")
    assert helper.is_file(), (
        f"{helper} is gone. signalbench cannot compile without it (its lib "
        "embeds the helper's output via include_bytes!). Either the submodule "
        "moved the crate or `git submodule update` did not run."
    )


def test_xdrtop_links_openssl_statically(rust_stage: str):
    for var in ("OPENSSL_STATIC=1", "OPENSSL_NO_VENDOR=1", "OPENSSL_DIR=/usr"):
        assert var in rust_stage, (
            f"{var} is missing from the xdrtop build. Without it openssl-sys "
            "links libssl.so.3/libcrypto.so.3 — the build still SUCCEEDS and the "
            "binary dies on any host without OpenSSL 3. Fixing it in Cargo.toml "
            "instead would mean editing the xdrtop submodule, which is forbidden."
        )


def test_the_stage_refuses_to_publish_an_unproven_binary(rust_stage: str):
    """A binary that exists is not a binary that runs."""
    assert "readelf -l" in rust_stage and "INTERP" in rust_stage, (
        "the static-linkage gate is gone; a dynamically linked binary would "
        "reach /app/rust-dist and fail on the customer's jumpbox instead of here"
    )
    assert "NEEDED" in rust_stage, "the DT_NEEDED check is gone"
    assert '"$f" --version' in rust_stage, (
        "nothing executes the built binaries inside the stage — the build would "
        "go green on a file that segfaults"
    )


def test_manifest_records_provenance_and_unsupported_targets(rust_stage: str):
    """MANIFEST.json is the pin analogue for a locally-built artifact, and
    unsupported_targets[] is what makes linux/amd64-only LEGIBLE instead of a
    mystery 404 on a Graviton jumpbox."""
    assert "MANIFEST.json" in rust_stage
    # Keys inside double-quoted shell strings arrive here as \"key\"; normalise
    # so this asserts on the JSON key, not on the shell quoting around it.
    emitted = rust_stage.replace('\\"', '"')
    for key in ('"builder_image"', '"unsupported_targets"', '"submodule_commit"',
                '"verified_exec"', '"sha256"'):
        assert key in emitted, f"MANIFEST.json no longer emits {key}"
    assert '"provenance"' in emitted, (
        "provenance is what distinguishes 'built from commit X' from 'unknown'. "
        "A manifest that omits it lets an unpinned build read as current."
    )
    for target in ("linux/arm64", "windows/amd64"):
        assert target in rust_stage, (
            f"{target} is no longer listed in unsupported_targets[] — either it "
            "is now built (update this test) or the absence became silent."
        )


# ---------------------------------------------------------------------------
# Drift guard against the tool registry
# ---------------------------------------------------------------------------


def test_every_cargo_built_registry_tool_has_a_bake_recipe(rust_stage: str):
    """If someone adds a fourth Rust tool to STATIC_TOOL_REGISTRY, it must also
    be baked — otherwise it silently reverts to 'cargo build on the jumpbox',
    the failure mode this whole pass removes."""
    import sys

    core = str(REPO_ROOT / "core")
    if core not in sys.path:
        sys.path.insert(0, core)
    from tools.registry import STATIC_TOOL_REGISTRY  # noqa: PLC0415

    cargo_tools = {
        name for name, spec in STATIC_TOOL_REGISTRY.items()
        if "cargo" in str(spec.get("build_cmd", ""))
    }
    assert cargo_tools, "no cargo-built tools found — the registry parse is broken"
    missing = sorted(t for t in cargo_tools if f"cortexsim-tool-{t}-linux-amd64" not in rust_stage)
    assert not missing, (
        f"cargo-built tools with no rust-builder recipe: {missing}. They can only "
        "be obtained by `cargo build --release` on the target host, which needs a "
        "Rust toolchain and crates.io egress there."
    )

"""The Rust tools' run_templates must match their actual CLIs.

Both were wrong for the entire life of the registry and nothing caught it,
because nothing ever ran them: `signalbench --technique T1082 --count 1
--output json` and `ackbarx --listen-port 162 --forward-url ...` are both
rejected outright by clap with "unexpected argument". The registry described
tools that do not exist.

The build_cmd was wrong too. signalbench does
`include_bytes!("../../embedded_binaries/pacemaker_helper")` and that file is
NOT in the upstream tree — it is produced by the separate helpers/pacemaker
crate — so the bare `cargo build --release` in the registry failed rc=101 for
every DC who ever ran it, with an error naming a file nobody can find rather
than the crate that makes it.

These tests are static because executing the binaries requires the rust-dist
bake, which is not present in every checkout. They pin the SHAPE that execution
proved: the flags that were rejected must never come back, and the subcommand /
config-file forms must stay.
"""
from __future__ import annotations

import pytest

from tools.registry import STATIC_TOOL_REGISTRY


#: Flags that were in the registry and are REJECTED by the real CLIs. Proven by
#: running the baked binaries on ubuntu:22.04.
_REJECTED = {
    "signalbench": ["--technique", "--count", "--output"],
    "ackbarx": ["--listen-port", "--forward-url"],
}


@pytest.mark.parametrize("tool,flags", sorted(_REJECTED.items()))
def test_no_rejected_flag_returns_to_a_run_template(tool, flags):
    template = STATIC_TOOL_REGISTRY[tool]["run_template"]
    for flag in flags:
        assert flag not in template, (
            f"{tool}'s run_template passes {flag}, which its CLI rejects with "
            f"'unexpected argument'. The tool would never run."
        )


def test_signalbench_uses_the_subcommand_form():
    """`signalbench run <TECHNIQUES>...` — a subcommand CLI, not flags."""
    assert " run " in STATIC_TOOL_REGISTRY["signalbench"]["run_template"]


def test_ackbarx_is_config_file_driven():
    template = STATIC_TOOL_REGISTRY["ackbarx"]["run_template"]
    assert "--config" in template or " -c " in template


def test_signalbench_build_builds_the_pacemaker_helper_first():
    """Without this the build dies on a missing embedded_binaries/ file."""
    build = STATIC_TOOL_REGISTRY["signalbench"]["build_cmd"]
    assert "helpers/pacemaker" in build, (
        "signalbench embeds helpers/pacemaker's output via include_bytes!, and "
        "embedded_binaries/ is not in the upstream tree. A bare `cargo build "
        "--release` fails rc=101."
    )
    assert "embedded_binaries" in build


@pytest.mark.parametrize("tool", ["signalbench", "ackbarx", "xdrtop"])
def test_rust_tools_point_at_the_baked_dist(tool):
    """Prefer the pre-built binary over a source tree that needs a toolchain.

    sources/*/target/release/ is empty in every checkout and in the image; the
    old paths could only resolve after a cargo build on the target host, which
    needs a Rust toolchain and crates.io egress — exactly what a customer's
    default-deny network blocks.
    """
    assert STATIC_TOOL_REGISTRY[tool]["binary"].startswith("rust-dist/"), (
        f"{tool} still points at a source tree that must be compiled on the "
        f"target host"
    )

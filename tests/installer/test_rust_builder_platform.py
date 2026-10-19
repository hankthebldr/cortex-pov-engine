"""The `rust-builder` stage must run as linux/amd64, whatever the build host is.

The stage compiles for one fixed triple, `x86_64-unknown-linux-musl`, because a
lab target is x86_64 regardless of SimCore's own architecture. It was pinned to
`--platform=$BUILDPLATFORM` so that `buildx --platform linux/arm64` would not run
it under QEMU-aarch64. That pin is only correct when the BUILD HOST is amd64.

On an Apple Silicon build host — where a DC actually builds — `$BUILDPLATFORM` is
linux/arm64: the stage runs an aarch64 rustc whose sysroot has no x86_64 `core`,
and the first crate fails with `error[E0463]: can't find crate for core` (cargo
exit 101). It was never seen because the stage SKIPs when the tier-2 submodules
are not checked out; the moment they are, the whole image build dies. Observed
2026-10-04 on darwin/arm64 with `git submodule update --init --recursive`.

Pinning the stage to linux/amd64 is a no-op on amd64 CI and runs under emulation
on arm64 hosts, which makes the triple native in both cases. This static check
parses the real Dockerfile so it runs anywhere with no Docker; the image-level
truth is `make check-rust-shelf` + `make check-rust-exec`.
"""
from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "core" / "Dockerfile"
TARGET_TRIPLE = "x86_64-unknown-linux-musl"


def _rust_builder_from_line() -> str:
    for line in DOCKERFILE.read_text().splitlines():
        if re.match(r"^FROM\b.*\bAS\s+rust-builder\s*$", line, re.IGNORECASE):
            return line
    raise AssertionError("core/Dockerfile has no `FROM ... AS rust-builder` stage")


def test_rust_builder_compiles_for_x86_64_musl():
    # The platform pin below is only correct for this triple; if the triple
    # changes, the pin must be re-derived rather than left to drift.
    assert f"--target {TARGET_TRIPLE}" in DOCKERFILE.read_text()


def test_rust_builder_stage_is_pinned_to_linux_amd64():
    line = _rust_builder_from_line()
    assert "--platform=linux/amd64" in line, (
        f"rust-builder must run as linux/amd64 so the {TARGET_TRIPLE} triple is "
        f"native on every build host; found: {line!r}. `$BUILDPLATFORM` is "
        "linux/arm64 on Apple Silicon and the stage then fails with E0463."
    )

"""install.sh must not check out a submodule that ships compiled implants.

`install.sh::init_submodules` fetched every registered submodule, which until
2026-11-10 included `sources/MITRE-Turla-Carbon` and its
`files/turla/Resources/Binaries/binaries.zip` of compiled Turla emulation
implants. Nothing in CortexSim read it, and the bootstrap dropped it onto
customer jumpboxes anyway.

The guard is `scripts/check-installer-submodules.py`. It runs install.sh's real
`init_submodules` with real git in a sandbox. It is enforced in CI by the
`adapters` job, which runs on the host. These tests run it on the tree, and
they hold its negative controls. The prod image has no git, so inside the image
every test here skips. The CI `adapters` step is what makes the guard binding.

The negative controls do not need the old tree. Each one rebuilds the defect
from the current install.sh plus a `.gitmodules` that registers the Turla repo
again. Each one fails if the guard stops being able to fail.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-installer-submodules.py"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git and bash; CI enforces this in the host-run `adapters` job",
)

TURLA = """
[submodule "sources/MITRE-Turla-Carbon"]
\tpath = sources/MITRE-Turla-Carbon
\turl = https://github.com/Palo-Cortex/MITRE-Turla-Carbon.git
"""


def _load():
    spec = importlib.util.spec_from_file_location("check_installer_submodules", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # dataclasses resolve annotations through it
    spec.loader.exec_module(mod)
    return mod


guard = _load()


def _tree(tmp_path: Path, *, extra_gitmodules: str = "", install_sh: str | None = None) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    (root / ".gitmodules").write_text((REPO / ".gitmodules").read_text() + extra_gitmodules)
    (root / "install.sh").write_text(
        install_sh if install_sh is not None else (REPO / "install.sh").read_text()
    )
    return root


def test_this_tree_installs_no_denylisted_submodule():
    report = guard.evaluate(REPO)
    assert report.ok, "\n".join(report.failures)
    assert report.initialised, "install.sh checked out nothing; the sandbox proves nothing"
    registered = {s.path for s in report.registered}
    assert not registered & {d.path for d in guard.DENYLIST}


def test_guard_fails_when_turla_is_registered_and_install_sh_fetches_everything(tmp_path):
    """The 2026-11-10 defect: registered, and fetched by `--init --recursive`."""
    report = guard.evaluate(_tree(tmp_path, extra_gitmodules=TURLA))
    assert not report.ok
    assert "sources/MITRE-Turla-Carbon" in report.initialised
    rules = sorted(f.split()[0] for f in report.failures)
    assert rules == ["R1", "R2"], report.failures


def test_guard_matches_the_denylist_by_url_when_the_path_moves(tmp_path):
    moved = TURLA.replace("sources/MITRE-Turla-Carbon", "vendor/carbon")
    report = guard.evaluate(_tree(tmp_path, extra_gitmodules=moved))
    assert any(f.startswith("R2 install.sh init_submodules checks out vendor/carbon")
               for f in report.failures), report.failures


def test_update_none_is_honoured_by_git_not_by_reading_text(tmp_path):
    """`update = none` makes git skip it, so R1 and R2 both hold."""
    skipped = TURLA.rstrip("\n") + "\n\tupdate = none\n"
    report = guard.evaluate(_tree(tmp_path, extra_gitmodules=skipped))
    assert "sources/MITRE-Turla-Carbon" not in report.initialised
    assert report.ok, report.failures


def test_content_rule_flags_a_binaries_zip_in_any_checked_out_submodule(tmp_path):
    pack = '\n[submodule "sources/fixture-pack"]\n\tpath = sources/fixture-pack\n' \
           "\turl = https://example.invalid/fixture-pack.git\n"
    root = _tree(tmp_path, extra_gitmodules=pack)
    payload = root / "sources" / "fixture-pack" / "files" / "Resources" / "BINARIES"
    payload.mkdir(parents=True)
    (payload / "implants.ZIP").write_bytes(b"PK\x03\x04")
    report = guard.evaluate(root)
    assert "sources/fixture-pack" in report.content_scanned
    assert [f.split()[0] for f in report.failures] == ["R3"], report.failures


def test_a_submodule_not_on_disk_is_reported_not_read_as_clean(tmp_path):
    report = guard.evaluate(_tree(tmp_path))
    assert report.ok
    assert set(report.content_not_on_disk) == set(report.initialised)
    assert report.content_scanned == []


def test_an_unrecognised_install_sh_raises_instead_of_passing(tmp_path):
    root = _tree(tmp_path, install_sh="#!/usr/bin/env bash\necho no init function here\n")
    with pytest.raises(guard.GuardError, match="init_submodules"):
        guard.evaluate(root)


def test_main_exit_codes(tmp_path, capsys):
    assert guard.main(["--root", str(REPO)]) == 0
    assert guard.main(["--root", str(_tree(tmp_path, extra_gitmodules=TURLA))]) == 1
    broken = tmp_path / "broken"
    broken.mkdir()
    assert guard.main(["--root", str(broken)]) == 2
    capsys.readouterr()

"""Guard for ``scripts/ci/check_agent_magic.py`` (the magic-byte half of
``make check-agent-shelf``).

The make target runs the script inside the BUILT IMAGE (``docker run -i
--entrypoint python3 $(IMAGE) - /app/agent-dist < script``), which this suite
cannot do without docker. What it can do is drive the same script the same way
-- source on stdin, shelf path as argv -- against synthetic shelves, and prove
it goes red on exactly the shelf the old ``test -s`` + ``sha256sum -c`` check
waved through: a correctly-named, non-empty, correctly-checksummed file that is
the wrong binary.
"""
from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check_agent_magic.py"


def _elf(machine: int) -> bytes:
    ident = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9
    return ident + struct.pack("<HH", 2, machine) + b"\0" * 64


def _macho(cputype: int) -> bytes:
    return b"\xcf\xfa\xed\xfe" + struct.pack("<I", cputype) + b"\0" * 64


def _pe(machine: int, lfanew: int = 0x80) -> bytes:
    dos = bytearray(b"MZ" + b"\0" * (lfanew - 2))
    struct.pack_into("<I", dos, 0x3C, lfanew)
    return bytes(dos) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 64


GOOD = {
    "cortexsim-agent-linux-amd64": _elf(0x3E),
    "cortexsim-agent-linux-arm64": _elf(0xB7),
    "cortexsim-agent-darwin-amd64": _macho(0x01000007),
    "cortexsim-agent-darwin-arm64": _macho(0x0100000C),
    "cortexsim-agent-windows-amd64.exe": _pe(0x8664),
}


def _shelf(tmp_path: Path) -> Path:
    d = tmp_path / "agent-dist"
    d.mkdir()
    for name, data in GOOD.items():
        (d / name).write_bytes(data)
    return d


def _run(shelf: Path) -> subprocess.CompletedProcess:
    # Exactly the make target's shape: source on stdin, shelf path as argv.
    return subprocess.run([sys.executable, "-", str(shelf)], input=SCRIPT.read_text(encoding="utf-8"),
                          capture_output=True, text=True, timeout=60)


def test_a_correct_shelf_passes(tmp_path):
    proc = _run(_shelf(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "5/5" in proc.stdout


@pytest.mark.parametrize("name,data,why", [
    # The case the old check cannot see: a linux ELF wearing the .exe name.
    ("cortexsim-agent-windows-amd64.exe", _elf(0x3E), "not a PE"),
    ("cortexsim-agent-windows-amd64.exe", _pe(0x014C), "PE machine 0x14C"),
    ("cortexsim-agent-linux-arm64", _elf(0x3E), "e_machine 0x3E, want 0xB7"),
    ("cortexsim-agent-linux-amd64", _macho(0x01000007), "not an ELF"),
    ("cortexsim-agent-darwin-arm64", _macho(0x01000007), "cputype 0x01000007, want 0x0100000C"),
    ("cortexsim-agent-darwin-amd64", _elf(0x3E), "not a 64-bit little-endian Mach-O"),
    ("cortexsim-agent-windows-amd64.exe", None, "missing"),
])
def test_a_mislabelled_or_missing_target_fails(tmp_path, name, data, why):
    d = _shelf(tmp_path)
    target = d / name
    target.unlink()
    if data is not None:
        target.write_bytes(data)
    proc = _run(d)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert why in proc.stdout, proc.stdout


def test_a_pe_header_past_the_first_4k_is_still_read(tmp_path):
    d = _shelf(tmp_path)
    (d / "cortexsim-agent-windows-amd64.exe").write_bytes(_pe(0x8664, lfanew=0x2000))
    proc = _run(d)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_absent_shelf_is_a_failure_not_an_ok(tmp_path):
    proc = _run(tmp_path / "nope")
    assert proc.returncode == 1
    assert "not a directory" in proc.stdout

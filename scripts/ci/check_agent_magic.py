"""Assert every beacon on the shelf is the binary its filename claims.

Run by ``make check-agent-shelf`` INSIDE the built image, with the image's own
python3, the script arriving on stdin:

    docker run --rm -i --entrypoint python3 $(IMAGE) - /app/agent-dist < scripts/ci/check_agent_magic.py

Presence is not identity. ``test -s`` and ``sha256sum -c SHA256SUMS`` both pass
for a non-empty ``cortexsim-agent-windows-amd64.exe`` that is really a linux
ELF -- the failure mode of a mislabelled loop variable in the Dockerfile's
agent-builder stage -- and the DC finds out on the customer's endpoint. This
reads each target's header:

    linux-*            ELF64 LE, e_machine 0x3E (amd64) / 0xB7 (arm64)
    darwin-*           Mach-O 64 LE (0xFEEDFACF), cputype 0x01000007 / 0x0100000C
    windows-amd64.exe  MZ, PE\\0\\0 at e_lfanew, machine 0x8664

Stdlib only (the runtime image has no pip extras). Exit 1 on any mismatch or
missing target; an empty shelf is a failure, never an ok.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ELF_MACHINE = {"amd64": 0x3E, "arm64": 0xB7}
MACHO_CPUTYPE = {"amd64": 0x01000007, "arm64": 0x0100000C}
PE_MACHINE = {"amd64": 0x8664}

TARGETS = (
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("darwin", "amd64"),
    ("darwin", "arm64"),
    ("windows", "amd64"),
)


def _name(goos: str, goarch: str) -> str:
    return f"cortexsim-agent-{goos}-{goarch}" + (".exe" if goos == "windows" else "")


def check(path: Path, goos: str, goarch: str) -> str | None:
    """Return None when ``path`` is a ``goos``/``goarch`` executable, else why not."""
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError as exc:
        return f"unreadable: {exc}"
    if goos == "linux":
        if head[:4] != b"\x7fELF":
            return f"not an ELF (magic {head[:4]!r})"
        if len(head) < 20:
            return "truncated ELF header"
        if head[4] != 2:
            return f"not ELF64 (EI_CLASS={head[4]})"
        if head[5] != 1:
            return f"not little-endian (EI_DATA={head[5]})"
        machine = struct.unpack_from("<H", head, 18)[0]
        if machine != ELF_MACHINE[goarch]:
            return f"e_machine 0x{machine:X}, want 0x{ELF_MACHINE[goarch]:X} ({goarch})"
        return None
    if goos == "darwin":
        if head[:4] != b"\xcf\xfa\xed\xfe":
            return f"not a 64-bit little-endian Mach-O (magic {head[:4]!r})"
        if len(head) < 8:
            return "truncated Mach-O header"
        cputype = struct.unpack_from("<I", head, 4)[0]
        if cputype != MACHO_CPUTYPE[goarch]:
            return f"cputype 0x{cputype:08X}, want 0x{MACHO_CPUTYPE[goarch]:08X} ({goarch})"
        return None
    if goos == "windows":
        if head[:2] != b"MZ":
            return f"not a PE (DOS magic {head[:2]!r})"
        if len(head) < 0x40:
            return "truncated DOS header"
        lfanew = struct.unpack_from("<I", head, 0x3C)[0]
        if lfanew + 6 > len(head):
            # e_lfanew past the first 4 KiB: read exactly what is needed.
            with path.open("rb") as fh:
                fh.seek(lfanew)
                pe = fh.read(6)
        else:
            pe = head[lfanew:lfanew + 6]
        if len(pe) < 6 or pe[:4] != b"PE\x00\x00":
            return f"no PE signature at e_lfanew=0x{lfanew:X}"
        machine = struct.unpack_from("<H", pe, 4)[0]
        if machine != PE_MACHINE[goarch]:
            return f"PE machine 0x{machine:X}, want 0x{PE_MACHINE[goarch]:X} ({goarch})"
        return None
    return f"unknown target os {goos!r}"


def main(argv: list[str]) -> int:
    shelf = Path(argv[1]) if len(argv) > 1 else Path("/app/agent-dist")
    if not shelf.is_dir():
        print(f"[agent-magic] FAIL: {shelf} is not a directory")
        return 1
    bad = 0
    for goos, goarch in TARGETS:
        path = shelf / _name(goos, goarch)
        if not path.is_file():
            print(f"[agent-magic] FAIL {path.name}: missing")
            bad += 1
            continue
        why = check(path, goos, goarch)
        if why:
            print(f"[agent-magic] FAIL {path.name}: {why}")
            bad += 1
        else:
            print(f"[agent-magic] ok   {path.name}: {goos}/{goarch}")
    if bad:
        print(f"[agent-magic] FAIL: {bad} of {len(TARGETS)} targets are not the binary their name claims")
        return 1
    print(f"[agent-magic] OK: {len(TARGETS)}/{len(TARGETS)} targets carry the header their name claims")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""check-installer-submodules.py — the jumpbox bootstrap must not fetch implants.

Why this exists
---------------
`install.sh::init_submodules` runs `git submodule update --init --recursive`,
which fetches every submodule `.gitmodules` registers. Until 2026-11-10 that
included `sources/MITRE-Turla-Carbon`, which nothing in CortexSim reads, and
which carries `files/turla/Resources/Binaries/binaries.zip`: compiled Turla
emulation implants (EPIC, Carbon, keylogger, Mimikatz, Snake, LightNeuron),
PsExec and a Python installer. A DC bootstrapping a customer jumpbox therefore
dropped them on the customer's host for no functional reason.

Three rules
-----------
R1 registration  A denylisted submodule (matched by URL or path) that is still
                 registered in `.gitmodules` must carry `update = none`.
                 Otherwise every fetch path pulls it, not only install.sh:
                 actions/checkout `submodules: recursive`, `git clone
                 --recurse-submodules`, and the session-start hook.
R2 install.sh    Run install.sh's real `init_submodules` with real git against
                 a sandbox superproject that registers this tree's
                 `.gitmodules` byte for byte. Every URL is rewritten to one
                 local placeholder repo and every non-file transport is
                 blocked, so the run never touches the network. Fail if it
                 checks out a denylisted submodule.
R3 content       For every submodule R2 shows install.sh checks out AND that is
                 checked out under the real tree, fail on any `*.zip` inside a
                 directory named `Binaries` (any case). A submodule that is not
                 on disk is counted and reported. It is never read as clean.

R2 runs git rather than parsing install.sh's git arguments, so pathspecs,
`update = none` and `-c submodule.*` overrides all get git's own semantics.

Exit codes: 0 every rule held · 1 a rule failed · 2 the guard could not run
(no git or bash, no `.gitmodules`, install.sh has no recognisable
`init_submodules`). An unrecognised install.sh is exit 2, never a pass.

Usage:
    scripts/check-installer-submodules.py                 # this checkout
    scripts/check-installer-submodules.py --root /other   # another tree
    CORTEXSIM_BASE_DIR=/repo scripts/check-installer-submodules.py
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Denied:
    url: str      # normalised: host/owner/repo, lower case, no scheme, no .git
    path: str     # path as .gitmodules registers it
    reason: str


DENYLIST: tuple[Denied, ...] = (
    Denied(
        url="github.com/palo-cortex/mitre-turla-carbon",
        path="sources/MITRE-Turla-Carbon",
        reason=(
            "compiled Turla emulation implants (EPIC, Carbon, keylogger, "
            "Mimikatz, Snake, LightNeuron) in "
            "files/turla/Resources/Binaries/binaries.zip; nothing in "
            "CortexSim reads it (retired 2026-11-10)"
        ),
    ),
)

# R3: a directory with this name (compared case-insensitively) that holds a .zip.
CONTENT_DIR = "binaries"


class GuardError(RuntimeError):
    """The guard could not evaluate the tree. Exit 2, never a pass."""


@dataclass(frozen=True)
class Submodule:
    name: str
    path: str
    url: str
    update: str | None


@dataclass
class Report:
    registered: list[Submodule]
    initialised: list[str]
    failures: list[str] = field(default_factory=list)
    content_scanned: list[str] = field(default_factory=list)
    content_not_on_disk: list[str] = field(default_factory=list)
    install_output: str = ""

    @property
    def ok(self) -> bool:
        return not self.failures


def normalise_url(url: str) -> str:
    u = url.strip().lower()
    u = re.sub(r"^[a-z+]+://", "", u)                 # https://, ssh://, git://
    u = re.sub(r"^[^@/]+@", "", u)                     # user@ prefix
    u = re.sub(r"^([^/:]+):(?!\d+/)", r"\1/", u)       # scp-style host:owner
    u = u.rstrip("/")
    if u.endswith(".git"):
        u = u[: -len(".git")]
    return u


def denied_for(sub: Submodule) -> Denied | None:
    url = normalise_url(sub.url)
    for d in DENYLIST:
        if url == d.url or sub.path == d.path:
            return d
    return None


def _git(*args: str, cwd: Path | None = None, env: dict | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise GuardError(f"git {' '.join(args)} failed ({proc.returncode}):\n{proc.stderr}")
    return proc.stdout


def read_gitmodules(root: Path) -> list[Submodule]:
    gm = root / ".gitmodules"
    if not gm.is_file():
        raise GuardError(f"{gm} not found; install.sh dies on this too")
    raw = _git("config", "-f", str(gm), "--null", "--list")
    fields: dict[str, dict[str, str]] = {}
    for entry in filter(None, raw.split("\0")):
        key, _, value = entry.partition("\n")
        if not key.startswith("submodule."):
            continue
        name, _, var = key[len("submodule."):].rpartition(".")
        fields.setdefault(name, {})[var.lower()] = value
    subs = []
    for name, f in fields.items():
        if "path" not in f or "url" not in f:
            raise GuardError(f".gitmodules entry {name!r} has no path or url: {f}")
        subs.append(Submodule(name, f["path"], f["url"], f.get("update")))
    return subs


_FUNC_START = re.compile(r"^init_submodules\(\)\s*\{\s*$")
_FUNC_END = re.compile(r"^\}\s*$")
_OPTIONAL = re.compile(r"^OPTIONAL_SUBMODULES=")


def extract_init_submodules(install_sh: str) -> str:
    """Return install.sh's top-level OPTIONAL_SUBMODULES and init_submodules text."""
    lines = install_sh.splitlines()
    optional: list[str] = []
    i = 0
    while i < len(lines):
        if _OPTIONAL.match(lines[i]):
            optional.append(lines[i])
            while "(" in lines[i] and ")" not in lines[i] and i + 1 < len(lines):
                i += 1
                optional.append(lines[i])
        i += 1
    start = next((n for n, line in enumerate(lines) if _FUNC_START.match(line)), None)
    if start is None:
        raise GuardError("install.sh has no top-level `init_submodules() {` — shape not recognised")
    end = next((n for n in range(start + 1, len(lines)) if _FUNC_END.match(lines[n])), None)
    if end is None:
        raise GuardError("install.sh init_submodules has no closing `}` at column 0")
    preamble = optional or ["OPTIONAL_SUBMODULES=()"]
    return "\n".join([*preamble, *lines[start:end + 1]]) + "\n"


_DRIVER = """\
set -euo pipefail
log_step() {{ printf '==> %s\\n' "$*"; }}
log_ok()   {{ printf 'ok   %s\\n' "$*"; }}
log_warn() {{ printf 'warn %s\\n' "$*"; }}
log_info() {{ printf 'info %s\\n' "$*"; }}
die()      {{ printf 'DIE  %s\\n' "$*" >&2; exit 97; }}
SCRIPT_DIR={script_dir}
{body}
init_submodules
"""


def run_install_sh_init(root: Path, subs: list[Submodule]) -> tuple[list[str], str]:
    """Run install.sh's init_submodules in a sandbox; return (checked-out paths, output)."""
    bash = shutil.which("bash")
    if bash is None or shutil.which("git") is None:
        raise GuardError("needs bash and git on PATH")
    body = extract_init_submodules((root / "install.sh").read_text())

    with tempfile.TemporaryDirectory(prefix="cxs-submodule-guard-") as tmp:
        tmpd = Path(tmp)
        home = tmpd / "home"
        home.mkdir()
        base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "LANG": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "guard", "GIT_AUTHOR_EMAIL": "guard@invalid",
            "GIT_COMMITTER_NAME": "guard", "GIT_COMMITTER_EMAIL": "guard@invalid",
        }

        placeholder = tmpd / "placeholder"
        _git("init", "-q", str(placeholder), env=base_env)
        (placeholder / "PLACEHOLDER").write_text("stand-in for every registered submodule\n")
        _git("add", "PLACEHOLDER", cwd=placeholder, env=base_env)
        _git("commit", "-q", "-m", "placeholder", cwd=placeholder, env=base_env)
        sha = _git("rev-parse", "HEAD", cwd=placeholder, env=base_env).strip()

        sup = tmpd / "superproject"
        _git("init", "-q", str(sup), env=base_env)
        shutil.copy2(root / ".gitmodules", sup / ".gitmodules")
        for s in subs:
            _git("update-index", "--add", "--cacheinfo", f"160000,{sha},{s.path}",
                 cwd=sup, env=base_env)
        _git("add", ".gitmodules", cwd=sup, env=base_env)
        _git("commit", "-q", "-m", "registered submodules", cwd=sup, env=base_env)

        # Rewrite every registered URL to the placeholder; allow only file://.
        cfg = [("protocol.file.allow", "always")]
        cfg += [(f"url.{placeholder.as_uri()}.insteadOf", s.url) for s in subs]
        run_env = dict(base_env, GIT_ALLOW_PROTOCOL="file", GIT_CONFIG_COUNT=str(len(cfg)))
        for n, (k, v) in enumerate(cfg):
            run_env[f"GIT_CONFIG_KEY_{n}"] = k
            run_env[f"GIT_CONFIG_VALUE_{n}"] = v

        driver = tmpd / "driver.sh"
        driver.write_text(_DRIVER.format(script_dir=_shq(str(sup)), body=body))
        proc = subprocess.run(
            [bash, str(driver)], cwd=sup, env=run_env,
            capture_output=True, text=True, timeout=300,
        )
        output = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise GuardError(
                f"install.sh init_submodules exited {proc.returncode} in the sandbox:\n{output}"
            )
        checked_out = [s.path for s in subs if (sup / s.path / ".git").exists()]
        return checked_out, output


def _shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def scan_content(tree_path: Path) -> list[str]:
    hits = []
    for dirpath, dirnames, filenames in os.walk(tree_path):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if Path(dirpath).name.casefold() == CONTENT_DIR:
            hits += [str(Path(dirpath, f)) for f in filenames if f.casefold().endswith(".zip")]
    return hits


def _populated(p: Path) -> bool:
    return p.is_dir() and any(e.name != ".git" for e in p.iterdir())


def evaluate(root: Path) -> Report:
    subs = read_gitmodules(root)
    initialised, output = run_install_sh_init(root, subs)
    report = Report(registered=subs, initialised=initialised, install_output=output)

    for s in subs:                                                     # R1
        d = denied_for(s)
        if d is not None and (s.update or "").strip().lower() != "none":
            report.failures.append(
                f"R1 {s.path} ({s.url}) is registered in .gitmodules without "
                f"`update = none`, so every fetch path pulls it: {d.reason}"
            )

    by_path = {s.path: s for s in subs}
    for path in initialised:                                           # R2
        d = denied_for(by_path[path])
        if d is not None:
            report.failures.append(
                f"R2 install.sh init_submodules checks out {path} "
                f"({by_path[path].url}): {d.reason}"
            )

    for path in initialised:                                           # R3
        tree = root / path
        if not _populated(tree):
            report.content_not_on_disk.append(path)
            continue
        report.content_scanned.append(path)
        for hit in scan_content(tree):
            report.failures.append(
                f"R3 install.sh checks out {path}, which ships "
                f"{Path(hit).relative_to(root)} (a .zip under a Binaries/ directory)"
            )
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    default_root = os.environ.get("CORTEXSIM_BASE_DIR") or str(Path(__file__).resolve().parents[1])
    ap.add_argument("--root", default=default_root, help="repo root to check")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    try:
        report = evaluate(root)
    except GuardError as exc:
        print(f"ERROR check-installer-submodules could not run: {exc}", file=sys.stderr)
        return 2

    print(f"check-installer-submodules: {root}")
    print(f"  registered in .gitmodules : {len(report.registered)}")
    print(f"  install.sh checks out     : {len(report.initialised)}")
    print(f"  denylist                  : {', '.join(d.path for d in DENYLIST)}")
    if report.content_not_on_disk:
        print(
            f"WARN R3 content not scanned for {len(report.content_not_on_disk)} of "
            f"{len(report.initialised)} submodule(s) not checked out here: "
            f"{', '.join(report.content_not_on_disk)}"
        )
    print(f"  content scanned (R3)      : {len(report.content_scanned)}")
    for f in report.failures:
        print(f"FAIL {f}")
    if report.ok:
        print("PASS install.sh fetches no denylisted submodule and no Binaries/*.zip")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

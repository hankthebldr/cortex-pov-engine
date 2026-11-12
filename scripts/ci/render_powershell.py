#!/usr/bin/env python3
"""Render every PowerShell artifact CortexSim emits into one directory.

Companion to ``scripts/ci/ps51-gate.ps1`` (``make check-powershell``). This half
writes the bytes; the gate half decodes them the way Windows PowerShell 5.1
reads them, parses them, and walks the AST for PowerShell-7-only constructs.

NECESSARY, NOT SUFFICIENT. The gate parses with whatever pwsh is on PATH, and
pwsh 7 is not Windows PowerShell 5.1 -- the product floor. Nothing this script
renders has been executed on Windows by rendering it.

Categories (each is a ``kind`` in manifest.json; every one is REQUIRED, and a
category that renders nothing is an error -- "rendered 0 <kind>" is degraded,
never ok):

  installer       GET /api/agents/install?os=windows, with and without a token,
                  with and without a windows/amd64 beacon on the shelf (4).
  uninstaller     GET /api/agents/install?os=windows&uninstall=1. A body that
                  starts with ``#!`` is NOT PowerShell, and the manifest says so
                  ("windows uninstaller is not PowerShell"); the gate fails it.
  bundle          generate_powershell() for every windows-emittable scenario,
                  loaded through the real loader (_parse_and_validate ->
                  _schema_to_orm_kwargs, the dict the API feeds the generator).
  step            every resolve_target(s, 'windows') step whose shell is
                  powershell, one file each. cmd-shell steps are listed, not
                  parsed. Windows-labelled steps that resolve to nothing are
                  counted as a warning.
  beacon-wrapper  the Go beacon's own wrapper shapes, via
                  ``go test ./executor -run TestDumpPowerShellSamples``.
  repo-ps1        every tracked ``*.ps1`` outside sources/ (git ls-files),
                  except the gate's deliberately-bad fixtures.

Byte fidelity: installers/uninstaller are written exactly as served; bundles as
the API serves them (UTF-8, the generator's own BOM included); steps with a
UTF-8 BOM, because both delivery paths hand PowerShell exact text (the beacon's
-EncodedCommand is UTF-16LE; the push harness writes each step with
``Set-Content -Encoding UTF8``, which carries a BOM on 5.1).

No network, no tenant, no SimCore process: the installer comes from FastAPI's
TestClient against the real ``api.agents`` router, in-process.

Usage:
    PYTHONPATH=core python3 scripts/ci/render_powershell.py --out DIR [--manifest]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "core"
SCHEMA = "cortexsim.ps-gate.manifest/1"
REQUIRED_KINDS = ("installer", "uninstaller", "bundle", "step", "beacon-wrapper", "repo-ps1")

# Tracked .ps1 files that are deliberately NOT 5.1-clean: the gate's own
# negative fixtures. Excluded by exact path, never by pattern, so a new bad
# file elsewhere cannot hide behind this list.
REPO_PS1_EXCLUDE = {
    "tests/ci/fixtures/bad_ps7_only.ps1": "gate negative fixture (PS7-only on purpose)",
    "tests/ci/fixtures/bad_cp1252_quote.ps1": "gate negative fixture (BOM-less em dash on purpose)",
}

GATE_TOKEN = "cxs_psgate_fake_token_000000"
UTF8_BOM = b"\xef\xbb\xbf"


def _bootstrap_imports() -> None:
    if str(CORE) not in sys.path:
        sys.path.insert(0, str(CORE))
    # Resolve spec/, scenarios/ and data/ against THIS checkout, never /app.
    os.environ.setdefault("CORTEXSIM_BASE_DIR", str(REPO))
    os.environ.setdefault("CORTEXSIM_ENV", "development")


@contextlib.contextmanager
def _env(name: str, value: str) -> Iterator[None]:
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def _strip_bom(data: bytes) -> bytes:
    return data[len(UTF8_BOM):] if data.startswith(UTF8_BOM) else data


def _prepare_out(out: Path) -> str | None:
    """Make ``out`` an empty directory, or say why not.

    Only a directory this script wrote (it holds our manifest.json) or an
    empty one is cleared. Anything else is refused rather than wiped: a typo
    in --out must never empty a real directory.
    """
    if out == REPO or out in REPO.parents:
        return f"refusing --out {out}: it is the checkout or one of its parents"
    if REPO in out.parents and out.relative_to(REPO).parts[0] != "build":
        return f"refusing --out {out}: inside the source tree but not under build/"
    if out.exists():
        if not out.is_dir():
            return f"refusing --out {out}: exists and is not a directory"
        if any(out.iterdir()):
            try:
                ours = json.loads((out / "manifest.json").read_text(encoding="utf-8")).get("schema") == SCHEMA
            except Exception:  # noqa: BLE001 -- unreadable means not ours
                ours = False
            if not ours:
                return (f"refusing --out {out}: not empty and not a previous render "
                        f"(no {SCHEMA} manifest.json) -- pick an empty or new directory")
            shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    return None


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


class Renderer:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.artifacts: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.extra: dict[str, Any] = {}

    # -- bookkeeping --------------------------------------------------------

    def add(self, rel: str, data: bytes, kind: str, source: str, *,
            delivery: str, not_powershell: str | None = None, **meta: Any) -> None:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entry: dict[str, Any] = {
            "path": rel, "kind": kind, "source": source, "delivery": delivery,
            "bom": data.startswith(UTF8_BOM), "bytes": len(data),
            "not_powershell": not_powershell,
        }
        entry.update(meta)
        self.artifacts.append(entry)

    def count(self, kind: str) -> int:
        return sum(1 for a in self.artifacts if a["kind"] == kind)

    def fail_if_empty(self, kind: str, why: str = "") -> None:
        if self.count(kind) == 0:
            self.errors.append(f"rendered 0 {kind}" + (f": {why}" if why else ""))

    # -- (a) + (b) installer / uninstaller ----------------------------------

    def render_installers(self) -> None:
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            from api.agents import router
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            self.errors.append(f"rendered 0 installer: cannot import the agents router: {exc!r}")
            self.errors.append(f"rendered 0 uninstaller: cannot import the agents router: {exc!r}")
            return

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)

        with tempfile.TemporaryDirectory(prefix="ps-gate-dist-") as tmp:
            served = Path(tmp) / "served"
            served.mkdir()
            # Magic bytes only: the installer checks the file EXISTS to decide
            # whether to emit the WINDOWS_AGENT_UNAVAILABLE preflight.
            (served / "cortexsim-agent-windows-amd64.exe").write_bytes(b"MZ" + b"\0" * 62)
            empty = Path(tmp) / "empty"
            empty.mkdir()

            variants = [
                ("windows-token-beacon-served", served, True),
                ("windows-notoken-beacon-served", served, False),
                ("windows-token-no-beacon", empty, True),
                ("windows-notoken-no-beacon", empty, False),
            ]
            for name, dist, with_token in variants:
                url = "/api/agents/install?os=windows"
                if with_token:
                    url += f"&token={GATE_TOKEN}"
                with _env("CORTEXSIM_AGENT_DIST", str(dist)):
                    r = client.get(url)
                if r.status_code != 200:
                    self.errors.append(f"installer {name}: GET {url} -> HTTP {r.status_code}")
                    continue
                self.add(
                    f"installer/{name}.ps1", r.content, "installer",
                    f"GET {url} (CORTEXSIM_AGENT_DIST={'windows/amd64 served' if dist is served else 'empty'})",
                    delivery="iwr | iex (UTF-8 via charset); or saved and run with -File (5.1 reads BOM-less as cp1252)",
                    content_type=r.headers.get("content-type"),
                )

            with _env("CORTEXSIM_AGENT_DIST", str(served)):
                url = "/api/agents/install?os=windows&uninstall=1"
                r = client.get(url)
            if r.status_code != 200:
                self.errors.append(f"uninstaller: GET {url} -> HTTP {r.status_code}")
            else:
                body = r.content
                not_ps = None
                if _strip_bom(body).startswith(b"#!"):
                    first = body.split(b"\n", 1)[0].decode("utf-8", "replace")
                    not_ps = (f"windows uninstaller is not PowerShell (body starts with '#!': {first!r}; "
                              f"served as {r.headers.get('content-type')!r}, "
                              f"{r.headers.get('content-disposition')!r})")
                self.add(
                    "uninstaller/windows-uninstall.ps1", body, "uninstaller", f"GET {url}",
                    delivery="iwr | iex", not_powershell=not_ps,
                    content_type=r.headers.get("content-type"),
                )
        self.fail_if_empty("installer")
        self.fail_if_empty("uninstaller")

    # -- (c) + (d) bundles and steps ----------------------------------------

    def render_scenarios(self) -> None:
        try:
            from engine import push_generator as pg
            from engine.orchestrator import resolve_steps_for_target
            from engine.scenario_loader import (
                _find_yaml_files,
                _parse_and_validate,
                _schema_to_orm_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            for k in ("bundle", "step"):
                self.errors.append(f"rendered 0 {k}: cannot import the scenario loader/generator: {exc!r}")
            return

        files = _find_yaml_files(str(REPO / "scenarios"))
        rejected: list[dict[str, str]] = []
        loaded = 0
        windows_emittable: list[str] = []
        push_withdrawn: list[str] = []
        cmd_steps: list[dict[str, str]] = []
        unresolved_labelled: list[dict[str, str]] = []
        pull_divergence: list[dict[str, str]] = []

        for fp in files:
            schema, err = _parse_and_validate(fp)
            if err or schema is None:
                rejected.append({"file": os.path.relpath(fp, REPO), "error": (err or "")[:300]})
                continue
            loaded += 1
            doc = _schema_to_orm_kwargs(schema)
            sid = doc["scenario_id"]

            win = pg.resolve_target(doc, "windows")

            # Windows-labelled steps the windows resolver cannot satisfy. Counted
            # for every scenario (not only emittable ones): a step that declares
            # `platforms: [windows]` and resolves to nothing is content that
            # claims Windows coverage and cannot deliver it.
            unresolved_ids = {s for s, _ in win.unresolved}
            for step in doc.get("steps") or []:
                plats = pg._declared_platforms(step)  # noqa: SLF001 -- the resolver's own reading
                if "windows" in plats and step.get("id") in unresolved_ids:
                    reason = dict(win.unresolved).get(step.get("id"), "")
                    unresolved_labelled.append({"scenario_id": sid, "step_id": step.get("id"), "reason": reason})

            if not win.emittable:
                continue
            windows_emittable.append(sid)

            if pg.push_not_supported(doc):
                # The API refuses the download (409 PUSH_NOT_SUPPORTED), so no
                # customer receives this bundle. Its steps still reach a beacon.
                push_withdrawn.append(sid)
            else:
                try:
                    bundle = pg.generate_powershell(doc)
                except Exception as exc:  # noqa: BLE001 -- a generator crash is a finding
                    self.errors.append(f"bundle {sid}: generate_powershell raised {exc!r}")
                else:
                    self.add(
                        f"bundles/{_safe(sid)}.ps1", bundle.encode("utf-8"), "bundle",
                        f"generate_powershell({sid}) via {os.path.relpath(fp, REPO)}",
                        delivery="downloaded .ps1, run with -File",
                        scenario_id=sid,
                    )

            # The pull path dispatches through the orchestrator's own resolver.
            # It must hand a Windows beacon exactly the text (and shell) this
            # gate parses; any divergence means the beacon runs text nothing
            # checked, so it is an error, not a warning.
            pulled, _markers, _unres = resolve_steps_for_target(list(doc.get("steps") or []), "windows")
            pulled_by_id = {p.get("id"): p for p in pulled}
            for res in win.steps:
                rel_src = f"{os.path.relpath(fp, REPO)}#{res.step_id} ({res.source})"
                pulled_step = pulled_by_id.get(res.step_id) or {}
                if (pulled_step.get("command") or "") != res.command or (pulled_step.get("shell") or "") != res.shell:
                    pull_divergence.append({
                        "scenario_id": sid, "step_id": res.step_id, "resolved_from": res.source,
                    })
                if res.shell != "powershell":
                    cmd_steps.append({"scenario_id": sid, "step_id": res.step_id,
                                      "shell": res.shell, "source": rel_src})
                    continue
                self.add(
                    f"steps/{_safe(sid)}/{_safe(res.step_id)}.ps1",
                    UTF8_BOM + res.command.encode("utf-8"), "step", rel_src,
                    delivery="beacon -EncodedCommand (exact text) / push harness Set-Content -Encoding UTF8 then -File",
                    scenario_id=sid, step_id=res.step_id, resolved_from=res.source,
                )

        if rejected:
            self.warnings.append(f"{len(rejected)} scenario file(s) rejected by _parse_and_validate "
                                 f"(not served, so not rendered): {[r['file'] for r in rejected][:10]}")
        if unresolved_labelled:
            sample = [f"{u['scenario_id']}#{u['step_id']}" for u in unresolved_labelled][:10]
            self.warnings.append(
                f"{len(unresolved_labelled)} Windows-labelled step(s) resolve to no Windows command "
                f"(warn-only): {sample}")
        if pull_divergence:
            sample = [f"{d['scenario_id']}#{d['step_id']}" for d in pull_divergence][:10]
            self.errors.append(
                f"{len(pull_divergence)} windows step(s): the pull dispatch "
                f"(orchestrator.resolve_steps_for_target) hands the beacon different text or shell "
                f"than this gate parsed: {sample}")

        self.extra["scenarios"] = {
            "yaml_files": len(files), "loaded": loaded, "rejected": rejected,
            "windows_emittable": len(windows_emittable),
            "push_withdrawn": push_withdrawn,
        }
        self.extra["cmd_steps"] = cmd_steps
        self.extra["unresolved_windows_steps"] = {"count": len(unresolved_labelled), "steps": unresolved_labelled}
        self.extra["pull_divergence"] = {"count": len(pull_divergence), "steps": pull_divergence}
        self.fail_if_empty("bundle")
        self.fail_if_empty("step")

    # -- (e) beacon wrapper -------------------------------------------------

    def render_beacon(self) -> None:
        go = shutil.which("go")
        if go is None:
            self.errors.append("rendered 0 beacon-wrapper: no `go` on PATH")
            return
        dump = self.out / "go"
        dump.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [go, "test", "./executor", "-run", "^TestDumpPowerShellSamples$", "-count=1", "-v"],
            cwd=REPO / "agent", env={**os.environ, "CORTEXSIM_PS_DUMP": str(dump)},
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0 or "--- PASS: TestDumpPowerShellSamples" not in proc.stdout:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
            self.errors.append("rendered 0 beacon-wrapper: go test TestDumpPowerShellSamples did not PASS "
                               f"(rc={proc.returncode}): " + " | ".join(tail))
            return
        for f in sorted(dump.glob("*.ps1")):
            data = f.read_bytes()
            self.artifacts.append({
                "path": f"go/{f.name}", "kind": "beacon-wrapper",
                "source": "agent/executor TestDumpPowerShellSamples (powerShellExitWrapper / powerShellChainStepScript)",
                "delivery": "anchor stdin (console code page)" if f.name.startswith("chain_two_steps")
                            else "-EncodedCommand (exact text)",
                "bom": data.startswith(UTF8_BOM), "bytes": len(data), "not_powershell": None,
            })
        self.fail_if_empty("beacon-wrapper", "TestDumpPowerShellSamples wrote no .ps1")

    # -- (f) tracked .ps1 ---------------------------------------------------

    def render_repo_ps1(self) -> None:
        git = shutil.which("git")
        if git is None:
            self.errors.append("rendered 0 repo-ps1: no `git` on PATH")
            return
        proc = subprocess.run([git, "ls-files", "-z", "--", "*.ps1"], cwd=REPO,
                              capture_output=True, timeout=120)
        if proc.returncode != 0:
            self.errors.append(f"rendered 0 repo-ps1: git ls-files failed: "
                               f"{proc.stderr.decode('utf-8', 'replace').strip()}")
            return
        excluded: list[dict[str, str]] = []
        for raw in proc.stdout.split(b"\0"):
            if not raw:
                continue
            rel = raw.decode("utf-8")
            if rel.startswith("sources/"):
                continue
            if rel in REPO_PS1_EXCLUDE:
                excluded.append({"path": rel, "reason": REPO_PS1_EXCLUDE[rel]})
                continue
            src = REPO / rel
            if not src.is_file():
                self.errors.append(f"repo-ps1 {rel}: tracked but missing from the working tree")
                continue
            self.add(f"repo/{rel}", src.read_bytes(), "repo-ps1", f"git ls-files: {rel}",
                     delivery="as committed")
        self.extra["repo_ps1_excluded"] = excluded
        self.fail_if_empty("repo-ps1", "git ls-files '*.ps1' found nothing outside sources/ and the fixtures")

    # -- manifest -----------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        head = None
        with contextlib.suppress(Exception):
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                                  text=True, timeout=30).stdout.strip() or None
        counts = {k: self.count(k) for k in REQUIRED_KINDS}
        return {
            "schema": SCHEMA,
            "generated_by": "scripts/ci/render_powershell.py",
            "repo_head": head,
            "caveat": ("pwsh parse + 5.1 decode + PS7-construct denylist only. Not Windows PowerShell 5.1 "
                       "execution; nothing here has run on Windows."),
            "required_kinds": list(REQUIRED_KINDS),
            "counts": counts,
            "artifacts": self.artifacts,
            "errors": self.errors,
            "warnings": self.warnings,
            **self.extra,
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=Path, help="output directory (wiped and recreated)")
    ap.add_argument("--manifest", action="store_true",
                    help="also print manifest.json to stdout (it is always written to --out)")
    args = ap.parse_args(argv)

    _bootstrap_imports()
    out: Path = args.out.resolve()
    refusal = _prepare_out(out)
    if refusal:
        print(f"[render-ps] {refusal}", file=sys.stderr)
        return 2

    r = Renderer(out)
    r.render_installers()
    r.render_scenarios()
    r.render_beacon()
    r.render_repo_ps1()

    manifest = r.manifest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    for kind, n in manifest["counts"].items():
        print(f"[render-ps] {kind:<15} {n}")
    print(f"[render-ps] cmd-shell steps listed (not parsed): {len(manifest.get('cmd_steps', []))}")
    for w in r.warnings:
        print(f"[render-ps] WARN: {w}")
    for e in r.errors:
        print(f"[render-ps] ERROR: {e}")
    print(f"[render-ps] wrote {len(r.artifacts)} file(s) + manifest.json to {out}")
    if args.manifest:
        print(json.dumps(manifest, indent=2))
    return 1 if r.errors else 0


if __name__ == "__main__":
    sys.exit(main())

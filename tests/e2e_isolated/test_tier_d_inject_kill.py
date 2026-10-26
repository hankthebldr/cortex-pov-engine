"""Tier-D kill injector (deploy/tier-d/inject-kill.sh), exercised locally.

The injector normally runs inside the docker target. Its pid-selection and
timeout logic is plain process-tree work though, so it is driven here against
a real process tree (beacon -> anchor shell -> step) and a fake SimCore that
serves ``GET /api/runs/{id}``. No docker, no SimCore.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INJECT = REPO_ROOT / "deploy" / "tier-d" / "inject-kill.sh"

# The injector and this file's tree helpers both shell out to ps and pgrep
# (procps). The prod image does not ship procps, and `make test-backend-non-utc`
# runs the whole tests/ tree inside it: without the tools the tests marked with
# needs_procps die
# with FileNotFoundError, which reads as a regression rather than "cannot run".
needs_procps = pytest.mark.skipif(
    shutil.which("ps") is None or shutil.which("pgrep") is None,
    reason="needs ps and pgrep (procps), absent from the prod image",
)

STEP = "=== STEP {n}/3 · {sid} · T1059.004 · identity=www-data ===\nx\n--- exit_code=0 ---"


class FakeSimCore:
    def __init__(self):
        self.output = ""
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = json.dumps({"run_id": "r1", "output": outer.output},
                                  ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # silence
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def simcore():
    s = FakeSimCore()
    yield s
    s.close()


def _kids(pid):
    out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True).stdout
    pairs = [line.split() for line in out.splitlines() if line.strip()]
    return sorted(int(a) for a, b in pairs if int(b) == pid)


def _alive(pid):
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


@pytest.fixture
def tree():
    """Factory for beacon(bash) -> anchor(bash) -> step(sleep).

    Returns (beacon, anchor, step) pids.
    """
    procs = []

    def make(with_anchor=True):
        # The trailing `wait; sleep 120; :` keeps the "beacon" alive after its
        # anchor or step is killed, as the real beacon survives them.
        cmd = ("bash -c 'while :; do sleep 120; done' & sleep 0.3; wait -n; sleep 120; :"
               if with_anchor else "sleep 120 & wait")
        p = subprocess.Popen(["bash", "-c", cmd], start_new_session=True)
        procs.append(p)
        deadline = time.time() + 5
        while time.time() < deadline:
            kids = _kids(p.pid)
            if kids and (not with_anchor or _kids(kids[0])):
                break
            time.sleep(0.05)
        kids = _kids(p.pid)
        anchor = kids[0] if kids else None
        step = _kids(anchor)[0] if (with_anchor and anchor and _kids(anchor)) else None
        return p.pid, anchor, step

    yield make
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()


def _run(simcore, tmp_path, beacon, mode, *, after="step-01", at="step-02", timeout=6):
    status = tmp_path / "status"
    proc = subprocess.run(
        [str(INJECT), "--simcore", simcore.url, "--run-id", "r1", "--mode", mode,
         "--at-step", at, "--after-step", after, "--beacon-pid", str(beacon),
         "--timeout", str(timeout), "--status-file", str(status)],
        capture_output=True, text=True, timeout=timeout + 15)
    return proc, status.read_text().strip()


@needs_procps
def test_subshell_kills_only_the_step(simcore, tree, tmp_path):
    beacon, anchor, step = tree()
    simcore.output = STEP.format(n=1, sid="step-01")
    proc, status = _run(simcore, tmp_path, beacon, "subshell")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert status.startswith("KILLED subshell") and f"pid={step}" in status
    time.sleep(0.2)
    assert not _alive(step)
    assert _alive(anchor) and _alive(beacon)


@needs_procps
def test_anchor_kills_the_beacons_shell_child(simcore, tree, tmp_path):
    beacon, anchor, step = tree()
    simcore.output = STEP.format(n=1, sid="step-01")
    proc, status = _run(simcore, tmp_path, beacon, "anchor")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert status.startswith("KILLED anchor") and f"pid={anchor}" in status
    time.sleep(0.2)
    assert not _alive(anchor) and _alive(beacon)


@needs_procps
def test_beacon_kills_the_beacon(simcore, tree, tmp_path):
    beacon, anchor, step = tree()
    simcore.output = STEP.format(n=1, sid="step-01")
    proc, status = _run(simcore, tmp_path, beacon, "beacon")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert status.startswith("KILLED beacon") and f"pid={beacon}" in status
    time.sleep(0.2)
    assert not _alive(beacon)


@needs_procps
def test_waits_for_the_previous_step_before_firing(simcore, tree, tmp_path):
    beacon, anchor, step = tree()
    simcore.output = ""  # step-01 not done yet
    status = tmp_path / "status"
    p = subprocess.Popen(
        [str(INJECT), "--simcore", simcore.url, "--run-id", "r1", "--mode", "subshell",
         "--at-step", "step-02", "--after-step", "step-01", "--beacon-pid", str(beacon),
         "--timeout", "10", "--status-file", str(status)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    assert p.poll() is None, "fired before the previous step reported"
    assert _alive(step)
    simcore.output = STEP.format(n=1, sid="step-01")
    assert p.wait(timeout=10) == 0
    assert status.read_text().startswith("KILLED subshell")


@needs_procps
def test_times_out_and_fails_when_the_previous_step_never_arrives(simcore, tree, tmp_path):
    beacon, anchor, step = tree()
    simcore.output = ""
    t0 = time.time()
    proc, status = _run(simcore, tmp_path, beacon, "subshell", timeout=2)
    assert proc.returncode == 3
    assert status.startswith("TIMEOUT subshell")
    assert time.time() - t0 < 10, "must time out, not hang"
    assert _alive(step) and _alive(anchor) and _alive(beacon), "a timeout kills nothing"


@needs_procps
def test_no_anchor_times_out_loudly(simcore, tree, tmp_path):
    """Without cgo_anchor/causality the beacon runs each step as its own `sh -c`
    child and there is no anchor: anchor mode must fail, not pick a bystander."""
    beacon, anchor, step = tree(with_anchor=False)
    simcore.output = STEP.format(n=1, sid="step-01")
    proc, status = _run(simcore, tmp_path, beacon, "anchor", timeout=2)
    assert proc.returncode == 3
    assert status.startswith("TIMEOUT anchor")
    assert _alive(beacon)


def test_missed_when_the_target_step_finished_first(simcore, tmp_path):
    simcore.output = STEP.format(n=1, sid="step-01") + STEP.format(n=2, sid="step-02")
    # a beacon with no children: nothing killable, and step-02 already reported
    p = subprocess.Popen(["sleep", "60"])
    try:
        proc, status = _run(simcore, tmp_path, p.pid, "subshell", timeout=8)
    finally:
        p.kill()
        p.wait()
    assert proc.returncode == 4
    assert status.startswith("MISSED subshell")


def test_usage_errors_exit_2():
    for args in (["--mode", "bogus"], ["--mode", "beacon"]):
        proc = subprocess.run([str(INJECT), *args], capture_output=True, text=True)
        assert proc.returncode == 2


def _spawn(procs, cmd, want_kids):
    p = subprocess.Popen(["bash", "-c", cmd], start_new_session=True)
    procs.append(p)
    deadline = time.time() + 5
    while time.time() < deadline and len(_kids(p.pid)) < want_kids:
        time.sleep(0.05)
    return p


@needs_procps
def test_anchor_mode_waits_until_the_step_has_forked(simcore, tmp_path):
    """I1: an anchor with no child means no step subshell yet -- never fire."""
    fifo = tmp_path / "f"
    os.mkfifo(fifo)
    procs = []
    try:
        # the anchor blocks on a builtin read: it has NO children
        p = _spawn(procs, f"bash -c 'read x < {fifo}; :' & sleep 120; :", 1)
        simcore.output = STEP.format(n=1, sid="step-01")
        proc, status = _run(simcore, tmp_path, p.pid, "anchor", timeout=3)
        assert proc.returncode == 3 and status.startswith("TIMEOUT anchor")
        assert all(_alive(k) for k in _kids(p.pid))
    finally:
        for q in procs:
            try:
                os.killpg(q.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            q.wait()


@needs_procps
def test_two_shell_children_is_ambiguous_and_kills_nothing(simcore, tmp_path):
    procs = []
    try:
        p = _spawn(procs, "bash -c 'sleep 100; :' & bash -c 'sleep 100; :' & sleep 120; :", 3)
        simcore.output = STEP.format(n=1, sid="step-01")
        proc, status = _run(simcore, tmp_path, p.pid, "anchor", timeout=5)
        assert proc.returncode == 3 and "ambiguous" in status
        assert all(_alive(k) for k in _kids(p.pid))
    finally:
        for q in procs:
            try:
                os.killpg(q.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            q.wait()

"""Offline bundle generation + the stdlib-only sender.

The point of the bundle is that a DC can run a log campaign from a host inside
the customer network, where the collector lives. So the tests do the real
thing: generate a bundle, stand up a throwaway HTTP sink, run ``send.py`` in a
**subprocess with SimCore removed from the import path**, and assert the sink
received byte-identical records. If the bundle ever grows a SimCore import,
``test_send_py_runs_with_no_simcore_on_the_path`` fails.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from eal_simulator import Campaign
from eal_simulator.bundle import BundleError, generate_bundle, plan_campaign
from eal_simulator.delivery import BUNDLE_TAXONOMY_SOURCE
from eal_simulator.registry import get_default_registry


# --------------------------------------------------------------------------
# Throwaway collector
# --------------------------------------------------------------------------


class _Sink:
    """Minimal HTTP log collector: records every POST body it is handed."""

    def __init__(self, status_code: int = 202):
        self.status_code = status_code
        self.received: list[dict] = []
        self._server = None
        self._thread = None

    def __enter__(self):
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler contract
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                sink.received.append({
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                })
                self.send_response(sink.status_code)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, *args):  # silence the stderr spew
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/logs/v1/event"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _campaign(steps, allowlist=("127.0.0.1", "collector.cortexsim-canary.invalid")):
    return Campaign.model_validate({
        "campaign_id": "CMP-BUNDLE-001",
        "name": "offline bundle",
        "target_allowlist": list(allowlist),
        "dry_run": True,
        "steps": steps,
    })


def _k8s_step(collector_url, step_id="step-01", **extra):
    params = {
        "collector_url": collector_url,
        "provider": "kubernetes",
        "event_pattern": "pod_exec",
        "iterations": 2,
    }
    params.update(extra)
    return {"step_id": step_id, "plugin": "k8s_audit_emitter", "params": params}


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


class TestPlanCampaign:
    def test_pre_renders_records_per_iteration(self):
        plan = plan_campaign(
            _campaign([_k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event")]),
            get_default_registry(),
        )
        step = plan["manifest"]["steps"][0]
        assert len(step["iterations"]) == 2
        assert step["record_count"] == sum(i["record_count"] for i in step["iterations"])
        assert plan["manifest"]["total_records"] == step["record_count"]
        for iteration in step["iterations"]:
            assert plan["records"][
                Path(iteration["records_file"]).stem
            ], "records must be materialised for every iteration"

    def test_records_are_shape_true_and_carry_the_run_id(self):
        plan = plan_campaign(
            _campaign([_k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event")]),
            get_default_registry(),
        )
        records = plan["records"]["step-01-i1"]
        sim_id = plan["manifest"]["steps"][0]["iterations"][0]["simulation_run_id"]
        assert all(r["cortexsim_run_id"] == sim_id for r in records)
        assert all(r["dataset"] == "kubernetes_audit_logs" for r in records)

    def test_no_credential_is_ever_written_into_the_plan(self):
        plan = plan_campaign(
            _campaign([_k8s_step(
                "https://collector.cortexsim-canary.invalid/logs/v1/event",
                auth_token="super-secret-token",
            )]),
            get_default_registry(),
        )
        assert "super-secret-token" not in json.dumps(plan["manifest"])
        step = plan["manifest"]["steps"][0]
        assert step["auth_token_env"] == "CORTEXSIM_COLLECTOR_TOKEN_STEP_01"

    def test_live_protocol_steps_are_reported_not_silently_dropped(self):
        plan = plan_campaign(
            _campaign([
                _k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event"),
                {"step_id": "step-02", "plugin": "c2_http_beacon",
                 "params": {"target_url": "http://testmynids.org/uid/index.html"}},
            ]),
            get_default_registry(),
        )
        skipped = plan["manifest"]["skipped_steps"]
        assert [s["step_id"] for s in skipped] == ["step-02"]
        assert skipped[0]["reason"] == "not_offline_bundleable"

    def test_campaign_with_nothing_bundleable_is_refused(self):
        with pytest.raises(BundleError, match="no offline-bundleable steps"):
            plan_campaign(
                _campaign([{
                    "step_id": "step-01", "plugin": "c2_http_beacon",
                    "params": {"target_url": "http://testmynids.org/uid/index.html"},
                }]),
                get_default_registry(),
            )

    def test_email_emitter_is_bundleable_too(self):
        plan = plan_campaign(
            _campaign([{
                "step_id": "step-01", "plugin": "email_emitter",
                "params": {
                    "collector_url": "https://collector.cortexsim-canary.invalid/logs/v1/event",
                    "provider": "proofpoint",
                    "event_pattern": "phishing_link",
                    "iterations": 1,
                },
            }]),
            get_default_registry(),
        )
        assert plan["manifest"]["total_records"] > 0


# --------------------------------------------------------------------------
# Bundle artifact
# --------------------------------------------------------------------------


class TestGenerateBundle:
    def test_bundle_contains_everything_needed_to_run(self, tmp_path):
        result = generate_bundle(
            _campaign([_k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event")]),
            get_default_registry(), tmp_path,
        )
        bundle = Path(result["path"])
        for name in ("manifest.json", "send.py", "run.sh", "README.md"):
            assert (bundle / name).is_file(), name
        assert (bundle / "records").is_dir()
        assert (bundle / "run.sh").stat().st_mode & 0o111
        assert Path(result["archive"]).is_file()

    def test_archive_unpacks_to_the_same_tree(self, tmp_path):
        result = generate_bundle(
            _campaign([_k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event")]),
            get_default_registry(), tmp_path,
        )
        dest = tmp_path / "unpacked"
        with tarfile.open(result["archive"]) as tar:
            tar.extractall(dest)
        root = dest / result["bundle_id"]
        assert (root / "manifest.json").is_file()
        assert (root / "send.py").is_file()

    def test_bundle_carries_no_simcore_import(self, tmp_path):
        result = generate_bundle(
            _campaign([_k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event")]),
            get_default_registry(), tmp_path,
        )
        source = (Path(result["path"]) / "send.py").read_text()
        # The bundle must run on a clean Ubuntu host: stdlib imports only.
        imported = set(re.findall(r"^(?:from|import)\s+([\w.]+)", source, re.M))
        forbidden = {"eal_simulator", "httpx", "pydantic", "fastapi", "core"}
        assert not (imported & forbidden), imported & forbidden

    def test_send_py_reimplements_the_whole_shared_taxonomy(self, tmp_path):
        result = generate_bundle(
            _campaign([_k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event")]),
            get_default_registry(), tmp_path,
        )
        source = (Path(result["path"]) / "send.py").read_text()
        missing = {c for c in BUNDLE_TAXONOMY_SOURCE if f'"{c}"' not in source}
        assert not missing, f"bundle taxonomy drifted from SimCore's: {missing}"

    def test_readme_names_the_skipped_steps(self, tmp_path):
        result = generate_bundle(
            _campaign([
                _k8s_step("https://collector.cortexsim-canary.invalid/logs/v1/event"),
                {"step_id": "step-02", "plugin": "c2_http_beacon",
                 "params": {"target_url": "http://testmynids.org/uid/index.html"}},
            ]),
            get_default_registry(), tmp_path,
        )
        readme = (Path(result["path"]) / "README.md").read_text()
        assert "step-02" in readme
        assert "not_offline_bundleable" in readme

    def test_archive_path_rejects_traversal(self, tmp_path):
        from eal_simulator.bundle import archive_path

        assert archive_path(tmp_path, "../../etc/passwd") is None
        assert archive_path(tmp_path, "nope") is None


# --------------------------------------------------------------------------
# The real thing: run the bundle against a live sink, SimCore off the path.
# --------------------------------------------------------------------------


def _run_send(bundle: Path, *args, env=None):
    """Execute the bundle's send.py in a subprocess with SimCore unimportable."""
    import os

    child_env = dict(os.environ)
    # Blank PYTHONPATH: conftest puts core/ on sys.path for the test process,
    # and the bundle must not be able to lean on that.
    child_env["PYTHONPATH"] = ""
    child_env.update(env or {})
    return subprocess.run(
        [sys.executable, "send.py", "--manifest", "manifest.json", *args],
        cwd=bundle, capture_output=True, text=True, env=child_env, timeout=60,
    )


class TestBundleEndToEnd:
    def _bundle(self, tmp_path, collector_url, **step_kw):
        result = generate_bundle(
            _campaign([_k8s_step(collector_url, **step_kw)]),
            get_default_registry(), tmp_path,
        )
        return Path(result["path"]), result["manifest"]

    def test_send_py_runs_with_no_simcore_on_the_path(self, tmp_path):
        with _Sink() as sink:
            bundle, manifest = self._bundle(tmp_path, sink.url)
            proc = _run_send(bundle)

        assert proc.returncode == 0, proc.stderr
        # Real bytes arrived at a real socket.
        assert len(sink.received) == manifest["total_records"]
        assert all(r["path"] == "/logs/v1/event" for r in sink.received)

    def test_records_arrive_byte_identical_to_what_simcore_planned(self, tmp_path):
        with _Sink() as sink:
            bundle, _ = self._bundle(tmp_path, sink.url)
            planned = [
                json.loads(line)
                for f in sorted((bundle / "records").glob("*.ndjson"))
                for line in f.read_text().splitlines() if line.strip()
            ]
            _run_send(bundle)

        delivered = [json.loads(r["body"]) for r in sink.received]
        assert delivered == planned

    def test_results_json_reports_delivery(self, tmp_path):
        with _Sink() as sink:
            bundle, manifest = self._bundle(tmp_path, sink.url)
            _run_send(bundle)

        results = json.loads((bundle / "results.json").read_text())
        assert results["delivery"]["outcome"] == "success"
        assert results["delivery"]["records_delivered"] == manifest["total_records"]
        assert results["campaign_id"] == "CMP-BUNDLE-001"

    def test_rejecting_collector_yields_exit_2_and_a_taxonomy_code(self, tmp_path):
        with _Sink(status_code=401) as sink:
            bundle, _ = self._bundle(tmp_path, sink.url)
            proc = _run_send(bundle)

        assert proc.returncode == 2, proc.stdout
        results = json.loads((bundle / "results.json").read_text())
        assert results["delivery"]["outcome"] == "error"
        assert results["delivery"]["records_delivered"] == 0
        assert results["delivery"]["failures"][0]["code"] == "collector_unauthorized"
        assert "collector_unauthorized" in proc.stdout

    def test_unreachable_collector_is_classified_not_a_traceback(self, tmp_path):
        bundle, _ = self._bundle(
            tmp_path, "http://127.0.0.1:1/logs/v1/event",
        )
        proc = _run_send(bundle)
        assert proc.returncode == 2
        assert "Traceback" not in proc.stderr
        results = json.loads((bundle / "results.json").read_text())
        assert results["delivery"]["failures"][0]["code"] == "collector_unreachable"

    def test_dry_run_sends_nothing(self, tmp_path):
        with _Sink() as sink:
            bundle, _ = self._bundle(tmp_path, sink.url)
            proc = _run_send(bundle, "--dry-run")

        assert proc.returncode == 0
        assert sink.received == []
        assert "DRY RUN" in proc.stdout

    def test_token_comes_from_the_environment_not_the_bundle(self, tmp_path):
        with _Sink() as sink:
            bundle, _ = self._bundle(
                tmp_path, sink.url, auth_token="secret-not-in-bundle",
            )
            assert "secret-not-in-bundle" not in (bundle / "manifest.json").read_text()
            _run_send(bundle, env={"CORTEXSIM_COLLECTOR_TOKEN": "runtime-token"})

        assert sink.received[0]["headers"]["authorization"] == "Bearer runtime-token"

    def test_batch_mode_posts_one_ndjson_body(self, tmp_path):
        with _Sink() as sink:
            bundle, manifest = self._bundle(
                tmp_path, sink.url, batch=True, event_pattern="secret_enumeration",
            )
            proc = _run_send(bundle)

        assert proc.returncode == 0
        # One POST per iteration rather than one per record.
        assert len(sink.received) == len(manifest["steps"][0]["iterations"])
        assert sink.received[0]["headers"]["content-type"] == "application/x-ndjson"

    def test_gzip_mode_is_decodable_by_the_collector(self, tmp_path):
        import gzip

        with _Sink() as sink:
            bundle, _ = self._bundle(tmp_path, sink.url, compress=True)
            proc = _run_send(bundle)

        assert proc.returncode == 0
        assert sink.received[0]["headers"]["content-encoding"] == "gzip"
        assert json.loads(gzip.decompress(sink.received[0]["body"]))["dataset"] == (
            "kubernetes_audit_logs"
        )

    def test_collector_url_override_redirects_the_whole_bundle(self, tmp_path):
        with _Sink() as sink:
            bundle, _ = self._bundle(
                tmp_path, "https://collector.cortexsim-canary.invalid/logs/v1/event",
            )
            proc = _run_send(bundle, "--collector-url", sink.url)

        assert proc.returncode == 0
        assert sink.received

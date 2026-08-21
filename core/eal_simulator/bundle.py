"""
Offline bundle generator — run an EAL log campaign where the collector lives.

The EAL simulator used to be able to emit from exactly one place: SimCore's own
process. That is the wrong place. The XSIAM Broker VM / HTTP log collector sits
*inside* the customer network, usually unreachable from wherever a DC parked
SimCore, and a log-streamer that cannot reach the collector validates nothing.

So a campaign can now be exported as a self-contained bundle the DC copies to a
jumpbox inside the customer network and runs there. The design constraint is the
same one push bundles carry: **no SimCore dependency at run time**. That is
achievable here because the log-streamer families build their records with pure
functions — SimCore pre-renders every record at bundle time and the bundle only
has to POST bytes, which ``urllib`` from the stdlib does fine on a clean Ubuntu
22.04 host with no pip install.

Bundle layout::

    <bundle_id>/
      manifest.json          campaign + per-step collector config + record index
      records/<step>-iNN.ndjson   pre-rendered, shape-true log records
      send.py                stdlib-only sender (urllib/gzip/ssl/json)
      run.sh                 entrypoint
      README.md              what this is, how to run it, what the codes mean

Deliberate omissions: **no credential is ever written into a bundle**. The
manifest names an environment variable; the operator exports the ingestion
token on the jumpbox. And the sender never invents a record — what it POSTs is
byte-identical to what SimCore would have sent, so a detection that fires on
the bundle fires on the online path.

Which steps qualify: any plugin exposing ``plan_offline_records`` (the whole
analytics log-streamer family plus ``email_emitter``). Everything else — C2
beacons, DNS tunnels, Playwright browser drives — is reported in
``skipped_steps`` with a reason rather than silently dropped, because a bundle
that quietly omits half a campaign is the same class of lie as a green run that
posted nothing.
"""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .campaign import Campaign
from .collector import resolve_step_collector
from .registry import PluginRegistry


logger = logging.getLogger("cortexsim.eal.bundle")

BUNDLE_VERSION = 1

#: Env var the bundle reads the ingestion token from. Named, never baked.
DEFAULT_TOKEN_ENV = "CORTEXSIM_COLLECTOR_TOKEN"


class BundleError(RuntimeError):
    """Raised when a campaign cannot produce a runnable bundle."""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _token_env_name(step_id: str) -> str:
    """Per-step override env var, e.g. ``CORTEXSIM_COLLECTOR_TOKEN_STEP_01``."""
    suffix = step_id.upper().replace("-", "_")
    return f"{DEFAULT_TOKEN_ENV}_{suffix}"


def plan_campaign(campaign: Campaign, registry: PluginRegistry) -> dict[str, Any]:
    """Pre-render every bundleable step's records; return the bundle manifest.

    Pure — touches no filesystem — so the plan can be inspected (and unit
    tested) without writing a bundle.
    """
    steps: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    records: dict[str, list[dict[str, Any]]] = {}

    for step in campaign.steps:
        if not registry.has(step.plugin):
            skipped.append({
                "step_id": step.step_id, "plugin": step.plugin,
                "reason": "plugin_not_registered",
            })
            continue
        plugin_cls = registry.get(step.plugin)
        if not getattr(plugin_cls, "supports_offline_bundle", False):
            skipped.append({
                "step_id": step.step_id, "plugin": step.plugin,
                "reason": "not_offline_bundleable",
                "detail": (
                    "plugin emits live protocol traffic rather than pre-renderable "
                    "log records; run it from an agent or from SimCore directly"
                ),
            })
            continue
        try:
            params = plugin_cls.validate_params(step.params)
        except Exception as exc:
            skipped.append({
                "step_id": step.step_id, "plugin": step.plugin,
                "reason": "params_invalid", "detail": str(exc),
            })
            continue

        target = resolve_step_collector(
            step.step_id, step.plugin, params,
            target_allowlist=list(campaign.target_allowlist),
        )
        if target is None:
            skipped.append({
                "step_id": step.step_id, "plugin": step.plugin,
                "reason": "no_collector",
            })
            continue

        plugin = plugin_cls()
        iterations_meta: list[dict[str, Any]] = []
        total_records = 0
        for i in range(int(getattr(params, "iterations", 1) or 1)):
            sim_run_id = f"cortexsim-{uuid.uuid4()}-i{i + 1}"
            built = plugin.plan_offline_records(
                params, sim_run_id=sim_run_id, iteration=i + 1,
            )
            key = f"{step.step_id}-i{i + 1}"
            records[key] = list(built)
            total_records += len(built)
            iterations_meta.append({
                "iteration": i + 1,
                "simulation_run_id": sim_run_id,
                "records_file": f"records/{key}.ndjson",
                "record_count": len(built),
            })

        steps.append({
            "step_id": step.step_id,
            "plugin": step.plugin,
            "description": step.description,
            "collector_url": target.url,
            "dataset": target.dataset,
            "auth_header": target.auth_header,
            "auth_scheme": target.auth_scheme,
            "auth_token_env": _token_env_name(step.step_id),
            "batch": target.batch,
            "compress": target.compress,
            "request_timeout": target.request_timeout,
            "sleep_seconds": float(getattr(params, "sleep_seconds", 0.0) or 0.0),
            "user_agent": getattr(params, "user_agent", None),
            "on_error": step.on_error,
            "iterations": iterations_meta,
            "record_count": total_records,
            "collector_warnings": target.warnings,
        })

    if not steps:
        raise BundleError(
            "campaign has no offline-bundleable steps — the log-streamer "
            "families (analytics emitters, email_emitter) are the plugins that "
            "pre-render records; live-protocol plugins must run from SimCore or "
            "an agent"
        )

    manifest = {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": campaign.campaign_id,
        "campaign_name": campaign.name,
        "authorized_by": campaign.authorized_by,
        "simulation_authorized": campaign.simulation_authorized,
        "target_allowlist": list(campaign.target_allowlist),
        "verify_tls": bool(getattr(campaign, "verify_tls", False)),
        "default_token_env": DEFAULT_TOKEN_ENV,
        "steps": steps,
        "skipped_steps": skipped,
        "total_records": sum(s["record_count"] for s in steps),
    }
    return {"manifest": manifest, "records": records}


def generate_bundle(
    campaign: Campaign,
    registry: PluginRegistry,
    output_dir: Path,
    *,
    bundle_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write the bundle directory + tar.gz under ``output_dir``."""
    plan = plan_campaign(campaign, registry)
    manifest = plan["manifest"]

    bundle_id = bundle_id or str(uuid.uuid4())
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir = output_dir / bundle_id
    if bundle_dir.exists():
        raise BundleError(f"bundle directory already exists: {bundle_dir}")
    bundle_dir.mkdir()

    try:
        records_dir = bundle_dir / "records"
        records_dir.mkdir()
        for key, events in plan["records"].items():
            body = "\n".join(json.dumps(e, separators=(",", ":")) for e in events)
            (records_dir / f"{key}.ndjson").write_text(body + "\n", encoding="utf-8")

        manifest["bundle_id"] = bundle_id
        (bundle_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        )
        (bundle_dir / "send.py").write_text(SEND_PY, encoding="utf-8")
        (bundle_dir / "run.sh").write_text(RUN_SH, encoding="utf-8")
        (bundle_dir / "run.sh").chmod(0o755)
        (bundle_dir / "send.py").chmod(0o755)
        (bundle_dir / "README.md").write_text(
            _render_readme(manifest), encoding="utf-8",
        )

        archive = output_dir / f"{bundle_id}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_id)
    except Exception as exc:
        shutil.rmtree(bundle_dir, ignore_errors=True)
        raise BundleError(f"bundle generation failed: {exc}") from exc

    logger.info(
        "eal bundle generated id=%s campaign=%s steps=%d records=%d",
        bundle_id, campaign.campaign_id, len(manifest["steps"]),
        manifest["total_records"],
    )
    return {
        "bundle_id": bundle_id,
        "campaign_id": campaign.campaign_id,
        "path": str(bundle_dir),
        "archive": str(archive),
        "manifest": manifest,
        "download_url": f"/api/eal/bundles/{bundle_id}/download",
    }


def archive_path(output_dir: Path, bundle_id: str) -> Optional[Path]:
    """Path to a generated archive, or None. Rejects traversal in ``bundle_id``."""
    if not bundle_id or "/" in bundle_id or ".." in bundle_id:
        return None
    archive = output_dir / f"{bundle_id}.tar.gz"
    return archive if archive.is_file() else None


def _render_readme(manifest: dict[str, Any]) -> str:
    lines = [
        f"# CortexSim EAL offline bundle — {manifest['campaign_id']}",
        "",
        f"{manifest['campaign_name']}",
        "",
        "Self-contained log-streamer bundle. Copy it to a host **inside the "
        "customer network** that can reach the collector (XSIAM Broker VM HTTP "
        "applet, native cloud collector, or a Cribl/nginx forwarder), then run "
        "it. Nothing here talks to SimCore: only `python3` (3.8+) from the "
        "stdlib is required — no pip install, no network access to anything but "
        "the collector.",
        "",
        "## Run",
        "",
        "```bash",
        f"export {manifest['default_token_env']}='<ingestion-token>'   # if the collector needs one",
        "./run.sh --dry-run     # print what would be posted, send nothing",
        "./run.sh               # send for real, writes results.json",
        "```",
        "",
        "Exit code: `0` every record delivered · `1` partial · `2` nothing "
        "delivered · `3` usage error.",
        "",
        "## What it sends",
        "",
        f"{manifest['total_records']} pre-rendered, shape-true records across "
        f"{len(manifest['steps'])} step(s). The records are byte-identical to "
        "what SimCore would have POSTed, so a detection that fires here fires "
        "on the online path too.",
        "",
        "| step | plugin | dataset | collector | records |",
        "|------|--------|---------|-----------|---------|",
    ]
    for step in manifest["steps"]:
        lines.append(
            f"| `{step['step_id']}` | `{step['plugin']}` | "
            f"`{step.get('dataset') or '—'}` | `{step['collector_url']}` | "
            f"{step['record_count']} |"
        )
    if manifest["skipped_steps"]:
        lines += [
            "",
            "## Steps NOT in this bundle",
            "",
            "These campaign steps emit live protocol traffic (or failed "
            "validation) and cannot be pre-rendered. Run them from SimCore or "
            "an agent on the target endpoint.",
            "",
        ]
        for skipped in manifest["skipped_steps"]:
            lines.append(
                f"- `{skipped['step_id']}` (`{skipped['plugin']}`) — "
                f"{skipped['reason']}"
            )
    lines += [
        "",
        "## Result codes",
        "",
        "`results.json` reports every POST under the same delivery taxonomy "
        "SimCore uses, so an operator can tell a *rejected payload* from an "
        "*unreachable network* without reading a stack trace:",
        "",
        "| code | meaning |",
        "|------|---------|",
        "| `collector_rejected` | collector answered 4xx — wrong path, content-type or dataset |",
        "| `collector_unauthorized` | 401/403/407 — wrong or missing ingestion token |",
        "| `collector_throttled` | 429 — lower burst/iterations or enable batch |",
        "| `collector_redirected` | 3xx — proxy/captive portal, not an ingestion endpoint |",
        "| `collector_unavailable` | 503 — collector reports itself down |",
        "| `collector_error` | 5xx — collector-side failure, records not ingested |",
        "| `collector_unreachable` | nothing answered — DNS, routing or firewall |",
        "| `collector_timeout` | connect/read timed out |",
        "| `collector_tls_error` | TLS handshake failed |",
        "| `transport_error` | other transport failure before a response |",
        "",
        "Credentials are never written into this bundle — the token is read "
        f"from `${manifest['default_token_env']}` (or a per-step "
        f"`{manifest['default_token_env']}_STEP_NN`) at run time.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bundle payloads. These are DATA, not imports: everything below runs on the
# jumpbox with no SimCore on sys.path.
# ---------------------------------------------------------------------------


RUN_SH = """#!/usr/bin/env bash
# CortexSim EAL offline bundle entrypoint. No SimCore, no pip install —
# python3 from the base image is the only requirement.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[cortexsim-eal] python3 not found — install python3 and re-run" >&2
    exit 3
fi

exec python3 send.py --manifest manifest.json "$@"
"""


SEND_PY = '''#!/usr/bin/env python3
"""
CortexSim EAL offline sender — POSTs pre-rendered log records to a collector.

Runs on a clean Ubuntu 22.04 host with nothing but the Python standard library.
It deliberately duplicates SimCore's delivery taxonomy as a literal table below
rather than importing it: this script must work with no CortexSim on the host.

Usage:
    ./run.sh [--dry-run] [--verify-tls] [--collector-url URL] [--out results.json]

Exit codes: 0 all delivered, 1 partial, 2 nothing delivered, 3 usage error.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --- delivery taxonomy (mirrors core/eal_simulator/delivery.py) -------------
COLLECTOR_REJECTED = "collector_rejected"
COLLECTOR_UNAUTHORIZED = "collector_unauthorized"
COLLECTOR_THROTTLED = "collector_throttled"
COLLECTOR_REDIRECTED = "collector_redirected"
COLLECTOR_ERROR = "collector_error"
COLLECTOR_UNAVAILABLE = "collector_unavailable"
COLLECTOR_UNREACHABLE = "collector_unreachable"
COLLECTOR_TIMEOUT = "collector_timeout"
COLLECTOR_TLS_ERROR = "collector_tls_error"
TRANSPORT_ERROR = "transport_error"

REMEDIATION = {
    COLLECTOR_REJECTED: "Collector refused the payload — check the URL path, "
                        "content-type and that the target dataset exists.",
    COLLECTOR_UNAUTHORIZED: "Collector rejected the credential — check the "
                            "ingestion token env var, header name and scheme.",
    COLLECTOR_THROTTLED: "Collector is rate-limiting — slow the send down or "
                         "regenerate the bundle with batch mode on.",
    COLLECTOR_REDIRECTED: "Collector answered with a redirect (proxy/captive "
                          "portal). Point at the final ingestion endpoint.",
    COLLECTOR_ERROR: "Collector returned a server error — records not ingested.",
    COLLECTOR_UNAVAILABLE: "Collector reports itself unavailable — confirm the "
                           "Broker VM HTTP applet is running.",
    COLLECTOR_UNREACHABLE: "Nothing answered — check DNS, routing and firewall "
                           "between this host and the collector.",
    COLLECTOR_TIMEOUT: "Connect/read timed out — raise the timeout or check for "
                       "a silently dropping firewall.",
    COLLECTOR_TLS_ERROR: "TLS handshake failed — omit --verify-tls when the "
                         "NGFW MitMs outbound traffic, or trust the CA.",
    TRANSPORT_ERROR: "Transport failure before a response was read.",
}


def classify_status(status_code):
    if 200 <= status_code < 300:
        return None
    if 300 <= status_code < 400:
        return COLLECTOR_REDIRECTED
    if status_code in (401, 403, 407):
        return COLLECTOR_UNAUTHORIZED
    if status_code == 429:
        return COLLECTOR_THROTTLED
    if 400 <= status_code < 500:
        return COLLECTOR_REJECTED
    if status_code == 503:
        return COLLECTOR_UNAVAILABLE
    return COLLECTOR_ERROR


def classify_exception(exc):
    names = {cls.__name__ for cls in type(exc).__mro__}
    text = str(exc).lower()
    if names & {"SSLError", "SSLCertVerificationError"}:
        return COLLECTOR_TLS_ERROR
    if names & {"timeout"} or "timed out" in text:
        return COLLECTOR_TIMEOUT
    if names & {"ConnectionRefusedError", "gaierror", "ConnectionError"}:
        return COLLECTOR_UNREACHABLE
    if names & {"URLError"}:
        if "ssl" in text or "certificate" in text:
            return COLLECTOR_TLS_ERROR
        if "timed out" in text:
            return COLLECTOR_TIMEOUT
        return COLLECTOR_UNREACHABLE
    return TRANSPORT_ERROR


# --- ledger ----------------------------------------------------------------
class Ledger(object):
    def __init__(self):
        self.records_attempted = 0
        self.records_delivered = 0
        self.requests_attempted = 0
        self.requests_delivered = 0
        self.bytes_attempted = 0
        self.bytes_delivered = 0
        self.status_counts = {}
        self.failures = {}

    def response(self, status_code, records, wire_bytes):
        self.records_attempted += records
        self.requests_attempted += 1
        self.bytes_attempted += wire_bytes
        key = str(status_code)
        self.status_counts[key] = self.status_counts.get(key, 0) + 1
        code = classify_status(status_code)
        if code is None:
            self.records_delivered += records
            self.requests_delivered += 1
            self.bytes_delivered += wire_bytes
            return None
        self._fail(code, records, status_code, None)
        return code

    def exception(self, exc, records, wire_bytes):
        self.records_attempted += records
        self.requests_attempted += 1
        self.bytes_attempted += wire_bytes
        self.status_counts["0"] = self.status_counts.get("0", 0) + 1
        code = classify_exception(exc)
        self._fail(code, records, None, "%s: %s" % (type(exc).__name__, exc))
        return code

    def _fail(self, code, records, status_code, error):
        bucket = self.failures.setdefault(code, {
            "code": code, "count": 0, "records": 0,
            "status_code": None, "sample_error": None,
            "remediation": REMEDIATION.get(code, ""),
        })
        bucket["count"] += 1
        bucket["records"] += records
        if status_code is not None:
            bucket["status_code"] = status_code
        if error and not bucket["sample_error"]:
            bucket["sample_error"] = error

    @property
    def outcome(self):
        if self.records_attempted == 0:
            return "success"
        if self.records_delivered == 0:
            return "error"
        if self.records_delivered < self.records_attempted:
            return "partial"
        return "success"

    def to_dict(self):
        return {
            "outcome": self.outcome,
            "records_attempted": self.records_attempted,
            "records_delivered": self.records_delivered,
            "requests_attempted": self.requests_attempted,
            "requests_delivered": self.requests_delivered,
            "bytes_attempted": self.bytes_attempted,
            "bytes_delivered": self.bytes_delivered,
            "status_counts": dict(self.status_counts),
            "failures": sorted(
                self.failures.values(), key=lambda b: -b["records"]
            ),
        }


# --- transport --------------------------------------------------------------
def build_ssl_context(verify_tls):
    ctx = ssl.create_default_context()
    if not verify_tls:
        # Default off: POVs routinely MitM outbound traffic through the
        # customer NGFW with a cert this host does not trust.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def post(url, body, headers, timeout, ssl_ctx):
    req = urllib.request.Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            resp.read()
            return int(resp.status), None
    except urllib.error.HTTPError as exc:
        # An HTTP error IS a response — classify it by status, not as transport.
        try:
            exc.read()
        except Exception:
            pass
        return int(exc.code), None
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError) as exc:
        inner = getattr(exc, "reason", None)
        return None, inner if isinstance(inner, BaseException) else exc


def step_headers(step, manifest, token):
    headers = {
        "content-type": "application/x-ndjson" if step["batch"] else "application/json",
        "x-simulation-source": "cortexsim-eal-offline-bundle/1.0",
        "x-simulation-campaign-id": manifest["campaign_id"],
    }
    if step.get("user_agent"):
        headers["user-agent"] = step["user_agent"]
    if token and step.get("auth_header"):
        scheme = (step.get("auth_scheme") or "").strip()
        headers[step["auth_header"]] = ("%s %s" % (scheme, token)).strip()
    return headers


def resolve_token(step, manifest):
    """Per-step env var wins, then the bundle-wide one. Never from the bundle."""
    for name in (step.get("auth_token_env"), manifest.get("default_token_env")):
        if name and os.environ.get(name):
            return os.environ[name]
    return None


def load_records(base, relative):
    lines = (base / relative).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def run_step(step, manifest, base, args, ssl_ctx):
    ledger = Ledger()
    token = resolve_token(step, manifest)
    headers = step_headers(step, manifest, token)
    url = args.collector_url or step["collector_url"]
    timeout = step.get("request_timeout") or 15.0

    for index, iteration in enumerate(step["iterations"]):
        records = load_records(base, iteration["records_file"])
        per_headers = dict(headers)
        per_headers["x-simulation-run-id"] = iteration["simulation_run_id"]

        if step["batch"]:
            payloads = [("\\n".join(json.dumps(r) for r in records).encode("utf-8"),
                         len(records))]
        else:
            payloads = [(json.dumps(r).encode("utf-8"), 1) for r in records]

        for body, count in payloads:
            send_headers = dict(per_headers)
            if step["compress"]:
                body = gzip.compress(body)
                send_headers["content-encoding"] = "gzip"
            if args.dry_run:
                ledger.records_attempted += count
                print("  [dry-run] %s -> %s (%d record(s), %d bytes)"
                      % (step["step_id"], url, count, len(body)))
                continue
            status, exc = post(url, body, send_headers, timeout, ssl_ctx)
            if exc is not None:
                code = ledger.exception(exc, count, len(body))
                print("  [FAIL] %s %s: %s" % (step["step_id"], code, exc))
            else:
                code = ledger.response(status, count, len(body))
                if code:
                    print("  [FAIL] %s status=%s %s" % (step["step_id"], status, code))

        sleep_seconds = step.get("sleep_seconds") or 0.0
        if sleep_seconds > 0 and index < len(step["iterations"]) - 1 and not args.dry_run:
            time.sleep(sleep_seconds)

    return ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description="CortexSim EAL offline sender")
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--out", default="results.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan, send nothing")
    parser.add_argument("--verify-tls", action="store_true",
                        help="verify the collector TLS certificate (off by "
                             "default so an NGFW MitM does not break the run)")
    parser.add_argument("--collector-url",
                        help="override every step's collector URL")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print("manifest not found: %s" % manifest_path, file=sys.stderr)
        return 3
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent

    ssl_ctx = build_ssl_context(args.verify_tls)
    print("CortexSim EAL offline bundle — campaign %s (%d step(s), %d record(s))"
          % (manifest["campaign_id"], len(manifest["steps"]),
             manifest.get("total_records", 0)))

    step_results = []
    totals = Ledger()
    for step in manifest["steps"]:
        print("- %s (%s) -> %s"
              % (step["step_id"], step["plugin"],
                 args.collector_url or step["collector_url"]))
        ledger = run_step(step, manifest, base, args, ssl_ctx)
        step_results.append({
            "step_id": step["step_id"],
            "plugin": step["plugin"],
            "collector_url": args.collector_url or step["collector_url"],
            "dataset": step.get("dataset"),
            "delivery": ledger.to_dict(),
        })
        totals.records_attempted += ledger.records_attempted
        totals.records_delivered += ledger.records_delivered
        totals.requests_attempted += ledger.requests_attempted
        totals.requests_delivered += ledger.requests_delivered
        totals.bytes_attempted += ledger.bytes_attempted
        totals.bytes_delivered += ledger.bytes_delivered
        for key, value in ledger.status_counts.items():
            totals.status_counts[key] = totals.status_counts.get(key, 0) + value
        for code, bucket in ledger.failures.items():
            merged = totals.failures.setdefault(code, {
                "code": code, "count": 0, "records": 0, "status_code": None,
                "sample_error": None, "remediation": bucket["remediation"],
            })
            merged["count"] += bucket["count"]
            merged["records"] += bucket["records"]
            merged["status_code"] = merged["status_code"] or bucket["status_code"]
            merged["sample_error"] = merged["sample_error"] or bucket["sample_error"]

    summary = totals.to_dict()
    results = {
        "bundle_id": manifest.get("bundle_id"),
        "campaign_id": manifest["campaign_id"],
        "dry_run": bool(args.dry_run),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "delivery": summary,
        "steps": step_results,
        "skipped_steps": manifest.get("skipped_steps", []),
    }
    # A jumpbox often hands you a read-only or full working directory. Losing
    # results.json is annoying; losing the CONSOLE VERDICT to a traceback after
    # every record already landed would be a lie in the other direction — so
    # the write is best-effort and the delivery outcome still decides the exit.
    try:
        Path(args.out).write_text(json.dumps(results, indent=2) + "\\n", encoding="utf-8")
        wrote = "Wrote %s." % args.out
    except OSError as exc:
        wrote = ("Could NOT write %s (%s) — the delivery result below is still "
                 "authoritative; re-run with --out /writable/path to keep it."
                 % (args.out, exc))

    if args.dry_run:
        print("DRY RUN — %d record(s) planned, nothing sent. %s"
              % (summary["records_attempted"], wrote))
        return 0

    print("%s — %d/%d record(s) accepted by the collector (%d bytes). %s"
          % (summary["outcome"].upper(), summary["records_delivered"],
             summary["records_attempted"], summary["bytes_delivered"], wrote))
    for bucket in summary["failures"]:
        print("  %s: %d record(s) lost — %s"
              % (bucket["code"], bucket["records"], bucket["remediation"]))

    if summary["outcome"] == "success":
        return 0
    if summary["outcome"] == "partial":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''

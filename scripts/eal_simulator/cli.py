#!/usr/bin/env python3
"""
CortexSim EAL Traffic Simulator — operator CLI.

Usage:
    python -m scripts.eal_simulator.cli list-plugins
    python -m scripts.eal_simulator.cli describe c2_http_beacon
    python -m scripts.eal_simulator.cli run path/to/campaign.yaml [--live]
    python -m scripts.eal_simulator.cli worker

The ``run`` subcommand is the primary entry point — it reads a campaign YAML,
loads built-in plugins, and executes the campaign synchronously, streaming the
ECS audit log to stdout. ``--live`` flips ``dry_run`` off (the spec must also
declare ``simulation_authorized: true`` and a non-empty ``target_allowlist``).

The ``worker`` subcommand is a placeholder for the K3s worker pod's entrypoint
when running with a Celery-style queue. In the in-memory queue case it simply
keeps the pod healthy so the API gateway can submit background tasks to it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml


# Ensure the in-tree core/ directory is importable when running as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE = _REPO_ROOT / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from eal_simulator import (  # noqa: E402
    AuditLogger,
    Campaign,
    CampaignExecutor,
    get_default_registry,
)


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_list_plugins(_args: argparse.Namespace) -> int:
    reg = get_default_registry()
    _print_json({"plugins": reg.manifest(), "total": len(reg)})
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    reg = get_default_registry()
    if not reg.has(args.plugin):
        print(f"error: unknown plugin '{args.plugin}'", file=sys.stderr)
        print(f"available: {reg.names()}", file=sys.stderr)
        return 2
    _print_json(reg.get(args.plugin).metadata())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    path = Path(args.campaign)
    if not path.exists():
        print(f"error: campaign file not found: {path}", file=sys.stderr)
        return 2

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        print(f"error: campaign file must define a YAML mapping at the root", file=sys.stderr)
        return 2

    if args.live:
        raw["dry_run"] = False
    elif "dry_run" not in raw:
        raw["dry_run"] = True

    try:
        campaign = Campaign.model_validate(raw)
    except Exception as exc:
        print(f"error: invalid campaign spec: {exc}", file=sys.stderr)
        return 2

    audit = AuditLogger(file_path=args.audit_file)
    executor = CampaignExecutor(audit=audit)

    state = asyncio.run(executor.execute(campaign))
    _print_json(state.to_dict())
    audit.close()
    if state.status == "complete":
        return 0
    return 1


def cmd_worker(_args: argparse.Namespace) -> int:
    """Keep the worker pod healthy.

    With the default in-memory task queue, simulation work runs inside the
    API pod — the worker is only needed for the (optional) Celery deployment.
    We block on a stdin-friendly loop and emit a heartbeat every 30s so log
    aggregation has something to scrape.
    """
    print(json.dumps({"event": "worker_started", "queue": "in-memory placeholder"}))
    try:
        while True:
            time.sleep(30)
            print(json.dumps({"event": "worker_heartbeat", "ts": time.time()}))
    except KeyboardInterrupt:
        print(json.dumps({"event": "worker_stopped"}))
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cortexsim-eal",
        description="CortexSim EAL Traffic Simulator CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-plugins", help="List registered plugins as JSON")

    p_describe = sub.add_parser("describe", help="Print a plugin's metadata + params schema")
    p_describe.add_argument("plugin", help="Plugin name (Meta.name)")

    p_run = sub.add_parser("run", help="Execute a campaign YAML")
    p_run.add_argument("campaign", help="Path to campaign YAML")
    p_run.add_argument("--live", action="store_true", help="Run live (sets dry_run=false)")
    p_run.add_argument("--audit-file", default=None, help="Append ECS audit lines to this path")

    sub.add_parser("worker", help="Long-running worker pod entrypoint")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "list-plugins": cmd_list_plugins,
        "describe": cmd_describe,
        "run": cmd_run,
        "worker": cmd_worker,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

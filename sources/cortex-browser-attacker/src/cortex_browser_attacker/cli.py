"""
cortex-browser-attacker CLI.

Subcommands:
    list-actions      print built-in action names + their params schema
    validate          parse a campaign YAML; report errors without running
    run               execute a campaign; write JSONL to --out

Examples:

    cortex-browser-attacker list-actions
    cortex-browser-attacker validate --campaign scenarios/browser/campaigns/cred-paste.yml
    cortex-browser-attacker run \\
        --campaign scenarios/browser/campaigns/cred-paste.yml \\
        --browser-channel chromium --headless \\
        --out /tmp/browser-001.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import yaml

from .actions import ACTION_REGISTRY
from .browser import BrowserDriver, PlaywrightDriver, StubDriver
from .campaign import BrowserCampaign
from .runner import Runner, open_jsonl_writer


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cortex-browser-attacker",
        description=(
            "Playwright-driven browser action runner for CortexSim Prisma "
            "Browser validation."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-actions", help="List built-in actions + their params")
    p_list.add_argument("--format", default="json", choices=["json", "names"])

    p_validate = sub.add_parser("validate", help="Parse a campaign YAML")
    p_validate.add_argument("--campaign", required=True)

    p_run = sub.add_parser("run", help="Execute a campaign")
    p_run.add_argument("--campaign", required=True)
    p_run.add_argument(
        "--browser-channel", default=None,
        choices=["prisma", "chromium", "stub"],
        help="Override the campaign's browser_channel. 'stub' never spins "
             "up a real browser (intended for unit tests / dry-validation).",
    )
    p_run.add_argument("--headless", action="store_true", default=None)
    p_run.add_argument("--no-headless", action="store_false", dest="headless")
    p_run.add_argument("--live", action="store_true",
                       help="Override the campaign's dry_run to false.")
    p_run.add_argument("--out", default="-",
                       help="Output JSONL path; '-' for stdout (default).")

    return parser.parse_args(argv)


def _load_campaign(path: str) -> BrowserCampaign:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"campaign root must be a mapping: {path}")
    return BrowserCampaign.model_validate(raw)


def cmd_list_actions(args: argparse.Namespace) -> int:
    if args.format == "names":
        for name in sorted(ACTION_REGISTRY):
            print(name)
        return 0
    out = {
        name: entry.params.model_json_schema()
        for name, entry in sorted(ACTION_REGISTRY.items())
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        campaign = _load_campaign(args.campaign)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2),
              file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "campaign_id": campaign.campaign_id,
        "actions": [a.action for a in campaign.actions],
        "browser_channel": campaign.browser_channel,
        "dry_run": campaign.dry_run,
    }, indent=2))
    return 0


def _build_driver(channel: str, *, headless: bool) -> BrowserDriver:
    if channel == "stub":
        return StubDriver()
    return PlaywrightDriver(channel=channel, headless=headless)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        campaign = _load_campaign(args.campaign)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.live:
        # Pydantic's validator forbids dry_run=false without the auth
        # block; the operator must have authored those fields already.
        campaign = campaign.model_copy(update={"dry_run": False})

    channel = args.browser_channel or campaign.browser_channel
    headless = campaign.headless if args.headless is None else args.headless

    driver = _build_driver(channel, headless=headless)

    if args.out == "-":
        writer = sys.stdout
        owns_writer = False
    else:
        writer = open_jsonl_writer(args.out)
        owns_writer = True

    try:
        summary = Runner(driver, out_stream=writer).run(campaign)
    finally:
        if owns_writer:
            writer.close()

    print(json.dumps({"summary": summary.to_dict()}), file=sys.stderr)
    return 0 if summary.failure_count == 0 else 1


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    return {
        "list-actions": cmd_list_actions,
        "validate": cmd_validate,
        "run": cmd_run,
    }[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

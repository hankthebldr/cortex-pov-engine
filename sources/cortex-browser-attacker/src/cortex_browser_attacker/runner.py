"""
Runner — iterates actions in a campaign through a BrowserDriver and
writes JSONL.

Enforces the campaign's ``target_allowlist`` for any action that
references a URL (currently ``navigate``). Other actions inherit the
allowlist of whichever URL the prior ``navigate`` landed on.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import logging
import re
import sys
from io import TextIOBase
from pathlib import Path
from typing import Iterable, Optional, TextIO
from urllib.parse import urlparse

from .actions import build_action
from .attempt import ActionResult, run_meta
from .browser import BrowserDriver
from .campaign import BrowserCampaign


logger = logging.getLogger("cortex_browser_attacker.runner")


class SafetyError(Exception):
    """Raised when an action targets a host outside the allowlist."""


_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$")


def _authorise(host: str, allowlist: list[str], *, dry_run: bool) -> None:
    """Raise SafetyError unless ``host`` matches an allowlist entry."""
    if dry_run:
        return
    if not host:
        raise SafetyError("missing host on action target")

    host = host.lower().strip()

    # CIDR / IP entries
    for entry in allowlist:
        if "/" in entry:
            try:
                net = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                continue
            try:
                if ipaddress.ip_address(host) in net:
                    return
            except ValueError:
                continue

    # Hostname suffix match
    for entry in allowlist:
        if "/" in entry:
            continue
        normalised = entry.lower().strip().lstrip(".")
        if host == normalised or host.endswith("." + normalised):
            return

    raise SafetyError(
        f"target host {host!r} not in allowlist {allowlist}"
    )


@dataclasses.dataclass
class RunSummary:
    campaign_id: str
    actions_run: int
    success_count: int
    blocked_count: int
    failure_count: int

    def to_dict(self) -> dict[str, Any]:  # type: ignore[name-defined]
        return dataclasses.asdict(self)


class Runner:
    def __init__(
        self,
        driver: BrowserDriver,
        *,
        out_stream: Optional[TextIO] = None,
    ) -> None:
        self.driver = driver
        self.out_stream = out_stream or sys.stdout

    def run(self, campaign: BrowserCampaign) -> RunSummary:
        # Emit run_meta header first so downstream consumers can read it
        # before any action_attempt arrives.
        meta = run_meta(
            campaign_id=campaign.campaign_id,
            name=campaign.name,
            action_count=len(campaign.actions),
            browser_channel=campaign.browser_channel,
            target_allowlist=list(campaign.target_allowlist),
        )
        self._emit(meta)

        session = self.driver.start()

        success = 0
        blocked = 0
        failed = 0

        try:
            for seq, ba in enumerate(campaign.actions):
                result = ActionResult(
                    seq=seq,
                    action_name=ba.action,
                    params=dict(ba.params),
                )
                result.start()

                # Pre-execute safety check for navigate-class actions.
                if ba.action == "navigate":
                    target_url = ba.params.get("url", "")
                    parsed = urlparse(target_url)
                    if parsed.hostname:
                        try:
                            _authorise(
                                parsed.hostname,
                                campaign.target_allowlist,
                                dry_run=campaign.dry_run,
                            )
                        except SafetyError as exc:
                            result.target_url = target_url
                            result.target_origin = parsed.hostname
                            result.complete("failure", error=f"safety_violation: {exc}")
                            self._emit(result.as_dict())
                            failed += 1
                            if ba.on_error == "abort":
                                break
                            continue
                        result.target_url = target_url

                if campaign.dry_run:
                    # Dry-run never touches the driver. Record what would
                    # have happened and short-circuit.
                    result.notes["dry_run"] = True
                    if ba.action == "navigate":
                        result.target_url = ba.params.get("url")
                        parsed = urlparse(ba.params.get("url", ""))
                        result.target_origin = parsed.hostname
                    # Pull expected_detection from params if present.
                    result.expected_detection = ba.params.get("expected_detection")
                    result.cortex_canary = ba.params.get("cortex_canary")
                    result.complete("success")
                    self._emit(result.as_dict())
                    success += 1
                    continue

                try:
                    action = build_action(ba.action, ba.params)
                except (KeyError, Exception) as exc:
                    result.complete("failure", error=f"build_action: {exc}")
                    self._emit(result.as_dict())
                    failed += 1
                    if ba.on_error == "abort":
                        break
                    continue

                try:
                    action.execute(session, result)
                except Exception as exc:  # pragma: no cover - belt-and-braces
                    result.complete("failure", error=f"action_error: {exc}")

                self._emit(result.as_dict())

                if result.outcome == "success":
                    success += 1
                elif result.outcome == "blocked":
                    blocked += 1
                else:
                    failed += 1

                if result.outcome != "success" and ba.on_error == "abort":
                    break

        finally:
            self.driver.stop()

        return RunSummary(
            campaign_id=campaign.campaign_id,
            actions_run=success + blocked + failed,
            success_count=success,
            blocked_count=blocked,
            failure_count=failed,
        )

    # ------------------------------------------------------------------

    def _emit(self, payload: dict) -> None:
        line = json.dumps(payload, separators=(",", ":"), default=str)
        self.out_stream.write(line + "\n")
        flush = getattr(self.out_stream, "flush", None)
        if callable(flush):
            try:
                flush()
            except (BrokenPipeError, OSError):  # pragma: no cover
                pass


def open_jsonl_writer(path: str | Path) -> TextIO:
    """Open a path for line-buffered append."""
    return open(path, "a", encoding="utf-8", buffering=1)


# Late import so `from .runner import RunSummary` type ref resolves at
# stub time. We can't put this at the top because it would create a
# loop.
from typing import Any  # noqa: E402

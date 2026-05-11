"""
cortex-browser-attacker — Playwright-driven browser action runner for
CortexSim Prisma Browser validation.

Public surface:

  - Action            abstract base class for a single browser action
  - ActionResult      garak-shape result record with as_dict() JSONL
  - BrowserDriver     abstract driver; ships PlaywrightDriver + StubDriver
  - Campaign          Pydantic schema for a browser-campaign YAML
  - Runner            iterates actions, writes JSONL of ActionResults

Drives the deployed customer Prisma Browser (channel='prisma') OR a
plain headless Chromium for lab POVs and unit tests. Prisma Browser
forwards its own telemetry to the customer XSIAM tenant — this tool
just *produces the activity*; it does not bridge PB to XSIAM.
"""

from __future__ import annotations

from .actions import (
    ACTION_REGISTRY,
    Action,
    ClickAction,
    CopyAction,
    DownloadAction,
    InstallExtensionAction,
    NavigateAction,
    PasteAction,
    ScreenshotAction,
    build_action,
)
from .attempt import ActionResult
from .browser import BrowserDriver, BrowserSession, PlaywrightDriver, StubDriver
from .campaign import BrowserAction, BrowserCampaign
from .events import action_result_to_ecs, run_meta_to_ecs
from .runner import Runner, RunSummary

__version__ = "1.0.0"

__all__ = [
    "ACTION_REGISTRY",
    "Action",
    "ActionResult",
    "BrowserAction",
    "BrowserCampaign",
    "BrowserDriver",
    "BrowserSession",
    "ClickAction",
    "CopyAction",
    "DownloadAction",
    "InstallExtensionAction",
    "NavigateAction",
    "PasteAction",
    "PlaywrightDriver",
    "Runner",
    "RunSummary",
    "ScreenshotAction",
    "StubDriver",
    "__version__",
    "action_result_to_ecs",
    "build_action",
    "run_meta_to_ecs",
]

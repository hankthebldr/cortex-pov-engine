"""
Built-in browser actions.

Each action consumes a ``BrowserSession`` (driver-agnostic) and a
Pydantic params model. The registry binds an action name → class so
``BrowserAction.action`` strings in the campaign YAML resolve.

Safety: actions that navigate to a URL check the parsed hostname
against the campaign's target_allowlist via the runner. Actions never
authorise targets themselves — the runner is the single chokepoint.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from .attempt import ActionResult
from .browser import BrowserSession


# --------------------------------------------------------------------------
# Action ABC
# --------------------------------------------------------------------------


class Action(abc.ABC):
    """Stateless browser action."""

    name: str = "abstract"

    class Params(BaseModel):
        pass

    def __init__(self, params: BaseModel) -> None:
        self.params = params

    @abc.abstractmethod
    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        """Mutate ``result`` in place — set ``page_url``, ``notes``, etc.

        ``result`` already has its lifecycle started; the action returns
        no value. It signals failure by raising or by setting
        ``result.complete('failure', error=...)``.
        """


# --------------------------------------------------------------------------
# Param models
# --------------------------------------------------------------------------


class NavigateParams(BaseModel):
    url: str
    wait_for: Optional[str] = Field(default=None, description="CSS selector to wait for")
    expected_detection: Optional[str] = None
    cortex_canary: Optional[str] = None

    @field_validator("url")
    @classmethod
    def _url_well_formed(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("url must be http or https")
        if not parsed.hostname:
            raise ValueError("url must include a hostname")
        return v


class PasteParams(BaseModel):
    selector: str
    content: str
    expected_detection: Optional[str] = None
    cortex_canary: Optional[str] = None

    @field_validator("selector")
    @classmethod
    def _selector_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("selector required")
        return v


class CopyParams(BaseModel):
    selector: str
    expected_detection: Optional[str] = None


class ClickParams(BaseModel):
    selector: str
    expected_detection: Optional[str] = None
    cortex_canary: Optional[str] = None


class DownloadParams(BaseModel):
    timeout_seconds: float = Field(default=15.0, ge=0.5, le=600.0)
    expected_detection: Optional[str] = None


class InstallExtensionParams(BaseModel):
    crx_path: str
    expected_detection: Optional[str] = None
    cortex_canary: Optional[str] = None

    @field_validator("crx_path")
    @classmethod
    def _path_safe(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("crx_path required")
        if ".." in v.split("/"):
            raise ValueError("crx_path must not contain '..'")
        return v


class ScreenshotParams(BaseModel):
    out_path: str = Field(default="/tmp/cortex-browser-screenshot.png")
    expected_detection: Optional[str] = None


# --------------------------------------------------------------------------
# Concrete actions
# --------------------------------------------------------------------------


class NavigateAction(Action):
    name = "navigate"

    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        params: NavigateParams = self.params  # type: ignore[assignment]
        info = session.goto(params.url, wait_for=params.wait_for)
        result.page_url = info.get("url")
        result.page_title = info.get("title")
        result.target_origin = urlparse(params.url).hostname
        result.expected_detection = params.expected_detection
        result.cortex_canary = params.cortex_canary
        result.complete("success")


class PasteAction(Action):
    name = "paste"

    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        params: PasteParams = self.params  # type: ignore[assignment]
        info = session.type_into(params.selector, params.content)
        result.notes["selector"] = params.selector
        result.notes["chars_typed"] = info.get("chars")
        result.notes["paste_source"] = "clipboard-typed"
        if session.current_url:
            result.page_url = session.current_url
            result.target_origin = urlparse(session.current_url).hostname
        result.expected_detection = params.expected_detection
        result.cortex_canary = params.cortex_canary
        result.complete("success")


class CopyAction(Action):
    name = "copy"

    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        params: CopyParams = self.params  # type: ignore[assignment]
        text = session.read_text(params.selector)
        session.set_clipboard(text)
        result.notes["selector"] = params.selector
        result.notes["chars_copied"] = len(text)
        result.notes["clipboard_origin"] = session.current_url
        if session.current_url:
            result.page_url = session.current_url
            result.target_origin = urlparse(session.current_url).hostname
        result.expected_detection = params.expected_detection
        result.complete("success")


class ClickAction(Action):
    name = "click"

    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        params: ClickParams = self.params  # type: ignore[assignment]
        info = session.click(params.selector)
        result.notes["selector"] = info.get("selector")
        if session.current_url:
            result.page_url = session.current_url
            result.target_origin = urlparse(session.current_url).hostname
        result.expected_detection = params.expected_detection
        result.cortex_canary = params.cortex_canary
        result.complete("success")


class DownloadAction(Action):
    name = "download"

    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        params: DownloadParams = self.params  # type: ignore[assignment]
        try:
            info = session.wait_for_download(timeout_seconds=params.timeout_seconds)
        except TimeoutError as exc:
            result.complete("failure", error=f"download_timeout: {exc}")
            return
        result.notes.update({
            "download_path": info.get("path"),
            "download_bytes": info.get("bytes"),
            "download_mime": info.get("mime"),
        })
        if session.current_url:
            result.page_url = session.current_url
            result.target_origin = urlparse(session.current_url).hostname
        result.expected_detection = params.expected_detection
        result.complete("success")


class InstallExtensionAction(Action):
    name = "install_extension"

    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        params: InstallExtensionParams = self.params  # type: ignore[assignment]
        info = session.install_extension(params.crx_path)
        result.notes.update({
            "crx_path": info.get("crx_path"),
            "installed": info.get("installed"),
            "blocked_by_policy": info.get("blocked_by_policy"),
        })
        result.expected_detection = params.expected_detection
        result.cortex_canary = params.cortex_canary
        # A managed-policy block is the *expected* outcome — the
        # detection signal is the *attempt*, not the install. Report
        # the action as blocked (not failure) so reports can group
        # the policy-enforcement column separately.
        if info.get("blocked_by_policy"):
            result.complete("blocked")
        else:
            result.complete("success")


class ScreenshotAction(Action):
    name = "screenshot"

    def execute(self, session: BrowserSession, result: ActionResult) -> None:
        params: ScreenshotParams = self.params  # type: ignore[assignment]
        info = session.screenshot(out_path=params.out_path)
        result.notes["out_path"] = info.get("out_path")
        result.notes["bytes"] = info.get("bytes")
        if session.current_url:
            result.page_url = session.current_url
            result.target_origin = urlparse(session.current_url).hostname
        result.expected_detection = params.expected_detection
        result.complete("success")


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _RegistryEntry:
    cls: type[Action]
    params: type[BaseModel]


ACTION_REGISTRY: dict[str, _RegistryEntry] = {
    "navigate": _RegistryEntry(NavigateAction, NavigateParams),
    "paste": _RegistryEntry(PasteAction, PasteParams),
    "copy": _RegistryEntry(CopyAction, CopyParams),
    "click": _RegistryEntry(ClickAction, ClickParams),
    "download": _RegistryEntry(DownloadAction, DownloadParams),
    "install_extension": _RegistryEntry(InstallExtensionAction, InstallExtensionParams),
    "screenshot": _RegistryEntry(ScreenshotAction, ScreenshotParams),
}


def build_action(name: str, raw_params: dict[str, Any]) -> Action:
    """Resolve an action name from the registry and validate its params."""
    key = name.lower().strip()
    entry = ACTION_REGISTRY.get(key)
    if entry is None:
        raise KeyError(
            f"unknown action '{name}'; available: {sorted(ACTION_REGISTRY)}"
        )
    params = entry.params.model_validate(raw_params or {})
    return entry.cls(params)

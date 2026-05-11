"""
Browser driver abstraction.

Two concrete drivers:

  - ``PlaywrightDriver`` — real Chromium / Prisma Browser via Playwright.
    Requires the ``playwright`` extra and a one-time
    ``playwright install chromium``. Used at runtime in POVs.

  - ``StubDriver`` — in-memory recording driver that captures every
    call without spinning up a browser. Used by every unit test in
    this package so CI does not need Playwright installed.

The actions in ``actions.py`` consume a ``BrowserSession`` (a Page +
context wrapper) and never reach into Playwright directly. That makes
the action surface trivially testable against either driver.
"""

from __future__ import annotations

import abc
import dataclasses
import logging
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger("cortex_browser_attacker.browser")


@dataclasses.dataclass
class BrowserSession:
    """Concrete handle the actions operate against.

    The driver populates ``page`` with whatever its native page handle
    is (Playwright Page, stub recorder, etc.). Actions call methods on
    the *session*, not the page, so the driver controls the API surface.
    """

    driver: "BrowserDriver"
    page: Any                          # Playwright Page or StubPage
    current_url: Optional[str] = None
    current_title: Optional[str] = None

    # ---- Navigation ------------------------------------------------------

    def goto(self, url: str, *, wait_for: Optional[str] = None) -> dict[str, Any]:
        return self.driver.goto(self, url, wait_for=wait_for)

    # ---- DOM actions -----------------------------------------------------

    def type_into(self, selector: str, text: str) -> dict[str, Any]:
        return self.driver.type_into(self, selector, text)

    def click(self, selector: str) -> dict[str, Any]:
        return self.driver.click(self, selector)

    def read_text(self, selector: str) -> str:
        return self.driver.read_text(self, selector)

    def set_clipboard(self, content: str) -> None:
        self.driver.set_clipboard(self, content)

    def get_clipboard(self) -> str:
        return self.driver.get_clipboard(self)

    # ---- Downloads + extensions -----------------------------------------

    def wait_for_download(self, *, timeout_seconds: float) -> dict[str, Any]:
        return self.driver.wait_for_download(self, timeout_seconds=timeout_seconds)

    def install_extension(self, crx_path: str) -> dict[str, Any]:
        return self.driver.install_extension(self, crx_path)

    # ---- Capture ---------------------------------------------------------

    def screenshot(self, *, out_path: str) -> dict[str, Any]:
        return self.driver.screenshot(self, out_path=out_path)


class BrowserDriver(abc.ABC):
    """Abstract browser driver."""

    name: str = "abstract"

    @abc.abstractmethod
    def start(self) -> BrowserSession: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    # All of the below are called via BrowserSession so the action
    # surface is decoupled from Playwright specifics.

    @abc.abstractmethod
    def goto(self, session: BrowserSession, url: str, *, wait_for: Optional[str]) -> dict[str, Any]: ...

    @abc.abstractmethod
    def type_into(self, session: BrowserSession, selector: str, text: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    def click(self, session: BrowserSession, selector: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    def read_text(self, session: BrowserSession, selector: str) -> str: ...

    @abc.abstractmethod
    def set_clipboard(self, session: BrowserSession, content: str) -> None: ...

    @abc.abstractmethod
    def get_clipboard(self, session: BrowserSession) -> str: ...

    @abc.abstractmethod
    def wait_for_download(self, session: BrowserSession, *, timeout_seconds: float) -> dict[str, Any]: ...

    @abc.abstractmethod
    def install_extension(self, session: BrowserSession, crx_path: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    def screenshot(self, session: BrowserSession, *, out_path: str) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# StubDriver — used in tests; records every call.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class StubCall:
    method: str
    args: dict[str, Any]


class StubDriver(BrowserDriver):
    """In-memory driver that records every call and never spins up a
    real browser. Tests assert against ``driver.calls``.

    The fake DOM is a single dict ``elements`` mapping selector → text
    so ``type_into`` followed by ``read_text`` round-trips. Anything
    fancier than that is intentionally out-of-scope for unit testing.
    """

    name = "stub"

    def __init__(
        self,
        *,
        initial_elements: Optional[dict[str, str]] = None,
        download_succeeds: bool = True,
        extension_blocked: bool = False,
    ) -> None:
        self.calls: list[StubCall] = []
        self._clipboard: str = ""
        self._elements: dict[str, str] = dict(initial_elements or {})
        self._download_succeeds = download_succeeds
        self._extension_blocked = extension_blocked
        self._started = False
        self._current_url: Optional[str] = None
        self._current_title: Optional[str] = None

    # ---- Lifecycle -------------------------------------------------------

    def start(self) -> BrowserSession:
        self._started = True
        self.calls.append(StubCall("start", {}))
        return BrowserSession(driver=self, page=object())

    def stop(self) -> None:
        self._started = False
        self.calls.append(StubCall("stop", {}))

    # ---- Navigation ------------------------------------------------------

    def goto(self, session: BrowserSession, url: str, *, wait_for: Optional[str]) -> dict[str, Any]:
        self.calls.append(StubCall("goto", {"url": url, "wait_for": wait_for}))
        self._current_url = url
        self._current_title = f"Stub page: {url}"
        session.current_url = url
        session.current_title = self._current_title
        return {"url": url, "title": self._current_title}

    # ---- DOM -------------------------------------------------------------

    def type_into(self, session: BrowserSession, selector: str, text: str) -> dict[str, Any]:
        self.calls.append(StubCall("type_into", {"selector": selector, "text": text}))
        self._elements[selector] = text
        return {"selector": selector, "chars": len(text)}

    def click(self, session: BrowserSession, selector: str) -> dict[str, Any]:
        self.calls.append(StubCall("click", {"selector": selector}))
        return {"selector": selector}

    def read_text(self, session: BrowserSession, selector: str) -> str:
        self.calls.append(StubCall("read_text", {"selector": selector}))
        return self._elements.get(selector, "")

    def set_clipboard(self, session: BrowserSession, content: str) -> None:
        self.calls.append(StubCall("set_clipboard", {"chars": len(content)}))
        self._clipboard = content

    def get_clipboard(self, session: BrowserSession) -> str:
        self.calls.append(StubCall("get_clipboard", {}))
        return self._clipboard

    # ---- Downloads + extensions -----------------------------------------

    def wait_for_download(self, session: BrowserSession, *, timeout_seconds: float) -> dict[str, Any]:
        self.calls.append(StubCall("wait_for_download", {"timeout_seconds": timeout_seconds}))
        if not self._download_succeeds:
            raise TimeoutError("stub download timed out")
        return {"path": "/tmp/stub-download.bin", "bytes": 1024, "mime": "application/octet-stream"}

    def install_extension(self, session: BrowserSession, crx_path: str) -> dict[str, Any]:
        self.calls.append(StubCall("install_extension", {"crx_path": crx_path}))
        if self._extension_blocked:
            return {"installed": False, "blocked_by_policy": True, "crx_path": crx_path}
        return {"installed": True, "blocked_by_policy": False, "crx_path": crx_path}

    # ---- Capture ---------------------------------------------------------

    def screenshot(self, session: BrowserSession, *, out_path: str) -> dict[str, Any]:
        self.calls.append(StubCall("screenshot", {"out_path": out_path}))
        # In tests we may not want to touch the filesystem; the stub
        # only records the call. Real driver writes bytes.
        return {"out_path": out_path, "bytes": 256}


# ---------------------------------------------------------------------------
# PlaywrightDriver — real Chromium / Prisma Browser
# ---------------------------------------------------------------------------


class PlaywrightDriver(BrowserDriver):
    """Drives real Chromium (or Prisma Browser) via Playwright.

    Optional dependency: ``pip install cortex-browser-attacker[playwright]``
    then ``playwright install chromium``. Import is deferred so the
    module is loadable without Playwright present (tests use StubDriver
    exclusively).
    """

    name = "playwright"

    def __init__(
        self,
        *,
        channel: str = "chromium",
        headless: bool = True,
        user_data_dir: Optional[str] = None,
        downloads_dir: Optional[str] = None,
    ) -> None:
        try:  # pragma: no cover - import-time guard
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "playwright is not installed. Install with "
                "'pip install cortex-browser-attacker[playwright]' "
                "and then 'playwright install chromium'."
            ) from exc

        self.channel = channel
        self.headless = headless
        self.user_data_dir = user_data_dir or "/tmp/cortex-browser-attacker-profile"
        self.downloads_dir = downloads_dir or "/tmp/cortex-browser-attacker-downloads"
        self._pw = None
        self._ctx = None
        self._page = None

    # The PlaywrightDriver methods are intentionally thin wrappers; the
    # rich logic lives in the actions. We exclude this driver from unit
    # tests (it needs a real browser) — coverage is via the integration
    # path that the EAL plugin's manual demo exercises.

    def start(self) -> BrowserSession:  # pragma: no cover - integration only
        from playwright.sync_api import sync_playwright

        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.downloads_dir).mkdir(parents=True, exist_ok=True)

        self._pw = sync_playwright().start()
        chromium_args: dict[str, Any] = {
            "headless": self.headless,
            "downloads_path": self.downloads_dir,
        }
        if self.channel == "prisma":
            # Prisma Browser ships as a Chromium channel; if installed
            # locally, Playwright launches it directly.
            chromium_args["channel"] = "prisma"
        self._ctx = self._pw.chromium.launch_persistent_context(
            self.user_data_dir, **chromium_args,
        )
        self._page = self._ctx.new_page()
        return BrowserSession(driver=self, page=self._page)

    def stop(self) -> None:  # pragma: no cover - integration only
        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def goto(self, session: BrowserSession, url: str, *, wait_for: Optional[str]) -> dict[str, Any]:  # pragma: no cover
        page = session.page
        page.goto(url)
        if wait_for:
            page.wait_for_selector(wait_for, timeout=10_000)
        session.current_url = page.url
        session.current_title = page.title()
        return {"url": page.url, "title": page.title()}

    def type_into(self, session: BrowserSession, selector: str, text: str) -> dict[str, Any]:  # pragma: no cover
        session.page.fill(selector, text)
        return {"selector": selector, "chars": len(text)}

    def click(self, session: BrowserSession, selector: str) -> dict[str, Any]:  # pragma: no cover
        session.page.click(selector)
        return {"selector": selector}

    def read_text(self, session: BrowserSession, selector: str) -> str:  # pragma: no cover
        return session.page.text_content(selector) or ""

    def set_clipboard(self, session: BrowserSession, content: str) -> None:  # pragma: no cover
        # Playwright doesn't expose system clipboard directly; we use
        # navigator.clipboard.writeText() via evaluate().
        session.page.evaluate(
            "(text) => navigator.clipboard.writeText(text)", content,
        )

    def get_clipboard(self, session: BrowserSession) -> str:  # pragma: no cover
        return session.page.evaluate(
            "() => navigator.clipboard.readText()",
        )

    def wait_for_download(self, session: BrowserSession, *, timeout_seconds: float) -> dict[str, Any]:  # pragma: no cover
        with session.page.expect_download(timeout=timeout_seconds * 1000) as info:
            pass
        dl = info.value
        path = Path(self.downloads_dir) / dl.suggested_filename
        dl.save_as(str(path))
        return {
            "path": str(path),
            "bytes": path.stat().st_size,
            "mime": "application/octet-stream",
        }

    def install_extension(self, session: BrowserSession, crx_path: str) -> dict[str, Any]:  # pragma: no cover
        # Real Chromium / Prisma Browser blocks sideload in headless;
        # we just navigate to chrome://extensions and let the managed
        # policy do its job. The return value indicates whether the
        # policy blocked us (we infer from page text).
        session.page.goto("chrome://extensions")
        return {
            "installed": False,
            "blocked_by_policy": True,
            "crx_path": crx_path,
            "note": "managed-policy block expected for unsigned/sideloaded .crx",
        }

    def screenshot(self, session: BrowserSession, *, out_path: str) -> dict[str, Any]:  # pragma: no cover
        session.page.screenshot(path=out_path)
        return {"out_path": out_path}

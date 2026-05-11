"""Action implementations + registry tests against StubDriver."""
from __future__ import annotations

import pytest

from cortex_browser_attacker.actions import (
    ACTION_REGISTRY,
    ClickAction,
    CopyAction,
    DownloadAction,
    InstallExtensionAction,
    NavigateAction,
    PasteAction,
    ScreenshotAction,
    build_action,
)
from cortex_browser_attacker.attempt import ActionResult
from cortex_browser_attacker.browser import StubDriver


@pytest.fixture
def driver():
    return StubDriver(initial_elements={"#account-detail": "ACME Corp / AC-12345"})


@pytest.fixture
def session(driver):
    s = driver.start()
    yield s
    driver.stop()


def _result_for(name: str) -> ActionResult:
    r = ActionResult(action_name=name)
    r.start()
    return r


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def test_registry_seven_actions():
    assert set(ACTION_REGISTRY) == {
        "navigate", "paste", "copy", "click",
        "download", "install_extension", "screenshot",
    }


def test_build_action_unknown_raises():
    with pytest.raises(KeyError, match="unknown action"):
        build_action("explode", {})


# --------------------------------------------------------------------------
# Navigate
# --------------------------------------------------------------------------


def test_navigate_records_url_and_origin(session, driver):
    action = build_action("navigate", {
        "url": "https://login.example.invalid/signin",
        "expected_detection": "PB DLP — credential paste",
    })
    result = _result_for("navigate")
    action.execute(session, result)
    assert result.outcome == "success"
    assert result.page_url == "https://login.example.invalid/signin"
    assert result.target_origin == "login.example.invalid"
    assert result.expected_detection == "PB DLP — credential paste"


def test_navigate_rejects_non_http_url():
    with pytest.raises(Exception, match="url must be http or https"):
        build_action("navigate", {"url": "javascript:alert(1)"})


# --------------------------------------------------------------------------
# Paste
# --------------------------------------------------------------------------


def test_paste_calls_type_into_with_credential(session, driver):
    build_action("navigate", {"url": "https://login.example.invalid/"}).execute(session, _result_for("navigate"))
    action = build_action("paste", {
        "selector": 'input[name="password"]',
        "content": "MyCorpSSO!@#-CORTEXSIM-CANARY",
        "cortex_canary": "CANARY-1",
    })
    result = _result_for("paste")
    action.execute(session, result)

    type_calls = [c for c in driver.calls if c.method == "type_into"]
    assert len(type_calls) == 1
    assert type_calls[0].args["selector"] == 'input[name="password"]'
    assert "CORTEXSIM-CANARY" in type_calls[0].args["text"]
    assert result.cortex_canary == "CANARY-1"


def test_paste_requires_non_empty_selector():
    with pytest.raises(Exception, match="selector required"):
        build_action("paste", {"selector": "", "content": "x"})


# --------------------------------------------------------------------------
# Copy
# --------------------------------------------------------------------------


def test_copy_reads_text_and_sets_clipboard(session, driver):
    build_action("navigate", {
        "url": "https://crm.example.invalid/account/AC-12345",
    }).execute(session, _result_for("navigate"))
    action = build_action("copy", {"selector": "#account-detail"})
    result = _result_for("copy")
    action.execute(session, result)
    assert driver.get_clipboard(session) == "ACME Corp / AC-12345"
    assert result.notes["chars_copied"] == len("ACME Corp / AC-12345")


# --------------------------------------------------------------------------
# Click
# --------------------------------------------------------------------------


def test_click_dispatches_to_driver(session, driver):
    action = build_action("click", {"selector": "#submit"})
    result = _result_for("click")
    action.execute(session, result)
    assert [c.args["selector"] for c in driver.calls if c.method == "click"] == ["#submit"]
    assert result.outcome == "success"


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


def test_download_success_records_path_and_bytes(driver):
    session = driver.start()
    action = build_action("download", {"timeout_seconds": 5.0})
    result = _result_for("download")
    action.execute(session, result)
    assert result.outcome == "success"
    assert result.notes["download_path"] == "/tmp/stub-download.bin"
    assert result.notes["download_bytes"] == 1024


def test_download_timeout_marks_failure():
    driver = StubDriver(download_succeeds=False)
    session = driver.start()
    action = build_action("download", {"timeout_seconds": 0.5})
    result = _result_for("download")
    action.execute(session, result)
    assert result.outcome == "failure"
    assert "download_timeout" in (result.error or "")


# --------------------------------------------------------------------------
# Install extension
# --------------------------------------------------------------------------


def test_install_extension_blocked_by_policy_reports_blocked():
    driver = StubDriver(extension_blocked=True)
    session = driver.start()
    action = build_action("install_extension", {
        "crx_path": "/tmp/sample.crx",
        "expected_detection": "PB extension policy",
    })
    result = _result_for("install_extension")
    action.execute(session, result)
    assert result.outcome == "blocked"
    assert result.notes["blocked_by_policy"] is True


def test_install_extension_success_when_policy_permits():
    driver = StubDriver(extension_blocked=False)
    session = driver.start()
    action = build_action("install_extension", {"crx_path": "/tmp/sample.crx"})
    result = _result_for("install_extension")
    action.execute(session, result)
    assert result.outcome == "success"


def test_install_extension_path_traversal_rejected():
    with pytest.raises(Exception, match="must not contain"):
        build_action("install_extension", {"crx_path": "../etc/passwd"})


# --------------------------------------------------------------------------
# Screenshot
# --------------------------------------------------------------------------


def test_screenshot_records_out_path(session, driver):
    action = build_action("screenshot", {"out_path": "/tmp/shot.png"})
    result = _result_for("screenshot")
    action.execute(session, result)
    assert result.notes["out_path"] == "/tmp/shot.png"
    assert any(c.method == "screenshot" for c in driver.calls)

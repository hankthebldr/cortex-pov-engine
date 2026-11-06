"""SEC-02 / OPS-02 — ``POST /api/tools/{tool}/start`` must not run caller argv.

``ToolInstantiator.start`` used to seed ``binary`` / ``source_path`` / ``port``
from the registry and then run ``sub_context.update(params)`` — so the caller's
body OVERRODE them — before ``run_template.format(...)``, ``shlex.split`` and
``subprocess.Popen``. ``{"params": {"binary": "<any command line>"}}`` against
``xdrtop`` (template ``"{binary}"``) therefore executed an arbitrary command as
whatever user SimCore runs as (root, in the shipped image), from an
unauthenticated route.

The contract these tests pin:

* a key the registry entry supplies or defines (``binary``, ``source_path``,
  ``port``, ``health_check`` ...) is refused — it is never the caller's to set;
* only placeholders the ``run_template`` actually references, and that the
  registry does not supply, are accepted (``mitre_id``, ``config_path``);
* values are validated strictly: ``mitre_id`` is a technique id, a ``*_path``
  resolves under ``CORTEXSIM_BASE_DIR``, and nothing may start with ``-`` or
  carry whitespace;
* a refusal is ``400 BAD_TOOL_PARAM`` and spawns NOTHING.

``subprocess.Popen`` is replaced by a recorder in every test that could reach
it with an accepted value — in CI the prod image really ships the signalbench
binary, and a unit test must not execute an ATT&CK technique.
"""
from __future__ import annotations

import shlex
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def tools_client(make_client):
    from api.tools import router

    return make_client(router)


@pytest.fixture(autouse=True)
def _forget_spawned_processes():
    """The instantiator is a module singleton; never leak a tracked process."""
    from tools.instantiator import instantiator

    before = dict(instantiator._processes)
    yield
    instantiator._processes.clear()
    instantiator._processes.update(before)


class _PopenRecorder:
    """Stands in for subprocess.Popen; records argv, spawns nothing."""

    calls: list[list[str]] = []

    def __init__(self, argv, **kwargs):  # noqa: ANN001
        type(self).calls.append(list(argv))
        self.pid = 424242

    def poll(self):
        return None


@pytest.fixture
def popen_recorder(monkeypatch):
    import tools.instantiator as inst_mod

    _PopenRecorder.calls = []
    monkeypatch.setattr(inst_mod.subprocess, "Popen", _PopenRecorder)
    return _PopenRecorder


# ---------------------------------------------------------------------------
# The RCE itself — observed RED on base cc26ddf
# ---------------------------------------------------------------------------


def _marker_command(marker: Path) -> str:
    """A command line that, if executed, creates ``marker``.

    Built from sys.executable rather than /usr/bin/touch so the proof does not
    depend on coreutils, and shlex-quoted so shlex.split round-trips it exactly.
    """
    code = f"open({str(marker)!r}, 'w').close()"
    return " ".join(shlex.quote(p) for p in (sys.executable, "-c", code))


def test_binary_override_is_refused_and_runs_nothing(tools_client, tmp_path):
    marker = tmp_path / "pwned"
    resp = tools_client.post(
        "/api/tools/xdrtop/start",
        json={"params": {"binary": _marker_command(marker)}},
    )
    # Give a spawned child (base behaviour) time to write the marker before we
    # assert its absence, so a fast refusal and a slow RCE cannot look alike.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)

    assert not marker.exists(), (
        "POST /api/tools/xdrtop/start with params.binary executed the caller's "
        "command line — the registry binary was overridden by the request body"
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()["detail"]
    assert body["code"] == "BAD_TOOL_PARAM"
    assert "binary" in body["detail"]


@pytest.mark.parametrize("key", ["binary", "source_path", "port", "health_check",
                                 "run_template", "build_cmd"])
def test_every_registry_key_is_refused(tools_client, popen_recorder, key):
    resp = tools_client.post(
        "/api/tools/signalbench/start",
        json={"params": {"mitre_id": "T1082", key: "/bin/true"}},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "BAD_TOOL_PARAM"
    assert popen_recorder.calls == []


def test_port_override_on_a_service_is_refused(tools_client, popen_recorder):
    resp = tools_client.post(
        "/api/tools/mocktaxii/start", json={"params": {"port": "9000 --evil"}},
    )
    assert resp.status_code == 400, resp.text
    assert popen_recorder.calls == []


def test_unreferenced_key_is_refused(tools_client, popen_recorder):
    """A key the template never references would be silently ignored by
    str.format — accepting it reads as 'applied' when it was not."""
    resp = tools_client.post(
        "/api/tools/xdrtop/start", json={"params": {"verbose": "1"}},
    )
    assert resp.status_code == 400, resp.text
    assert "verbose" in resp.json()["detail"]["detail"]
    assert popen_recorder.calls == []


# ---------------------------------------------------------------------------
# Strict value validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    "T1082 T1016",          # whitespace -> a second argv token
    "-h",                   # option injection
    "--technique=T1082",
    "T108",                 # not a technique id
    "T1082.1",              # sub-technique is exactly three digits
    "t1082",
    "T1082;id",
    "",
    "T1082\n",
])
def test_mitre_id_is_validated(tools_client, popen_recorder, bad):
    resp = tools_client.post(
        "/api/tools/signalbench/start", json={"params": {"mitre_id": bad}},
    )
    assert resp.status_code == 400, (bad, resp.text)
    assert resp.json()["detail"]["code"] == "BAD_TOOL_PARAM"
    assert popen_recorder.calls == []


@pytest.mark.parametrize("bad", [123, None, ["T1082"], {"a": 1}, True])
def test_non_string_values_are_refused(tools_client, popen_recorder, bad):
    resp = tools_client.post(
        "/api/tools/signalbench/start", json={"params": {"mitre_id": bad}},
    )
    assert resp.status_code == 400, (bad, resp.text)
    assert popen_recorder.calls == []


@pytest.mark.parametrize("good", ["T1082", "T1059.004"])
def test_valid_mitre_id_reaches_popen_as_one_token(tools_client, popen_recorder, good):
    from tools.instantiator import instantiator

    resp = tools_client.post(
        "/api/tools/signalbench/start", json={"params": {"mitre_id": good}},
    )
    assert resp.status_code == 200, resp.text
    assert len(popen_recorder.calls) == 1
    argv = popen_recorder.calls[0]
    assert argv[1:] == ["run", good]
    assert argv[0].endswith("cortexsim-tool-signalbench-linux-amd64")
    assert argv[0].startswith(instantiator._base_dir)


def test_config_path_outside_base_dir_is_refused(tools_client, popen_recorder, tmp_path):
    outside = tmp_path / "ackbarx.yml"
    outside.write_text("x: 1\n")
    for bad in ("../../../etc/passwd", str(outside), "-c/etc/passwd",
                "cfg/a b.yml"):
        resp = tools_client.post(
            "/api/tools/ackbarx/start", json={"params": {"config_path": bad}},
        )
        assert resp.status_code == 400, (bad, resp.text)
        assert resp.json()["detail"]["code"] == "BAD_TOOL_PARAM"
    assert popen_recorder.calls == []


def test_config_path_under_base_dir_is_accepted(tools_client, popen_recorder, monkeypatch, tmp_path):
    from tools.instantiator import instantiator

    monkeypatch.setattr(instantiator, "_base_dir", str(tmp_path))
    (tmp_path / "cfg").mkdir()
    cfg = tmp_path / "cfg" / "ackbarx.yml"
    cfg.write_text("x: 1\n")

    resp = tools_client.post(
        "/api/tools/ackbarx/start", json={"params": {"config_path": "cfg/ackbarx.yml"}},
    )
    assert resp.status_code == 200, resp.text
    argv = popen_recorder.calls[0]
    assert argv[1] == "--config"
    assert Path(argv[2]).resolve() == cfg.resolve()
    assert len(argv) == 3


def test_missing_required_placeholder_is_a_400_not_a_500(tools_client, popen_recorder):
    """signalbench needs mitre_id. A missing placeholder is the caller's
    mistake and must say which one — not a 500 START_FAILED."""
    resp = tools_client.post("/api/tools/signalbench/start", json={"params": {}})
    assert resp.status_code == 400, resp.text
    assert "mitre_id" in resp.json()["detail"]["detail"]
    assert popen_recorder.calls == []


def test_instantiator_refuses_directly_too():
    """Defence in depth: the API is not the only possible caller of start()."""
    from tools.instantiator import ToolParamError, instantiator

    with pytest.raises(ToolParamError) as exc:
        instantiator.start("xdrtop", {"binary": "/bin/sh -c id"})
    assert exc.value.code == "BAD_TOOL_PARAM"

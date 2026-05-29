"""Tests for the ftp_egress EAL plugin.

Param-validation tests run synchronously against the Pydantic model.
Dry-run + happy-path tests drive the plugin against a local TCP
sinkhole running on 127.0.0.1 so we exercise the actual wire format
without sending real traffic to a third-party FTP server.
"""
from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.ftp_egress import (
    FtpEgress,
    FtpEgressParams,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FtpSinkhole:
    """Minimal TCP listener that mimics the FTP control-channel response
    sequence (banner → 331 → 230 → 215 → 200 → 227 → 150 → 226 → 221)
    well enough for the plugin to walk a full session.

    Captures every byte the client sends on the control channel; the
    test asserts the captured bytes contain USER / PASS / STOR.
    """

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.data_payload: bytes = b""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.host, self.port = self._sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        try:
            self._sock.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    client, _ = self._sock.accept()
                except (socket.timeout, OSError):
                    continue
                threading.Thread(target=self._handle, args=(client,), daemon=True).start()
        except Exception:  # pragma: no cover
            pass

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(2.0)
        try:
            # Greet client.
            client.sendall(b"220 cortexsim-sinkhole FTP ready\r\n")
            data_listener: socket.socket | None = None
            data_port = 0
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                self.received.append(chunk)
                upper = chunk.upper()
                if upper.startswith(b"USER"):
                    client.sendall(b"331 Password required\r\n")
                elif upper.startswith(b"PASS"):
                    client.sendall(b"230 Login OK\r\n")
                elif upper.startswith(b"SYST"):
                    client.sendall(b"215 UNIX Type: L8\r\n")
                elif upper.startswith(b"NOOP"):
                    client.sendall(b"200 NOOP ok\r\n")
                elif upper.startswith(b"PASV"):
                    data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    data_listener.bind(("127.0.0.1", 0))
                    data_listener.listen(1)
                    _, data_port = data_listener.getsockname()
                    p1, p2 = (data_port >> 8) & 0xFF, data_port & 0xFF
                    # 127,0,0,1,p1,p2
                    client.sendall(
                        f"227 Entering Passive Mode (127,0,0,1,{p1},{p2})\r\n".encode("ascii")
                    )
                elif upper.startswith(b"STOR"):
                    client.sendall(b"150 Opening data connection\r\n")
                    if data_listener is not None:
                        try:
                            data_listener.settimeout(2.0)
                            data_sock, _ = data_listener.accept()
                            with data_sock:
                                buf = b""
                                while True:
                                    try:
                                        b = data_sock.recv(8192)
                                    except (socket.timeout, OSError):
                                        break
                                    if not b:
                                        break
                                    buf += b
                                self.data_payload = buf
                        except (socket.timeout, OSError):
                            pass
                        data_listener.close()
                        data_listener = None
                    client.sendall(b"226 Transfer complete\r\n")
                elif upper.startswith(b"QUIT"):
                    client.sendall(b"221 Goodbye\r\n")
                    break
                else:
                    client.sendall(b"502 Command not implemented\r\n")
        except Exception:  # pragma: no cover
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass


# --------------------------------------------------------------------------
# Param validation
# --------------------------------------------------------------------------


class TestParamValidation:
    def test_target_host_required(self):
        with pytest.raises(Exception):
            FtpEgressParams.model_validate({})

    def test_target_port_bounds(self):
        with pytest.raises(Exception):
            FtpEgressParams.model_validate({"target_host": "x", "target_port": 0})
        with pytest.raises(Exception):
            FtpEgressParams.model_validate({"target_host": "x", "target_port": 99999})

    @pytest.mark.parametrize("evil", [
        "alice\r\nNOOP injected",
        "alice\nQUIT",
        "alice\x00bob",
    ])
    def test_username_rejects_control_chars(self, evil):
        with pytest.raises(Exception, match="control character"):
            FtpEgressParams.model_validate({"target_host": "x", "username": evil})

    def test_password_rejects_crlf(self):
        with pytest.raises(Exception, match="control character"):
            FtpEgressParams.model_validate(
                {"target_host": "x", "password": "pwd\r\nSTOR"},
            )

    def test_iterations_capped(self):
        with pytest.raises(Exception):
            FtpEgressParams.model_validate({"target_host": "x", "iterations": 0})
        with pytest.raises(Exception):
            FtpEgressParams.model_validate({"target_host": "x", "iterations": 1000})


# --------------------------------------------------------------------------
# Campaign-driven happy + dry-run paths
# --------------------------------------------------------------------------


def _campaign(*, host: str, port: int, dry_run: bool = False, **extras: Any) -> Campaign:
    spec = {
        "campaign_id": "CMP-NDR-FTP-001",
        "name": "ftp_egress test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "ftp_egress",
            "params": {
                "target_host": host,
                "target_port": port,
                "iterations": 1,
                "sleep_seconds": 0.0,
                "connect_timeout": 2.0,
                "idle_seconds": 0.0,
                **extras,
            },
        }],
    }
    if not dry_run:
        spec.update({
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": [host],
        })
    return Campaign.model_validate(spec)


class TestFtpEgressExecution:
    def test_dry_run_emits_one_event_no_socket(self):
        campaign = _campaign(host="ftp-sinkhole.invalid", port=21, dry_run=True)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail.get("dry_run") is True
        assert sr.events_emitted == 1

    def test_happy_path_sends_user_pass_and_stor(self):
        sinkhole = _FtpSinkhole()
        sinkhole.start()
        try:
            campaign = _campaign(host=sinkhole.host, port=sinkhole.port,
                                 send_stor=True, stor_bytes=128)
            executor = CampaignExecutor(audit=AuditLogger(file_path=None))
            state = _run(executor.execute(campaign))
            # Give the sinkhole's data-channel handler a beat to settle.
            import time as _t
            _t.sleep(0.1)
        finally:
            sinkhole.stop()

        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["sessions_completed"] == 1
        captured = b"".join(sinkhole.received)
        # Cleartext credentials are the headline EAL signal — assert
        # USER + PASS landed on the control channel verbatim.
        assert b"USER cortexsim\r\n" in captured
        assert b"PASS cortexsim-lab\r\n" in captured
        # SYST + STOR landed too.
        assert b"SYST\r\n" in captured
        assert b"STOR " in captured
        # Data-channel payload reached the sinkhole.
        assert len(sinkhole.data_payload) == 128

    def test_stor_disabled_skips_data_channel(self):
        sinkhole = _FtpSinkhole()
        sinkhole.start()
        try:
            campaign = _campaign(host=sinkhole.host, port=sinkhole.port, send_stor=False)
            executor = CampaignExecutor(audit=AuditLogger(file_path=None))
            state = _run(executor.execute(campaign))
        finally:
            sinkhole.stop()

        sr = state.step_results[0]
        assert sr.status == "success"
        captured = b"".join(sinkhole.received)
        # USER+PASS still happen — they're the cleartext-credential EAL.
        assert b"USER cortexsim\r\n" in captured
        # But no STOR / PASV was issued.
        assert b"STOR " not in captured
        assert sinkhole.data_payload == b""

    def test_target_outside_allowlist_is_rejected(self):
        sinkhole = _FtpSinkhole()
        sinkhole.start()
        try:
            spec = {
                "campaign_id": "CMP-NDR-FTP-002",
                "name": "bad allowlist",
                "simulation_authorized": True,
                "authorized_by": "tester",
                # Sinkhole host is NOT in this allowlist
                "target_allowlist": ["only-this-other-host.invalid"],
                "steps": [{
                    "step_id": "step-01",
                    "plugin": "ftp_egress",
                    "params": {
                        "target_host": sinkhole.host,
                        "target_port": sinkhole.port,
                        "iterations": 1,
                        "sleep_seconds": 0.0,
                        "connect_timeout": 2.0,
                        "idle_seconds": 0.0,
                    },
                }],
            }
            campaign = Campaign.model_validate(spec)
            executor = CampaignExecutor(audit=AuditLogger(file_path=None))
            _run(executor.execute(campaign))
        finally:
            sinkhole.stop()

        # The plugin must fail or no-op when the host is outside the
        # allowlist — depending on safety policy, the executor either
        # short-circuits with status=failure or no FTP bytes hit the wire.
        captured = b"".join(sinkhole.received)
        assert b"USER " not in captured, (
            "safety policy must prevent USER/PASS from leaving the simulator "
            "when target_host is outside the campaign's target_allowlist"
        )

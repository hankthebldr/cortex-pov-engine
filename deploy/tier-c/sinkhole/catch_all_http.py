#!/usr/bin/env python3
"""CortexSim Tier-C sinkhole — HTTP/HTTPS catch-all.

Stdlib only (no third-party deps so it runs in a bare Ubuntu image). Binds an
HTTP listener on :80 and an HTTPS listener on :443 (self-signed cert baked
into the image). EVERY request — any host, any path, any method — gets a
deterministic ``200 OK`` with a tiny JSON body, and is logged as one JSONL
line to stdout AND to ``/results/sinkhole/http.jsonl`` when that volume is
mounted.

The point is to make the *network shape* of a C2/exfil step observable while
guaranteeing nothing egresses: the runner's traffic terminates here. We never
proxy, never forward, never reach a real service.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RESULTS_DIR = os.environ.get("CORTEXSIM_RESULTS_DIR", "/results")
LOG_PATH = os.path.join(RESULTS_DIR, "sinkhole", "http.jsonl")
CERT = os.environ.get("SINKHOLE_CERT", "/opt/sinkhole/sinkhole.crt")
KEY = os.environ.get("SINKHOLE_KEY", "/opt/sinkhole/sinkhole.key")

_log_lock = threading.Lock()


def _emit(record: dict) -> None:
    line = json.dumps(record, separators=(",", ":"))
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with _log_lock, open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        # Results volume may not be mounted (stdout log is still the record).
        pass


class CatchAll(BaseHTTPRequestHandler):
    # Silence the default stderr access log; we emit our own JSONL.
    def log_message(self, *_args) -> None:  # noqa: D401
        return

    def _handle(self) -> None:
        body_len = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(body_len) if body_len else b""
        _emit(
            {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "scheme": getattr(self.server, "scheme", "http"),
                "method": self.command,
                "host": self.headers.get("Host", ""),
                "path": self.path,
                "ua": self.headers.get("User-Agent", ""),
                "content_length": body_len,
                "client": self.client_address[0] if self.client_address else "",
            }
        )
        payload = json.dumps(
            {"sinkhole": True, "host": self.headers.get("Host", ""), "path": self.path}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Server", "cortexsim-tier-c-sinkhole")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    # Map every common verb to the same handler.
    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = do_OPTIONS = _handle


def _serve(port: int, tls: bool) -> None:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), CatchAll)
    httpd.scheme = "https" if tls else "http"  # type: ignore[attr-defined]
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=CERT, keyfile=KEY)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    print(f"[sinkhole] listening {'https' if tls else 'http'} on :{port}", flush=True)
    httpd.serve_forever()


def main() -> None:
    threads = [
        threading.Thread(target=_serve, args=(80, False), daemon=True),
        threading.Thread(target=_serve, args=(443, True), daemon=True),
    ]
    for t in threads:
        t.start()
    # Block forever; the container is torn down by run-tier-c.sh.
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()

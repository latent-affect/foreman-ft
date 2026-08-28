#!/usr/bin/env python3
"""Live-updating local HTTP server for the Foreman cross-project status dashboard.

Same no-cache-per-request design and loopback-only binding as atlas_dashboard_server.py /
an operator's own single-project dashboard script, generalized to every project instead
of one.

Usage:
    /path/to/venv/bin/python3 /path/to/ticket-system/tools/foreman_status_dashboard_server.py [port]
"""

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import foreman_status_dashboard_data  # noqa: E402

HTML_PATH = TOOLS_DIR / "foreman_status_dashboard.html"
DEFAULT_PORT = 8424  # example-project-a 8420, example-project-b 8422, atlas 8423 -- one free above atlas


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[foreman-status-dashboard] {self.address_string()} {fmt % args}\n")

    def _send(self, status, body_bytes, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML_PATH.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return
        if self.path == "/data.json":
            try:
                import json
                snapshot = foreman_status_dashboard_data.build_snapshot()
                body = json.dumps(snapshot, default=str).encode("utf-8")
                self._send(200, body, "application/json")
            except Exception as exc:  # noqa: BLE001 -- report the real error to the page, don't hide it
                import json
                body = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode("utf-8")
                self._send(500, body, "application/json")
            return
        self._send(404, b"not found", "text/plain")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Foreman status dashboard: http://127.0.0.1:{port}/  (loopback only, Ctrl-C to stop)")
    server.serve_forever()


if __name__ == "__main__":
    main()

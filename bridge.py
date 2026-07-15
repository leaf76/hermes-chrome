#!/usr/bin/env python3
"""Hermes daily-Chrome Tab Group bridge.

Local HTTP queue between Hermes CLI and the Chrome extension service worker.

  Extension long-polls:  GET  /v1/poll?timeout=25
  Hermes enqueues:       POST /v1/command  {id, action, ...}
  Extension reports:     POST /v1/result   {id, ok, data|error}
  Hermes waits:          GET  /v1/result/<id>?timeout=30

Bind: 127.0.0.1:19876 only.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = os.environ.get("HERMES_TABGROUP_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_TABGROUP_BRIDGE_PORT", "19876"))

_cmd_q: queue.Queue = queue.Queue()
_results: dict[str, dict] = {}
_results_cv = threading.Condition()
_started_at = time.time()


def _json_response(handler: BaseHTTPRequestHandler, code: int, obj: dict | list | None = None) -> None:
    body = b"" if obj is None else json.dumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    if body:
        handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter
        pass

    def do_OPTIONS(self) -> None:
        _json_response(self, 204, None)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/health", "/v1/health"):
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "hermes-tabgroup-bridge",
                    "uptime_s": round(time.time() - _started_at, 1),
                    "queued": _cmd_q.qsize(),
                },
            )
            return

        if path == "/v1/poll":
            timeout = float(qs.get("timeout", ["25"])[0] or 25)
            timeout = max(1.0, min(timeout, 55.0))
            try:
                cmd = _cmd_q.get(timeout=timeout)
            except queue.Empty:
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            _json_response(self, 200, cmd)
            return

        if path.startswith("/v1/result/"):
            rid = path[len("/v1/result/") :].strip("/")
            timeout = float(qs.get("timeout", ["30"])[0] or 30)
            timeout = max(1.0, min(timeout, 120.0))
            deadline = time.time() + timeout
            with _results_cv:
                while rid not in _results and time.time() < deadline:
                    remaining = deadline - time.time()
                    _results_cv.wait(timeout=max(0.05, remaining))
                if rid not in _results:
                    _json_response(self, 408, {"ok": False, "error": "timeout waiting for extension result"})
                    return
                payload = _results.pop(rid)
            _json_response(self, 200, payload)
            return

        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _json_response(self, 400, {"ok": False, "error": "invalid json"})
            return

        if path == "/v1/command":
            if not isinstance(body, dict) or not body.get("action"):
                _json_response(self, 400, {"ok": False, "error": "action required"})
                return
            cid = body.get("id") or str(uuid.uuid4())
            cmd = dict(body)
            cmd["id"] = cid
            _cmd_q.put(cmd)
            _json_response(self, 200, {"ok": True, "id": cid, "queued": True})
            return

        if path == "/v1/result":
            if not isinstance(body, dict) or not body.get("id"):
                _json_response(self, 400, {"ok": False, "error": "id required"})
                return
            rid = body["id"]
            with _results_cv:
                _results[rid] = body
                _results_cv.notify_all()
            _json_response(self, 200, {"ok": True})
            return

        _json_response(self, 404, {"ok": False, "error": "not found"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"hermes-tabgroup-bridge listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

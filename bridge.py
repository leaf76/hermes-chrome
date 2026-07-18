#!/usr/bin/env python3
"""Hermes Chrome — local bridge.

HTTP queue between Hermes / agent CLI and the Hermes Chrome extension.

  Extension long-polls:  GET  /v1/poll?timeout=25
  CLI enqueues:          POST /v1/command  {id, action, ...}
  Extension reports:     POST /v1/result   {id, ok, data|error}
  CLI waits:             GET  /v1/result/<id>?timeout=30
  Optional hello:        POST /v1/hello    {version, extension}

Bind: 127.0.0.1 only (default port 19876).

Optional auth:
  HERMES_CHROME_BRIDGE_TOKEN — if set, require header X-Hermes-Chrome-Token
  (or ?token=) on /v1/command, /v1/poll, and /v1/result*.
  /v1/health stays open for liveness probes.
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

HOST = os.environ.get("HERMES_CHROME_BRIDGE_HOST") or os.environ.get(
    "HERMES_TABGROUP_BRIDGE_HOST", "127.0.0.1"
)
PORT = int(
    os.environ.get("HERMES_CHROME_BRIDGE_PORT")
    or os.environ.get("HERMES_TABGROUP_BRIDGE_PORT", "19876")
)
TOKEN = (
    os.environ.get("HERMES_CHROME_BRIDGE_TOKEN")
    or os.environ.get("HERMES_TABGROUP_BRIDGE_TOKEN")
    or ""
).strip()
# Consider extension connected if it polled within this many seconds.
_CONNECTED_MAX_AGE_S = float(
    os.environ.get("HERMES_CHROME_EXTENSION_CONNECTED_S", "45") or 45
)

_cmd_q: queue.Queue = queue.Queue()
_results: dict[str, dict] = {}
_results_cv = threading.Condition()
_started_at = time.time()
_last_poll_at: float | None = None
_extension_version: str | None = None
_extension_name: str | None = None
_state_lock = threading.Lock()


def _note_extension_seen(
    *, version: str | None = None, name: str | None = None
) -> None:
    global _last_poll_at, _extension_version, _extension_name
    with _state_lock:
        _last_poll_at = time.time()
        if version:
            _extension_version = str(version)[:32]
        if name:
            _extension_name = str(name)[:64]


def _health_payload() -> dict:
    with _state_lock:
        last = _last_poll_at
        ver = _extension_version
        name = _extension_name
    age = None if last is None else round(time.time() - last, 1)
    connected = age is not None and age <= _CONNECTED_MAX_AGE_S
    return {
        "ok": True,
        "service": "hermes-chrome-bridge",
        "uptime_s": round(time.time() - _started_at, 1),
        "queued": _cmd_q.qsize(),
        "auth": bool(TOKEN),
        "extension_last_seen_s": age,
        "extension_connected": connected,
        "extension_version": ver,
        "extension": name,
        "connected_max_age_s": _CONNECTED_MAX_AGE_S,
    }


def _json_response(
    handler: BaseHTTPRequestHandler, code: int, obj: dict | list | None = None
) -> None:
    body = b"" if obj is None else json.dumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    # Local-only companion; CORS open for chrome-extension pages on same machine.
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers", "Content-Type, X-Hermes-Chrome-Token"
    )
    handler.end_headers()
    if body:
        handler.wfile.write(body)


def _token_ok(handler: BaseHTTPRequestHandler, qs: dict) -> bool:
    if not TOKEN:
        return True
    header = handler.headers.get("X-Hermes-Chrome-Token") or handler.headers.get(
        "X-Hermes-Token"
    )
    qtok = (qs.get("token") or [None])[0]
    return (header or qtok or "") == TOKEN


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass

    def do_OPTIONS(self) -> None:
        _json_response(self, 204, None)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ("/health", "/v1/health"):
            _json_response(self, 200, _health_payload())
            return

        if not _token_ok(self, qs):
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return

        if path == "/v1/poll":
            # Any poll (including empty 204) proves the SW is alive.
            ver = (qs.get("version") or [None])[0]
            name = (qs.get("extension") or [None])[0]
            _note_extension_seen(version=ver, name=name or "hermes-chrome")
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
                    _json_response(
                        self,
                        408,
                        {"ok": False, "error": "timeout waiting for extension result"},
                    )
                    return
                payload = _results.pop(rid)
            _json_response(self, 200, payload)
            return

        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _json_response(self, 400, {"ok": False, "error": "invalid json"})
            return

        if not _token_ok(self, qs):
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return

        if path == "/v1/hello":
            if not isinstance(body, dict):
                _json_response(self, 400, {"ok": False, "error": "object required"})
                return
            _note_extension_seen(
                version=body.get("version"),
                name=body.get("extension") or "hermes-chrome",
            )
            _json_response(self, 200, {"ok": True, **_health_payload()})
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
    # Refuse non-loopback binds unless explicitly forced (safety).
    if HOST not in ("127.0.0.1", "localhost", "::1") and os.environ.get(
        "HERMES_CHROME_BRIDGE_ALLOW_NONLOCAL"
    ) != "1":
        raise SystemExit(
            f"refusing to bind non-local host {HOST!r}; "
            "set HERMES_CHROME_BRIDGE_ALLOW_NONLOCAL=1 to override"
        )
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    auth = "on" if TOKEN else "off"
    print(
        f"hermes-chrome-bridge listening on http://{HOST}:{PORT} (auth={auth})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

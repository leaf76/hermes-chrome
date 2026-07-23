#!/usr/bin/env python3
"""Hermes Chrome — local bridge.

HTTP queue between Hermes / agent CLI and the Hermes Chrome extension.

  Extension long-polls:  GET  /v1/poll?timeout=25
  CLI enqueues:          POST /v1/command  {id, action, ...}
  Extension reports:     POST /v1/result   {id, ok, data|error}
  CLI waits:             GET  /v1/result/<id>?timeout=30
  Optional hello:        POST /v1/hello    {version, extension}
  Pair (extension):      POST /v1/pair     {}  (chrome-extension Origin, window)

Bind: 127.0.0.1 only (default port 19876).

Auth (default ON):
  Token from HERMES_CHROME_BRIDGE_TOKEN / HERMES_TABGROUP_BRIDGE_TOKEN, else
  loaded/generated in ~/.hermes/run/hermes-chrome/bridge.env.
  Require header X-Hermes-Chrome-Token on /v1/command, /v1/poll, /v1/result*.
  Set HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH=1 to run without a token (not recommended).
  /v1/health stays open for liveness (no secrets).
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = os.environ.get("HERMES_CHROME_BRIDGE_HOST") or os.environ.get(
    "HERMES_TABGROUP_BRIDGE_HOST", "127.0.0.1"
)
PORT = int(
    os.environ.get("HERMES_CHROME_BRIDGE_PORT")
    or os.environ.get("HERMES_TABGROUP_BRIDGE_PORT", "19876")
)

# Consider extension connected if it polled within this many seconds.
_CONNECTED_MAX_AGE_S = float(
    os.environ.get("HERMES_CHROME_EXTENSION_CONNECTED_S", "45") or 45
)

# Request / queue limits (DoS hardening)
_MAX_BODY_BYTES = int(
    os.environ.get("HERMES_CHROME_BRIDGE_MAX_BODY", str(2 * 1024 * 1024))
)  # 2 MiB commands
_MAX_RESULT_BYTES = int(
    os.environ.get("HERMES_CHROME_BRIDGE_MAX_RESULT", str(12 * 1024 * 1024))
)  # capture/base64
_MAX_QUEUE = int(os.environ.get("HERMES_CHROME_BRIDGE_MAX_QUEUE", "64"))
_RESULT_TTL_S = float(os.environ.get("HERMES_CHROME_BRIDGE_RESULT_TTL", "120") or 120)
_PAIRING_WINDOW_S = float(
    os.environ.get("HERMES_CHROME_BRIDGE_PAIRING_WINDOW", "300") or 300
)

_cmd_q: queue.Queue = queue.Queue(maxsize=max(1, _MAX_QUEUE))
_results: dict[str, tuple[float, dict]] = {}  # id -> (expires_at, payload)
_results_cv = threading.Condition()
_started_at = time.time()
_last_poll_at: float | None = None
_extension_version: str | None = None
_extension_name: str | None = None
_state_lock = threading.Lock()
_pairing_until = _started_at + max(30.0, _PAIRING_WINDOW_S)
_pair_used = False
# When extension goes quiet, re-open pairing so reload can auto-pair without CLI.
_PAIRING_REOPEN_AFTER_S = float(
    os.environ.get("HERMES_CHROME_BRIDGE_PAIRING_REOPEN_S", "20") or 20
)


def _run_dir() -> Path:
    run = os.environ.get("HERMES_CHROME_RUN") or os.path.join(
        os.path.expanduser("~"), ".hermes", "run", "hermes-chrome"
    )
    p = Path(run)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _env_file() -> Path:
    return _run_dir() / "bridge.env"


def _load_token_from_env_file() -> str:
    path = _env_file()
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key not in (
            "HERMES_CHROME_BRIDGE_TOKEN",
            "HERMES_TABGROUP_BRIDGE_TOKEN",
        ):
            continue
        val = val.strip().strip("'").strip('"')
        if val:
            return val
    return ""


def _write_token_env_file(token: str) -> Path:
    path = _env_file()
    # Restrictive perms
    path.write_text(
        "# Hermes Chrome bridge auth (local only). Auto-managed; do not commit.\n"
        f"export HERMES_CHROME_BRIDGE_TOKEN='{token}'\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _resolve_token() -> tuple[str, str]:
    """Return (token, source). Empty token only if ALLOW_NO_AUTH."""
    allow_no = os.environ.get("HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH", "").strip() in (
        "1",
        "true",
        "yes",
    )
    env_tok = (
        os.environ.get("HERMES_CHROME_BRIDGE_TOKEN")
        or os.environ.get("HERMES_TABGROUP_BRIDGE_TOKEN")
        or ""
    ).strip()
    if env_tok:
        return env_tok, "env"
    file_tok = _load_token_from_env_file()
    if file_tok:
        return file_tok, "bridge.env"
    if allow_no:
        return "", "disabled"
    # Default: generate and persist
    tok = secrets.token_urlsafe(32)
    _write_token_env_file(tok)
    return tok, "generated"


TOKEN, TOKEN_SOURCE = _resolve_token()


def _note_extension_seen(
    *, version: str | None = None, name: str | None = None
) -> None:
    global _last_poll_at, _extension_version, _extension_name, _pair_used
    with _state_lock:
        _last_poll_at = time.time()
        if version:
            _extension_version = str(version)[:32]
        if name:
            _extension_name = str(name)[:64]
        # Authenticated poll means extension is live; close open pairing window.
        if TOKEN:
            _pair_used = True


def _maybe_reopen_pairing() -> None:
    """Re-open pairing when extension is disconnected so reload can auto-pair."""
    global _pair_used, _pairing_until
    if not TOKEN:
        return
    with _state_lock:
        last = _last_poll_at
        age = None if last is None else time.time() - last
        disconnected = age is None or age > _CONNECTED_MAX_AGE_S
        if not disconnected:
            return
        # Only reopen after a short quiet period (avoid flapping during reloads).
        quiet_ok = age is None or age >= _PAIRING_REOPEN_AFTER_S
        if not quiet_ok:
            return
        open_now = (not _pair_used) and time.time() < _pairing_until
        if open_now:
            return
        _pair_used = False
        _pairing_until = time.time() + max(60.0, _PAIRING_WINDOW_S)


def _purge_results(now: float | None = None) -> None:
    now = time.time() if now is None else now
    dead = [k for k, (exp, _) in _results.items() if exp <= now]
    for k in dead:
        _results.pop(k, None)


def _health_payload() -> dict:
    _maybe_reopen_pairing()
    with _state_lock:
        last = _last_poll_at
        ver = _extension_version
        name = _extension_name
        pairing = (not _pair_used) and time.time() < _pairing_until and bool(TOKEN)
    age = None if last is None else round(time.time() - last, 1)
    connected = age is not None and age <= _CONNECTED_MAX_AGE_S
    return {
        "ok": True,
        "service": "hermes-chrome-bridge",
        "uptime_s": round(time.time() - _started_at, 1),
        "queued": _cmd_q.qsize(),
        "auth": bool(TOKEN),
        "auth_source": TOKEN_SOURCE if TOKEN else "off",
        "pairing_open": pairing,
        "extension_last_seen_s": age,
        "extension_connected": connected,
        "extension_version": ver,
        "extension": name,
        "connected_max_age_s": _CONNECTED_MAX_AGE_S,
        "limits": {
            "max_body_bytes": _MAX_BODY_BYTES,
            "max_result_bytes": _MAX_RESULT_BYTES,
            "max_queue": _MAX_QUEUE,
            "result_ttl_s": _RESULT_TTL_S,
        },
    }


def _cors_origin(handler: BaseHTTPRequestHandler) -> str | None:
    """Allow chrome-extension:// origins only (not *)."""
    origin = (handler.headers.get("Origin") or "").strip()
    if origin.startswith("chrome-extension://"):
        # Basic shape check
        if re.match(r"^chrome-extension://[a-p]{32}$", origin) or re.match(
            r"^chrome-extension://[a-zA-Z0-9\-]+$", origin
        ):
            return origin
    return None


def _set_cors(handler: BaseHTTPRequestHandler) -> None:
    origin = _cors_origin(handler)
    if origin:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Hermes-Chrome-Token, X-Hermes-Token",
        )


def _json_response(
    handler: BaseHTTPRequestHandler, code: int, obj: dict | list | None = None
) -> None:
    body = b"" if obj is None else json.dumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    _set_cors(handler)
    handler.end_headers()
    if body:
        handler.wfile.write(body)


def _client_is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    addr = handler.client_address[0] if handler.client_address else ""
    return addr in ("127.0.0.1", "::1", "localhost")


def _token_ok(handler: BaseHTTPRequestHandler, qs: dict) -> bool:
    if not TOKEN:
        return True
    header = handler.headers.get("X-Hermes-Chrome-Token") or handler.headers.get(
        "X-Hermes-Token"
    )
    # Query token still accepted for legacy CLIs but discouraged (logs).
    qtok = (qs.get("token") or [None])[0]
    provided = (header or qtok or "").strip()
    if not provided:
        return False
    # Constant-time compare
    try:
        return secrets.compare_digest(provided, TOKEN)
    except (TypeError, ValueError):
        return False


def _read_body(handler: BaseHTTPRequestHandler, *, max_bytes: int) -> bytes | None:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return None
    if length < 0:
        return None
    if length > max_bytes:
        # Drain so clients can still read the 413 response cleanly.
        remaining = length
        while remaining > 0:
            chunk = handler.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
        return None
    if length == 0:
        return b"{}"
    return handler.rfile.read(length)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass

    def do_OPTIONS(self) -> None:
        # Preflight only for allowed extension origins
        if _cors_origin(self) is None and (self.headers.get("Origin") or ""):
            self.send_response(403)
            self.end_headers()
            return
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
                _set_cors(self)
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
                while True:
                    _purge_results()
                    if rid in _results:
                        _exp, payload = _results.pop(rid)
                        _json_response(self, 200, payload)
                        return
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    _results_cv.wait(timeout=max(0.05, remaining))
            _json_response(
                self,
                408,
                {"ok": False, "error": "timeout waiting for extension result"},
            )
            return

        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        global _pair_used, _pairing_until
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # Pairing: extension can fetch token once during pairing window (loopback + extension Origin).
        if path == "/v1/pair":
            raw = _read_body(self, max_bytes=4096)
            if raw is None:
                _json_response(self, 413, {"ok": False, "error": "body too large"})
                return
            if not TOKEN:
                _json_response(
                    self,
                    400,
                    {"ok": False, "error": "auth disabled; no token to pair"},
                )
                return
            if not _client_is_loopback(self):
                _json_response(self, 403, {"ok": False, "error": "loopback only"})
                return
            origin = _cors_origin(self)
            if origin is None:
                _json_response(
                    self,
                    403,
                    {
                        "ok": False,
                        "error": "chrome-extension Origin required for pairing",
                    },
                )
                return
            with _state_lock:
                open_pair = (not _pair_used) and time.time() < _pairing_until
            if not open_pair:
                # Last chance: auto-reopen if extension looks disconnected.
                _maybe_reopen_pairing()
                with _state_lock:
                    open_pair = (not _pair_used) and time.time() < _pairing_until
            if not open_pair:
                _json_response(
                    self,
                    403,
                    {
                        "ok": False,
                        "error": "pairing closed; run: hermes-chrome.sh pair-open "
                        "or paste token from bridge.env into Options",
                    },
                )
                return
            with _state_lock:
                _pair_used = True
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "token": TOKEN,
                    "bridge": f"http://{HOST}:{PORT}",
                    "hint": "Saved in extension storage; do not share this token",
                    "auto": True,
                },
            )
            return

        if path == "/v1/pair-open":
            # CLI re-opens pairing window (requires existing token auth, or no-auth mode).
            raw = _read_body(self, max_bytes=4096)
            if raw is None:
                _json_response(self, 413, {"ok": False, "error": "body too large"})
                return
            if not _client_is_loopback(self):
                _json_response(self, 403, {"ok": False, "error": "loopback only"})
                return
            if TOKEN and not _token_ok(self, qs):
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            with _state_lock:
                _pair_used = False
                _pairing_until = time.time() + max(30.0, _PAIRING_WINDOW_S)
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "pairing_open": True,
                    "window_s": _PAIRING_WINDOW_S,
                    "hint": "Click Pair in extension Options within the window",
                },
            )
            return

        # Remaining POSTs require token when configured
        max_body = (
            _MAX_RESULT_BYTES if path == "/v1/result" else _MAX_BODY_BYTES
        )
        raw = _read_body(self, max_bytes=max_body)
        if raw is None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length > max_body:
                _json_response(
                    self,
                    413,
                    {
                        "ok": False,
                        "error": f"body exceeds max {max_body} bytes",
                    },
                )
            else:
                _json_response(self, 400, {"ok": False, "error": "invalid body"})
            return
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
            try:
                _cmd_q.put_nowait(cmd)
            except queue.Full:
                _json_response(
                    self,
                    503,
                    {
                        "ok": False,
                        "error": f"command queue full (max {_MAX_QUEUE})",
                    },
                )
                return
            _json_response(self, 200, {"ok": True, "id": cid, "queued": True})
            return

        if path == "/v1/result":
            if not isinstance(body, dict) or not body.get("id"):
                _json_response(self, 400, {"ok": False, "error": "id required"})
                return
            rid = str(body["id"])
            # Prevent unbounded result id growth / spoof overwrite of fresh results
            with _results_cv:
                _purge_results()
                if len(_results) >= _MAX_QUEUE * 4:
                    _json_response(
                        self,
                        503,
                        {"ok": False, "error": "result store full"},
                    )
                    return
                _results[rid] = (time.time() + _RESULT_TTL_S, body)
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
        f"hermes-chrome-bridge listening on http://{HOST}:{PORT} "
        f"(auth={auth} source={TOKEN_SOURCE} pairing_window_s={_PAIRING_WINDOW_S})",
        flush=True,
    )
    if TOKEN and TOKEN_SOURCE == "generated":
        print(
            f"generated bridge token → {_env_file()} "
            "(extension: Options → Pair with bridge, or paste token)",
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

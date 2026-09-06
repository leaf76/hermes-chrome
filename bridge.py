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
  /v1/health public body is minimal; full detail needs the token.
  Query-string ?token= is OFF by default (HERMES_CHROME_ALLOW_QUERY_TOKEN=1 to enable).
  Pairing/CORS only allow listed chrome-extension ids (CWS id + optional extras).
  Auto-reopen pairing after disconnect is OFF by default
  (HERMES_CHROME_AUTO_REPAIR=1 to enable).
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ROOT = Path(__file__).resolve().parent
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))
from version_util import product_version  # noqa: E402

COMPANION_VERSION = product_version(_ROOT)

HOST = os.environ.get("HERMES_CHROME_BRIDGE_HOST") or os.environ.get(
    "HERMES_TABGROUP_BRIDGE_HOST", "127.0.0.1"
)
PORT = int(
    os.environ.get("HERMES_CHROME_BRIDGE_PORT")
    or os.environ.get("HERMES_TABGROUP_BRIDGE_PORT", "19876")
)

# Chrome Web Store id (stable). Unpacked builds pin the same id via manifest key.
CWS_EXTENSION_ID = "mkoaoadlkijccmmbkioagnlngbbeocfa"

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
# Quiet period before optional auto-reopen (only if AUTO_REPAIR enabled).
_PAIRING_REOPEN_AFTER_S = float(
    os.environ.get("HERMES_CHROME_BRIDGE_PAIRING_REOPEN_S", "20") or 20
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _allowed_extension_ids() -> frozenset[str]:
    """Official CWS/unpacked id plus HERMES_CHROME_ALLOWED_EXTENSION_IDS (comma-separated)."""
    ids = {CWS_EXTENSION_ID}
    extra = (
        os.environ.get("HERMES_CHROME_ALLOWED_EXTENSION_IDS")
        or os.environ.get("HERMES_CHROME_EXTRA_EXTENSION_ID")
        or ""
    ).strip()
    for part in extra.replace(" ", ",").split(","):
        part = part.strip().lower()
        if re.fullmatch(r"[a-p]{32}", part):
            ids.add(part)
    return frozenset(ids)


def _extension_id_from_origin(origin: str) -> str | None:
    m = re.match(r"^chrome-extension://([a-p]{32})/?$", (origin or "").strip())
    if not m:
        # Broader Chrome id charset (some builds); still require 32 chars a-p for MV3
        m = re.match(r"^chrome-extension://([a-z]{32})/?$", (origin or "").strip(), re.I)
    if not m:
        return None
    return m.group(1).lower()


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
    """Optionally re-open pairing when extension is disconnected.

    Default OFF. Enable with HERMES_CHROME_AUTO_REPAIR=1 (or legacy
    HERMES_CHROME_BRIDGE_AUTO_REPAIR=1). Prefer explicit pair-open after token
    is established; stored extension tokens already re-auth without re-pair.
    """
    global _pair_used, _pairing_until
    if not TOKEN:
        return
    if not (
        _env_truthy("HERMES_CHROME_AUTO_REPAIR")
        or _env_truthy("HERMES_CHROME_BRIDGE_AUTO_REPAIR")
    ):
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


def _health_payload(*, detail: bool = False) -> dict:
    # Do not auto-reopen pairing from public health probes.
    if detail:
        _maybe_reopen_pairing()
    with _state_lock:
        last = _last_poll_at
        ver = _extension_version
        name = _extension_name
        pairing = (not _pair_used) and time.time() < _pairing_until and bool(TOKEN)
    age = None if last is None else round(time.time() - last, 1)
    connected = age is not None and age <= _CONNECTED_MAX_AGE_S
    # Public: liveness only (no pairing_open / version / limits recon).
    public = {
        "ok": True,
        "service": "hermes-chrome-bridge",
        "uptime_s": round(time.time() - _started_at, 1),
        "auth_required": bool(TOKEN),
        "auth": bool(TOKEN),
        "extension_connected": connected,
    }
    if not detail:
        return public
    public.update(
        {
            "queued": _cmd_q.qsize(),
            "auth_source": TOKEN_SOURCE if TOKEN else "off",
            "pairing_open": pairing,
            "extension_last_seen_s": age,
            "extension_version": ver,
            "companion_version": COMPANION_VERSION,
            "extension": name,
            "connected_max_age_s": _CONNECTED_MAX_AGE_S,
            "allowed_extension_ids": sorted(_allowed_extension_ids()),
            "auto_repair": _env_truthy("HERMES_CHROME_AUTO_REPAIR")
            or _env_truthy("HERMES_CHROME_BRIDGE_AUTO_REPAIR"),
            "query_token_allowed": _env_truthy("HERMES_CHROME_ALLOW_QUERY_TOKEN"),
            "limits": {
                "max_body_bytes": _MAX_BODY_BYTES,
                "max_result_bytes": _MAX_RESULT_BYTES,
                "max_queue": _MAX_QUEUE,
                "result_ttl_s": _RESULT_TTL_S,
            },
        }
    )
    return public


def _cors_origin(handler: BaseHTTPRequestHandler) -> str | None:
    """Allow only listed chrome-extension:// origins (not * , not arbitrary ids)."""
    origin = (handler.headers.get("Origin") or "").strip()
    ext_id = _extension_id_from_origin(origin)
    if not ext_id:
        return None
    if ext_id not in _allowed_extension_ids():
        return None
    return f"chrome-extension://{ext_id}"


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
    provided = (header or "").strip()
    # Query-string token is OFF by default (leaks via shell history / access logs).
    if not provided and _env_truthy("HERMES_CHROME_ALLOW_QUERY_TOKEN"):
        qtok = (qs.get("token") or [None])[0]
        provided = (qtok or "").strip()
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


def _hermes_state_db_path() -> Path | None:
    env_p = os.environ.get("HERMES_STATE_DB")
    if env_p:
        p = Path(env_p).expanduser().resolve()
        if p.is_file():
            return p
    default_p = Path.home() / ".hermes" / "state.db"
    if default_p.is_file():
        return default_p
    return None


def _get_hermes_sessions(limit: int = 30) -> list[dict]:
    db_path = _hermes_state_db_path()
    if not db_path:
        return []
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, title, started_at,
                   COALESCE(last_activity_at, started_at) as last_activity,
                   message_count, model
            FROM sessions
            WHERE archived = 0
            ORDER BY last_activity DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [
            {
                "id": r["id"],
                "title": r["title"] or "Untitled",
                "started_at": r["started_at"],
                "last_activity": r["last_activity"],
                "message_count": r["message_count"],
                "model": r["model"] or "",
            }
            for r in cur.fetchall()
        ]
        conn.close()
        return rows
    except Exception:
        return []


def _get_hermes_messages(session_id: str, limit: int = 150) -> list[dict]:
    db_path = _hermes_state_db_path()
    if not db_path or not session_id:
        return []
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, role, content, reasoning, tool_calls, tool_name, timestamp
            FROM messages
            WHERE session_id = ? AND active = 1
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = []
        for r in cur.fetchall():
            tool_calls = None
            if r["tool_calls"]:
                try:
                    tool_calls = json.loads(r["tool_calls"])
                except Exception:
                    tool_calls = r["tool_calls"]
            rows.append({
                "id": r["id"],
                "role": r["role"],
                "content": r["content"] or "",
                "reasoning": r["reasoning"] or "",
                "tool_calls": tool_calls,
                "tool_name": r["tool_name"] or "",
                "timestamp": r["timestamp"],
            })
        conn.close()
        return rows
    except Exception:
        return []


def _get_hermes_models_info() -> dict:
    config_path = Path.home() / ".hermes" / "config.yaml"
    cache_path = Path.home() / ".hermes" / "provider_models_cache.json"

    current_model = "gemini-3.8-flash"
    current_provider = "gemini"
    current_effort = "high"

    if config_path.is_file():
        try:
            text = config_path.read_text(encoding="utf-8")
            m_prov = re.search(r"^\s*provider:\s*([^\s#]+)", text, re.MULTILINE)
            if m_prov:
                current_provider = m_prov.group(1).strip()
            m_model = re.search(r"^\s*default:\s*([^\s#]+)", text, re.MULTILINE)
            if m_model:
                current_model = m_model.group(1).strip()
            m_effort = re.search(r"^\s*reasoning_effort:\s*([^\s#]+)", text, re.MULTILINE)
            if m_effort:
                current_effort = m_effort.group(1).strip()
        except Exception:
            pass

    provider_labels = {
        "gemini": "Google Gemini",
        "copilot": "GitHub Copilot",
        "openrouter": "OpenRouter",
        "xai-oauth": "xAI (Grok)",
        "opencode-free": "OpenCode Free",
        "copilot-acp": "Copilot ACP",
    }

    effort_options = [
        {"value": "none", "label": "Off", "desc": "No thinking"},
        {"value": "minimal", "label": "Min", "desc": "Minimal"},
        {"value": "low", "label": "Low", "desc": "Low effort"},
        {"value": "medium", "label": "Med", "desc": "Medium"},
        {"value": "high", "label": "High", "desc": "High effort"},
        {"value": "xhigh", "label": "Max", "desc": "Maximum effort"},
    ]

    providers_list = []
    if cache_path.is_file():
        try:
            cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
            for p_id, p_data in cache_data.items():
                if isinstance(p_data, dict) and "models" in p_data and isinstance(p_data["models"], list):
                    models = p_data["models"]
                    if models:
                        providers_list.append({
                            "id": p_id,
                            "name": provider_labels.get(p_id, p_id.replace("-", " ").title()),
                            "models": models,
                        })
        except Exception:
            pass

    def sort_key(p):
        if p["id"] == current_provider:
            return (0, p["name"])
        return (1, p["name"])

    providers_list.sort(key=sort_key)

    return {
        "ok": True,
        "current_model": current_model,
        "current_provider": current_provider,
        "current_effort": current_effort,
        "effort_options": effort_options,
        "providers": providers_list,
    }


def _update_hermes_config(data: dict) -> dict:
    config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.is_file():
        return {"saved": False, "error": "config file not found"}
    try:
        text = config_path.read_text(encoding="utf-8")
        updated_fields = {}
        if "model" in data and isinstance(data["model"], str) and data["model"].strip():
            new_m = data["model"].strip()
            text = re.sub(r"(^\s*default:\s*)[^\s#]+", rf"\g<1>{new_m}", text, count=1, flags=re.MULTILINE)
            updated_fields["model"] = new_m
        if "provider" in data and isinstance(data["provider"], str) and data["provider"].strip():
            new_p = data["provider"].strip()
            text = re.sub(r"(^\s*provider:\s*)[^\s#]+", rf"\g<1>{new_p}", text, count=1, flags=re.MULTILINE)
            updated_fields["provider"] = new_p
        if "reasoning_effort" in data and isinstance(data["reasoning_effort"], str) and data["reasoning_effort"].strip():
            new_e = data["reasoning_effort"].strip().lower()
            text = re.sub(r"(^\s*reasoning_effort:\s*)[^\s#]+", rf"\g<1>{new_e}", text, count=1, flags=re.MULTILINE)
            updated_fields["reasoning_effort"] = new_e
        config_path.write_text(text, encoding="utf-8")
        return {"saved": True, "fields": updated_fields}
    except Exception as e:
        return {"saved": False, "error": str(e)}


def _run_hermes_cli_prompt(
    prompt: str,
    session_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    reasoning: str | None = None,
) -> dict:
    hermes_bin = shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
    if not os.path.isfile(hermes_bin) and not shutil.which("hermes"):
        return {"ok": False, "error": "Hermes CLI binary not found on system"}

    cmd = [hermes_bin, "-z", prompt]
    if session_id and session_id not in ("__auto__", "__new__"):
        cmd.extend(["--resume", session_id])
    elif session_id == "__auto__":
        cmd.extend(["--resume", "latest"])

    if model and isinstance(model, str) and model.strip():
        cmd.extend(["-m", model.strip()])

    if provider and isinstance(provider, str) and provider.strip():
        cmd.extend(["--provider", provider.strip()])

    if reasoning and isinstance(reasoning, str) and reasoning.strip().lower() not in ("none", "off"):
        cmd.extend(["--reasoning", reasoning.strip().lower()])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        if proc.returncode != 0:
            return {"ok": False, "error": err or f"Hermes exited with code {proc.returncode}"}
        return {"ok": True, "response": out}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Hermes CLI execution timed out after 120s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
            # Public liveness is minimal; full payload requires token when auth is on.
            detail = (not TOKEN) or _token_ok(self, qs)
            _json_response(self, 200, _health_payload(detail=detail))
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

        if path == "/v1/hermes/sessions":
            limit = int((qs.get("limit") or ["30"])[0])
            limit = max(1, min(limit, 100))
            sessions = _get_hermes_sessions(limit=limit)
            _json_response(self, 200, {"ok": True, "sessions": sessions})
            return

        if path.startswith("/v1/hermes/sessions/") and path.endswith("/messages"):
            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[0] == "v1" and parts[1] == "hermes" and parts[2] == "sessions" and parts[4] == "messages":
                sid = parts[3]
                limit = int((qs.get("limit") or ["150"])[0])
                limit = max(1, min(limit, 300))
                messages = _get_hermes_messages(session_id=sid, limit=limit)
                _json_response(self, 200, {"ok": True, "session_id": sid, "messages": messages})
                return

        if path == "/v1/hermes/models":
            models_info = _get_hermes_models_info()
            _json_response(self, 200, models_info)
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
                        "error": "pairing requires Origin of an allowed Hermes Chrome "
                        f"extension id (official: {CWS_EXTENSION_ID}); "
                        "set HERMES_CHROME_ALLOWED_EXTENSION_IDS for extras",
                    },
                )
                return
            with _state_lock:
                open_pair = (not _pair_used) and time.time() < _pairing_until
            if not open_pair:
                # Optional auto-reopen only when HERMES_CHROME_AUTO_REPAIR=1.
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

        if path == "/v1/hermes/config":
            if not isinstance(body, dict):
                _json_response(self, 400, {"ok": False, "error": "object required"})
                return
            updated = _update_hermes_config(body)
            _json_response(self, 200, {"ok": True, "updated": updated})
            return

        if path == "/v1/hermes/prompt":
            if not isinstance(body, dict) or not body.get("prompt"):
                _json_response(self, 400, {"ok": False, "error": "prompt required"})
                return
            prompt = str(body["prompt"])
            session_id = body.get("session_id")
            model = body.get("model")
            provider = body.get("provider")
            reasoning = body.get("reasoning_effort")
            res = _run_hermes_cli_prompt(
                prompt,
                session_id=session_id,
                model=model,
                provider=provider,
                reasoning=reasoning,
            )
            _json_response(self, 200, res)
            return

        _json_response(self, 404, {"ok": False, "error": "not found"})


def _spawn_self_update_loop() -> None:
    """Daily companion git update (no-op unless auto-update is enabled)."""
    script = _ROOT / "lib" / "self_update.py"
    if not script.is_file():
        return

    def loop() -> None:
        time.sleep(600)
        while True:
            try:
                subprocess.run(
                    [sys.executable, str(script), "--maybe", "--restart"],
                    cwd=str(_ROOT),
                    timeout=180,
                    capture_output=True,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            time.sleep(86400)

    threading.Thread(
        target=loop, name="hermes-chrome-self-update", daemon=True
    ).start()


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
    auto = (
        "on"
        if (
            _env_truthy("HERMES_CHROME_AUTO_REPAIR")
            or _env_truthy("HERMES_CHROME_BRIDGE_AUTO_REPAIR")
        )
        else "off"
    )
    print(
        f"hermes-chrome-bridge listening on http://{HOST}:{PORT} "
        f"(auth={auth} source={TOKEN_SOURCE} pairing_window_s={_PAIRING_WINDOW_S} "
        f"auto_repair={auto} ext_ids={','.join(sorted(_allowed_extension_ids()))})",
        flush=True,
    )
    if TOKEN and TOKEN_SOURCE == "generated":
        print(
            f"generated bridge token → {_env_file()} "
            "(extension: Options → Pair with bridge, or paste token)",
            flush=True,
        )
    _spawn_self_update_loop()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

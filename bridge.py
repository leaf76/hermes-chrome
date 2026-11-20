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

import contextlib
import json
import math
import os
import queue
import re
import secrets
import shutil
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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
_MAX_RESULTS_TOTAL_BYTES = 64 * 1024 * 1024

_cmd_q: queue.Queue = queue.Queue(maxsize=max(1, _MAX_QUEUE))
_results: dict[str, tuple[float, int, dict]] = {}  # id -> (expires_at_mono, approx_bytes, payload)
_results_cv = threading.Condition()
_started_at = time.monotonic()
_last_poll_at: float | None = None
_extension_version: str | None = None
_extension_name: str | None = None
_state_lock = threading.Lock()
_config_lock = threading.Lock()
_cli_prompt_sem = threading.BoundedSemaphore(2)
_pairing_until = _started_at + max(30.0, _PAIRING_WINDOW_S)
_pair_used = False
# Quiet period before optional auto-reopen (only if AUTO_REPAIR enabled).
_PAIRING_REOPEN_AFTER_S = float(
    os.environ.get("HERMES_CHROME_BRIDGE_PAIRING_REOPEN_S", "20") or 20
)

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_VALID_REASONING = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


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
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
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
    content = (
        "# Hermes Chrome bridge auth (local only). Auto-managed; do not commit.\n"
        f"export HERMES_CHROME_BRIDGE_TOKEN='{token}'\n"
    ).encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _resolve_token() -> tuple[str, str]:
    """Return (token, source). Empty token only if ALLOW_NO_AUTH."""
    allow_no = _env_truthy("HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH")
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


TOKEN = ""
TOKEN_SOURCE = "uninitialized"


def _note_extension_seen(
    *, version: str | None = None, name: str | None = None
) -> None:
    global _last_poll_at, _extension_version, _extension_name, _pair_used
    with _state_lock:
        _last_poll_at = time.monotonic()
        if version:
            _extension_version = str(version)[:32]
        if name:
            _extension_name = str(name)[:64]
        # Authenticated poll means extension is live; close open pairing window.
        if TOKEN:
            _pair_used = True


def _maybe_reopen_pairing_locked(now: float | None = None) -> None:
    """Optionally re-open pairing when extension is disconnected (caller holds _state_lock).

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
    now = time.monotonic() if now is None else now
    last = _last_poll_at
    age = None if last is None else now - last
    disconnected = age is None or age > _CONNECTED_MAX_AGE_S
    if not disconnected:
        return
    # Only reopen after a short quiet period (avoid flapping during reloads).
    quiet_ok = age is None or age >= _PAIRING_REOPEN_AFTER_S
    if not quiet_ok:
        return
    open_now = (not _pair_used) and now < _pairing_until
    if open_now:
        return
    _pair_used = False
    _pairing_until = now + max(60.0, _PAIRING_WINDOW_S)


def _purge_results(now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    dead = [k for k, (exp, _sz, _pl) in _results.items() if exp <= now]
    for k in dead:
        _results.pop(k, None)


def _health_payload(*, detail: bool = False) -> dict:
    # Do not auto-reopen pairing from public health probes.
    now = time.monotonic()
    with _state_lock:
        if detail:
            _maybe_reopen_pairing_locked(now)
        last = _last_poll_at
        ver = _extension_version
        name = _extension_name
        pairing = (not _pair_used) and now < _pairing_until and bool(TOKEN)
    age = None if last is None else round(now - last, 1)
    connected = age is not None and age <= _CONNECTED_MAX_AGE_S
    # Public: liveness only (no pairing_open / version / limits recon).
    public = {
        "ok": True,
        "service": "hermes-chrome-bridge",
        "uptime_s": round(now - _started_at, 1),
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
    allowed = {eid: f"chrome-extension://{eid}" for eid in _allowed_extension_ids()}
    return allowed.get(ext_id)


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
    return addr in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")


def _parse_host_header(raw_host: str) -> str:
    h = (raw_host or "").strip().lower()
    if not h:
        return ""
    if h.startswith("["):
        end = h.find("]")
        return h[1:end] if end != -1 else ""
    if ":" in h:
        return h.partition(":")[0]
    return h


def _host_allowed(handler: BaseHTTPRequestHandler) -> bool:
    hostname = _parse_host_header(handler.headers.get("Host") or "")
    if not hostname:
        return False
    allowed = {"127.0.0.1", "localhost", "::1"}
    if _env_truthy("HERMES_CHROME_BRIDGE_ALLOW_NONLOCAL"):
        allowed.add(HOST.strip("[]").lower())
    return hostname in allowed


def _parse_float(raw: str | None, default: float, lo: float, hi: float) -> float:
    if raw is None or raw == "":
        return default
    val = float(raw)
    if not math.isfinite(val):
        raise ValueError("non-finite float")
    return max(lo, min(val, hi))


def _parse_int(raw: str | None, default: int, lo: int, hi: int) -> int:
    if raw is None or raw == "":
        return default
    val = int(raw)
    return max(lo, min(val, hi))


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
        uri = f"{db_path.as_uri()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as conn:
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
            return [
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
    except Exception:
        return []


def _get_hermes_session_info(session_id: str) -> dict | None:
    db_path = _hermes_state_db_path()
    if not db_path or not session_id:
        return None
    try:
        uri = f"{db_path.as_uri()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, title, started_at,
                       COALESCE(last_activity_at, started_at) as last_activity,
                       message_count, model
                FROM sessions
                WHERE id = ?
                LIMIT 1
                """,
                (session_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {
                "id": r["id"],
                "title": r["title"] or "Untitled",
                "started_at": r["started_at"],
                "last_activity": r["last_activity"],
                "message_count": r["message_count"],
                "model": r["model"] or "",
            }
    except Exception:
        return None


def _get_hermes_messages(session_id: str, limit: int = 150) -> list[dict]:
    db_path = _hermes_state_db_path()
    if not db_path or not session_id:
        return []
    try:
        uri = f"{db_path.as_uri()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=2.0)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, role, content, reasoning, tool_calls, tool_name, timestamp
                FROM (
                    SELECT id, role, content, reasoning, tool_calls, tool_name, timestamp
                    FROM messages
                    WHERE session_id = ? AND active = 1
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                )
                ORDER BY timestamp ASC, id ASC
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


def _replace_yaml_key(text: str, key: str, new_val: str) -> str:
    """Replace key within top-level `model:` section when present, else first match."""
    pattern = re.compile(rf"(^\s*{re.escape(key)}:\s*)[^\s#]+", re.MULTILINE)
    lines = text.splitlines(keepends=True)
    in_model_block = False
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if indent == 0:
            in_model_block = bool(re.match(r"^model:\s*(?:#.*)?$", stripped))
            if in_model_block:
                continue
        if in_model_block:
            replaced, n = pattern.subn(lambda m: f"{m.group(1)}{new_val}", line, count=1)
            if n:
                lines[idx] = replaced
                return "".join(lines)
    return pattern.sub(lambda m: f"{m.group(1)}{new_val}", text, count=1)


def _update_hermes_config(data: dict) -> dict:
    config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.is_file():
        return {"saved": False, "error": "config file not found"}

    new_m: str | None = None
    new_p: str | None = None
    new_e: str | None = None

    if "model" in data and data["model"] is not None:
        if not isinstance(data["model"], str) or not _MODEL_RE.fullmatch(data["model"].strip()):
            return {"saved": False, "error": "invalid model identifier"}
        new_m = data["model"].strip()
    if "provider" in data and data["provider"] is not None:
        if not isinstance(data["provider"], str):
            return {"saved": False, "error": "invalid provider identifier"}
        cand_p = data["provider"].strip()
        if not _PROVIDER_RE.fullmatch(cand_p) or cand_p.lower() == "custom":
            return {"saved": False, "error": "invalid provider identifier"}
        new_p = cand_p
    if "reasoning_effort" in data and data["reasoning_effort"] is not None:
        if not isinstance(data["reasoning_effort"], str):
            return {"saved": False, "error": "invalid reasoning_effort"}
        cand_e = data["reasoning_effort"].strip().lower()
        if cand_e not in _VALID_REASONING:
            return {"saved": False, "error": "invalid reasoning_effort"}
        new_e = cand_e

    with _config_lock:
        try:
            text = config_path.read_text(encoding="utf-8")
            updated_fields = {}
            if new_m is not None:
                text = _replace_yaml_key(text, "default", new_m)
                updated_fields["model"] = new_m
            if new_p is not None:
                text = _replace_yaml_key(text, "provider", new_p)
                updated_fields["provider"] = new_p
            if new_e is not None:
                text = _replace_yaml_key(text, "reasoning_effort", new_e)
                updated_fields["reasoning_effort"] = new_e
            tmp = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, config_path)
            return {"saved": True, "fields": updated_fields}
        except Exception as e:
            return {"saved": False, "error": str(e)}


def _resolve_hermes_binary() -> tuple[str | None, str | None]:
    candidates: list[str] = []
    which_bin = shutil.which("hermes")
    if which_bin:
        candidates.append(which_bin)
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA") or ""
        if local_app:
            candidates.append(str(Path(local_app) / "hermes" / "bin" / "hermes.exe"))
        candidates.append(str(Path.home() / ".local" / "bin" / "hermes.exe"))
    else:
        candidates.append(os.path.expanduser("~/.local/bin/hermes"))

    for cand in candidates:
        p = Path(cand)
        if not p.is_file():
            continue
        if os.name == "nt" and p.suffix.lower() in {".cmd", ".bat"}:
            return None, "refusing Windows .cmd/.bat hermes shim; install native hermes.exe"
        return str(p.resolve()), None
    return None, "Hermes CLI binary not found on system"


def _run_hermes_cli_prompt(
    prompt: str,
    session_id: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    reasoning: str | None = None,
) -> dict:
    if not isinstance(prompt, str) or not prompt.strip() or "\x00" in prompt:
        return {"ok": False, "error": "invalid prompt"}
    if len(prompt) > 32000:
        return {"ok": False, "error": "prompt exceeds 32000 chars"}

    hermes_bin, bin_err = _resolve_hermes_binary()
    if not hermes_bin:
        return {"ok": False, "error": bin_err or "Hermes CLI binary not found on system"}

    safe_prompt = f" {prompt}" if prompt.startswith("-") else prompt
    args: list[str] = ["-z", safe_prompt]

    if session_id is not None:
        if not isinstance(session_id, str):
            return {"ok": False, "error": "invalid session_id"}
        sid = session_id.strip()
        if sid == "__auto__":
            args.extend(["--resume", "latest"])
        elif sid and sid != "__new__":
            if not _SESSION_ID_RE.fullmatch(sid):
                return {"ok": False, "error": "invalid session_id format"}
            args.extend(["--resume", sid])

    if model is not None:
        if not isinstance(model, str):
            return {"ok": False, "error": "invalid model"}
        m_val = model.strip()
        if m_val:
            if not _MODEL_RE.fullmatch(m_val):
                return {"ok": False, "error": "invalid model identifier"}
            args.extend(["-m", m_val])

    if provider is not None:
        if not isinstance(provider, str):
            return {"ok": False, "error": "invalid provider"}
        p_val = provider.strip()
        if p_val and p_val.lower() != "custom":
            if not _PROVIDER_RE.fullmatch(p_val):
                return {"ok": False, "error": "invalid provider identifier"}
            args.extend(["--provider", p_val])

    if reasoning is not None:
        if not isinstance(reasoning, str):
            return {"ok": False, "error": "invalid reasoning_effort"}
        r_val = reasoning.strip().lower()
        if r_val and r_val not in ("none", "off"):
            if r_val not in _VALID_REASONING:
                return {"ok": False, "error": "invalid reasoning_effort"}
            args.extend(["--reasoning", r_val])

    if not _cli_prompt_sem.acquire(blocking=False):
        return {"ok": False, "error": "Hermes CLI busy; another prompt is already running"}
    try:
        proc = subprocess.run(
            [hermes_bin, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=120,
            shell=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return {"ok": False, "error": err or f"Hermes exited with code {proc.returncode}"}
        return {"ok": True, "response": out}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Hermes CLI execution timed out after 120s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _cli_prompt_sem.release()


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Skip HTTPServer.server_bind()'s socket.getfqdn(host) reverse DNS lookup,
        # which can stall ~35s on macOS runners and offline hosts.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host or "127.0.0.1"
        self.server_port = port


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        try:
            self.request.settimeout(15.0)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass

    def do_OPTIONS(self) -> None:
        if not _host_allowed(self):
            _json_response(self, 403, {"ok": False, "error": "invalid host"})
            return
        # Preflight only for allowed extension origins
        if _cors_origin(self) is None and (self.headers.get("Origin") or ""):
            self.send_response(403)
            self.end_headers()
            return
        _json_response(self, 204, None)

    def do_GET(self) -> None:
        if not _host_allowed(self):
            _json_response(self, 403, {"ok": False, "error": "invalid host"})
            return
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
            try:
                timeout = _parse_float(qs.get("timeout", ["25"])[0], 25.0, 1.0, 55.0)
            except ValueError:
                _json_response(self, 400, {"ok": False, "error": "invalid timeout"})
                return
            try:
                cmd = _cmd_q.get(timeout=timeout)
            except queue.Empty:
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                _set_cors(self)
                self.end_headers()
                return
            try:
                _json_response(self, 200, cmd)
            except OSError:
                try:
                    _cmd_q.put_nowait(cmd)
                except queue.Full:
                    pass
            return

        if path.startswith("/v1/result/"):
            rid = path[len("/v1/result/") :].strip("/")
            try:
                timeout = _parse_float(qs.get("timeout", ["30"])[0], 30.0, 1.0, 120.0)
            except ValueError:
                _json_response(self, 400, {"ok": False, "error": "invalid timeout"})
                return
            deadline = time.monotonic() + timeout
            found_payload: dict | None = None
            with _results_cv:
                while True:
                    _purge_results()
                    if rid in _results:
                        _exp, _sz, found_payload = _results.pop(rid)
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    _results_cv.wait(timeout=max(0.05, remaining))
            if found_payload is not None:
                _json_response(self, 200, found_payload)
                return
            _json_response(
                self,
                408,
                {"ok": False, "error": "timeout waiting for extension result"},
            )
            return

        if path == "/v1/hermes/sessions":
            try:
                limit = _parse_int((qs.get("limit") or ["30"])[0], 30, 1, 100)
            except ValueError:
                _json_response(self, 400, {"ok": False, "error": "invalid limit"})
                return
            sessions = _get_hermes_sessions(limit=limit)
            _json_response(self, 200, {"ok": True, "sessions": sessions})
            return

        if path.startswith("/v1/hermes/sessions/") and path.endswith("/messages"):
            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[0] == "v1" and parts[1] == "hermes" and parts[2] == "sessions" and parts[4] == "messages":
                sid = unquote(parts[3])
                try:
                    limit = _parse_int((qs.get("limit") or ["150"])[0], 150, 1, 300)
                except ValueError:
                    _json_response(self, 400, {"ok": False, "error": "invalid limit"})
                    return
                messages = _get_hermes_messages(session_id=sid, limit=limit)
                session_info = _get_hermes_session_info(sid)
                _json_response(
                    self,
                    200,
                    {"ok": True, "session_id": sid, "session": session_info, "messages": messages},
                )
                return

        if path == "/v1/hermes/models":
            models_info = _get_hermes_models_info()
            _json_response(self, 200, models_info)
            return

        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        global _pair_used, _pairing_until
        if not _host_allowed(self):
            _json_response(self, 403, {"ok": False, "error": "invalid host"})
            return
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
                now = time.monotonic()
                open_pair = (not _pair_used) and now < _pairing_until
                if not open_pair:
                    # Optional auto-reopen only when HERMES_CHROME_AUTO_REPAIR=1.
                    _maybe_reopen_pairing_locked(now)
                    open_pair = (not _pair_used) and now < _pairing_until
                if open_pair:
                    _pair_used = True
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
            url_host = f"[{HOST}]" if ":" in HOST and not HOST.startswith("[") else HOST
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "token": TOKEN,
                    "bridge": f"http://{url_host}:{PORT}",
                    "hint": "Saved in extension storage; do not share this token",
                    "auto": True,
                },
            )
            return

        if path == "/v1/pair-open":
            # CLI re-opens pairing window (requires existing token auth, or no-auth mode).
            if not _client_is_loopback(self):
                _json_response(self, 403, {"ok": False, "error": "loopback only"})
                return
            if TOKEN and not _token_ok(self, qs):
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            raw = _read_body(self, max_bytes=4096)
            if raw is None:
                _json_response(self, 413, {"ok": False, "error": "body too large"})
                return
            with _state_lock:
                _pair_used = False
                _pairing_until = time.monotonic() + max(30.0, _PAIRING_WINDOW_S)
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

        # Auth & Content-Type check BEFORE reading potentially large request bodies.
        if not TOKEN:
            ctype = (self.headers.get("Content-Type") or "").strip().lower()
            if not ctype.startswith("application/json"):
                _json_response(
                    self,
                    415,
                    {"ok": False, "error": "Content-Type application/json required"},
                )
                return
        elif not _token_ok(self, qs):
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return

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
        except (json.JSONDecodeError, UnicodeDecodeError):
            _json_response(self, 400, {"ok": False, "error": "invalid json"})
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
            payload_bytes = len(raw)
            reject_code: int | None = None
            reject_err: str | None = None
            with _results_cv:
                now = time.monotonic()
                _purge_results(now)
                if rid in _results:
                    reject_code = 409
                    reject_err = "result already exists"
                else:
                    total_bytes = sum(sz for _e, sz, _p in _results.values())
                    if len(_results) >= _MAX_QUEUE * 4 or (total_bytes + payload_bytes) > _MAX_RESULTS_TOTAL_BYTES:
                        reject_code = 503
                        reject_err = "result store full"
                    else:
                        _results[rid] = (now + _RESULT_TTL_S, payload_bytes, body)
                        _results_cv.notify_all()
            if reject_code is not None:
                _json_response(self, reject_code, {"ok": False, "error": reject_err})
                return
            _json_response(self, 200, {"ok": True})
            return

        if path == "/v1/hermes/config":
            if not isinstance(body, dict):
                _json_response(self, 400, {"ok": False, "error": "object required"})
                return
            updated = _update_hermes_config(body)
            _json_response(self, 200, {"ok": bool(updated.get("saved")), "updated": updated})
            return

        if path == "/v1/hermes/prompt":
            if not isinstance(body, dict) or not body.get("prompt"):
                _json_response(self, 400, {"ok": False, "error": "prompt required"})
                return
            prompt = body["prompt"]
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
    global TOKEN, TOKEN_SOURCE
    # Refuse non-loopback binds unless explicitly forced (safety).
    if HOST not in ("127.0.0.1", "localhost", "::1") and os.environ.get(
        "HERMES_CHROME_BRIDGE_ALLOW_NONLOCAL"
    ) != "1":
        raise SystemExit(
            f"refusing to bind non-local host {HOST!r}; "
            "set HERMES_CHROME_BRIDGE_ALLOW_NONLOCAL=1 to override"
        )
    server = _BridgeServer((HOST, PORT), Handler)
    # Resolve/generate token only after winning the port bind.
    TOKEN, TOKEN_SOURCE = _resolve_token()
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

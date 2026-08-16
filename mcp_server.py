#!/usr/bin/env python3
"""Hermes Chrome — stdio MCP server for Grok / Cursor / Claude / other agents.

Makes the local bridge + Chrome extension usable as MCP tools after a one-time
install. Pure stdlib (no pip deps).

  Grok example (~/.grok/config.toml):
    [mcp_servers.hermes-chrome]
    command = "/path/to/python"
    args = ["/path/to/hermes-chrome/mcp_server.py"]
    enabled = true
    startup_timeout_sec = 45
    tool_timeout_sec = 120

    [mcp_servers.hermes-chrome.env]
    HERMES_CHROME_ROOT = "/path/to/hermes-chrome"

On start / first tool call the server ensures bridge.py is listening on
127.0.0.1:19876, opens pairing if the extension is quiet, and returns clear
errors when extension_connected is false.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Paths / bridge
# ---------------------------------------------------------------------------

ROOT = Path(
    os.environ.get("HERMES_CHROME_ROOT")
    or Path(__file__).resolve().parent
).resolve()
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))
from version_util import mismatch, product_version  # noqa: E402
BRIDGE_PY = ROOT / "bridge.py"
RUN_DIR = Path(
    os.environ.get("HERMES_CHROME_RUN")
    or Path.home() / ".hermes" / "run" / "hermes-chrome"
)
HOST = os.environ.get("HERMES_CHROME_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_CHROME_BRIDGE_PORT", "19876"))
BRIDGE_URL = f"http://{HOST}:{PORT}"
ENV_FILE = RUN_DIR / "bridge.env"

SERVER_NAME = "hermes-chrome"
SERVER_VERSION = product_version(ROOT)
PROTOCOL_VERSION = "2024-11-05"

# Agent-facing payloads: keep context small; never echo secrets or PNG bytes.
AGENT_JSON_MAX = 12_000
EVAL_VALUE_MAX = 4_000
TAB_TITLE_MAX = 120
TAB_URL_MAX = 240
DEFAULT_TAB_LIMIT = 40
_DROP_KEYS = frozenset(
    {
        "pngBase64",
        "png_base64",
        "token",
        "bridgeToken",
        "authorization",
        "nativeHostPath",
        "pid",
    }
)

_bridge_proc: subprocess.Popen | None = None


def _log(msg: str) -> None:
    """MCP stdio uses stdout for protocol; logs go to stderr."""
    sys.stderr.write(f"[hermes-chrome-mcp] {msg}\n")
    sys.stderr.flush()


def _ellipsis(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    if n <= 1:
        return "…"
    return s[: n - 1] + "…"


def strip_secrets(obj: Any) -> Any:
    """Drop tokens / PNG base64 / host paths from anything returned to an agent."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if k in _DROP_KEYS or kl in {"token", "pngbase64", "authorization"}:
                continue
            out[k] = strip_secrets(v)
        return out
    if isinstance(obj, list):
        return [strip_secrets(x) for x in obj]
    return obj


def compact_health(h: dict[str, Any]) -> dict[str, Any]:
    """One-glance status; internals stay out of the prompt unless not ready."""
    ready = bool(h.get("extension_connected"))
    if ready:
        out: dict[str, Any] = {"ok": True, "ready": True}
        if h.get("auth") is not None:
            out["auth"] = h.get("auth")
        drift = mismatch(h.get("companion_version"), h.get("extension_version"))
        if drift:
            out["update"] = drift
        return strip_secrets(out)
    out = {
        "ok": False,
        "ready": False,
        "error": h.get("error") or "extension_disconnected",
        "hint": h.get("hint")
        or "Companion + Chrome extension, then click the icon once.",
    }
    if "pairing_open" in h:
        out["pairing_open"] = h.get("pairing_open")
    if h.get("auth") is not None:
        out["auth"] = h.get("auth")
    return strip_secrets(out)


def compact_tabs(data: dict[str, Any], *, limit: int | None = None) -> dict[str, Any]:
    cap = limit if limit is not None else DEFAULT_TAB_LIMIT
    cap = max(1, min(int(cap), 80))
    tabs_out: list[dict[str, Any]] = []
    for t in data.get("tabs") or []:
        if not isinstance(t, dict):
            continue
        tabs_out.append(
            {
                "id": t.get("id") if t.get("id") is not None else t.get("tabId"),
                "title": _ellipsis(str(t.get("title") or ""), TAB_TITLE_MAX),
                "url": _ellipsis(str(t.get("url") or ""), TAB_URL_MAX),
                "active": bool(t.get("active")),
            }
        )
        if len(tabs_out) >= cap:
            break
    return {"ok": True, "n": len(tabs_out), "tabs": tabs_out}


def compact_nav(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": data.get("ok", True) is not False,
        "tabId": data.get("tabId") if data.get("tabId") is not None else data.get("id"),
        "url": _ellipsis(str(data.get("url") or ""), TAB_URL_MAX),
        "mode": data.get("mode"),
    }


def compact_eval(data: dict[str, Any]) -> dict[str, Any]:
    val: Any = data.get("value") if "value" in data else data.get("result", data)
    if isinstance(val, str) and len(val) > EVAL_VALUE_MAX:
        val = val[:EVAL_VALUE_MAX] + "…"
    elif isinstance(val, (dict, list)):
        raw = json.dumps(val, ensure_ascii=False, separators=(",", ":"))
        if len(raw) > EVAL_VALUE_MAX:
            val = raw[:EVAL_VALUE_MAX] + "…"
    out: dict[str, Any] = {"ok": data.get("ok", True) is not False, "value": val}
    if data.get("tabId") is not None:
        out["tabId"] = data.get("tabId")
    if data.get("error"):
        out["ok"] = False
        out["error"] = data.get("error")
    return out


def cap_json(obj: Any, budget: int = AGENT_JSON_MAX) -> Any:
    obj = strip_secrets(obj)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if len(raw) <= budget:
        return obj
    preview = raw[: max(0, budget - 24)] + "…"
    return {"ok": False, "truncated": True, "chars": len(raw), "preview": preview}


def _load_token() -> str:
    tok = (
        os.environ.get("HERMES_CHROME_BRIDGE_TOKEN")
        or os.environ.get("HERMES_TABGROUP_BRIDGE_TOKEN")
        or ""
    ).strip()
    if tok:
        return tok
    if not ENV_FILE.is_file():
        return ""
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if not line.startswith("HERMES_CHROME_BRIDGE_TOKEN="):
                continue
            _, _, val = line.partition("=")
            return val.strip().strip("'").strip('"')
    except OSError:
        return ""
    return ""


def _headers(json_body: bool = False) -> dict[str, str]:
    h: dict[str, str] = {}
    if json_body:
        h["Content-Type"] = "application/json"
    tok = _load_token()
    if tok:
        h["X-Hermes-Chrome-Token"] = tok
    return h


def _http_json(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    url = f"{BRIDGE_URL}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=data,
        method=method,
        headers=_headers(json_body=body is not None),
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.getcode() or 200
            if not raw:
                return code, None
            return code, json.loads(raw.decode("utf-8"))
    except HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {"ok": False, "error": raw.decode("utf-8", errors="replace")[:500]}
        return e.code, payload
    except URLError as e:
        return 0, {"ok": False, "error": f"bridge unreachable: {e.reason}"}


def health() -> dict[str, Any]:
    code, payload = _http_json("GET", "/v1/health", timeout=2.0)
    if code == 200 and isinstance(payload, dict):
        return payload
    return {
        "ok": False,
        "bridge": "down",
        "error": (payload or {}).get("error") if isinstance(payload, dict) else "down",
    }


def ensure_bridge(timeout_s: float = 8.0) -> dict[str, Any]:
    """Start bridge.py if /v1/health is down. Returns health payload."""
    global _bridge_proc
    h = health()
    if h.get("ok") is True or h.get("service") == "hermes-chrome-bridge":
        return h

    if not BRIDGE_PY.is_file():
        return {
            "ok": False,
            "error": f"missing bridge.py at {BRIDGE_PY}",
            "hint": "Set HERMES_CHROME_ROOT to the hermes-chrome repo root",
        }

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RUN_DIR / "bridge.mcp.log"
    env = os.environ.copy()
    env["HERMES_CHROME_RUN"] = str(RUN_DIR)
    env["HERMES_CHROME_BRIDGE_HOST"] = HOST
    env["HERMES_CHROME_BRIDGE_PORT"] = str(PORT)

    _log(f"starting bridge: {BRIDGE_PY}")
    try:
        log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        _bridge_proc = subprocess.Popen(
            [sys.executable, str(BRIDGE_PY)],
            cwd=str(ROOT),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        (RUN_DIR / "bridge.pid").write_text(str(_bridge_proc.pid), encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"failed to start bridge: {e}"}

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        h = health()
        if h.get("ok") is True or h.get("service") == "hermes-chrome-bridge":
            # Nudge pairing so CWS extension can auto-pair while quiet.
            _http_json("POST", "/v1/pair-open", body={}, timeout=3.0)
            return h
        time.sleep(0.15)

    return {
        "ok": False,
        "error": f"bridge did not become healthy within {timeout_s}s",
        "log": str(log_path),
        "hint": "Check Python can run bridge.py; ensure port 19876 is free",
    }


def wait_extension(timeout_s: float = 25.0) -> dict[str, Any]:
    """Wait until extension_connected, re-opening pairing periodically."""
    ensure_bridge()
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = health()
        if last.get("extension_connected"):
            return last
        if int(time.time()) % 5 == 0:
            _http_json("POST", "/v1/pair-open", body={}, timeout=2.0)
        time.sleep(0.5)
    last = last or health()
    last = dict(last)
    last["ok"] = False
    last["error"] = (
        "extension not connected. Install/enable Hermes Chrome, click the icon "
        "once, and wait for auto-pair (or popup → Pair). "
        f"health={json.dumps({k: last.get(k) for k in ('auth','pairing_open','extension_last_seen_s','extension_version')})}"
    )
    return last


def send_command(action: str, extra: dict[str, Any] | None = None, *, wait_s: float = 30.0) -> dict[str, Any]:
    """Enqueue command and wait for extension result."""
    h = wait_extension(timeout_s=min(25.0, wait_s))
    if not h.get("extension_connected"):
        return h

    payload: dict[str, Any] = {"id": str(uuid.uuid4()), "action": action}
    if extra:
        payload.update(extra)

    code, enq = _http_json("POST", "/v1/command", body=payload, timeout=15.0)
    if code != 200 or not isinstance(enq, dict) or not enq.get("id"):
        return {
            "ok": False,
            "error": f"enqueue failed HTTP {code}",
            "detail": enq,
        }
    rid = enq["id"]
    rcode, result = _http_json(
        "GET", f"/v1/result/{rid}?timeout={int(max(5, wait_s))}", timeout=wait_s + 15
    )
    if rcode != 200 or not isinstance(result, dict):
        return {
            "ok": False,
            "error": f"result failed HTTP {rcode}",
            "detail": result,
        }
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or result,
        }
    data = result.get("data")
    return data if isinstance(data, dict) else {"ok": True, "data": data}


def _save_capture_payload(
    data: dict[str, Any],
    *,
    prefer: str = "auto",
    out: str | None = None,
) -> dict[str, Any]:
    """Persist pngBase64 from extension capture payload; never return base64 to agents."""
    import base64
    from datetime import datetime, timezone

    b64 = data.get("pngBase64") or ""
    meta = {
        k: data.get(k)
        for k in ("title", "url", "tabId", "prefer", "bytes")
        if k in data
    }
    meta["ok"] = True
    if not b64:
        meta["ok"] = False
        meta["error"] = "no pngBase64 in capture response"
        return meta
    raw = base64.b64decode(b64)
    if out:
        path = Path(out).expanduser()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = RUN_DIR / f"capture-{prefer}-{stamp}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    meta["path"] = str(path.resolve())
    meta["saved_bytes"] = len(raw)
    return meta


def capture_png(
    prefer: str = "auto",
    out: str | None = None,
    *,
    tab_id: int | None = None,
    url_includes: str | None = None,
    title_includes: str | None = None,
) -> dict[str, Any]:
    """Capture tab PNG for any site; strip base64 after saving path."""
    extra: dict[str, Any] = {
        "prefer": prefer,
        "settleMs": 600,
    }
    if prefer == "active" or prefer in ("gc", "nq"):
        extra["allowCrossWorkspace"] = True
    if prefer == "active":
        extra["allowActiveCapture"] = True
    if tab_id is not None:
        extra["tabId"] = int(tab_id)
    if url_includes:
        extra["urlIncludes"] = url_includes
    if title_includes:
        extra["titleIncludes"] = title_includes

    h = wait_extension()
    if not h.get("extension_connected"):
        return h

    payload = {"id": str(uuid.uuid4()), "action": "capture", **extra}
    code, enq = _http_json("POST", "/v1/command", body=payload, timeout=15.0)
    if code != 200 or not isinstance(enq, dict):
        return {"ok": False, "error": "enqueue capture failed", "detail": enq}
    rid = enq.get("id") or payload["id"]
    rcode, result = _http_json("GET", f"/v1/result/{rid}?timeout=75", timeout=90.0)
    if rcode != 200 or not isinstance(result, dict) or not result.get("ok"):
        return {
            "ok": False,
            "error": (result or {}).get("error") if isinstance(result, dict) else result,
        }
    data = result.get("data") or {}
    return _save_capture_payload(data, prefer=prefer, out=out)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "hermes_chrome_status",
        "description": (
            "Hermes Chrome health. Check ready=true before other ops."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "wait_extension_s": {
                    "type": "number",
                    "description": "Optional seconds to wait for extension (0=no wait)",
                    "default": 0,
                }
            },
        },
    },
    {
        "name": "hermes_chrome_ping",
        "description": "Ping via the extension. Requires ready=true.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hermes_chrome_list_tabs",
        "description": (
            "List workspace tabs as compact {id,title,url,active}. "
            "all_tabs=true is privacy-sensitive. Filter with url_includes / title_includes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "all_tabs": {"type": "boolean", "default": False},
                "url_includes": {"type": "string"},
                "title_includes": {"type": "string"},
                "limit": {"type": "integer", "description": "Max tabs (default 40, max 80)"},
            },
        },
    },
    {
        "name": "hermes_chrome_list_tv",
        "description": (
            "Optional: TradingView-related tabs. Prefer list_tabs + url_includes."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "hermes_chrome_capture",
        "description": (
            "Capture visible tab; returns file path + tab meta, never PNG bytes. "
            "prefer=auto workspace, active=focused (opt-in). Prefer tab_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prefer": {
                    "type": "string",
                    "enum": ["auto", "active", "gc", "nq"],
                    "default": "auto",
                },
                "out": {
                    "type": "string",
                    "description": "Optional output .png path",
                },
                "url_includes": {
                    "type": "string",
                    "description": "Optional URL substring to select a tab (any site)",
                },
                "title_includes": {
                    "type": "string",
                    "description": "Optional title substring to select a tab",
                },
                "tab_id": {
                    "type": "integer",
                    "description": "Capture this tab id (from list_tabs)",
                },
            },
        },
    },
    {
        "name": "hermes_chrome_open",
        "description": (
            "Open http(s) URL in the workspace (inactive tab). Returns tabId."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "hermes_chrome_stop",
        "description": (
            "Close the agent workspace Tab Group (not the bridge)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "close_tabs": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "If true (default), close tabs in the workspace. "
                        "If false, ungroup tabs but leave them open."
                    ),
                },
            },
        },
    },
    {
        "name": "hermes_chrome_navigate",
        "description": "Navigate a tab (or workspace default) to url. Returns tabId.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "tab_id": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "hermes_chrome_eval",
        "description": (
            "Eval JS in a tab (MAIN world). tab_id from list_tabs. Value truncated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer"},
                "expression": {"type": "string"},
            },
            "required": ["tab_id", "expression"],
        },
    },
    {
        "name": "hermes_chrome_click",
        "description": "Click a CSS selector in a tab.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer"},
                "selector": {"type": "string"},
            },
            "required": ["tab_id", "selector"],
        },
    },
    {
        "name": "hermes_chrome_type",
        "description": "Type text into a CSS selector in a tab.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "integer"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "append": {"type": "boolean", "default": False},
            },
            "required": ["tab_id", "selector", "text"],
        },
    },
]


def _tool_result(obj: Any, *, is_error: bool = False) -> dict[str, Any]:
    obj = cap_json(strip_secrets(obj)) if not isinstance(obj, str) else obj
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    try:
        if name == "hermes_chrome_status":
            ensure_bridge()
            wait_s = float(args.get("wait_extension_s") or 0)
            if wait_s > 0:
                h = wait_extension(timeout_s=wait_s)
            else:
                h = health()
            compact = compact_health(h)
            return _tool_result(compact, is_error=not compact.get("ok"))

        if name == "hermes_chrome_ping":
            return _tool_result(send_command("ping"))

        if name == "hermes_chrome_list_tabs":
            extra: dict[str, Any] = {}
            if args.get("all_tabs"):
                extra["allTabs"] = True
                extra["groupOnly"] = False
            else:
                extra["groupOnly"] = True
            if args.get("url_includes"):
                extra["urlIncludes"] = str(args["url_includes"])
            if args.get("title_includes"):
                extra["titleIncludes"] = str(args["title_includes"])
            if args.get("limit") is not None:
                extra["limit"] = int(args["limit"])
            else:
                extra["limit"] = DEFAULT_TAB_LIMIT
            data = send_command("list_tabs", extra)
            if isinstance(data, dict) and data.get("tabs") is not None:
                return _tool_result(compact_tabs(data, limit=extra["limit"]))
            return _tool_result(data, is_error=not data.get("ok", True) if isinstance(data, dict) else False)

        if name == "hermes_chrome_list_tv":
            data = send_command("list_tv")
            if isinstance(data, dict) and data.get("tabs") is not None:
                return _tool_result(compact_tabs(data))
            return _tool_result(data)

        if name == "hermes_chrome_capture":
            prefer = str(args.get("prefer") or "auto")
            out = args.get("out")
            return _tool_result(
                capture_png(
                    prefer=prefer,
                    out=out,
                    tab_id=int(args["tab_id"]) if args.get("tab_id") is not None else None,
                    url_includes=str(args["url_includes"]) if args.get("url_includes") else None,
                    title_includes=str(args["title_includes"]) if args.get("title_includes") else None,
                )
            )

        if name == "hermes_chrome_open":
            url = str(args.get("url") or "")
            if not url:
                return _tool_result({"ok": False, "error": "url required"}, is_error=True)
            data = send_command("open", {"url": url})
            if isinstance(data, dict) and not data.get("error"):
                return _tool_result(compact_nav(data))
            return _tool_result(data, is_error=True)

        if name == "hermes_chrome_stop":
            close_tabs = args.get("close_tabs")
            if close_tabs is None:
                close_tabs = True
            return _tool_result(
                send_command("stop", {"closeTabs": bool(close_tabs)})
            )

        if name == "hermes_chrome_navigate":
            url = str(args.get("url") or "")
            if not url:
                return _tool_result({"ok": False, "error": "url required"}, is_error=True)
            extra = {"url": url, "active": False}
            if args.get("tab_id") is not None:
                extra["tabId"] = int(args["tab_id"])
            data = send_command("navigate", extra)
            if isinstance(data, dict) and not data.get("error"):
                return _tool_result(compact_nav(data))
            return _tool_result(data, is_error=True)

        if name == "hermes_chrome_eval":
            data = send_command(
                "eval",
                {
                    "tabId": int(args["tab_id"]),
                    "expression": str(args["expression"]),
                },
            )
            if isinstance(data, dict):
                return _tool_result(
                    compact_eval(data),
                    is_error=data.get("ok") is False or bool(data.get("error")),
                )
            return _tool_result(data)

        if name == "hermes_chrome_click":
            return _tool_result(
                send_command(
                    "click",
                    {
                        "tabId": int(args["tab_id"]),
                        "selector": str(args["selector"]),
                    },
                )
            )

        if name == "hermes_chrome_type":
            return _tool_result(
                send_command(
                    "type",
                    {
                        "tabId": int(args["tab_id"]),
                        "selector": str(args["selector"]),
                        "text": str(args.get("text") or ""),
                        "clear": not bool(args.get("append")),
                    },
                )
            )

        return _tool_result({"ok": False, "error": f"unknown tool: {name}"}, is_error=True)
    except Exception as e:  # noqa: BLE001 — surface to MCP client
        return _tool_result({"ok": False, "error": f"{type(e).__name__}: {e}"}, is_error=True)


# ---------------------------------------------------------------------------
# MCP stdio framing (Content-Length)
# ---------------------------------------------------------------------------

def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        try:
            text = line.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if ":" not in text:
            continue
        k, _, v = text.partition(":")
        headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(msg: dict[str, Any]) -> None:
    raw = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw)
    sys.stdout.buffer.flush()


def _handle(msg: dict[str, Any]) -> None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    # Notifications (no id) — ignore unknown
    if mid is None:
        if method == "notifications/initialized":
            _log("client initialized")
            # Eager bridge so extension can auto-pair while user talks to agent
            h = ensure_bridge()
            _log(f"bridge health after init: connected={h.get('extension_connected')} auth={h.get('auth')}")
        return

    if method == "initialize":
        _write_message(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": (
                        "Local Chrome only. Call status until ready=true, then open / "
                        "list_tabs / capture / eval. Capture returns a file path, not PNG. "
                        "Do not put tokens or native-host details in follow-up prompts."
                    ),
                },
            }
        )
        return

    if method == "ping":
        _write_message({"jsonrpc": "2.0", "id": mid, "result": {}})
        return

    if method == "tools/list":
        _write_message(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"tools": TOOLS},
            }
        )
        return

    if method == "tools/call":
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        result = call_tool(str(name), arguments if isinstance(arguments, dict) else {})
        _write_message({"jsonrpc": "2.0", "id": mid, "result": result})
        return

    _write_message(
        {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    )


def main() -> int:
    _log(f"starting MCP server root={ROOT} bridge={BRIDGE_URL}")
    # Pre-warm bridge so extension can connect before first tool call
    try:
        ensure_bridge()
    except Exception as e:  # noqa: BLE001
        _log(f"ensure_bridge at startup failed: {e}")

    while True:
        try:
            msg = _read_message()
        except Exception as e:  # noqa: BLE001
            _log(f"read error: {e}")
            return 1
        if msg is None:
            _log("stdin closed")
            return 0
        try:
            _handle(msg)
        except Exception as e:  # noqa: BLE001
            _log(f"handle error: {e}")
            mid = msg.get("id")
            if mid is not None:
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32000, "message": str(e)},
                    }
                )


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Shared local bridge helpers for MCP + Native Messaging host.

Stdlib only. Safe to import from mcp_server.py and native_host/host.py.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(
    os.environ.get("HERMES_CHROME_ROOT")
    or Path(__file__).resolve().parents[1]
).resolve()
BRIDGE_PY = ROOT / "bridge.py"
RUN_DIR = Path(
    os.environ.get("HERMES_CHROME_RUN")
    or Path.home() / ".hermes" / "run" / "hermes-chrome"
)
HOST = (
    os.environ.get("HERMES_CHROME_BRIDGE_HOST")
    or os.environ.get("HERMES_TABGROUP_BRIDGE_HOST")
    or "127.0.0.1"
)
PORT = int(
    os.environ.get("HERMES_CHROME_BRIDGE_PORT")
    or os.environ.get("HERMES_TABGROUP_BRIDGE_PORT")
    or "19876"
)
_URL_HOST = f"[{HOST}]" if ":" in HOST and not HOST.startswith("[") else HOST
BRIDGE_URL = f"http://{_URL_HOST}:{PORT}"
ENV_FILE = RUN_DIR / "bridge.env"

# Chrome Web Store id (stable). Unpacked builds pin the same id via manifest key.
CWS_EXTENSION_ID = "mkoaoadlkijccmmbkioagnlngbbeocfa"
NATIVE_HOST_NAME = "com.leaf76.hermes_chrome"

# Keep in sync with bridge.py _MAX_RESULT_BYTES (base64 JSON over /v1/result).
BRIDGE_MAX_RESULT_BYTES = int(
    os.environ.get("HERMES_CHROME_BRIDGE_MAX_RESULT", str(12 * 1024 * 1024))
)
# Raw body limit for extension fetch_url → base64 result payloads.
BRIDGE_FETCH_MAX_RAW_BYTES = max(
    1024, (BRIDGE_MAX_RESULT_BYTES * 3) // 4 - 4096
)

_bridge_proc: subprocess.Popen | None = None


def log(msg: str, *, prefix: str = "hermes-chrome") -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")
    sys.stderr.flush()


def load_token() -> str:
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
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() not in (
                "HERMES_CHROME_BRIDGE_TOKEN",
                "HERMES_TABGROUP_BRIDGE_TOKEN",
            ):
                continue
            return val.strip().strip("'").strip('"')
    except OSError:
        return ""
    return ""


def auth_headers(*, json_body: bool = False) -> dict[str, str]:
    h: dict[str, str] = {}
    if json_body:
        h["Content-Type"] = "application/json"
    tok = load_token()
    if tok:
        h["X-Hermes-Chrome-Token"] = tok
    return h


def http_json(
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
        headers=auth_headers(json_body=body is not None),
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
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
            payload = {
                "ok": False,
                "error": raw.decode("utf-8", errors="replace")[:500],
            }
        return e.code, payload
    except URLError as e:
        return 0, {"ok": False, "error": f"bridge unreachable: {e.reason}"}
    except (TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError) as e:
        return 0, {"ok": False, "error": f"bridge error: {e}"}


# Hot path (MCP tool call → ensure_bridge + wait_extension) used to pay two
# /v1/health round trips per command. Cache only *connected* payloads for a
# short TTL; negative results are never cached so reconnect detection keeps
# its ~0.5s polling cadence.
HEALTH_TTL_S = 2.0
_health_cache: tuple[float, dict[str, Any]] | None = None


def health(*, max_age_s: float = HEALTH_TTL_S) -> dict[str, Any]:
    """GET /v1/health; caches only extension-connected results for max_age_s."""
    global _health_cache
    now = time.monotonic()
    if max_age_s > 0 and _health_cache is not None:
        at, cached = _health_cache
        if now - at <= max_age_s:
            return cached
    code, payload = http_json("GET", "/v1/health", timeout=2.0)
    _health_cache = None
    if code == 200 and isinstance(payload, dict):
        if payload.get("extension_connected"):
            _health_cache = (time.monotonic(), payload)
        return payload
    err = None
    if isinstance(payload, dict):
        err = payload.get("error")
    return {"ok": False, "bridge": "down", "error": err or "down"}


def health_fresh() -> dict[str, Any]:
    """Force a network /v1/health round trip (bypass the TTL cache)."""
    return health(max_age_s=0)


def bridge_up(h: dict[str, Any] | None = None) -> bool:
    h = h if h is not None else health()
    return h.get("service") == "hermes-chrome-bridge" and h.get("ok") is True


def pair_open() -> dict[str, Any]:
    code, payload = http_json("POST", "/v1/pair-open", body={}, timeout=3.0)
    if isinstance(payload, dict):
        return payload
    return {"ok": code == 200}


def _acquire_start_lock(lock_path: Path, stale_s: float = 15.0) -> bool:
    try:
        if lock_path.exists():
            age = time.time() - lock_path.stat().st_mtime
            if age > stale_s:
                lock_path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    except OSError:
        return False


def ensure_bridge(
    timeout_s: float = 8.0,
    *,
    log_name: str = "bridge.runtime.log",
    prefix: str = "hermes-chrome",
) -> dict[str, Any]:
    """Start bridge.py if health is down. Returns health payload."""
    global _bridge_proc
    if _bridge_proc is not None and _bridge_proc.poll() is not None:
        _bridge_proc = None

    h = health()
    if bridge_up(h):
        return h

    if not BRIDGE_PY.is_file():
        return {
            "ok": False,
            "error": f"missing bridge.py at {BRIDGE_PY}",
            "hint": "Set HERMES_CHROME_ROOT or reinstall the companion",
        }

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUN_DIR / "bridge.start.lock"
    have_lock = _acquire_start_lock(lock_path)
    log_path = RUN_DIR / log_name
    try:
        if have_lock:
            # Re-check after acquiring lock in case another process just started it.
            h = health_fresh()
            if bridge_up(h):
                return h

            env = os.environ.copy()
            env["HERMES_CHROME_RUN"] = str(RUN_DIR)
            env["HERMES_CHROME_BRIDGE_HOST"] = HOST
            env["HERMES_CHROME_BRIDGE_PORT"] = str(PORT)
            env.setdefault("HERMES_CHROME_ROOT", str(ROOT))

            log(f"starting bridge: {BRIDGE_PY}", prefix=prefix)
            log_f = None
            try:
                log_f = open(log_path, "a", encoding="utf-8")
                kwargs: dict[str, Any] = {
                    "cwd": str(ROOT),
                    "env": env,
                    "stdout": log_f,
                    "stderr": subprocess.STDOUT,
                    "stdin": subprocess.DEVNULL,
                }
                # Detach so host/MCP exit does not kill bridge.
                if os.name == "nt":
                    # Detach without a console window; do not kill with parent.
                    flags = 0
                    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    kwargs["creationflags"] = flags
                else:
                    kwargs["start_new_session"] = True
                _bridge_proc = subprocess.Popen(
                    [sys.executable, str(BRIDGE_PY)],
                    **kwargs,
                )
                (RUN_DIR / "bridge.pid").write_text(str(_bridge_proc.pid), encoding="utf-8")
            except OSError as e:
                return {"ok": False, "error": f"failed to start bridge: {e}"}
            finally:
                if log_f is not None:
                    try:
                        log_f.close()
                    except OSError:
                        pass

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            h = health()
            if bridge_up(h):
                pair_open()
                return h
            time.sleep(0.15)
    finally:
        if have_lock:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "ok": False,
        "error": f"bridge did not become healthy within {timeout_s}s",
        "log": str(log_path),
        "hint": "Check Python can run bridge.py; ensure port 19876 is free",
    }


def ensure_and_pair(timeout_s: float = 8.0, **kwargs: Any) -> dict[str, Any]:
    h = ensure_bridge(timeout_s=timeout_s, **kwargs)
    if bridge_up(h):
        pair_open()
        h = health()
    out = dict(h) if isinstance(h, dict) else {"ok": False, "error": h}
    out["bridge_url"] = BRIDGE_URL
    out["root"] = str(ROOT)
    out["native_host"] = NATIVE_HOST_NAME
    return out


def wait_extension(
    timeout_s: float = 25.0,
    *,
    ensure: bool = True,
    log_name: str = "bridge.runtime.log",
    prefix: str = "hermes-chrome",
) -> dict[str, Any]:
    """Wait until extension_connected; re-open pairing periodically."""
    if ensure:
        ensure_bridge(timeout_s=min(8.0, max(1.0, timeout_s)), log_name=log_name, prefix=prefix)
    last = health()
    if last.get("extension_connected"):
        return last
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_pair_at = 0.0
    while time.monotonic() < deadline:
        last = health()
        if last.get("extension_connected"):
            return last
        now = time.monotonic()
        if now - last_pair_at >= 5.0:
            pair_open()
            last_pair_at = now
        time.sleep(0.5)
    last = dict(last or health())
    last["ok"] = False
    last["error"] = (
        "extension not connected. Install/enable Hermes Chrome, click the icon "
        "once, and wait for auto-pair (or popup → Pair). "
        "Repair: hermes-chrome doctor --fix"
    )
    return last

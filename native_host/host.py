#!/usr/bin/env python3
"""Chrome Native Messaging host for Hermes Chrome.

Chrome launches this when the extension calls connectNative("com.leaf76.hermes_chrome").
The host ensures the local HTTP bridge is running, then stays on the native port
until Chrome disconnects.

Protocol: 4-byte little-endian length + UTF-8 JSON (Chrome native messaging).
Stdout is the native channel — never print logs to stdout (use stderr).
"""

from __future__ import annotations

import json
import struct
import sys
import time
from pathlib import Path

# Allow `import bridge_runtime` from repo lib/
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bridge_runtime import (  # noqa: E402
    NATIVE_HOST_NAME,
    ROOT,
    ensure_and_pair,
    health,
    log,
)
from version_util import product_version  # noqa: E402

HOST_VERSION = product_version(ROOT)


_MAX_NATIVE_MSG_BYTES = 1024 * 1024


def _read_exact(stream, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _write_all(stream, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(data):
        n = stream.write(view[written:])
        if n is None:
            written = len(data)
            break
        if n == 0:
            raise OSError("short write to native host stdout")
        written += n


def read_message() -> dict | None:
    raw_len = _read_exact(sys.stdin.buffer, 4)
    if not raw_len:
        return None
    (length,) = struct.unpack("<I", raw_len)
    if length <= 0 or length > _MAX_NATIVE_MSG_BYTES:
        return None
    data = _read_exact(sys.stdin.buffer, length)
    if not data:
        return None
    return json.loads(data.decode("utf-8"))


def send_message(obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_NATIVE_MSG_BYTES:
        raw = json.dumps(
            {"ok": False, "error": "native messaging response exceeded 1 MiB limit"},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    _write_all(sys.stdout.buffer, struct.pack("<I", len(raw)))
    _write_all(sys.stdout.buffer, raw)
    sys.stdout.buffer.flush()


def handle(msg: dict) -> dict:
    action = str(msg.get("action") or msg.get("type") or "ensure").lower()
    if action in ("ping", "hello"):
        return {
            "ok": True,
            "pong": True,
            "host": NATIVE_HOST_NAME,
            "version": HOST_VERSION,
            "root": str(ROOT),
        }
    if action in ("health", "status"):
        h = health()
        h = dict(h) if isinstance(h, dict) else {"ok": False}
        h["host"] = NATIVE_HOST_NAME
        h["host_version"] = HOST_VERSION
        h["root"] = str(ROOT)
        return h
    if action in ("pair_open", "pair-open", "open_pair", "pair"):
        h = ensure_and_pair(timeout_s=10.0, log_name="bridge.native.log", prefix="native-host")
        h["host"] = NATIVE_HOST_NAME
        h["host_version"] = HOST_VERSION
        h["action"] = "pair_open"
        return h
    # default: ensure bridge + open pairing
    h = ensure_and_pair(timeout_s=10.0, log_name="bridge.native.log", prefix="native-host")
    h["host"] = NATIVE_HOST_NAME
    h["host_version"] = HOST_VERSION
    h["action"] = "ensure"
    return h


def main() -> int:
    log(f"native host start version={HOST_VERSION} root={ROOT}", prefix="native-host")
    # Ensure bridge is ready without emitting an unsolicited stdout frame before
    # reading Chrome's first request (sendNativeMessage takes the 1st frame as reply).
    try:
        ensure_and_pair(
            timeout_s=10.0, log_name="bridge.native.log", prefix="native-host"
        )
    except Exception as e:  # noqa: BLE001
        log(f"boot ensure failed: {e}", prefix="native-host")

    while True:
        try:
            msg = read_message()
        except Exception as e:  # noqa: BLE001
            log(f"read error: {e}", prefix="native-host")
            return 1
        if msg is None:
            log("stdin closed — chrome disconnected", prefix="native-host")
            return 0
        try:
            reply = handle(msg if isinstance(msg, dict) else {})
        except Exception as e:  # noqa: BLE001
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        try:
            send_message(reply)
        except Exception as e:  # noqa: BLE001
            log(f"write error: {e}", prefix="native-host")
            return 1
        # Soft idle: stay alive for more messages from the extension.
        time.sleep(0.01)


if __name__ == "__main__":
    raise SystemExit(main())

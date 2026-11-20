#!/usr/bin/env python3
"""Regression tests for native_host/host.py binary messaging framing."""

from __future__ import annotations

import io
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from native_host import host as nh  # noqa: E402


class _FakeStdStream:
    def __init__(self, initial: bytes = b"") -> None:
        self.buffer = io.BytesIO(initial)


class NativeHostTests(unittest.TestCase):
    def test_ping_roundtrip_has_no_unsolicited_boot_frame(self) -> None:
        req = json.dumps({"action": "ping"}).encode("utf-8")
        frame = struct.pack("<I", len(req)) + req
        fake_in = _FakeStdStream(frame)
        fake_out = _FakeStdStream()

        with mock.patch("sys.stdin", fake_in), mock.patch("sys.stdout", fake_out):
            with (
                mock.patch.object(nh, "ensure_and_pair", return_value={"ok": False}),
                mock.patch.object(nh, "health", return_value={"ok": False}),
            ):
                rc = nh.main()

        self.assertEqual(rc, 0)
        raw = fake_out.buffer.getvalue()
        self.assertGreaterEqual(len(raw), 4)
        first_len = struct.unpack("<I", raw[:4])[0]
        self.assertEqual(len(raw), 4 + first_len)
        msg = json.loads(raw[4:].decode("utf-8"))
        self.assertTrue(msg["ok"])
        self.assertTrue(msg["pong"])
        self.assertEqual(msg["host"], "com.leaf76.hermes_chrome")

    def test_truncated_eof_returns_cleanly(self) -> None:
        fake_in = _FakeStdStream(b"\x04\x00")
        fake_out = _FakeStdStream()
        with mock.patch("sys.stdin", fake_in), mock.patch.object(sys, "stdout", fake_out):
            self.assertIsNone(nh.read_message())
        fake_in2 = _FakeStdStream(b"\x04\x00")
        with mock.patch("sys.stdin", fake_in2), mock.patch("sys.stdout", fake_out):
            with mock.patch.object(nh, "ensure_and_pair", return_value={"ok": False}):
                self.assertEqual(nh.main(), 0)
        self.assertEqual(fake_out.buffer.getvalue(), b"")


if __name__ == "__main__":
    unittest.main()

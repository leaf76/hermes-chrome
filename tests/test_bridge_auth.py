#!/usr/bin/env python3
"""HTTP smoke tests for bridge auth (ephemeral subprocess, no Chrome)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BridgeAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls._tmp.name)
        cls.token = "test-bridge-token-smoke"
        (cls.run_dir / "bridge.env").write_text(
            f"export HERMES_CHROME_BRIDGE_TOKEN='{cls.token}'\n",
            encoding="utf-8",
        )
        cls.port = _free_port()
        env = os.environ.copy()
        env["HERMES_CHROME_RUN"] = str(cls.run_dir)
        env["HERMES_CHROME_BRIDGE_HOST"] = "127.0.0.1"
        env["HERMES_CHROME_BRIDGE_PORT"] = str(cls.port)
        env["HERMES_CHROME_BRIDGE_TOKEN"] = cls.token
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "bridge.py")],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{cls.base}/v1/health", timeout=0.5) as resp:
                    if resp.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.1)
        else:
            cls.proc.kill()
            raise RuntimeError("bridge did not start for auth tests")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls._tmp.cleanup()

    def _post(self, path: str, body: dict, *, token: str | None = None) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Hermes-Chrome-Token"] = token
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                payload = json.loads(raw.decode("utf-8") or "{}")
                return resp.status, payload
        except urllib.error.HTTPError as e:
            raw = e.read() if e.fp else b""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {"ok": False, "error": raw.decode("utf-8", errors="replace")}
            return e.code, payload

    def test_public_health_is_minimal(self) -> None:
        with urllib.request.urlopen(f"{self.base}/v1/health", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload.get("ok"))
        self.assertNotIn("pairing_open", payload)

    def test_authed_health_includes_detail(self) -> None:
        req = urllib.request.Request(
            f"{self.base}/v1/health",
            headers={"X-Hermes-Chrome-Token": self.token},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertIn("pairing_open", payload)

    def test_command_without_token_is_401(self) -> None:
        code, payload = self._post("/v1/command", {"action": "ping"})
        self.assertEqual(code, 401)
        self.assertFalse(payload.get("ok", True))

    def test_command_with_token_queues(self) -> None:
        code, payload = self._post(
            "/v1/command",
            {"action": "ping"},
            token=self.token,
        )
        self.assertEqual(code, 200)
        self.assertTrue(payload.get("queued"))
        self.assertTrue(payload.get("id"))


if __name__ == "__main__":
    unittest.main()

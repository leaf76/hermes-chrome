#!/usr/bin/env python3
"""HTTP smoke tests for bridge auth (ephemeral subprocess, no Chrome)."""

from __future__ import annotations

import http.client
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

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
            try:
                raw = e.read() if e.fp else b""
            finally:
                e.close()
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
        # Drain queued ping so subsequent poll tests see a clean queue.
        req = urllib.request.Request(
            f"{self.base}/v1/poll?timeout=1",
            headers={"X-Hermes-Chrome-Token": self.token},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            polled = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(polled.get("id"), payload.get("id"))
        self._post("/v1/result", {"id": polled["id"], "ok": True, "data": {"drained": True}}, token=self.token)

    def test_host_header_rejection_403(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.putrequest("GET", "/v1/health", skip_host=True)
            conn.putheader("Host", "evil.example:19876")
            conn.endheaders()
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 403)
        finally:
            conn.close()

    def test_401_before_body_read_on_malformed_body(self) -> None:
        req = urllib.request.Request(
            f"{self.base}/v1/command",
            data=b"{not-valid-json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 401)
        ctx.exception.close()

    def test_pair_origin_cors_and_single_use(self) -> None:
        code, _ = self._post("/v1/pair-open", {}, token=self.token)
        self.assertEqual(code, 200)

        # Invalid Origin rejected
        bad_req = urllib.request.Request(
            f"{self.base}/v1/pair",
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://evil.example",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as bad_ctx:
            urllib.request.urlopen(bad_req, timeout=3)
        self.assertEqual(bad_ctx.exception.code, 403)
        bad_ctx.exception.close()

        # Allowed CWS extension origin succeeds once and echoes exact CORS header
        good_origin = "chrome-extension://mkoaoadlkijccmmbkioagnlngbbeocfa"
        good_req = urllib.request.Request(
            f"{self.base}/v1/pair",
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": good_origin,
            },
        )
        with urllib.request.urlopen(good_req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), good_origin)
            body = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("token"), self.token)

        # Second pair attempt without re-opening fails (single-use)
        reuse_req = urllib.request.Request(
            f"{self.base}/v1/pair",
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": good_origin,
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as reuse_ctx:
            urllib.request.urlopen(reuse_req, timeout=3)
        self.assertEqual(reuse_ctx.exception.code, 403)
        reuse_ctx.exception.close()

    def test_poll_and_result_roundtrip_and_duplicate_409(self) -> None:
        code, q = self._post("/v1/command", {"action": "ping"}, token=self.token)
        self.assertEqual(code, 200)
        cmd_id = q["id"]

        poll_req = urllib.request.Request(
            f"{self.base}/v1/poll?timeout=1",
            headers={"X-Hermes-Chrome-Token": self.token},
        )
        with urllib.request.urlopen(poll_req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            cmd = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(cmd["id"], cmd_id)

        r1_code, r1_body = self._post(
            "/v1/result",
            {"id": cmd_id, "ok": True, "data": {"pong": True}},
            token=self.token,
        )
        self.assertEqual(r1_code, 200)
        self.assertTrue(r1_body.get("ok"))

        r2_code, _ = self._post(
            "/v1/result",
            {"id": cmd_id, "ok": True, "data": {"pong": True}},
            token=self.token,
        )
        self.assertEqual(r2_code, 409)

        res_req = urllib.request.Request(
            f"{self.base}/v1/result/{cmd_id}?timeout=1",
            headers={"X-Hermes-Chrome-Token": self.token},
        )
        with urllib.request.urlopen(res_req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            got = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(got.get("ok"))
        self.assertEqual(got.get("data"), {"pong": True})

    def test_bad_query_param_returns_400(self) -> None:
        req = urllib.request.Request(
            f"{self.base}/v1/poll?timeout=notanumber",
            headers={"X-Hermes-Chrome-Token": self.token},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()


class BridgeHelpersTests(unittest.TestCase):
    def test_update_hermes_config_blocks_yaml_injection_and_targets_model_section(self) -> None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import bridge  # noqa: E402

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            cfg_dir = home / ".hermes"
            cfg_dir.mkdir(parents=True)
            cfg = cfg_dir / "config.yaml"
            cfg.write_text(
                "other:\n"
                "  default: leave-me-alone\n"
                "model:\n"
                "  default: old-model\n"
                "  provider: openrouter\n"
                "  reasoning_effort: medium\n",
                encoding="utf-8",
            )
            with mock.patch("pathlib.Path.home", return_value=home):
                bad = bridge._update_hermes_config({"model": "gpt-4\nmalicious: true"})
                self.assertFalse(bad.get("saved"))
                bad_prov = bridge._update_hermes_config({"provider": "custom"})
                self.assertFalse(bad_prov.get("saved"))
                good = bridge._update_hermes_config(
                    {"model": "gemini-3.8-flash", "provider": "gemini", "reasoning_effort": "high"}
                )
                self.assertTrue(good.get("saved"))
                updated = cfg.read_text(encoding="utf-8")
                self.assertIn("other:\n  default: leave-me-alone", updated)
                self.assertIn("default: gemini-3.8-flash", updated)
                self.assertIn("provider: gemini", updated)
                self.assertIn("reasoning_effort: high", updated)

    def test_run_hermes_cli_prompt_rejects_flags_and_cmd_wrappers(self) -> None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import bridge  # noqa: E402

        with mock.patch.object(bridge, "_resolve_hermes_binary", return_value=("C:\\fake\\hermes.cmd", None)):
            # Model starting with dash rejected before CLI exec
            r1 = bridge._run_hermes_cli_prompt("hello", model="-flag")
            self.assertFalse(r1.get("ok"))
            # Session starting with dash rejected
            r2 = bridge._run_hermes_cli_prompt("hello", session_id="--resume=other")
            self.assertFalse(r2.get("ok"))
            # Provider invalid rejected
            r3 = bridge._run_hermes_cli_prompt("hello", provider="bad;prov")
            self.assertFalse(r3.get("ok"))

        with tempfile.TemporaryDirectory() as td:
            cmd_path = Path(td) / "hermes.cmd"
            cmd_path.write_text("@echo off\r\n", encoding="utf-8")
            with mock.patch("shutil.which", return_value=str(cmd_path)):
                bin_path, err = bridge._resolve_hermes_binary()
                if sys.platform == "win32":
                    self.assertIsNone(bin_path)
                    self.assertIn("hermes.exe", (err or "").lower())

    def test_session_messages_url_decode_and_newest_n_order(self) -> None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import bridge  # noqa: E402

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, model TEXT, started_at REAL, message_count INTEGER)"
            )
            conn.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, reasoning TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, active INTEGER)"
            )
            sid = "sess/special:1"
            conn.execute("INSERT INTO sessions VALUES (?, 'Test', 'gemini', 1700000000, 3)", (sid,))
            conn.execute("INSERT INTO messages VALUES (1, ?, 'user', 'first', '', NULL, '', 100.0, 1)", (sid,))
            conn.execute("INSERT INTO messages VALUES (2, ?, 'assistant', 'second', '', NULL, '', 200.0, 1)", (sid,))
            conn.execute("INSERT INTO messages VALUES (3, ?, 'user', 'third', '', NULL, '', 300.0, 1)", (sid,))
            conn.commit()
            conn.close()

            orig_tok = bridge.TOKEN
            bridge.TOKEN = "unit-msg-token"
            srv = bridge._BridgeServer(("127.0.0.1", 0), bridge.Handler)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                with mock.patch.object(bridge, "_hermes_state_db_path", return_value=db):
                    enc_sid = urllib.parse.quote(sid, safe="")
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/v1/hermes/sessions/{enc_sid}/messages?limit=2",
                        headers={"X-Hermes-Chrome-Token": "unit-msg-token"},
                    )
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(data.get("ok"))
                    self.assertEqual([m["content"] for m in data["messages"]], ["second", "third"])
            finally:
                srv.shutdown()
                srv.server_close()
                bridge.TOKEN = orig_tok


if __name__ == "__main__":
    unittest.main()

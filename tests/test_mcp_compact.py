#!/usr/bin/env python3
"""Unit tests for agent-facing MCP payload compression (no live Chrome)."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_server as m  # noqa: E402


class CompactTests(unittest.TestCase):
    def test_strip_secrets_drops_png_and_token(self) -> None:
        got = m.strip_secrets(
            {
                "ok": True,
                "token": "secret",
                "pngBase64": "AAAA" * 200,
                "nested": {"png_base64": "xx", "keep": 1},
            }
        )
        self.assertEqual(got, {"ok": True, "nested": {"keep": 1}})

    def test_compact_health_ready_is_tiny(self) -> None:
        got = m.compact_health(
            {
                "ok": True,
                "service": "hermes-chrome-bridge",
                "extension_connected": True,
                "auth": True,
                "companion_version": "1.8.2",
                "extension_version": "1.8.3",
                "root": "/secret/path",
                "bridge_url": "http://127.0.0.1:19876",
                "token": "nope",
            }
        )
        self.assertEqual(got["ok"], True)
        self.assertEqual(got["ready"], True)
        self.assertEqual(got["update"], "companion")
        self.assertIn("self-update", got["fix"])
        self.assertNotIn("root", got)

    def test_compact_health_extension_behind_fix(self) -> None:
        got = m.compact_health(
            {
                "ok": True,
                "extension_connected": True,
                "companion_version": "1.8.4",
                "extension_version": "1.8.3",
            }
        )
        self.assertEqual(got["update"], "extension")
        self.assertIn("Web Store", got["fix"])

    def test_compact_health_aligned_has_no_update(self) -> None:
        got = m.compact_health(
            {
                "ok": True,
                "extension_connected": True,
                "companion_version": "1.8.6",
                "extension_version": "1.8.6",
            }
        )
        self.assertNotIn("update", got)
        self.assertNotIn("fix", got)

    def test_compact_health_not_ready_has_hint(self) -> None:
        got = m.compact_health({"extension_connected": False, "auth": True})
        self.assertFalse(got["ok"])
        self.assertFalse(got["ready"])
        self.assertIn("hint", got)

    def test_compact_tabs_drops_window_status(self) -> None:
        got = m.compact_tabs(
            {
                "tabs": [
                    {
                        "id": 3,
                        "windowId": 99,
                        "groupId": 1,
                        "url": "https://example.com/a",
                        "title": "Example",
                        "active": False,
                        "pinned": False,
                        "status": "complete",
                        "inWorkspace": True,
                    }
                ]
            }
        )
        self.assertEqual(got["n"], 1)
        self.assertEqual(
            got["tabs"][0],
            {"id": 3, "title": "Example", "url": "https://example.com/a", "active": False},
        )

    def test_compact_eval_truncates(self) -> None:
        got = m.compact_eval({"ok": True, "tabId": 1, "value": "x" * 9000})
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["value"]), m.EVAL_VALUE_MAX + 1)
        self.assertTrue(got["value"].endswith("…"))

    def test_compact_read_page_truncates_and_metas(self) -> None:
        got = m.compact_read_page(
            {
                "ok": True,
                "tabId": 7,
                "title": "T" * 300,
                "url": "u" * 500,
                "text": "x" * 9000,
                "textLength": 20000,
                "status": "complete",
                "timedOut": False,
            }
        )
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["title"]), m.TAB_TITLE_MAX)
        self.assertEqual(len(got["url"]), m.TAB_URL_MAX)
        self.assertEqual(len(got["text"]), m.EVAL_VALUE_MAX + 1)
        self.assertEqual(got["text_chars"], 20000)
        self.assertEqual(got["status"], "complete")
        self.assertNotIn("timedOut", got)

    def test_compact_read_page_timeout_flag(self) -> None:
        got = m.compact_read_page(
            {"ok": True, "tabId": 1, "status": "loading", "timedOut": True}
        )
        self.assertTrue(got["timedOut"])
        self.assertEqual(got["text"], "")

    def test_env_wait_ext_s_override(self) -> None:
        saved = os.environ.pop("HERMES_CHROME_WAIT_EXT_S", None)
        try:
            self.assertEqual(m.env_wait_ext_s(), m.DEFAULT_WAIT_EXT_S)
            os.environ["HERMES_CHROME_WAIT_EXT_S"] = "5"
            self.assertEqual(m.env_wait_ext_s(), 5.0)
            os.environ["HERMES_CHROME_WAIT_EXT_S"] = "-1"
            self.assertEqual(m.env_wait_ext_s(), m.DEFAULT_WAIT_EXT_S)
            os.environ["HERMES_CHROME_WAIT_EXT_S"] = "bogus"
            self.assertEqual(m.env_wait_ext_s(), m.DEFAULT_WAIT_EXT_S)
            os.environ["HERMES_CHROME_WAIT_EXT_S"] = "0"
            self.assertEqual(m.env_wait_ext_s(), 0.0)
        finally:
            if saved is None:
                os.environ.pop("HERMES_CHROME_WAIT_EXT_S", None)
            else:
                os.environ["HERMES_CHROME_WAIT_EXT_S"] = saved

    def test_cap_json_and_tool_result_minified(self) -> None:
        huge = {"ok": True, "blob": "n" * (m.AGENT_JSON_MAX + 100)}
        capped = m.cap_json(huge)
        self.assertTrue(capped.get("truncated"))
        res = m._tool_result({"ok": True, "tabId": 1})
        text = res["content"][0]["text"]
        self.assertNotIn("\n", text)
        self.assertEqual(json.loads(text), {"ok": True, "tabId": 1})

    def test_stdio_framing_supports_ndjson_and_content_length(self) -> None:
        import io
        import sys as _sys

        saved_in, saved_out, saved_mode = _sys.stdin, _sys.stdout, m._framing_mode
        try:
            nd_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
            fake_in = type("_FakeStream", (), {"buffer": io.BytesIO(nd_req.encode("utf-8"))})()
            out_buf = io.BytesIO()
            fake_out = type("_FakeStream", (), {"buffer": out_buf})()
            _sys.stdin, _sys.stdout = fake_in, fake_out
            got = m._read_message()
            self.assertEqual(got["method"], "ping")
            self.assertEqual(m._framing_mode, "ndjson")
            m._write_message({"jsonrpc": "2.0", "id": 1, "result": {}})
            self.assertTrue(out_buf.getvalue().endswith(b"\n"))
            self.assertFalse(out_buf.getvalue().startswith(b"Content-Length:"))

            raw = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode("utf-8")
            cl_req = f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw
            fake_in2 = type("_FakeStream", (), {"buffer": io.BytesIO(cl_req)})()
            out_buf2 = io.BytesIO()
            fake_out2 = type("_FakeStream", (), {"buffer": out_buf2})()
            _sys.stdin, _sys.stdout = fake_in2, fake_out2
            got2 = m._read_message()
            self.assertEqual(got2["id"], 2)
            self.assertEqual(m._framing_mode, "content-length")
            m._write_message({"jsonrpc": "2.0", "id": 2, "result": {}})
            self.assertTrue(out_buf2.getvalue().startswith(b"Content-Length:"))
        finally:
            _sys.stdin, _sys.stdout, m._framing_mode = saved_in, saved_out, saved_mode

    def test_csp_safe_tools_dispatch_and_validation(self) -> None:
        import tempfile
        from unittest.mock import patch

        # Click requires selector, text, or text_includes.
        bad_click = m.call_tool("hermes_chrome_click", {"tab_id": 1})
        self.assertTrue(bad_click["isError"])

        bad_set = m.call_tool("hermes_chrome_editor_set", {"tab_id": 1})
        self.assertTrue(bad_set["isError"])

        calls: list[tuple[str, dict]] = []

        def fake_send(action, extra=None, *, wait_s=30.0):
            calls.append((action, dict(extra or {})))
            return {"ok": True, "action": action}

        with patch.object(m, "send_command", side_effect=fake_send):
            r_click = m.call_tool(
                "hermes_chrome_click",
                {"tab_id": 7, "text_includes": "Update on chart", "mouse_events": True},
            )
            self.assertFalse(r_click["isError"])
            r_query = m.call_tool(
                "hermes_chrome_dom_query",
                {"tab_id": 7, "text_includes": "Pine Editor", "limit": 999},
            )
            self.assertFalse(r_query["isError"])
            r_get = m.call_tool(
                "hermes_chrome_editor_get",
                {"tab_id": 7, "max_chars": 999999, "list_models": True},
            )
            self.assertFalse(r_get["isError"])

            with tempfile.NamedTemporaryFile(
                "w", suffix=".pine", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write("//@version=6\nindicator('ok')\n")
                tmp_path = tmp.name
            try:
                r_set = m.call_tool(
                    "hermes_chrome_editor_set",
                    {"tab_id": 7, "file_path": tmp_path},
                )
                self.assertFalse(r_set["isError"])
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        self.assertEqual(calls[0][0], "click")
        self.assertEqual(calls[0][1]["textIncludes"], "Update on chart")
        self.assertTrue(calls[0][1]["mouseEvents"])
        self.assertEqual(calls[1][0], "dom_query")
        self.assertEqual(calls[1][1]["limit"], 100)
        self.assertEqual(calls[2][0], "editor_get")
        self.assertEqual(calls[2][1]["maxChars"], 8000)
        self.assertTrue(calls[2][1]["listModels"])
        self.assertEqual(calls[3][0], "editor_set")
        self.assertIn("indicator('ok')", calls[3][1]["text"])

    def test_mcp_tool_names_include_csp_safe_actions(self) -> None:
        names = {t["name"] for t in m.TOOLS}
        self.assertTrue(
            {
                "hermes_chrome_start",
                "hermes_chrome_open",
                "hermes_chrome_dom_query",
                "hermes_chrome_editor_get",
                "hermes_chrome_editor_set",
                "hermes_chrome_click",
            }.issubset(names)
        )

    def test_separate_window_dispatch_and_compact_nav(self) -> None:
        from unittest.mock import patch

        calls: list[tuple[str, dict]] = []

        def fake_send(action, extra=None, *, wait_s=30.0):
            calls.append((action, dict(extra or {})))
            return {
                "tabId": 42,
                "windowId": 9001,
                "separateWindow": True,
                "url": extra.get("url") or "chrome://newtab/",
                "mode": "new_window",
            }

        with patch.object(m, "send_command", side_effect=fake_send):
            r_start = m.call_tool(
                "hermes_chrome_start",
                {"separate_window": True, "width": 1000, "height": 700},
            )
            r_open = m.call_tool(
                "hermes_chrome_open",
                {
                    "url": "https://example.com",
                    "separate_window": True,
                    "new_tab": True,
                },
            )
            r_read = m.call_tool(
                "hermes_chrome_read_page",
                {"url": "https://example.com", "separate_window": True},
            )

        self.assertFalse(r_start["isError"])
        self.assertFalse(r_open["isError"])
        self.assertFalse(r_read["isError"])
        self.assertEqual(calls[0], ("start", {"separateWindow": True, "width": 1000, "height": 700}))
        self.assertEqual(
            calls[1],
            ("open", {"url": "https://example.com", "newTab": True, "separateWindow": True}),
        )
        self.assertTrue(calls[2][1]["separateWindow"])
        open_payload = json.loads(r_open["content"][0]["text"])
        self.assertEqual(open_payload["windowId"], 9001)
        self.assertTrue(open_payload["separateWindow"])


class MCPHardeningTests(unittest.TestCase):
    def test_compact_read_page_respects_text_max(self) -> None:
        got = m.compact_read_page(
            {"ok": True, "title": "Example", "url": "https://example.com", "text": "A" * 2000},
            text_max=500,
        )
        self.assertLessEqual(len(got["text"]), 501)
        self.assertGreaterEqual(len(got["text"]), 500)

    def test_handle_jsonrpc_array_returns_invalid_request(self) -> None:
        raw_buf = io.BytesIO()
        fake_stdout = type("_FakeStdout", (), {"buffer": raw_buf})()
        with mock.patch("sys.stdout", fake_stdout):
            m._handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        payload = raw_buf.getvalue().split(b"\r\n\r\n", 1)[-1].decode("utf-8")
        resp = json.loads(payload)
        self.assertEqual(resp["error"]["code"], -32600)

    def test_editor_set_file_path_blocks_sensitive_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".env").write_text("SECRET=1", encoding="utf-8")
            ssh_dir = home / ".ssh"
            ssh_dir.mkdir()
            (ssh_dir / "id_rsa").write_text("KEY", encoding="utf-8")
            run_dir = home / ".hermes" / "run" / "hermes-chrome"
            run_dir.mkdir(parents=True)
            (run_dir / "bridge.env").write_text("export HERMES_CHROME_BRIDGE_TOKEN='x'", encoding="utf-8")

            with mock.patch("pathlib.Path.home", return_value=home):
                for bad in (home / ".env", ssh_dir / "id_rsa", run_dir / "bridge.env"):
                    res = m.call_tool("hermes_chrome_editor_set", {"tab_id": 1, "file_path": str(bad)})
                    self.assertTrue(res["isError"], f"expected block for {bad}")

    def test_capture_blocks_invalid_prefer_and_unsafe_out(self) -> None:
        r_bad_pref = m.call_tool("hermes_chrome_capture", {"prefer": "evil"})
        self.assertTrue(r_bad_pref["isError"])
        bad_out = str(Path.home() / ".ssh" / "shot.png")
        r_bad_out = m.call_tool("hermes_chrome_capture", {"prefer": "auto", "out": bad_out})
        self.assertTrue(r_bad_out["isError"])


if __name__ == "__main__":
    unittest.main()

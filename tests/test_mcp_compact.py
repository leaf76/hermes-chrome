#!/usr/bin/env python3
"""Unit tests for agent-facing MCP payload compression (no live Chrome)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

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
        self.assertNotIn("root", got)

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

    def test_cap_json_and_tool_result_minified(self) -> None:
        huge = {"ok": True, "blob": "n" * (m.AGENT_JSON_MAX + 100)}
        capped = m.cap_json(huge)
        self.assertTrue(capped.get("truncated"))
        res = m._tool_result({"ok": True, "tabId": 1})
        text = res["content"][0]["text"]
        self.assertNotIn("\n", text)
        self.assertEqual(json.loads(text), {"ok": True, "tabId": 1})


if __name__ == "__main__":
    unittest.main()

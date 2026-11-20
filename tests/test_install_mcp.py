#!/usr/bin/env python3
"""Regression tests for scripts/install-mcp.py."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_install_mcp():
    spec = importlib.util.spec_from_file_location(
        "install_mcp",
        ROOT / "scripts" / "install-mcp.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class InstallMcpTests(unittest.TestCase):
    def test_windows_backslash_toml_parses_and_uninstall_clean(self) -> None:
        im = _load_install_mcp()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            grok_dir = home / ".grok"
            grok_dir.mkdir(parents=True)
            conf = grok_dir / "config.toml"
            fake_root = home / "repo"
            fake_root.mkdir()
            (fake_root / "mcp_server.py").write_text("# stub\n", encoding="utf-8")
            win_py = r"C:\Users\demo\.hermes\tools\python.exe"

            with mock.patch("pathlib.Path.home", return_value=home):
                with mock.patch.dict(os.environ, {"GROK_CONFIG": str(conf)}, clear=False):
                    msg = im.install_grok(fake_root, win_py)
                    self.assertTrue(msg.startswith("ok:"))
                    parsed = tomllib.loads(conf.read_text(encoding="utf-8"))
                    srv = parsed["mcp_servers"]["hermes-chrome"]
                    self.assertEqual(srv["command"], win_py)

                    # Fresh file starts with MARKER_START (no leading newline) — uninstall must still strip cleanly
                    un_msg = im.install_grok(fake_root, win_py, uninstall=True)
                    self.assertIn("cleaned", un_msg)
                    self.assertEqual(conf.read_text(encoding="utf-8"), "")

    def test_vscode_uses_servers_and_stdio_type(self) -> None:
        im = _load_install_mcp()
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            vsc = td_path / "Code" / "User" / "mcp.json"
            vsc.parent.mkdir(parents=True)
            msg = im.install_json_mcp(
                vsc,
                ROOT,
                "python3",
                key="servers",
                include_type=True,
                label="VS Code",
            )
            self.assertTrue(msg.startswith("ok:"))
            data = json.loads(vsc.read_text(encoding="utf-8"))
            entry = data["servers"]["hermes-chrome"]
            self.assertEqual(entry["type"], "stdio")
            self.assertEqual(entry["command"], "python3")


if __name__ == "__main__":
    unittest.main()

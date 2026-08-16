#!/usr/bin/env python3
"""Static checks for security-sensitive product defaults (no live Chrome)."""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

from policy import check_host_policy  # noqa: E402

# Assignments that would ship auth-off / query-token / non-local bind as default.
_FOOTGUN_ASSIGN = re.compile(
    r"^\s*(?:export\s+)?"
    r"HERMES_CHROME_(?:BRIDGE_ALLOW_NO_AUTH|ALLOW_QUERY_TOKEN|BRIDGE_ALLOW_NONLOCAL)"
    r"\s*=\s*1\b",
    re.MULTILINE,
)

_SKIP_DIR_NAMES = {
    ".git",
    "dist",
    "node_modules",
    "__pycache__",
    "store",
}


class PolicyDefaultTests(unittest.TestCase):
    def test_blocks_private_and_metadata_hosts(self) -> None:
        for url in (
            "http://127.0.0.1/secret",
            "http://localhost/x",
            "http://169.254.169.254/latest/meta-data",
            "http://metadata.google.internal/",
        ):
            got = check_host_policy(url)
            self.assertFalse(got["ok"], msg=url)

    def test_allows_public_https_host(self) -> None:
        got = check_host_policy("https://example.com/path")
        self.assertTrue(got["ok"], msg=got)


class ManifestTests(unittest.TestCase):
    def test_bridge_host_permissions_are_loopback_only(self) -> None:
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        hosts = manifest.get("host_permissions") or []
        self.assertIn("http://127.0.0.1:19876/*", hosts)
        self.assertIn("http://localhost:19876/*", hosts)
        extra = [
            h
            for h in hosts
            if h not in ("http://127.0.0.1:19876/*", "http://localhost:19876/*", "<all_urls>")
        ]
        self.assertEqual(extra, [])

    def test_native_messaging_permission(self) -> None:
        manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("nativeMessaging", manifest.get("permissions") or [])


class RepoHygieneTests(unittest.TestCase):
    def test_gitignore_covers_bridge_env(self) -> None:
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("bridge.env", gi)

    def test_no_committed_env_or_runtime_pid(self) -> None:
        forbidden_names = {"bridge.env", "bridge.pid", "bridge.log"}
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            for name in filenames:
                if name in forbidden_names:
                    found.append(str(Path(dirpath) / name))
        self.assertEqual(found, [])

    def test_installers_do_not_default_security_footguns(self) -> None:
        hits: list[str] = []
        for rel in (
            "scripts/install.sh",
            "scripts/install-for-agent.sh",
            "scripts/install-windows.ps1",
            "scripts/install-launchd.sh",
            "scripts/setup-local.sh",
            "scripts/hermes-chrome.sh",
        ):
            path = ROOT / rel
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith("REM "):
                    continue
                if _FOOTGUN_ASSIGN.search(line):
                    hits.append(f"{rel}:{i}:{line.strip()}")
        self.assertEqual(hits, [])

    def test_bridge_source_defaults_loopback_and_auth(self) -> None:
        text = (ROOT / "bridge.py").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1"', text)
        self.assertIn("HERMES_CHROME_BRIDGE_ALLOW_NO_AUTH", text)
        self.assertNotIn('ALLOW_NO_AUTH", "1"', text)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from self_update import official_origin  # noqa: E402


class OfficialOriginTests(unittest.TestCase):
    def test_allows_https_and_ssh(self) -> None:
        self.assertTrue(official_origin("https://github.com/leaf76/hermes-chrome"))
        self.assertTrue(official_origin("https://github.com/leaf76/hermes-chrome.git"))
        self.assertTrue(official_origin("git@github.com:leaf76/hermes-chrome.git"))

    def test_rejects_other_remotes(self) -> None:
        self.assertFalse(official_origin("https://github.com/evil/hermes-chrome"))
        self.assertFalse(official_origin("https://example.com/leaf76/hermes-chrome.git"))
        self.assertFalse(official_origin(""))


if __name__ == "__main__":
    unittest.main()

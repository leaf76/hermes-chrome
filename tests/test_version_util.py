#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from version_util import cmp_semver, mismatch, product_version, update_report  # noqa: E402


class VersionUtilTests(unittest.TestCase):
    def test_product_version_from_manifest(self) -> None:
        ver = product_version(ROOT)
        self.assertRegex(ver, r"^\d+\.\d+\.\d+")

    def test_cmp_and_mismatch(self) -> None:
        self.assertEqual(cmp_semver("1.8.3", "v1.8.2"), 1)
        self.assertEqual(mismatch("1.8.2", "1.8.3"), "companion")
        self.assertEqual(mismatch("1.8.3", "1.8.2"), "extension")
        self.assertIsNone(mismatch("1.8.3", "1.8.3"))
        self.assertIsNone(mismatch(None, "1.8.3"))

    def test_update_report_behind_github(self) -> None:
        r = update_report(local="1.8.2", latest_tag="v1.8.3", extension="1.8.2")
        self.assertTrue(r["behind_github"])
        self.assertTrue(any("install.sh" in h for h in r["hints"]))


if __name__ == "__main__":
    unittest.main()

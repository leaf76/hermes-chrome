#!/usr/bin/env python3
"""Release/version consistency gates (static)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

from version_util import product_version  # noqa: E402


class VersionConsistencyTests(unittest.TestCase):
    def test_manifest_matches_npm(self) -> None:
        manifest = json.loads(
            (ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
        )
        npm = json.loads((ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], npm["version"])

    def test_product_version_reads_manifest(self) -> None:
        manifest = json.loads(
            (ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(product_version(ROOT), manifest["version"])


if __name__ == "__main__":
    unittest.main()

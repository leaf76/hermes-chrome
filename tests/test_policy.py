#!/usr/bin/env python3
"""Policy loader and host checks."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

from policy import check_host_policy, load_policy  # noqa: E402


class PolicyInvalidTests(unittest.TestCase):
    def test_invalid_policy_file_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "policy.json"
            bad.write_text("{not-json", encoding="utf-8")
            prev = os.environ.get("HERMES_CHROME_POLICY")
            os.environ["HERMES_CHROME_POLICY"] = str(bad)
            try:
                pol = load_policy()
                self.assertTrue(pol.get("_invalid"))
                got = check_host_policy("https://example.com/")
                self.assertFalse(got["ok"])
                codes = {f["code"] for f in got.get("findings") or []}
                self.assertIn("policy_invalid", codes)
            finally:
                if prev is None:
                    os.environ.pop("HERMES_CHROME_POLICY", None)
                else:
                    os.environ["HERMES_CHROME_POLICY"] = prev


if __name__ == "__main__":
    unittest.main()

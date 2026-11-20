#!/usr/bin/env python3
"""Policy loader and host checks."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

from check_url import check_url  # noqa: E402
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


class PolicyHardeningTests(unittest.TestCase):
    def _with_policy(self, data: dict) -> tempfile.TemporaryDirectory:
        td = tempfile.TemporaryDirectory()
        pol_path = Path(td.name) / "policy.json"
        pol_path.write_text(json.dumps(data), encoding="utf-8")
        self._prev_pol = os.environ.get("HERMES_CHROME_POLICY")
        os.environ["HERMES_CHROME_POLICY"] = str(pol_path)
        return td

    def _restore_policy(self, td: tempfile.TemporaryDirectory) -> None:
        if self._prev_pol is None:
            os.environ.pop("HERMES_CHROME_POLICY", None)
        else:
            os.environ["HERMES_CHROME_POLICY"] = self._prev_pol
        td.cleanup()

    def test_blocks_trailing_dot_and_nonstandard_ips(self) -> None:
        for url in (
            "https://localhost./",
            "http://127.1/",
            "http://2130706433/",
            "http://0x7f000001/",
            "http://100.64.0.1/",
            "http://[::ffff:127.0.0.1]/",
        ):
            got = check_host_policy(url)
            self.assertFalse(got["ok"], f"expected block for {url}")

    def test_require_https_blocks_non_https_schemes(self) -> None:
        td = self._with_policy({"require_https": True, "block_private": False})
        try:
            for url in ("file:///etc/passwd", "gopher://example.com/", "http://example.com/"):
                got = check_host_policy(url)
                self.assertFalse(got["ok"], f"expected require_https block for {url}")
                codes = {f["code"] for f in got["findings"]}
                self.assertIn("policy_require_https", codes)
        finally:
            self._restore_policy(td)

    def test_empty_host_blocked_when_allow_hosts_set(self) -> None:
        td = self._with_policy({"allow_hosts": ["example.com"], "block_private": False})
        try:
            got = check_host_policy("file:///etc/hosts")
            self.assertFalse(got["ok"])
            codes = {f["code"] for f in got["findings"]}
            self.assertIn("policy_not_allowlisted", codes)
        finally:
            self._restore_policy(td)

    def test_check_url_invalid_port_handled_cleanly(self) -> None:
        for bad_url in ("https://example.com:99999/", "https://example.com:abc/"):
            res = check_url(bad_url, follow=False)
            self.assertFalse(res["ok"])
            codes = {f["code"] for f in res.get("findings") or []}
            self.assertIn("invalid_port", codes)


if __name__ == "__main__":
    unittest.main()

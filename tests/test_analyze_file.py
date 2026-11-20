#!/usr/bin/env python3
"""Regression tests for lib/analyze_file.py."""

from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

from analyze_file import analyze_file  # noqa: E402


class AnalyzeFileTests(unittest.TestCase):
    def test_tar_gz_traversal_symlink_and_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tgz = Path(td) / "sample.tar.gz"
            with tarfile.open(tgz, "w:gz") as tf:
                ti = tarfile.TarInfo("../evil.txt")
                payload = b"hello"
                ti.size = len(payload)
                tf.addfile(ti, io.BytesIO(payload))

                sym = tarfile.TarInfo("link.txt")
                sym.type = tarfile.SYMTYPE
                sym.linkname = "/etc/passwd"
                tf.addfile(sym)

            res = analyze_file(tgz)
            self.assertFalse(res["ok"])
            codes = {f["code"] for f in res["findings"]}
            self.assertIn("tar_path_traversal", codes)
            self.assertIn("tar_link", codes)
            # Ensure tar member types / bytes never break JSON serialization
            dumped = json.dumps(res)
            self.assertIn("tar_link", dumped)

    def test_zip_symlink_detected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / "sym.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zi = zipfile.ZipInfo("bad_link")
                zi.external_attr = 0o120777 << 16
                zf.writestr(zi, "../secret")

            res = analyze_file(zp)
            self.assertFalse(res["ok"])
            codes = {f["code"] for f in res["findings"]}
            self.assertIn("zip_symlink", codes)

    def test_macho_binary_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bin_path = Path(td) / "payload.bin"
            bin_path.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 64)
            res = analyze_file(bin_path)
            self.assertFalse(res["ok"])
            codes = {f["code"] for f in res["findings"]}
            self.assertIn("executable_magic", codes)


if __name__ == "__main__":
    unittest.main()

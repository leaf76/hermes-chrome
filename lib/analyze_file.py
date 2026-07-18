"""Local file analysis: images, zip/tar safety, magic bytes, hashes.

No third-party upload. Heuristic zip-bomb / path traversal checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

# Defaults (override via env)
MAX_ZIP_MEMBERS = int(os.environ.get("HERMES_CHROME_ANALYZE_MAX_MEMBERS", "5000"))
MAX_UNCOMPRESSED = int(
    os.environ.get("HERMES_CHROME_ANALYZE_MAX_UNCOMPRESSED", str(512 * 1024 * 1024))
)  # 512 MiB total declared
MAX_RATIO = float(os.environ.get("HERMES_CHROME_ANALYZE_MAX_RATIO", "100"))
MAX_NEST_HINT = 8


def _finding(level: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"level": level, "code": code, "message": message}
    out.update(extra)
    return out


def _risk(findings: list[dict[str, Any]]) -> str:
    levels = {f["level"] for f in findings}
    if "block" in levels:
        return "high"
    if "warn" in levels:
        return "medium"
    return "low"


def sha256_file(path: Path, *, limit: int | None = None) -> str:
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
            if limit is not None and n >= limit:
                break
    return h.hexdigest()


def detect_magic(head: bytes) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"MZ"):
        return "pe_exe"
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"#!"):
        return "script"
    return "unknown"


def ext_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def analyze_image(path: Path, head: bytes) -> dict[str, Any]:
    kind = detect_magic(head)
    info: dict[str, Any] = {"kind": kind}
    findings: list[dict[str, Any]] = []
    try:
        if kind == "png" and len(head) >= 24:
            w, h = struct.unpack(">II", head[16:24])
            info["width"], info["height"] = w, h
        elif kind == "gif" and len(head) >= 10:
            w, h = struct.unpack("<HH", head[6:10])
            info["width"], info["height"] = w, h
        elif kind == "jpeg":
            # Scan SOF markers
            data = path.read_bytes()
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }:
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    info["width"], info["height"] = w, h
                    break
                if marker == 0xD9:
                    break
                if marker in (0xD8, 0x01) or (0xD0 <= marker <= 0xD9):
                    i += 2
                    continue
                if i + 4 > len(data):
                    break
                seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
                i += 2 + seg_len
        elif kind == "webp" and len(head) >= 30:
            # VP8x or simple VP8
            if head[12:16] == b"VP8X" and len(head) >= 30:
                # canvas size 24-bit little-endian minus 1
                w = 1 + int.from_bytes(head[24:27], "little")
                h = 1 + int.from_bytes(head[27:30], "little")
                info["width"], info["height"] = w, h
    except Exception as e:  # noqa: BLE001
        findings.append(
            _finding("warn", "image_parse", f"Could not parse image geometry: {e}")
        )

    ext = ext_of(path)
    magic = kind
    if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
        mapped = {"jpg": "jpeg", "jpeg": "jpeg"}.get(ext, ext)
        if magic not in ("unknown",) and magic != mapped and not (
            mapped == "jpeg" and magic == "jpeg"
        ):
            if not (ext in {"jpg", "jpeg"} and magic == "jpeg"):
                findings.append(
                    _finding(
                        "warn",
                        "ext_mismatch",
                        f"Extension .{ext} does not match magic '{magic}'",
                    )
                )
    info["findings"] = findings
    return info


def _zip_name_unsafe(name: str) -> str | None:
    if not name:
        return "empty_name"
    # Null byte
    if "\x00" in name:
        return "null_byte"
    # Absolute / drive
    if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        return "absolute_path"
    # Traversal
    parts = name.replace("\\", "/").split("/")
    if ".." in parts:
        return "path_traversal"
    return None


def analyze_zip(path: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    total_comp = 0
    total_uncomp = 0
    try:
        with zipfile.ZipFile(path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_MEMBERS:
                findings.append(
                    _finding(
                        "block",
                        "too_many_members",
                        f"{len(infos)} members exceeds limit {MAX_ZIP_MEMBERS}",
                    )
                )
            for info in infos[:MAX_ZIP_MEMBERS]:
                name = info.filename
                unsafe = _zip_name_unsafe(name)
                entry = {
                    "name": name,
                    "compressed_size": info.compress_size,
                    "file_size": info.file_size,
                    "is_dir": name.endswith("/"),
                }
                if unsafe:
                    entry["unsafe"] = unsafe
                    findings.append(
                        _finding(
                            "block",
                            f"zip_{unsafe}",
                            f"Unsafe zip member path: {name} ({unsafe})",
                        )
                    )
                total_comp += max(0, info.compress_size or 0)
                total_uncomp += max(0, info.file_size or 0)
                members.append(entry)
    except zipfile.BadZipFile as e:
        findings.append(_finding("block", "bad_zip", f"Invalid zip: {e}"))
        return {
            "kind": "zip",
            "members": members,
            "member_count": len(members),
            "total_compressed": total_comp,
            "total_uncompressed": total_uncomp,
            "findings": findings,
        }

    ratio = (total_uncomp / total_comp) if total_comp > 0 else 0.0
    if total_uncomp > MAX_UNCOMPRESSED:
        findings.append(
            _finding(
                "block",
                "uncompressed_too_large",
                f"Declared uncompressed size {total_uncomp} exceeds {MAX_UNCOMPRESSED}",
            )
        )
    if total_comp > 0 and ratio > MAX_RATIO:
        findings.append(
            _finding(
                "block",
                "compression_ratio",
                f"Compression ratio {ratio:.1f}x exceeds {MAX_RATIO}x (zip-bomb heuristic)",
                ratio=round(ratio, 2),
            )
        )
    elif total_comp > 0 and ratio > MAX_RATIO / 2:
        findings.append(
            _finding(
                "warn",
                "high_compression_ratio",
                f"High compression ratio {ratio:.1f}x",
                ratio=round(ratio, 2),
            )
        )

    # Nested archive hint
    nested = [
        m["name"]
        for m in members
        if m["name"].lower().endswith((".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"))
    ]
    if nested:
        findings.append(
            _finding(
                "warn",
                "nested_archive",
                f"Contains nested archives: {len(nested)}",
                samples=nested[:10],
            )
        )

    return {
        "kind": "zip",
        "members": members[:200],  # cap listing in JSON
        "member_count": len(members),
        "members_truncated": len(members) > 200,
        "total_compressed": total_comp,
        "total_uncompressed": total_uncomp,
        "compression_ratio": round(ratio, 3) if total_comp else None,
        "findings": findings,
    }


def analyze_tar(path: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    total = 0
    try:
        with tarfile.open(path, "r:*") as tf:
            for i, m in enumerate(tf.getmembers()):
                if i >= MAX_ZIP_MEMBERS:
                    findings.append(
                        _finding(
                            "block",
                            "too_many_members",
                            f"More than {MAX_ZIP_MEMBERS} tar members",
                        )
                    )
                    break
                name = m.name
                unsafe = _zip_name_unsafe(name)
                entry = {
                    "name": name,
                    "size": m.size,
                    "type": m.type,
                    "is_dir": m.isdir(),
                    "is_symlink": m.issym() or m.islnk(),
                }
                if m.issym() or m.islnk():
                    findings.append(
                        _finding(
                            "block",
                            "tar_link",
                            f"Tar contains link (blocked): {name} -> {m.linkname}",
                        )
                    )
                if unsafe:
                    findings.append(
                        _finding(
                            "block",
                            f"tar_{unsafe}",
                            f"Unsafe tar member path: {name} ({unsafe})",
                        )
                    )
                total += max(0, m.size or 0)
                members.append(entry)
    except tarfile.TarError as e:
        findings.append(_finding("block", "bad_tar", f"Invalid tar: {e}"))

    if total > MAX_UNCOMPRESSED:
        findings.append(
            _finding(
                "block",
                "uncompressed_too_large",
                f"Tar total size {total} exceeds {MAX_UNCOMPRESSED}",
            )
        )

    return {
        "kind": "tar",
        "members": members[:200],
        "member_count": len(members),
        "members_truncated": len(members) > 200,
        "total_size": total,
        "findings": findings,
    }


def analyze_file(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    findings: list[dict[str, Any]] = []
    if not p.exists():
        return {
            "ok": False,
            "risk": "high",
            "path": str(p),
            "findings": [_finding("block", "missing", f"File not found: {p}")],
        }
    if not p.is_file():
        return {
            "ok": False,
            "risk": "high",
            "path": str(p),
            "findings": [_finding("block", "not_file", f"Not a regular file: {p}")],
        }

    size = p.stat().st_size
    head = p.read_bytes()[:4096]
    magic = detect_magic(head)
    digest = sha256_file(p)
    ext = ext_of(p)

    # Double extension on filename
    name = p.name
    if re.search(
        r"\.(pdf|doc|docx|png|jpg|jpeg|zip|txt)\.(exe|scr|bat|cmd|js|vbs|ps1|msi)$",
        name,
        re.I,
    ):
        findings.append(
            _finding(
                "block",
                "double_extension",
                f"Suspicious double extension: {name}",
            )
        )

    if magic in {"pe_exe", "elf"}:
        findings.append(
            _finding("block", "executable_magic", f"File magic is executable ({magic})")
        )
    if ext in {"exe", "scr", "bat", "cmd", "ps1", "msi", "dll", "js", "vbs"}:
        findings.append(
            _finding("warn", "executable_ext", f"Executable-like extension .{ext}")
        )

    # Extension vs magic
    if ext == "png" and magic not in {"png", "unknown"}:
        findings.append(
            _finding("warn", "ext_mismatch", f".png but magic is {magic}")
        )
    if ext in {"jpg", "jpeg"} and magic not in {"jpeg", "unknown"}:
        findings.append(
            _finding("warn", "ext_mismatch", f".{ext} but magic is {magic}")
        )
    if ext == "zip" and magic not in {"zip", "unknown"}:
        findings.append(
            _finding("warn", "ext_mismatch", f".zip but magic is {magic}")
        )
    if ext == "pdf" and magic not in {"pdf", "unknown"}:
        findings.append(
            _finding("warn", "ext_mismatch", f".pdf but magic is {magic}")
        )

    detail: dict[str, Any] = {}
    if magic in {"png", "jpeg", "gif", "webp"} or ext in {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
    }:
        detail = analyze_image(p, head)
        findings.extend(detail.pop("findings", []))
    elif magic == "zip" or ext == "zip":
        detail = analyze_zip(p)
        findings.extend(detail.pop("findings", []))
    elif magic == "tar" or ext in {"tar", "tgz", "tar.gz"}:
        # .tar.gz may be gzip magic first
        if magic == "gzip" or name.endswith((".tgz", ".tar.gz")):
            try:
                detail = analyze_tar(p)
                findings.extend(detail.pop("findings", []))
            except Exception as e:  # noqa: BLE001
                findings.append(
                    _finding("warn", "tar_open", f"Could not open as tar: {e}")
                )
        else:
            detail = analyze_tar(p)
            findings.extend(detail.pop("findings", []))
    elif magic == "gzip":
        findings.append(
            _finding(
                "info",
                "gzip",
                "gzip container — analyze extracted payload separately if needed",
            )
        )

    risk = _risk(findings)
    ok = not any(f["level"] == "block" for f in findings)
    return {
        "ok": ok,
        "risk": risk,
        "path": str(p),
        "name": p.name,
        "size": size,
        "sha256": digest,
        "magic": magic,
        "extension": ext,
        "detail": detail,
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: analyze_file.py <path> [path...]", file=sys.stderr)
        return 2
    results = []
    worst = 0
    for path in argv:
        r = analyze_file(path)
        results.append(r)
        if not r.get("ok"):
            worst = max(worst, 2)
        elif r.get("risk") == "medium":
            worst = max(worst, 1)
    if len(results) == 1:
        print(json.dumps(results[0], indent=2, ensure_ascii=False))
    else:
        print(json.dumps({"files": results}, indent=2, ensure_ascii=False))
    return worst


if __name__ == "__main__":
    raise SystemExit(main())

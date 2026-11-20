"""Shared network helpers for hermes-chrome local ops (macOS-friendly TLS)."""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
from typing import Any


def ssl_context() -> ssl.SSLContext:
    """Best-effort system CA bundle (certifi when present)."""
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def curl_available() -> bool:
    return bool(shutil.which("curl"))


def curl_head_single(url: str, *, timeout_s: float = 8) -> dict[str, Any]:
    """Use curl -I without following redirects so caller can validate each hop."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    max_t = str(max(1, int(timeout_s)))
    conn_t = str(max(1, int(min(timeout_s, 30))))
    proc = subprocess.run(
        [
            curl,
            "-sS",
            "-g",
            "--proto",
            "=http,https",
            "--proto-redir",
            "=http,https",
            "-I",
            "-o",
            os.devnull,
            "-w",
            "%{http_code}\n%{redirect_url}\n%{content_type}\n",
            "--connect-timeout",
            conn_t,
            "--max-time",
            max_t,
            "--",
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5.0, timeout_s + 5),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"curl exit {proc.returncode}").strip()
        raise RuntimeError(err)
    raw_lines = (proc.stdout or "").splitlines()
    if len(raw_lines) < 3:
        raise RuntimeError(f"unexpected curl -I output: {proc.stdout!r}")
    status_s, redirect_url, ctype = raw_lines[-3:]
    try:
        status = int(status_s.strip())
    except ValueError as e:
        raise RuntimeError(f"unexpected curl -I status: {proc.stdout!r}") from e
    return {
        "status": status,
        "location": redirect_url.strip() or None,
        "content_type": ctype.strip() or None,
        "mode": "curl_head_single",
    }


def curl_head_chain(url: str, *, max_redirs: int = 8, timeout_s: float = 15) -> dict[str, Any]:
    """Use curl -I -L to resolve redirects with system certs."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    max_t = str(max(1, int(timeout_s)))
    conn_t = str(max(1, int(min(timeout_s, 30))))
    # -w writes JSON meta after headers on stdout mixed — use -D headers -o os.devnull -w
    proc = subprocess.run(
        [
            curl,
            "-sS",
            "-g",
            "--proto",
            "=http,https",
            "--proto-redir",
            "=http,https",
            "-L",
            "--max-redirs",
            str(max_redirs),
            "-I",  # HEAD
            "-o",
            os.devnull,
            "-w",
            "%{http_code}\n%{url_effective}\n%{num_redirects}\n",
            "--connect-timeout",
            conn_t,
            "--max-time",
            max_t,
            "--",
            url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5.0, timeout_s + 5),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"curl exit {proc.returncode}").strip()
        raise RuntimeError(err)
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    # last 3 lines from -w
    if len(lines) < 3:
        # sometimes only write-out lines
        parts = (proc.stdout or "").strip().splitlines()
        lines = [p.strip() for p in parts if p.strip()]
    try:
        status = int(lines[-3])
        final = lines[-2]
        redirs = int(lines[-1])
    except (ValueError, IndexError) as e:
        raise RuntimeError(f"unexpected curl -I output: {proc.stdout!r}") from e
    return {
        "status": status,
        "final_url": final,
        "redirects": redirs,
        "ok_http": 200 <= status < 400,
        "mode": "curl_head",
    }


def curl_download(
    url: str,
    dest: str,
    *,
    max_bytes: int,
    timeout_s: float = 120,
    follow_redirects: bool = True,
) -> dict[str, Any]:
    """Download with curl to dest; enforce max size via -m and post-check size."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    max_t = str(max(1, int(timeout_s)))
    cmd = [
        curl,
        "-sS",
        "-g",
        "--proto",
        "=http,https",
        "--proto-redir",
        "=http,https",
        "--fail",
    ]
    if follow_redirects:
        cmd.extend(["-L", "--max-redirs", "8"])
    cmd.extend(
        [
            "-o",
            dest,
            "-w",
            "%{http_code}\n%{url_effective}\n%{size_download}\n%{content_type}\n",
            "--connect-timeout",
            "15",
            "--max-time",
            max_t,
            # soft limit: curl has --max-filesize
            "--max-filesize",
            str(max_bytes),
            "--",
            url,
        ]
    )
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5.0, timeout_s + 10),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"curl exit {proc.returncode}").strip()
        raise RuntimeError(err)
    # write-out is last lines; content_type may be empty line
    raw_lines = (proc.stdout or "").splitlines()
    # take last 4 lines as write-out
    if len(raw_lines) < 4:
        raise RuntimeError(f"unexpected curl -w output: {proc.stdout!r}")
    status_s, final, size_s, ctype = raw_lines[-4:]
    try:
        status = int(status_s)
        size = int(float(size_s or 0))
    except ValueError as e:
        raise RuntimeError(f"bad curl meta: {raw_lines[-4:]}") from e
    return {
        "status": status,
        "final_url": final,
        "bytes": size,
        "content_type": ctype or None,
        "mode": "curl",
    }


def urllib_open(url: str, *, timeout: float = 30, method: str = "GET"):
    ctx = ssl_context()
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": "hermes-chrome/1.8.9 (+local)",
            "Accept": "*/*",
        },
    )
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
    return opener.open(req, timeout=timeout)

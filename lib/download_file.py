"""Download a URL to the hermes-chrome downloads directory (local only).

Modes:
  - direct: urllib (no browser cookies)
  - cookies: via extension fetch_url action over the bridge (daily Chrome cookies)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

# Reuse check/analyze from same package dir
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from analyze_file import analyze_file  # noqa: E402
from check_url import check_url  # noqa: E402
from net_util import curl_available, curl_download, ssl_context  # noqa: E402

DEFAULT_MAX_BYTES = int(
    os.environ.get("HERMES_CHROME_DOWNLOAD_MAX_BYTES", str(50 * 1024 * 1024))
)
DEFAULT_BRIDGE = os.environ.get("HERMES_CHROME_BRIDGE", "http://127.0.0.1:19876").rstrip(
    "/"
)


def _bridge_token() -> str:
    tok = (
        os.environ.get("HERMES_CHROME_BRIDGE_TOKEN")
        or os.environ.get("HERMES_TABGROUP_BRIDGE_TOKEN")
        or ""
    ).strip()
    if tok:
        return tok
    run = os.environ.get("HERMES_CHROME_RUN") or os.path.join(
        os.path.expanduser("~"), ".hermes", "run", "hermes-chrome"
    )
    env_path = Path(run) / "bridge.env"
    if not env_path.is_file():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if line.startswith("HERMES_CHROME_BRIDGE_TOKEN="):
                val = line.split("=", 1)[1].strip().strip("'").strip('"')
                return val
    except OSError:
        return ""
    return ""


def default_download_dir() -> Path:
    run = os.environ.get("HERMES_CHROME_RUN") or os.path.join(
        os.path.expanduser("~"), ".hermes", "run", "hermes-chrome"
    )
    d = Path(run) / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(name: str) -> str:
    name = name.strip().replace("\x00", "")
    name = re.sub(r"[/\\]+", "_", name)
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name, flags=re.UNICODE)
    name = name.strip(" .")
    if not name or name in {".", ".."}:
        name = "download.bin"
    return name[:180]


def _filename_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    base = path.rsplit("/", 1)[-1] if path else ""
    base = urllib.parse.unquote(base)
    return _safe_filename(base or f"download-{int(time.time())}.bin")


def _filename_from_cd(header: str | None) -> str | None:
    if not header:
        return None
    # filename="..." or filename*=UTF-8''...
    m = re.search(r'filename\*=UTF-8\'\'([^;]+)', header, re.I)
    if m:
        return _safe_filename(urllib.parse.unquote(m.group(1)))
    m = re.search(r'filename="([^"]+)"', header, re.I)
    if m:
        return _safe_filename(m.group(1))
    m = re.search(r"filename=([^;]+)", header, re.I)
    if m:
        return _safe_filename(m.group(1).strip().strip('"'))
    return None


def download_direct(
    url: str,
    *,
    out: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    name = _filename_from_url(url)
    dest = out or (default_download_dir() / name)
    dest = dest.expanduser()
    if dest.is_dir():
        dest = dest / name
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Prefer curl (system CA). Fall back to urllib + certifi/ssl_context.
    if curl_available():
        try:
            meta = curl_download(url, str(dest), max_bytes=max_bytes)
            written = int(meta.get("bytes") or dest.stat().st_size)
            if written > max_bytes:
                try:
                    dest.unlink()
                except OSError:
                    pass
                return {
                    "ok": False,
                    "error": f"download exceeded max_bytes {max_bytes}",
                    "url": url,
                }
            return {
                "ok": True,
                "path": str(dest.resolve()),
                "bytes": written,
                "url": url,
                "final_url": meta.get("final_url") or url,
                "content_type": meta.get("content_type"),
                "status": meta.get("status"),
                "mode": "curl",
            }
        except Exception as e:  # noqa: BLE001
            curl_err = str(e)
            # fall through
    else:
        curl_err = None

    import ssl

    ctx = ssl_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "hermes-chrome-download/1.4 (+local)",
            "Accept": "*/*",
        },
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        with opener.open(req, timeout=60) as resp:
            final = resp.geturl()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            cd = headers.get("content-disposition")
            name2 = _filename_from_cd(cd) or _filename_from_url(final or url)
            if out is None and name2 != dest.name:
                dest = dest.with_name(name2)
            cl = headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > max_bytes:
                return {
                    "ok": False,
                    "error": f"content-length {cl} exceeds max_bytes {max_bytes}",
                    "url": url,
                    "final_url": final,
                }
            written = 0
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        f.close()
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                        return {
                            "ok": False,
                            "error": f"download exceeded max_bytes {max_bytes}",
                            "url": url,
                            "final_url": final,
                        }
                    f.write(chunk)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ssl.SSLError) as e:
        msg = str(e)
        if curl_err:
            msg = f"curl failed ({curl_err}); urllib failed: {e}"
        return {"ok": False, "error": msg, "url": url}

    return {
        "ok": True,
        "path": str(dest.resolve()),
        "bytes": written,
        "url": url,
        "final_url": final,
        "content_type": headers.get("content-type"),
        "mode": "urllib",
    }


def _bridge_command(bridge: str, payload: dict[str, Any], *, timeout_s: float = 90) -> dict[str, Any]:
    cid = payload.get("id") or str(uuid.uuid4())
    payload = {**payload, "id": cid}
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    tok = _bridge_token()
    if tok:
        headers["X-Hermes-Chrome-Token"] = tok
    req = urllib.request.Request(
        f"{bridge}/v1/command",
        data=data,
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        enq = json.loads(r.read().decode())
    rid = enq.get("id") or cid
    result_req = urllib.request.Request(
        f"{bridge}/v1/result/{rid}?timeout={int(timeout_s)}",
        headers={k: v for k, v in headers.items() if k != "Content-Type"},
        method="GET",
    )
    with urllib.request.urlopen(result_req, timeout=timeout_s + 15) as r:
        body = json.loads(r.read().decode())
    if not body.get("ok"):
        raise RuntimeError(body.get("error") or str(body))
    return body.get("data") or {}


def download_with_cookies(
    url: str,
    *,
    out: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    bridge: str = DEFAULT_BRIDGE,
) -> dict[str, Any]:
    data = _bridge_command(
        bridge,
        {
            "action": "fetch_url",
            "url": url,
            "maxBytes": max_bytes,
        },
        timeout_s=120,
    )
    b64 = data.get("bodyBase64") or ""
    if not b64:
        return {"ok": False, "error": "extension returned empty body", "url": url}
    raw = base64.b64decode(b64)
    if len(raw) > max_bytes:
        return {
            "ok": False,
            "error": f"body {len(raw)} exceeds max_bytes {max_bytes}",
            "url": url,
        }
    name = (
        _filename_from_cd(data.get("contentDisposition"))
        or _filename_from_url(data.get("finalUrl") or url)
    )
    dest = out or (default_download_dir() / name)
    dest = dest.expanduser()
    if dest.is_dir():
        dest = dest / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return {
        "ok": True,
        "path": str(dest.resolve()),
        "bytes": len(raw),
        "url": url,
        "final_url": data.get("finalUrl") or url,
        "content_type": data.get("contentType"),
        "status": data.get("status"),
        "mode": "cookies",
    }


def download(
    url: str,
    *,
    out: str | Path | None = None,
    check: bool = True,
    force: bool = False,
    cookies: bool = False,
    analyze: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    bridge: str = DEFAULT_BRIDGE,
) -> dict[str, Any]:
    result: dict[str, Any] = {"url": url}

    if check:
        chk = check_url(url, follow=True)
        result["check"] = chk
        if not chk.get("ok") and not force:
            result["ok"] = False
            result["error"] = "URL blocked by check-url (use --force to override)"
            return result
        if chk.get("risk") == "medium" and not force:
            # Allow medium with warning but still download (user can --force to silence)
            result["check_warning"] = True
        # Prefer final URL after redirects for direct download
        fetch_url = chk.get("final_url") or url
    else:
        fetch_url = url

    out_path = Path(out).expanduser() if out else None
    if cookies:
        dl = download_with_cookies(
            fetch_url, out=out_path, max_bytes=max_bytes, bridge=bridge
        )
    else:
        dl = download_direct(fetch_url, out=out_path, max_bytes=max_bytes)

    result.update(dl)
    if not dl.get("ok"):
        return result

    if analyze and dl.get("path"):
        result["analyze"] = analyze_file(dl["path"])
        if not result["analyze"].get("ok") and not force:
            result["ok"] = False
            result["error"] = "File blocked by analyze (use --force to keep anyway)"
            # leave file on disk for inspection
            return result

    result["ok"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    url = None
    out = None
    check = True
    force = False
    cookies = False
    analyze = True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--out", "-o"):
            out = argv[i + 1]
            i += 2
        elif a == "--no-check":
            check = False
            i += 1
        elif a == "--force":
            force = True
            i += 1
        elif a in ("--cookies", "--with-cookies"):
            cookies = True
            i += 1
        elif a == "--no-analyze":
            analyze = False
            i += 1
        elif a in ("-h", "--help"):
            print(
                "usage: download_file.py [--out path] [--no-check] [--force] "
                "[--cookies] [--no-analyze] <url>",
                file=sys.stderr,
            )
            return 2
        else:
            url = a
            i += 1
    if not url:
        print(
            "usage: download_file.py [--out path] [--no-check] [--force] "
            "[--cookies] [--no-analyze] <url>",
            file=sys.stderr,
        )
        return 2

    result = download(
        url,
        out=out,
        check=check,
        force=force,
        cookies=cookies,
        analyze=analyze,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("ok"):
        return 2
    if result.get("check", {}).get("risk") == "medium" or result.get(
        "analyze", {}
    ).get("risk") == "medium":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

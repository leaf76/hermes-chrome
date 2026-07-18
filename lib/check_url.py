"""Local URL safety checks (no third-party threat intel).

Heuristics only — fail-closed on clearly dangerous schemes; warn on risky patterns.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from net_util import curl_available, curl_head_chain, ssl_context  # noqa: E402
from policy import check_host_policy  # noqa: E402

# Default limits
MAX_URL_LEN = 2048
MAX_REDIRECTS = 8
REQUEST_TIMEOUT_S = 8.0

BLOCKED_SCHEMES = {
    "javascript",
    "vbscript",
    "data",
    "file",
    "blob",
    "chrome",
    "chrome-extension",
    "about",
    "view-source",
}

# Not blocked hard, but high risk for agent auto-open
WARN_SCHEMES = {"http", "ftp", "ws"}

SUSPICIOUS_TLDS = {
    "zip",
    "mov",
    "country",
    "gq",
    "tk",
    "ml",
    "ga",
    "cf",
    "top",
    "xyz",
    "work",
    "click",
    "link",
    "loan",
    "racing",
    "download",
    "review",
}

# Hostnames that are never fine for agent downloads from "internet" context
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _finding(
    level: str, code: str, message: str, **extra: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {"level": level, "code": code, "message": message}
    out.update(extra)
    return out


def _host_is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _host_is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        return False


def inspect_url_static(url: str) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    raw = (url or "").strip()
    if not raw:
        findings.append(_finding("block", "empty", "URL is empty"))
        return {
            "ok": False,
            "risk": "high",
            "url": raw,
            "findings": findings,
        }

    if len(raw) > MAX_URL_LEN:
        findings.append(
            _finding(
                "block",
                "too_long",
                f"URL length {len(raw)} exceeds {MAX_URL_LEN}",
            )
        )

    # Whitespace / control chars
    if re.search(r"[\x00-\x1f\x7f]", raw):
        findings.append(
            _finding("block", "control_chars", "URL contains control characters")
        )

    # @ phishing pattern in path-looking hosts is handled by urllib
    parsed = urllib.parse.urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        findings.append(_finding("block", "no_scheme", "URL missing scheme"))
        return {
            "ok": False,
            "risk": "high",
            "url": raw,
            "parsed": {"scheme": "", "netloc": parsed.netloc, "path": parsed.path},
            "findings": findings,
        }

    if scheme in BLOCKED_SCHEMES:
        findings.append(
            _finding(
                "block",
                "blocked_scheme",
                f"Scheme '{scheme}' is not allowed for agent open/download",
            )
        )
    elif scheme in WARN_SCHEMES:
        findings.append(
            _finding(
                "warn",
                "insecure_scheme",
                f"Scheme '{scheme}' is not encrypted",
            )
        )
    elif scheme not in {"https", "http"}:
        findings.append(
            _finding(
                "warn",
                "unusual_scheme",
                f"Unusual scheme '{scheme}'",
            )
        )

    host = (parsed.hostname or "").lower()
    if not host and scheme in {"http", "https"}:
        findings.append(_finding("block", "no_host", "URL has no hostname"))

    if parsed.username or parsed.password:
        findings.append(
            _finding(
                "warn",
                "userinfo",
                "URL embeds username/password (credential phishing pattern)",
            )
        )

    if host in LOCAL_HOSTS or (host and _host_is_private_ip(host)):
        findings.append(
            _finding(
                "warn",
                "local_or_private",
                f"Host '{host}' is localhost or private/reserved",
            )
        )

    if host and _host_is_ip(host):
        findings.append(
            _finding("warn", "ip_literal", f"Host is a bare IP address: {host}")
        )

    # Double-encoding / excessive %
    if raw.count("%") > 40:
        findings.append(
            _finding("warn", "heavy_encoding", "URL is heavily percent-encoded")
        )

    # Homoglyph-ish: mixed scripts not fully checked; flag punycode as info
    if host.startswith("xn--") or ".xn--" in host:
        findings.append(
            _finding("info", "punycode", f"IDN/punycode host: {host}")
        )

    # Suspicious TLD
    if host and "." in host:
        tld = host.rsplit(".", 1)[-1]
        if tld in SUSPICIOUS_TLDS:
            findings.append(
                _finding(
                    "warn",
                    "suspicious_tld",
                    f"TLD '.{tld}' is commonly abused (heuristic)",
                )
            )

    # Double extension style in path (invoice.pdf.exe)
    path = parsed.path or ""
    base = path.rsplit("/", 1)[-1]
    if re.search(r"\.(pdf|doc|docx|xls|xlsx|png|jpg|jpeg|gif|zip|txt)\.(exe|scr|bat|cmd|js|vbs|ps1|msi|dll)$", base, re.I):
        findings.append(
            _finding(
                "block",
                "double_extension",
                f"Suspicious double extension in path: {base}",
            )
        )
    elif re.search(r"\.(exe|scr|bat|cmd|ps1|msi|dll|js|vbs)$", base, re.I):
        findings.append(
            _finding(
                "warn",
                "executable_path",
                f"Path looks like an executable: {base}",
            )
        )

    risk = _risk_from_findings(findings)
    ok = not any(f["level"] == "block" for f in findings)
    return {
        "ok": ok,
        "risk": risk,
        "url": raw,
        "parsed": {
            "scheme": scheme,
            "host": host,
            "port": parsed.port,
            "path": path,
            "query": parsed.query,
        },
        "findings": findings,
    }


def _risk_from_findings(findings: list[dict[str, Any]]) -> str:
    levels = {f["level"] for f in findings}
    if "block" in levels:
        return "high"
    if "warn" in levels:
        return "medium"
    if "info" in levels:
        return "low"
    return "low"


def follow_redirects(url: str, *, max_redirects: int = MAX_REDIRECTS) -> dict[str, Any]:
    """Follow redirects with HEAD (prefer curl/system certs). Local only."""
    static0 = inspect_url_static(url)
    if not static0["ok"]:
        return {
            "ok": False,
            "risk": static0["risk"],
            "url": url,
            "final_url": url,
            "redirects": 0,
            "chain": [],
            "findings": static0["findings"],
            "error": "blocked_before_request",
        }

    # Prefer curl — uses macOS/system CA store (python.org builds often lack certs).
    if curl_available():
        try:
            meta = curl_head_chain(url, max_redirs=max_redirects, timeout_s=REQUEST_TIMEOUT_S)
            final = meta.get("final_url") or url
            static_final = inspect_url_static(final)
            findings = list(static_final["findings"])
            prev_s = urllib.parse.urlparse(url).scheme
            next_s = urllib.parse.urlparse(final).scheme
            if prev_s == "https" and next_s == "http":
                findings.append(
                    _finding(
                        "warn",
                        "https_to_http",
                        f"Redirect downgrades HTTPS → HTTP: {final}",
                    )
                )
            redirs = int(meta.get("redirects") or 0)
            if redirs > 4:
                findings.append(
                    _finding(
                        "warn",
                        "long_redirect_chain",
                        f"Long redirect chain ({redirs} hops)",
                    )
                )
            status = meta.get("status")
            if status and int(status) >= 400:
                findings.append(
                    _finding("warn", "http_error", f"HTTP {status} at {final}")
                )
            # Final URL must not be blocked
            if not static_final["ok"]:
                findings.append(
                    _finding(
                        "block",
                        "redirect_to_blocked",
                        f"Redirect target blocked: {final}",
                    )
                )
            return {
                "ok": not any(f["level"] == "block" for f in findings),
                "risk": _risk_from_findings(findings),
                "url": url,
                "final_url": final,
                "redirects": redirs,
                "chain": [
                    {
                        "url": url,
                        "status": status,
                        "final_url": final,
                        "mode": "curl_head",
                    }
                ],
                "findings": findings,
                "status": status,
            }
        except Exception as e:  # noqa: BLE001
            # fall through to urllib
            curl_err = str(e)
    else:
        curl_err = None

    chain: list[dict[str, Any]] = []
    current = url
    ctx = ssl_context()

    for i in range(max_redirects + 1):
        static = inspect_url_static(current)
        if not static["ok"] and i == 0:
            return {
                "ok": False,
                "risk": static["risk"],
                "url": url,
                "final_url": current,
                "redirects": 0,
                "chain": chain,
                "findings": static["findings"],
                "error": "blocked_before_request",
            }

        req = urllib.request.Request(
            current,
            method="GET",
            headers={
                "User-Agent": "hermes-chrome-check-url/1.4 (+local; no cloud)",
                "Accept": "*/*",
            },
        )
        try:
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=ctx),
                NoRedirectHandler(),
            )
            with opener.open(req, timeout=REQUEST_TIMEOUT_S) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                chain.append(
                    {
                        "url": current,
                        "status": status,
                        "content_type": headers.get("content-type"),
                        "content_length": headers.get("content-length"),
                        "server": headers.get("server"),
                    }
                )
                try:
                    resp.read(0)
                except Exception:
                    pass
                break
        except urllib.error.HTTPError as e:
            status = e.code
            headers = {
                k.lower(): v for k, v in (e.headers.items() if e.headers else [])
            }
            loc = headers.get("location")
            chain.append(
                {
                    "url": current,
                    "status": status,
                    "location": loc,
                    "content_type": headers.get("content-type"),
                }
            )
            if status in {301, 302, 303, 307, 308} and loc:
                nxt = urllib.parse.urljoin(current, loc)
                hop_static = inspect_url_static(nxt)
                if not hop_static["ok"]:
                    return {
                        "ok": False,
                        "risk": "high",
                        "url": url,
                        "final_url": nxt,
                        "redirects": len(chain),
                        "chain": chain,
                        "findings": hop_static["findings"]
                        + [
                            _finding(
                                "block",
                                "redirect_to_blocked",
                                f"Redirect target blocked: {nxt}",
                            )
                        ],
                    }
                current = nxt
                if i >= max_redirects:
                    return {
                        "ok": False,
                        "risk": "high",
                        "url": url,
                        "final_url": current,
                        "redirects": len(chain),
                        "chain": chain,
                        "findings": [
                            _finding(
                                "block",
                                "too_many_redirects",
                                f"Exceeded {max_redirects} redirects",
                            )
                        ],
                    }
                continue
            static2 = inspect_url_static(current)
            findings = list(static2["findings"])
            findings.append(
                _finding("warn", "http_error", f"HTTP {status} at {current}")
            )
            return {
                "ok": static2["ok"],
                "risk": _risk_from_findings(findings),
                "url": url,
                "final_url": current,
                "redirects": max(0, len(chain) - 1),
                "chain": chain,
                "findings": findings,
                "status": status,
            }
        except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as e:
            static2 = inspect_url_static(url)
            findings = list(static2["findings"])
            msg = f"Request failed: {e}"
            if curl_err:
                msg = f"curl failed ({curl_err}); urllib failed: {e}"
            findings.append(_finding("warn", "network_error", msg))
            return {
                "ok": static2["ok"],
                "risk": _risk_from_findings(findings),
                "url": url,
                "final_url": current,
                "redirects": len(chain),
                "chain": chain,
                "findings": findings,
                "error": str(e),
            }

    static_final = inspect_url_static(current)
    findings = list(static_final["findings"])
    if chain:
        first_s = urllib.parse.urlparse(url).scheme
        last_s = urllib.parse.urlparse(current).scheme
        if first_s == "https" and last_s == "http":
            findings.append(
                _finding("warn", "https_to_http", "Final hop is HTTP after HTTPS")
            )
    if len(chain) > 4:
        findings.append(
            _finding(
                "warn",
                "long_redirect_chain",
                f"Long redirect chain ({len(chain)} hops)",
            )
        )

    return {
        "ok": not any(f["level"] == "block" for f in findings),
        "risk": _risk_from_findings(findings),
        "url": url,
        "final_url": current,
        "redirects": max(0, len(chain) - 1),
        "chain": chain,
        "findings": findings,
        "status": chain[-1].get("status") if chain else None,
    }


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None

    def http_error_301(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, msg, headers, fp
        )

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, msg, headers, fp
        )

    def http_error_303(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, msg, headers, fp
        )

    def http_error_307(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, msg, headers, fp
        )

    def http_error_308(self, req, fp, code, msg, headers):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, msg, headers, fp
        )


def check_url(url: str, *, follow: bool = True) -> dict[str, Any]:
    static = inspect_url_static(url)
    # Local host policy (allow/deny lists)
    pol = check_host_policy(url)
    if pol.get("findings"):
        static["findings"] = list(static.get("findings") or []) + list(pol["findings"])
        static["ok"] = static["ok"] and pol.get("ok", True)
        static["risk"] = _risk_from_findings(static["findings"])
        static["policy"] = {
            "path": pol.get("policy_path"),
            "ok": pol.get("ok"),
        }

    if not follow or not static["ok"]:
        if not follow:
            return {**static, "final_url": url, "redirects": 0, "chain": []}
        if not static["ok"]:
            return {
                **static,
                "final_url": url,
                "redirects": 0,
                "chain": [],
            }
    result = follow_redirects(url)
    # Re-check policy on final URL
    if result.get("final_url") and result["final_url"] != url:
        pol2 = check_host_policy(result["final_url"])
        if pol2.get("findings"):
            result["findings"] = list(result.get("findings") or []) + list(
                pol2["findings"]
            )
            result["ok"] = result.get("ok", True) and pol2.get("ok", True)
            result["risk"] = _risk_from_findings(result["findings"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    follow = True
    url = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--no-follow",):
            follow = False
            i += 1
        elif a in ("--follow",):
            follow = True
            i += 1
        elif a in ("-h", "--help"):
            print(
                "usage: check_url.py [--no-follow] <url>",
                file=sys.stderr,
            )
            return 2
        else:
            url = a
            i += 1
    if not url:
        print("usage: check_url.py [--no-follow] <url>", file=sys.stderr)
        return 2
    result = check_url(url, follow=follow)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    # exit 2 = blocked, 1 = medium risk warn, 0 = ok/low
    if not result.get("ok"):
        return 2
    if result.get("risk") == "medium":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

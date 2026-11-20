"""Host allow/deny policy for hermes-chrome local ops.

Config file (JSON), first existing path wins:
  $HERMES_CHROME_POLICY
  ~/.hermes/run/hermes-chrome/policy.json
  <repo>/policy.example.json is never auto-loaded (example only)

Example:
{
  "allow_hosts": ["example.com", "*.github.com"],
  "deny_hosts": ["malware.example"],
  "require_https": false,
  "block_ip_literals": true,
  "block_private_hosts": true
}

Empty allow_hosts = allow all (except deny_hosts).
Defaults when no policy file: block private hosts + IP literals (fail-closed).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Safer defaults when no policy.json is present.
_DEFAULT_POLICY: dict[str, Any] = {
    "allow_hosts": [],
    "deny_hosts": [],
    "require_https": False,
    "block_ip_literals": True,
    "block_private_hosts": True,
}


def policy_paths() -> list[Path]:
    paths: list[Path] = []
    env = (os.environ.get("HERMES_CHROME_POLICY") or "").strip()
    if env:
        paths.append(Path(env).expanduser())
    run = os.environ.get("HERMES_CHROME_RUN") or os.path.join(
        os.path.expanduser("~"), ".hermes", "run", "hermes-chrome"
    )
    paths.append(Path(run) / "policy.json")
    return paths


def load_policy() -> dict[str, Any]:
    for p in policy_paths():
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {
                    **_DEFAULT_POLICY,
                    "_path": str(p),
                    "_invalid": True,
                }
            if not isinstance(data, dict):
                return {
                    **_DEFAULT_POLICY,
                    "_path": str(p),
                    "_invalid": True,
                }
            merged = {**_DEFAULT_POLICY, **data}
            merged["_path"] = str(p)
            return merged
    return {**_DEFAULT_POLICY, "_path": None}


def _normalize_host(host: str) -> str:
    h = (host or "").strip().lower().strip("[]").rstrip(".")
    if not h:
        return ""
    try:
        if any(ord(ch) > 127 for ch in h):
            h = h.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        pass
    return h


def _parse_ip_any(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    h = (host or "").strip().strip("[]").rstrip(".")
    if not h:
        return None
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    # Catch non-standard IPv4 notations (decimal integer, octal, hex, shorthand like 127.1)
    if re.fullmatch(r"[0-9a-fx.]+", h, re.I):
        try:
            packed = socket.inet_aton(h)
            return ipaddress.IPv4Address(packed)
        except OSError:
            pass
    return None


def _ip_is_non_global(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _ip_is_non_global(ip.ipv4_mapped)
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    if isinstance(ip, ipaddress.IPv4Address) and ip in cgnat:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def resolve_host_ips_private(host: str) -> bool:
    """Resolve DNS for host and return True if any returned IP is private/non-global."""
    h = _normalize_host(host)
    if not h:
        return True
    ip = _parse_ip_any(h)
    if ip is not None:
        return _ip_is_non_global(ip)
    try:
        infos = socket.getaddrinfo(h, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        addr = info[4][0] if info[4] else ""
        addr_ip = _parse_ip_any(addr.split("%", 1)[0])
        if addr_ip is not None and _ip_is_non_global(addr_ip):
            return True
    return False


def _host_match(host: str, pattern: str) -> bool:
    host = _normalize_host(host)
    pattern = _normalize_host(pattern)
    if not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[1:]  # .example.com
        return host.endswith(suffix) or host == pattern[2:]
    return host == pattern


def _host_is_ip(host: str) -> bool:
    return _parse_ip_any(_normalize_host(host)) is not None


def _host_is_private(host: str) -> bool:
    h = _normalize_host(host)
    if not h:
        return False
    if h in {"localhost", "0.0.0.0", "::1"} or h.endswith(".localhost"):
        return True
    if h in {"metadata.google.internal", "metadata"} or h.endswith(".internal"):
        return True
    ip = _parse_ip_any(h)
    if ip is not None:
        return _ip_is_non_global(ip)
    return False


def check_host_policy(url: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy if policy is not None else load_policy()
    findings: list[dict[str, Any]] = []
    if policy.get("_invalid"):
        findings.append(
            {
                "level": "block",
                "code": "policy_invalid",
                "message": f"policy file unreadable or invalid: {policy.get('_path')}",
            }
        )
        return {
            "ok": False,
            "host": _normalize_host(urlparse(url).hostname or ""),
            "policy_path": policy.get("_path"),
            "findings": findings,
        }
    parsed = urlparse(url)
    host = _normalize_host(parsed.hostname or "")
    scheme = (parsed.scheme or "").lower()

    if policy.get("require_https") and scheme != "https":
        findings.append(
            {
                "level": "block",
                "code": "policy_require_https",
                "message": "policy requires https",
            }
        )

    # Fail-closed defaults: private / link-local / metadata hosts
    if host and policy.get("block_private_hosts", True) and _host_is_private(host):
        findings.append(
            {
                "level": "block",
                "code": "policy_block_private_host",
                "message": f"private/loopback/metadata host blocked by policy: {host}",
            }
        )

    if host and policy.get("block_ip_literals", True) and _host_is_ip(host):
        findings.append(
            {
                "level": "block",
                "code": "policy_block_ip_literal",
                "message": f"IP-literal host blocked by policy: {host}",
            }
        )

    deny = policy.get("deny_hosts") or []
    for pat in deny:
        if _host_match(host, str(pat)):
            findings.append(
                {
                    "level": "block",
                    "code": "policy_deny_host",
                    "message": f"host denied by policy: {host} (matched {pat})",
                }
            )
            break

    allow = policy.get("allow_hosts") or []
    if allow:
        if not host or not any(_host_match(host, str(pat)) for pat in allow):
            findings.append(
                {
                    "level": "block",
                    "code": "policy_not_allowlisted",
                    "message": f"host not in allow_hosts: {host or '(empty)'}",
                }
            )

    ok = not any(f["level"] == "block" for f in findings)
    return {
        "ok": ok,
        "host": host,
        "policy_path": policy.get("_path"),
        "findings": findings,
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "show"
    if cmd in ("show", "print"):
        p = load_policy()
        print(json.dumps(p, indent=2, ensure_ascii=False))
        return 0
    if cmd == "check" and len(argv) >= 2:
        r = check_host_policy(argv[1])
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0 if r.get("ok") else 2
    print("usage: policy.py show | check <url>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

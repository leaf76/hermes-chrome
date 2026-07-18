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
  "block_ip_literals": false
}

Empty allow_hosts = allow all (except deny_hosts).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


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
                if isinstance(data, dict):
                    data["_path"] = str(p)
                    return data
            except (OSError, json.JSONDecodeError):
                continue
    return {
        "allow_hosts": [],
        "deny_hosts": [],
        "require_https": False,
        "block_ip_literals": False,
        "_path": None,
    }


def _host_match(host: str, pattern: str) -> bool:
    host = (host or "").lower().rstrip(".")
    pattern = (pattern or "").lower().strip()
    if not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[1:]  # .example.com
        return host.endswith(suffix) or host == pattern[2:]
    return host == pattern


def check_host_policy(url: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy if policy is not None else load_policy()
    findings: list[dict[str, Any]] = []
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "").lower()

    if policy.get("require_https") and scheme == "http":
        findings.append(
            {
                "level": "block",
                "code": "policy_require_https",
                "message": "policy requires https",
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
    if allow and host:
        if not any(_host_match(host, str(pat)) for pat in allow):
            findings.append(
                {
                    "level": "block",
                    "code": "policy_not_allowlisted",
                    "message": f"host not in allow_hosts: {host}",
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

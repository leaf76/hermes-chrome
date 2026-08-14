#!/usr/bin/env python3
"""Hermes Chrome environment doctor — ready-check for companion + extension.

Stdlib only. Exit 0 if healthy enough for agents (bridge up + token + extension
seen recently when possible); exit 1 with actionable hints otherwise.

  python3 scripts/doctor.py
  python3 scripts/doctor.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

# Allow import from repo
_ROOT_GUESS = Path(__file__).resolve().parents[1]
if str(_ROOT_GUESS / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT_GUESS / "lib"))
if str(_ROOT_GUESS) not in sys.path:
    sys.path.insert(0, str(_ROOT_GUESS))


def fixed_install_root() -> Path:
    return Path.home() / ".hermes" / "hermes-chrome"


def resolve_root() -> Path:
    env = os.environ.get("HERMES_CHROME_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    fixed = fixed_install_root()
    if (fixed / "bridge.py").is_file():
        return fixed.resolve()
    return _ROOT_GUESS.resolve()


def check() -> dict[str, Any]:
    root = resolve_root()
    run = Path(
        os.environ.get("HERMES_CHROME_RUN")
        or Path.home() / ".hermes" / "run" / "hermes-chrome"
    ).expanduser()
    python = sys.executable
    py_which = shutil.which("python3") or shutil.which("python")

    report: dict[str, Any] = {
        "ok": False,
        "ready": False,
        "root": str(root),
        "run_dir": str(run),
        "fixed_install": str(fixed_install_root()),
        "using_fixed_install": root == fixed_install_root().resolve(),
        "checks": [],
        "hints": [],
    }

    def add(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})

    # Python
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    add("python", sys.version_info >= (3, 9), f"{python} ({ver})")
    if sys.version_info < (3, 9):
        report["hints"].append("Install Python 3.9+ from https://www.python.org/downloads/")

    # Layout
    bridge_py = root / "bridge.py"
    mcp_py = root / "mcp_server.py"
    host_py = root / "native_host" / "host.py"
    cli = root / "scripts" / "hermes-chrome.sh"
    add("bridge.py", bridge_py.is_file(), str(bridge_py))
    add("mcp_server.py", mcp_py.is_file(), str(mcp_py))
    add("native_host", host_py.is_file(), str(host_py))
    add("cli", cli.is_file(), str(cli))
    if not bridge_py.is_file():
        report["hints"].append(
            "Companion not found. Install from GitHub: "
            "curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash"
        )

    # Token
    env_file = run / "bridge.env"
    token_ok = False
    if env_file.is_file():
        text = env_file.read_text(encoding="utf-8", errors="replace")
        token_ok = "HERMES_CHROME_BRIDGE_TOKEN=" in text
    add("bridge.env token", token_ok, str(env_file))
    if not token_ok:
        report["hints"].append("Run: hermes-chrome.sh token-setup generate  (or re-run install.sh)")

    # Native host manifest
    nm = run / "native-messaging" / "com.leaf76.hermes_chrome.json"
    add("native messaging manifest", nm.is_file(), str(nm))
    if not nm.is_file():
        report["hints"].append(
            f"From companion root run: bash {root}/scripts/install-native-host.sh install"
        )

    # PATH shim
    shim = Path.home() / ".local" / "bin" / "hermes-chrome"
    on_path = bool(shutil.which("hermes-chrome"))
    shim_exists = shim.is_file()
    add("PATH shim ~/.local/bin/hermes-chrome", shim_exists or on_path, str(shim))
    if shim_exists and not on_path:
        report["hints"].append(
            '~/.local/bin is not in your current PATH. Add it via: echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zshrc'
        )

    # Bridge HTTP
    host = os.environ.get("HERMES_CHROME_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("HERMES_CHROME_BRIDGE_PORT", "19876"))
    url = f"http://{host}:{port}/v1/health"
    health: dict[str, Any] = {}
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=2) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        add("bridge :19876", True, url)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        add("bridge :19876", False, f"{url} — {e}")
        report["hints"].append(
            "Start companion: re-run install.sh or hermes-chrome.sh bridge-start / install-launchd"
        )

    ext = bool(health.get("extension_connected") or health.get("extensionConnected"))
    # Some health payloads use last_seen / clients
    if not ext and isinstance(health, dict):
        if health.get("extension_last_seen") or health.get("clients"):
            ext = True
    add("extension connected", ext if health else False, json.dumps(health)[:200] if health else "no health")
    if health and not ext:
        report["hints"].append(
            "Install/reload Hermes Chrome extension, click icon, Pair if needed. "
            "CWS or Load unpacked from extension/"
        )

    # MCP snippet
    snip = run / "mcp-snippet.json"
    add("mcp snippet", snip.is_file(), str(snip))

    critical_ok = all(
        c["ok"]
        for c in report["checks"]
        if c["name"] in ("python", "bridge.py", "bridge :19876", "bridge.env token")
    )
    report["ok"] = critical_ok
    report["ready"] = critical_ok and ext
    report["health"] = health
    report["python_which"] = py_which
    if report["ready"]:
        report["hints"] = ["All good — agents can use CLI or MCP."]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes Chrome doctor")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = check()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Hermes Chrome doctor")
        print(f"  root:     {report['root']}")
        print(f"  run_dir:  {report['run_dir']}")
        print(f"  fixed:    {report['fixed_install']} (in use: {report['using_fixed_install']})")
        print()
        for c in report["checks"]:
            mark = "OK " if c["ok"] else "FAIL"
            print(f"  [{mark}] {c['name']}: {c['detail']}")
        print()
        print("ready:" if report["ready"] else "not ready:")
        for h in report["hints"]:
            print(f"  • {h}")
        if report["ready"]:
            print("  Smoke: hermes-chrome --json ping")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

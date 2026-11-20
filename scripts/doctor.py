#!/usr/bin/env python3
"""Hermes Chrome environment doctor — ready-check for companion + extension.

Stdlib only. Exit 0 if healthy enough for agents (bridge up + token + extension
seen recently when possible); exit 1 with actionable hints otherwise.

  python3 scripts/doctor.py
  python3 scripts/doctor.py --json
  python3 scripts/doctor.py --fix      # safe repairs, then re-check
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

# Allow import from repo
_ROOT_GUESS = Path(__file__).resolve().parents[1]
if str(_ROOT_GUESS / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT_GUESS / "lib"))
if str(_ROOT_GUESS) not in sys.path:
    sys.path.insert(0, str(_ROOT_GUESS))

from version_util import fetch_latest_tag, product_version, update_report  # noqa: E402


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


def resolve_run() -> Path:
    return Path(
        os.environ.get("HERMES_CHROME_RUN")
        or Path.home() / ".hermes" / "run" / "hermes-chrome"
    ).expanduser()


def apply_fixes(root: Path, run: Path) -> list[str]:
    """Safe, idempotent repairs. Never deletes user data or touches existing tokens."""
    fixes: list[str] = []
    run.mkdir(parents=True, exist_ok=True)

    # 1) bridge.env permissions (POSIX only)
    env_file = run / "bridge.env"
    if env_file.is_file() and sys.platform != "win32":
        try:
            mode = env_file.stat().st_mode & 0o777
            if mode != 0o600:
                env_file.chmod(0o600)
                fixes.append(f"chmod 600 {env_file}")
        except OSError as e:
            fixes.append(f"chmod {env_file} failed: {e}")

    # 2) PATH shim (POSIX only)
    shim = Path.home() / ".local" / "bin" / "hermes-chrome"
    target = root / "scripts" / "hermes-chrome.sh"
    if sys.platform != "win32" and not shim.exists() and target.is_file():
        try:
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.symlink_to(target)
            fixes.append(f"recreated PATH shim {shim} -> {target}")
        except OSError as e:
            fixes.append(f"PATH shim recreate failed: {e}")

    # 3) Native messaging manifest
    nm = run / "native-messaging" / "com.leaf76.hermes_chrome.json"
    if not nm.is_file():
        if sys.platform == "win32":
            ps_script = root / "scripts" / "install-native-host.ps1"
            if ps_script.is_file():
                try:
                    proc = subprocess.run(
                        [
                            "powershell.exe",
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(ps_script),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if proc.returncode == 0:
                        fixes.append("re-registered Windows native messaging host")
                    else:
                        detail = (proc.stderr or proc.stdout or "").strip()[:200]
                        fixes.append(f"native host register failed: {detail}")
                except (OSError, subprocess.TimeoutExpired) as e:
                    fixes.append(f"native host register failed: {e}")
        else:
            host_script = root / "scripts" / "install-native-host.sh"
            if host_script.is_file():
                try:
                    proc = subprocess.run(
                        ["bash", str(host_script), "install"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if proc.returncode == 0:
                        fixes.append("re-registered native messaging host")
                    else:
                        detail = (proc.stderr or proc.stdout or "").strip()[:200]
                        fixes.append(f"native host register failed: {detail}")
                except (OSError, subprocess.TimeoutExpired) as e:
                    fixes.append(f"native host register failed: {e}")

    # 4) Missing token → generate directly in Python (cross-platform, UTF-8 no BOM)
    if not env_file.is_file() or "HERMES_CHROME_BRIDGE_TOKEN=" not in (
        env_file.read_text(encoding="utf-8", errors="replace") if env_file.is_file() else ""
    ):
        try:
            tok = secrets.token_urlsafe(32)
            body = (
                "# Hermes Chrome bridge auth (local only). Auto-managed; do not commit.\n"
                f"export HERMES_CHROME_BRIDGE_TOKEN='{tok}'\n"
            )
            if sys.platform != "win32":
                fd = os.open(str(env_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body)
                os.chmod(env_file, 0o600)
            else:
                env_file.write_text(body, encoding="utf-8")
            fixes.append(f"generated bridge token ({env_file})")
        except OSError as e:
            fixes.append(f"token generate failed: {e}")

    # 5) Bridge down → start + open pairing
    os.environ.setdefault("HERMES_CHROME_ROOT", str(root))
    os.environ["HERMES_CHROME_RUN"] = str(run)
    try:
        from bridge_runtime import bridge_up, ensure_and_pair, health_fresh

        was_up = bridge_up(health_fresh())
        h = ensure_and_pair(timeout_s=10.0)
        if bridge_up(h):
            if h.get("extension_connected"):
                fixes.append(
                    "bridge verified up; extension connected"
                    if was_up
                    else "bridge started; extension connected"
                )
            else:
                fixes.append(
                    "bridge verified up (extension still needs a click/Pair)"
                    if was_up
                    else "bridge started (extension still needs a click/Pair)"
                )
        else:
            fixes.append(f"bridge start failed: {h.get('error', 'unknown')}")
    except Exception as e:  # noqa: BLE001 — doctor must not crash on fix path
        fixes.append(f"bridge start failed: {type(e).__name__}: {e}")

    # Give the extension a beat to reconnect after bridge start.
    time.sleep(1.0)
    return fixes


def check(*, check_update: bool = True) -> dict[str, Any]:
    root = resolve_root()
    run = resolve_run()
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
        if sys.platform == "win32":
            report["hints"].append(
                "Companion not found. Run from GitHub clone: "
                "powershell -ExecutionPolicy Bypass -File .\\scripts\\install-windows.ps1"
            )
        else:
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
        report["hints"].append(
            f"Run: {python} {root / 'scripts' / 'doctor.py'} --fix  (or re-run installer)"
            if sys.platform == "win32"
            else "Run: hermes-chrome.sh token-setup generate  (or re-run install.sh)"
        )

    # Native host manifest
    nm = run / "native-messaging" / "com.leaf76.hermes_chrome.json"
    add("native messaging manifest", nm.is_file(), str(nm))
    if not nm.is_file():
        if sys.platform == "win32":
            report["hints"].append(
                f"Run: powershell -ExecutionPolicy Bypass -File {root / 'scripts' / 'install-native-host.ps1'}"
            )
        else:
            report["hints"].append(
                f"From companion root run: bash {root}/scripts/install-native-host.sh install"
            )

    # PATH shim
    shim = Path.home() / ".local" / "bin" / "hermes-chrome"
    on_path = bool(shutil.which("hermes-chrome"))
    shim_exists = shim.is_file()
    if sys.platform == "win32":
        add("PATH shim ~/.local/bin/hermes-chrome", True, "optional on Windows (use npx or scripts/hermes-chrome.sh under Git Bash)")
    else:
        add("PATH shim ~/.local/bin/hermes-chrome", shim_exists or on_path, str(shim))
        if shim_exists and not on_path:
            report["hints"].append(
                '~/.local/bin is not in your current PATH. Add it via: echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zshrc'
            )

    # Bridge HTTP (bypass system/env HTTP proxies for loopback health check)
    host = os.environ.get("HERMES_CHROME_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("HERMES_CHROME_BRIDGE_PORT", "19876"))
    url = f"http://{host}:{port}/v1/health"
    health: dict[str, Any] = {}
    headers: dict[str, str] = {}
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("HERMES_CHROME_BRIDGE_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip("'").strip('"')
                    if tok:
                        headers["X-Hermes-Chrome-Token"] = tok
                    break
        except OSError:
            pass
    try:
        req = Request(url, method="GET", headers=headers)
        opener = build_opener(ProxyHandler({}))
        with opener.open(req, timeout=2) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        add("bridge :19876", True, url)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        add("bridge :19876", False, f"{url} — {e}")
        report["hints"].append(
            f"Start companion: powershell -ExecutionPolicy Bypass -File {root / 'scripts' / 'install-windows.ps1'}"
            if sys.platform == "win32"
            else "Start companion: re-run install.sh or hermes-chrome.sh bridge-start / install-launchd"
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

    local_ver = product_version(root)
    ext_ver = None
    if isinstance(health, dict):
        ext_ver = health.get("extension_version")
        if health.get("companion_version"):
            local_ver = str(health.get("companion_version"))
    skip_upd = (not check_update) or os.environ.get("HERMES_CHROME_SKIP_UPDATE_CHECK") == "1"
    latest = None if skip_upd else fetch_latest_tag()
    upd = update_report(local=local_ver, latest_tag=latest, extension=ext_ver)
    report["version"] = upd
    add("companion version", True, local_ver)
    add(
        "updates",
        not upd["hints"],
        f"github={latest or 'skipped'} extension={ext_ver or 'unknown'}",
    )
    try:
        from self_update import is_enabled as _au_on

        add("companion auto-update", True, "enabled" if _au_on() else "disabled")
    except Exception:  # noqa: BLE001
        add("companion auto-update", True, "unknown")

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
    report["hints"].extend(upd["hints"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes Chrome doctor")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--skip-update",
        action="store_true",
        help="Do not query GitHub Releases for a newer companion",
    )
    ap.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Attempt safe repairs (env perms, PATH shim, native host, token, "
            "bridge start) then re-run checks"
        ),
    )
    args = ap.parse_args()

    fixes: list[str] = []
    if args.fix:
        fixes = apply_fixes(resolve_root(), resolve_run())

    report = check(check_update=not args.skip_update)
    if args.fix:
        report["fixes"] = fixes
        report["hints"] = [h for h in report["hints"] if "re-run install" not in h]

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Hermes Chrome doctor")
        print(f"  root:     {report['root']}")
        print(f"  run_dir:  {report['run_dir']}")
        print(f"  fixed:    {report['fixed_install']} (in use: {report['using_fixed_install']})")
        if fixes:
            print()
            print("applied fixes:")
            for f in fixes:
                print(f"  • {f}")
        print()
        for c in report["checks"]:
            mark = "OK " if c["ok"] else ("WARN" if c["name"] == "updates" else "FAIL")
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

#!/usr/bin/env python3
"""Register Hermes Chrome stdio MCP with local agent clients.

Stdlib only. Safe to re-run: only overwrites blocks/entries we previously wrote
(or creates missing keys). Never deletes unrelated MCP servers.

Supported clients:
  - Grok Build      (~/.grok/config.toml)
  - Cursor          (~/.cursor/mcp.json)
  - Claude Desktop  (platform config path)
  - VS Code / Copilot-style mcp.json under ~/.vscode or user data (best-effort)

Also writes a copy-paste snippet to:
  ~/.hermes/run/hermes-chrome/mcp-snippet.json

Usage:
  python3 scripts/install-mcp.py
  python3 scripts/install-mcp.py --root /path/to/hermes-chrome
  python3 scripts/install-mcp.py --status
  python3 scripts/install-mcp.py --uninstall
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any

MARKER_START = "# --- hermes-chrome (auto by install-mcp) ---"
MARKER_END = "# --- end hermes-chrome ---"
# Legacy markers from older installers
LEGACY_MARKERS = (
    r"# --- hermes-chrome \(auto by scripts/install-for-agent\.sh\) ---",
    r"# --- hermes-chrome \(auto by scripts/install-windows\.ps1\) ---",
    r"# --- hermes-chrome \(auto by install-mcp\) ---",
)


def log(msg: str) -> None:
    print(f"[hermes-chrome-mcp] {msg}", file=sys.stderr)


def die(msg: str) -> None:
    log(f"error: {msg}")
    raise SystemExit(1)


def find_python() -> str:
    return sys.executable or shutil.which("python3") or shutil.which("python") or "python3"


def default_root() -> Path:
    env = os.environ.get("HERMES_CHROME_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # Prefer fixed install location when present
    fixed = Path.home() / ".hermes" / "hermes-chrome"
    if (fixed / "mcp_server.py").is_file():
        return fixed.resolve()
    return Path(__file__).resolve().parents[1]


def run_dir() -> Path:
    return Path(
        os.environ.get("HERMES_CHROME_RUN")
        or Path.home() / ".hermes" / "run" / "hermes-chrome"
    ).expanduser()


def mcp_entry(root: Path, python: str) -> dict[str, Any]:
    mcp_py = str((root / "mcp_server.py").resolve())
    return {
        "command": python,
        "args": [mcp_py],
        "env": {
            "HERMES_CHROME_ROOT": str(root.resolve()),
        },
    }


def write_snippet(root: Path, python: str) -> Path:
    rd = run_dir()
    rd.mkdir(parents=True, exist_ok=True)
    path = rd / "mcp-snippet.json"
    payload = {
        "comment": "Paste into Cursor/Claude Desktop mcpServers (or use install-mcp.py)",
        "mcpServers": {
            "hermes-chrome": mcp_entry(root, python),
        },
        "grok_toml_hint": f'''[mcp_servers.hermes-chrome]
command = "{python}"
args = ["{(root / "mcp_server.py").resolve()}"]
enabled = true
startup_timeout_sec = 45
tool_timeout_sec = 120

[mcp_servers.hermes-chrome.env]
HERMES_CHROME_ROOT = "{root.resolve()}"
''',
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def strip_legacy_toml_blocks(text: str) -> str:
    for m in LEGACY_MARKERS:
        text = re.sub(
            rf"\n{m}.*?{re.escape(MARKER_END)}\n?",
            "\n",
            text,
            flags=re.S,
        )
    text = re.sub(
        rf"\n{re.escape(MARKER_START)}.*?{re.escape(MARKER_END)}\n?",
        "\n",
        text,
        flags=re.S,
    )
    return text


def install_grok(root: Path, python: str, *, uninstall: bool = False) -> str:
    conf = Path(os.environ.get("GROK_CONFIG") or Path.home() / ".grok" / "config.toml")
    if not conf.parent.is_dir() and not uninstall:
        return "skip: no ~/.grok (install Grok Build, re-run)"
    if not conf.is_file() and uninstall:
        return "skip: no Grok config"
    if not conf.parent.is_dir():
        conf.parent.mkdir(parents=True, exist_ok=True)

    text = conf.read_text(encoding="utf-8") if conf.is_file() else ""
    text = strip_legacy_toml_blocks(text)

    if uninstall:
        if conf.is_file():
            conf.write_text(text.rstrip() + "\n", encoding="utf-8")
        return f"cleaned auto MCP block in {conf}"

    # Custom entry we did not write — leave alone
    if re.search(r"\[mcp_servers\.hermes-chrome\]", text):
        return f"skip: custom [mcp_servers.hermes-chrome] in {conf}"

    mcp_py = (root / "mcp_server.py").resolve()
    block = f"""
{MARKER_START}
[mcp_servers.hermes-chrome]
command = "{python}"
args = ["{mcp_py}"]
enabled = true
startup_timeout_sec = 45
tool_timeout_sec = 120

[mcp_servers.hermes-chrome.env]
HERMES_CHROME_ROOT = "{root.resolve()}"
{MARKER_END}
"""
    new_text = (text.rstrip() + "\n" + block).strip() + "\n"
    conf.write_text(new_text, encoding="utf-8")
    return f"ok: Grok → {conf}"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        die(f"invalid JSON in {path}: {e}")


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def install_json_mcp(
    path: Path,
    root: Path,
    python: str,
    *,
    key: str = "mcpServers",
    uninstall: bool = False,
    create_if_missing: bool = True,
    label: str = "client",
) -> str:
    if not path.parent.is_dir() and not create_if_missing:
        return f"skip: no {path.parent} ({label})"
    if not path.is_file() and uninstall:
        return f"skip: no {path} ({label})"
    if not path.is_file() and not create_if_missing:
        return f"skip: no {path} ({label})"

    data = _load_json(path) if path.is_file() else {}
    servers = data.get(key)
    if servers is None:
        servers = {}
        data[key] = servers
    if not isinstance(servers, dict):
        return f"skip: {path} {key} is not an object"

    if uninstall:
        if "hermes-chrome" in servers:
            servers.pop("hermes-chrome", None)
            _save_json(path, data)
            return f"removed hermes-chrome from {path}"
        return f"skip: hermes-chrome not in {path}"

    servers["hermes-chrome"] = mcp_entry(root, python)
    _save_json(path, data)
    return f"ok: {label} → {path}"


def claude_desktop_paths() -> list[Path]:
    home = Path.home()
    system = platform.system()
    paths: list[Path] = []
    if system == "Darwin":
        paths.append(
            home
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    elif system == "Windows":
        appdata = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        paths.append(home / ".config" / "Claude" / "claude_desktop_config.json")
    return paths


def install_all(root: Path, python: str, *, uninstall: bool = False) -> list[str]:
    mcp_py = root / "mcp_server.py"
    if not mcp_py.is_file() and not uninstall:
        die(f"missing {mcp_py} — set HERMES_CHROME_ROOT or pass --root")

    results: list[str] = []
    results.append(install_grok(root, python, uninstall=uninstall))

    # Cursor
    results.append(
        install_json_mcp(
            Path.home() / ".cursor" / "mcp.json",
            root,
            python,
            uninstall=uninstall,
            create_if_missing=Path.home().joinpath(".cursor").is_dir(),
            label="Cursor",
        )
    )

    # Claude Desktop
    for p in claude_desktop_paths():
        results.append(
            install_json_mcp(
                p,
                root,
                python,
                uninstall=uninstall,
                create_if_missing=p.parent.is_dir(),
                label="Claude Desktop",
            )
        )

    # VS Code user mcp (newer)
    vscode_candidates = [
        Path.home() / ".config" / "Code" / "User" / "mcp.json",
        Path.home() / "Library" / "Application Support" / "Code" / "User" / "mcp.json",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        vscode_candidates.append(Path(appdata) / "Code" / "User" / "mcp.json")
    for p in vscode_candidates:
        if p.parent.is_dir() or (uninstall and p.is_file()):
            results.append(
                install_json_mcp(
                    p,
                    root,
                    python,
                    uninstall=uninstall,
                    create_if_missing=False,
                    label="VS Code",
                )
            )

    if not uninstall:
        snip = write_snippet(root, python)
        results.append(f"ok: snippet → {snip}")

    return results


def status(root: Path) -> None:
    python = find_python()
    print(f"root:   {root}")
    print(f"python: {python}")
    print(f"mcp:    {root / 'mcp_server.py'} ({'yes' if (root / 'mcp_server.py').is_file() else 'missing'})")
    snip = run_dir() / "mcp-snippet.json"
    print(f"snippet:{snip} ({'yes' if snip.is_file() else 'missing'})")
    checks = [
        ("Grok", Path.home() / ".grok" / "config.toml"),
        ("Cursor", Path.home() / ".cursor" / "mcp.json"),
    ]
    for p in claude_desktop_paths():
        checks.append(("Claude Desktop", p))
    for label, path in checks:
        has = False
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            has = "hermes-chrome" in text or "mcp_server.py" in text
        print(f"{label:14} {path}  {'REGISTERED' if has else ('present' if path.is_file() else '—')}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Register Hermes Chrome MCP with local agents")
    ap.add_argument("--root", type=Path, default=None, help="HERMES_CHROME_ROOT")
    ap.add_argument("--python", default=None, help="Python executable for MCP command")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    root = (args.root or default_root()).expanduser().resolve()
    python = args.python or find_python()

    if args.status:
        status(root)
        return 0

    results = install_all(root, python, uninstall=args.uninstall)
    for line in results:
        log(line)
    if not args.uninstall:
        log("Restart agent sessions (Grok / Cursor / Claude) to load hermes_chrome_* tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

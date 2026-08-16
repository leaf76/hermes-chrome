#!/usr/bin/env python3
"""Companion self-update: git ff-only from the official GitHub remote.

Extension updates via Chrome Web Store. This only updates the machine half
(~/.hermes/hermes-chrome by default). Never runs curl|bash.

Safety:
  - Official origin only (github.com/leaf76/hermes-chrome)
  - Skip dirty worktrees
  - ff-only (no merge commits / no force push consumption)
  - HERMES_CHROME_AUTO_UPDATE=0 always disables
  - Default off until `enable` (install.sh turns it on for non --dev installs)

  python3 lib/self_update.py enable|disable|status|--maybe|--force
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_ROOT_GUESS = Path(__file__).resolve().parents[1]
if str(_ROOT_GUESS / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT_GUESS / "lib"))

from version_util import product_version  # noqa: E402

OFFICIAL_REMOTE = re.compile(
    r"(?:https://github\.com/|git@github\.com:)leaf76/hermes-chrome(?:\.git)?/?$",
    re.I,
)
FIXED_INSTALL = Path.home() / ".hermes" / "hermes-chrome"
FLAG_NAME = "auto-update.on"
STAMP_NAME = "self-update.json"
INTERVAL_S = 24 * 60 * 60
GIT_TIMEOUT = 120


def run_dir() -> Path:
    return Path(
        os.environ.get("HERMES_CHROME_RUN")
        or Path.home() / ".hermes" / "run" / "hermes-chrome"
    ).expanduser()


def install_root() -> Path:
    env = os.environ.get("HERMES_CHROME_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if (FIXED_INSTALL / "bridge.py").is_file():
        return FIXED_INSTALL.resolve()
    return _ROOT_GUESS.resolve()


def flag_path() -> Path:
    return run_dir() / FLAG_NAME


def stamp_path() -> Path:
    return run_dir() / STAMP_NAME


def env_disabled() -> bool:
    v = (os.environ.get("HERMES_CHROME_AUTO_UPDATE") or "").strip().lower()
    return v in {"0", "false", "no", "off"}


def env_forced() -> bool:
    v = (os.environ.get("HERMES_CHROME_AUTO_UPDATE") or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    if env_disabled():
        return False
    if env_forced():
        return True
    return flag_path().is_file()


def set_enabled(on: bool) -> dict[str, Any]:
    run_dir().mkdir(parents=True, exist_ok=True)
    p = flag_path()
    if on:
        p.write_text("1\n", encoding="utf-8")
    elif p.is_file():
        p.unlink()
    return status()


def _git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=check,
    )


def official_origin(url: str) -> bool:
    u = (url or "").strip()
    return bool(OFFICIAL_REMOTE.search(u))


def _write_stamp(payload: dict[str, Any]) -> None:
    run_dir().mkdir(parents=True, exist_ok=True)
    payload = {**payload, "at": int(time.time())}
    stamp_path().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def status(root: Path | None = None) -> dict[str, Any]:
    root = (root or install_root()).resolve()
    out: dict[str, Any] = {
        "ok": True,
        "enabled": is_enabled(),
        "env_disabled": env_disabled(),
        "root": str(root),
        "version": product_version(root),
        "flag": str(flag_path()),
    }
    if stamp_path().is_file():
        try:
            out["last"] = json.loads(stamp_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out["last"] = None
    git_dir = root / ".git"
    out["git"] = git_dir.exists()
    if git_dir.exists():
        r = _git(root, "remote", "get-url", "origin")
        out["origin"] = (r.stdout or "").strip()
        out["official_origin"] = official_origin(out["origin"])
    return out


def maybe_update(
    *,
    force: bool = False,
    restart: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    root = (root or install_root()).resolve()
    result: dict[str, Any] = {"ok": True, "updated": False, "root": str(root)}

    if not force and not is_enabled():
        result["skipped"] = "disabled"
        return result

    if not force and stamp_path().is_file():
        try:
            prev = json.loads(stamp_path().read_text(encoding="utf-8"))
            age = time.time() - float(prev.get("at") or 0)
            if age < INTERVAL_S:
                result["skipped"] = "recent"
                result["age_s"] = int(age)
                return result
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    if not (root / ".git").exists():
        result["ok"] = False
        result["error"] = "not a git checkout; re-run install.sh"
        _write_stamp(result)
        return result

    origin = _git(root, "remote", "get-url", "origin")
    url = (origin.stdout or "").strip()
    result["origin"] = url
    if not official_origin(url):
        result["ok"] = False
        result["error"] = "origin is not github.com/leaf76/hermes-chrome"
        _write_stamp(result)
        return result

    dirty = _git(root, "status", "--porcelain")
    if (dirty.stdout or "").strip():
        result["skipped"] = "dirty"
        _write_stamp(result)
        return result

    fetched = _git(root, "fetch", "--depth", "1", "origin", "main")
    if fetched.returncode != 0:
        result["ok"] = False
        result["error"] = (fetched.stderr or fetched.stdout or "git fetch failed")[:400]
        _write_stamp(result)
        return result

    head = (_git(root, "rev-parse", "HEAD").stdout or "").strip()
    remote = (_git(root, "rev-parse", "origin/main").stdout or "").strip()
    result["head"] = head[:12]
    result["origin_main"] = remote[:12]
    if head == remote:
        result["skipped"] = "current"
        _write_stamp(result)
        return result

    pulled = _git(root, "merge", "--ff-only", "origin/main")
    if pulled.returncode != 0:
        result["ok"] = False
        result["error"] = (pulled.stderr or "ff-only merge failed")[:400]
        _write_stamp(result)
        return result

    result["updated"] = True
    result["version"] = product_version(root)

    native = root / "scripts" / "install-native-host.sh"
    native_ps = root / "scripts" / "install-native-host.ps1"
    try:
        if os.name == "nt" and native_ps.is_file():
            subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(native_ps),
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
        elif native.is_file():
            subprocess.run(
                ["bash", str(native), "install"],
                capture_output=True,
                timeout=120,
                check=False,
            )
        result["native_host"] = "refreshed"
    except (OSError, subprocess.TimeoutExpired) as e:
        result["native_host"] = f"skip: {e}"

    _write_stamp(result)

    if restart:
        cli = root / "scripts" / "hermes-chrome.sh"
        try:
            if cli.is_file():
                subprocess.Popen(
                    ["bash", str(cli), "bridge-restart"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                result["restart"] = "scheduled"
        except OSError as e:
            result["restart"] = str(e)

    return result


def _print(obj: dict[str, Any]) -> int:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0 if obj.get("ok", True) else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    if cmd in ("enable", "on"):
        return _print(set_enabled(True))
    if cmd in ("disable", "off"):
        return _print(set_enabled(False))
    if cmd in ("status", "--status"):
        return _print(status())
    if cmd in ("--maybe", "maybe"):
        return _print(maybe_update(restart="--restart" in args))
    if cmd in ("--force", "now", "run"):
        return _print(maybe_update(force=True, restart="--restart" in args or True))
    print("usage: self_update.py enable|disable|status|--maybe|--force [--restart]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

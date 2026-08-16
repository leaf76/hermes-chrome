"""Product version helpers (stdlib). Source of truth: extension/manifest.json."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

GITHUB_LATEST = "https://api.github.com/repos/leaf76/hermes-chrome/releases/latest"
INSTALL_HINT = (
    "curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash"
)
CWS_URL = (
    "https://chromewebstore.google.com/detail/hermes-chrome/mkoaoadlkijccmmbkioagnlngbbeocfa"
)


def product_version(root: Path) -> str:
    manifest = root / "extension" / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        ver = str(data.get("version") or "").strip()
        if ver:
            return ver
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return "0.0.0"


def parse_semver(text: str | None) -> tuple[int, int, int]:
    if not text:
        return (0, 0, 0)
    nums = [int(x) for x in re.findall(r"\d+", str(text))[:3]]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def cmp_semver(a: str | None, b: str | None) -> int:
    pa, pb = parse_semver(a), parse_semver(b)
    return (pa > pb) - (pa < pb)


def mismatch(companion: str | None, extension: str | None) -> str | None:
    """Which half is behind, or None if aligned / unknown."""
    if not companion or not extension:
        return None
    c = cmp_semver(companion, extension)
    if c < 0:
        return "companion"
    if c > 0:
        return "extension"
    return None


def fetch_latest_tag(timeout_s: float = 2.5) -> str | None:
    req = Request(
        GITHUB_LATEST,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "hermes-chrome-doctor",
        },
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    tag = str(data.get("tag_name") or "").strip()
    return tag or None


def update_report(
    *,
    local: str,
    latest_tag: str | None,
    extension: str | None = None,
) -> dict[str, Any]:
    latest = (latest_tag or "").lstrip("v")
    behind_github = bool(latest) and cmp_semver(local, latest) < 0
    half = mismatch(local, extension)
    hints: list[str] = []
    if behind_github:
        hints.append(
            f"Companion {local} < GitHub {latest_tag}. Update: {INSTALL_HINT}"
        )
    if half == "companion":
        hints.append(
            f"Extension {extension} is newer than companion {local}. Re-run: {INSTALL_HINT}"
        )
    elif half == "extension":
        hints.append(
            f"Companion {local} is newer than extension {extension}. "
            f"Reload/update the extension: {CWS_URL}"
        )
    return {
        "local": local,
        "latest": latest_tag,
        "behind_github": behind_github,
        "mismatch": half,
        "hints": hints,
    }

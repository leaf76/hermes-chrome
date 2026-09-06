#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"
DIST="$ROOT/store/dist"
VER=$(python3 -c "import json;print(json.load(open('$EXT/manifest.json'))['version'])")
NAME="hermes-chrome-v${VER}"
OUT="$DIST/${NAME}.zip"
mkdir -p "$DIST"
rm -f "$OUT"
# zip only store-safe files (include offline Guide help.html — popup/Options link to it)
(
  cd "$EXT"
  zip -X -r "$OUT" \
    manifest.json \
    background.js \
    popup.html popup.js popup.css \
    sidepanel.html sidepanel.js sidepanel.css \
    options.html options.js \
    help.html help.js \
    icons/icon16.png icons/icon32.png icons/icon48.png icons/icon128.png
)
echo "Wrote $OUT"
unzip -l "$OUT"
python3 - <<PY
import json, zipfile
z=zipfile.ZipFile("$OUT")
names=set(z.namelist())
need=[
    "manifest.json",
    "background.js",
    "popup.html",
    "popup.js",
    "popup.css",
    "sidepanel.html",
    "sidepanel.js",
    "sidepanel.css",
    "options.html",
    "help.html",
    "help.js",
    "icons/icon128.png",
]
missing=[n for n in need if n not in names]
assert not missing, missing
m=json.loads(z.read("manifest.json"))
assert m["manifest_version"]==3
assert m.get("version"), "version required"
# Guide / setup UX must ship in CWS package
help_html=z.read("help.html").decode()
assert "two parts" in help_html.lower() or "You need two parts" in help_html or "需要兩半" in help_html
popup=z.read("popup.html").decode()
assert "setupPanel" in popup or "setupCard" in popup
assert "summaryCard" in popup
assert "Technical details" in popup
print("package_ok", m["name"], m["version"], "files", len(names))
print("description:", m.get("description", ""))
PY

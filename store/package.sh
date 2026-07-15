#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/extension"
DIST="$ROOT/store/dist"
VER=$(python3 -c "import json;print(json.load(open('$EXT/manifest.json'))['version'])")
NAME="hermes-agent-tabgroup-v${VER}"
OUT="$DIST/${NAME}.zip"
mkdir -p "$DIST"
rm -f "$OUT"
# zip only store-safe files
(
  cd "$EXT"
  zip -X -r "$OUT"     manifest.json     background.js     popup.html popup.js popup.css     options.html options.js     icons/icon16.png icons/icon32.png icons/icon48.png icons/icon128.png
)
echo "Wrote $OUT"
unzip -l "$OUT"
python3 - <<PY
import json, zipfile
z=zipfile.ZipFile("$OUT")
names=set(z.namelist())
need=["manifest.json","background.js","popup.html","options.html","icons/icon128.png"]
missing=[n for n in need if n not in names]
assert not missing, missing
m=json.loads(z.read("manifest.json"))
assert m["manifest_version"]==3
print("package_ok", m["name"], m["version"], "files", len(names))
PY

#!/usr/bin/env bash
# Build GitHub Release assets for hermes-chrome companion + extension.
#
# Usage (from repo root):
#   ./scripts/packaging/build-release-assets.sh
#   ./scripts/packaging/build-release-assets.sh --version 1.8.0
#   OUT_DIR=dist ./scripts/packaging/build-release-assets.sh
#
# Outputs under dist/release/ (or $OUT_DIR):
#   hermes-chrome-companion-<ver>.tar.gz
#   hermes-chrome-extension-v<ver>.zip
#   install.sh  (copy of scripts/install.sh for curl-stable URL on release)
#   checksums.txt
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  VERSION="$(python3 -c "import json; print(json.load(open('extension/manifest.json'))['version'])")"
fi
# strip leading v
VERSION="${VERSION#v}"

OUT_DIR="${OUT_DIR:-$ROOT/dist/release}"
STAGE="${OUT_DIR}/_stage"
NAME="hermes-chrome-companion-${VERSION}"
TARBALL="${OUT_DIR}/${NAME}.tar.gz"
EXT_ZIP_SRC=""
EXT_ZIP_DST="${OUT_DIR}/hermes-chrome-extension-v${VERSION}.zip"

echo "[release] version=${VERSION}"
echo "[release] out=${OUT_DIR}"

rm -rf "$STAGE" "$OUT_DIR"
mkdir -p "$STAGE/${NAME}" "$OUT_DIR"

# Companion tree (no git history, no store dist bloat, no caches)
rsync -a \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'store/dist/' \
  --exclude 'store/submission-pack/' \
  --exclude 'dist/' \
  --exclude 'npm/node_modules/' \
  --exclude 'scripts/packaging/_build/' \
  "${ROOT}/" "${STAGE}/${NAME}/"

# Mark version in release
printf '%s\n' "$VERSION" >"${STAGE}/${NAME}/VERSION"
printf '%s\n' "$VERSION" >"${STAGE}/${NAME}/extension/VERSION" 2>/dev/null || true

tar -C "$STAGE" -czf "$TARBALL" "$NAME"
echo "[release] wrote $TARBALL"

# Extension CWS zip
if [[ -x "$ROOT/store/package.sh" || -f "$ROOT/store/package.sh" ]]; then
  bash "$ROOT/store/package.sh"
  EXT_ZIP_SRC="$ROOT/store/dist/hermes-chrome-v${VERSION}.zip"
  if [[ ! -f "$EXT_ZIP_SRC" ]]; then
    # package.sh uses manifest version — should match
    EXT_ZIP_SRC="$(ls -1 "$ROOT/store/dist"/hermes-chrome-v*.zip 2>/dev/null | tail -1 || true)"
  fi
  if [[ -n "$EXT_ZIP_SRC" && -f "$EXT_ZIP_SRC" ]]; then
    cp "$EXT_ZIP_SRC" "$EXT_ZIP_DST"
    echo "[release] wrote $EXT_ZIP_DST"
  else
    echo "[release] warn: extension zip not found" >&2
  fi
fi

# Standalone installers for release assets (pinned URL convenience)
cp "$ROOT/scripts/install.sh" "$OUT_DIR/install.sh"
cp "$ROOT/scripts/install-windows.ps1" "$OUT_DIR/install-windows.ps1"
chmod +x "$OUT_DIR/install.sh"

# checksums
(
  cd "$OUT_DIR"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 ./* >checksums.txt 2>/dev/null || true
    # avoid hashing checksums itself if re-run — regenerate cleanly
    rm -f checksums.txt
    for f in *; do
      [[ "$f" == "checksums.txt" || "$f" == _* ]] && continue
      [[ -f "$f" ]] || continue
      shasum -a 256 "$f"
    done >checksums.txt
  elif command -v sha256sum >/dev/null 2>&1; then
    rm -f checksums.txt
    for f in *; do
      [[ "$f" == "checksums.txt" || "$f" == _* ]] && continue
      [[ -f "$f" ]] || continue
      sha256sum "$f"
    done >checksums.txt
  fi
)

rm -rf "$STAGE"
echo "[release] checksums:"
cat "${OUT_DIR}/checksums.txt" 2>/dev/null || true
echo "[release] done → $OUT_DIR"
ls -la "$OUT_DIR"

#!/usr/bin/env bash
# Build a macOS installer package for Hermes Chrome companion.
#
# Default: unsigned .pkg (Gatekeeper will warn; users can right-click → Open).
# Optional signing / notarization when Apple developer credentials are available:
#
#   export HERMES_CHROME_SIGN_IDENTITY="Developer ID Installer: Your Name (TEAMID)"
#   export HERMES_CHROME_NOTARY_PROFILE="notary-profile"  # xcrun notarytool store-credentials
#   ./scripts/packaging/build-macos-pkg.sh
#
# Payload strategy:
#   - Ships companion sources under /usr/local/lib/hermes-chrome
#   - postinstall copies into the console user's ~/.hermes/hermes-chrome
#     and runs install-for-agent as that user (bridge + native host + MCP)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

VERSION=""
SIGN_IDENTITY="${HERMES_CHROME_SIGN_IDENTITY:-}"
NOTARY_PROFILE="${HERMES_CHROME_NOTARY_PROFILE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --sign) SIGN_IDENTITY="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  VERSION="$(python3 -c "import json; print(json.load(open('extension/manifest.json'))['version'])")"
fi
VERSION="${VERSION#v}"

[[ "$(uname -s)" == "Darwin" ]] || { echo "error: macOS only" >&2; exit 1; }
command -v pkgbuild >/dev/null || { echo "error: pkgbuild missing" >&2; exit 1; }

BUILD="${ROOT}/scripts/packaging/_build/macos"
PKGROOT="${BUILD}/pkgroot"
SCRIPTS="${BUILD}/scripts"
OUT_DIR="${ROOT}/dist/pkg"
IDENTIFIER="com.leaf76.hermes-chrome.companion"
PKG_NAME="hermes-chrome-${VERSION}.pkg"
PKG_PATH="${OUT_DIR}/${PKG_NAME}"

rm -rf "$BUILD"
mkdir -p "${PKGROOT}/usr/local/lib/hermes-chrome" "$SCRIPTS" "$OUT_DIR"

echo "[pkg] staging companion → pkgroot"
rsync -a \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'store/dist/' \
  --exclude 'store/submission-pack/' \
  --exclude 'dist/' \
  --exclude 'npm/node_modules/' \
  --exclude 'scripts/packaging/_build/' \
  "${ROOT}/" "${PKGROOT}/usr/local/lib/hermes-chrome/"

printf '%s\n' "$VERSION" >"${PKGROOT}/usr/local/lib/hermes-chrome/VERSION"

# postinstall runs as root; re-target console user home
cat >"${SCRIPTS}/postinstall" <<'POST'
#!/bin/bash
set -euo pipefail
LOG="/var/log/hermes-chrome-pkg-install.log"
exec >>"$LOG" 2>&1
echo "=== hermes-chrome postinstall $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

SRC="/usr/local/lib/hermes-chrome"
CONSOLE_USER="$(stat -f%Su /dev/console 2>/dev/null || true)"
if [[ -z "$CONSOLE_USER" || "$CONSOLE_USER" == "root" || "$CONSOLE_USER" == "loginwindow" ]]; then
  # Fallback: most recent non-root console session is hard; use /Users/* with home
  CONSOLE_USER="$(ls -1 /Users 2>/dev/null | grep -v -E '^(Shared|Guest)$' | head -1 || true)"
fi
if [[ -z "$CONSOLE_USER" || "$CONSOLE_USER" == "root" ]]; then
  echo "warn: could not resolve console user; sources left at $SRC"
  exit 0
fi
USER_HOME="$(dscl . -read "/Users/${CONSOLE_USER}" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
if [[ -z "$USER_HOME" || ! -d "$USER_HOME" ]]; then
  USER_HOME="/Users/${CONSOLE_USER}"
fi
echo "user=$CONSOLE_USER home=$USER_HOME"

DEST="${USER_HOME}/.hermes/hermes-chrome"
RUN_DIR="${USER_HOME}/.hermes/run/hermes-chrome"
mkdir -p "$(dirname "$DEST")" "$RUN_DIR"

# Copy as the target user when possible
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '__pycache__/' \
    "${SRC}/" "${DEST}/"
else
  rm -rf "$DEST"
  mkdir -p "$DEST"
  cp -R "${SRC}/." "$DEST/"
fi
chown -R "${CONSOLE_USER}:staff" "${USER_HOME}/.hermes" 2>/dev/null || chown -R "${CONSOLE_USER}" "${USER_HOME}/.hermes" || true

# Run install-for-agent as the user (non-interactive)
sudo -u "$CONSOLE_USER" -H env \
  HOME="$USER_HOME" \
  HERMES_CHROME_ROOT="$DEST" \
  HERMES_CHROME_RUN="$RUN_DIR" \
  PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH" \
  bash "${DEST}/scripts/install-for-agent.sh" || {
    echo "warn: install-for-agent failed (user can re-run: ${DEST}/scripts/install.sh --dev)"
  }

# PATH shim for user
BIN_DIR="${USER_HOME}/.local/bin"
mkdir -p "$BIN_DIR"
ln -sfn "${DEST}/scripts/hermes-chrome.sh" "${BIN_DIR}/hermes-chrome"
chown -R "${CONSOLE_USER}:staff" "${USER_HOME}/.local" 2>/dev/null || true

echo "done: companion → $DEST"
exit 0
POST
chmod +x "${SCRIPTS}/postinstall"

echo "[pkg] pkgbuild → $PKG_PATH"
pkgbuild \
  --root "$PKGROOT" \
  --scripts "$SCRIPTS" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --install-location "/" \
  "${BUILD}/component.pkg"

# Wrap in product archive for nicer installer UI
DIST_XML="${BUILD}/distribution.xml"
cat >"$DIST_XML" <<EOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Hermes Chrome Companion</title>
  <organization>com.leaf76</organization>
  <domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true"/>
  <options customize="never" require-scripts="true" hostArchitectures="x86_64,arm64"/>
  <welcome file="welcome.html" mime-type="text/html"/>
  <conclusion file="conclusion.html" mime-type="text/html"/>
  <pkg-ref id="${IDENTIFIER}"/>
  <choices-outline>
    <line choice="default">
      <line choice="${IDENTIFIER}"/>
    </line>
  </choices-outline>
  <choice id="default"/>
  <choice id="${IDENTIFIER}" visible="false">
    <pkg-ref id="${IDENTIFIER}"/>
  </choice>
  <pkg-ref id="${IDENTIFIER}" version="${VERSION}" onConclusion="none">component.pkg</pkg-ref>
</installer-gui-script>
EOF

RESOURCES="${BUILD}/resources"
mkdir -p "$RESOURCES"
cat >"${RESOURCES}/welcome.html" <<EOF
<html><body style="font-family: -apple-system, sans-serif; font-size: 13px;">
<h2>Hermes Chrome Companion</h2>
<p>This installs the <b>machine half</b> (local bridge + Native Messaging host).</p>
<p>You still need the <b>Chrome extension</b> from the Chrome Web Store (or Load unpacked).</p>
<p><b>Not on npm</b> as the runtime. Optional MCP is included and registered when Grok/Cursor/Claude are present.</p>
<p>Requires <b>Python 3.9+</b> on PATH.</p>
<p>Version ${VERSION}</p>
</body></html>
EOF
cat >"${RESOURCES}/conclusion.html" <<EOF
<html><body style="font-family: -apple-system, sans-serif; font-size: 13px;">
<h2>Almost done</h2>
<ol>
<li>Install / reload <b>Hermes Chrome</b> extension in Chrome.</li>
<li>Click the extension icon → should show <b>Connected</b> (Pair if asked).</li>
<li>CLI: <code>~/.local/bin/hermes-chrome doctor</code></li>
</ol>
<p>If install failed silently, run:<br/>
<code>bash ~/.hermes/hermes-chrome/scripts/install.sh --dev</code></p>
</body></html>
EOF

UNSIGNED="${BUILD}/${PKG_NAME}"
productbuild \
  --distribution "$DIST_XML" \
  --resources "$RESOURCES" \
  --package-path "$BUILD" \
  "$UNSIGNED"

cp "$UNSIGNED" "$PKG_PATH"
echo "[pkg] built unsigned: $PKG_PATH"

if [[ -n "$SIGN_IDENTITY" ]]; then
  SIGNED="${OUT_DIR}/hermes-chrome-${VERSION}-signed.pkg"
  echo "[pkg] signing with: $SIGN_IDENTITY"
  productsign --sign "$SIGN_IDENTITY" "$PKG_PATH" "$SIGNED"
  mv "$SIGNED" "$PKG_PATH"
  echo "[pkg] signed: $PKG_PATH"
  if [[ -n "$NOTARY_PROFILE" ]]; then
    echo "[pkg] notarizing (profile=$NOTARY_PROFILE)…"
    xcrun notarytool submit "$PKG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$PKG_PATH"
    echo "[pkg] notarized + stapled"
  else
    echo "[pkg] skip notarization (set HERMES_CHROME_NOTARY_PROFILE)"
  fi
else
  echo "[pkg] unsigned (set HERMES_CHROME_SIGN_IDENTITY to sign)"
fi

ls -la "$PKG_PATH"
echo "[pkg] done"

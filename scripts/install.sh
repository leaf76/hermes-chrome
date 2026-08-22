#!/usr/bin/env bash
# Hermes Chrome — full companion installer (machine half).
#
# One-liner (recommended):
#   curl -fsSL https://raw.githubusercontent.com/leaf76/hermes-chrome/main/scripts/install.sh | bash
#
# From a local clone (copies/syncs into the fixed install root):
#   bash scripts/install.sh
#   bash scripts/install.sh --dev          # use this clone in-place (no copy)
#   bash scripts/install.sh --skip-mcp
#   bash scripts/install.sh --no-open      # do not auto-open the CWS page
#   bash scripts/install.sh --uninstall
#
# Installs to:
#   ~/.hermes/hermes-chrome/     code (bridge, MCP, scripts, extension/)
#   ~/.hermes/run/hermes-chrome/ runtime (token, pid, native host wrapper)
# PATH shim:
#   ~/.local/bin/hermes-chrome
#
# Not on npm. Optional MCP is mcp_server.py in the install root.
set -euo pipefail

REPO_URL="${HERMES_CHROME_REPO:-https://github.com/leaf76/hermes-chrome.git}"
REPO_RAW_BASE="${HERMES_CHROME_RAW:-https://raw.githubusercontent.com/leaf76/hermes-chrome/main}"
INSTALL_ROOT="${HERMES_CHROME_INSTALL:-$HOME/.hermes/hermes-chrome}"
RUN_DIR="${HERMES_CHROME_RUN:-$HOME/.hermes/run/hermes-chrome}"
BIN_DIR="${HERMES_CHROME_BIN:-$HOME/.local/bin}"
CWS_URL="https://chromewebstore.google.com/detail/hermes-chrome/mkoaoadlkijccmmbkioagnlngbbeocfa"

SKIP_MCP=0
UNINSTALL=0
DEV_MODE=0
NO_OPEN=0
FROM_SOURCE=""

die() { echo "error: $*" >&2; exit 1; }
log() { echo "[hermes-chrome] $*"; }

usage() {
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-mcp|--skip-grok) SKIP_MCP=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --dev) DEV_MODE=1; shift ;;
    --no-open) NO_OPEN=1; shift ;;
    --root)
      INSTALL_ROOT="${2:-}"; shift 2 || die "--root needs path"
      ;;
    --from)
      FROM_SOURCE="${2:-}"; shift 2 || die "--from needs path"
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown arg: $1 (try --help)" ;;
  esac
done

# Detect real Python 3 (reject WindowsApps stubs when under Git Bash)
find_python() {
  local c p
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      p="$(command -v "$c")"
      case "$p" in
        *WindowsApps*) continue ;;
      esac
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        echo "$p"
        return 0
      fi
    fi
  done
  return 1
}

uname_s="$(uname -s 2>/dev/null || echo unknown)"

# When piped from curl, BASH_SOURCE may be stdin — resolve carefully.
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_PATH" && -f "$SCRIPT_PATH" ]]; then
  THIS_SCRIPT="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)/$(basename "$SCRIPT_PATH")"
  LOCAL_SCRIPTS="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
  LOCAL_ROOT="$(cd "${LOCAL_SCRIPTS}/.." && pwd)"
else
  THIS_SCRIPT=""
  LOCAL_SCRIPTS=""
  LOCAL_ROOT=""
fi

if [[ -n "$FROM_SOURCE" ]]; then
  LOCAL_ROOT="$(cd "$FROM_SOURCE" && pwd)"
  LOCAL_SCRIPTS="${LOCAL_ROOT}/scripts"
fi

# Windows → hand off to PowerShell installer when available
if [[ "$uname_s" == MINGW* || "$uname_s" == MSYS* || "$uname_s" == CYGWIN* ]]; then
  if [[ -n "$LOCAL_ROOT" && -f "${LOCAL_ROOT}/scripts/install-windows.ps1" ]]; then
    log "Windows detected — install-windows.ps1"
    ps_args=("-ExecutionPolicy" "Bypass" "-File" "${LOCAL_ROOT}/scripts/install-windows.ps1")
    [[ "$SKIP_MCP" == "1" ]] && ps_args+=("-SkipGrok")
    [[ "$UNINSTALL" == "1" ]] && ps_args+=("-Uninstall")
    exec powershell.exe "${ps_args[@]}"
  fi
  die "On Windows, clone the repo and run: powershell -ExecutionPolicy Bypass -File .\\scripts\\install-windows.ps1"
fi

PYTHON3="$(find_python)" || die "Python 3.9+ required (real python on PATH). https://www.python.org/downloads/"
log "python: $PYTHON3"

ensure_source_tree() {
  mkdir -p "$(dirname "$INSTALL_ROOT")"
  if [[ "$DEV_MODE" == "1" ]]; then
    [[ -n "$LOCAL_ROOT" && -f "${LOCAL_ROOT}/bridge.py" ]] || die "--dev requires running from a clone"
    INSTALL_ROOT="$LOCAL_ROOT"
    log "dev mode: using clone in-place → $INSTALL_ROOT"
    return 0
  fi

  # Prefer syncing from local clone when this script lives in a full tree
  if [[ -n "$LOCAL_ROOT" && -f "${LOCAL_ROOT}/bridge.py" && -f "${LOCAL_ROOT}/mcp_server.py" ]]; then
    log "syncing from local clone: $LOCAL_ROOT → $INSTALL_ROOT"
    mkdir -p "$INSTALL_ROOT"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude 'store/dist/' \
        --exclude 'store/submission-pack/' \
        "${LOCAL_ROOT}/" "${INSTALL_ROOT}/"
    else
      # portable fallback
      rm -rf "${INSTALL_ROOT}.tmp"
      mkdir -p "${INSTALL_ROOT}.tmp"
      tar -C "$LOCAL_ROOT" \
        --exclude '__pycache__' --exclude 'store/dist' \
        -cf - . | tar -C "${INSTALL_ROOT}.tmp" -xf -
      rm -rf "$INSTALL_ROOT"
      mv "${INSTALL_ROOT}.tmp" "$INSTALL_ROOT"
    fi
    return 0
  fi

  # curl | bash path: clone or pull
  if [[ -d "${INSTALL_ROOT}/.git" ]]; then
    log "updating existing install: $INSTALL_ROOT"
    git -C "$INSTALL_ROOT" fetch --depth 1 origin main 2>/dev/null \
      || git -C "$INSTALL_ROOT" fetch --depth 1 origin master 2>/dev/null \
      || true
    git -C "$INSTALL_ROOT" reset --hard origin/main 2>/dev/null \
      || git -C "$INSTALL_ROOT" pull --ff-only 2>/dev/null \
      || log "git update skipped (offline?); using existing tree"
  else
    log "cloning $REPO_URL → $INSTALL_ROOT"
    rm -rf "$INSTALL_ROOT"
    git clone --depth 1 "$REPO_URL" "$INSTALL_ROOT" \
      || die "git clone failed — install git or clone manually"
  fi
  [[ -f "${INSTALL_ROOT}/bridge.py" ]] || die "clone missing bridge.py"
}

install_path_shim() {
  mkdir -p "$BIN_DIR"
  local target="${INSTALL_ROOT}/scripts/hermes-chrome.sh"
  [[ -f "$target" ]] || die "missing $target"
  chmod +x "$target" "${INSTALL_ROOT}/scripts/"*.sh 2>/dev/null || true
  chmod +x "${INSTALL_ROOT}/scripts/install-mcp.py" "${INSTALL_ROOT}/scripts/doctor.py" 2>/dev/null || true
  ln -sfn "$target" "${BIN_DIR}/hermes-chrome"
  log "PATH shim: ${BIN_DIR}/hermes-chrome → $target"
  case ":$PATH:" in
    *":${BIN_DIR}:"*) ;;
    *)
      local rc_file="$HOME/.zshrc"
      if [[ "${SHELL:-}" == *"bash"* ]] || [[ ! -f "$HOME/.zshrc" && -f "$HOME/.bashrc" ]]; then
        rc_file="$HOME/.bashrc"
      fi
      log "⚠️  Notice: ${BIN_DIR} is not in your current PATH."
      log "   To use 'hermes-chrome' directly in Terminal, run:"
      log "   echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $rc_file && source $rc_file"
      ;;
  esac
}

do_uninstall() {
  log "uninstall companion services (code under $INSTALL_ROOT kept unless you delete it)"
  export HERMES_CHROME_ROOT="$INSTALL_ROOT"
  export HERMES_CHROME_RUN="$RUN_DIR"
  if [[ -f "${INSTALL_ROOT}/scripts/install-launchd.sh" && "$uname_s" == "Darwin" ]]; then
    bash "${INSTALL_ROOT}/scripts/install-launchd.sh" uninstall || true
  fi
  if [[ -f "${INSTALL_ROOT}/scripts/hermes-chrome.sh" ]]; then
    bash "${INSTALL_ROOT}/scripts/hermes-chrome.sh" bridge-stop >/dev/null 2>&1 || true
  fi
  if [[ -f "${INSTALL_ROOT}/scripts/install-native-host.sh" ]]; then
    bash "${INSTALL_ROOT}/scripts/install-native-host.sh" uninstall || true
  fi
  if [[ -f "${INSTALL_ROOT}/scripts/install-mcp.py" ]]; then
    "$PYTHON3" "${INSTALL_ROOT}/scripts/install-mcp.py" --root "$INSTALL_ROOT" --uninstall || true
  fi
  rm -f "${BIN_DIR}/hermes-chrome"
  log "removed PATH shim"
  log "left in place: $INSTALL_ROOT and $RUN_DIR (delete manually if desired)"
  exit 0
}

# ---- main ----
if [[ "$UNINSTALL" == "1" ]]; then
  [[ -d "$INSTALL_ROOT" ]] || INSTALL_ROOT="${LOCAL_ROOT:-$INSTALL_ROOT}"
  do_uninstall
fi

ensure_source_tree
export HERMES_CHROME_ROOT="$INSTALL_ROOT"
export HERMES_CHROME_RUN="$RUN_DIR"
mkdir -p "$RUN_DIR"

# Persist root hint for other tools
printf '%s\n' "$INSTALL_ROOT" >"${RUN_DIR}/install-root.txt"

install_path_shim

log "running install-for-agent (bridge + native host)…"
agent_args=()
[[ "$SKIP_MCP" == "1" ]] && agent_args+=(--skip-mcp)
# Always skip old grok-only path; we run install-mcp.py after for multi-client
agent_args+=(--skip-mcp)
bash "${INSTALL_ROOT}/scripts/install-for-agent.sh" "${agent_args[@]}"

if [[ "$SKIP_MCP" != "1" ]]; then
  log "registering MCP (Grok / Cursor / Claude Desktop when present)…"
  "$PYTHON3" "${INSTALL_ROOT}/scripts/install-mcp.py" --root "$INSTALL_ROOT" --python "$PYTHON3" || log "MCP register partial"
fi

log "running doctor…"
"$PYTHON3" "${INSTALL_ROOT}/scripts/doctor.py" || true

if [[ "$DEV_MODE" != "1" ]]; then
  log "enabling companion auto-update (git ff-only from GitHub; disable: hermes-chrome self-update disable)"
  "$PYTHON3" "${INSTALL_ROOT}/lib/self_update.py" enable || true
fi

maybe_open_cws() {
  [[ "$NO_OPEN" == "1" ]] && return 0
  [[ -n "${CI:-}" ]] && return 0
  local opener=""
  case "$uname_s" in
    Darwin) opener="open" ;;
    Linux)
      if command -v xdg-open >/dev/null 2>&1; then opener="xdg-open"; fi
      ;;
  esac
  if [[ -n "$opener" ]]; then
    log "opening Chrome Web Store page… (--no-open to skip)"
    "$opener" "$CWS_URL" >/dev/null 2>&1 || true
  fi
}
maybe_open_cws

cat <<EOF

══════════════════════════════════════════════════════════
  Hermes Chrome companion installed (machine half)
══════════════════════════════════════════════════════════

  Install root:  ${INSTALL_ROOT}
  Runtime:       ${RUN_DIR}
  CLI:           ${BIN_DIR}/hermes-chrome
  MCP server:    ${INSTALL_ROOT}/mcp_server.py
  Snippet:       ${RUN_DIR}/mcp-snippet.json

  NOT on npm. MCP is optional and lives in this install root.

═══ Next: browser half ═══
  1. Install Hermes Chrome extension:
       Chrome Web Store: ${CWS_URL}
       or Load unpacked: ${INSTALL_ROOT}/extension
  2. Reload extension → click icon once → Pair if asked
  3. Ready when popup says Connected

═══ Smoke ═══
  hermes-chrome --json bridge-status
  hermes-chrome --json ping
  hermes-chrome doctor

═══ MCP ═══
  Restart Grok / Cursor / Claude Desktop after install.
  Or paste ${RUN_DIR}/mcp-snippet.json into your client.

Docs: ${INSTALL_ROOT}/docs/GUIDE.md
EOF

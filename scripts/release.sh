#!/usr/bin/env bash
# Hermes Chrome — one-command release helper.
#
# Usage:
#   scripts/release.sh v1.8.7                # bump → verify → package → commit + tag
#   scripts/release.sh v1.8.7 --push         # also push branch + tag (GHA builds release)
#   scripts/release.sh v1.8.7 --allow-dirty  # skip clean-worktree gate (not recommended)
#
# Steps:
#   1. Validate tag format (^v\d+\.\d+\.\d+$) and that it does not exist yet
#   2. Bump extension/manifest.json + npm/package.json (manifest is source of truth)
#   3. Run ./scripts/ci-check.sh (compileall, unit tests, security defaults, CWS zip)
#   4. Commit "release: ship vX.Y.Z" and create annotated-lightweight tag vX.Y.Z
#
# Pushing the tag triggers .github/workflows/release.yml.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG="${1:-}"
ALLOW_DIRTY=0
PUSH=0

while [[ $# -gt 1 ]]; do
  case "$2" in
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --push) PUSH=1 ;;
    *)
      echo "error: unknown arg: $2" >&2
      exit 1
      ;;
  esac
  shift
done

[[ -n "$TAG" ]] || {
  echo "usage: $0 vX.Y.Z [--push] [--allow-dirty]" >&2
  exit 1
}
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "error: tag must look like v1.2.3 (got: $TAG)" >&2
  exit 1
}
VERSION="${TAG#v}"

DIRTY="$(git status --porcelain | wc -l | tr -d ' ')"
if [[ "$DIRTY" != "0" && "$ALLOW_DIRTY" != "1" ]]; then
  echo "error: worktree is dirty; commit/stash first or pass --allow-dirty" >&2
  git status --porcelain >&2
  exit 1
fi

git fetch --tags >/dev/null 2>&1 || true
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists" >&2
  exit 1
fi

bump_version() {
  python3 - "$1" "$VERSION" <<'PY'
import json
import sys

path, version = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
data["version"] = version
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"[release] bumped {path} -> {version}")
PY
}

bump_version extension/manifest.json
bump_version npm/package.json

echo "[release] running ci-check (tests + packaging gates)…"
bash scripts/ci-check.sh

git add extension/manifest.json npm/package.json
git commit -m "release: ship ${TAG}"
git tag "$TAG"

echo
echo "[release] ${TAG} ready:"
git --no-pager log --oneline -1
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo
if [[ "$PUSH" == "1" ]]; then
  git push origin "$BRANCH" "$TAG"
  echo "[release] pushed ${BRANCH} + ${TAG} — GitHub Actions will build the release."
else
  echo "[release] next step (manual):"
  echo "  git push origin ${BRANCH} ${TAG}    # triggers GHA release build"
fi

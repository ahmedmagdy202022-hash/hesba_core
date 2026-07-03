#!/usr/bin/env bash
set -euo pipefail

ZIP_NAME="120B_DASHBOARD_HESBA_ASSETS_PACK_v2.zip"
BRANCH="feature/120b-dashboard-identity-fix"
TARGET_DIR="static/hesba/dashboard/assets"
TMP_DIR="/tmp/hesba_dashboard_assets_v2"

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "ERROR: Run this script from inside the hesba_core repository."
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

CURRENT_BRANCH="$(git branch --show-current)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  echo "ERROR: Current branch is '$CURRENT_BRANCH'. Expected '$BRANCH'."
  echo "Run: git checkout $BRANCH"
  exit 1
fi

ZIP_PATH=""
for candidate in \
  "$REPO_ROOT/$ZIP_NAME" \
  "$REPO_ROOT/inventory/$ZIP_NAME" \
  "$REPO_ROOT/static/$ZIP_NAME" \
  "$REPO_ROOT/docs/$ZIP_NAME"
do
  if [ -f "$candidate" ]; then
    ZIP_PATH="$candidate"
    break
  fi
done

if [ -z "$ZIP_PATH" ]; then
  echo "ERROR: Could not find $ZIP_NAME."
  echo "Place it in the repo root or inventory/ and run again."
  exit 1
fi

echo "Using assets zip: $ZIP_PATH"
rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"
unzip -q -o "$ZIP_PATH" -d "$TMP_DIR"

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"

if [ -d "$TMP_DIR/120B_DASHBOARD_HESBA_ASSETS_PACK_v2" ]; then
  cp -R "$TMP_DIR/120B_DASHBOARD_HESBA_ASSETS_PACK_v2/." "$TARGET_DIR/"
else
  cp -R "$TMP_DIR/." "$TARGET_DIR/"
fi

if ! find "$TARGET_DIR" -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' \) | grep -q .; then
  echo "ERROR: No PNG/JPG files were extracted into $TARGET_DIR."
  exit 1
fi

# Do not keep the zip in the repository working tree.
rm -f "$REPO_ROOT/$ZIP_NAME" "$REPO_ROOT/inventory/$ZIP_NAME"

echo "Extracted assets:"
find "$TARGET_DIR" -maxdepth 3 -type f | sort | head -120

echo "Running Django checks..."
python manage.py check
python manage.py test reports.tests

git add "$TARGET_DIR" templates/dashboard/dashboard_mock.html static/hesba/css/dashboard_mock.css docs/preview_links.md reports/tests.py scripts/install_dashboard_assets_v2.sh

echo "Git status after staging:"
git status --short

if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit -m "Integrate 120B dashboard assets pack v2"
  git push origin "$BRANCH"
  echo "Committed and pushed dashboard assets v2 integration."
fi

echo "Preview after runserver: /dashboard/?lang=ar and /dashboard/?lang=en"

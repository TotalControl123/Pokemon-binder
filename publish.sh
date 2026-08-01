#!/usr/bin/env bash
#
# Rebuild the binder and publish it to GitHub Pages.
#
#   ./publish.sh collectr-export.csv
#   ./publish.sh collectr-export.csv ~/code/pokemon-binder
#
# First-time setup is in README.md.

set -euo pipefail

CSV="${1:?usage: ./publish.sh <collectr-export.csv> [repo-dir]}"
REPO="${2:-./site}"
OWNER="${BINDER_OWNER:-}"

[ -f "$CSV" ] || { echo "no such file: $CSV" >&2; exit 1; }
[ -d "$REPO/.git" ] || { echo "$REPO is not a git repo - see README.md" >&2; exit 1; }

echo "==> building"
python3 build_binder.py "$CSV" -o "$REPO/index.html" ${OWNER:+--owner "$OWNER"} \
  ${BINDER_FORCE:+--force} || { echo "build failed - nothing published" >&2; exit 1; }

echo "==> publishing"
cd "$REPO"
git add index.html
if git diff --cached --quiet; then
  echo "no changes since last publish"
  exit 0
fi
git commit -q -m "binder update $(date +%Y-%m-%d)"
git push -q

REMOTE="$(git remote get-url origin)"
SLUG="$(echo "$REMOTE" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
USER="${SLUG%%/*}"
NAME="${SLUG##*/}"
echo
echo "published: https://${USER}.github.io/${NAME}/"
echo "(first deploy takes a minute or two to go live)"

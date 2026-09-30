#!/usr/bin/env bash
# Commit and push the pipeline-owned data files — the one list, shared by the
# daily refresh and the weekly review so the two can never drift apart again.
# (The review's own list once left out data/slug-registry.json; the tree stayed
# dirty and the next refresh refused to run.)
#
#   automation/commit_pipeline.sh "<commit message>" [author name] [author email]
#
# Exits 0 when there was nothing to commit. Pulls with rebase before pushing,
# so a commit a human pushed meanwhile does not fail the push.
set -euo pipefail

REPO_DIR="${BLD_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

MESSAGE="${1:?commit message required}"
AUTHOR_NAME="${2:-}"
AUTHOR_EMAIL="${3:-}"

PIPELINE_FILES=(
  data/events-published.json
  data/events
  data/venues.json
  data/sources.json
  data/known_duplicates.json
  data/link-check.json
  data/slug-registry.json
  data/facebook-signals.json
)

existing=()
for path in "${PIPELINE_FILES[@]}"; do
  [[ -e "$path" ]] && existing+=("$path")
done
git add -- "${existing[@]}"

if git diff --cached --quiet; then
  echo "no changes to publish"
  exit 0
fi

if [[ -n "$AUTHOR_NAME" ]]; then
  git -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" commit --quiet -m "$MESSAGE"
else
  git commit --quiet -m "$MESSAGE"
fi
git pull --rebase --quiet
git push --quiet
echo "pushed: $(git log --oneline -1)"

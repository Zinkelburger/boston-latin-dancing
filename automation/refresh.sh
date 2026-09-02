#!/usr/bin/env bash
# Daily deterministic refresh: scrape → ingest → archive → publish → push.
# Safe to run unattended — only certain-confidence merges happen; anything
# uncertain waits in the queues for the weekly agent run.
#
# git push is the deploy: the static-site host rebuilds on push.
#
# Cron usage (see automation/README.md):
#   flock -n /tmp/bld-refresh.lock /path/to/repo/automation/refresh.sh
set -euo pipefail

REPO_DIR="${BLD_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

log() { printf '[%s] %s\n' "$(date -Is)" "$*"; }

# Never fight a human or a broken previous run: bail if the tree is dirty.
if [[ -n "$(git status --porcelain)" ]]; then
  log "ERROR: working tree dirty — refusing to run. Resolve manually."
  exit 1
fi

git pull --rebase --quiet

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --quiet -r requirements.txt

# run_pipeline.py exits 2 on a tripwire (published files already restored
# from the pre-run snapshot) and 1 on a hard failure. Either way nothing below
# may run — a commit after a tripwire would push the restored files as if
# they were a fresh scrape. Name the reason in the log rather than relying on
# a bare `set -e` death.
set +e
.venv/bin/python scripts/run_pipeline.py
pipeline_rc=$?
set -e
if [[ "$pipeline_rc" -eq 2 ]]; then
  log "TRIPWIRE: live-event count collapsed; published files restored. Not committing."
  exit 2
elif [[ "$pipeline_rc" -ne 0 ]]; then
  log "ERROR: run_pipeline.py exited $pipeline_rc; not committing."
  exit "$pipeline_rc"
fi

# Link check reports, it does not gate. A link dying upstream is not a reason
# to hold back every other event's update — but it must never pass unnoticed,
# so the report is committed and the weekly agent run clears it.
if ! .venv/bin/python scripts/check_links.py --only-live --fail-on-broken; then
  log "WARNING: broken links published — see data/link-check.json"
fi

# The registry is written by publish(); it must ship with the data it
# describes, or the build has no alias pages for the URLs this run retired.
git add public/events.json data/events-published.json data/events/ \
        data/venues.json data/sources.json data/known_duplicates.json \
        data/link-check.json data/slug-registry.json
if git diff --cached --quiet; then
  log "no changes to publish"
  exit 0
fi

git -c user.name="BLD Pipeline" -c user.email="pipeline@bostonsalsa.org" \
  commit --quiet -m "Auto-refresh events $(date +%Y-%m-%d)"
git push --quiet
log "refresh pushed"

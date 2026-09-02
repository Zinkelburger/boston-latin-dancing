#!/usr/bin/env bash
# Weekly agent run (Tuesdays at noon): a headless Cursor agent clears the
# rejected/pending review queues, verifies events against sources, publishes,
# and pushes. Requires the Cursor CLI (`cursor-agent`) and CURSOR_API_KEY.
#
# Cron usage (see automation/README.md):
#   flock -n /tmp/bld-agent.lock /path/to/repo/automation/agent_review.sh
set -euo pipefail

REPO_DIR="${BLD_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODEL="${BLD_AGENT_MODEL:-composer}"
cd "$REPO_DIR"

LOG_DIR="$REPO_DIR/automation/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/agent-$(date +%Y%m%d-%H%M).log"

log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG_FILE"; }

if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  log "ERROR: CURSOR_API_KEY not set"
  exit 1
fi

# Fresh deterministic data first, so the agent reviews today's queues.
# Tolerate failure (e.g. tripwire) — investigating it is part of the
# agent's job; the prompt tells it to check pipeline state first.
log "running deterministic refresh"
"$REPO_DIR/automation/refresh.sh" >>"$LOG_FILE" 2>&1 \
  || log "WARNING: refresh failed or tripwired — agent will investigate"

log "starting agent (model: $MODEL)"
# --force auto-approves every command the agent proposes, and this shell holds
# push credentials: the only guardrails are the ones written in agent_prompt.md
# (see automation/README.md, "Security"). Under `set -e` a non-zero agent exit
# would kill the script right here, before the finish line and log pruning —
# so capture the status instead of letting it propagate.
STATUS=0
cursor-agent --print --force --model "$MODEL" --output-format text \
  "$(cat "$REPO_DIR/automation/agent_prompt.md")" >>"$LOG_FILE" 2>&1 || STATUS=$?

log "agent finished with exit code $STATUS"

# Prune logs older than 90 days.
find "$LOG_DIR" -name 'agent-*.log' -mtime +90 -delete 2>/dev/null || true

exit "$STATUS"

#!/usr/bin/env bash
# Weekly review: deterministic refresh, then a headless Claude Code agent that
# works through automation/agent_prompt.md (review queues, verify, publish,
# push, write the summary).
#
# Started by the bld-review.service systemd user unit (see desktop/README.md),
# or by hand. Every line of the run goes to one JSON-lines file,
# automation/logs/review-<timestamp>.jsonl, which the desktop tray app tails:
#   {"type":"bld", ...}  lines this script writes (phase changes, refresh output)
#   anything else        Claude's own --output-format stream-json events
#
# Settings come from the environment (the service loads
# ~/.config/bld-review/env, which the tray app's Settings tab writes):
#   BLD_AGENT_MODEL        model id (default claude-opus-5-5)
#   BLD_SKIP_REFRESH=1     skip refresh.sh and go straight to the agent
set -euo pipefail

REPO_DIR="${BLD_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODEL="${BLD_AGENT_MODEL:-claude-opus-5-5}"
cd "$REPO_DIR"

LOG_DIR="$REPO_DIR/automation/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/review-$(date +%Y%m%d-%H%M%S).jsonl"
ln -sfn "$(basename "$RUN_LOG")" "$LOG_DIR/review-latest.jsonl"

emit() { # emit <kind> <text>
  python3 -c 'import json,sys,datetime; print(json.dumps({"type":"bld","kind":sys.argv[1],"text":sys.argv[2],"ts":datetime.datetime.now().astimezone().isoformat(timespec="seconds")}))' \
    "$1" "$2" >>"$RUN_LOG"
}

emit phase "start"
emit info "model: $MODEL"

if [[ "${BLD_SKIP_REFRESH:-0}" != "1" ]]; then
  emit phase "refresh"
  # Tolerate failure (dirty tree, tripwire): the agent's first task is to
  # check pipeline state and investigate.
  REFRESH_RC=0
  "$REPO_DIR/automation/refresh.sh" 2>&1 | while IFS= read -r line; do
    emit refresh "$line"
  done || REFRESH_RC=$?
  if [[ "$REFRESH_RC" -ne 0 ]]; then
    emit warning "refresh failed or tripwired (exit $REFRESH_RC); the agent will investigate"
  fi
fi

emit phase "agent"
# Unattended: permission checks are bypassed and this user can push to main.
# The guardrails are the hard rules in agent_prompt.md (see desktop/README.md).
STATUS=0
claude -p "$(cat "$REPO_DIR/automation/agent_prompt.md")" \
  --model "$MODEL" \
  --mcp-config "$REPO_DIR/automation/claude-mcp.json" --strict-mcp-config \
  --permission-mode bypassPermissions \
  --output-format stream-json --verbose \
  >>"$RUN_LOG" 2>&1 || STATUS=$?

emit phase "done"
emit exit "$STATUS"

# Prune run logs older than 90 days.
find "$LOG_DIR" -name 'review-2*.jsonl' -mtime +90 -delete 2>/dev/null || true

exit "$STATUS"

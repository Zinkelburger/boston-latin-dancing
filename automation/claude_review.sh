#!/usr/bin/env bash
# Weekly review. Everything that needs no judgment is a script; the agent only
# answers the questions scripts/weekly_review.py writes.
#
#   refresh  automation/refresh.sh --review: scrape → ingest (quarantine) → archive
#   prepare  weekly_review.py prepare: verify, link check, cross-check,
#            deterministic fixes, then the worklist of questions
#   agent    headless Claude with ONLY the review tools (review_next /
#            review_answer / review_skip / review_link_check / event_get, plus
#            web search to find links). No shell, no file edits, no git.
#   recheck  weekly_review.py recheck: verify what the answers left and turn
#            anything finish would block on into follow-up questions; if there
#            are any (exit 3), the agent gets one more pass to answer them
#   finish   weekly_review.py finish: publish (tripwire-guarded), link check,
#            doctor, summary; then commit_pipeline.sh commits and pushes
#
# Started by the bld-review.service systemd user unit (see desktop/README.md),
# or by hand. Every line of the run goes to one JSON-lines file,
# automation/logs/review-<timestamp>.jsonl, which the desktop tray app tails:
#   {"type":"bld", ...}  lines this script writes (phase changes, step output)
#   anything else        Claude's own --output-format stream-json events
#
# Settings come from the environment (the service loads
# ~/.config/bld-review/env, which the tray app's Settings tab writes):
#   BLD_AGENT_MODEL        model id (default claude-opus-5-5)
#   BLD_SKIP_REFRESH=1     skip refresh.sh and go straight to prepare
set -euo pipefail

REPO_DIR="${BLD_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MODEL="${BLD_AGENT_MODEL:-claude-opus-5-5}"
cd "$REPO_DIR"

LOG_DIR="$REPO_DIR/automation/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/review-$(date +%Y%m%d-%H%M%S).jsonl"
ln -sfn "$(basename "$RUN_LOG")" "$LOG_DIR/review-latest.jsonl"
PY="$REPO_DIR/.venv/bin/python"

emit() { # emit <kind> <text>
  python3 -c 'import json,sys,datetime; print(json.dumps({"type":"bld","kind":sys.argv[1],"text":sys.argv[2],"ts":datetime.datetime.now().astimezone().isoformat(timespec="seconds")}))' \
    "$1" "$2" >>"$RUN_LOG"
}

step() { # step <kind> <command...>: run, streaming output into the log; returns its exit code
  local kind="$1"; shift
  local rc=0
  "$@" 2>&1 | while IFS= read -r line; do emit "$kind" "$line"; done || rc=$?
  return "$rc"
}

emit phase "start"
emit info "model: $MODEL"

# A failed/dirty refresh is not permission to edit or deploy the existing tree.
# Check even with BLD_SKIP_REFRESH, which only skips fetching sources.
if [[ -n "$(git status --porcelain)" ]]; then
  emit warning "working tree dirty; resolve or commit the existing changes before reviewing"
  emit phase "done"; emit exit 1; exit 1
fi

if [[ "${BLD_SKIP_REFRESH:-0}" != "1" ]]; then
  emit phase "refresh"
  if ! step refresh "$REPO_DIR/automation/refresh.sh" --review; then
    emit warning "refresh failed or tripwired; stopping before review, publish or commit"
    emit phase "done"; emit exit 1; exit 1
  fi
fi

emit phase "prepare"
if ! step prepare "$PY" scripts/weekly_review.py prepare; then
  emit warning "prepare failed; nothing will be published or committed"
  emit phase "done"; emit exit 1; exit 1
fi

run_agent() { # unattended and permission-free, so the guardrail is the tool list:
  # the MCP server runs with BLD_MCP_PROFILE=review (see claude-mcp.json) and
  # the only built-ins are web search and fetch, for finding an organizer's page.
  claude -p "$(cat "$REPO_DIR/automation/agent_prompt.md")" \
    --model "$MODEL" \
    --mcp-config "$REPO_DIR/automation/claude-mcp.json" --strict-mcp-config \
    --permission-mode bypassPermissions \
    --tools "WebSearch,WebFetch" \
    --output-format stream-json --verbose \
    </dev/null >>"$RUN_LOG" 2>&1
}

emit phase "agent"
STATUS=0
if "$PY" -c 'import sys; sys.path.insert(0,"scripts"); import weekly_review as w; sys.exit(0 if w.next_item().get("done") else 1)'; then
  emit info "no questions this week; skipping the agent"
else
  run_agent || STATUS=$?
fi

# Answers change the map. Whatever finish would block on becomes a follow-up
# question now, answered in one more pass, instead of an unpublished week.
if [[ "$STATUS" -eq 0 ]]; then
  emit phase "recheck"
  RECHECK=0
  step recheck "$PY" scripts/weekly_review.py recheck || RECHECK=$?
  if [[ "$RECHECK" -eq 3 ]]; then
    emit phase "agent"
    emit info "follow-up questions from the recheck; one more pass"
    run_agent || STATUS=$?
  elif [[ "$RECHECK" -ne 0 ]]; then
    emit warning "recheck failed (exit $RECHECK); finish will still gate publishing"
  fi
fi
step info "$PY" scripts/weekly_review.py status || true

if [[ "$STATUS" -ne 0 ]]; then
  emit warning "agent failed (exit $STATUS); review state is preserved, nothing further published or committed"
  emit phase "done"; emit exit "$STATUS"; exit "$STATUS"
fi

emit phase "finish"
FINISH=0
step finish "$PY" scripts/weekly_review.py finish || FINISH=$?
if [[ "$FINISH" -eq 0 ]]; then
  if ! step finish automation/commit_pipeline.sh "Weekly agent review $(date +%Y-%m-%d)"; then
    emit warning "commit or push failed; the data is published locally but not deployed"
    STATUS=1
  fi
elif [[ "$FINISH" -eq 2 ]]; then
  emit warning "TRIPWIRE: live events collapsed; nothing committed. See last-agent-summary.md"
  STATUS=2
else
  emit warning "finish failed (exit $FINISH); nothing committed"
  STATUS=1
fi

# refresh.sh refuses to run on a dirty tree, so anything left modified would
# silently skip next week's refresh. Say so where the tray shows it.
DIRTY="$(git status --porcelain --untracked-files=no)"
if [[ -n "$DIRTY" ]]; then
  emit warning "working tree left dirty; next refresh will refuse to run: $(echo "$DIRTY" | tr '\n' ' ')"
fi

emit phase "done"
emit exit "$STATUS"

# Prune run logs older than 90 days.
find "$LOG_DIR" -name 'review-2*.jsonl' -mtime +90 -delete 2>/dev/null || true

exit "$STATUS"

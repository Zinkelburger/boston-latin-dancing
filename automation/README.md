# Automation

One cron job on the VPS (the same box that runs the submissions API):
**`agent_review.sh`, Tuesdays at 12:00 PM Boston time.** It:

1. Runs `refresh.sh` — deterministic scrape → ingest → archive → publish →
   commit → push. Existing events get refreshed (certain-confidence merges
   only) and past events archived, but **brand-new events are quarantined
   into the pending queue** — nothing unreviewed ever reaches the map.
2. Launches a headless Cursor agent (Composer) that reviews the quarantined
   new events and dedup pairs, clears the rejected queue, verifies events
   against sources, publishes, and pushes.

`git push` **is** the deploy — the static host rebuilds the site on push. If a
push produces a broken build, the host keeps serving the previous deploy;
check the host dashboard.

`refresh.sh` is safe to run standalone any time (e.g. as an optional daily
cron to archive past events between Tuesdays) — quarantine means it can never
put junk on the map.

## One-time VPS setup

```bash
# 1. Clone (use SSH so cron can push without prompting)
git clone git@github.com:Zinkelburger/boston-latin-dancing.git /opt/bld/site
cd /opt/bld/site

# Add a deploy key with write access to the repo:
#   ssh-keygen -t ed25519 -C "bld-pipeline" -f ~/.ssh/bld_deploy
#   → paste ~/.ssh/bld_deploy.pub into GitHub → repo Settings → Deploy keys
#     (check "Allow write access")
# and in ~/.ssh/config point github.com at that key.

# 2. Python (needs python3-venv: apt install python3-venv)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3. Repo .env — needed by fetch_submissions.py
printf 'BLD_ADMIN_TOKEN=<token>\n' > .env

# 4. Cursor CLI for the weekly agent
curl https://cursor.com/install -fsSL | bash
# Verify the flags used in agent_review.sh still exist — the CLI moves fast:
cursor-agent --help
# Get an API key from cursor.com/dashboard → make it available to cron
# (e.g. in /etc/environment or the crontab itself):
#   CURSOR_API_KEY=...

# 5. Smoke-test by hand before trusting cron
automation/refresh.sh
automation/agent_review.sh   # watch automation/logs/agent-*.log
```

Note: `.cursor/mcp.json` must not hard-code a `cwd` that only exists on the
dev machine, or the MCP server silently fails to start on the VPS and the
agent runs without its tools.

## Crontab

`crontab -e` for the user that owns the clone:

```cron
CRON_TZ=America/New_York
CURSOR_API_KEY=<key>
# Tuesday noon: refresh + agent review (the weekly update)
0 12 * * 2 flock -n /tmp/bld-agent.lock /opt/bld/site/automation/agent_review.sh >> /opt/bld/site/automation/logs/cron-agent.log 2>&1
# Optional: daily deterministic refresh (archives past events between
# Tuesdays; quarantine means it can never add unreviewed events)
#15 11 * * * flock -n /tmp/bld-refresh.lock /opt/bld/site/automation/refresh.sh >> /opt/bld/site/automation/logs/refresh.log 2>&1
```

If your cron daemon doesn't support `CRON_TZ` (cronie does, some don't),
either set the whole server to `America/New_York` or shift the hours to the
UTC equivalents.

## Behavior notes

- **Dirty tree** → `refresh.sh` refuses to run. It never stomps on
  in-progress manual work; fix the tree by hand.
- **Tripwire** → if the published live-event count drops below 70% of the
  previous run, `run_pipeline.py` restores the previous published files and
  exits 2, so nothing is committed. A failed scrape can't blank the site.
- **Scraper failures** are per-source and non-fatal; they're listed under
  `scrapers_failed` in the summary JSON in `refresh.log`.
- **Agent guardrails** live in `agent_prompt.md`: MCP tools only, no force
  push, stop-and-report when unsure. Each run logs to
  `automation/logs/agent-<timestamp>.log` and writes
  `automation/logs/last-agent-summary.md`.
- Logs older than 90 days are pruned automatically.

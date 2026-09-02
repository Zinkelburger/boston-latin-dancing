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

## Security: the agent runs with `--force` while holding push credentials

Read this before enabling the Tuesday cron.

- `agent_review.sh` launches `cursor-agent --force`. `--force` auto-approves
  every shell command the agent proposes; nothing prompts, nothing is
  sandboxed. The agent runs as the same user that owns the clone, in the
  same shell environment, which means it can run anything that user can.
- That same user holds the deploy key with **write access** (step 1 of the
  setup above) so that `refresh.sh` can push. The agent therefore has push
  access to `main` for the whole of its run.
- The **only** guardrails are the instructions in `agent_prompt.md` ("MCP
  tools only", "no force push", "stop and report when unsure"). They are
  prompt text. A confused or prompt-injected agent — a scraped event
  description is untrusted input that ends up in the agent's context — is
  not technically prevented from running `git push --force`, deleting data,
  or exfiltrating the `.env` token.

Recommended hardening (not done here; it changes the key setup on the VPS):

1. Give the clone a **read-only** deploy key. Neither the agent nor the
   pipeline needs write access to do its job.
2. Move the push into a **separate step** that runs after the agent has
   exited — a small script invoked by cron (or by `agent_review.sh` after
   `cursor-agent` returns) that uses a write key held somewhere the agent's
   shell cannot read (a different user, an ssh-agent socket that is not
   exported to the agent, or a GitHub App token minted for that step only).
   The agent then produces a commit; a process it cannot influence decides
   whether to publish it.
3. Until then, treat `automation/logs/agent-*.log` as an audit trail and
   review it after each run.

## Behavior notes

- **Dirty tree** → `refresh.sh` refuses to run. It never stomps on
  in-progress manual work; fix the tree by hand.
- **Tripwire** → if the published live-event count drops below 70% of the
  previous run, `run_pipeline.py` restores the previous published files and
  exits 2, so nothing is committed. A failed scrape can't blank the site.
- **Scraper failures** are per-source and non-fatal; they're listed under
  `scrapers_failed` in the summary JSON in `refresh.log`. A failed scraper
  exits 1, records `fetch_error` in `data/scraper-health.json`, and leaves
  its previous `data/scraped/<id>.json` in place — a stale file beats an
  empty one. Which scrapers run comes from `data/sources.json` alone
  (`enabled: true` plus a `scraper` field); there is no second list.
- **Agent exit status** is captured (`|| STATUS=$?`) so the finish line is
  always logged and old logs are always pruned, even when the agent fails.
- **Agent guardrails** live in `agent_prompt.md`: MCP tools only, no force
  push, stop-and-report when unsure. Each run logs to
  `automation/logs/agent-<timestamp>.log` and writes
  `automation/logs/last-agent-summary.md`.
- Logs older than 90 days are pruned automatically.

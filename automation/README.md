# Automation

`refresh.sh` is the deterministic scrape → ingest → archive → publish →
commit → push. Existing events get refreshed (certain-confidence merges
only) and past events archived, but **brand-new events are quarantined
into the pending queue** — nothing unreviewed ever reaches the map.

The weekly review (`claude_review.sh`, a systemd user timer on the desktop,
Wednesdays at noon — see `desktop/README.md`) is built so the agent only makes
judgment calls, and every one is a multiple-choice question:

1. `refresh.sh --review` — scrape, ingest and archive without publishing or
   committing. The existing map stays intact until the review passes its checks.
   A dirty checkout or failed refresh stops the run before the agent starts.
2. `scripts/weekly_review.py prepare` — verify every event against its
   source, check every link, cross-check each event's sources, and apply the
   fixes with one right answer (drop past queue rows, archive an event whose
   own page says EventCancelled, swap a dead link for an event-matching live alternate, shed
   dead alternates). It then writes `automation/logs/worklist.json`: one
   question per judgment call, with its evidence and allowed answers.
3. The agent (`agent_prompt.md`) loops `review_next` → `review_answer` using
   the MCP server's review profile (`BLD_MCP_PROFILE=review`, five tools)
   plus web search. It has no shell, file or git access. Every answer is
   validated before it changes anything. A URL must pass
   `scripts/link_guard.py`: the page loads and names the event itself; sharing
   a venue is insufficient. Structured event names and dates must belong to the
   same record. A dated series link must match a recorded occurrence, not merely
   the same weekday. Pages without a machine-readable date are reported as such.
   An address must geocode inside greater
   Boston. An invalid answer is refused with the reason.
4. `scripts/weekly_review.py finish` — verify the final active events, check
   links and run the doctor **before** the tripwire-guarded publish. Unanswered
   questions, doctor blockers or broken links stop publication with exit 1;
   the tripwire returns 2. An agent process failure also stops deployment.
   `automation/logs/last-agent-summary.md` records the decisions and blockers.
5. `commit_pipeline.sh` commits the pipeline-owned files (the one list, shared
   with `refresh.sh`) and pushes.

By hand: run the three `weekly_review.py` steps yourself, and answer with
`python3 -c 'import sys; sys.path.insert(0,"scripts"); import weekly_review as w; print(w.next_item())'`
or from any MCP client. Without `BLD_MCP_PROFILE` the server exposes every
tool.

`git push` **is** the deploy — the static host rebuilds the site on push. If a
push produces a broken build, the host keeps serving the previous deploy;
check the host dashboard.

A blocked review preserves its local changes for inspection and leaves the
published files untouched. It may leave a dirty tree, which intentionally blocks
the next scheduled run. Resolve the reported issues through the event-store or
MCP APIs, resume the saved worklist with `review_next`, and run
`python3 scripts/weekly_review.py finish` again. Commit/deploy only after it
succeeds; do not delete queued records or commit unrelated edits to clear the gate.

`refresh.sh` is safe to run standalone any time — quarantine means it can
never put junk on the map.

Facebook pages are part of the deterministic refresh: `scrape_facebook.py`
renders each page with headless Chrome (`scripts/fetch_facebook.py`), reads
the Events tab into the evidence envelope, and reads the newest post, album
titles and OCR'd flyer text into `data/facebook-signals.json`. Dates stated
there without a matching event show up as `facebook_signals` warnings in
`npm run doctor`. The weekly review turns each one into a question: add the
event at the organizer's venue, or dismiss the date for good
(`data/events/facebook-signal-dismissals.json`).

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

# 3b. Chrome for the Facebook capture (scripts/fetch_facebook.py). Any of
#     google-chrome / chromium / chromium-browser on PATH works, or point
#     BLD_CHROME at the binary. Without it the Facebook scrapers only
#     normalize whatever envelope is already in data/scraped/ and the doctor
#     flags the evidence stale after 14 days.
apt install chromium        # Debian/Ubuntu; ~1 min per weekly run for 6 pages
python3 scripts/fetch_facebook.py --all --dry-run   # smoke test: prints each envelope

# 4. Smoke-test by hand before trusting cron
automation/refresh.sh
```

## Crontab

`crontab -e` for the user that owns the clone:

```cron
CRON_TZ=America/New_York
# Optional: daily deterministic refresh (archives past events; quarantine
# means it can never add unreviewed events)
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
  `scrapers_failed` in the summary JSON in `refresh.log`. A failed scraper
  exits 1, records `fetch_error` in `data/scraper-health.json`, and leaves
  its previous `data/scraped/<id>.json` in place — a stale file beats an
  empty one. Which scrapers run comes from `data/sources.json` alone
  (`enabled: true` plus a `scraper` field); there is no second list.
- Logs older than 90 days are pruned automatically.

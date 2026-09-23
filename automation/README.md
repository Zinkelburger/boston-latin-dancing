# Automation

`refresh.sh` is the deterministic scrape → ingest → archive → publish →
commit → push. Existing events get refreshed (certain-confidence merges
only) and past events archived, but **brand-new events are quarantined
into the pending queue** — nothing unreviewed ever reaches the map.

The weekly review of those queues (new events, dedup pairs, the rejected
queue, verification against sources) follows `agent_prompt.md`. On the desktop
it runs as a systemd user timer, Wednesdays at noon, via `claude_review.sh`,
with a tray app to watch it — see `desktop/README.md`. It can also be run by
hand from Claude Code.

`git push` **is** the deploy — the static host rebuilds the site on push. If a
push produces a broken build, the host keeps serving the previous deploy;
check the host dashboard.

`refresh.sh` is safe to run standalone any time — quarantine means it can
never put junk on the map.

Facebook pages are part of the deterministic refresh: `scrape_facebook.py`
renders each page with headless Chrome (`scripts/fetch_facebook.py`), reads
the Events tab into the evidence envelope, and reads the newest post, album
titles and OCR'd flyer text into `data/facebook-signals.json`. Dates stated
there without a matching event show up as `facebook_signals` warnings in
`npm run doctor`, which the weekly review works through (see
`agent_prompt.md`, step 1).

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

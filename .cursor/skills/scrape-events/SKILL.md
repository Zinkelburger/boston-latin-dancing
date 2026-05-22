---
name: scrape-events
description: >-
  Scrape dance events from all sources and update the map. Use when asked to
  refresh events, scrape Facebook pages (BOBAS, Dante's, etc.), run the pipeline,
  check for new events, update public/events.json, or "update the map".
---

# Update the Map

Use the **boston-latin-dance MCP tools** for all event operations. Never manually
edit `public/events.json` — it's a build artifact.

## Step 0: Check prerequisites

```bash
pip install -r requirements.txt --quiet
test -f .env && grep -q BLD_ADMIN_TOKEN .env && echo "OK" || echo "MISSING"
```

If `.env` is missing or has no `BLD_ADMIN_TOKEN`, ask the user for the token.

## Step 1: Run automated scrapers + ingest

Call the MCP tool:

```
event_scrape()
```

This runs all automated scrapers (ICS, Lister, Eventbrite, submissions),
ingests new events into `data/events/active.json` (with dedup), and auto-archives
past events.

To scrape a single source:

```
event_scrape(source_id="beatrice-calendar")
```

Available source_ids: `beatrice-calendar`, `lister-events`, `eventbrite-boston-latin`, `submissions`

## Step 2: Run Facebook scrapers via browser

For each Facebook source in `data/sources.json` with `"type": "facebook"`:

### 2a. Navigate to the Facebook events page

Source URLs are in `data/sources.json` under `facebook_events_url`. Current sources:

| Source ID | URL |
|-----------|-----|
| bobas | `https://www.facebook.com/profile.php?id=61551665503735&sk=events` |
| dantes-salsa | `https://www.facebook.com/DantesSalsaInferno/events` |

Use `browser_navigate` to open the URL.

### 2b. Close the login dialog

Facebook shows a login popup. Use `browser_snapshot`, find the "Close" button,
and click it. It may reappear -- close it again.

### 2c. Check for Upcoming events

Look at the snapshot for tab elements:
- If an **"Upcoming"** tab exists and is NOT selected, click it
- If only a **"Past"** tab exists, there are no upcoming events -- write `[]`
- If "Upcoming" is already selected, events below are the upcoming ones

### 2d. Extract event details

For each visible event:

1. Note the event name from the link text
2. Click the event link to open its detail page
3. Close any login dialog that appears
4. Extract from the snapshot:
   - **Date/time** (e.g. "Friday, May 22, 2026 at 8:30 PM -- 1:00 AM EDT")
   - **Name** (heading)
   - **Location** (venue button/link)
   - **URL** (page URL, e.g. `https://www.facebook.com/events/1234567`)
5. Click "See more" if visible to get the full description

### 2e. Write raw JSON and run the Facebook scraper

Write extracted events to `data/scraped/<source-id>-raw.json`:

```json
[
  {
    "name": "Sunset Salsa Bachata on the Docks",
    "date": "May 26, 2026",
    "time": "6:00 PM",
    "end_time": "9:00 PM",
    "location": "Hatch Shell on the Esplanade",
    "url": "https://www.facebook.com/events/123456",
    "description": "Free outdoor social..."
  }
]
```

Then run:

```bash
python3 scripts/scrape_facebook.py <source-id> --from-file data/scraped/<source-id>-raw.json
```

After the scraper writes `data/scraped/<source-id>.json`, ingest it:

```
event_ingest(source_id="<source-id>")
```

## Step 3: Publish

Call the MCP tool:

```
event_publish()
```

This regenerates `public/events.json` from the active event store + expanded venues.

## Step 4: Review flagged events

Check active events for issues:

```
event_list(status="active")
```

For events with `styles=["other"]`, `cost=null`, or missing coords:
- Use `event_edit` to fix styles, cost, or coordinates
- For missing coords, follow the **geocode-events** skill

Ask the user if unsure about any classification.

## Step 4.5: Verify events against sources

Run the verification tool to check event details against their source URLs:

```
event_verify(stale_days=7)
```

This produces a report categorizing each event. Handle by status:

| Status | Action |
|--------|--------|
| `confirmed` | No action needed |
| `needs_browser` | Visit the Facebook URL via browser MCP. Navigate, close login dialog, snapshot, and check date/location/status against our data. |
| `no_source` | Web search `"{event name} {location} boston"` to find a source URL. If found, use `event_edit` to add it. |
| `location_mismatch` | Investigate which location is correct. Use `event_set_location_override` to fix. |
| `cancelled` / `page_gone` | Present to user — they decide whether to archive. |
| `needs_review` | Check the flagged text and present to user. |
| `unverifiable` | Flag for user to manually check (Instagram links, etc.) |

Present all flagged items to the user **before** publishing. Never auto-remove events.

## Step 5: Clear processed submissions

```bash
curl -s -X POST \
  -H "Authorization: Bearer $(grep BLD_ADMIN_TOKEN .env | cut -d= -f2)" \
  https://api.bostonsalsa.org/api/submissions/clear
```

## Step 6: Verify build

```bash
npx next build
```

## Step 7: Commit and push

Ask the user to confirm, then:

```bash
git add public/events.json data/events/ data/venues.json scripts/ data/sources.json
git commit -m "Update events $(date +%Y-%m-%d)"
git push
```

---

## Architecture Reference

```
data/events/active.json     Source of truth for live events
data/events/archive.json    Past events (dedup history + reactivation)
data/events/pending.json    Unreviewed submissions
data/events/changelog.jsonl Audit log of all mutations
data/events/dedup-log.jsonl Dedup decision audit trail
data/events/verification-report.json Last verification run output
data/venues.json            Permanent weekly venues (Havana Club, Dante's, etc.)
data/sources.json           Config: URLs, source IDs, defaults
data/scraped/*.json         Intermediate scraped data
data/geocode-cache.json     Nominatim results cache
public/events.json          BUILD ARTIFACT (generated by event_publish)
scripts/event_store.py      Core lifecycle logic (dedup, archive, publish)
scripts/verify_events.py   Event verification engine
scripts/scraper_utils.py    Geocoding, style detection, VENUE_COORDS
mcp-server/server.py        MCP tool definitions
```

## MCP Tools Quick Reference

| Tool | Purpose |
|------|---------|
| `event_list` | Query active/pending/archive events |
| `event_get` | Full details of one event by ID |
| `event_add` | Add event (auto dedup + geocode + style detect) |
| `event_edit` | Update fields on an active event |
| `event_archive` | Move past events to archive |
| `event_approve` | Move pending submission to active |
| `event_reject` | Remove pending with reason |
| `event_scrape` | Run scrapers + ingest + archive |
| `event_ingest` | Ingest from data/scraped/ without re-scraping |
| `event_publish` | Regenerate public/events.json |
| `venue_list` | List permanent venues |
| `venue_add` | Add a new permanent venue |
| `source_list` | List registered sources |
| `source_add` | Register a new source |
| `event_verify` | Verify events against source URLs |
| `event_set_location_override` | Fix wrong location permanently (survives re-scrape) |

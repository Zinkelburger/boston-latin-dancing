---
name: scrape-events
description: >-
  Scrape dance events from all sources and update the map. Use when asked to
  refresh events, scrape Facebook pages (BOBAS, Dante's, etc.), run the pipeline,
  check for new events, or update public/events.json.
---

# Scrape Events Pipeline

Full workflow to refresh all event data for the boston-latin-dance map.

## Quick Reference

```bash
# Full pipeline (ICS + Lister + Eventbrite + merge)
npm run fetch-all

# Individual scrapers
python3 scripts/scrape_ics.py           # Beatrice's Google Calendar
python3 scripts/scrape_lister.py        # listerevents.com
python3 scripts/scrape_eventbrite.py    # Eventbrite search
python3 scripts/scrape_facebook.py bobas          # BOBAS (needs browser)
python3 scripts/scrape_facebook.py dantes-salsa   # Dante's (needs browser)
python3 scripts/scrape_facebook.py --all           # All FB sources

# Merge all scraped sources into public/events.json
python3 scripts/merge_events.py
```

## Architecture

All scrapers are Python. Config lives in `data/sources.json`. Each scraper
writes to `data/scraped/<source-id>.json`. The merge script combines them
into `public/events.json`.

```
data/sources.json          <- config: URLs, source IDs, defaults
scripts/scrape_ics.py      <- ICS feeds (Beatrice calendar)
scripts/scrape_eventbrite.py <- Eventbrite search pages
scripts/scrape_lister.py   <- listerevents.com (Wix)
scripts/scrape_facebook.py <- Any FB page events tab (BOBAS, Dante's, ...)
scripts/scraper_utils.py   <- Shared: geocoding, style detection, VENUE_COORDS
scripts/merge_events.py    <- Dedup, collapse recurring, geocode, write final JSON
```

## Facebook Scraping (Browser Required)

Facebook sources require the Cursor browser MCP. For each FB source:

### Step 1: Navigate to the events page

Source URLs are in `data/sources.json` under `facebook_events_url`. Example:
- BOBAS: `https://www.facebook.com/profile.php?id=61551665503735&sk=events`
- Dante's: `https://www.facebook.com/DantesSalsaInferno/events`

Use `browser_navigate` to go to the URL.

### Step 2: Close the login dialog

Facebook shows a login popup. Find the "Close" button in the snapshot and click it.
It may reappear -- close it again as needed.

### Step 3: Check for Upcoming vs Past tabs

Look at the page snapshot for tab elements:
- If an **"Upcoming"** tab exists and is NOT selected, click it
- If only a **"Past"** tab exists, there are no upcoming events -- write `[]`
- If "Upcoming" is selected, the events listed below are upcoming events

### Step 4: Extract event details

For each upcoming event visible in the snapshot:

1. Note the event name from the link text
2. Click the event link or navigate to its URL to get the detail page
3. Close any login dialog that appears
4. Extract from the detail page snapshot:
   - **Date/time**: from the button like "Friday, May 22, 2026 at 8:30 PM – 1:00 AM EDT"
   - **Name**: from the heading
   - **Location**: from the venue button/link
   - **URL**: from the page URL (e.g. `https://www.facebook.com/events/1234567`)
5. Click "See more" if visible to get the full description

### Step 5: Write raw JSON and run the scraper

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
python3 scripts/merge_events.py
```

### If no upcoming events

This is normal for some sources (like BOBAS, which posts events last-minute).
Just write `[]` to the scraped file -- don't invent events.

## After Merge: Review Flagged Events

`merge_events.py` prints events that need review at the end:

```
⚠ 3 events need review:
  - SUSHI CON SALSA | styles=other, cost=null
  - Boston Salsa Festival | lat=null
```

For each flagged event:
- **styles=other**: Read the event name/description and set the correct styles
  (bachata, salsa, kizomba, zouk, merengue). Edit `public/events.json` directly.
- **cost=null**: Check the event description or URL for pricing. Fill in if found.
- **lat=null**: Follow the geocode-events skill to resolve coordinates. If the
  event has no location string at all, it can't be mapped -- leave it.

Ask the user if you're unsure about any classification.

## Adding a New Source

1. Add an entry to `data/sources.json`:

```json
{
  "id": "my-new-source",
  "type": "facebook",
  "scraper": "scrape_facebook.py",
  "name": "My Dance Group",
  "facebook_events_url": "https://www.facebook.com/MyDanceGroup/events",
  "defaults": {
    "styles": ["bachata", "salsa"],
    "location": "Some Venue"
  },
  "enabled": true
}
```

Supported types: `ics`, `eventbrite`, `wix-events`, `facebook`.

2. If using an existing type, the corresponding scraper handles it automatically.

3. For a new type, create `scripts/scrape_<type>.py` that:
   - Reads config from `sources.json` via `get_source(source_id)`
   - Writes events to `data/scraped/<source-id>.json` via `write_scraped()`
   - Uses `make_event()` from `scraper_utils.py` to build consistent events

4. Add to `SOURCE_PRIORITY` in `merge_events.py` if dedup ordering matters.

5. Add an npm script in `package.json` and include it in `fetch-all`.

## Key Files

| File | Purpose |
|------|---------|
| `data/sources.json` | Central config for all sources |
| `data/scraped/*.json` | Intermediate scraped data (gitignored) |
| `data/geocode-cache.json` | Nominatim results cache |
| `public/events.json` | Final merged output (consumed by frontend) |
| `public/recurring.json` | Manually curated weekly venues |
| `scripts/scraper_utils.py` | Shared: `VENUE_COORDS`, geocoding, style/cost detection |
| `types/event.ts` | TypeScript `DanceEvent` schema |

---
name: add-source
description: >-
  Add a new event source to the boston-latin-dance map. Use when the user gives
  you an event URL, an organizer page, a Facebook group, a calendar link, or
  asks to "add this event" or "add this source" or "scrape this site".
---

# Add a Source

Two modes: **one-off** (add a single event) or **recurring** (add a site that
will have future events). Follow the steps below to determine which, then
execute the appropriate path.

## Step 1: Fetch and analyze the page

Run this in a shell to check for structured data:

```bash
curl -sL "<URL>" \
  -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" \
  | python3 -c "
import sys, json, re
html = sys.stdin.read()
blocks = re.findall(r'<script[^>]*type=\"application/ld\+json\"[^>]*>(.*?)</script>', html, re.S)
for b in blocks:
    try:
        d = json.loads(b)
        items = d if isinstance(d, list) else [d]
        for item in items:
            if item.get('@type') in ('Event', 'SocialEvent', 'DanceEvent'):
                print(json.dumps(item, indent=2)[:2000])
    except: pass
if not blocks: print('NO JSON-LD FOUND')
"
```

**If JSON-LD is found:** You have structured data. Go to Step 2A.

**If no JSON-LD:** Use the browser MCP. Navigate to the URL with
`browser_navigate`, then `browser_snapshot` to read the page content.
Go to Step 2B.

## Step 2A: Extract from JSON-LD

The schema.org Event object has these standard fields:

| JSON-LD field | Maps to |
|---|---|
| `name` | event name |
| `startDate` | ISO start datetime |
| `endDate` | ISO end datetime |
| `location.name` | venue name |
| `location.address` or `location.address.streetAddress` | street address |
| `location.geo.latitude` / `longitude` | coordinates |
| `description` | description |
| `offers[].price` or `offers[].lowPrice` | cost |
| page URL | event URL |

Check for recurring signals:
- `endDate - startDate > 14 days` -> recurring
- Page shows "Other dates" or a list of future dates -> recurring
- Multiple JSON-LD Event objects on the same page -> recurring series

Go to Step 3.

## Step 2B: Extract from browser snapshot

From the snapshot, identify:
- **Event name**: main heading
- **Date/time**: look for date patterns (e.g. "May 22, 2026, 6:00 PM")
- **Location**: venue name and address
- **Description**: body text
- **Cost**: look for "$" amounts or "Free"
- **Recurring signals**: "Other dates" section, weekly schedule, "every Tuesday"

Go to Step 3.

## Step 3: Decide mode

Check these signals and suggest to the user:

| Signal | Suggests |
|---|---|
| Site has an `/events` listing page | Recurring source |
| Multiple upcoming dates visible | Recurring source |
| It's a Facebook page with events tab | Recurring source (use existing FB scraper) |
| It's a Google Calendar / ICS feed | Recurring source (use existing ICS scraper) |
| It's an Eventbrite organizer | Recurring source (add to existing EB config) |
| Single event, one date, no organizer page | One-off |

Ask the user: "This looks like a [one-off event / recurring source]. Should I
add just this event, or set up a scraper for future events from this site?"

## Step 4A: One-off event

Use the `event_add` MCP tool:

```
event_add(
    name="<event name>",
    start_date="2026-06-15T20:00:00-04:00",
    end_date="2026-06-15T23:00:00-04:00",
    location="<venue>, <address>",
    description="<description>",
    url="<url>",
    styles="salsa,bachata",  # comma-separated, or omit for auto-detect
    cost="$15",              # or omit for auto-detect
    source="manual",
)
```

The tool automatically:
- Generates a unique ID
- Geocodes the location
- Deduplicates against existing events
- Detects styles and cost if not provided

Then publish:

```
event_publish()
```

Done. Tell the user the event was added.

## Step 4B: Recurring source

### 4B.1: Check if an existing scraper handles it

| Site type | Action |
|---|---|
| **Facebook page** | Register with `source_add` MCP tool, type `facebook`. Existing `scripts/scrape_facebook.py` handles it. |
| **Google Calendar / ICS feed** | Register with `source_add`, type `ics`. Existing `scripts/scrape_ics.py` handles it. |
| **Eventbrite organizer** | Add search queries to the existing `eventbrite-boston-latin` entry in `data/sources.json`. |
| **Wix site with JSON-LD** | Model a new scraper after `scripts/scrape_lister.py`. |
| **Other site with JSON-LD** | Same pattern as Wix. |
| **Custom HTML (no JSON-LD)** | Generate a scraper with `requests` + `BeautifulSoup`. |

If an existing scraper handles it, skip to 4B.3.

### 4B.2: Generate a new scraper

Create `scripts/scrape_<source-id>.py` following this template:

```python
#!/usr/bin/env python3
"""Scrape events from <source name>."""

import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    filter_future_events, get_source, make_event, write_scraped,
)

SOURCE_ID = "<source-id>"
UA = {"User-Agent": "boston-latin-dance-dev/0.1"}


def fetch_events(listing_url: str) -> list[dict]:
    # 1. Fetch the listing/events page
    # 2. Find event detail links
    # 3. For each link, fetch the page and extract event data
    # 4. Return list of make_event() dicts
    pass


def main():
    source = get_source(SOURCE_ID)
    if not source or not source.get("enabled"):
        print(f"Source '{SOURCE_ID}' not found or disabled")
        return

    events = fetch_events(source["url"])
    events = filter_future_events(events)
    write_scraped(SOURCE_ID, events)


if __name__ == "__main__":
    main()
```

Fill in `fetch_events()` based on the site's HTML structure.

If the listing shows a date but no time, do **not** guess hours — set `startDate` and
`endDate` to midnight local on that date (`start === end`).

### 4B.3: Register the source

Use the `source_add` MCP tool:

```
source_add(
    source_id="<source-id>",
    source_type="<ics|eventbrite|wix-events|facebook|custom>",
    name="<Human-readable name>",
    scraper="scrape_<source-id>.py",
    url="<listing page or feed URL>",
)
```

For Facebook sources, also pass extra config:

```
source_add(
    source_id="<source-id>",
    source_type="facebook",
    name="<Page Name>",
    scraper="scrape_facebook.py",
    url="https://www.facebook.com/<page>/events",
    config_json='{"facebook_events_url": "...", "defaults": {"styles": ["bachata", "salsa"]}}'
)
```

### 4B.4: Wire into the pipeline

1. Add to `SOURCE_PRIORITY` in `scripts/event_store.py`:

   ```python
   SOURCE_PRIORITY = {
       ...
       "<source-id>": 5,
   }
   ```

2. Add npm script to `package.json`:

   ```json
   "fetch-<short-name>": "python3 scripts/scrape_<source-id>.py"
   ```

### 4B.5: Test

```bash
python3 scripts/scrape_<source-id>.py
```

Then ingest and publish:

```
event_ingest(source_id="<source-id>")
event_publish()
```

Check:
- Scraper wrote events to `data/scraped/<source-id>.json`
- Ingest reports added/merged/`dropped_non_latin` counts (non-Latin events are
  dropped at ingest, not queued — for a noisy general calendar, keyword-filter
  at scrape time with `filter_latin_events`, as `scrape_somerville_arts.py` does)
- `public/events.json` includes the new events

### 4B.6: Done

Tell the user the source was added. List the files created/modified.
No commit unless they ask.

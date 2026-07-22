#!/usr/bin/env python3
"""Generic keyword-filtered scraper for big, noisy calendars that expose an ICS feed.

The "easy process" for a big Boston calendar with a lot of noise: most
municipal / arts / venue calendars publish a full-calendar iCal feed (anything
running the ubiquitous "The Events Calendar" WordPress plugin, Google Calendar,
Outlook, etc.). That feed is mostly noise for us — craft fairs, concerts, yoga,
yard sales — so we parse the whole thing once and keep only the events that
mention Latin social dance (the shared keyword filter). Everything else never
enters the pipeline, and no per-source Python is needed.

Adding a new noisy calendar is therefore CONFIG-ONLY. In data/sources.json:

    {
      "id": "somerville-arts",
      "type": "keyword-calendar",
      "scraper": "scrape_keyword_calendar.py",
      "name": "Somerville Arts Council",
      "url": "https://somervilleartscouncil.org/events/",
      "tribe_ical": true          # append ?ical=1 to reach the full iCal feed
    }

Feed URL resolution, in order:
  1. source["feed_url"]                     — an explicit iCal URL
  2. source["url"] + "?ical=1"              — when source["tribe_ical"] is true
                                              (The Events Calendar convention)
  3. source["url"]                          — assume it is already an iCal feed

Fail-safe: any fetch/parse error yields an empty scrape rather than raising, so a
flaky calendar can never break the weekly pipeline. Health is recorded so a page
that loads but parses to zero raw events (markup changed) is flagged for redesign
instead of silently going dark.

Usage: python3 scripts/scrape_keyword_calendar.py <source_id>
"""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_ics import parse_ics_feed
from scraper_utils import (
    filter_future_events,
    filter_latin_events,
    get_source,
    record_scrape_health,
    write_scraped,
)

UA = {"User-Agent": "boston-latin-dance-dev/0.1"}


def resolve_feed_url(source: dict) -> str:
    feed_url = source.get("feed_url")
    if feed_url:
        return feed_url
    url = source["url"]
    if source.get("tribe_ical"):
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}ical=1"
    return url


def scrape_source(source_id: str) -> list[dict]:
    source = get_source(source_id)
    if not source or not source.get("enabled"):
        print(f"Source '{source_id}' is disabled or not found in sources.json")
        return []

    feed_url = resolve_feed_url(source)
    print(f"[{source_id}] Fetching iCal feed from {feed_url[:90]}")
    try:
        resp = requests.get(feed_url, headers=UA, timeout=45)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[{source_id}] Fetch failed: {exc} — emitting nothing")
        record_scrape_health(source_id, 0, 0, fetched=False,
                             note=f"fetch failed: {exc}")
        write_scraped(source_id, [])
        return []

    try:
        events = parse_ics_feed(resp.text, source_id=source_id)
    except Exception as exc:
        print(f"[{source_id}] iCal parse failed: {exc} — emitting nothing")
        record_scrape_health(
            source_id, 0, 0,
            note=f"feed reached us but iCal parse failed ({exc}); "
                 "the feed format may have changed — redesign the scraper",
        )
        write_scraped(source_id, [])
        return []

    print(f"[{source_id}] Parsed {len(events)} events; applying Latin keyword filter")
    latin = filter_latin_events(events)
    upcoming = filter_future_events(latin)

    # Health: raw_found is events parsed BEFORE the keyword/future filters. Zero on
    # a feed that reached us means the feed returned no parseable VEVENTs — the
    # export format likely changed and the scraper needs a redesign. (kept==0 with
    # raw_found>0 is normal — just no upcoming Latin events on the calendar.)
    note = ""
    if not events:
        note = ("feed loaded but no VEVENTs parsed — the calendar's iCal export "
                "may have changed or moved; redesign/repoint the scraper")
    record_scrape_health(source_id, len(events), len(upcoming), note=note)

    write_scraped(source_id, upcoming)
    return upcoming


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/scrape_keyword_calendar.py <source_id>")
        sys.exit(1)
    scrape_source(sys.argv[1])


if __name__ == "__main__":
    main()

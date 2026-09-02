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

Failure semantics come from scraper_utils.run_scraper: a fetch or parse error
exits 1, records a health failure, and keeps the previous file. Health is
recorded with the raw VEVENT count so a feed that loads but parses to zero
events (format changed) is flagged for redesign instead of silently going dark.

Usage: python3 scripts/scrape_keyword_calendar.py <source_id>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_ics import parse_ics_feed
from scraper_utils import (
    ScrapeResult,
    fetch,
    filter_latin_events,
    run_scraper,
    scraper_argparser,
)


def resolve_feed_url(source: dict) -> str:
    feed_url = source.get("feed_url")
    if feed_url:
        return feed_url
    url = source["url"]
    if source.get("tribe_ical"):
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}ical=1"
    return url


def fetch_source(source: dict) -> ScrapeResult:
    source_id = source["id"]
    feed_url = resolve_feed_url(source)
    print(f"[{source_id}] Fetching iCal feed from {feed_url[:90]}")
    ics_text = fetch(feed_url, timeout=45).text

    events = parse_ics_feed(ics_text, source_id=source_id)
    print(f"[{source_id}] Parsed {len(events)} events; applying Latin keyword filter")
    latin = filter_latin_events(events)

    # Health: raw_found is events parsed BEFORE the keyword/future filters. Zero on
    # a feed that reached us means the feed returned no parseable VEVENTs — the
    # export format likely changed and the scraper needs a redesign. (kept==0 with
    # raw_found>0 is normal — just no upcoming Latin events on the calendar.)
    note = ""
    if not events:
        note = ("feed loaded but no VEVENTs parsed — the calendar's iCal export "
                "may have changed or moved; redesign/repoint the scraper")
    return ScrapeResult(latin, raw_found=len(events), note=note)


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())

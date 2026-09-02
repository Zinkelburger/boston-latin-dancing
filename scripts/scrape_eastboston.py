#!/usr/bin/env python3
"""
Scrape Latin dance events from eastboston.com's Sugar Calendar listing.

Strategy:
  1. Fetch the /events/ listing page which shows up to 90 upcoming events.
  2. Parse event titles, URLs, and start/end times from <time> elements.
  3. Filter to Latin-relevant events with the shared keyword filter.
  4. Fetch detail pages for descriptions.
  5. Write data/scraped/eastboston-events.json.

Usage: python3 scripts/scrape_eastboston.py [eastboston-events]
"""

import hashlib
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    ScrapeResult,
    fetch,
    make_event,
    mentions_latin,
    run_scraper,
    scraper_argparser,
)

SOURCE_ID = "eastboston-events"


def parse_listing(html: str) -> list[dict]:
    """Parse the Sugar Calendar listing page into basic event dicts."""
    soup = BeautifulSoup(html, "html.parser")
    events = []

    for block in soup.find_all(
        "div", class_="sugar-calendar-event-list-block__listview__event"
    ):
        title_tag = block.find(
            "h4", class_="sugar-calendar-event-list-block__event__title"
        )
        if not title_tag:
            continue
        link = title_tag.find("a")
        if not link:
            continue

        name = link.text.strip()
        url = link["href"]

        times = block.find_all("time")
        start_dt = None
        end_dt = None

        for t in times:
            dt_str = t.get("datetime", "")
            fmt = t.get("data-conversion-format")
            if not dt_str:
                continue
            # Skip day-of-week and day-number formatters
            if fmt in ("D", "d"):
                continue
            try:
                dt = datetime.fromisoformat(dt_str)
            except ValueError:
                continue

            if start_dt is None:
                start_dt = dt
            elif dt > start_dt:
                end_dt = dt

        if not start_dt:
            continue

        events.append({
            "name": name,
            "url": url,
            "start": start_dt,
            "end": end_dt or start_dt,
        })

    return events


def is_dance_relevant(name: str, description: str = "") -> bool:
    """The shared Latin keyword rule — the same one ingest applies."""
    return mentions_latin(f"{name} {description}")


def fetch_description(url: str) -> str:
    """Fetch an event detail page and extract description text."""
    try:
        html = fetch(url, browser=True, timeout=15).text
    except Exception as e:
        print(f"    Failed to fetch detail: {e}")
        return ""

    soup = BeautifulSoup(html, "html.parser")

    content = soup.find("div", class_="td-post-content")
    if not content:
        content = soup.find("div", class_="entry-content")
    if not content:
        return ""

    # Remove the Sugar Calendar metadata block (Date:, Time:, Calendar: labels)
    for meta in content.find_all(
        "div", class_=re.compile(r"sugar-calendar")
    ):
        meta.decompose()

    text = content.get_text(separator="\n", strip=True)

    # Strip the metadata block that Sugar Calendar injects at the top.
    # Pattern: Date: / value / Add to Calendar / ... / Time: / value / Calendar: / value
    text = re.sub(
        r"^(?:Date:\n.*?\n)?(?:Add to Calendar\n(?:.*?\n)*?Download\n)?(?:Time:\n.*?\n-\n.*?\n)?(?:Calendar:\n.*?\n)?",
        "",
        text,
        flags=re.S,
    )
    text = text.strip()

    return text


def make_event_id(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    short_hash = hashlib.md5(slug.encode()).hexdigest()[:8]
    return f"eb-com-{short_hash}"


def build_events(raw_events: list[dict], source: dict, *, delay: float = 0.8) -> list[dict]:
    """Keyword-filter the listing rows, fetch details, and build DanceEvents."""
    # First pass: filter by title
    candidates = []
    for ev in raw_events:
        if is_dance_relevant(ev["name"]):
            candidates.append(ev)
            print(f"  [relevant] {ev['name']}")
        else:
            print(f"  [skip] {ev['name']}")

    print(f"\n{len(candidates)} potentially relevant events, fetching details...")

    default_location = (source.get("defaults") or {}).get("location", "East Boston, MA")
    events: list[dict] = []
    for i, ev in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {ev['name']}")
        description = fetch_description(ev["url"])

        if not is_dance_relevant(ev["name"], description):
            print("    -> skipped after description check")
            continue

        # Try extracting location from description, else the source default.
        location = ""
        loc_match = re.search(r"Location:\s*(.+?)(?:\n|$)", description)
        if loc_match:
            location = loc_match.group(1).strip()
        if not location:
            location = default_location

        event = make_event(
            id=make_event_id(ev["url"]),
            name=ev["name"],
            start=ev["start"],
            end=ev["end"],
            location=location,
            description=description,
            url=ev["url"],
            source=SOURCE_ID,
        )
        events.append(event)
        print(f"    -> {event['name']} | {event['dayOfWeek']} | styles={event['styles']}")

        if delay and i < len(candidates) - 1:
            time.sleep(delay)
    return events


def fetch_source(source: dict) -> ScrapeResult:
    listing_url = source["url"]
    print(f"Fetching events listing from {listing_url}")
    html = fetch(listing_url, browser=True, timeout=15).text

    raw_events = parse_listing(html)
    print(f"Found {len(raw_events)} events on listing page")
    events = build_events(raw_events, source)
    print(f"\nResults: {len(events)} events from {len(raw_events)} total listings")
    return ScrapeResult(events, raw_found=len(raw_events))


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())

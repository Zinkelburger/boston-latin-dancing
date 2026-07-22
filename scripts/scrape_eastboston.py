#!/usr/bin/env python3
"""
Scrape Latin dance events from eastboston.com's Sugar Calendar listing.

Strategy:
  1. Fetch the /events/ listing page which shows up to 90 upcoming events.
  2. Parse event titles, URLs, and start/end times from <time> elements.
  3. Filter to dance-relevant events (must mention a known dance style).
  4. Fetch detail pages for descriptions.
  5. Write data/scraped/eastboston-events.json.
"""

import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    detect_styles,
    extract_cost,
    filter_future_events,
    get_source,
    make_event,
    write_scraped,
)

SOURCE_ID = "eastboston-events"
UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

DANCE_KEYWORDS = re.compile(
    r"salsa|bachata|kizomba|zouk|merengue|latin\s+(?:music|dance)|cumbia|reggaeton|mambo|cha\s*cha|dance\s+festival",
    re.I,
)


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
    combined = f"{name} {description}"
    return bool(DANCE_KEYWORDS.search(combined))


def fetch_description(url: str) -> str:
    """Fetch an event detail page and extract description text."""
    try:
        resp = requests.get(url, headers=UA, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"    Failed to fetch detail: {e}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

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


def main():
    source = get_source(SOURCE_ID)
    if not source or not source.get("enabled"):
        print(f"Source '{SOURCE_ID}' not found or disabled")
        return

    listing_url = source["url"]
    print(f"Fetching events listing from {listing_url}")

    try:
        resp = requests.get(listing_url, headers=UA, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch listing: {e}")
        return

    raw_events = parse_listing(resp.text)
    print(f"Found {len(raw_events)} events on listing page")

    # First pass: filter by title
    candidates = []
    for ev in raw_events:
        if is_dance_relevant(ev["name"]):
            candidates.append(ev)
            print(f"  [relevant] {ev['name']}")
        else:
            print(f"  [skip] {ev['name']}")

    print(f"\n{len(candidates)} potentially relevant events, fetching details...")

    events: list[dict] = []
    for i, ev in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {ev['name']}")
        description = fetch_description(ev["url"])

        if not is_dance_relevant(ev["name"], description):
            print(f"    -> skipped after description check")
            continue

        location = ""
        # Try extracting location from description
        loc_match = re.search(r"Location:\s*(.+?)(?:\n|$)", description)
        if loc_match:
            location = loc_match.group(1).strip()

        # Use defaults from source config if no location found
        if not location:
            defaults = source.get("defaults", {})
            location = defaults.get("location", "East Boston, MA")

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

        if i < len(candidates) - 1:
            time.sleep(0.8)

    print(f"\nResults: {len(events)} events from {len(raw_events)} total listings")
    events = filter_future_events(events)
    write_scraped(SOURCE_ID, events)


if __name__ == "__main__":
    main()

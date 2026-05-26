#!/usr/bin/env python3
"""Scrape upcoming socials from Fiesta Dance Company (Squarespace)."""

import hashlib
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import filter_future_events, get_source, make_event, write_scraped

SOURCE_ID = "fiesta-dance-company"
UA = {"User-Agent": "boston-latin-dance-dev/0.1"}
EDT = timezone(timedelta(hours=-4))

WEBSITE = "https://fiestadancecompany.com"
SOCIALS_URL = f"{WEBSITE}/upcoming-socials"
INSTAGRAM = "https://www.instagram.com/fiestadancecompany/"

# Parsed from https://fiestadancecompany.com/locations
VENUE_ADDRESSES = {
    "sol de mexico": "Sol de Mexico, 350 E Main St, Milford, MA 01757",
    "westborough community center": "Westborough Community Center, 1500 Union St, 2nd Floor, Westborough, MA 01581",
    "westborough": "Westborough Community Center, 1500 Union St, 2nd Floor, Westborough, MA 01581",
}

LINE_RE = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+"
    r"(\d{1,2})\s+-\s+(.+?)\s+-\s+(.+)$",
    re.I,
)


def resolve_location(venue: str, city_hint: str) -> str:
    key = venue.strip().lower()
    if key in VENUE_ADDRESSES:
        return VENUE_ADDRESSES[key]
    for name, address in VENUE_ADDRESSES.items():
        if name in key or key in name:
            return address
    city = city_hint.strip()
    if city and city.lower() not in venue.lower():
        return f"{venue.strip()}, {city}, MA"
    return f"{venue.strip()}, MA"


def parse_social_line(text: str, year: int) -> dict | None:
    m = LINE_RE.match(text.strip())
    if not m:
        return None

    day_name, month_str, day_num, venue, city_hint = m.groups()
    month_str = month_str[:3].title()
    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    month = month_map.get(month_str)
    if not month:
        return None

    try:
        # Page lists date only — no time; use midnight as date anchor (start === end).
        start = datetime(year, month, int(day_num), 0, 0, tzinfo=EDT)
    except ValueError:
        return None

    end = start
    location = resolve_location(venue, city_hint)
    name = "Salsa & Bachata Social w/ Fiesta Dance Co"
    description = (
        f"Salsa & Bachata social hosted by Fiesta Dance Company at {venue.strip()}.\n\n"
        f"Organized by Fiesta Dance Company\n"
        f"Website: {SOCIALS_URL}\n"
        f"Instagram: {INSTAGRAM}"
    )
    slug = re.sub(r"[^a-z0-9]+", "-", f"{venue}-{city_hint}".lower()).strip("-")
    event_id = f"fiesta-{start.strftime('%Y%m%d')}-{slug}"
    if len(event_id) > 64:
        event_id = f"fiesta-{hashlib.sha1(event_id.encode()).hexdigest()[:12]}"

    return make_event(
        id=event_id,
        name=name,
        start=start,
        end=end,
        location=location,
        description=description,
        url=SOCIALS_URL,
        styles=["salsa", "bachata"],
        recurring=False,
        source=SOURCE_ID,
    )


def fetch_events(listing_url: str) -> list[dict]:
    resp = requests.get(listing_url, headers=UA, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    lines: list[str] = []
    for block in soup.select("[data-block-type='1337'], .sqs-block-content"):
        text = block.get_text(" ", strip=True)
        if LINE_RE.match(text):
            lines.append(text)

    if not lines:
        for node in soup.find_all(string=LINE_RE):
            lines.append(node.strip())

    year = datetime.now(EDT).year
    events: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        ev = parse_social_line(line, year)
        if not ev or ev["id"] in seen:
            continue
        # If the parsed date is far in the past, try next year.
        start_dt = datetime.fromisoformat(ev["startDate"])
        if start_dt < datetime.now(EDT) - timedelta(days=7):
            ev = parse_social_line(line, year + 1)
            if not ev:
                continue
        seen.add(ev["id"])
        events.append(ev)
        print(f"  -> {ev['name']} on {ev['dayOfWeek']} @ {ev['location']}")

    return events


def main():
    source = get_source(SOURCE_ID)
    if not source or not source.get("enabled"):
        print(f"Source '{SOURCE_ID}' not found or disabled")
        return

    listing_url = source.get("url", SOCIALS_URL)
    print(f"Fetching socials from {listing_url}")
    events = fetch_events(listing_url)
    events = filter_future_events(events)
    write_scraped(SOURCE_ID, events)


if __name__ == "__main__":
    main()

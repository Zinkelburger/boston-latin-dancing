#!/usr/bin/env python3
"""Scrape upcoming socials from Fiesta Dance Company (Squarespace).

Usage: python3 scripts/scrape_fiesta_dance.py [fiesta-dance-company]
"""

import hashlib
import re
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    MONTH_NAME_RE,
    NY_TZ,
    fetch,
    make_event,
    month_number,
    resolve_year,
    run_scraper,
    scraper_argparser,
)

SOURCE_ID = "fiesta-dance-company"

WEBSITE = "https://fiestadancecompany.com"
SOCIALS_URL = f"{WEBSITE}/upcoming-socials"
INSTAGRAM = "https://www.instagram.com/fiestadancecompany/"

# Parsed from https://fiestadancecompany.com/locations
VENUE_ADDRESSES = {
    "sol de mexico": "Sol de Mexico, 350 E Main St, Milford, MA 01757",
    "westborough community center": "Westborough Community Center, 1500 Union St, 2nd Floor, Westborough, MA 01581",
    "westborough": "Westborough Community Center, 1500 Union St, 2nd Floor, Westborough, MA 01581",
    "agave mexican grill & cantina": "Agave Mexican Grill & Cantina, 197A Boston Post Rd W, Marlborough, MA 01752",
    "agave mexican grill": "Agave Mexican Grill & Cantina, 197A Boston Post Rd W, Marlborough, MA 01752",
}

_DAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"

LINE_RE = re.compile(
    rf"^({_DAY})\s+({MONTH_NAME_RE})\.?\s+(\d{{1,2}})\s+-\s+(.+?)\s+-\s+(.+)$",
    re.I,
)

# Squarespace collapses the whole listing into one text block, so several
# "Friday July 17 - Venue - City" entries arrive concatenated. Split at each
# new day+month+date boundary before line-parsing, or the first entry's
# city group swallows every entry after it.
ENTRY_BOUNDARY_RE = re.compile(rf"(?={_DAY}\s+{MONTH_NAME_RE}\.?\s+\d{{1,2}}\s+-)", re.I)


def split_entries(text: str) -> list[str]:
    return [seg.strip() for seg in ENTRY_BOUNDARY_RE.split(text) if seg.strip()]


def resolve_location(venue: str, city_hint: str) -> str:
    key = venue.strip().lower()
    if key in VENUE_ADDRESSES:
        return VENUE_ADDRESSES[key]
    for name, address in VENUE_ADDRESSES.items():
        if name in key or key in name:
            return address
    city = re.sub(r",?\s*MA\.?$", "", city_hint.strip(), flags=re.I).strip()
    if city and city.lower() not in venue.lower():
        return f"{venue.strip()}, {city}, MA"
    return f"{venue.strip()}, MA"


def parse_social_line(text: str, today: datetime) -> dict | None:
    """One 'Friday July 17 - Venue - City' line -> DanceEvent, or None.

    The page prints no year: a date more than a week past rolls to next year,
    and a rollover that lands most of a year out is a stale entry left on the
    listing, not an upcoming social, so it is dropped (see resolve_year).
    """
    m = LINE_RE.match(text.strip())
    if not m:
        return None

    _day_name, month_str, day_num, venue, city_hint = m.groups()
    month = month_number(month_str)
    if not month:
        return None
    when = resolve_year(month, int(day_num), today)
    if when is None:
        return None

    # Page lists date only — no time; use midnight as date anchor (start === end).
    start = datetime(when.year, when.month, when.day, 0, 0, tzinfo=NY_TZ)
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


def parse_socials_page(page_html: str, today: datetime | None = None) -> list[dict]:
    """Parse the socials listing page into DanceEvent dicts."""
    today = today or datetime.now(NY_TZ)
    soup = BeautifulSoup(page_html, "html.parser")

    lines: list[str] = []
    for block in soup.select("[data-block-type='1337'], .sqs-block-content"):
        text = block.get_text(" ", strip=True)
        for segment in split_entries(text):
            if LINE_RE.match(segment):
                lines.append(segment)

    if not lines:
        for node in soup.find_all(string=LINE_RE):
            lines.extend(s for s in split_entries(node.strip()) if LINE_RE.match(s))

    events: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        ev = parse_social_line(line, today)
        if not ev or ev["id"] in seen:
            continue
        seen.add(ev["id"])
        events.append(ev)
        print(f"  -> {ev['name']} on {ev['dayOfWeek']} @ {ev['location']}")

    return events


def fetch_source(source: dict) -> list[dict]:
    listing_url = source.get("url", SOCIALS_URL)
    print(f"Fetching socials from {listing_url}")
    page = fetch(listing_url, timeout=15).text
    return parse_socials_page(page)


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())

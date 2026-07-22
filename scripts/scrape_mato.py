#!/usr/bin/env python3
"""
Scrape dance events from ma.to venue pages.

Strategy:
  1. Fetch the venue page and find all /event/ links.
  2. Filter to dance-related events (salsa, bachata, etc.).
  3. For each event page, extract the JSON-LD schema.org Event object.
  4. Build DanceEvent dicts with geocoding from the venue JSON-LD.
"""

import json
import re
import sys
import time
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    filter_future_events,
    get_source,
    make_event,
    write_scraped,
)

UA = {"User-Agent": "boston-latin-dance-dev/0.1"}

DANCE_KEYWORDS = re.compile(r"salsa|bachata|latin|kizomba|zouk|merengue|cumbia", re.I)


def fetch_event_links(venue_url: str) -> list[str]:
    """Get all /event/ links from a ma.to venue page."""
    resp = requests.get(venue_url, headers=UA, timeout=15)
    resp.raise_for_status()

    links: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="(/event/[^"]+)"', resp.text):
        path = match.group(1)
        full = urljoin(venue_url, path)
        if full not in seen:
            seen.add(full)
            links.append(full)
    return links


def extract_jsonld_event(html_text: str) -> dict | None:
    """Extract schema.org Event from JSON-LD script tags."""
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("@type") == "Event":
                return data
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "Event":
                        return item
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def is_dance_event(name: str, description: str) -> bool:
    """Check if an event is dance-related based on name/description."""
    text = f"{name} {description}"
    return bool(DANCE_KEYWORDS.search(text))


def parse_event_page(url: str) -> dict | None:
    """Fetch an event page and return a DanceEvent dict."""
    resp = requests.get(url, headers=UA, timeout=15)
    resp.raise_for_status()

    ld = extract_jsonld_event(resp.text)
    if not ld:
        print(f"  No JSON-LD found: {url}")
        return None

    name = unescape(ld.get("name", "Untitled"))
    description = unescape(ld.get("description", ""))

    if not is_dance_event(name, description):
        print(f"  Skipping (not dance): {name}")
        return None

    location_obj = ld.get("location", {})
    venue_name = location_obj.get("name", "")
    address = location_obj.get("address", "")
    if isinstance(address, dict):
        address = address.get("streetAddress", "")
    location = f"{venue_name}, {address}".strip(", ") if address else venue_name

    start_str = ld.get("startDate", "")
    end_str = ld.get("endDate", start_str)

    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
    except (ValueError, TypeError):
        print(f"  Bad date: {name} start={start_str}")
        return None

    lat, lng = None, None
    geo = location_obj.get("geo")
    if isinstance(geo, dict):
        lat = geo.get("latitude")
        lng = geo.get("longitude")

    organizer = ld.get("organizer", {})
    org_name = organizer.get("name", "") if isinstance(organizer, dict) else ""

    cost = None
    offers = ld.get("offers", {})
    if isinstance(offers, dict):
        price = offers.get("price", offers.get("lowPrice"))
        if price is not None:
            cost = "Free" if float(price) == 0 else f"${price}"

    slug = url.rstrip("/").split("/")[-1]
    event_id = f"mato-{slug}"

    desc_with_source = f"{description}\n\nSource: {url}"

    return make_event(
        id=event_id,
        name=name,
        start=start,
        end=end,
        location=location,
        lat=lat,
        lng=lng,
        description=desc_with_source,
        url=url,
        cost=cost,
        recurring=False,
        source=get_source_id(),
    )


_source_id = None


def get_source_id() -> str:
    return _source_id or "mato-lawn-on-d"


def main():
    global _source_id

    source_id = sys.argv[1] if len(sys.argv) > 1 else "mato-lawn-on-d"
    _source_id = source_id

    source = get_source(source_id)
    if not source or not source.get("enabled"):
        print(f"Source '{source_id}' not found or disabled in sources.json")
        return

    venue_url = source["url"]
    print(f"Fetching event links from {venue_url}")
    links = fetch_event_links(venue_url)
    print(f"Found {len(links)} event links")

    events: list[dict] = []
    for i, link in enumerate(links):
        print(f"  [{i+1}/{len(links)}] {link}")
        try:
            ev = parse_event_page(link)
            if ev:
                events.append(ev)
                print(f"    -> {ev['name']} ({ev['dayOfWeek']} {ev['startDate'][:10]})")
        except Exception as e:
            print(f"    ERROR: {e}")
        if i < len(links) - 1:
            time.sleep(0.5)

    events = filter_future_events(events)
    write_scraped(source_id, events)


if __name__ == "__main__":
    main()

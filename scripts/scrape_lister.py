#!/usr/bin/env python3
"""
Scrape events from listerevents.com (Wix-based site).

Strategy:
  1. Fetch the /events listing page, find all event detail links.
  2. For each detail page, extract the JSON-LD schema.org Event object
     (Wix embeds this automatically) and the "About the event" description.
  3. Filter to Boston-area events, detect styles/cost, geocode.
  4. Write data/scraped/lister-events.json.
"""

import json
import re
import sys
import time
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from scraper_utils import (
    detect_styles,
    extract_cost,
    filter_future_events,
    geocode,
    get_source,
    make_event,
    write_scraped,
)

SOURCE_ID = "lister-events"
UA = {"User-Agent": "boston-latin-dance-dev/0.1"}

# Locations outside greater Boston to skip
EXCLUDE_REGIONS = {"portland", "maine", "me 04"}


def fetch_event_links(listing_url: str) -> list[str]:
    """Get all /event-details/* links from the listing page."""
    resp = requests.get(listing_url, headers=UA, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/event-details/" in href:
            full = urljoin(listing_url, href)
            if full not in seen:
                seen.add(full)
                links.append(full)
    return links


def extract_jsonld_event(soup: BeautifulSoup) -> dict | None:
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


def extract_about_text(soup: BeautifulSoup) -> str:
    """Pull the 'About the event' description from the detail page HTML."""
    for heading in soup.find_all(["h2", "h3"]):
        if "about the event" in heading.get_text(strip=True).lower():
            container = heading.find_parent()
            while container and len(container.get_text(strip=True)) < 100:
                container = container.find_parent()
            if container:
                text = container.get_text(separator="\n", strip=True)
                idx = text.lower().find("about the event")
                if idx >= 0:
                    desc = text[idx + len("about the event"):].strip()
                    for cutoff in [
                        "Share this event",
                        "bottom of page",
                        "Show More",
                        "Show more",
                        "See More",
                        "See more",
                        "Read More",
                        "Read more",
                    ]:
                        ci = desc.find(cutoff)
                        if ci > 0:
                            desc = desc[:ci].strip()
                    return desc
    return ""


def should_skip(location_str: str, address_str: str) -> bool:
    """Return True if the event is outside the Boston area."""
    combined = f"{location_str} {address_str}".lower()
    return any(region in combined for region in EXCLUDE_REGIONS)


def parse_detail_page(url: str) -> dict | None:
    """Fetch an event detail page and return a DanceEvent dict."""
    resp = requests.get(url, headers=UA, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    ld = extract_jsonld_event(soup)
    if not ld:
        print(f"  No JSON-LD found: {url}")
        return None

    name = unescape(ld.get("name", "Untitled"))
    description_short = unescape(ld.get("description", ""))

    about = extract_about_text(soup)
    # Prefer whichever description is longer — the HTML "about" text may be
    # truncated by Wix's collapsed view while JSON-LD has the full version.
    if about and description_short:
        description = about if len(about) >= len(description_short) else description_short
    else:
        description = about or description_short

    location_obj = ld.get("location", {})
    venue_name = location_obj.get("name", "")
    address = location_obj.get("address", "")
    if isinstance(address, dict):
        address = address.get("streetAddress", "")
    location = f"{venue_name}, {address}".strip(", ") if address else venue_name

    if should_skip(venue_name, address):
        print(f"  Skipping (outside Boston): {name} @ {location}")
        return None

    start_str = ld.get("startDate", "")
    end_str = ld.get("endDate", start_str)

    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
    except (ValueError, TypeError):
        print(f"  Bad date: {name} start={start_str}")
        return None

    # If the date range spans many weeks, it's a recurring weekly series
    span_days = (end - start).days
    recurring = span_days > 14

    lat, lng = None, None
    geo = location_obj.get("geo")
    if isinstance(geo, dict):
        lat = geo.get("latitude")
        lng = geo.get("longitude")

    slug = url.rstrip("/").split("/")[-1]
    event_id = f"lister-{slug}"

    return make_event(
        id=event_id,
        name=name,
        start=start,
        end=end if not recurring else start,
        location=location,
        lat=lat,
        lng=lng,
        description=description,
        url=url,
        recurring=recurring,
        source=SOURCE_ID,
    )


def main():
    source = get_source(SOURCE_ID)
    if not source or not source.get("enabled"):
        print(f"Source '{SOURCE_ID}' is disabled or not found in sources.json")
        return

    listing_url = source["url"]
    print(f"Fetching event links from {listing_url}")
    links = fetch_event_links(listing_url)
    print(f"Found {len(links)} event detail pages")

    events: list[dict] = []
    for i, link in enumerate(links):
        print(f"  [{i+1}/{len(links)}] {link}")
        try:
            ev = parse_detail_page(link)
            if ev:
                events.append(ev)
                print(f"    -> {ev['name']} ({ev['dayOfWeek']}) recurring={ev['recurring']}")
        except Exception as e:
            print(f"    ERROR: {e}")
        if i < len(links) - 1:
            time.sleep(0.5)

    events = filter_future_events(events)
    write_scraped(SOURCE_ID, events)


if __name__ == "__main__":
    main()

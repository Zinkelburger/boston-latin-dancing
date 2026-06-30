#!/usr/bin/env python3
"""
Generic Wix Events scraper – works for any Wix-based site that embeds
schema.org JSON-LD on event detail pages.

Usage:
    python3 scripts/scrape_wix.py <source-id>

The source entry in data/sources.json should look like:
    {
        "id": "my-source",
        "type": "wix-events",
        "scraper": "scrape_wix.py",
        "name": "Human Name",
        "url": "https://example.com/events",          # primary listing page
        "listing_urls": ["https://example.com/more"],  # optional extra listing pages
        "link_pattern": "/event-details/",             # default, or "/events-1/" etc.
        "enabled": true
    }
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
    filter_future_events,
    get_source,
    make_event,
    write_scraped,
)

UA = {"User-Agent": "boston-latin-dance-dev/0.1"}

EXCLUDE_REGIONS = {"portland", "maine", "me 04"}


def fetch_event_links(listing_urls: list[str], link_pattern: str) -> list[str]:
    """Collect event detail links from one or more listing pages."""
    seen: set[str] = set()
    links: list[str] = []
    for listing_url in listing_urls:
        print(f"  Listing page: {listing_url}")
        try:
            resp = requests.get(listing_url, headers=UA, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f"    Failed: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0].split("#")[0]
            if link_pattern in href:
                full = urljoin(listing_url, href)
                path = full.rstrip("/").split("/")[-1]
                listing_path = listing_url.rstrip("/").split("/")[-1]
                if path == listing_path:
                    continue
                if full not in seen:
                    seen.add(full)
                    links.append(full)

        print(f"    Found {len(seen)} unique event links so far")
        time.sleep(0.5)
    return links


_EVENT_TYPES = {"Event", "SocialEvent", "DanceEvent", "MusicEvent", "Festival"}


def extract_jsonld_event(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") in _EVENT_TYPES:
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") in _EVENT_TYPES:
                    return item
    return None


def extract_about_text(soup: BeautifulSoup) -> str:
    for heading in soup.find_all(["h2", "h3"]):
        text = heading.get_text(strip=True).lower()
        if "about the event" in text or "about this event" in text:
            container = heading.find_parent()
            while container and len(container.get_text(strip=True)) < 100:
                container = container.find_parent()
            if container:
                full = container.get_text(separator="\n", strip=True)
                idx = full.lower().find("about the event")
                if idx < 0:
                    idx = full.lower().find("about this event")
                if idx >= 0:
                    desc = full[idx + len("about the event"):].strip()
                    for cutoff in ["Share this event", "Share This Event",
                                   "bottom of page", "Show More", "Show more",
                                   "Read More", "Read more"]:
                        ci = desc.find(cutoff)
                        if ci > 0:
                            desc = desc[:ci].strip()
                    return desc
    return ""


def should_skip(location_str: str, address_str: str) -> bool:
    combined = f"{location_str} {address_str}".lower()
    return any(region in combined for region in EXCLUDE_REGIONS)


def parse_detail_page(url: str, source_id: str) -> dict | None:
    resp = requests.get(url, headers=UA, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    ld = extract_jsonld_event(soup)
    if not ld:
        print(f"    No JSON-LD found")
        return None

    name = unescape(ld.get("name", "Untitled"))
    description_short = unescape(ld.get("description", ""))

    about = extract_about_text(soup)
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
        print(f"    Skipping (outside Boston): {name} @ {location}")
        return None

    start_str = ld.get("startDate", "")
    end_str = ld.get("endDate", start_str)

    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
    except (ValueError, TypeError):
        print(f"    Bad date: {name} start={start_str}")
        return None

    span_days = (end - start).days
    recurring = span_days > 14

    lat, lng = None, None
    geo = location_obj.get("geo")
    if isinstance(geo, dict):
        lat = geo.get("latitude")
        lng = geo.get("longitude")

    slug = url.rstrip("/").split("/")[-1]
    event_id = f"{source_id}-{slug}"

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
        source=source_id,
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scrape_wix.py <source-id>")
        sys.exit(1)

    source_id = sys.argv[1]
    source = get_source(source_id)
    if not source or not source.get("enabled"):
        print(f"Source '{source_id}' is disabled or not found in sources.json")
        return

    listing_urls = source.get("listing_urls", [source["url"]])
    link_pattern = source.get("link_pattern", "/event-details/")

    print(f"Scraping {source['name']} (link pattern: {link_pattern})")
    links = fetch_event_links(listing_urls, link_pattern)
    print(f"Found {len(links)} event detail pages")

    events: list[dict] = []
    for i, link in enumerate(links):
        print(f"  [{i+1}/{len(links)}] {link.split('/')[-1][:60]}")
        try:
            ev = parse_detail_page(link, source_id)
            if ev:
                events.append(ev)
                print(f"    -> {ev['name']} ({ev['dayOfWeek']}) styles={ev['styles']}")
        except Exception as e:
            print(f"    ERROR: {e}")
        if i < len(links) - 1:
            time.sleep(0.5)

    events = filter_future_events(events)
    write_scraped(source_id, events)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Scrape Latin dance events from Eventbrite.

Strategy:
  1. Crawl Eventbrite search pages for each query in sources.json to discover event URLs.
  2. Fetch each event detail page, extract the JSON-LD schema.org SocialEvent/Event object.
  3. Filter to dance-relevant events (must mention a known dance style in title/description).
  4. Detect styles, cost, geocode, write data/scraped/eventbrite-boston-latin.json.
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

SOURCE_ID = "eventbrite-boston-latin"
UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

DANCE_KEYWORDS = re.compile(
    r"salsa|bachata|kizomba|zouk|merengue|latin\s*dance|cumbia|reggaeton|mambo|cha\s*cha",
    re.I,
)


def discover_event_urls(search_urls: list[str]) -> list[str]:
    """Crawl Eventbrite search/category pages to collect event URLs."""
    all_urls: set[str] = set()
    for search_url in search_urls:
        print(f"  Searching: {search_url}")
        try:
            resp = requests.get(search_url, headers=UA, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f"    Failed: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0]
            if "/e/" in href and "tickets-" in href:
                full = urljoin("https://www.eventbrite.com", href)
                all_urls.add(full)

        print(f"    Found {len(all_urls)} total unique event URLs so far")
        time.sleep(1.0)

    return sorted(all_urls)


def extract_event_jsonld(soup: BeautifulSoup) -> dict | None:
    """Find the SocialEvent or Event JSON-LD on an Eventbrite page."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") in ("SocialEvent", "Event", "DanceEvent"):
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") in ("SocialEvent", "Event", "DanceEvent"):
                    return item
    return None


def extract_full_description(soup: BeautifulSoup) -> str:
    """Pull the long-form description from the Eventbrite page HTML."""
    # Eventbrite puts the description in a structured-content div
    for div in soup.find_all("div", class_=re.compile(r"structured-content")):
        text = div.get_text(separator="\n", strip=True)
        if len(text) > 50:
            return text

    # Fallback: look for the summary/description section
    for div in soup.find_all("div", attrs={"data-testid": re.compile(r"description")}):
        text = div.get_text(separator="\n", strip=True)
        if text:
            return text

    return ""


def is_dance_relevant(name: str, description: str) -> bool:
    combined = f"{name} {description}"
    return bool(DANCE_KEYWORDS.search(combined))


def parse_address(location_obj: dict) -> tuple[str, str]:
    """Return (venue_name, full_location_string) from JSON-LD location."""
    venue_name = location_obj.get("name", "")
    addr_obj = location_obj.get("address", {})

    if isinstance(addr_obj, str):
        return venue_name, f"{venue_name}, {addr_obj}".strip(", ")

    street = addr_obj.get("streetAddress", "")
    locality = addr_obj.get("addressLocality", "")
    region = addr_obj.get("addressRegion", "")

    parts = [p for p in [street, locality, region] if p]
    address_str = ", ".join(parts)

    if venue_name and address_str:
        return venue_name, f"{venue_name}\n{address_str}"
    return venue_name, address_str or venue_name


def parse_offers(offers: list | dict | None) -> str | None:
    """Extract price from JSON-LD offers."""
    if not offers:
        return None

    if isinstance(offers, dict):
        offers = [offers]

    for offer in offers:
        low = offer.get("lowPrice") or offer.get("price")
        if low is not None:
            try:
                price = float(low)
                if price == 0:
                    return "Free"
                return f"${price:.0f}" if price == int(price) else f"${price:.2f}"
            except (ValueError, TypeError):
                pass
    return None


def scrape_event_page(url: str) -> dict | None:
    """Fetch a single Eventbrite event page and return a DanceEvent dict."""
    resp = requests.get(url, headers=UA, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    ld = extract_event_jsonld(soup)
    if not ld:
        return None

    name = unescape(ld.get("name", "Untitled"))
    short_desc = unescape(ld.get("description", ""))
    long_desc = extract_full_description(soup)
    description = long_desc if long_desc else short_desc

    if not is_dance_relevant(name, description):
        return None

    location_obj = ld.get("location", {})
    venue_name, location = parse_address(location_obj)

    start_str = ld.get("startDate", "")
    end_str = ld.get("endDate", start_str)

    try:
        start = datetime.fromisoformat(start_str)
        end = datetime.fromisoformat(end_str)
    except (ValueError, TypeError):
        return None

    span_days = (end - start).days
    recurring = span_days > 14

    offers = ld.get("offers")
    cost = parse_offers(offers)

    # Eventbrite ticket ID from URL
    slug = url.rstrip("/").split("/")[-1]
    ticket_match = re.search(r"tickets-(\d+)", slug)
    event_id = f"eb-{ticket_match.group(1)}" if ticket_match else f"eb-{hash(url) % 10**8}"

    organizer = ld.get("organizer", {})
    organizer_name = organizer.get("name", "")

    desc_with_source = description
    if organizer_name:
        desc_with_source = f"{description}\n\nOrganizer: {organizer_name}"

    return make_event(
        id=event_id,
        name=name,
        start=start,
        end=end if not recurring else start,
        location=location,
        description=desc_with_source,
        url=url,
        cost=cost,
        recurring=recurring,
        source=SOURCE_ID,
    )


def main():
    source = get_source(SOURCE_ID)
    if not source or not source.get("enabled"):
        print(f"Source '{SOURCE_ID}' is disabled or not found in sources.json")
        return

    search_urls = source.get("search_queries", [])

    print("Discovering event URLs from Eventbrite search pages...")
    urls = discover_event_urls(search_urls)
    print(f"\nTotal unique event URLs: {len(urls)}")

    events: list[dict] = []
    skipped_irrelevant = 0
    skipped_error = 0

    for i, url in enumerate(urls):
        print(f"  [{i+1}/{len(urls)}] {url.split('/')[-1][:60]}")
        try:
            ev = scrape_event_page(url)
            if ev:
                events.append(ev)
                print(f"    -> {ev['name']} | {ev['dayOfWeek']} | styles={ev['styles']}")
            else:
                skipped_irrelevant += 1
                print(f"    -> skipped (not dance-relevant or no JSON-LD)")
        except Exception as e:
            skipped_error += 1
            print(f"    ERROR: {e}")

        if i < len(urls) - 1:
            time.sleep(0.8)

    print(f"\nResults: {len(events)} events, {skipped_irrelevant} irrelevant, {skipped_error} errors")
    events = filter_future_events(events)
    write_scraped(SOURCE_ID, events)


if __name__ == "__main__":
    main()

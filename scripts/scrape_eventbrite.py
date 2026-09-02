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
from pathlib import Path
from urllib.parse import urljoin

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

SOURCE_ID = "eventbrite-boston-latin"


def extract_event_urls(search_html: str) -> set[str]:
    """Event page URLs linked from one Eventbrite search/category page."""
    urls: set[str] = set()
    soup = BeautifulSoup(search_html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if "/e/" in href and "tickets-" in href:
            urls.add(urljoin("https://www.eventbrite.com", href))
    return urls


def discover_event_urls(search_urls: list[str]) -> tuple[list[str], bool]:
    """Crawl Eventbrite search/category pages to collect event URLs.

    Returns (urls, any_search_page_fetched) so the caller can tell "no
    results" from "Eventbrite was unreachable".
    """
    all_urls: set[str] = set()
    any_fetched = False
    for search_url in search_urls:
        print(f"  Searching: {search_url}")
        try:
            html = fetch(search_url, browser=True, timeout=15).text
            any_fetched = True
        except Exception as e:
            print(f"    Failed: {e}")
            continue

        all_urls |= extract_event_urls(html)
        print(f"    Found {len(all_urls)} total unique event URLs so far")
        time.sleep(1.0)

    return sorted(all_urls), any_fetched


_EVENT_TYPES = {
    "SocialEvent", "Event", "DanceEvent", "EducationEvent",
    "MusicEvent", "Festival", "BusinessEvent",
}

def extract_event_jsonld(soup: BeautifulSoup) -> dict | None:
    """Find an event JSON-LD on an Eventbrite page (any schema.org event type)."""
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
    """The shared Latin keyword rule — the same one ingest applies."""
    return mentions_latin(f"{name} {description}")


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
    return parse_event_page(fetch(url, browser=True, timeout=15).text, url)


def parse_event_page(page_html: str, url: str) -> dict | None:
    """Turn an Eventbrite event page's HTML into a DanceEvent dict (or None)."""
    soup = BeautifulSoup(page_html, "html.parser")

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


def fetch_source(source: dict) -> ScrapeResult:
    search_urls = source.get("search_queries", [])

    print("Discovering event URLs from Eventbrite search pages...")
    urls, any_fetched = discover_event_urls(search_urls)
    if not any_fetched:
        raise RuntimeError("no Eventbrite search page could be fetched")
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
                print("    -> skipped (not dance-relevant or no JSON-LD)")
        except Exception as e:
            skipped_error += 1
            print(f"    ERROR: {e}")

        if i < len(urls) - 1:
            time.sleep(0.8)

    print(f"\nResults: {len(events)} events, {skipped_irrelevant} irrelevant, {skipped_error} errors")
    # Health keys on discovered event links: zero on a reachable search page
    # means Eventbrite's result markup changed, not that Boston stopped dancing.
    note = "" if urls else "search pages loaded but no event links found — result markup may have changed"
    return ScrapeResult(events, raw_found=len(urls), note=note)


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())

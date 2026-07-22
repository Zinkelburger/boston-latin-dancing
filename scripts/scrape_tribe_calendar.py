#!/usr/bin/env python3
"""Generic keyword-filtered scraper for "The Events Calendar" municipal/arts sites.

"The Events Calendar" (a.k.a. Modern Tribe / Tribe Events) is the dominant
WordPress events plugin — Somerville Arts Council, countless town arts councils,
libraries, and cultural orgs run it. These calendars are mostly noise for us
(craft fairs, blues shows, yoga, yard sales), so we do NOT want every event in
the pipeline. Instead we:

  1. read the listing page(s) and collect each event's permalink,
  2. fetch that event's own iCal export (``<permalink>ical/``), served as clean
     RFC 5545 with GEO coordinates,
  3. parse it with the shared ICS parser (scrape_ics.parse_ics_feed),
  4. drop everything that doesn't mention Latin social dance (keyword filter),
  5. write only the survivors to data/scraped/<source_id>.json.

Why the per-event iCal and not the bulk ``?ical=1`` export? On many Tribe sites
the bulk export is stuck/cached and returns ancient events regardless of date
params (Somerville's returns 2010–2013 forever), while the HTML listing and the
per-event iCal always reflect the current calendar. If a site's bulk feed *is*
healthy, use the simpler ``scrape_keyword_calendar.py`` instead.

No LLM is needed to reject the noise: a keyword scan does it. The weekly agent
still reviews what survives (via the normal quarantine), but it never has to wade
through a municipal calendar's worth of unrelated events.

Adding a new Tribe calendar is CONFIG-ONLY — no new Python. In data/sources.json:

    {
      "id": "town-arts",
      "type": "keyword-calendar",
      "scraper": "scrape_tribe_calendar.py",
      "name": "Town Arts Council",
      "url": "https://townarts.org/events/",
      "event_path_prefix": "https://townarts.org/events/",   # optional; derived from url
      "listing_urls": [ ...optional extra listing/category pages... ]
    }

Usage: python3 scripts/scrape_tribe_calendar.py <source_id>
"""

import html
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_ics import parse_ics_feed
from scraper_utils import (
    clean_location,
    filter_future_events,
    filter_latin_events,
    geocode,
    get_source,
    record_scrape_health,
    write_scraped,
)

UA = {"User-Agent": "boston-latin-dance-dev/0.1"}
# Slugs under /events/ that are not real events (calendar chrome / taxonomies).
_NON_EVENT_SLUGS = {"categories", "category", "tag", "list", "month", "day", "week", "photo"}


def _fetch(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, headers=UA, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def collect_event_urls(listing_urls: list[str], prefix: str) -> tuple[list[str], bool]:
    """Return (distinct event permalinks, any_listing_fetched).

    ``any_listing_fetched`` lets the caller tell a genuine structure change
    (page loaded but no permalinks) from the site simply being unreachable.
    """
    esc = re.escape(prefix)
    pat = re.compile(rf'href="({esc}[a-z0-9][a-z0-9-]+/)"', re.I)
    seen: dict[str, None] = {}
    any_fetched = False
    for url in listing_urls:
        try:
            html_text = _fetch(url)
            any_fetched = True
        except Exception as exc:
            print(f"  Listing fetch failed for {url}: {exc}")
            continue
        for link in pat.findall(html_text):
            slug = link[len(prefix):].strip("/").split("/")[0]
            if slug and slug not in _NON_EVENT_SLUGS:
                seen.setdefault(link, None)
    return list(seen), any_fetched


def _geo_by_uid(ics_text: str) -> dict[str, tuple[float, float]]:
    """Map each VEVENT's UID to its GEO coordinates, when present.

    The Events Calendar embeds authoritative ``GEO:lat;lng`` in its per-event
    iCal, so we prefer it over re-geocoding the address string.
    """
    coords: dict[str, tuple[float, float]] = {}
    uid = None
    for line in ics_text.splitlines():
        if line.startswith("UID:"):
            uid = line[4:].strip()
        elif line.startswith("GEO:") and uid:
            m = re.match(r"GEO:([\-0-9.]+);([\-0-9.]+)", line.strip())
            if m:
                coords[uid] = (float(m.group(1)), float(m.group(2)))
        elif line.startswith("END:VEVENT"):
            uid = None
    return coords


def _clean_text(value: str) -> str:
    """Unescape HTML entities and strip any stray tags from ICS text."""
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def scrape_event(url: str, source_id: str) -> list[dict]:
    """Fetch and parse one event's iCal into DanceEvent dicts."""
    ics_url = url.rstrip("/") + "/ical/"
    try:
        ics_text = _fetch(ics_url)
    except Exception as exc:
        print(f"  iCal fetch failed for {url}: {exc}")
        return []
    if "BEGIN:VEVENT" not in ics_text:
        return []

    events = parse_ics_feed(ics_text, source_id=source_id)
    geo = _geo_by_uid(ics_text)

    for ev in events:
        ev["name"] = _clean_text(ev.get("name", ""))
        ev["description"] = _clean_text(ev.get("description", ""))
        ev["location"] = clean_location(ev.get("location", ""))
        ev["url"] = url  # the human event page, not the .ics
        if ev.get("lat") is None or ev.get("lng") is None:
            if ev["id"] in geo:
                ev["lat"], ev["lng"] = geo[ev["id"]]
            elif ev.get("location"):
                coords = geocode(ev["location"])
                if coords:
                    ev["lat"], ev["lng"] = coords
    return events


def scrape_source(source_id: str) -> list[dict]:
    source = get_source(source_id)
    if not source or not source.get("enabled"):
        print(f"Source '{source_id}' is disabled or not found in sources.json")
        return []

    prefix = source.get("event_path_prefix")
    if not prefix:
        base = source["url"].split("?")[0].rstrip("/")
        prefix = base + "/"
    listing_urls = source.get("listing_urls") or [source["url"]]

    print(f"[{source_id}] Collecting event links from {len(listing_urls)} listing page(s)...")
    event_urls, listing_fetched = collect_event_urls(listing_urls, prefix)
    print(f"[{source_id}] Found {len(event_urls)} event pages")

    all_events: list[dict] = []
    seen_ids: set[str] = set()
    for url in event_urls:
        for ev in scrape_event(url, source_id):
            if ev.get("id") and ev["id"] not in seen_ids:
                seen_ids.add(ev["id"])
                all_events.append(ev)

    print(f"[{source_id}] Parsed {len(all_events)} events; applying Latin keyword filter")
    latin = filter_latin_events(all_events)
    upcoming = filter_future_events(latin)

    # Health: raw_found is events parsed BEFORE the keyword filter. Zero on a
    # reachable listing means the permalink markup changed and the scraper needs
    # a redesign; the weekly agent surfaces this so we don't silently miss events.
    note = ""
    if listing_fetched and not event_urls:
        note = ("listing page loaded but no event permalinks matched — page markup "
                "may have changed; redesign the scraper")
    record_scrape_health(source_id, len(all_events), len(upcoming),
                         fetched=listing_fetched, note=note)

    write_scraped(source_id, upcoming)
    return upcoming


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/scrape_tribe_calendar.py <source_id>")
        sys.exit(1)
    scrape_source(sys.argv[1])


if __name__ == "__main__":
    main()

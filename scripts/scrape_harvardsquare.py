#!/usr/bin/env python3
"""
Seasonal watcher for Latin social dances on municipal "happenings" calendars
(Harvard Square, optionally Cambridge USA / CambridgeSide).

Why this scraper is deliberately conservative
---------------------------------------------
These are giant civic event calendars (comedy, book readings, movies, concerts
-- hundreds of listings). Latin *social dances* on them are rare: realistically
Harvard Square's annual "Salsa Squared" plus the occasional outdoor summer
salsa night. So this scraper:

  1. Only runs during the outdoor-dance season (``active_months`` in the source
     config -- default June-August). Outside that window it is a no-op, so it
     never adds noise 9 months of the year. See ``main()``.

  2. Filters to events that name a real *social* dance STYLE (salsa, bachata,
     kizomba, zouk, merengue, timba) via ``detect_styles``. This precision
     filter drops Latin *concerts* / listening shows (e.g. "Boma Bango",
     "Angel Subero & Latin Logic") that a bare "latin" keyword would catch.

  3. Corrects a data-quality bug in Harvard Square's calendar: evening parties
     are sometimes encoded with an AM start (a 7 PM social shows startDate
     07:00). See ``_fix_evening_ampm``.

Data source: these sites run "The Events Calendar" (Tribe) on WordPress. Their
REST API and iCal export are Cloudflare-blocked (403), but every listing page
embeds a JSON-LD array of schema.org ``Event`` objects, which we parse directly.

Caveat: the JSON-LD often omits the real event venue (Salsa Squared lists the
organizer's *office* address, not Brattle Plaza) or leaves it blank. New events
therefore go to the pending queue for a human to confirm venue/time before they
hit the map -- this is a watcher, not an unattended publisher.
"""

import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    NY_TZ,
    clean_location,
    detect_styles,
    filter_future_events,
    get_source,
    make_event,
    write_scraped,
)

SOURCE_ID = "harvardsquare"
UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}
DEFAULT_ACTIVE_MONTHS = [6, 7, 8]  # outdoor-dance season; overridable per source


# ── JSON-LD extraction ───────────────────────────────────────────────

def _iter_jsonld_objects(blob):
    """Yield every dict in a parsed JSON-LD blob, flattening arrays and @graph."""
    items = blob if isinstance(blob, list) else [blob]
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("@graph"), list):
            for sub in item["@graph"]:
                if isinstance(sub, dict):
                    yield sub
        else:
            yield item


def parse_jsonld_events(html: str) -> list[dict]:
    """Return all schema.org Event objects embedded in a page's JSON-LD."""
    events = []
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            blob = json.loads(block)
        except json.JSONDecodeError:
            continue
        for obj in _iter_jsonld_objects(blob):
            if obj.get("@type") in ("Event", "SocialEvent", "DanceEvent"):
                events.append(obj)
    return events


def _clean_html(text: str) -> str:
    """Strip tags / unescape entities from a JSON-LD description blob."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    return dt


def _fix_evening_ampm(start, end):
    """Repair Harvard Square's AM/PM encoding bug for evening socials.

    Their calendar sometimes stores an evening start as its AM twin -- a
    7 PM–10 PM party comes through as 07:00–22:00. A social dance that runs
    from the morning until night is not a real thing, so when we see a
    pre-noon start paired with a late-evening end spanning 6+ hours, we shift
    the start into the PM. Daytime festivals (end before 5 PM) and normal
    evening events (start already PM) are left untouched.
    """
    if (
        start and end
        and start.hour < 12
        and end.hour >= 17
        and (end - start) >= timedelta(hours=6)
    ):
        return start + timedelta(hours=12), end
    return start, end


def _jsonld_location(obj: dict) -> str:
    """Best-effort venue string from a JSON-LD Event's location field."""
    loc = obj.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if isinstance(loc, str):
        return clean_location(loc)
    name = loc.get("name") or ""
    addr = loc.get("address") or ""
    if isinstance(addr, dict):
        addr = ", ".join(
            str(addr.get(k, "")).strip()
            for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
            if addr.get(k)
        )
    parts = [p for p in (name, addr) if p]
    return clean_location(", ".join(parts))


def make_event_id(url: str) -> str:
    slug = (url or "").rstrip("/").split("/")[-1]
    if slug:
        return f"{SOURCE_ID}-{slug}"
    return f"{SOURCE_ID}-{hashlib.md5((url or '').encode()).hexdigest()[:8]}"


# ── Detail page (best-effort description) ─────────────────────────────

def fetch_description(url: str) -> str:
    """Pull a readable description from a Tribe Events detail page."""
    try:
        resp = requests.get(url, headers=UA, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"    detail fetch failed: {e}")
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    content = (
        soup.find("div", class_="tribe-events-single-event-description")
        or soup.find("div", class_="tribe-events-content")
        or soup.find("div", class_="entry-content")
    )
    if not content:
        return ""
    text = content.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    return text[:1500].strip()


# ── Main ─────────────────────────────────────────────────────────────

def fetch_events(listing_urls: list[str], defaults: dict) -> list[dict]:
    default_location = defaults.get("location", "")
    seen_ids: set[str] = set()
    events: list[dict] = []

    for listing_url in listing_urls:
        print(f"Fetching {listing_url}")
        try:
            resp = requests.get(listing_url, headers=UA, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  failed: {e}")
            continue

        raw = parse_jsonld_events(resp.text)
        print(f"  {len(raw)} JSON-LD events on page")

        for obj in raw:
            name = re.sub(r"\s+", " ", str(obj.get("name") or "")).strip()
            desc = _clean_html(str(obj.get("description") or ""))
            styles = detect_styles(f"{name} {desc}")
            if styles == ["other"]:
                continue  # not a recognized social-dance style -> skip (concerts, etc.)

            start = _parse_dt(obj.get("startDate"))
            end = _parse_dt(obj.get("endDate")) or start
            if not start:
                continue
            start, end = _fix_evening_ampm(start, end)

            url = obj.get("url") or listing_url
            eid = make_event_id(url)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)

            location = _jsonld_location(obj) or default_location
            description = desc.strip()
            detail = fetch_description(url) if url and "/event/" in url else ""
            if len(detail) > len(description):
                description = detail
            styles = detect_styles(f"{name} {description}")

            event = make_event(
                id=eid,
                name=name,
                start=start,
                end=end,
                location=location,
                description=description,
                url=url,
                styles=styles if styles != ["other"] else None,
                source=SOURCE_ID,
            )
            events.append(event)
            print(
                f"  [keep] {name} | {event['dayOfWeek']} "
                f"{start:%Y-%m-%d %H:%M} | styles={event['styles']} | loc={location!r}"
            )
            time.sleep(0.8)

    return events


def main():
    source = get_source(SOURCE_ID)
    if not source or not source.get("enabled"):
        print(f"Source '{SOURCE_ID}' not found or disabled")
        write_scraped(SOURCE_ID, [])
        return

    active_months = source.get("active_months", DEFAULT_ACTIVE_MONTHS)
    now_month = datetime.now(NY_TZ).month
    if active_months and now_month not in active_months:
        print(
            f"Off-season (month {now_month} not in {active_months}); "
            "skipping Harvard Square scrape."
        )
        write_scraped(SOURCE_ID, [])
        return

    listing_urls = source.get("listing_urls") or [source.get("url")]
    listing_urls = [u for u in listing_urls if u]
    events = fetch_events(listing_urls, source.get("defaults", {}))
    events = filter_future_events(events)
    write_scraped(SOURCE_ID, events)


if __name__ == "__main__":
    main()

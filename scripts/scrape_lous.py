#!/usr/bin/env python3
"""Scrape danceable Latin nights from Lou's (wearelous.com) Squarespace calendar.

Lou's mixes jazz, R&B, listening concerts, and occasional Latin dance nights.
We only keep events people would actually dance at — salsa/bachata/merengue
nights, Latin brunch/socials, and known dance acts like La Diáspora Combo.
Afro-Cuban / bossa / Caribbean dinner concerts (Sugar Kings, etc.) are skipped.

Uses the Squarespace JSON API (`?format=json`); no HTML listing scrape needed.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    NY_TZ,
    filter_future_events,
    get_source,
    make_event,
    record_scrape_health,
    write_scraped,
)

SOURCE_ID = "lous-live"
BASE = "https://www.wearelous.com"
VENUE = "Lou's, 13 Brattle St, Cambridge, MA 02138"
# Squarespace sometimes ships NYC placeholder coords — never trust item.location.
VENUE_LAT = 42.3736
VENUE_LNG = -71.1212

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

# Title must look like a dance night — not a seated Afro-Cuban / bossa concert.
DANCE_NIGHT_RE = re.compile(
    r"\b(salsa|bachata|merengue|kizomba|zouk|timba|mambo|"
    r"latin\s*(?:night|brunch|dance|social|party)|"
    r"dance\s*(?:night|party|social))\b",
    re.I,
)

# Acts that reliably bring a dance floor at Lou's (even if the title is sparse).
KNOWN_DANCE_ACTS_RE = re.compile(
    r"di[aá]spora(?:\s+combo)?|vibra\s*tropical|"
    r"nenas\s*del\s*swing|kristalis",
    re.I,
)

DAY_PREFIX_RE = re.compile(
    r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+(?:brunch|dinner))?\s*[–—\-]\s*",
    re.I,
)
VENUE_SUFFIX_RE = re.compile(
    r"\s*[–—\-]\s*in the (?:performance space|lounge|stage)\s*$|"
    r"\s+in the (?:performance space|lounge|stage)\s*$",
    re.I,
)
DASH_SPLIT_RE = re.compile(r"\s*[–—]\s*")
INSTAGRAM_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)",
    re.I,
)
FACEBOOK_RE = re.compile(
    r"https?://(?:www\.)?facebook\.com/([A-Za-z0-9.]+)",
    re.I,
)


def _clean_title(raw: str) -> str:
    text = html_lib.unescape(raw or "")
    text = text.replace("\xa0", " ").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _ms_to_dt(ms) -> datetime | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).astimezone(NY_TZ)
    except (TypeError, ValueError, OSError):
        return None


def _body_text(body_html: str) -> str:
    if not body_html:
        return ""
    soup = BeautifulSoup(body_html, "html.parser")
    # Drop FAQ / reservation chrome that pads every Lou's post.
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    # Keep the artist blurb; cut common trailing sections.
    cut = re.search(
        r"\n(?:Reservation|FAQs|When Can I Arrive\?|Tickets)\b",
        text,
        re.I,
    )
    if cut:
        text = text[: cut.start()]
    text = re.sub(r"^(?:about the artist)\s*", "", text, flags=re.I).strip()
    return text


def _artist_social_url(*blobs: str) -> str | None:
    """Prefer Instagram; fall back to a Facebook page/profile link."""
    for blob in blobs:
        if not blob:
            continue
        text = html_lib.unescape(blob)
        m = INSTAGRAM_RE.search(text)
        if m:
            handle = m.group(1).rstrip("/")
            if handle.lower() not in {"p", "reel", "stories", "explore"}:
                return f"https://www.instagram.com/{handle}/"
    for blob in blobs:
        if not blob:
            continue
        text = html_lib.unescape(blob)
        m = FACEBOOK_RE.search(text)
        if m:
            handle = m.group(1).rstrip("/")
            if handle.lower() not in {"share", "sharer", "dialog", "events", "watch"}:
                return f"https://www.facebook.com/{handle}"
    return None


def _extract_artist(title: str) -> str | None:
    """Best-effort artist from Lou's 'DAY – theme – Artist – in the room' titles."""
    core = DAY_PREFIX_RE.sub("", title)
    core = VENUE_SUFFIX_RE.sub("", core).strip(" –—-|")
    if not core:
        return None

    parts = [p.strip() for p in DASH_SPLIT_RE.split(core) if p.strip()]
    if len(parts) >= 2:
        # Last segment is usually the act; drop tiny genre-only leftovers.
        candidate = parts[-1]
    else:
        candidate = core

    candidate = re.sub(r"^(?:ft\.?|feat\.?|featuring)\s+", "", candidate, flags=re.I)
    candidate = candidate.strip(" –—-|")
    if len(candidate) < 2:
        return None
    # Title-case lightly for messy ALL CAPS titles without destroying accents.
    if candidate.isupper():
        candidate = candidate.title()
    return candidate


def is_danceable_lous_event(title: str, description: str = "") -> bool:
    """Keep salsa/bachata nights and known dance acts; skip listening concerts."""
    if DANCE_NIGHT_RE.search(title) or KNOWN_DANCE_ACTS_RE.search(title):
        return True
    # Known dance act named only in the body (rare).
    if KNOWN_DANCE_ACTS_RE.search(description) and DANCE_NIGHT_RE.search(description):
        return True
    return False


def _event_id(url_id: str) -> str:
    slug = (url_id or "event").strip("/") or "event"
    short = hashlib.md5(slug.encode()).hexdigest()[:8]
    return f"lous-{short}"


def _absolute_url(path: str) -> str:
    return urljoin(BASE + "/", path.lstrip("/"))


def fetch_events(listing_url: str) -> tuple[list[dict], int]:
    """Return (latin_events, raw_upcoming_count)."""
    json_url = listing_url
    if "format=json" not in json_url:
        sep = "&" if "?" in json_url else "?"
        json_url = f"{listing_url}{sep}format=json"

    resp = requests.get(json_url, headers=UA, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    upcoming = payload.get("upcoming") or []
    if not isinstance(upcoming, list):
        upcoming = []

    events: list[dict] = []
    for item in upcoming:
        title = _clean_title(item.get("title") or "")
        if not title:
            continue

        description = _body_text(item.get("body") or "")
        if not is_danceable_lous_event(title, description):
            print(f"  [skip] {title}")
            continue

        start = _ms_to_dt(item.get("startDate"))
        end = _ms_to_dt(item.get("endDate")) or start
        if not start:
            print(f"  [skip no-date] {title}")
            continue

        path = item.get("fullUrl") or f"/lous-live/{item.get('urlId', '')}"
        url = _absolute_url(path)
        artist = _extract_artist(title)
        artist_url = _artist_social_url(item.get("excerpt") or "", item.get("body") or "")

        # Prefer a readable name: artist + short theme when useful.
        display_name = title
        if artist and artist.lower() not in title.lower()[: len(artist) + 5]:
            display_name = title  # already contains artist
        # Cleaner public name for known patterns.
        if artist:
            theme = DAY_PREFIX_RE.sub("", title)
            theme = VENUE_SUFFIX_RE.sub("", theme).strip()
            # If theme is "Latin Brunch – La diaspora combo", keep as-is cleaned.
            display_name = theme or title

        ev = make_event(
            id=_event_id(item.get("urlId") or item.get("id") or url),
            name=display_name,
            start=start,
            end=end,
            location=VENUE,
            lat=VENUE_LAT,
            lng=VENUE_LNG,
            description=description,
            url=url,
            source=SOURCE_ID,
        )
        if artist:
            ev["artist"] = artist
        if artist_url:
            ev["artistUrl"] = artist_url
        ev["venueId"] = SOURCE_ID

        print(f"  [keep] {ev['startDate'][:16]}  {ev['name']}"
              + (f"  ({artist})" if artist else ""))
        events.append(ev)

    return events, len(upcoming)


def main():
    source = get_source(SOURCE_ID)
    if not source or not source.get("enabled"):
        print(f"Source '{SOURCE_ID}' not found or disabled")
        return

    url = source.get("url") or f"{BASE}/lous-live"
    print(f"Fetching Lou's Live from {url}?format=json")

    try:
        events, raw_found = fetch_events(url)
    except Exception as e:
        print(f"Failed to scrape Lou's: {e}")
        record_scrape_health(SOURCE_ID, raw_found=0, kept=0, fetched=False, note=str(e))
        write_scraped(SOURCE_ID, [])
        return

    events = filter_future_events(events)
    record_scrape_health(SOURCE_ID, raw_found=raw_found, kept=len(events))
    write_scraped(SOURCE_ID, events)


if __name__ == "__main__":
    main()

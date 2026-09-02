#!/usr/bin/env python3
"""Scrape J&L Dance Studio's sitewide upcoming-events announcement bar.

The studio lists official dates (socials, parties, fests they host or promote,
studio closures, workshops) in a Squarespace announcement bar. There is no
JSON-LD Event feed. We parse `websiteSettings.announcementBarSettings` from
`/events?format=json` and keep listings you could show up and dance at —
socials, parties, underground nights, festivals — not classes, workshops,
beginner cycles, or "studio closed" notes.

Date-only listings use midnight local with start === end (do not invent hours),
except J&L Underground Social, whose 7–11pm run-of-show is on the dedicated
social page.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    DAY_NUM_RE,
    MONTH_NAME_RE,
    NY_TZ,
    ScrapeResult,
    fetch,
    make_event,
    month_number,
    resolve_year as _resolve_year_date,
    run_scraper,
    scraper_argparser,
)

SOURCE_ID = "jandl-events"
EVENTS_URL = "https://jandldancestudio.com/events"
UNDERGROUND_URL = "https://jandldancestudio.com/jl-underground-social"
STUDIO = "J&L Dance Studio, 75 Pleasant St #125, Malden, MA 02148"
STUDIO_LAT = 42.4271
STUDIO_LNG = -71.0662

_MONTH = MONTH_NAME_RE
_DAY = DAY_NUM_RE
# "August 17th" | "August 21-23" | "August 31st-September 8th" | "September 9th & 14th"
DATE_HEAD_RE = re.compile(
    rf"^\s*({_MONTH})\s+({_DAY})"
    rf"(?:\s*[-–]\s*(?:({_MONTH})\s+)?({_DAY}))?"
    rf"(?:\s*&\s*({_DAY}))?"
    rf"\s*:?\s*(.*)$",
    re.I | re.S,
)
PROMO_CODE_RE = re.compile(r"\s*[-–—]\s*code\s+(\S+)\s*$", re.I)
SKIP_RE = re.compile(
    r"studio\s+closed|\bclosed\b|beginner\s+cycles?|new\s+cycles?\s+begin",
    re.I,
)
KEEP_RE = re.compile(
    r"\b(practice\s+social|underground|party|parties|fest|festival|"
    r"congress|weekender|practica|baile)\b",
    re.I,
)
SOCIAL_RE = re.compile(r"\bsocials?\b", re.I)
SOCIAL_FALSE_FRIEND_RE = re.compile(
    r"social\s+dance\s+(safety|etiquette|technique|workshop)",
    re.I,
)
WORKSHOP_RE = re.compile(r"\b(workshop|class|classes|technique|lesson|lessons|training)\b", re.I)
STYLE_NIGHT_RE = re.compile(
    r"\b(salsa|bachata|kizomba|merengue|zouk)\b.*\b(night|baile)\b|"
    r"\b(night|baile)\b.*\b(salsa|bachata|kizomba|merengue|zouk)\b",
    re.I,
)
OFFSITE_RE = re.compile(r"\b(fest|festival|congress|weekender)\b", re.I)
STUDIO_HOSTED_RE = re.compile(r"j\s*&\s*l|underground|studio", re.I)


def _ordinal_day(token: str) -> int:
    return int(re.sub(r"(?:st|nd|rd|th)$", "", token, flags=re.I))


def _month_num(token: str) -> int:
    month = month_number(token)
    if month is None:
        raise ValueError(f"not a month: {token!r}")
    return month


def resolve_year(month: int, day: int, now: datetime) -> datetime | None:
    """Attach a year (shared rollover rule): dates more than a week in the
    past roll to next year, unless that lands more than ~6 months out — a
    stale leftover on the bar. Returns a midnight Eastern datetime."""
    when = _resolve_year_date(month, day, now)
    if when is None:
        return None
    return datetime(when.year, when.month, when.day, tzinfo=NY_TZ)


def parse_date_head(date_and_title: str, now: datetime) -> list[dict]:
    """Return [{start, end, title}, ...] for one announcement line."""
    m = DATE_HEAD_RE.match(date_and_title.strip())
    if not m:
        return []
    month_a, day_a, month_b, day_b, day_and, rest = m.groups()
    title = re.sub(r"\s+", " ", (rest or "").strip())
    start_month = _month_num(month_a)
    start_day = _ordinal_day(day_a)
    start = resolve_year(start_month, start_day, now)
    if start is None:
        return []

    # "Sep 9th & 14th" → two dated copies of the same title
    if day_and and not day_b:
        second = resolve_year(start_month, _ordinal_day(day_and), now)
        out = [{"start": start, "end": start, "title": title}]
        if second is not None:
            out.append({"start": second, "end": second, "title": title})
        return out

    if day_b:
        end_month = _month_num(month_b) if month_b else start_month
        end_day = _ordinal_day(day_b)
        end = resolve_year(end_month, end_day, now)
        if end is None:
            end = start
        # Range that wraps the year (Dec 28–Jan 3) after start resolved to this Dec.
        if end < start:
            try:
                end = datetime(start.year + 1, end_month, end_day, tzinfo=NY_TZ)
            except ValueError:
                end = start
        # "August 21-23" names an inclusive last day; store it the way the
        # calendar feeds do — iCalendar's DTEND for an all-day event is
        # exclusive — so the UI can render one date range from either source.
        if end > start:
            end += timedelta(days=1)
        return [{"start": start, "end": end, "title": title}]

    return [{"start": start, "end": start, "title": title}]


def is_danceable(title: str) -> bool:
    """Keep listings you could show up and dance at; drop classes and closures."""
    if not title or SKIP_RE.search(title):
        return False
    if SOCIAL_FALSE_FRIEND_RE.search(title):
        return False
    if KEEP_RE.search(title):
        return True
    if SOCIAL_RE.search(title) and not WORKSHOP_RE.search(title):
        return True
    if STYLE_NIGHT_RE.search(title) and not WORKSHOP_RE.search(title):
        return True
    return False


def split_titles(title: str) -> list[str]:
    """One bar line can pack several same-day items separated by semicolons."""
    parts = [p.strip(" .") for p in title.split(";")]
    return [p for p in parts if p]


def strip_promo(title: str) -> tuple[str, str | None]:
    m = PROMO_CODE_RE.search(title)
    if not m:
        return title.strip(), None
    return title[: m.start()].strip(), m.group(1).strip()


def slug_id(start: datetime, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    event_id = f"jandl-{start.strftime('%Y%m%d')}-{slug}"
    if len(event_id) > 64:
        event_id = f"jandl-{start.strftime('%Y%m%d')}-{hashlib.sha1(name.encode()).hexdigest()[:10]}"
    return event_id


def extract_announcement_html(page_json: dict) -> str:
    settings = (page_json.get("websiteSettings") or {}).get("announcementBarSettings") or {}
    return settings.get("text") or ""


def parse_announcement_items(announcement_html: str, now: datetime) -> list[dict]:
    """Parse dated list items from the announcement-bar HTML.

    Returns raw dated rows (including workshops/closures) so scrape health can
    tell 'bar is empty/unparseable' from 'nothing danceable this week'.
    """
    if not announcement_html:
        return []
    soup = BeautifulSoup(announcement_html, "html.parser")
    items = []
    for li in soup.find_all("li"):
        text = html_lib.unescape(li.get_text(" ", strip=True))
        text = re.sub(r"\s+", " ", text).strip()
        items.extend(parse_date_head(text, now))
    if items:
        return items
    # Fallback: no <li>, try paragraph lines.
    for node in soup.find_all(["p", "div"]):
        text = html_lib.unescape(node.get_text(" ", strip=True))
        text = re.sub(r"\s+", " ", text).strip()
        items.extend(parse_date_head(text, now))
    return items


def _is_offsite(name: str) -> bool:
    return bool(OFFSITE_RE.search(name)) and not STUDIO_HOSTED_RE.search(name)


def _is_underground(name: str) -> bool:
    return bool(re.search(r"underground", name, re.I))


def row_to_event(start: datetime, end: datetime, title: str, listing_url: str) -> dict | None:
    name, promo = strip_promo(title)
    if not name or not is_danceable(name):
        return None

    underground = _is_underground(name)
    offsite = _is_offsite(name)

    if underground:
        # Standing run-of-show is on the dedicated social page, not guessed.
        start = start.replace(hour=19, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=0)
        location = STUDIO
        lat, lng = STUDIO_LAT, STUDIO_LNG
        url = UNDERGROUND_URL
        cost = "$15"
        description = (
            "Listed on J&L Dance Studio's upcoming-events bar.\n\n"
            "J&L Underground Social at J&L Dance Studio, 75 Pleasant Street #125, "
            "1st Floor, Malden, MA.\n"
            "7:00pm–8:00pm Bachata Footwork Challenge (or beginner crash course).\n"
            "8:00pm–11:00pm social dancing (bachata with salsa/kizomba/merengue).\n"
            "$15 cash at the door."
        )
        styles = ["bachata", "salsa", "kizomba", "merengue"]
    elif offsite:
        # The bar gives a title and a link, never a venue. "Boston, MA" is the
        # honest answer, but geocoding it pins the event on City Hall, so ship
        # it without coordinates rather than inventing an address.
        location = "Boston, MA"
        lat = lng = None
        url = listing_url
        cost = None
        description = (
            f"Listed on J&L Dance Studio's upcoming-events bar as {name}. "
            "This is an event J&L is promoting — venue is not the Malden studio."
        )
        if promo:
            description += f" J&L promo code: {promo}."
        styles = None
    else:
        # Date-only studio listing — do not invent hours.
        location = STUDIO
        lat, lng = STUDIO_LAT, STUDIO_LNG
        url = listing_url
        cost = None
        description = (
            f"Listed on J&L Dance Studio's upcoming-events bar: {name}.\n"
            f"J&L Dance Studio, 75 Pleasant Street #125, Malden, MA."
        )
        styles = None

    ev = make_event(
        id=slug_id(start, name),
        name="J&L Underground Social" if underground else name,
        start=start,
        end=end,
        location=location,
        lat=lat,
        lng=lng,
        description=description,
        url=url,
        styles=styles,
        cost=cost,
        source=SOURCE_ID,
        venue_unknown=offsite,
    )
    ev["organizer"] = "J&L Dance Studio"
    return ev


def items_to_events(items: list[dict], listing_url: str) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for item in items:
        for title in split_titles(item["title"]):
            ev = row_to_event(item["start"], item["end"], title, listing_url)
            if not ev or ev["id"] in seen:
                continue
            seen.add(ev["id"])
            events.append(ev)
            print(f"  [keep] {ev['startDate'][:10]}  {ev['name']}")
    return events


def fetch_page_json(listing_url: str) -> dict:
    resp = fetch(
        listing_url,
        browser=True,
        params={"format": "json"},
        headers={"Accept": "application/json, text/html;q=0.9"},
        timeout=20,
    )
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Squarespace did not return JSON for {listing_url}") from e


def fetch_events(listing_url: str, now: datetime | None = None) -> tuple[list[dict], int]:
    now = now or datetime.now(NY_TZ)
    page = fetch_page_json(listing_url)
    html = extract_announcement_html(page)
    raw_items = parse_announcement_items(html, now)
    print(f"Found {len(raw_items)} dated announcement item(s)")
    for item in raw_items:
        flag = "keep" if any(is_danceable(t) for t in split_titles(item["title"])) else "skip"
        print(f"  [{flag}] {item['start'].strftime('%Y-%m-%d')}  {item['title']}")
    events = items_to_events(raw_items, listing_url)
    return events, len(raw_items)


def fetch_source(source: dict) -> ScrapeResult:
    listing_url = source.get("url") or EVENTS_URL
    print(f"Fetching J&L announcement bar from {listing_url}?format=json")
    events, raw_found = fetch_events(listing_url)
    return ScrapeResult(events, raw_found=raw_found)


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())

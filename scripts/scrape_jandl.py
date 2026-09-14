#!/usr/bin/env python3
"""Scrape Buena Vibra Dance Studio's (formerly J&L Dance Studio) socials page.

The studio rebranded in September 2026: jandldancestudio.com now redirects to
buenavibradance.com, the sitewide announcement bar this scraper used to parse
is gone, and there is no JSON-LD Event feed. Upcoming socials are announced on
one static page, ``/socials``, as prose::

    Upcoming Social Oct. 3rd
    COST: $15 CASH ONLY
    TIME: 7-11PM
    MUSIC FORMAT: 70% BACHATA, 30% SALSA

We read the dates off the "Upcoming Social" heading and the run-of-show from
the labelled lines. Nothing here is a class, so every dated row is an event.
Scrape health keys on the heading: a page that loads but has no "Upcoming
Social" line is ``structure_missing``, not "no socials this month".
"""

from __future__ import annotations

import html as html_lib
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

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
SOCIALS_URL = "https://buenavibradance.com/socials"
STUDIO = "Buena Vibra Dance Studio (formerly J&L), 75 Pleasant St, Suite 125, Malden, MA 02148"
STUDIO_LAT = 42.4271
STUDIO_LNG = -71.0662
SOCIAL_NAME = "Buena Vibra Dance Social"
ORGANIZER = "Buena Vibra Dance Studio"
DEFAULT_HOURS = (19, 23)  # standing 7–11 PM run-of-show, used only if TIME: is absent

_MONTH_DAY = rf"{MONTH_NAME_RE}\.?\s+{DAY_NUM_RE}"
UPCOMING_RE = re.compile(
    rf"Upcoming\s+Socials?\s*:?\s*((?:{_MONTH_DAY}(?:\s*(?:,|&|and|\+|/)\s*)?)+)",
    re.I,
)
MONTH_DAY_RE = re.compile(rf"({MONTH_NAME_RE})\.?\s+({DAY_NUM_RE})", re.I)
TIME_RE = re.compile(
    r"TIME\s*:\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–to]+\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.I,
)
COST_RE = re.compile(r"COST\s*:\s*(\$\s?\d+(?:\.\d{2})?(?:\s+cash(?:\s+only)?)?)", re.I)
FORMAT_RE = re.compile(r"MUSIC\s+FORMAT\s*:\s*((?:\d+%\s*[A-Za-z]+[,\s]*)+)", re.I)
STYLE_WORDS = ("bachata", "salsa", "kizomba", "merengue", "zouk", "cumbia")


def page_text(html: str) -> str:
    """Visible text of a Squarespace page, whitespace collapsed."""
    text = re.sub(r"<(script|style).*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


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
    stale leftover on the page. Returns a midnight Eastern datetime."""
    when = _resolve_year_date(month, day, now)
    if when is None:
        return None
    return datetime(when.year, when.month, when.day, tzinfo=NY_TZ)


def parse_hours(text: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """``TIME: 7-11PM`` → ((19, 0), (23, 0)). A start with no am/pm borrows
    the end's; an end that lands before the start crosses midnight."""
    m = TIME_RE.search(text)
    if not m:
        return None
    sh, sm, sap, eh, em, eap = m.groups()
    sap = (sap or eap).lower()
    eap = eap.lower()

    def to24(h: str, ap: str) -> int:
        h = int(h) % 12
        return h + 12 if ap == "pm" else h

    return (to24(sh, sap), int(sm or 0)), (to24(eh, eap), int(em or 0))


def parse_socials_page(html: str, now: datetime) -> list[dict]:
    """Return one raw row per date on the "Upcoming Social" heading.

    Rows carry the page's run-of-show (hours, cost, music format) so the
    caller never invents them; missing pieces are ``None``.
    """
    text = page_text(html)
    m = UPCOMING_RE.search(text)
    if not m:
        return []
    hours = parse_hours(text)
    cost_m = COST_RE.search(text)
    cost = re.sub(r"\s+", " ", cost_m.group(1)).strip() if cost_m else None
    fmt_m = FORMAT_RE.search(text)
    music_format = fmt_m.group(1).strip().rstrip(",").strip() if fmt_m else None

    rows: list[dict] = []
    seen: set[str] = set()
    for month_tok, day_tok in MONTH_DAY_RE.findall(m.group(1)):
        start = resolve_year(_month_num(month_tok), _ordinal_day(day_tok), now)
        if start is None or start.isoformat() in seen:
            continue
        seen.add(start.isoformat())
        rows.append({"date": start, "hours": hours, "cost": cost, "music_format": music_format})
    return rows


def _styles_from_format(music_format: str | None) -> list[str] | None:
    if not music_format:
        return None
    found = [s for s in STYLE_WORDS if re.search(rf"\b{s}\b", music_format, re.I)]
    return found or None


def slug_id(start: datetime) -> str:
    return f"jandl-{start.strftime('%Y%m%d')}-buena-vibra-social"


def row_to_event(row: dict, listing_url: str) -> dict:
    day: datetime = row["date"]
    (sh, sm), (eh, em) = row["hours"] or ((DEFAULT_HOURS[0], 0), (DEFAULT_HOURS[1], 0))
    start = day.replace(hour=sh, minute=sm)
    end = day.replace(hour=eh, minute=em)
    if end <= start:
        end += timedelta(days=1)

    lines = [
        f"Monthly social at {ORGANIZER} (formerly J&L Dance Studio / J&L Underground Social), "
        "75 Pleasant Street, Suite 125, 1st Floor, Malden, MA.",
    ]
    if row["music_format"]:
        lines.append(f"Music format: {row['music_format']}.")
    if row["cost"]:
        lines.append(f"Cost: {row['cost']}.")
    lines.append(
        "Ample street parking free after 7pm; CBD and Jackson Street garages $1.25/hour. "
        "5 minute walk from Malden Center (Orange Line). All levels welcome."
    )

    ev = make_event(
        id=slug_id(start),
        name=SOCIAL_NAME,
        start=start,
        end=end,
        location=STUDIO,
        lat=STUDIO_LAT,
        lng=STUDIO_LNG,
        description="\n".join(lines),
        url=listing_url,
        styles=_styles_from_format(row["music_format"]),
        cost=row["cost"],
        source=SOURCE_ID,
    )
    ev["organizer"] = ORGANIZER
    return ev


def fetch_events(listing_url: str, now: datetime | None = None) -> tuple[list[dict], int]:
    now = now or datetime.now(NY_TZ)
    html = fetch(listing_url, browser=True, timeout=20).text
    rows = parse_socials_page(html, now)
    print(f"Found {len(rows)} dated social(s) on the Upcoming Social heading")
    events = [row_to_event(row, listing_url) for row in rows]
    for ev in events:
        print(f"  [keep] {ev['startDate'][:16]}  {ev['name']}  {ev.get('cost') or ''}")
    return events, len(rows)


def fetch_source(source: dict) -> ScrapeResult:
    listing_url = source.get("url") or SOCIALS_URL
    print(f"Fetching Buena Vibra socials page {listing_url}")
    events, raw_found = fetch_events(listing_url)
    note = "" if raw_found else "page loaded but has no 'Upcoming Social' heading — layout changed?"
    return ScrapeResult(events, raw_found=raw_found, note=note)


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())

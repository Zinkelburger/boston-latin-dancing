#!/usr/bin/env python3
"""
Scrape Latin-dance events from the Hatch Shell / Esplanade season calendar.

https://www.hatchshell.com/events.php is a hand-rolled single-page season list
(mostly walks, runs, concerts, movie nights) with the occasional salsa event.
Same strategy as scrape_somerville_arts.py: parse the whole page, keyword-filter
to Latin events, and emit only those — the municipal noise never enters the
pipeline and no LLM is needed to reject a 5K walk.

The page has no ICS/JSON feed, so this parses its fixed HTML shape: each event is
a `<div class='event_desc'>` block of

    <b>Event:</b> <title>
    <br><b>Date:</b> MON DD
    <br><b>Time:</b> 1:00pm-3:00pm
    <p class=event_link> <description> </p>
    <a href=...> (optional)

Dates carry no year (it's a single-season page), so we assume the current year
and roll forward only when a date is far enough in the past to clearly belong to
next season; filter_future_events then drops anything still past.

Fail-safe: any fetch/parse error yields an empty scrape rather than raising, so a
flaky page can never break the weekly pipeline (run_pipeline records the failure
and moves on).

Usage: python3 scripts/scrape_hatch_shell.py [source_id]
"""

import hashlib
import html
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    NY_TZ,
    clean_location,
    detect_styles,
    extract_cost,
    filter_future_events,
    filter_latin_events,
    geocode,
    get_source,
    make_event,
    record_scrape_health,
    write_scraped,
)

DEFAULT_SOURCE_ID = "hatch-shell"
UA = {"User-Agent": "boston-latin-dance-dev/0.1"}
DEFAULT_LOCATION = "Hatch Memorial Shell, Esplanade, Boston, MA"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _field(block: str, label: str) -> str:
    """Value of a `<b>Label:</b> value` field, up to the next tag/line break."""
    m = re.search(rf"<b>\s*{label}\s*:\s*</b>\s*([^<\n]+)", block, re.I)
    return m.group(1).strip() if m else ""


def _parse_time(time_str: str) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """First and (optional) second time in a string like '6:00pm-8:00pm'."""
    times = re.findall(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)", time_str, re.I)
    parsed = []
    for hh, mm, ap in times[:2]:
        h = int(hh) % 12
        if ap.lower() == "pm":
            h += 12
        parsed.append((h, int(mm or 0)))
    start = parsed[0] if parsed else None
    end = parsed[1] if len(parsed) > 1 else None
    return start, end


def _resolve_year(month: int, day: int, now: datetime) -> int:
    """Pick the season year for a month/day that carries no year.

    Assume the current year; if that date is already far in the past (well before
    this run, so it clearly belongs to a later-published season), roll forward one
    year. Recent-past events of the current season stay in the current year and
    get dropped by the future filter.
    """
    year = now.year
    try:
        candidate = datetime(year, month, day, tzinfo=NY_TZ)
    except ValueError:
        return year
    if candidate < now - timedelta(days=180):
        return year + 1
    return year


def parse_events(page_html: str, source_id: str) -> list[dict]:
    """Parse the season page into DanceEvent dicts (best-effort per event)."""
    now = datetime.now(NY_TZ)
    # Each event block runs from one "Event:" label to the next.
    blocks = re.split(r"(?=<b>\s*Event\s*:\s*</b>)", page_html, flags=re.I)
    events: list[dict] = []
    for block in blocks:
        if not re.search(r"<b>\s*Event\s*:\s*</b>", block, re.I):
            continue
        try:
            title = _strip_tags(_field(block, "Event"))
            date_str = _field(block, "Date")
            time_str = _field(block, "Time")
            if not title or not date_str:
                continue

            m = re.match(r"([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2})", date_str.strip())
            if not m:
                continue
            month = _MONTHS.get(m.group(1).lower())
            day = int(m.group(2))
            if not month:
                continue
            year = _resolve_year(month, day, now)

            start_hm, end_hm = _parse_time(time_str)
            sh, sm = start_hm or (18, 0)  # sensible evening default if time is TBA
            start = datetime(year, month, day, sh, sm, tzinfo=NY_TZ)
            if end_hm:
                eh, em = end_hm
                end = datetime(year, month, day, eh, em, tzinfo=NY_TZ)
                if end < start:  # crosses midnight
                    end += timedelta(days=1)
            else:
                end = start + timedelta(hours=2)

            desc_m = re.search(r"<p[^>]*class=[\"']?event_link[\"']?[^>]*>(.*?)</p>", block, re.S | re.I)
            description = _strip_tags(desc_m.group(1)) if desc_m else ""
            link_m = re.search(r"<a[^>]+href=[\"']?(https?://[^\s\"'>]+)", block, re.I)
            url = link_m.group(1) if link_m else None

            ev_id = f"{source_id}-{hashlib.sha1(f'{title}{month:02d}{day:02d}'.encode()).hexdigest()[:12]}"
            combined = f"{title} {description}"
            ev = make_event(
                id=ev_id,
                name=title,
                start=start,
                end=end,
                location=DEFAULT_LOCATION,
                description=description,
                url=url,
                styles=detect_styles(combined),
                cost=extract_cost(combined),
                source=source_id,
            )
            if ev.get("lat") is None or ev.get("lng") is None:
                coords = geocode(DEFAULT_LOCATION)
                if coords:
                    ev["lat"], ev["lng"] = coords
            events.append(ev)
        except Exception as exc:
            print(f"  Skipped an event block: {exc}")
            continue
    return events


def scrape_source(source_id: str) -> list[dict]:
    source = get_source(source_id)
    if not source or not source.get("enabled"):
        print(f"Source '{source_id}' is disabled or not found in sources.json")
        return []

    url = source["url"]
    print(f"[{source_id}] Fetching {url}")
    try:
        resp = requests.get(url, headers=UA, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[{source_id}] Fetch failed: {exc} — emitting nothing")
        record_scrape_health(source_id, 0, 0, fetched=False,
                             note=f"fetch failed: {exc}")
        write_scraped(source_id, [])
        return []

    events = parse_events(resp.text, source_id)
    print(f"[{source_id}] Parsed {len(events)} events; applying Latin keyword filter")
    latin = filter_latin_events(events)
    upcoming = filter_future_events(latin)

    # Health: raw_found is events parsed BEFORE keyword/future filters. Zero on a
    # page that loaded means the <b>Event:</b>… markup changed and the parser
    # needs a redesign. (kept==0 with raw_found>0 is normal — just no upcoming
    # Latin events, e.g. between seasons.)
    note = ""
    if not events:
        note = "page loaded but no event blocks matched — HTML structure may have changed; redesign the scraper"
    record_scrape_health(source_id, len(events), len(upcoming), note=note)

    write_scraped(source_id, upcoming)
    return upcoming


def main():
    source_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE_ID
    scrape_source(source_id)


if __name__ == "__main__":
    main()

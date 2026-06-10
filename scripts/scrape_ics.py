#!/usr/bin/env python3
"""
Scrape events from an ICS (iCalendar) feed.

Replaces the old fetch-ics.ts. Uses the `icalendar` library for proper RFC 5545
parsing (including RRULE expansion via `dateutil`). Reads URL from sources.json.

Outputs: data/scraped/beatrice-calendar.json
"""

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from icalendar import Calendar
from dateutil.rrule import rrulestr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    detect_styles,
    extract_cost,
    geocode,
    get_source,
    make_event,
    write_scraped,
    DAYS,
)

DEFAULT_SOURCE_ID = "beatrice-calendar"

RRULE_HORIZON_WEEKS = 12


def _ical_dt_to_datetime(dt_val) -> datetime | None:
    """Convert an icalendar date/datetime to a tz-aware Python datetime."""
    if dt_val is None:
        return None
    d = dt_val.dt if hasattr(dt_val, "dt") else dt_val
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return None


def _unescape_ics(text: str) -> str:
    """Unescape ICS text values."""
    return (
        text.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _fix_rrule_until(rule_str: str) -> str:
    """Fix UNTIL values that dateutil rejects when DTSTART is timezone-aware.

    Google Calendar sometimes emits date-only UNTIL (e.g. UNTIL=20260625) or
    local-time UNTIL without a Z suffix. dateutil requires UTC datetime format.
    """
    def _to_utc(m: re.Match) -> str:
        val = m.group(1)
        if len(val) == 8:
            return f"UNTIL={val}T235959Z"
        if len(val) == 15:
            return f"UNTIL={val}Z"
        return m.group(0)

    return re.sub(r"UNTIL=(\d{8}T\d{6}|(\d{8}))(?![\dTZ])", _to_utc, rule_str)


def parse_ics_feed(ics_text: str, source_id: str = DEFAULT_SOURCE_ID) -> list[dict]:
    """Parse an ICS feed and return a list of DanceEvent dicts."""
    cal = Calendar.from_ical(ics_text)
    now = datetime.now(timezone.utc) - timedelta(days=1)
    horizon = now + timedelta(weeks=RRULE_HORIZON_WEEKS)
    events: list[dict] = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("uid", f"ics-{len(events)}"))
        summary = str(component.get("summary", "Untitled Event"))
        raw_desc = component.get("description")
        description = _unescape_ics(str(raw_desc)) if raw_desc else ""
        raw_loc = component.get("location")
        location = _unescape_ics(str(raw_loc)) if raw_loc else ""

        url_val = component.get("url")
        url = str(url_val) if url_val else None
        if url and url.startswith("fb://"):
            url = None

        dtstart = _ical_dt_to_datetime(component.get("dtstart"))
        dtend = _ical_dt_to_datetime(component.get("dtend"))
        if dtstart is None:
            continue

        rrule = component.get("rrule")
        is_recurring = rrule is not None

        occurrences: list[tuple[datetime, datetime]] = []

        if rrule:
            try:
                duration = (dtend - dtstart) if dtend else timedelta(hours=2)
                rule_str = rrule.to_ical().decode("utf-8")
                rule_str = _fix_rrule_until(rule_str)
                rule = rrulestr(rule_str, dtstart=dtstart)
                for occ_start in rule.between(now, horizon, inc=True):
                    if occ_start.tzinfo is None:
                        occ_start = occ_start.replace(tzinfo=dtstart.tzinfo or timezone.utc)
                    occurrences.append((occ_start, occ_start + duration))
            except Exception as e:
                print(f"  RRULE error for '{summary}': {e}")
                if dtstart >= now:
                    occurrences.append((dtstart, dtend or dtstart))
        else:
            if dtstart >= now:
                occurrences.append((dtstart, dtend or dtstart))

        if not occurrences:
            continue

        combined = f"{summary} {description}"
        styles = detect_styles(combined)
        cost = extract_cost(combined)

        if len(occurrences) == 1:
            start, end = occurrences[0]
            ev = make_event(
                id=uid,
                name=summary,
                start=start,
                end=end,
                location=location,
                description=description,
                url=url,
                styles=styles,
                cost=cost,
                recurring=is_recurring,
                source=source_id,
            )
            events.append(ev)
        else:
            start, end = occurrences[0]
            ev = make_event(
                id=uid,
                name=summary,
                start=start,
                end=end,
                location=location,
                description=description,
                url=url,
                styles=styles,
                cost=cost,
                recurring=True,
                source=source_id,
            )
            ev["recurrences"] = [s.isoformat() for s, _ in occurrences]
            events.append(ev)

    return events


def scrape_ics_source(source_id: str) -> list[dict]:
    """Fetch and parse a single ICS source. Returns the event list."""
    source = get_source(source_id)
    if not source or not source.get("enabled"):
        print(f"Source '{source_id}' is disabled or not found in sources.json")
        return []

    ics_url = source["url"]
    print(f"[{source_id}] Fetching ICS feed from {ics_url[:80]}...")

    resp = requests.get(
        ics_url,
        headers={"User-Agent": "boston-latin-dance-dev/0.1"},
        timeout=30,
    )
    resp.raise_for_status()
    print(f"[{source_id}] Fetched {len(resp.text)} bytes")

    events = parse_ics_feed(resp.text, source_id=source_id)
    print(f"[{source_id}] Parsed {len(events)} future events")

    with_coords = sum(1 for e in events if e.get("lat") and e.get("lng"))
    print(f"  {with_coords} with coordinates, {len(events) - with_coords} without")

    style_counts: dict[str, int] = {}
    for e in events:
        for s in e.get("styles", []):
            style_counts[s] = style_counts.get(s, 0) + 1
    print(f"  Styles: {style_counts}")

    write_scraped(source_id, events)
    return events


def main():
    source_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE_ID
    scrape_ics_source(source_id)


if __name__ == "__main__":
    main()

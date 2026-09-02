#!/usr/bin/env python3
"""
Scrape events from an ICS (iCalendar) feed.

Uses the `icalendar` library for proper RFC 5545 parsing (including RRULE
expansion via `dateutil`). Reads the feed URL from sources.json.

Usage: python3 scripts/scrape_ics.py [<source_id>]   (default: beatrice-calendar)
Outputs: data/scraped/<source_id>.json
"""

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from icalendar import Calendar
from dateutil.rrule import rrulestr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import (
    NY_TZ,
    ScrapeResult,
    detect_styles,
    extract_cost,
    fetch,
    make_event,
    run_scraper,
    scraper_argparser,
)

DEFAULT_SOURCE_ID = "beatrice-calendar"

RRULE_HORIZON_WEEKS = 12


def _ical_dt_to_datetime(dt_val) -> datetime | None:
    """Convert an icalendar date/datetime to a tz-aware Python datetime.

    A naive (floating) ICS time is local wall-clock, so it is interpreted as
    Eastern — never UTC.
    """
    if dt_val is None:
        return None
    d = dt_val.dt if hasattr(dt_val, "dt") else dt_val
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=NY_TZ)
        return d
    if isinstance(d, date):
        return datetime(d.year, d.month, d.day, tzinfo=NY_TZ)
    return None


def _unescape_ics(text: str) -> str:
    """Unescape ICS text values."""
    return (
        text.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _fix_rrule_until(rule_str: str, tz=NY_TZ) -> str:
    """Rewrite UNTIL values dateutil rejects when DTSTART is timezone-aware.

    Google Calendar sometimes emits a date-only UNTIL (``UNTIL=20260625``) or a
    floating local-time UNTIL without a Z suffix. dateutil requires a UTC
    datetime. A floating UNTIL is *local wall-clock* (the series' timezone),
    so it is localized to ``tz`` and then converted to UTC — simply appending
    "Z" would treat 23:59:59 Eastern as 23:59:59 UTC, four or five hours
    earlier, and silently drop the series' last occurrence.
    """
    def _to_utc(m: re.Match) -> str:
        val = m.group(1)
        if len(val) == 8:
            local = datetime.strptime(val, "%Y%m%d").replace(
                hour=23, minute=59, second=59, tzinfo=tz)
        elif len(val) == 15:
            local = datetime.strptime(val, "%Y%m%dT%H%M%S").replace(tzinfo=tz)
        else:
            return m.group(0)
        return "UNTIL=" + local.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    return re.sub(r"UNTIL=(\d{8}T\d{6}|\d{8})(?![\dTZ])", _to_utc, rule_str)


def parse_ics_feed(
    ics_text: str,
    source_id: str = DEFAULT_SOURCE_ID,
    now: datetime | None = None,
) -> list[dict]:
    """Parse an ICS feed and return a list of upcoming DanceEvent dicts.

    ``now`` (tz-aware) pins the "upcoming" window for tests; it defaults to
    the current UTC time.
    """
    cal = Calendar.from_ical(ics_text)
    now = (now or datetime.now(timezone.utc)) - timedelta(days=1)
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
        fb_url = None
        if url and url.startswith("fb://"):
            # fb:// deep links don't open in a browser; keep the web event
            # page as a fallback but prefer a link written in the description.
            m = re.search(r"id=(\d+)", url)
            fb_url = f"https://www.facebook.com/events/{m.group(1)}/" if m else None
            url = None
        if not url:
            m = re.search(r"https?://[^\s<>\"'\)\]]+", description)
            url = m.group(0).rstrip(".,;!") if m else fb_url
        if url:
            url = re.sub(r"fbclid=[^&]+&?", "", url).rstrip("?&")

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
                rule_str = _fix_rrule_until(rule_str, tz=dtstart.tzinfo or NY_TZ)
                rule = rrulestr(rule_str, dtstart=dtstart)
                for occ_start in rule.between(now, horizon, inc=True):
                    if occ_start.tzinfo is None:
                        occ_start = occ_start.replace(tzinfo=dtstart.tzinfo or NY_TZ)
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
            recurring=is_recurring or len(occurrences) > 1,
            source=source_id,
        )
        if len(occurrences) > 1:
            ev["recurrences"] = [s.isoformat() for s, _ in occurrences]
        events.append(ev)

    return events


def fetch_source(source: dict) -> ScrapeResult:
    """Fetch and parse one ICS source from sources.json."""
    source_id = source["id"]
    ics_url = source["url"]
    print(f"[{source_id}] Fetching ICS feed from {ics_url[:80]}...")

    ics_text = fetch(ics_url, timeout=30).text
    print(f"[{source_id}] Fetched {len(ics_text)} bytes")

    events = parse_ics_feed(ics_text, source_id=source_id)
    print(f"[{source_id}] Parsed {len(events)} future events")

    with_coords = sum(1 for e in events if e.get("lat") and e.get("lng"))
    print(f"  {with_coords} with coordinates, {len(events) - with_coords} without")

    style_counts: dict[str, int] = {}
    for e in events:
        for s in e.get("styles", []):
            style_counts[s] = style_counts.get(s, 0) + 1
    print(f"  Styles: {style_counts}")

    # Health keys on VEVENTs in the feed, not on how many are upcoming: an
    # all-past calendar is still structurally fine.
    raw_found = ics_text.count("BEGIN:VEVENT")
    note = "" if raw_found else "feed loaded but contains no VEVENTs — is the calendar still public?"
    return ScrapeResult(events, raw_found=raw_found, note=note)


def main(argv: list[str] | None = None) -> int:
    args = scraper_argparser(__doc__, default_source_id=DEFAULT_SOURCE_ID).parse_args(argv)
    return run_scraper(args.source_id, fetch_source)


if __name__ == "__main__":
    sys.exit(main())

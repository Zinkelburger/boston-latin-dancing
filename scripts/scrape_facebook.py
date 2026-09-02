#!/usr/bin/env python3
"""
Generic Facebook events scraper for any page with an Events tab.

Works for BOBAS, Dante's Salsa Inferno, or any FB page listed in sources.json
with type "facebook".

No browser MCP? Headless Chrome renders the public events tabs without login:

  google-chrome --headless=new --disable-gpu --no-sandbox \
    --virtual-time-budget=15000 --dump-dom <facebook_events_url> > page.html

Strip tags and read the event cards ("Upcoming"/"Past" sections, card text like
"Fri, Jul 10 <name> · Cambridge"); individual event pages give exact date/time
in og: meta tags and visible text. Build the raw JSON below from that, then run
this script.

Designed to be run by an agent with browser MCP (or the headless fallback):

  1. Navigate to the page's facebook_events_url
  2. Close the login dialog (click the X)
  3. Check for an "Upcoming" tab; if it exists, click event links to get details
  4. Extract event data from the accessibility snapshot
  5. Write raw JSON to data/scraped/<source_id>-raw.json
  6. This script normalizes it into data/scraped/<source_id>.json

Usage:
  python3 scripts/scrape_facebook.py <source_id>
  python3 scripts/scrape_facebook.py <source_id> --from-file data/scraped/<id>-raw.json
  python3 scripts/scrape_facebook.py --all

Without --from-file the raw input defaults to data/scraped/<source_id>-raw.json.
If no raw input exists the script prints the instructions above and exits 0
WITHOUT touching data/scraped/<source_id>.json — the pipeline runs this every
day, and a run with nothing new must never wipe the last good scrape.

The raw file is a JSON array of raw event objects:
  [{"name": "...", "date": "May 22, 2026", "time": "6:00 PM",
    "end_time": "9:00 PM", "location": "...", "url": "...", "description": "..."}]
"""

import functools
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_io import read_json
from scraper_utils import (
    NY_TZ,
    SCRAPED_DIR,
    ScraperSkipped,
    detect_styles,
    extract_cost,
    load_sources,
    make_event,
    resolve_year,
    run_scraper,
    scraper_argparser,
)

# Facebook card text often omits the year ("Fri, Jul 10"). A date this far in
# the past with no year written is next year's, not last year's.
NO_YEAR_ROLLOVER_DAYS = 30
_EXPLICIT_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _parse_fb_datetime(date_str: str, time_str: str = "", today: date | None = None) -> datetime | None:
    """Parse date/time strings scraped from Facebook into a datetime.

    Strings without an explicit year are resolved relative to ``today``: if
    the year-less date is more than NO_YEAR_ROLLOVER_DAYS in the past it is
    rolled forward to next year, since Facebook only lists upcoming events
    that way.
    """
    combined = f"{date_str} {time_str}".strip()
    if not combined:
        return None

    for fmt in [
        "%A, %B %d, %Y at %I:%M %p",
        "%A, %B %d, %Y at %I:%M%p",
        "%B %d, %Y at %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%B %d, %Y %I%p",
        "%B %d, %Y",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue

    try:
        from dateutil.parser import parse as dtparse
        parsed = dtparse(combined)
    except Exception:
        return None

    if _EXPLICIT_YEAR_RE.search(combined):
        return parsed
    today = today or datetime.now(NY_TZ).date()
    resolved = resolve_year(parsed.month, parsed.day, today,
                            grace_days=NO_YEAR_ROLLOVER_DAYS, max_ahead_days=None)
    if resolved is None:
        return parsed
    return parsed.replace(year=resolved.year)


def parse_raw_event(raw: dict, idx: int, source_id: str, defaults: dict | None = None) -> dict | None:
    """Convert a raw scraped event dict into a DanceEvent."""
    defaults = defaults or {}

    name = raw.get("name", defaults.get("name", "Event"))

    date_str = raw.get("date", "")
    time_str = raw.get("time", "")
    start = _parse_fb_datetime(date_str, time_str)

    if not start:
        print(f"  Could not parse date for event #{idx}: date='{date_str}' time='{time_str}'")
        return None

    if start.tzinfo is None:
        start = start.replace(tzinfo=NY_TZ)

    end_time_str = raw.get("end_time", "")
    duration_hours = raw.get("duration_hours", 3)
    if end_time_str:
        end_dt = _parse_fb_datetime(date_str, end_time_str)
        if end_dt:
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=NY_TZ)
            if end_dt <= start:
                end_dt += timedelta(days=1)
            end = end_dt
        else:
            end = start + timedelta(hours=duration_hours)
    else:
        end = start + timedelta(hours=duration_hours)

    location = raw.get("location", defaults.get("location", ""))
    description = raw.get("description", defaults.get("description", ""))
    url = raw.get("url", defaults.get("url"))
    recurring = raw.get("recurring", defaults.get("recurring", False))

    combined = f"{name} {description}"
    styles = detect_styles(combined)
    if styles == ["other"] and defaults.get("styles"):
        styles = defaults["styles"]

    cost = extract_cost(combined)
    if cost is None:
        cost = defaults.get("cost")

    return make_event(
        id=f"{source_id}-{start.strftime('%Y%m%d')}-{idx}",
        name=name,
        start=start,
        end=end,
        location=location,
        description=description,
        url=url,
        styles=styles,
        cost=cost,
        recurring=recurring,
        source=source_id,
    )


def from_file(path: Path, source_id: str, defaults: dict | None = None) -> list[dict]:
    """Load and parse raw events from a JSON file."""
    raw_events = read_json(path)
    if not isinstance(raw_events, list):
        raw_events = [raw_events]

    events = []
    for i, raw in enumerate(raw_events):
        ev = parse_raw_event(raw, i, source_id, defaults)
        if ev:
            events.append(ev)
            print(f"  -> {ev['name'][:50]} ({ev['dayOfWeek']} {ev['startDate'][:10]})")
    return events


def get_fb_sources() -> list[dict]:
    """Get all Facebook-type sources from sources.json."""
    return [s for s in load_sources() if s.get("type") == "facebook" and s.get("enabled")]


def raw_input_path(source_id: str) -> Path:
    return SCRAPED_DIR / f"{source_id}-raw.json"


def fetch_source(source: dict, from_file_path: Path | None = None) -> list[dict]:
    """Normalize a source's raw events file; skip (untouched) when there is none."""
    source_id = source["id"]
    fb_url = source.get("facebook_events_url", "")
    defaults = source.get("defaults", {})

    print(f"\n{'='*60}")
    print(f"Source: {source['name']} ({source_id})")
    print(f"FB URL: {fb_url}")

    path = from_file_path or raw_input_path(source_id)
    if not path.exists():
        print(
            f"No raw events file at {path}.\n"
            f"To scrape, an agent with a browser should:\n"
            f"  1. Navigate to {fb_url}\n"
            f"  2. Close the login dialog\n"
            f"  3. Check for Upcoming tab, extract events\n"
            f"  4. Save to data/scraped/{source_id}-raw.json\n"
            f"  5. Re-run: python3 scripts/scrape_facebook.py {source_id}"
        )
        raise ScraperSkipped(f"no raw input at {path.name}; existing scrape left as is")

    print(f"Loading from file: {path}")
    return from_file(path, source_id, defaults)


def main(argv: list[str] | None = None) -> int:
    parser = scraper_argparser(__doc__, required=False)
    parser.add_argument("--from-file", type=Path, help="Path to raw events JSON")
    parser.add_argument("--all", action="store_true", help="Normalize every Facebook source")
    args = parser.parse_args(argv)

    if args.all:
        sources = get_fb_sources()
        if not sources:
            print("No enabled Facebook sources in sources.json")
            return 0
        return max(run_scraper(s["id"], fetch_source) for s in sources)
    if args.source_id:
        return run_scraper(
            args.source_id,
            functools.partial(fetch_source, from_file_path=args.from_file),
        )
    fb_sources = get_fb_sources()
    if fb_sources:
        print(f"Available Facebook sources: {[s['id'] for s in fb_sources]}")
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

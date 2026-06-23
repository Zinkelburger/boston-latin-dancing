#!/usr/bin/env python3
"""
Fetch user-submitted events from the BLD API on Contabo and write them
as standard scraped events to data/scraped/submissions.json.

Requires BLD_API_URL and BLD_ADMIN_TOKEN in the environment (or .env).
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import make_event, write_scraped, detect_styles

# Submitted times are Eastern wall-clock; localize DST-aware, never fixed -04.
NY_TZ = ZoneInfo("America/New_York")

ROOT = Path(__file__).resolve().parent.parent

def _load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

_load_dotenv()

API_URL = os.environ.get("BLD_API_URL", "https://api.bostonsalsa.org")
ADMIN_TOKEN = os.environ.get("BLD_ADMIN_TOKEN", "")


def fetch_submissions() -> list[dict]:
    if not ADMIN_TOKEN:
        print("ERROR: BLD_ADMIN_TOKEN not set. Add it to .env")
        return []

    resp = requests.get(
        f"{API_URL}/api/submissions",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def submission_to_event(sub: dict) -> dict:
    """Convert a raw submission into a standard DanceEvent dict."""
    name = sub.get("event_name", "Untitled")
    url = sub.get("event_url", "")
    location = sub.get("location", "")
    description = sub.get("notes", "")
    raw_styles = sub.get("styles", [])

    styles = [s for s in raw_styles if s in {"bachata", "salsa", "kizomba", "zouk", "merengue"}]
    if not styles:
        styles = detect_styles(f"{name} {description}")

    date_str = sub.get("date", "") or sub.get("start_date", "")
    time_str = sub.get("time", "")

    start = _parse_datetime(date_str, time_str)
    end = start + timedelta(hours=3) if start else None

    if start is None:
        start = datetime.now(timezone.utc)
        end = start + timedelta(hours=3)

    sub_id = hashlib.sha1(f"{name}:{url}:{date_str}".encode()).hexdigest()[:16]

    return make_event(
        id=f"submit-{sub_id}",
        name=name,
        start=start,
        end=end,
        location=location,
        description=description,
        url=url or None,
        styles=styles,
        recurring=sub.get("is_recurring", False),
        source="submissions",
    )


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

    if time_str:
        time_str = time_str.strip().upper()
        for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M"):
            try:
                t = datetime.strptime(time_str, fmt)
                dt = dt.replace(hour=t.hour, minute=t.minute)
                break
            except ValueError:
                continue

    return dt.replace(tzinfo=NY_TZ)


def main():
    print("Fetching submissions from BLD API...")
    subs = fetch_submissions()
    print(f"  Found {len(subs)} submissions")

    if not subs:
        write_scraped("submissions", [])
        return

    events = [submission_to_event(s) for s in subs]
    print(f"  Converted {len(events)} events")
    write_scraped("submissions", events)


if __name__ == "__main__":
    main()

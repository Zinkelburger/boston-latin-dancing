#!/usr/bin/env python3
"""
Fetch user-submitted events from the BLD API on Contabo and write them
as standard scraped events to data/scraped/submissions.json.

Requires BLD_API_URL and BLD_ADMIN_TOKEN in the environment (or .env).

Exit codes:
  0  ok (submissions that could not be dated are reported on stderr and
     listed in the summary, but do not fail the run)
  2  BLD_ADMIN_TOKEN missing — nothing was written, so the previous
     scraped file is left untouched rather than replaced with []
"""

import hashlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scraper_utils import (  # noqa: E402
    detect_styles,
    make_event,
    record_scrape_health,
    write_scraped,
)
from recurrence_utils import _ORDINAL_WORDS  # noqa: E402

# Submitted times are Eastern wall-clock; localize DST-aware, never fixed -04.
NY_TZ = ZoneInfo("America/New_York")

ROOT = Path(__file__).resolve().parents[1]

# The form's style checkboxes (lib/constants.ts STYLE_LABELS) minus "other":
# a submission that only ticked "other" is better served by keyword detection
# on its name/notes than by an "other"-only event that the Latin filter drops.
KNOWN_STYLES = frozenset({"bachata", "salsa", "kizomba", "zouk", "merengue"})

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
WEEK_ORDINALS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "Last": -1}

# How far ahead a recurring submission is expanded. Long enough that a
# monthly series carries three dates, which is what recurrence_label needs to
# read the cadence back off the list.
RECURRENCE_WEEKS_AHEAD = 12
DEFAULT_START_HOUR = 20
ASSUMED_HOURS = 3

# "Every other week" parity when the submitter gave no start date. Must match
# event_store._matches_schedule_note so the pipeline and the frontend agree on
# which weeks a biweekly series falls on.
EVERY_OTHER_REF = datetime(2026, 1, 2)


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


class MissingAdminToken(RuntimeError):
    pass


def fetch_submissions() -> list[dict]:
    if not ADMIN_TOKEN:
        raise MissingAdminToken(
            "BLD_ADMIN_TOKEN not set. Add it to .env (same value as on the API host)."
        )

    resp = requests.get(
        f"{API_URL}/api/submissions",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ── Date handling ─────────────────────────────────────────────────────


def _parse_date(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _parse_time(time_str: str) -> Optional[tuple[int, int]]:
    if not time_str:
        return None
    time_str = time_str.strip().upper()
    for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M"):
        try:
            t = datetime.strptime(time_str, fmt)
            return t.hour, t.minute
        except ValueError:
            continue
    return None


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    dt = _parse_date(date_str)
    if dt is None:
        return None
    hm = _parse_time(time_str)
    if hm:
        dt = dt.replace(hour=hm[0], minute=hm[1])
    return dt.replace(tzinfo=NY_TZ)


def _nth_weekday_of_month(year: int, month: int, weekday_idx: int, nth: int) -> Optional[datetime]:
    """nth (1-4) or last (-1) occurrence of DAYS[weekday_idx] in a month."""
    from calendar import monthrange
    matches = [
        datetime(year, month, day)
        for day in range(1, monthrange(year, month)[1] + 1)
        if datetime(year, month, day).isoweekday() % 7 == weekday_idx
    ]
    if nth == -1:
        return matches[-1] if matches else None
    return matches[nth - 1] if len(matches) >= nth else None


def recurrence_dates(
    recurrence_type: str,
    day_of_week: str,
    week_of_month: str = "",
    start_date: str = "",
    today: Optional[datetime] = None,
    weeks_ahead: int = RECURRENCE_WEEKS_AHEAD,
) -> list[datetime]:
    """Concrete naive Boston dates for a recurring submission.

    weekly   — every <day_of_week>
    biweekly — every other <day_of_week>, anchored on start_date when given
               (the submitter said which week it runs), else on the shared
               EVERY_OTHER_REF parity the rest of the pipeline uses
    monthly  — the <week_of_month> <day_of_week> of each month

    Dates before start_date are never produced; dates before today are not
    either, so a series submitted long ago keeps rolling forward instead of
    being archived as past.
    """
    if day_of_week not in DAYS or recurrence_type not in ("weekly", "biweekly", "monthly"):
        return []
    weekday_idx = DAYS.index(day_of_week)
    today = (today or datetime.now(NY_TZ)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    first_allowed = _parse_date(start_date)
    window_start = max(today, first_allowed) if first_allowed else today
    window_end = window_start + timedelta(weeks=weeks_ahead)

    dates: list[datetime] = []
    if recurrence_type == "monthly":
        nth = WEEK_ORDINALS.get(week_of_month)
        if nth is None:
            return []
        y, m = window_start.year, window_start.month
        while datetime(y, m, 1) <= window_end:
            d = _nth_weekday_of_month(y, m, weekday_idx, nth)
            if d is not None and window_start <= d < window_end:
                dates.append(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return dates

    anchor = first_allowed if (recurrence_type == "biweekly" and first_allowed) else EVERY_OTHER_REF
    d = window_start
    while d < window_end:
        if d.isoweekday() % 7 == weekday_idx:
            if recurrence_type == "weekly" or ((d - anchor).days // 7) % 2 == 0:
                dates.append(d)
        d += timedelta(days=1)
    return dates


def recurrence_note(recurrence_type: str, day_of_week: str, week_of_month: str = "") -> str:
    """Human label in the exact phrasing recurrence_utils produces from dates.

    Wording is shared with the engine (_ORDINAL_WORDS) so a label we set and a
    label it infers later never disagree.
    """
    if recurrence_type == "weekly":
        return f"Every {day_of_week}"
    if recurrence_type == "biweekly":
        return f"Every other {day_of_week}"
    if recurrence_type == "monthly":
        nth = WEEK_ORDINALS.get(week_of_month)
        if nth == -1:
            return f"Last {day_of_week} of each month"
        if nth:
            return f"{_ORDINAL_WORDS[nth]} {day_of_week} of each month"
        return f"{day_of_week}s monthly"
    return ""


# ── Conversion ────────────────────────────────────────────────────────


def submission_to_event(sub: dict, today: Optional[datetime] = None) -> Optional[dict]:
    """Convert a raw submission into a standard DanceEvent dict.

    Returns None when the submission carries neither a parseable date nor a
    usable recurrence: inventing start=now would give it a fresh date every
    run while keeping the same id, so it would drift across the calendar
    forever. The caller reports those for a human to chase up.
    """
    name = sub.get("event_name", "Untitled")
    url = sub.get("event_url", "")
    location = sub.get("location", "")
    description = sub.get("notes", "")
    raw_styles = sub.get("styles", []) or []

    styles = [s for s in raw_styles if s in KNOWN_STYLES]
    if not styles:
        styles = detect_styles(f"{name} {description}")

    date_str = sub.get("date", "") or sub.get("start_date", "")
    time_str = sub.get("time", "")
    hm = _parse_time(time_str) or (DEFAULT_START_HOUR, 0)

    recurring = bool(sub.get("is_recurring", False))
    recurrence_type = sub.get("recurrence_type", "") or ""
    day_of_week = sub.get("day_of_week", "") or ""
    week_of_month = sub.get("week_of_month", "") or ""

    recurrences: list[str] = []
    label = ""
    start: Optional[datetime] = None

    if recurring and recurrence_type and day_of_week:
        dates = recurrence_dates(
            recurrence_type, day_of_week, week_of_month,
            start_date=sub.get("start_date", "") or "", today=today,
        )
        if dates:
            local = [d.replace(hour=hm[0], minute=hm[1], tzinfo=NY_TZ) for d in dates]
            start = local[0]
            recurrences = [d.isoformat() for d in local]
            label = recurrence_note(recurrence_type, day_of_week, week_of_month)

    if start is None:
        start = _parse_datetime(date_str, time_str)

    if start is None:
        return None

    end = start + timedelta(hours=ASSUMED_HOURS)
    # Recurring series hash on start_date (often blank), so the id is stable
    # across runs while the recurrence list rolls forward.
    sub_id = hashlib.sha1(f"{name}:{url}:{date_str}".encode()).hexdigest()[:16]

    event = make_event(
        id=f"submit-{sub_id}",
        name=name,
        start=start,
        end=end,
        location=location,
        description=description,
        url=url or None,
        styles=styles,
        recurring=recurring,
        source="submissions",
    )
    if recurrences:
        event["recurrences"] = recurrences
        event["recurrenceLabel"] = label
    return event


def _skip_reason(sub: dict) -> str:
    if sub.get("is_recurring"):
        return "recurring but no usable recurrence_type/day_of_week/week_of_month"
    if sub.get("date") or sub.get("start_date"):
        return f"unparseable date {sub.get('date') or sub.get('start_date')!r}"
    return "no date given"


def convert_submissions(subs: list[dict], today: Optional[datetime] = None) -> tuple[list[dict], list[dict]]:
    """Split raw submissions into events and the ones that need a date."""
    events: list[dict] = []
    needs_date: list[dict] = []
    for sub in subs:
        event = submission_to_event(sub, today=today)
        if event is None:
            needs_date.append({
                "event_name": sub.get("event_name", ""),
                "event_url": sub.get("event_url", ""),
                "contact": sub.get("email") or sub.get("instagram") or "",
                "submitted_at": sub.get("submitted_at", ""),
                "needs_date": True,
                "reason": _skip_reason(sub),
            })
        else:
            events.append(event)
    return events, needs_date


def main() -> dict:
    print("Fetching submissions from BLD API...")
    try:
        subs = fetch_submissions()
    except MissingAdminToken as exc:
        record_scrape_health("submissions", 0, 0, fetched=False, note=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        record_scrape_health(
            "submissions", 0, 0, fetched=False,
            note=f"{type(exc).__name__}: {exc}"[:300],
        )
        raise
    print(f"  Found {len(subs)} submissions")

    events, needs_date = convert_submissions(subs)
    for item in needs_date:
        print(
            f"WARNING: skipping submission {item['event_name']!r} ({item['event_url']}, "
            f"contact {item['contact'] or 'unknown'}): {item['reason']}. "
            "Ask the organizer for a date; it was not written to the scraped file.",
            file=sys.stderr,
        )
    print(f"  Converted {len(events)} events")
    write_scraped("submissions", events)
    record_scrape_health(
        "submissions",
        len(subs),
        len(events),
        skipped=not subs,
        note="API returned no submissions" if not subs else "",
    )
    return {"fetched": len(subs), "converted": len(events), "needs_date": needs_date}


if __name__ == "__main__":
    main()

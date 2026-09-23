"""Venue schedule entries: clock-time parsing, nth-weekday and every-other-
week rules, seasonal bounds, and validation of a venues.json schedule."""

import re
from datetime import datetime
from typing import Optional

from recurrence_utils import DAY_INDEX, DAYS_LIST


TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)
_TIME_24H_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def parse_time(time_str: str) -> Optional[tuple[int, int]]:
    """"9:00 PM" -> (21, 0); a bare 24-hour "21:00" is accepted too."""
    m = TIME_RE.search(time_str)
    if not m:
        m24 = _TIME_24H_RE.match(time_str or "")
        if not m24:
            return None
        h, mi = int(m24.group(1)), int(m24.group(2))
        return (h, mi) if h < 24 and mi < 60 else None
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3).upper() == "PM" and h != 12:
        h += 12
    elif m.group(3).upper() == "AM" and h == 12:
        h = 0
    return (h, mi)


def parse_time_range(time_str: str) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    parts = re.split(r"\s*[–—-]\s*", time_str)
    if len(parts) != 2:
        return None
    start = parse_time(parts[0])
    end = parse_time(parts[1])
    if start and end:
        return (start, end)
    return None


def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> Optional[datetime]:
    from calendar import monthrange
    count = 0
    for day in range(1, monthrange(year, month)[1] + 1):
        d = datetime(year, month, day)
        if d.weekday() == (weekday - 1) % 7:
            count += 1
            if count == nth:
                return d
    return None


# Phase reference for "every other week" schedules with no explicit anchor.
# Kept as the historical constant so venues that never set one keep the same
# weeks they always had.
_EVERY_OTHER_DEFAULT_ANCHOR = datetime(2026, 1, 2)
_ANCHOR_FORMAT = "%Y-%m-%d"


def parse_anchor(anchor: Optional[str]) -> Optional[datetime]:
    """A schedule entry's ``anchor`` ("YYYY-MM-DD") as a naive datetime."""
    if not anchor:
        return None
    try:
        return datetime.strptime(anchor, _ANCHOR_FORMAT)
    except (TypeError, ValueError):
        return None


def schedule_date_allowed(date: datetime, entry: dict) -> bool:
    """Whether a schedule entry's optional seasonal bounds include ``date``."""
    starts = parse_anchor(entry.get("starts"))
    until = parse_anchor(entry.get("until"))
    day = date.replace(hour=0, minute=0, second=0, microsecond=0)
    return (starts is None or day >= starts) and (until is None or day <= until)


def matches_schedule_note(date: datetime, note: str, weekday_name: str,
                           anchor: Optional[str] = None) -> bool:
    """Does the schedule entry's note admit this date?

    ``anchor`` sets the phase of an "every other" / "alternating" schedule: a
    date on which the night happens, so the weeks an even number of weeks away
    are on and the odd ones are off. Without it the historical default applies.
    """
    note_lower = note.lower() if note else ""
    nth_match = re.search(r"(\d)(?:st|nd|rd|th)\s+\w+day", note_lower)
    if nth_match:
        nth = int(nth_match.group(1))
        target = _nth_weekday_of_month(date.year, date.month, DAY_INDEX[weekday_name], nth)
        return target is not None and target.date() == date.date()
    if "every other" in note_lower or "alternating" in note_lower:
        ref = parse_anchor(anchor) or _EVERY_OTHER_DEFAULT_ANCHOR
        week_num = (date - ref).days // 7
        return week_num % 2 == 0
    return True


def validate_venue_schedule(schedule: list) -> list[str]:
    """Human-readable problems with a venues.json ``schedule`` list; [] if valid.

    Each entry is an object with ``dayOfWeek`` (a full weekday name), an
    optional ``time`` the expander can parse ("9:00 PM – 1:00 AM" or a bare
    "HH:MM"), an optional ``note`` string, optional ``starts``/``until``
    seasonal bounds, and an optional ``anchor`` ("YYYY-MM-DD", the phase of
    an every-other-week schedule).
    """
    problems: list[str] = []
    if not isinstance(schedule, list) or not schedule:
        return ["schedule must be a non-empty list of {dayOfWeek, time?, note?, starts?, until?, anchor?} objects"]

    for i, entry in enumerate(schedule):
        label = f"schedule[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be an object, got {type(entry).__name__}")
            continue

        day = entry.get("dayOfWeek")
        if day not in DAYS_LIST:
            problems.append(f"{label}: dayOfWeek must be one of {', '.join(DAYS_LIST)} (got {day!r})")

        time_str = entry.get("time")
        if time_str is not None:
            if not isinstance(time_str, str):
                problems.append(f"{label}: time must be a string (got {time_str!r})")
            elif time_str.strip() and not (parse_time_range(time_str) or parse_time(time_str)):
                problems.append(
                    f"{label}: time {time_str!r} is not parseable — use \"HH:MM\" or "
                    f"\"H:MM AM – H:MM PM\""
                )

        note = entry.get("note")
        if note is not None and not isinstance(note, str):
            problems.append(f"{label}: note must be a string (got {note!r})")

        for bound in ("starts", "until"):
            value = entry.get(bound)
            if value is not None and (
                not isinstance(value, str) or parse_anchor(value) is None
            ):
                problems.append(
                    f"{label}: {bound} must be a YYYY-MM-DD date (got {value!r})"
                )

        anchor = entry.get("anchor")
        if anchor is not None:
            parsed = parse_anchor(anchor) if isinstance(anchor, str) else None
            if parsed is None:
                problems.append(f"{label}: anchor must be a YYYY-MM-DD date (got {anchor!r})")
            elif day in DAYS_LIST:
                anchor_day = DAYS_LIST[parsed.isoweekday() % 7]
                if anchor_day != day:
                    problems.append(
                        f"{label}: anchor {anchor} is a {anchor_day}, not a {day} — "
                        f"it should be a date the night actually happens"
                    )
    return problems

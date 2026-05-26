"""
Human-readable recurrence labels for published events.

Used at publish time; mirrored in lib/recurrences.ts for dev without republish.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
DAYS_LIST = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_INDEX = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
             "Thursday": 4, "Friday": 5, "Saturday": 6}
DAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _parse_date(iso_str: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None

_ORDINAL_WORDS = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth"}


def _parse_recurrence_dates(recurrences: list[str]) -> list[datetime]:
    out: list[datetime] = []
    for iso in recurrences:
        dt = _parse_date(iso)
        if not dt:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        out.append(dt.astimezone(NY_TZ))
    return sorted(out)


def _weekday_index(dt: datetime) -> int:
    return dt.isoweekday() % 7


def _day_name(dt: datetime) -> str:
    return DAYS_LIST[_weekday_index(dt)]


def _nth_weekday_of_month(dt: datetime) -> int:
    count = 0
    for day in range(1, dt.day + 1):
        d = datetime(dt.year, dt.month, day, tzinfo=dt.tzinfo)
        if _weekday_index(d) == _weekday_index(dt):
            count += 1
    return count


def _is_last_weekday_of_month(dt: datetime) -> bool:
    from calendar import monthrange
    last_day = monthrange(dt.year, dt.month)[1]
    return dt.day + 7 > last_day


def _ordinal_phrase(nth: int, is_last: bool) -> str:
    if is_last:
        return "Last"
    return _ORDINAL_WORDS.get(nth, f"{nth}th")


def _median_gap_days(dates: list[datetime]) -> Optional[float]:
    if len(dates) < 2:
        return None
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    gaps.sort()
    mid = len(gaps) // 2
    if len(gaps) % 2:
        return float(gaps[mid])
    return (gaps[mid - 1] + gaps[mid]) / 2.0


def _label_from_schedule_note(note: str, day_name: str) -> Optional[str]:
    note_lower = (note or "").lower()
    if "every other" in note_lower or "alternating" in note_lower:
        return f"Every other {day_name}"
    nth_match = re.search(r"(\d)(?:st|nd|rd|th)\s+\w+day", note_lower)
    if nth_match or re.search(r"\b1st\b", note_lower):
        nth = int(nth_match.group(1)) if nth_match else 1
        word = _ORDINAL_WORDS.get(nth, f"{nth}th")
        return f"{word} {day_name} of each month"
    if "of each month" in note_lower or "of the month" in note_lower:
        return f"{day_name}s monthly"
    return None


def _compact_schedule_days(schedule: list[dict]) -> str:
    indices = sorted({_weekday_index_from_name(s["dayOfWeek"]) for s in schedule})
    if len(indices) == 7:
        return "Every night"
    segments: list[str] = []
    i = 0
    while i < len(indices):
        start = indices[i]
        j = i
        while j + 1 < len(indices) and indices[j + 1] == indices[j] + 1:
            j += 1
        if j == i:
            segments.append(DAY_SHORT[start])
        else:
            segments.append(f"{DAY_SHORT[start]}–{DAY_SHORT[indices[j]]}")
        i = j + 1
    return ", ".join(segments)


def _weekday_index_from_name(day_name: str) -> int:
    return DAY_INDEX.get(day_name, 0)


def _label_from_schedule(schedule: list[dict]) -> Optional[str]:
    if not schedule:
        return None

    if len(schedule) == 1:
        entry = schedule[0]
        day = entry.get("dayOfWeek", "")
        note = entry.get("note", "")
        from_note = _label_from_schedule_note(note, day)
        if from_note:
            return from_note
        time_str = entry.get("time", "").strip()
        if time_str:
            return f"Every {day} · {time_str}"
        return f"Every {day}"

    days_compact = _compact_schedule_days(schedule)
    if len(schedule) >= 4:
        return f"{days_compact} · see schedule"
    return days_compact


def _label_from_recurrence_dates(dates: list[datetime]) -> Optional[str]:
    if len(dates) < 2:
        return None

    weekdays = {_weekday_index(d) for d in dates}
    if len(weekdays) != 1:
        return None

    day_name = _day_name(dates[0])
    nth_values = [_nth_weekday_of_month(d) for d in dates]
    last_flags = [_is_last_weekday_of_month(d) for d in dates]

    if len(set(nth_values)) == 1 and len(set(last_flags)) == 1:
        gap = _median_gap_days(dates)
        if gap is not None and 24 <= gap <= 35:
            word = _ordinal_phrase(nth_values[0], last_flags[0])
            return f"{word} {day_name} of each month"

    gap = _median_gap_days(dates)
    if gap is not None:
        if 6 <= gap <= 8:
            return f"Every {day_name}"
        if 13 <= gap <= 15:
            return f"Every other {day_name}"

    return None


def recurrence_label(event: dict) -> Optional[str]:
    """Infer a short human-readable recurrence summary, or None."""
    if not event.get("recurring"):
        return None

    schedule = event.get("schedule")
    if schedule:
        return _label_from_schedule(schedule)

    recurrences = event.get("recurrences") or []
    dates = _parse_recurrence_dates(recurrences)
    if len(dates) >= 2:
        label = _label_from_recurrence_dates(dates)
        if label:
            return label

    dow = event.get("dayOfWeek")
    if dow and len(dates) <= 1:
        return f"Every {dow}"

    return None

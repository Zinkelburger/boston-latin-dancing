"""Dates of an event: parsing to aware Boston times, its occurrences, and
day/time comparisons between two events."""

from datetime import datetime
from typing import Optional

from recurrence_utils import DAYS_LIST, NY_TZ, parse_date


def as_aware(dt: datetime) -> datetime:
    """Naive timestamps in this store mean Boston wall-clock time."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=NY_TZ)


def parse_aware(iso_str: str) -> Optional[datetime]:
    dt = parse_date(iso_str or "")
    return None if dt is None else as_aware(dt)


def eastern_iso(dt: datetime) -> str:
    """One canonical spelling for an instant: Eastern time with its offset."""
    return as_aware(dt).astimezone(NY_TZ).isoformat()


def occurrence_instants(event: dict) -> list[datetime]:
    """Every dated occurrence of an event (startDate + recurrences[]), parsed
    to aware datetimes, de-duplicated by *instant* and sorted.

    The stored strings mix +00:00, -04:00 and -05:00 offsets, so the same
    moment can be spelled two ways. Sorting the strings interleaves them and
    keeps both; sorting instants does not.
    """
    seen: dict[float, datetime] = {}
    for raw in [event.get("startDate", "")] + list(event.get("recurrences") or []):
        dt = parse_aware(raw)
        if dt is None:
            continue
        seen.setdefault(dt.timestamp(), dt)
    return [seen[k] for k in sorted(seen)]


def last_occurrence(event: dict) -> Optional[datetime]:
    """When an event is finally over: the latest of startDate and recurrences.

    Taking recurrences[-1] alone silently retires a live series whose stored
    list has gone stale — "Rueda in the Pahk" ran every Sunday with a list that
    ended weeks earlier, so archive_past_events() filed it away while its own
    startDate was still in the future. Whichever field is further out wins, so
    an inconsistent record errs toward staying on the map. The list is compared
    as instants, not strings, so a mixed-offset list cannot mis-order.
    """
    instants = occurrence_instants(event)
    return instants[-1] if instants else None


DEAD_HOURS = range(1, 9)


def implausible_start_hour(event: dict) -> Optional[int]:
    """Boston-local start hour if it lands somewhere no social dance starts.

    A timezone bug that converts the wrong way pushes a 9 PM social to 1 AM.
    Nothing here legitimately starts between 1 and 9 in the morning, so that
    window is a free tripwire on double conversions. Midnight is excluded:
    date-only listings (Fiesta Dance Co) anchor there deliberately.

    This cannot see an artifact that lands back in the evening — 9 PM read as
    5 PM is invisible here, and needs a second source. See "Whose clock to
    trust" in .cursor/rules/verification.md.
    """
    dt = parse_date(event.get("startDate", "") or "")
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    hour = dt.astimezone(NY_TZ).hour
    return hour if hour in DEAD_HOURS else None


def same_calendar_day(a: dict, b: dict) -> Optional[bool]:
    """True if start dates fall on the same calendar day in America/New_York."""
    date_a = parse_date(a.get("startDate", ""))
    date_b = parse_date(b.get("startDate", ""))
    if not date_a or not date_b:
        return None
    if date_a.tzinfo is None:
        date_a = date_a.replace(tzinfo=NY_TZ)
    if date_b.tzinfo is None:
        date_b = date_b.replace(tzinfo=NY_TZ)
    return date_a.astimezone(NY_TZ).date() == date_b.astimezone(NY_TZ).date()


def dates_within(a: dict, b: dict, hours: float) -> Optional[bool]:
    """True if dates within range, False if not, None if dates unparseable."""
    date_a = parse_date(a.get("startDate", ""))
    date_b = parse_date(b.get("startDate", ""))
    if not date_a or not date_b:
        return None
    if date_a.tzinfo is None:
        date_a = date_a.replace(tzinfo=NY_TZ)
    if date_b.tzinfo is None:
        date_b = date_b.replace(tzinfo=NY_TZ)
    date_a = date_a.astimezone(NY_TZ)
    date_b = date_b.astimezone(NY_TZ)
    return abs((date_a - date_b).total_seconds()) < hours * 3600


def weekday_of(iso_str: str) -> Optional[str]:
    """Boston weekday name for an ISO timestamp, or None if unparseable."""
    dt = parse_aware(iso_str)
    if dt is None:
        return None
    return DAYS_LIST[dt.astimezone(NY_TZ).isoweekday() % 7]


def event_day_of_week(event: dict) -> Optional[str]:
    """Return dayOfWeek from the field or infer it from startDate."""
    return event.get("dayOfWeek") or weekday_of(event.get("startDate", ""))


def wall_clock_minutes(event: dict) -> Optional[int]:
    dt = parse_aware(event.get("startDate", ""))
    if dt is None:
        return None
    local = dt.astimezone(NY_TZ)
    return local.hour * 60 + local.minute

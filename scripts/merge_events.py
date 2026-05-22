#!/usr/bin/env python3
"""
Merge all scraped event sources into public/events.json.

Reads:
  - data/scraped/*.json        (output of each scraper)
  - public/recurring.json      (manually curated weekly venues)

Deduplication:
  - Exact ID match across sources
  - Fuzzy: same name (normalized) + date within 1 day + coord proximity

Priority (highest wins for conflicts):
  beatrice-calendar > recurring-venues > eventbrite > lister > facebook sources

Writes: public/events.json
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unicodedata import normalize as unicode_normalize

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import ROOT, SCRAPED_DIR, geocode

EVENTS_JSON = ROOT / "public" / "events.json"
RECURRING_JSON = ROOT / "public" / "recurring.json"


def slugify(name: str, event_id: str) -> str:
    base = unicode_normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60]
    suffix = event_id[:8].lower()
    return f"{base}-{suffix}"

SOURCE_PRIORITY = {
    "beatrice-calendar": 0,
    "": 0,
    "recurring-venues": 1,
    "eventbrite-boston-latin": 2,
    "lister-events": 3,
    "bobas": 4,
    "dantes-salsa": 4,
}


def source_rank(event: dict) -> int:
    return SOURCE_PRIORITY.get(event.get("source", ""), 5)


def normalize_name(name: str) -> str:
    """Normalize event name for fuzzy matching."""
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Remove date-like suffixes (e.g. "may 22nd", "5/23/26")
    name = re.sub(r"\b\d{1,2}[/\-]\d{1,2}([/\-]\d{2,4})?\b", "", name)
    name = re.sub(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}\w*\b", "", name, flags=re.I)
    return name.strip()


def parse_date(iso_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None


def _coords_close(a: dict, b: dict, threshold_km: float = 0.3) -> bool:
    """Check if two events are at approximately the same location (within 300m)."""
    lat_a, lng_a = a.get("lat"), a.get("lng")
    lat_b, lng_b = b.get("lat"), b.get("lng")
    if lat_a is None or lng_a is None or lat_b is None or lng_b is None:
        return False
    import math
    dlat = lat_a - lat_b
    dlng = (lng_a - lng_b) * math.cos(math.radians(lat_a))
    dist = math.sqrt(dlat * dlat + dlng * dlng) * 111
    return dist <= threshold_km


def is_duplicate(a: dict, b: dict) -> bool:
    """Check if two events are duplicates.

    Duplicate = exact ID match, or (fuzzy name match AND date within 1 day).
    Coordinate proximity is used as an additional positive signal when names
    partially match.
    """
    if a["id"] == b["id"]:
        return True

    name_a = normalize_name(a["name"])
    name_b = normalize_name(b["name"])
    if not name_a or not name_b:
        return False

    names_match = (
        name_a == name_b
        or name_a in name_b
        or name_b in name_a
    )

    # Weaker name match boosted by same coords
    if not names_match and _coords_close(a, b):
        words_a = set(name_a.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
        words_b = set(name_b.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
        if words_a and words_b:
            overlap = words_a & words_b
            names_match = len(overlap) >= max(1, min(len(words_a), len(words_b)) * 0.5)

    if not names_match:
        return False

    date_a = parse_date(a["startDate"])
    date_b = parse_date(b["startDate"])
    if date_a and date_b:
        return abs((date_a - date_b).total_seconds()) < 86400

    return False


def merge_event(existing: dict, new: dict) -> dict:
    """Merge a new event into an existing one, filling gaps."""
    merged = dict(existing)

    if not merged.get("description") and new.get("description"):
        merged["description"] = new["description"]
    elif new.get("description") and len(new["description"]) > len(merged.get("description", "")):
        # Prefer longer, richer descriptions
        if source_rank(new) <= source_rank(existing):
            merged["description"] = new["description"]

    if not merged.get("url") and new.get("url"):
        merged["url"] = new["url"]

    if not merged.get("cost") and new.get("cost"):
        merged["cost"] = new["cost"]

    if (merged.get("lat") is None or merged.get("lng") is None) and new.get("lat") and new.get("lng"):
        merged["lat"] = new["lat"]
        merged["lng"] = new["lng"]

    if merged.get("styles") == ["other"] and new.get("styles") != ["other"]:
        merged["styles"] = new["styles"]

    if not merged.get("recurring") and new.get("recurring"):
        merged["recurring"] = True

    if not merged.get("schedule") and new.get("schedule"):
        merged["schedule"] = new["schedule"]

    if not merged.get("recurrences") and new.get("recurrences"):
        merged["recurrences"] = new["recurrences"]

    return merged


def load_ics_events() -> list[dict]:
    """Load the ICS-sourced events from data/scraped/beatrice-calendar.json."""
    scraped_ics = SCRAPED_DIR / "beatrice-calendar.json"
    if scraped_ics.exists():
        return json.loads(scraped_ics.read_text())

    print("  No ICS events found (run `npm run fetch-events` first)")
    return []


def load_scraped_events() -> list[dict]:
    """Load all scraped source files except beatrice-calendar (loaded separately)."""
    all_events: list[dict] = []
    for path in sorted(SCRAPED_DIR.glob("*.json")):
        if path.name == "beatrice-calendar.json":
            continue
        try:
            events = json.loads(path.read_text())
            print(f"  {path.name}: {len(events)} events")
            all_events.extend(events)
        except Exception as e:
            print(f"  ERROR loading {path.name}: {e}")
    return all_events


def deduplicate(events: list[dict]) -> list[dict]:
    """Deduplicate events, keeping higher-priority sources."""
    # Sort by source priority (best first)
    events.sort(key=source_rank)

    result: list[dict] = []
    for ev in events:
        dup_idx = None
        for i, existing in enumerate(result):
            if is_duplicate(existing, ev):
                dup_idx = i
                break

        if dup_idx is not None:
            result[dup_idx] = merge_event(result[dup_idx], ev)
        else:
            result.append(ev)

    return result


def _location_key(location: str) -> str:
    """Extract the street address portion for grouping (e.g. '668 memorial dr').

    Tries to find a 'NUMBER STREET' pattern in the location string.
    Falls back to the first meaningful segment.
    """
    loc = location.lower()
    # Try to extract a street address like "668 Memorial Dr"
    m = re.search(r"\d+\s+[\w\s]+(?:st|ave|blvd|rd|dr|ln|way|ct|pl|pkwy|drive|street|avenue)\b", loc, re.I)
    if m:
        addr = m.group(0).strip()
        addr = re.sub(r"[^\w\s]", "", addr)
        addr = re.sub(r"\s+", " ", addr).strip()
        # Normalize common abbreviations
        for full, abbr in [("street", "st"), ("avenue", "ave"), ("boulevard", "blvd"),
                           ("drive", "dr"), ("road", "rd"), ("lane", "ln"),
                           ("parkway", "pkwy"), ("place", "pl"), ("court", "ct")]:
            addr = re.sub(rf"\b{full}\b", abbr, addr)
        return addr
    # Fallback: first line, cleaned
    lines = [l.strip() for l in loc.split("\n") if l.strip()]
    first = lines[0] if lines else loc
    first = re.sub(r"[^\w\s]", "", first)
    return re.sub(r"\s+", " ", first).strip()


def _names_are_same_series(a: str, b: str) -> bool:
    """Check if two normalized names refer to the same event series."""
    if a == b:
        return True
    if a in b or b in a:
        return True
    # Check if they share enough significant words
    words_a = set(a.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
    words_b = set(b.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
    if not words_a or not words_b:
        return False
    overlap = words_a & words_b
    smaller = min(len(words_a), len(words_b))
    return len(overlap) >= max(2, smaller * 0.6)


def collapse_recurring_series(events: list[dict]) -> list[dict]:
    """Collapse multiple occurrences of the same recurring series into one event.

    Deterministic: groups by fuzzy name + location similarity. If a group has 2+
    events, keeps the best one (highest priority source), sets recurring=True,
    and stores all dates in a `recurrences` array sorted chronologically.
    """
    # Build groups by finding events that match pairwise
    groups: list[list[int]] = []  # each group is a list of indices
    assigned: set[int] = set()

    for i, ev_i in enumerate(events):
        if i in assigned:
            continue
        group = [i]
        assigned.add(i)
        name_i = normalize_name(ev_i["name"])
        loc_i = _location_key(ev_i.get("location", ""))

        for j, ev_j in enumerate(events):
            if j in assigned:
                continue
            name_j = normalize_name(ev_j["name"])
            loc_j = _location_key(ev_j.get("location", ""))

            if not _names_are_same_series(name_i, name_j):
                continue
            # Location must share the same street address or both be empty
            if loc_i and loc_j:
                if loc_i != loc_j and loc_i not in loc_j and loc_j not in loc_i:
                    continue
            elif loc_i != loc_j:
                continue

            group.append(j)
            assigned.add(j)

        groups.append(group)

    result: list[dict] = []
    collapsed_count = 0

    for idx_group in groups:
        group = [events[i] for i in idx_group]

        if len(group) == 1:
            result.append(group[0])
            continue

        collapsed_count += len(group) - 1

        group.sort(key=lambda e: (source_rank(e), -len(e.get("description", ""))))
        best = dict(group[0])

        dates: list[str] = sorted({ev["startDate"] for ev in group})

        now = datetime.now().astimezone()
        future_dates = [d for d in dates if parse_date(d) and parse_date(d) >= now]
        if future_dates:
            best["startDate"] = future_dates[0]
            for ev in group:
                if ev["startDate"] == future_dates[0]:
                    best["endDate"] = ev["endDate"]
                    break
        else:
            best["startDate"] = dates[-1]

        best["recurring"] = True
        best["recurrences"] = dates

        dt = parse_date(best["startDate"])
        if dt:
            days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
            best["dayOfWeek"] = days[dt.isoweekday() % 7]

        for ev in group[1:]:
            if (best.get("lat") is None or best.get("lng") is None) and ev.get("lat") and ev.get("lng"):
                best["lat"] = ev["lat"]
                best["lng"] = ev["lng"]
            if not best.get("cost") and ev.get("cost"):
                best["cost"] = ev["cost"]
            if not best.get("url") and ev.get("url"):
                best["url"] = ev["url"]

        print(f"  Collapsed: \"{best['name']}\" ({len(group)} occurrences -> 1, dates: {[d[:10] for d in dates]})")
        result.append(best)

    if collapsed_count:
        print(f"  Total collapsed: {collapsed_count} duplicate occurrences removed")

    return result


# ── Recurring venue -> event generation ───────────────────────────

DAY_INDEX = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
             "Thursday": 4, "Friday": 5, "Saturday": 6}
DAYS_LIST = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)


def _parse_time(time_str: str) -> tuple[int, int] | None:
    """Parse '9:00 PM' -> (21, 0)."""
    m = _TIME_RE.search(time_str)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3).upper() == "PM" and h != 12:
        h += 12
    elif m.group(3).upper() == "AM" and h == 12:
        h = 0
    return (h, mi)


def _parse_time_range(time_str: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Parse '9:00 PM – 2:00 AM' -> ((21,0), (2,0))."""
    parts = re.split(r"\s*[–—-]\s*", time_str)
    if len(parts) != 2:
        return None
    start = _parse_time(parts[0])
    end = _parse_time(parts[1])
    if start and end:
        return (start, end)
    return None


def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: int) -> datetime | None:
    """Get the nth occurrence (1-based) of a weekday in a month."""
    from calendar import monthrange
    count = 0
    for day in range(1, monthrange(year, month)[1] + 1):
        d = datetime(year, month, day)
        if d.weekday() == (weekday - 1) % 7:  # Python weekday: Mon=0
            count += 1
            if count == nth:
                return d
    return None


def _matches_schedule_note(date: datetime, note: str, weekday_name: str) -> bool:
    """Check if a date matches a schedule note like '1st Saturday of each month'."""
    note_lower = note.lower() if note else ""

    # "1st/2nd/3rd/4th Xday of each month"
    nth_match = re.search(r"(\d)(?:st|nd|rd|th)\s+\w+day", note_lower)
    if nth_match:
        nth = int(nth_match.group(1))
        py_weekday = (DAY_INDEX[weekday_name] - 1) % 7  # Sun=6 in Python
        target = _nth_weekday_of_month(date.year, date.month, DAY_INDEX[weekday_name], nth)
        return target is not None and target.date() == date.date()

    # "every other" -- generate every 2 weeks from a reference point
    if "every other" in note_lower or "alternating" in note_lower:
        ref = datetime(2026, 1, 2)  # a known Friday
        week_num = (date - ref).days // 7
        return week_num % 2 == 0

    return True


def generate_from_recurring(weeks_ahead: int = 8) -> list[dict]:
    """Read recurring.json and generate concrete DanceEvent dicts.

    One event per venue, with schedule preserved and recurrences listing
    all upcoming dates for the next `weeks_ahead` weeks.
    """
    if not RECURRING_JSON.exists():
        print("  recurring.json not found, skipping")
        return []

    venues = json.loads(RECURRING_JSON.read_text())
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_window = today + timedelta(weeks=weeks_ahead)
    events: list[dict] = []

    for venue in venues:
        schedule = venue.get("schedule", [])
        if not schedule:
            continue

        all_dates: list[datetime] = []

        for sched in schedule:
            day_name = sched["dayOfWeek"]
            target_wday = DAY_INDEX.get(day_name)
            if target_wday is None:
                continue

            time_range = _parse_time_range(sched.get("time", ""))
            note = sched.get("note", "")

            d = today
            while d < end_window:
                # isoweekday: Mon=1..Sun=7; we need Sun=0..Sat=6
                if d.isoweekday() % 7 == target_wday:
                    if _matches_schedule_note(d, note, day_name):
                        if time_range:
                            start_h, start_m = time_range[0]
                            dt = d.replace(hour=start_h, minute=start_m)
                        else:
                            dt = d.replace(hour=20, minute=0)
                        all_dates.append(dt)
                d += timedelta(days=1)

        if not all_dates:
            continue

        all_dates.sort()

        # Build recurrences as ISO strings
        from datetime import timezone as tz
        est = tz(timedelta(hours=-4))
        recurrences = [dt.replace(tzinfo=est).isoformat() for dt in all_dates]

        next_dt = all_dates[0]
        # Compute endDate from the first schedule entry's time range
        time_range = _parse_time_range(schedule[0].get("time", ""))
        if time_range:
            end_h, end_m = time_range[1]
            end_dt = next_dt.replace(hour=end_h, minute=end_m)
            if end_dt <= next_dt:
                end_dt += timedelta(days=1)
        else:
            end_dt = next_dt + timedelta(hours=3)

        ev = {
            "id": venue["id"],
            "name": venue["name"],
            "startDate": next_dt.replace(tzinfo=est).isoformat(),
            "endDate": end_dt.replace(tzinfo=est).isoformat(),
            "dayOfWeek": DAYS_LIST[next_dt.isoweekday() % 7],
            "location": venue.get("location", ""),
            "lat": venue.get("lat"),
            "lng": venue.get("lng"),
            "description": venue.get("description", ""),
            "url": venue.get("url"),
            "styles": venue.get("styles", ["other"]),
            "cost": venue.get("cost"),
            "recurring": True,
            "recurrences": recurrences,
            "schedule": schedule,
            "source": "recurring-venues",
        }
        events.append(ev)
        print(f"  {venue['name']}: {len(all_dates)} upcoming dates, next: {next_dt.strftime('%a %b %d')}")

    return events


def geocode_missing(events: list[dict]) -> int:
    """Geocode events that are missing coordinates."""
    count = 0
    for ev in events:
        if (ev.get("lat") is None or ev.get("lng") is None) and ev.get("location"):
            coords = geocode(ev["location"])
            if coords:
                ev["lat"], ev["lng"] = coords
                count += 1
                print(f"  Geocoded: {ev['name'][:40]} -> {coords[0]:.4f}, {coords[1]:.4f}")
    return count


def flag_needs_review(events: list[dict]) -> list[dict]:
    """Return events that a Cursor agent should review (missing styles/cost/coords)."""
    flagged = []
    for ev in events:
        issues = []
        if ev.get("styles") == ["other"]:
            issues.append("styles=other")
        if ev.get("cost") is None and ev.get("description"):
            issues.append("cost=null")
        if ev.get("lat") is None and ev.get("location"):
            issues.append("lat=null")
        if issues:
            flagged.append({"event": ev, "issues": issues})
    return flagged


def main():
    print("Loading events from all sources...")
    ics_events = load_ics_events()
    print(f"  ICS (Beatrice): {len(ics_events)} events")

    print("\nGenerating events from recurring venues...")
    recurring_events = generate_from_recurring()
    print(f"  Generated: {len(recurring_events)} venue events")

    scraped_events = load_scraped_events()
    print(f"  Scraped total: {len(scraped_events)} events")

    all_events = ics_events + recurring_events + scraped_events
    print(f"\nTotal before dedup: {len(all_events)}")

    deduped = deduplicate(all_events)
    print(f"After dedup: {len(deduped)} events ({len(all_events) - len(deduped)} duplicates removed)")

    print("\nCollapsing recurring series...")
    deduped = collapse_recurring_series(deduped)
    print(f"After collapse: {len(deduped)} events")

    print("\nGeocoding missing coordinates...")
    geocoded = geocode_missing(deduped)
    print(f"  Geocoded {geocoded} events")

    # Sort by start date
    deduped.sort(key=lambda e: e.get("startDate", ""))

    with_coords = sum(1 for e in deduped if e.get("lat") and e.get("lng"))
    without_coords = len(deduped) - with_coords
    print(f"\nFinal: {len(deduped)} events ({with_coords} with coords, {without_coords} without)")

    style_counts: dict[str, int] = {}
    for e in deduped:
        for s in e.get("styles", []):
            style_counts[s] = style_counts.get(s, 0) + 1
    print(f"  Styles: {style_counts}")

    source_counts: dict[str, int] = {}
    for e in deduped:
        src = e.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
    print(f"  Sources: {source_counts}")

    # Flag events that need agent review
    flagged = flag_needs_review(deduped)
    if flagged:
        print(f"\n⚠ {len(flagged)} events need review:")
        for f in flagged:
            ev = f["event"]
            print(f"  - {ev['name'][:50]} | {', '.join(f['issues'])}")

    for ev in deduped:
        ev["slug"] = slugify(ev["name"], ev["id"])
        ev.pop("source", None)

    EVENTS_JSON.write_text(json.dumps(deduped, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(deduped)} events to {EVENTS_JSON}")


if __name__ == "__main__":
    main()

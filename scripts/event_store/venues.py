"""Permanent venues (data/venues.json): expanding weekly schedules into
events, and adding or editing a venue."""

import json
from datetime import datetime, timedelta

import atomic_io
import scraper_utils
from recurrence_utils import DAY_INDEX, DAYS_LIST, NY_TZ
from scraper_utils import detect_styles

from . import paths
from .schedule import (
    matches_schedule_note,
    parse_anchor,
    parse_time_range,
    schedule_date_allowed,
    validate_venue_schedule,
)
from .slugs import slug_base
from .storage import append_changelog, locked


def expand_venues(weeks_ahead: int = 8) -> list[dict]:
    """Read data/venues.json and generate concrete DanceEvent dicts."""
    venues = atomic_io.read_json(paths.VENUES_JSON, default=[])
    # Anchor the weekly grid to Boston's current date, and stamp generated
    # occurrences as America/New_York so EDT/EST is correct across DST.
    today = datetime.now(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end_window = today + timedelta(weeks=weeks_ahead)
    events: list[dict] = []

    for venue in venues:
        schedule = venue.get("schedule", [])
        if not schedule:
            continue

        # Specific YYYY-MM-DD dates to skip (e.g. a night taken over by a
        # special-edition event, or a one-off cancellation). Keeps the weekly
        # hub from claiming a date that a distinct pin already owns.
        exclude_dates = set(venue.get("excludeDates") or [])

        all_dates: list[datetime] = []
        for sched in schedule:
            day_name = sched["dayOfWeek"]
            target_wday = DAY_INDEX.get(day_name)
            if target_wday is None:
                continue
            time_range = parse_time_range(sched.get("time", ""))
            note = sched.get("note", "")
            anchor = sched.get("anchor")
            d = today
            while d < end_window:
                if (
                    d.isoweekday() % 7 == target_wday
                    and d.strftime("%Y-%m-%d") not in exclude_dates
                    and schedule_date_allowed(d, sched)
                ):
                    if matches_schedule_note(d, note, day_name, anchor):
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

        recurrences = [dt.replace(tzinfo=NY_TZ).isoformat() for dt in all_dates]
        next_dt = all_dates[0]
        time_range = parse_time_range(schedule[0].get("time", ""))
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
            "startDate": next_dt.replace(tzinfo=NY_TZ).isoformat(),
            "endDate": end_dt.replace(tzinfo=NY_TZ).isoformat(),
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
        if venue.get("urls"):
            ev["urls"] = venue["urls"]
        if venue.get("nextDateApproximate"):
            ev["nextDateApproximate"] = True
        if venue.get("recurrenceLabel"):
            ev["recurrenceLabel"] = venue["recurrenceLabel"]
        if venue.get("sourceId"):
            ev["_sourceId"] = venue["sourceId"]
        events.append(ev)

    return events


@locked
def exclude_venue_date(venue_id: str, date_str: str) -> bool:
    """Stop a venue hub from generating a pin on one date. Returns True if added."""
    venues = atomic_io.read_json(paths.VENUES_JSON, default=[])
    for venue in venues:
        if venue.get("id") != venue_id:
            continue
        excluded = venue.setdefault("excludeDates", [])
        if date_str in excluded:
            return False
        excluded.append(date_str)
        excluded.sort()
        atomic_io.write_json(paths.VENUES_JSON, venues)
        return True
    raise ValueError(f"No venue with id '{venue_id}' in venues.json")


@locked
def add_venue(venue: dict) -> dict:
    """Append a venue to data/venues.json.

    Validates name, location, url and schedule (validate_venue_schedule),
    refuses a venue whose name or id already exists, geocodes when lat/lng
    are missing. Returns ``{"status": "added"|"invalid"|"exists",
    "problems": [...]}``; ``added`` results carry the stored ``venue``.
    """
    problems: list[str] = []
    if not isinstance(venue, dict):
        return {"status": "invalid", "problems": ["venue must be an object"]}

    for key in ("name", "location", "url"):
        if not str(venue.get(key) or "").strip():
            problems.append(f"missing {key}")
    problems.extend(validate_venue_schedule(venue.get("schedule")))
    if problems:
        return {"status": "invalid", "problems": problems}

    name = venue["name"].strip()
    venue_id = (venue.get("id") or slug_base(name)).strip()
    venues = atomic_io.read_json(paths.VENUES_JSON, default=[])
    for existing in venues:
        if (existing.get("name") or "").strip().lower() == name.lower():
            return {"status": "exists",
                    "problems": [f"a venue named {name!r} already exists (id {existing.get('id')!r})"]}
        if existing.get("id") == venue_id:
            return {"status": "exists",
                    "problems": [f"a venue with id {venue_id!r} already exists"]}

    record = dict(venue)
    record["id"] = venue_id
    record["name"] = name
    if not record.get("styles"):
        record["styles"] = detect_styles(f"{name} {record.get('description', '')}")
    warnings: list[str] = []
    if record.get("lat") is None or record.get("lng") is None:
        coords = scraper_utils.geocode(record["location"])
        if coords:
            record["lat"], record["lng"] = coords
        else:
            warnings.append("could not geocode location — the venue will have no map pin until lat/lng are set")

    venues.append(record)
    atomic_io.write_json(paths.VENUES_JSON, venues)
    append_changelog("venue_add", venue_id, name)
    result = {"status": "added", "problems": [], "venue": record}
    if warnings:
        result["warnings"] = warnings
    return result


def load_venues() -> list[dict]:
    """Load permanent venues through the same strict JSON reader as the store."""
    return atomic_io.read_json(paths.VENUES_JSON, default=[])


def _validate_venue_record(venue: dict) -> list[str]:
    """Validate a complete venue record before add/edit persists it."""
    problems: list[str] = []
    for key in ("id", "name", "location", "url"):
        if not str(venue.get(key) or "").strip():
            problems.append(f"missing {key}")
    problems.extend(validate_venue_schedule(venue.get("schedule")))

    for key in ("styles", "urls", "excludeDates"):
        value = venue.get(key)
        if value is not None and not isinstance(value, list):
            problems.append(f"{key} must be an array")
    if isinstance(venue.get("styles"), list) and not all(
        isinstance(value, str) and value.strip() for value in venue["styles"]
    ):
        problems.append("styles entries must be non-empty strings")
    if isinstance(venue.get("urls"), list) and not all(
        isinstance(value, str) and value.strip() for value in venue["urls"]
    ):
        problems.append("urls entries must be non-empty strings")
    if isinstance(venue.get("excludeDates"), list):
        for value in venue["excludeDates"]:
            if not isinstance(value, str) or parse_anchor(value) is None:
                problems.append(f"excludeDates entry must be YYYY-MM-DD (got {value!r})")

    lat, lng = venue.get("lat"), venue.get("lng")
    if (lat is None) != (lng is None):
        problems.append("lat and lng must be provided together")
    if lat is not None and (
        not isinstance(lat, (int, float))
        or isinstance(lat, bool)
        or not -90 <= lat <= 90
    ):
        problems.append("lat must be a number between -90 and 90")
    if lng is not None and (
        not isinstance(lng, (int, float))
        or isinstance(lng, bool)
        or not -180 <= lng <= 180
    ):
        problems.append("lng must be a number between -180 and 180")
    return problems


@locked
def edit_venue(venue_id: str, updates: dict, dry_run: bool = False) -> dict:
    """Validate and update one permanent venue without allowing identity drift."""
    if not isinstance(updates, dict):
        return {"status": "invalid", "problems": ["updates must be an object"]}
    if "id" in updates and updates["id"] != venue_id:
        return {"status": "invalid", "problems": ["venue id cannot be changed"]}
    if ("lat" in updates) != ("lng" in updates):
        return {"status": "invalid", "problems": ["lat and lng updates must be provided together"]}

    venues = load_venues()
    idx = next((i for i, venue in enumerate(venues) if venue.get("id") == venue_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No venue with id '{venue_id}'"}

    before = venues[idx]
    candidate = dict(before)
    candidate.update(updates)
    candidate["id"] = venue_id
    if isinstance(candidate.get("name"), str):
        candidate["name"] = candidate["name"].strip()

    for i, other in enumerate(venues):
        candidate_name = candidate.get("name") if isinstance(candidate.get("name"), str) else ""
        if i != idx and (other.get("name") or "").strip().lower() == candidate_name.lower():
            return {
                "status": "exists",
                "problems": [f"a venue named {candidate.get('name')!r} already exists (id {other.get('id')!r})"],
            }

    location_changed = candidate.get("location") != before.get("location")
    coords_explicit = "lat" in updates or "lng" in updates
    if location_changed and not coords_explicit:
        coords = scraper_utils.geocode(candidate.get("location", ""))
        if not coords:
            return {
                "status": "invalid",
                "problems": ["new location could not be geocoded; provide both lat and lng explicitly"],
            }
        candidate["lat"], candidate["lng"] = coords

    problems = _validate_venue_record(candidate)
    if problems:
        return {"status": "invalid", "problems": problems}

    changed = {
        key: {"before": before.get(key), "after": candidate.get(key)}
        for key in sorted(set(before) | set(candidate))
        if before.get(key) != candidate.get(key)
    }
    if dry_run:
        return {"status": "dry_run", "venue": candidate, "changes": changed}
    if not changed:
        return {"status": "unchanged", "venue": before, "changes": {}}

    venues[idx] = candidate
    atomic_io.write_json(paths.VENUES_JSON, venues)
    append_changelog("venue_edit", venue_id, json.dumps(changed, sort_keys=True))
    return {"status": "updated", "venue": candidate, "changes": changed}

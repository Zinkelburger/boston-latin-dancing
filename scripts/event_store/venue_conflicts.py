"""Collisions between scraped events and venue schedule hubs.

At publish, a scraped event that lands on a hub's night is either folded into
the hub (plainly the same night) or kept and queued for review. The queue is
data/events/venue-conflicts.json; resolve_venue_conflict() records a ruling.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import atomic_io
import scraper_utils
from recurrence_utils import NY_TZ, parse_date

from . import paths, storage
from .locations import canonical_location, infer_location, location_key, locations_same
from .names import is_special_edition, names_are_same_series, normalize_name
from .occurrences import event_day_of_week
from .schedule import TIME_RE, matches_schedule_note, parse_time_range, schedule_date_allowed
from .sources import is_venue_schedule_record
from .storage import append_changelog, locked, log
from .venues import exclude_venue_date


def venue_schedule_covers_event(venue_event: dict, ev: dict, day: str) -> bool:
    """True when the hub's schedule would actually generate this event's date.

    A "1st Friday" hub must not swallow a scraped 5th-Friday event: the hub
    won't show that date, so suppressing the scrape would hide a real night.
    Entries whose note has no date pattern cover every such weekday, matching
    the old day-of-week behavior.
    """
    dt = parse_date(ev.get("startDate", ""))
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        dt = dt.astimezone(NY_TZ).replace(tzinfo=None)
    for entry in venue_event.get("schedule") or []:
        if entry.get("dayOfWeek") != day:
            continue
        if dt is None or (
            schedule_date_allowed(dt, entry)
            and matches_schedule_note(
                dt, entry.get("note", ""), day, entry.get("anchor")
            )
        ):
            return True
    return False


def _hub_schedule_entry(hub: dict, day: str) -> Optional[dict]:
    """The hub's schedule entry for a weekday, or None."""
    for entry in hub.get("schedule") or []:
        if entry.get("dayOfWeek") == day:
            return entry
    return None


# Fallback length for an event with a start but no end. Only used to decide
# overlap; the assumption is surfaced in the review row so a human can see it.
_ASSUMED_EVENT_HOURS = 3


def event_local_window(ev: dict) -> tuple[Optional[datetime], Optional[datetime], bool]:
    """Event start/end as naive New York datetimes, plus whether end was assumed."""
    start = parse_date(ev.get("startDate", ""))
    if start is None:
        return None, None, False
    if start.tzinfo is None:
        start = start.replace(tzinfo=NY_TZ)
    start = start.astimezone(NY_TZ).replace(tzinfo=None)

    end = parse_date(ev.get("endDate", ""))
    if end is None:
        return start, start + timedelta(hours=_ASSUMED_EVENT_HOURS), True
    if end.tzinfo is None:
        end = end.replace(tzinfo=NY_TZ)
    end = end.astimezone(NY_TZ).replace(tzinfo=None)
    if end <= start:
        return start, start + timedelta(hours=_ASSUMED_EVENT_HOURS), True
    return start, end, False


def _hub_local_window(entry: dict, on_date: datetime) -> Optional[tuple[datetime, datetime]]:
    """The hub's window on a given date, or None when the time can't be parsed.

    Closing times past midnight ("9:00 PM – 2:00 AM") roll into the next day.
    """
    parsed = parse_time_range(entry.get("time") or "")
    if not parsed:
        return None
    (sh, sm), (eh, em) = parsed
    day = on_date.replace(hour=0, minute=0, second=0, microsecond=0)
    start = day.replace(hour=sh, minute=sm)
    end = day.replace(hour=eh, minute=em)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _windows_overlap(hub: dict, entry: dict, ev: dict) -> tuple[Optional[bool], dict]:
    """Does the scraped event's window intersect the hub's window that night?

    Returns (overlap, detail). `overlap` is None when either side has no usable
    time — the caller must then treat the collision as a judgment call rather
    than guessing. Windows that merely touch (event ends exactly when the hub
    opens) do not overlap: that is the Battle-of-the-Beats shape, an afternoon
    program that hands off to the venue's regular night.
    """
    ev_start, ev_end, end_assumed = event_local_window(ev)
    detail: dict = {"event_end_assumed": end_assumed}
    if ev_start is None:
        detail["reason"] = "event has no parseable start time"
        return None, detail

    hub_window = _hub_local_window(entry, ev_start)
    if hub_window is None:
        detail["reason"] = f"hub time {entry.get('time')!r} is not parseable"
        return None, detail

    hub_start, hub_end = hub_window
    detail["event_window"] = _format_window(ev_start, ev_end)
    detail["hub_window"] = f"{entry.get('dayOfWeek', '')}s, {entry.get('time', '')}"
    return (ev_start < hub_end and hub_start < ev_end), detail


def _format_window(start: datetime, end: Optional[datetime]) -> str:
    def _t(d: datetime) -> str:
        return d.strftime("%-I:%M %p")
    if end is None:
        return f"{start.strftime('%a %b %-d')}, {_t(start)}"
    return f"{start.strftime('%a %b %-d')}, {_t(start)} – {_t(end)}"


# Generic words in a venue name that identify nothing on their own — "Club"
# alone must not make "Salsa Club Night" read as a Havana Club event.
_GENERIC_VENUE_WORDS = {"the", "club", "bar", "lounge", "studio", "dance",
                        "salsa", "bachata", "social", "boston", "cambridge"}


def _reads_like_hub_night(hub: dict, ev: dict) -> bool:
    """True when the scraped name reads like the venue's own regular night.

    Used only to decide whether a time-overlapping collision is obvious enough
    to fold silently, never to delete an event whose times don't overlap. A
    distinctly-branded name (anniversary, takeover, guest lineup) is exempt
    even when it names the venue — those are takeovers, which need a human to
    say whether they replace the regular night or run alongside it.
    """
    ev_name = normalize_name(ev.get("name", ""))
    hub_name = normalize_name(hub.get("name", ""))
    if not ev_name or not hub_name:
        return False
    if is_special_edition(ev_name):
        return False
    if names_are_same_series(ev_name, hub_name):
        return True
    distinctive = set(hub_name.split()) - _GENERIC_VENUE_WORDS
    return bool(distinctive) and bool(distinctive & set(ev_name.split()))


def _scraped_at_venue_hub(hub: dict, scraped: dict) -> bool:
    """True when scraped event is at the same venue as a schedule hub.

    Uses _locations_same() but rejects coords-only matches unless the scraped
    event also names the venue or shares its street address (avoids nearby venues).
    """
    if not locations_same(hub, scraped):
        return False

    hub_loc = (hub.get("location") or "").lower().strip()
    scraped_loc = (scraped.get("location") or "").lower().strip()
    if canonical_location(hub.get("location", "")) and canonical_location(scraped.get("location", "")):
        return True
    if hub_loc and scraped_loc and hub_loc == scraped_loc:
        return True

    hub_name = (hub.get("name") or "").lower()
    scraped_name = (scraped.get("name") or "").lower()
    if hub_name and (hub_name in scraped_name or hub_name in scraped_loc):
        return True

    hub_key = location_key(hub_loc)
    scraped_key = location_key(scraped_loc)
    if hub_key and scraped_key and (hub_key == scraped_key or hub_key in scraped_key or scraped_key in hub_key):
        return True

    return False


def _venue_match_reason(hub: dict, ev: dict) -> str:
    """Short label for *why* an event was considered to be at this hub."""
    hub_loc = (hub.get("location") or "").lower().strip()
    ev_loc = (ev.get("location") or "").lower().strip()
    if canonical_location(hub.get("location", "")) and canonical_location(ev.get("location", "")):
        return "canonical venue alias"
    if hub_loc and ev_loc and hub_loc == ev_loc:
        return "identical location string"
    hub_name = (hub.get("name") or "").lower()
    if hub_name and (hub_name in (ev.get("name") or "").lower() or hub_name in ev_loc):
        return f"venue name {hub.get('name')!r} appears in the event"
    return f"street address ({location_key(ev_loc) or ev_loc or 'unknown'})"


def _truncate(text: str, limit: int = 400) -> str:
    # Scraped blurbs open with boilerplate and a "Source: <url>" line; dropping
    # it buys back a chunk of the budget for text that actually informs.
    text = re.sub(r"Source:\s*\S+", " ", text or "")
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "… [truncated — event_get for full text]"


def _time_mentions(text: str, limit: int = 6) -> list[str]:
    """Lines in a description that state a clock time.

    An event's own run-of-show ("4:30 PM: Workshops / 7:00 PM: Social Dance
    Party") is the single most decisive evidence about whether it is the
    venue's regular night, and it usually sits far past any truncation point.
    Pull those lines out so the reviewer sees them without fetching the event.
    """
    found: list[str] = []
    for raw in (text or "").splitlines():
        line = " ".join(raw.split())
        if not line or not TIME_RE.search(line):
            continue
        if len(line) > 120:
            line = line[:120].rstrip() + "…"
        if line not in found:
            found.append(line)
        if len(found) >= limit:
            break
    return found


def _venue_conflict_row(hub: dict, ev: dict, entry: dict, overlap: Optional[bool],
                        detail: dict, kept: bool) -> dict:
    """Everything needed to decide this one collision, in one self-contained row.

    Deliberately carries facts only — no recommendation. A precomputed verdict
    would just be the old name-regex wearing a different hat, and the reviewer
    would anchor on it instead of reading the two descriptions.
    """
    other_days = [s.get("dayOfWeek", "")[:3] for s in hub.get("schedule") or []
                  if s.get("dayOfWeek") != entry.get("dayOfWeek")]
    row = {
        "id": ev.get("id"),
        "event": {
            "name": ev.get("name"),
            "window": detail.get("event_window") or ev.get("startDate", ""),
            "location": ev.get("location"),
            "styles": ev.get("styles", []),
            "cost": ev.get("cost"),
            "source": ev.get("source"),
            "url": ev.get("url"),
            "recurring": bool(ev.get("recurring")),
            "schedule_in_description": _time_mentions(ev.get("description") or ""),
            "description": _truncate(ev.get("description") or ""),
        },
        "hub": {
            "id": hub.get("id"),
            "name": hub.get("name"),
            "window": detail.get("hub_window")
                      or f"{entry.get('dayOfWeek', '')}s, {entry.get('time', '')}",
            "note": entry.get("note"),
            "cost": hub.get("cost"),
            "url": hub.get("url"),
            "also_runs": other_days,
            "description": _truncate(hub.get("description") or "", 200),
        },
        "times_overlap": overlap,
        "matched_on": _venue_match_reason(hub, ev),
    }
    if detail.get("event_end_assumed"):
        row["event_end_assumed"] = True
        row["assumed_note"] = (
            f"event had no usable end time; assumed {_ASSUMED_EVENT_HOURS}h for the overlap test"
        )
    if detail.get("reason"):
        row["overlap_unknown_because"] = detail["reason"]
    if kept:
        row["currently"] = "published — both pins showing"
        row["if_you_do_nothing"] = "stays published; this row returns next publish"
    else:
        row["currently"] = "suppressed — folded into the venue hub, no separate pin"
        row["if_you_do_nothing"] = "stays suppressed"
    return row


def _resolve_venue_collision(hub: dict, ev: dict, day: str) -> tuple[str, Optional[dict]]:
    """Decide what to do about one event/hub collision.

    Returns ("duplicate"|"keep", row). A row of None means a human or the
    review agent already ruled on this pair and it needs no further attention.
    """
    entry = _hub_schedule_entry(hub, day) or {}
    overlap, detail = _windows_overlap(hub, entry, ev)

    decided = ev.get("_venue_conflict_decision") or {}
    if decided.get("hub") == hub.get("id"):
        if decided.get("decision") == "duplicate":
            row = _venue_conflict_row(hub, ev, entry, overlap, detail, kept=False)
            row["resolved"] = decided
            return "duplicate", row
        return "keep", None

    # An event already flagged as a big one-off is never silently folded into a
    # weekly night, whatever the clock says. It still surfaces for review so the
    # call gets recorded once instead of being re-derived every publish.
    if ev.get("special"):
        return "keep", _venue_conflict_row(hub, ev, entry, overlap, detail, kept=True)

    if overlap and _reads_like_hub_night(hub, ev):
        return "duplicate", _venue_conflict_row(hub, ev, entry, overlap, detail, kept=False)

    return "keep", _venue_conflict_row(hub, ev, entry, overlap, detail, kept=True)


def suppress_venue_covered_events(
    venue_events: list[dict], active_events: list[dict]
) -> tuple[list[dict], set, dict]:
    """Resolve overlap between venue hubs and scraped events.

    Regular venues: a scraped event is folded into the hub only when it is
    plainly the hub's own weekly night — same place, same weekday, overlapping
    clock times, and a name that reads like the venue's night. Anything else
    stays on the map and is queued for review instead. Deleting an event is
    the one outcome that is invisible to visitors, so it requires the strongest
    evidence; a duplicate pin is merely untidy and self-correcting.

    Irregular venues (nextDateApproximate): scraped events WIN — the venue entry is
    suppressed when confirmed scraped events exist. This lets the "Date unconfirmed"
    venue entry show only when no confirmed scrape is available.

    Returns (kept, suppressed_venue_ids, report) where report carries the
    suppression log and the review queue.
    """
    regular_hubs = [v for v in venue_events if is_venue_schedule_record(v) and not v.get("nextDateApproximate")]
    irregular_hubs = [v for v in venue_events if is_venue_schedule_record(v) and v.get("nextDateApproximate")]

    report: dict = {"suppressed": [], "conflicts": []}

    if not regular_hubs and not irregular_hubs:
        return active_events, set(), report

    now = datetime.now(NY_TZ)

    def _has_future_date(ev: dict) -> bool:
        """True if event has a start date today or later."""
        dt = parse_date(ev.get("startDate", ""))
        if not dt:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        return dt.astimezone(NY_TZ).date() >= now.date()

    # Track which irregular venues have confirmed scraped events covering them
    irregular_hub_covered: set[str] = set()

    kept: list[dict] = []
    for ev in active_events:
        if is_venue_schedule_record(ev):
            kept.append(ev)
            continue

        day = event_day_of_week(ev)
        if not day:
            kept.append(ev)
            continue

        if not ev.get("location"):
            infer_location(ev)
        if ev.get("lat") is None and ev.get("location"):
            coords = scraper_utils.geocode(ev["location"])
            if coords:
                ev["lat"], ev["lng"] = coords

        # Check regular hubs — same place, same weekday, hub's schedule admits
        # the date. That is a *collision*, not yet a verdict.
        colliding_hub = None
        for hub in regular_hubs:
            if not venue_schedule_covers_event(hub, ev, day):
                continue
            if _scraped_at_venue_hub(hub, ev):
                colliding_hub = hub
                break

        if colliding_hub is not None:
            verdict, row = _resolve_venue_collision(colliding_hub, ev, day)
            if verdict == "duplicate":
                report["suppressed"].append(row)
                continue
            if row is not None:
                report["conflicts"].append(row)

        # Check irregular hubs — only future scraped events from the matching
        # source can confirm (suppress) the venue entry.
        if _has_future_date(ev):
            ev_source = ev.get("source", "")
            for hub in irregular_hubs:
                # The scraped event must be at the venue's location to confirm it,
                # otherwise an unrelated event from the same source (different
                # venue/series) would wrongly suppress the placeholder.
                if not _scraped_at_venue_hub(hub, ev):
                    continue
                # A configured source link tightens the match further: only that
                # source's events at this location count as confirmation.
                hub_source_id = hub.get("_sourceId", "")
                if hub_source_id and ev_source != hub_source_id:
                    continue
                irregular_hub_covered.add(hub["id"])
                if not ev.get("cost") and hub.get("cost"):
                    ev["cost"] = hub["cost"]
                if not ev.get("url") and hub.get("url"):
                    ev["url"] = hub["url"]
                if not ev.get("urls") and hub.get("urls"):
                    ev["urls"] = hub["urls"]
                break

        kept.append(ev)

    # Suppress irregular venue entries that have confirmed scraped events
    suppressed_venues = set()
    for hub in irregular_hubs:
        if hub["id"] in irregular_hub_covered:
            suppressed_venues.add(hub["id"])

    return kept, suppressed_venues, report


VENUE_CONFLICT_DECISIONS = ("distinct", "replaces", "duplicate")


def load_venue_conflicts() -> dict:
    """The review queue written by the last publish()."""
    return atomic_io.read_json(
        paths.VENUE_CONFLICTS_JSON,
        default={"generated_at": None, "conflicts": [], "suppressed": []},
    )


@locked
def resolve_venue_conflict(event_id: str, decision: str, note: str = "",
                           hub_id: Optional[str] = None) -> dict:
    """Record a ruling on an event/venue-hub collision so it never re-surfaces.

    distinct  — both are real; the event keeps its own pin alongside the hub.
    replaces  — the event takes over the venue that night; the hub is told to
                skip that date so a phantom pin for the usual night doesn't ship.
    duplicate — the scrape is just the hub's weekly night; fold it in.
    """
    if decision not in VENUE_CONFLICT_DECISIONS:
        return {"status": "error",
                "error": f"decision must be one of {', '.join(VENUE_CONFLICT_DECISIONS)}"}

    if hub_id is None:
        queue = load_venue_conflicts()
        for row in queue.get("conflicts", []) + queue.get("suppressed", []):
            if row.get("id") == event_id:
                hub_id = row.get("hub", {}).get("id")
                break
    if hub_id is None:
        return {"status": "error",
                "error": f"No venue conflict on record for '{event_id}'. "
                         "Run event_publish() to refresh the queue, or pass hub_id."}

    active = storage.load_active()
    event = next((e for e in active if e.get("id") == event_id), None)
    if event is None:
        return {"status": "error", "error": f"Event '{event_id}' is not in active."}

    event["_venue_conflict_decision"] = {
        "hub": hub_id,
        "decision": decision,
        "at": datetime.now(timezone.utc).isoformat(),
        "note": note,
    }

    excluded_date = None
    if decision == "replaces":
        start, _end, _assumed = event_local_window(event)
        if start is None:
            return {"status": "error",
                    "error": "Cannot exclude a hub date: event has no parseable startDate."}
        excluded_date = start.strftime("%Y-%m-%d")
        try:
            exclude_venue_date(hub_id, excluded_date)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

    storage.save_active(active)
    append_changelog("venue_conflict_resolved", event_id,
                      f"{decision} vs hub {hub_id}" + (f": {note}" if note else ""))
    return {
        "status": "resolved",
        "event_id": event_id,
        "event_name": event.get("name"),
        "hub": hub_id,
        "decision": decision,
        "hub_date_excluded": excluded_date,
        "next": "Run event_publish() to apply.",
    }


def write_venue_conflicts(report: dict) -> None:
    """Persist the venue-hub review queue and say out loud what got folded.

    Suppression used to be silent, which is why a marquee event sat deleted for
    a week while every pipeline run reported success. Anything the pipeline
    removes from the map now names itself at publish time (on stderr).
    """
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "conflicts": report.get("conflicts", []),
        "suppressed": report.get("suppressed", []),
    }
    atomic_io.write_json(paths.VENUE_CONFLICTS_JSON, payload)

    # Reporting must never be what breaks a publish, so read every field softly.
    for row in payload["suppressed"]:
        ev, hub = row.get("event", {}), row.get("hub", {})
        resolved = " [resolved: duplicate]" if row.get("resolved") else ""
        log(f"  🔇 folded into venue hub: {ev.get('name')!r} ({ev.get('window', '?')}) "
             f"→ {hub.get('name')} {hub.get('window', '?')}{resolved}")
    if payload["conflicts"]:
        log(f"  🔎 {len(payload['conflicts'])} venue conflict(s) need review "
             f"(kept on the map meanwhile) — event_list(status=\"venue_conflict\"):")
        for row in payload["conflicts"]:
            overlap = {True: "times overlap", False: "no time overlap"}.get(
                row.get("times_overlap"), "overlap unknown")
            log(f"       - {row.get('event', {}).get('name')!r} "
                 f"vs {row.get('hub', {}).get('name')} ({overlap})")

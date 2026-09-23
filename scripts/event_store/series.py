"""Recurring series: collapsing per-date records into one pin, and rolling
a stale series forward to its next night."""

from datetime import datetime
from typing import Optional

from recurrence_utils import NY_TZ, parse_date

from .locations import location_key
from .names import is_special_edition, names_are_same_series, normalize_name
from .occurrences import eastern_iso, event_day_of_week, occurrence_instants, parse_aware, weekday_of
from .sources import is_venue_schedule_record, source_rank
from .urls import collect_urls, event_url_list, url_rank


def _collapse_urls(group_events: list[dict], next_start: str) -> tuple[Optional[str], list[str]]:
    """Pick the best primary URL for a collapsed series.

    Source rank decides which *record* wins. Link quality is separate: among
    equal-quality URLs, prefer the one that belongs to the next occurrence so
    a closed past listing on the same host does not outrank the open one.
    """
    next_dt = parse_aware(next_start)
    all_urls: list[str] = []
    next_keys: set[str] = set()
    seen: set[str] = set()
    for ev in group_events:
        ev_start = parse_aware(ev.get("startDate", ""))
        from_next = bool(next_dt and ev_start and ev_start == next_dt)
        for u in event_url_list(ev):
            key = u.rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                all_urls.append(u)
            # Flag the key on *any* member dated at the next occurrence — the
            # winning record often already carries that member's URL in its
            # own urls[], and attributing the flag only to whichever record
            # happened to mention it first left the stale link primary.
            if from_next:
                next_keys.add(key)
    if not all_urls:
        return None, []

    def rank(u: str) -> tuple[int, int]:
        key = u.rstrip("/").lower()
        return (url_rank(u), 0 if key in next_keys else 1)

    primary = min(all_urls, key=rank)
    primary_key = primary.rstrip("/").lower()
    extras = [
        u for u in collect_urls({"url": primary}, *group_events)
        if u.rstrip("/").lower() != primary_key
    ]
    return primary, extras


def collapse_recurring_series(events: list[dict]) -> list[dict]:
    groups: list[list[int]] = []
    assigned: set[int] = set()

    for i, ev_i in enumerate(events):
        if i in assigned:
            continue
        group = [i]
        assigned.add(i)
        name_i = normalize_name(ev_i["name"])
        loc_i = location_key(ev_i.get("location", ""))
        dow_i = event_day_of_week(ev_i)

        for j, ev_j in enumerate(events):
            if j in assigned:
                continue
            name_j = normalize_name(ev_j["name"])
            loc_j = location_key(ev_j.get("location", ""))
            dow_j = event_day_of_week(ev_j)

            # Venue schedule hubs (e.g. Havana Club) share a location/name with
            # scraped night-specific series but are distinct map entries.
            if is_venue_schedule_record(ev_i) != is_venue_schedule_record(ev_j):
                continue

            if dow_i != dow_j:
                continue

            if not names_are_same_series(name_i, name_j):
                continue
            # A distinctly-branded special edition (anniversary, festival, guest
            # artist, themed night) sharing a series' venue + weeknight should
            # stay its own pin rather than vanish into the generic series name.
            if name_i != name_j and is_special_edition(name_i) != is_special_edition(name_j):
                continue
            if loc_i and loc_j:
                if loc_i != loc_j and loc_i not in loc_j and loc_j not in loc_i:
                    continue
            elif loc_i != loc_j:
                continue

            group.append(j)
            assigned.add(j)

        groups.append(group)

    result: list[dict] = []
    for idx_group in groups:
        group_events = [events[i] for i in idx_group]
        if len(group_events) == 1:
            result.append(group_events[0])
            continue

        group_events.sort(key=source_rank)
        best = dict(group_events[0])
        # Union every member's dates: a member may itself already carry a
        # recurrences[] (e.g. a previously-collapsed series), not just a single
        # startDate. Dropping those would lose future occurrences. Union by
        # *instant*: the strings mix +00:00 and -04:00 spellings of the same
        # moment, and sorting strings interleaved them and kept both.
        instants: dict[float, datetime] = {}
        for ev in group_events:
            for occ in occurrence_instants(ev):
                instants.setdefault(occ.timestamp(), occ)
        if not instants:
            result.extend(group_events)
            continue
        date_dts = [instants[k] for k in sorted(instants)]
        dates: list[str] = [eastern_iso(d) for d in date_dts]

        # Preserve the event's duration so endDate never desyncs from startDate
        # when we roll forward to an occurrence no single member's startDate
        # matches (the new startDate often comes from a member's recurrences[]).
        orig_start = parse_date(best.get("startDate", ""))
        orig_end = parse_date(best.get("endDate", ""))
        duration = orig_end - orig_start if (orig_start and orig_end and orig_end >= orig_start) else None

        def _roll_end(new_start_iso: str) -> Optional[str]:
            new_start = parse_aware(new_start_iso)
            if new_start is None or duration is None:
                return None
            return eastern_iso(new_start + duration)

        now = datetime.now(NY_TZ)
        future = [d for d in date_dts if d >= now]
        new_start = eastern_iso(future[0] if future else date_dts[-1])
        best["startDate"] = new_start
        rolled_end = _roll_end(new_start)
        if rolled_end:
            best["endDate"] = rolled_end

        best["recurring"] = True
        best["recurrences"] = dates
        best["dayOfWeek"] = weekday_of(new_start) or best.get("dayOfWeek")

        for ev in group_events[1:]:
            if (best.get("lat") is None or best.get("lng") is None) and ev.get("lat") and ev.get("lng"):
                best["lat"] = ev["lat"]
                best["lng"] = ev["lng"]
            if not best.get("cost") and ev.get("cost"):
                best["cost"] = ev["cost"]

        # Primary link must be the working page for the *next* night, not
        # whichever member won on source rank (that is how By the River kept
        # a closed July Lister URL after Sep/Oct listings collapsed into it).
        primary, extra = _collapse_urls(group_events, new_start)
        if primary:
            best["url"] = primary
            if extra:
                best["urls"] = extra
            else:
                best.pop("urls", None)

        result.append(best)

    return result


def roll_series_forward(ev: dict, today: datetime) -> bool:
    """Advance a stale recurring series to its next occurrence, for publish.

    A live weekly series whose stored startDate is weeks old leaks that date
    into JSON-LD, meta descriptions and the search dropdown. When the event
    carries a recurrences[] list and its startDate is before ``today``, move
    startDate (and endDate by the same delta) to the first occurrence on or
    after today and keep the original in ``firstStartDate``. Only the
    published copy changes; the stored record and its id are untouched.
    Returns True when something moved.
    """
    if not ev.get("recurrences"):
        return False
    start = parse_aware(ev.get("startDate", ""))
    if start is None or start >= today:
        return False
    occurrences = occurrence_instants(ev)
    upcoming = [d for d in occurrences if d >= today]
    if not upcoming:
        return False
    new_start = upcoming[0]
    delta = new_start - start
    ev["firstStartDate"] = ev["startDate"]
    ev["startDate"] = eastern_iso(new_start)
    end = parse_aware(ev.get("endDate", ""))
    if end is not None:
        ev["endDate"] = eastern_iso(end + delta)
    ev["recurrences"] = [eastern_iso(d) for d in occurrences]
    ev["dayOfWeek"] = weekday_of(ev["startDate"]) or ev.get("dayOfWeek")
    return True

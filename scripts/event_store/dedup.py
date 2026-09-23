"""Duplicate detection and merging.

dedup_confidence() grades a pair "certain" (auto-merge), "review" (queue for
a human) or None; merge_event() combines two records of one event.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import atomic_io
from recurrence_utils import DAYS_LIST

from . import paths
from .known_duplicates import known_duplicate_verdict
from .locations import locations_same
from .names import (
    content_words,
    distinctive_words,
    name_styles,
    normalize_name,
    shared_word_count,
)
from .occurrences import (
    dates_within,
    event_day_of_week,
    last_occurrence,
    parse_aware,
    same_calendar_day,
    wall_clock_minutes,
)
from .sources import is_venue_schedule_record, pick_winner, source_rank
from .urls import collect_urls, dropped_url_list, url_key, url_match, url_rank


# Two recurring nights at one venue on one weekday still are not the same
# series when their titles claim different dances or their doors open hours
# apart. This is the gap a "certain" verdict must not walk through.
_SERIES_MAX_START_GAP_MIN = 120


def _named_weekdays_conflict(a: dict, b: dict) -> bool:
    """True when both titles explicitly name different weekdays."""
    def named(event: dict) -> set[str]:
        title = (event.get("name") or "").lower()
        return {day for day in DAYS_LIST if day.lower() in title}

    days_a, days_b = named(a), named(b)
    return bool(days_a and days_b and days_a.isdisjoint(days_b))


def _series_signals_conflict(a: dict, b: dict) -> bool:
    """True when two same-venue, same-weekday recurring names read as two
    different nights: the titles name different styles (one says bachata,
    the other salsa, neither says both), or the start times sit more than
    _SERIES_MAX_START_GAP_MIN apart on the clock."""
    styles_a, styles_b = name_styles(a.get("name", "")), name_styles(b.get("name", ""))
    if styles_a and styles_b and not (styles_a <= styles_b or styles_b <= styles_a):
        return True
    mins_a, mins_b = wall_clock_minutes(a), wall_clock_minutes(b)
    if mins_a is not None and mins_b is not None:
        gap = abs(mins_a - mins_b)
        gap = min(gap, 24 * 60 - gap)
        if gap > _SERIES_MAX_START_GAP_MIN:
            return True
    return False


def dedup_confidence(a: dict, b: dict) -> Optional[str]:
    """Determine dedup confidence between two events.

    Returns:
      "certain" – same ID, URL, or multi-signal match; auto-merge
      "review"  – suspicious match; route to pending
      None      – not a duplicate
    """
    id_a, id_b = a.get("id"), b.get("id")
    if not id_a or not id_b:
        return None

    if is_venue_schedule_record(a) != is_venue_schedule_record(b):
        return None

    known = known_duplicate_verdict(a, b)
    if known == "certain":
        return "certain"
    if known == "skip":
        return None

    if id_a == id_b:
        return "certain"

    if url_match(a, b):
        # A shared URL is conclusive only when the dates agree (or can't be
        # compared). Series whose occurrences all share one organizer URL
        # (e.g. Fiesta's /upcoming-socials page) must not merge distinct
        # dates into one record — each occurrence stays its own event and
        # collapse_recurring_series groups them at publish.
        if dates_within(a, b, 24) is not False:
            return "certain"

    name_a_raw = a.get("name")
    name_b_raw = b.get("name")
    if not name_a_raw or not name_b_raw:
        return None

    name_a = normalize_name(name_a_raw)
    name_b = normalize_name(name_b_raw)
    if not name_a or not name_b:
        return None

    within_24h = dates_within(a, b, 24)
    same_day = same_calendar_day(a, b)
    same_loc = locations_same(a, b)
    names_exact = (name_a == name_b)
    names_substring = (name_a in name_b or name_b in name_a) and not names_exact

    words_a = content_words(name_a)
    words_b = content_words(name_b)
    word_overlap_strong = False
    if words_a and words_b:
        shared = shared_word_count(words_a, words_b)
        smaller = min(len(words_a), len(words_b))
        # The overlap must include at least one word that actually identifies
        # this event. Without this, "Salsa and bachata rooftop party" (Allston,
        # 2 PM) matched "Black Mamba's Salsa and Bachata Social" (Natick, 7 PM)
        # on {salsa, bachata} alone — two words shared by most of the calendar.
        shared_distinctive = shared_word_count(
            distinctive_words(words_a), distinctive_words(words_b))
        if smaller > 0 and shared >= max(2, smaller * 0.5) and shared_distinctive >= 1:
            word_overlap_strong = True

    # "certain" tier: multiple strong signals converge — these are always the
    # same event from different sources (e.g. Eventbrite + calendar listing)
    if same_day is True and same_loc and (names_exact or names_substring or word_overlap_strong):
        return "certain"

    if names_exact and same_loc and within_24h is True:
        return "certain"

    # Cross-source recurring series: same weekly event published by different
    # calendars (e.g. venue calendar + organizer calendar). Occurrence dates
    # differ (so no date-proximity check applies), but the series is the same.
    # Substring matches are deliberately excluded here — "salsa" is a substring
    # of "salsa & bachata social", and two distinct weekly series sharing a
    # venue + weekday must not be auto-merged. Substring matches fall through to
    # the within_7d "review" tier below.
    if same_loc and (names_exact or word_overlap_strong):
        a_recurring = a.get("recurring") or bool(a.get("recurrences"))
        b_recurring = b.get("recurring") or bool(b.get("recurrences"))
        if a_recurring and b_recurring:
            dow_a = event_day_of_week(a)
            dow_b = event_day_of_week(b)
            if dow_a and dow_b and dow_a == dow_b:
                # Venue + weekday + shared words is not enough on its own:
                # "Havana Club Bachata Thursdays" and "Havana Club Salsa
                # Thursdays" clear all three and are two different nights.
                # Different styles in the titles, or doors hours apart,
                # demote the pair to review so a human decides.
                if _series_signals_conflict(a, b):
                    return "review"
                return "certain"

    # "review" tier: single-signal or weaker matches
    if names_exact and within_24h is True:
        return "review"

    if same_loc and same_day is True:
        return "review"

    if names_substring and within_24h is True:
        return "review"

    if word_overlap_strong and within_24h is True:
        return "review"

    if names_exact and within_24h is None:
        return "review"

    # Cross-source recurring series: same venue + strong name match but different
    # occurrence dates (>24h apart). Flag for review so they can be merged.
    within_7d = dates_within(a, b, 168)
    if same_loc and (names_exact or names_substring or word_overlap_strong) and within_7d is True:
        if (
            (a.get("recurring") or a.get("recurrences"))
            and (b.get("recurring") or b.get("recurrences"))
            and _named_weekdays_conflict(a, b)
        ):
            return None
        return "review"

    return None


def dedup_reason(a: dict, b: dict, confidence: str) -> str:
    """Build a human-readable reason string for the audit log."""
    parts = []
    name_a = normalize_name(a.get("name", ""))
    name_b = normalize_name(b.get("name", ""))

    if a.get("id") == b.get("id"):
        parts.append("same_id")
    elif url_match(a, b):
        parts.append("same_url")
    elif name_a == name_b:
        parts.append("exact_name")
    elif name_a in name_b or name_b in name_a:
        parts.append("substring_name")
    elif name_a and name_b:
        words_a = content_words(name_a)
        words_b = content_words(name_b)
        overlap = words_a & words_b
        parts.append(f"word_overlap({len(overlap)}/{min(len(words_a), len(words_b))})")

    within = dates_within(a, b, 24)
    if within is True:
        parts.append("within_24h")
    elif within is None:
        parts.append("no_dates")

    if locations_same(a, b):
        parts.append("same_location")

    if same_calendar_day(a, b) is True:
        parts.append("same_day")

    return "+".join(parts)


def log_dedup(action: str, kept: dict, candidate: dict, confidence: str, reason: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "kept_id": kept["id"],
        "kept_name": kept["name"][:80],
        "candidate_id": candidate["id"],
        "candidate_name": candidate["name"][:80],
    }
    atomic_io.append_line(paths.DEDUP_LOG, json.dumps(entry))


def merge_event(a: dict, b: dict) -> dict:
    """Merge two events, keeping the higher-precedence record as the base."""
    winner, loser = pick_winner(a, b)
    merged = dict(winner)

    # Preserve location overrides set by verification or manual fix.
    if winner.get("_location_override"):
        merged["location"] = winner["_location_override"]
        merged["_location_override"] = winner["_location_override"]
        if winner.get("lat") is not None:
            merged["lat"] = winner["lat"]
            merged["lng"] = winner["lng"]
    elif loser.get("location") and not merged.get("location"):
        merged["location"] = loser["location"]

    # Preserve verification metadata from whichever side has it (prefer winner).
    for key in (
        "_verified_at",
        "_verified_status",
        "_verified_notes",
        "_verification_url",
        "_verification_attestation",
        "_location_override",
    ):
        if winner.get(key):
            merged[key] = winner[key]
        elif loser.get(key):
            merged[key] = loser[key]

    # Preserve a manually-set `special` big-event override when the stored
    # (flagged) record loses to a fresh scrape of the same event.
    if merged.get("special") is None and loser.get("special") is not None:
        merged["special"] = loser["special"]

    # Same for a recorded venue-conflict ruling. Without this a re-scrape wins
    # on source precedence, arrives with no decision attached, and the pair is
    # back in the review queue every week — the reviewer re-litigates a call
    # they already made, which is exactly the failure this queue exists to end.
    if merged.get("_venue_conflict_decision") is None and loser.get("_venue_conflict_decision"):
        merged["_venue_conflict_decision"] = loser["_venue_conflict_decision"]

    # Never overwrite winner fields with loser content when winner is a venue hub.
    if is_venue_schedule_record(winner):
        if not merged.get("description") and loser.get("description"):
            merged["description"] = loser["description"]
    elif not merged.get("description") and loser.get("description"):
        merged["description"] = loser["description"]

    if not merged.get("url") and loser.get("url"):
        merged["url"] = loser["url"]
    if not merged.get("cost") and loser.get("cost"):
        merged["cost"] = loser["cost"]
    # Prefer a specific price over "Free" when the loser is a ticketing source
    elif (merged.get("cost") or "").lower() == "free" and loser.get("cost") and loser["cost"].lower() != "free":
        if loser.get("source") in ("eventbrite-boston-latin",) or "$" in loser.get("cost", ""):
            merged["cost"] = loser["cost"]
    if (merged.get("lat") is None or merged.get("lng") is None) and loser.get("lat") and loser.get("lng"):
        merged["lat"] = loser["lat"]
        merged["lng"] = loser["lng"]
    if merged.get("styles") == ["other"] and loser.get("styles") != ["other"]:
        merged["styles"] = loser["styles"]
    if not merged.get("recurring") and loser.get("recurring"):
        merged["recurring"] = True
    if not merged.get("schedule") and loser.get("schedule"):
        merged["schedule"] = loser["schedule"]
    if not merged.get("recurrences") and loser.get("recurrences"):
        merged["recurrences"] = loser["recurrences"]

    # Accumulate all source URLs into urls[], promoting the best-quality link
    # to primary. A merge must never downgrade a working canonical page to a
    # share wrapper just because the record carrying the wrapper won on source
    # precedence — ties keep the winner's existing order, so this only ever
    # fires when the alternative is strictly better.
    all_urls = collect_urls(winner, loser)
    dropped = dropped_url_list(winner) + dropped_url_list(loser)
    if dropped:
        merged["_dropped_urls"] = sorted({url_key(u): u for u in dropped}.values())
        suppressed = {url_key(u) for u in dropped}
        # A URL the reviewer struck off stays off, however many re-scrapes
        # hand it back. Only drop it while something still links the event —
        # a record with no link at all is worse than a stale one.
        remaining = [u for u in all_urls if url_key(u) not in suppressed]
        if remaining:
            all_urls = remaining
    if all_urls:
        primary = min(all_urls, key=lambda u: (url_rank(u), all_urls.index(u)))
        merged["url"] = primary
        extra = [u for u in all_urls if u and u != primary]
        if extra:
            merged["urls"] = extra
        else:
            merged.pop("urls", None)

    # Re-scrape of the same event id: refresh date/time from incoming data.
    # recurrences[] has to come along, or the record ends up describing two
    # different weeks — a refreshed startDate with a months-old occurrence
    # list, which is what quietly archived "Rueda in the Pahk" mid-season.
    # An incoming copy with no recurrences[] is a single occurrence, not a
    # claim that the series ended, so it never clears a stored list.
    if winner.get("id") == loser.get("id"):
        for key in ("startDate", "endDate", "dayOfWeek", "recurrences"):
            if loser.get(key):
                merged[key] = loser[key]

    # Calendar providers sometimes replace a series UID while retaining its
    # title, venue, weekday, and canonical URL. Dedup correctly recognizes the
    # new UID as the same recurring event, but source precedence ties used to
    # keep the older occurrence window forever. When two copies from the same
    # source are recurring series, take the schedule with the furthest coverage
    # (and, on a tie, the later start) so a fresh feed can extend the season.
    elif (
        a.get("source")
        and a.get("source") == b.get("source")
        and (a.get("recurring") or a.get("recurrences"))
        and (b.get("recurring") or b.get("recurrences"))
        and a.get("recurrences")
        and b.get("recurrences")
    ):
        def _series_freshness(event: dict) -> tuple[datetime, datetime]:
            floor = datetime.min.replace(tzinfo=timezone.utc)
            return (
                last_occurrence(event) or floor,
                parse_aware(event.get("startDate", "")) or floor,
            )

        freshest = max((a, b), key=_series_freshness)
        if freshest is not winner:
            for key in ("startDate", "endDate", "dayOfWeek", "recurrences"):
                if freshest.get(key):
                    merged[key] = freshest[key]

    # A time the reviewer corrected by hand outranks the source's, the same way
    # a location override does. Eventbrite listed a Saturday-night fundraiser at
    # 6 AM; the weekly review fixed it and the next same-id refresh above put
    # the 6 AM start straight back.
    for side in (winner, loser):
        override = side.get("_time_override")
        if override:
            merged.update(override)
            merged["_time_override"] = override
            if side.get("dayOfWeek"):
                merged["dayOfWeek"] = side["dayOfWeek"]
            break

    return merged


def find_duplicate_in(event: dict, pool: list[dict]) -> Optional[tuple[int, str]]:
    """Return (index, confidence) of best duplicate in pool, or None."""
    # An exact ID match is the same record re-scraped — it must win over any
    # other certain-tier match, or a refresh merges into a lookalike from a
    # different source and the true record never gets updated.
    for i, existing in enumerate(pool):
        if existing.get("id") == event.get("id"):
            conf = dedup_confidence(existing, event)
            if conf is not None:
                return (i, conf)
            break

    best_idx: Optional[int] = None
    best_conf: Optional[str] = None
    conf_rank = {"certain": 0, "review": 1}

    for i, existing in enumerate(pool):
        conf = dedup_confidence(existing, event)
        if conf is None:
            continue
        if best_conf is None or conf_rank[conf] < conf_rank[best_conf]:
            best_idx = i
            best_conf = conf
            if conf == "certain":
                break

    if best_idx is not None and best_conf is not None:
        return (best_idx, best_conf)
    return None


def deduplicate(events: list[dict], *, record_log: bool = True) -> list[dict]:
    """Deduplicate for publish. Only merges 'certain' matches."""
    events.sort(key=source_rank)
    result: list[dict] = []
    for ev in events:
        match = find_duplicate_in(ev, result)
        if match is not None:
            idx, conf = match
            if conf == "certain":
                reason = dedup_reason(result[idx], ev, conf)
                if record_log:
                    log_dedup(conf, result[idx], ev, conf, reason)
                result[idx] = merge_event(result[idx], ev)
            else:
                result.append(ev)
        else:
            result.append(ev)
    return result

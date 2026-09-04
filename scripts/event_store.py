"""
Event store: the canonical lifecycle layer for boston-latin-dance events.

Manages JSON files:
  data/events/active.json   – current/upcoming events (published to map)
  data/events/archive.json  – past events (for dedup + history)
  data/events/pending.json  – unreviewed user submissions
  data/events/rejected.json – non-Latin events flagged for agent review
  data/events/blocked.json  – permanently excluded events (checked at ingest)

Also reads:
  data/venues.json          – permanent weekly venue schedules

Provides:
  - CRUD operations with dedup, geocode, validation
  - Archive lifecycle (active -> archive when past)
  - Reactivation (archive -> active when event recurs)
  - Block lifecycle (permanent exclusion with categories)
  - Publish step (generate public/events.json)
"""

import functools
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unicodedata import normalize as unicode_normalize

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_io
from atomic_io import CorruptJSONError  # noqa: F401 - re-exported for callers
from scraper_utils import ROOT, SCRAPED_DIR, VENUE_COORDS, clean_location, geocode, detect_styles, extract_cost, load_sources, mentions_latin, _eventbrite_address, _normalize, _is_near_boston
from source_signal import noisy_source_ids, unreliable_source_ids
# Calendar constants and the ISO parser live in recurrence_utils (which does not
# import this module) so the two never drift. They are re-exported from here
# because the MCP server imports parse_date / DAYS_LIST / NY_TZ from event_store.
from recurrence_utils import DAY_INDEX, DAYS_LIST, NY_TZ, parse_date, recurrence_label  # noqa: F401

# ── Paths ─────────────────────────────────────────────────────────────

EVENTS_DIR = ROOT / "data" / "events"
ACTIVE_JSON = EVENTS_DIR / "active.json"
ARCHIVE_JSON = EVENTS_DIR / "archive.json"
PENDING_JSON = EVENTS_DIR / "pending.json"
REJECTED_JSON = EVENTS_DIR / "rejected.json"
BLOCKED_JSON = EVENTS_DIR / "blocked.json"
CHANGELOG = EVENTS_DIR / "changelog.jsonl"
VENUES_JSON = ROOT / "data" / "venues.json"
PUBLIC_EVENTS_JSON = ROOT / "data" / "events-published.json"

DEDUP_LOG = EVENTS_DIR / "dedup-log.jsonl"
KNOWN_DUPLICATES_JSON = ROOT / "data" / "known_duplicates.json"
SOURCES_JSON = ROOT / "data" / "sources.json"
LOCATION_ALIASES_JSON = ROOT / "data" / "location-aliases.json"
# Publish-time review queue: scraped events that collide with a venue hub but
# are not obviously the hub's regular night. Regenerated every publish.
VENUE_CONFLICTS_JSON = EVENTS_DIR / "venue-conflicts.json"

# One lock for the whole store. Every read/modify/write of any store file —
# active, archive, pending, rejected, blocked, known duplicates, venues,
# sources — runs under it, so the long-lived MCP server, the cron pipeline and
# the review CLIs serialise instead of overwriting each other's saves. A single
# store-wide lock (rather than one per file) means a multi-file move can never
# deadlock on lock ordering. atomic_io.locked is re-entrant, so lifecycle
# functions may call each other freely. The sidecar is <STORE_LOCK>.lock.
STORE_LOCK = EVENTS_DIR / "store"

EVENTS_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str) -> None:
    """Operator-facing progress and warnings. Always stderr: the MCP server
    speaks JSON-RPC on stdout, and a stray print there corrupts the stream."""
    print(msg, file=sys.stderr, flush=True)


def store_lock():
    """Context manager holding the store-wide lock (re-entrant).

    CLIs and the MCP server wrap any direct load_*/modify/save_* sequence in
    it so their write cannot race a lifecycle function in another process.
    """
    return atomic_io.locked(STORE_LOCK)


def _locked(fn):
    """Run a lifecycle function under the store-wide lock."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with atomic_io.locked(STORE_LOCK):
            return fn(*args, **kwargs)
    return wrapper

# ── Source priority ───────────────────────────────────────────────────
# Lower rank = higher precedence. Venue schedule hubs always win (see source_rank).

SOURCE_PRIORITY = {
    "manual": 0,
    "submissions": 1,
    "recurring-venues": 2,
    "beatrice-calendar": 10,
    "sensualeros-boston": 10,
    "eventbrite-boston-latin": 11,
    "lister-events": 12,
    "fiesta-dance-company": 12,
    "bobas": 13,
    "dantes-salsa": 13,
    "sabor-latino": 13,
    "unabulla-cuban-boston": 10,
    "timba-messengers": 11,
    "mato-lawn-on-d": 12,
    "lowell-sitp": 13,
    "nlf-events": 12,
    "pr-festival-ma": 14,
    "eastboston-events": 14,
    "harvardsquare": 14,
    "lous-live": 13,
    "jandl-events": 13,
    "": 20,
}

VENUE_HUB_RANK = -1000


def _is_venue_schedule_record(event: dict) -> bool:
    """Venue hub records carry a weekly schedule and must not collapse into scraped series."""
    return bool(event.get("schedule"))


def source_rank(event: dict) -> int:
    if _is_venue_schedule_record(event):
        return VENUE_HUB_RANK
    return SOURCE_PRIORITY.get(event.get("source", ""), 50)


def pick_winner(a: dict, b: dict) -> tuple[dict, dict]:
    """Return (winner, loser) by source precedence. Never uses description length."""
    if source_rank(a) <= source_rank(b):
        return a, b
    return b, a


# ── Location aliases ──────────────────────────────────────────────────
# Maps variant location names to a canonical key so events at the same
# physical venue match even when sources name the venue differently. The
# table itself is data, not code: data/location-aliases.json, shaped
# {"canonical-key": ["alias", "alias", ...]}. Keys starting with "_" are notes.


def _load_location_aliases(path: Optional[Path] = None) -> dict[str, str]:
    """Flatten the aliases file into the alias -> canonical-key map that
    _canonical_location() walks. Insertion order is preserved because the
    substring pass takes the first alias that matches, so the file's order is
    the precedence order. A missing file means no aliases; a corrupt one raises."""
    raw = atomic_io.read_json(path or LOCATION_ALIASES_JSON, default={})
    aliases: dict[str, str] = {}
    for key, variants in raw.items():
        if key.startswith("_"):
            continue
        for alias in variants:
            aliases[alias.lower().strip()] = key
    return aliases


LOCATION_ALIASES: dict[str, str] = _load_location_aliases()


def _canonical_location(location: str) -> Optional[str]:
    """Return a canonical location key, or None if no alias matches."""
    lower = location.lower().strip()
    if lower in LOCATION_ALIASES:
        return LOCATION_ALIASES[lower]
    for alias, key in LOCATION_ALIASES.items():
        if alias in lower:
            return key
    return None


# ── Deduplication ─────────────────────────────────────────────────────

_STOPWORDS = frozenset({"the", "at", "in", "and", "of", "by", "a", "an", "y"})


def normalize_name(name: str) -> str:
    """Lowercase, drop dates / edition numbers, drop punctuation, squeeze spaces.

    Date and number stripping runs *before* punctuation stripping: "9/12",
    "Vol. 3" and "#4" only exist while the slash, dot and hash are still there.
    The old order removed the punctuation first, so "Salsa Social 9/12" became
    "salsa social 912" and three of these patterns could never match.
    """
    name = name.lower()
    name = re.sub(r"\b\d{1,2}[/\-]\d{1,2}([/\-]\d{2,4})?\b", " ", name)
    name = re.sub(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}\w*\b", " ", name, flags=re.I)
    name = re.sub(r"\bvol\s*\.?\s*\d+\b", " ", name)
    name = re.sub(r"#\d+", " ", name)
    name = re.sub(r"\b\d{1,2}(st|nd|rd|th)\b", " ", name)
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _content_words(name: str) -> set[str]:
    return set(name.split()) - _STOPWORDS


# Words that appear in a large share of event names on a Latin-dance site and
# therefore carry almost no identifying signal. Two events sharing only
# {"salsa", "bachata"} are not evidence of the same event — on this site that
# describes most of the calendar. Overlap on these alone must not drive a merge.
_GENERIC_DANCE_WORDS = frozenset({
    "salsa", "bachata", "kizomba", "zouk", "merengue", "cumbia", "chacha",
    "timba", "rueda", "reggaeton", "dembow", "mambo",
    "latin", "latino", "latina", "afrolatin",
    "dance", "dancing", "dancers", "social", "socials", "party", "parties",
    "night", "nights", "music", "live", "dj", "event", "events",
})

# Tokens shorter than this identify nothing on their own ("w" from "w/ Tina",
# "co" from "Dance Co"), so they cannot serve as the distinguishing word.
_DISTINCTIVE_MIN_LEN = 3


def _distinctive_words(words: set[str]) -> set[str]:
    """Words specific enough to identify a particular event."""
    return {w for w in words
            if len(w) >= _DISTINCTIVE_MIN_LEN and w not in _GENERIC_DANCE_WORDS}


# Minimum token length eligible for fuzzy (1-edit) matching. One- and two-
# letter tokens ("dj", "w", "co") are within an edit of almost anything, so
# they must match exactly. Three letters is the floor because real spelling
# variants live there: "Kiz Thursday" and "Kizz Thursday" are the same night.
_FUZZY_MIN_LEN = 3


def _edit_distance_le1(a: str, b: str) -> bool:
    """True if a and b are within one insert/delete/substitute of each other."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = j = edits = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if la == lb:        # substitution
            i += 1
            j += 1
        elif la > lb:       # deletion from a
            i += 1
        else:               # insertion into a
            j += 1
    if i < la or j < lb:    # trailing leftover char
        edits += 1
    return edits <= 1


def _shared_word_count(words_a: set[str], words_b: set[str]) -> int:
    """Count shared words, allowing one-character typos on longer tokens.

    e.g. {"kizz","thursday"} vs {"kiz","thursday"} -> 2, so spelling variants
    of the same event name ("Kizz" vs "Kiz") still register as a match.
    """
    remaining = set(words_b)
    unmatched: list[str] = []
    shared = 0
    for w in words_a:
        if w in remaining:
            remaining.discard(w)
            shared += 1
        else:
            unmatched.append(w)
    for w in unmatched:
        if len(w) < _FUZZY_MIN_LEN:
            continue
        match = next(
            (x for x in remaining
             if len(x) >= _FUZZY_MIN_LEN and _edit_distance_le1(w, x)),
            None,
        )
        if match is not None:
            remaining.discard(match)
            shared += 1
    return shared


def _as_aware(dt: datetime) -> datetime:
    """Naive timestamps in this store mean Boston wall-clock time."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=NY_TZ)


def _parse_aware(iso_str: str) -> Optional[datetime]:
    dt = parse_date(iso_str or "")
    return None if dt is None else _as_aware(dt)


def _eastern_iso(dt: datetime) -> str:
    """One canonical spelling for an instant: Eastern time with its offset."""
    return _as_aware(dt).astimezone(NY_TZ).isoformat()


def _occurrence_instants(event: dict) -> list[datetime]:
    """Every dated occurrence of an event (startDate + recurrences[]), parsed
    to aware datetimes, de-duplicated by *instant* and sorted.

    The stored strings mix +00:00, -04:00 and -05:00 offsets, so the same
    moment can be spelled two ways. Sorting the strings interleaves them and
    keeps both; sorting instants does not.
    """
    seen: dict[float, datetime] = {}
    for raw in [event.get("startDate", "")] + list(event.get("recurrences") or []):
        dt = _parse_aware(raw)
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
    instants = _occurrence_instants(event)
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


def _coords_close(a: dict, b: dict, threshold_km: float = 0.3) -> bool:
    lat_a, lng_a = a.get("lat"), a.get("lng")
    lat_b, lng_b = b.get("lat"), b.get("lng")
    if lat_a is None or lng_a is None or lat_b is None or lng_b is None:
        return False
    dlat = lat_a - lat_b
    dlng = (lng_a - lng_b) * math.cos(math.radians(lat_a))
    dist = math.sqrt(dlat * dlat + dlng * dlng) * 111
    return dist <= threshold_km


def _locations_same(a: dict, b: dict) -> bool:
    """Check if two events are at the same venue (aliases, coords, or string)."""
    canon_a = _canonical_location(a.get("location", ""))
    canon_b = _canonical_location(b.get("location", ""))
    if canon_a and canon_b:
        return canon_a == canon_b
    if _coords_close(a, b):
        return True
    loc_a = (a.get("location") or "").lower().strip()
    loc_b = (b.get("location") or "").lower().strip()
    if loc_a and loc_b:
        return loc_a == loc_b
    return False


def _same_calendar_day(a: dict, b: dict) -> Optional[bool]:
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


def _dates_within(a: dict, b: dict, hours: float) -> Optional[bool]:
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


def _url_match(a: dict, b: dict) -> bool:
    """Check if both events share any URL across url + urls[] fields."""
    def _all_urls(ev: dict) -> set[str]:
        result: set[str] = set()
        u = (ev.get("url") or "").rstrip("/").lower()
        if u:
            result.add(u)
        for extra in ev.get("urls") or []:
            norm = extra.rstrip("/").lower()
            if norm:
                result.add(norm)
        return result

    urls_a = _all_urls(a)
    urls_b = _all_urls(b)
    return bool(urls_a & urls_b)


def _load_known_duplicates() -> list[dict]:
    """Always read the file. It is a few KB, and a process-level cache is how
    a long-lived MCP server wrote a stale list back over verdicts the cron
    pipeline had recorded in the meantime. A corrupt file raises rather than
    reading as "no verdicts"."""
    return atomic_io.read_json(KNOWN_DUPLICATES_JSON, default=[])


def _known_duplicate_verdict(a: dict, b: dict) -> Optional[str]:
    """Return 'certain' if confirmed same, 'skip' if confirmed different, else None."""
    id_a, id_b = a.get("id"), b.get("id")
    if not id_a or not id_b:
        return None
    for entry in _load_known_duplicates():
        if {entry["id_a"], entry["id_b"]} == {id_a, id_b}:
            if entry["verdict"] == "same":
                return "certain"
            if entry["verdict"] == "different":
                return "skip"
    return None


@_locked
def _persist_known_duplicate(id_a: str, id_b: str, verdict: str) -> None:
    """Save a human-reviewed duplicate pair to known_duplicates.json.

    Read + modify + write under the store lock, so two reviewers (or the
    server and the pipeline) cannot each append to their own copy and have
    the second save erase the first.
    """
    pair = sorted([id_a, id_b])
    id_a, id_b = pair[0], pair[1]

    entries = _load_known_duplicates()
    for entry in entries:
        if entry["id_a"] == id_a and entry["id_b"] == id_b:
            entry["verdict"] = verdict
            entry["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            break
    else:
        entries.append({
            "id_a": id_a,
            "id_b": id_b,
            "verdict": verdict,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        })

    atomic_io.write_json(KNOWN_DUPLICATES_JSON, entries)


def list_known_duplicates() -> list[dict]:
    """Return all human-reviewed duplicate verdicts (a copy, newest first)."""
    entries = list(_load_known_duplicates())
    entries.sort(key=lambda e: e.get("reviewed_at", ""), reverse=True)
    return entries


@_locked
def forget_known_duplicate(id_a: str, id_b: str) -> dict:
    """Delete a stored duplicate verdict so the pair is re-evaluated from scratch.

    Undoes a wrong ``verdict:"same"`` (which otherwise auto-merges the pair
    forever) or a wrong ``verdict:"different"`` (which suppresses the pair from
    review forever). Removing the record does not un-merge already-merged events.
    """
    pair = set([id_a, id_b])
    entries = _load_known_duplicates()
    kept = [e for e in entries if {e.get("id_a"), e.get("id_b")} != pair]
    if len(kept) == len(entries):
        return {"status": "not_found",
                "message": f"No stored verdict for pair {sorted(pair)}"}
    atomic_io.write_json(KNOWN_DUPLICATES_JSON, kept)
    return {"status": "forgotten", "pair": sorted(pair),
            "remaining": len(kept)}


def _weekday_of(iso_str: str) -> Optional[str]:
    """Boston weekday name for an ISO timestamp, or None if unparseable."""
    dt = _parse_aware(iso_str)
    if dt is None:
        return None
    return DAYS_LIST[dt.astimezone(NY_TZ).isoweekday() % 7]


def _event_day_of_week(event: dict) -> Optional[str]:
    """Return dayOfWeek from the field or infer it from startDate."""
    return event.get("dayOfWeek") or _weekday_of(event.get("startDate", ""))


# Two recurring nights at one venue on one weekday still are not the same
# series when their titles claim different dances or their doors open hours
# apart. This is the gap a "certain" verdict must not walk through.
_SERIES_MAX_START_GAP_MIN = 120


def _name_styles(name: str) -> set[str]:
    """Dance styles named in a title, via the scraper's own keyword list."""
    return {s for s in detect_styles(name or "") if s != "other"}


def _wall_clock_minutes(event: dict) -> Optional[int]:
    dt = _parse_aware(event.get("startDate", ""))
    if dt is None:
        return None
    local = dt.astimezone(NY_TZ)
    return local.hour * 60 + local.minute


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
    styles_a, styles_b = _name_styles(a.get("name", "")), _name_styles(b.get("name", ""))
    if styles_a and styles_b and not (styles_a <= styles_b or styles_b <= styles_a):
        return True
    mins_a, mins_b = _wall_clock_minutes(a), _wall_clock_minutes(b)
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

    if _is_venue_schedule_record(a) != _is_venue_schedule_record(b):
        return None

    known = _known_duplicate_verdict(a, b)
    if known == "certain":
        return "certain"
    if known == "skip":
        return None

    if id_a == id_b:
        return "certain"

    if _url_match(a, b):
        # A shared URL is conclusive only when the dates agree (or can't be
        # compared). Series whose occurrences all share one organizer URL
        # (e.g. Fiesta's /upcoming-socials page) must not merge distinct
        # dates into one record — each occurrence stays its own event and
        # collapse_recurring_series groups them at publish.
        if _dates_within(a, b, 24) is not False:
            return "certain"

    name_a_raw = a.get("name")
    name_b_raw = b.get("name")
    if not name_a_raw or not name_b_raw:
        return None

    name_a = normalize_name(name_a_raw)
    name_b = normalize_name(name_b_raw)
    if not name_a or not name_b:
        return None

    within_24h = _dates_within(a, b, 24)
    same_day = _same_calendar_day(a, b)
    same_loc = _locations_same(a, b)
    names_exact = (name_a == name_b)
    names_substring = (name_a in name_b or name_b in name_a) and not names_exact

    words_a = _content_words(name_a)
    words_b = _content_words(name_b)
    word_overlap_strong = False
    if words_a and words_b:
        shared = _shared_word_count(words_a, words_b)
        smaller = min(len(words_a), len(words_b))
        # The overlap must include at least one word that actually identifies
        # this event. Without this, "Salsa and bachata rooftop party" (Allston,
        # 2 PM) matched "Black Mamba's Salsa and Bachata Social" (Natick, 7 PM)
        # on {salsa, bachata} alone — two words shared by most of the calendar.
        shared_distinctive = _shared_word_count(
            _distinctive_words(words_a), _distinctive_words(words_b))
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
            dow_a = _event_day_of_week(a)
            dow_b = _event_day_of_week(b)
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
    within_7d = _dates_within(a, b, 168)
    if same_loc and (names_exact or names_substring or word_overlap_strong) and within_7d is True:
        if (
            (a.get("recurring") or a.get("recurrences"))
            and (b.get("recurring") or b.get("recurrences"))
            and _named_weekdays_conflict(a, b)
        ):
            return None
        return "review"

    return None


def _dedup_reason(a: dict, b: dict, confidence: str) -> str:
    """Build a human-readable reason string for the audit log."""
    parts = []
    name_a = normalize_name(a.get("name", ""))
    name_b = normalize_name(b.get("name", ""))

    if a.get("id") == b.get("id"):
        parts.append("same_id")
    elif _url_match(a, b):
        parts.append("same_url")
    elif name_a == name_b:
        parts.append("exact_name")
    elif name_a in name_b or name_b in name_a:
        parts.append("substring_name")
    elif name_a and name_b:
        words_a = _content_words(name_a)
        words_b = _content_words(name_b)
        overlap = words_a & words_b
        parts.append(f"word_overlap({len(overlap)}/{min(len(words_a), len(words_b))})")

    within = _dates_within(a, b, 24)
    if within is True:
        parts.append("within_24h")
    elif within is None:
        parts.append("no_dates")

    if _locations_same(a, b):
        parts.append("same_location")

    if _same_calendar_day(a, b) is True:
        parts.append("same_day")

    return "+".join(parts)


def _log_dedup(action: str, kept: dict, candidate: dict, confidence: str, reason: str) -> None:
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
    atomic_io.append_line(DEDUP_LOG, json.dumps(entry))


def _url_host(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url.lower())
    return m.group(1) if m else ""


# Facebook share wrappers (/events/s/<slug>/<share-id>/, /share/<id>) carry a
# share-story id rather than an event id and do not resolve for logged-out
# visitors, so they are the worst possible primary link.
_SHARE_WRAPPER_RE = re.compile(r"facebook\.com/(?:events/s/|share/)|fb\.me/", re.I)


def url_rank(url: str) -> int:
    """Rank a URL's fitness as the primary (clicked) link. Lower wins.

    Deliberately separate from SOURCE_PRIORITY: that ranks how much we trust a
    source's claim that an event exists, which is unrelated to how good that
    source's links are. beatrice-calendar outranks lister-events on coverage
    but ships Facebook share wrappers, so letting one number decide both
    replaced an organizer's canonical page with a link that 404s for visitors.
    """
    if not url:
        return 100
    lower = url.lower()
    host = _url_host(url)
    if _SHARE_WRAPPER_RE.search(lower):
        return 40
    if "facebook.com" in host or "fb.com" in host:
        return 20 if "/events/" in lower else 30
    if "instagram.com" in host:
        return 30
    return 10


def _event_url_list(ev: dict) -> list[str]:
    return [u for u in [ev.get("url"), *(ev.get("urls") or [])] if u]


def _url_key(url: str) -> str:
    """Comparison form for a URL, matching _collect_urls' dedup rule."""
    return (url or "").rstrip("/").lower()


def _dropped_url_list(ev: dict) -> list[str]:
    """URLs a reviewer removed by hand, which re-scrapes must not resurrect."""
    return [u for u in (ev.get("_dropped_urls") or []) if u]


def _collect_urls(*events: dict) -> list[str]:
    """Gather unique URLs from the given events, keeping one per domain."""
    seen_hosts: set[str] = set()
    seen_urls: set[str] = set()
    result: list[str] = []
    for ev in events:
        for u in _event_url_list(ev):
            normalized = u.rstrip("/").lower()
            if normalized in seen_urls:
                continue
            host = _url_host(u)
            if host and host in seen_hosts:
                continue
            seen_urls.add(normalized)
            if host:
                seen_hosts.add(host)
            result.append(u)
    return result


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
    if _is_venue_schedule_record(winner):
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
    all_urls = _collect_urls(winner, loser)
    dropped = _dropped_url_list(winner) + _dropped_url_list(loser)
    if dropped:
        merged["_dropped_urls"] = sorted({_url_key(u): u for u in dropped}.values())
        suppressed = {_url_key(u) for u in dropped}
        # A URL the reviewer struck off stays off, however many re-scrapes
        # hand it back. Only drop it while something still links the event —
        # a record with no link at all is worse than a stale one.
        remaining = [u for u in all_urls if _url_key(u) not in suppressed]
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
                _parse_aware(event.get("startDate", "")) or floor,
            )

        freshest = max((a, b), key=_series_freshness)
        if freshest is not winner:
            for key in ("startDate", "endDate", "dayOfWeek", "recurrences"):
                if freshest.get(key):
                    merged[key] = freshest[key]

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
                reason = _dedup_reason(result[idx], ev, conf)
                if record_log:
                    _log_dedup(conf, result[idx], ev, conf, reason)
                result[idx] = merge_event(result[idx], ev)
            else:
                result.append(ev)
        else:
            result.append(ev)
    return result


# ── Recurring series collapse ─────────────────────────────────────────

def _location_key(location: str) -> str:
    loc = location.lower()
    m = re.search(r"\d+\s+[\w\s]+(?:st|ave|blvd|rd|dr|ln|way|ct|pl|pkwy|drive|street|avenue)\b", loc, re.I)
    if m:
        addr = m.group(0).strip()
        addr = re.sub(r"[^\w\s]", "", addr)
        addr = re.sub(r"\s+", " ", addr).strip()
        for full, abbr in [("street", "st"), ("avenue", "ave"), ("boulevard", "blvd"),
                           ("drive", "dr"), ("road", "rd"), ("lane", "ln"),
                           ("parkway", "pkwy"), ("place", "pl"), ("court", "ct")]:
            addr = re.sub(rf"\b{full}\b", abbr, addr)
        return addr
    lines = [l.strip() for l in loc.split("\n") if l.strip()]
    first = lines[0] if lines else loc
    first = re.sub(r"[^\w\s]", "", first)
    return re.sub(r"\s+", " ", first).strip()


# Signals that a name is a distinctly-branded special edition rather than a
# regular occurrence of a series: anniversaries, festivals, guest artists
# ("ft"/"featuring"), guest-promoter takeovers, holiday/themed nights, lineups
# ("vs"). Such an event keeps its own map pin instead of being folded into the
# generic series name (or a venue hub). Run against normalize_name() output
# (lowercased, punctuation stripped).
_SPECIAL_EDITION_RE = re.compile(
    r"\b(?:anniversary|anniversaries|\d+\s*year|festival|festiva|edition|"
    r"ft|feat|featuring|takeover|special|halloween|nye|new year|christmas|"
    r"valentine|vs)\b",
    re.I,
)


def _is_special_edition(name: str) -> bool:
    return bool(_SPECIAL_EDITION_RE.search(name or ""))


# Big-event detector for the published `special` flag: festivals, annual
# editions, congresses, weekenders, galas, cruises, benefits — the marquee
# one-offs a visitor plans around, as opposed to a weekly bar social.
# Narrower than _SPECIAL_EDITION_RE on purpose: guest-DJ/"ft."/holiday-theme
# nights are special *editions* of a series but not big events. Name check
# runs against normalize_name() output; description check uses raw text.
_BIG_EVENT_RE = re.compile(
    r"\b(?:festival|congress|weekender|annual|anniversary|anniversaries|"
    r"gala|cruise|block party|benefit|fundraiser|solidarity|encuentro)\b",
    re.I,
)

# Plain-named community marquees ("Baila por Venezuela") often bury the
# signal in the description. Keep this tighter than the name regex — only
# clear benefit / multi-org solidarity language, not every artist lineup.
_BIG_EVENT_DESC_RE = re.compile(
    r"\b(?:benefit\s+(?:concert|show|dance|night|party|event)|"
    r"fundraiser|all proceeds|earthquake relief|in solidarity|"
    r"stand in solidarity)\b",
    re.I,
)

# Satellite parties of a big event ("Pre-Party: Boston Salsa Festival") are
# regular socials that merely carry the festival's name — never auto-flag
# them. An explicit special:true still wins in _derive_special.
_SATELLITE_PARTY_RE = re.compile(r"\b(?:pre|after)\s*party\b", re.I)


def _derive_special(ev: dict) -> None:
    """Resolve the published `special` flag (big one-off events).

    An explicit `special: true/false` already on the stored event always wins,
    so judgment calls the regex can't make ("Salsa at the Shell") are set at
    review time with edit_event and survive here. An explicit false ships as
    an absent field, not `special: false`. Otherwise the heuristic flags
    non-recurring one-offs whose name or description reads like a big event.
    """
    explicit = ev.get("special")
    if explicit is not None:
        if not explicit:
            ev.pop("special", None)
        return
    if ev.get("recurring") or ev.get("schedule") or ev.get("searchOnly"):
        return
    name = normalize_name(ev.get("name", ""))
    if _SATELLITE_PARTY_RE.search(name):
        return
    if _BIG_EVENT_RE.search(name) or _BIG_EVENT_DESC_RE.search(ev.get("description") or ""):
        ev["special"] = True


# Advisory class/workshop detector. Not a hard filter — an event with a Latin
# style tag passes the automated relevance gate even when it is really a class,
# so this only *flags* pending rows to draw the reviewing agent's attention.
_CLASS_HINT_RE = re.compile(
    r"\b(class(?:es)?|workshop|boot\s*camp|technique|lesson[s]?|drill[s]?|"
    r"intensive|course|seminar|fundamentals|footwork|styling)\b",
    re.I,
)
_SOCIAL_HINT_RE = re.compile(
    r"\b(social|party|parties|night[s]?|fiesta|milonga|practica|pr[aá]ctica|"
    r"dj|live\s+music|live\s+band|open\s+dancing)\b",
    re.I,
)


def _looks_like_class(event: dict) -> bool:
    """Heuristic: reads like a class/workshop with no social component.

    Advisory only. Returns True when class-y words appear and no social/party
    signal offsets them (an event that runs "lesson at 8, social at 9" has both
    and is not flagged).
    """
    text = f"{event.get('name', '')} {event.get('description', '')}"
    if not _CLASS_HINT_RE.search(text):
        return False
    return not _SOCIAL_HINT_RE.search(text)


def _special_edition_mismatch(a: dict, b: dict) -> bool:
    """True when exactly one of two events is a special edition.

    Merging across this line folds an anniversary/festival/takeover/guest night
    into its recurring series (or vice-versa), which must never happen.
    """
    return _is_special_edition(normalize_name(a.get("name", ""))) != \
        _is_special_edition(normalize_name(b.get("name", "")))


# Collapsing is held to a higher bar than dedup's 0.5: folding two
# occurrences into one pin is invisible to visitors, so the names must share
# the larger part of their words.
_SERIES_OVERLAP_RATIO = 0.6


def _names_are_same_series(a: str, b: str) -> bool:
    if a == b:
        return True
    if a in b or b in a:
        return True
    words_a = _content_words(a)
    words_b = _content_words(b)
    if not words_a or not words_b:
        return False
    shared = _shared_word_count(words_a, words_b)
    smaller = min(len(words_a), len(words_b))
    return shared >= max(2, smaller * _SERIES_OVERLAP_RATIO)


def _venue_schedule_covers_event(venue_event: dict, ev: dict, day: str) -> bool:
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
            _schedule_date_allowed(dt, entry)
            and _matches_schedule_note(
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


def _event_local_window(ev: dict) -> tuple[Optional[datetime], Optional[datetime], bool]:
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
    parsed = _parse_time_range(entry.get("time") or "")
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
    ev_start, ev_end, end_assumed = _event_local_window(ev)
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
    if _is_special_edition(ev_name):
        return False
    if _names_are_same_series(ev_name, hub_name):
        return True
    distinctive = set(hub_name.split()) - _GENERIC_VENUE_WORDS
    return bool(distinctive) and bool(distinctive & set(ev_name.split()))


def _scraped_at_venue_hub(hub: dict, scraped: dict) -> bool:
    """True when scraped event is at the same venue as a schedule hub.

    Uses _locations_same() but rejects coords-only matches unless the scraped
    event also names the venue or shares its street address (avoids nearby venues).
    """
    if not _locations_same(hub, scraped):
        return False

    hub_loc = (hub.get("location") or "").lower().strip()
    scraped_loc = (scraped.get("location") or "").lower().strip()
    if _canonical_location(hub.get("location", "")) and _canonical_location(scraped.get("location", "")):
        return True
    if hub_loc and scraped_loc and hub_loc == scraped_loc:
        return True

    hub_name = (hub.get("name") or "").lower()
    scraped_name = (scraped.get("name") or "").lower()
    if hub_name and (hub_name in scraped_name or hub_name in scraped_loc):
        return True

    hub_key = _location_key(hub_loc)
    scraped_key = _location_key(scraped_loc)
    if hub_key and scraped_key and (hub_key == scraped_key or hub_key in scraped_key or scraped_key in hub_key):
        return True

    return False


def _venue_match_reason(hub: dict, ev: dict) -> str:
    """Short label for *why* an event was considered to be at this hub."""
    hub_loc = (hub.get("location") or "").lower().strip()
    ev_loc = (ev.get("location") or "").lower().strip()
    if _canonical_location(hub.get("location", "")) and _canonical_location(ev.get("location", "")):
        return "canonical venue alias"
    if hub_loc and ev_loc and hub_loc == ev_loc:
        return "identical location string"
    hub_name = (hub.get("name") or "").lower()
    if hub_name and (hub_name in (ev.get("name") or "").lower() or hub_name in ev_loc):
        return f"venue name {hub.get('name')!r} appears in the event"
    return f"street address ({_location_key(ev_loc) or ev_loc or 'unknown'})"


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
        if not line or not _TIME_RE.search(line):
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


def _suppress_venue_covered_events(
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
    regular_hubs = [v for v in venue_events if _is_venue_schedule_record(v) and not v.get("nextDateApproximate")]
    irregular_hubs = [v for v in venue_events if _is_venue_schedule_record(v) and v.get("nextDateApproximate")]

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
        if _is_venue_schedule_record(ev):
            kept.append(ev)
            continue

        day = _event_day_of_week(ev)
        if not day:
            kept.append(ev)
            continue

        if not ev.get("location"):
            _infer_location(ev)
        if ev.get("lat") is None and ev.get("location"):
            coords = geocode(ev["location"])
            if coords:
                ev["lat"], ev["lng"] = coords

        # Check regular hubs — same place, same weekday, hub's schedule admits
        # the date. That is a *collision*, not yet a verdict.
        colliding_hub = None
        for hub in regular_hubs:
            if not _venue_schedule_covers_event(hub, ev, day):
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


def _collapse_urls(group_events: list[dict], next_start: str) -> tuple[Optional[str], list[str]]:
    """Pick the best primary URL for a collapsed series.

    Source rank decides which *record* wins. Link quality is separate: among
    equal-quality URLs, prefer the one that belongs to the next occurrence so
    a closed past listing on the same host does not outrank the open one.
    """
    next_dt = _parse_aware(next_start)
    all_urls: list[str] = []
    next_keys: set[str] = set()
    seen: set[str] = set()
    for ev in group_events:
        ev_start = _parse_aware(ev.get("startDate", ""))
        from_next = bool(next_dt and ev_start and ev_start == next_dt)
        for u in _event_url_list(ev):
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
        u for u in _collect_urls({"url": primary}, *group_events)
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
        loc_i = _location_key(ev_i.get("location", ""))
        dow_i = _event_day_of_week(ev_i)

        for j, ev_j in enumerate(events):
            if j in assigned:
                continue
            name_j = normalize_name(ev_j["name"])
            loc_j = _location_key(ev_j.get("location", ""))
            dow_j = _event_day_of_week(ev_j)

            # Venue schedule hubs (e.g. Havana Club) share a location/name with
            # scraped night-specific series but are distinct map entries.
            if _is_venue_schedule_record(ev_i) != _is_venue_schedule_record(ev_j):
                continue

            if dow_i != dow_j:
                continue

            if not _names_are_same_series(name_i, name_j):
                continue
            # A distinctly-branded special edition (anniversary, festival, guest
            # artist, themed night) sharing a series' venue + weeknight should
            # stay its own pin rather than vanish into the generic series name.
            if name_i != name_j and _is_special_edition(name_i) != _is_special_edition(name_j):
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
            for occ in _occurrence_instants(ev):
                instants.setdefault(occ.timestamp(), occ)
        if not instants:
            result.extend(group_events)
            continue
        date_dts = [instants[k] for k in sorted(instants)]
        dates: list[str] = [_eastern_iso(d) for d in date_dts]

        # Preserve the event's duration so endDate never desyncs from startDate
        # when we roll forward to an occurrence no single member's startDate
        # matches (the new startDate often comes from a member's recurrences[]).
        orig_start = parse_date(best.get("startDate", ""))
        orig_end = parse_date(best.get("endDate", ""))
        duration = orig_end - orig_start if (orig_start and orig_end and orig_end >= orig_start) else None

        def _roll_end(new_start_iso: str) -> Optional[str]:
            new_start = _parse_aware(new_start_iso)
            if new_start is None or duration is None:
                return None
            return _eastern_iso(new_start + duration)

        now = datetime.now(NY_TZ)
        future = [d for d in date_dts if d >= now]
        new_start = _eastern_iso(future[0] if future else date_dts[-1])
        best["startDate"] = new_start
        rolled_end = _roll_end(new_start)
        if rolled_end:
            best["endDate"] = rolled_end

        best["recurring"] = True
        best["recurrences"] = dates
        best["dayOfWeek"] = _weekday_of(new_start) or best.get("dayOfWeek")

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


# ── Venue expansion ───────────────────────────────────────────────────

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)
_TIME_24H_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _parse_time(time_str: str) -> Optional[tuple[int, int]]:
    """"9:00 PM" -> (21, 0); a bare 24-hour "21:00" is accepted too."""
    m = _TIME_RE.search(time_str)
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


def _parse_time_range(time_str: str) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    parts = re.split(r"\s*[–—-]\s*", time_str)
    if len(parts) != 2:
        return None
    start = _parse_time(parts[0])
    end = _parse_time(parts[1])
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


def _parse_anchor(anchor: Optional[str]) -> Optional[datetime]:
    """A schedule entry's ``anchor`` ("YYYY-MM-DD") as a naive datetime."""
    if not anchor:
        return None
    try:
        return datetime.strptime(anchor, _ANCHOR_FORMAT)
    except (TypeError, ValueError):
        return None


def _schedule_date_allowed(date: datetime, entry: dict) -> bool:
    """Whether a schedule entry's optional seasonal bounds include ``date``."""
    starts = _parse_anchor(entry.get("starts"))
    until = _parse_anchor(entry.get("until"))
    day = date.replace(hour=0, minute=0, second=0, microsecond=0)
    return (starts is None or day >= starts) and (until is None or day <= until)


def _matches_schedule_note(date: datetime, note: str, weekday_name: str,
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
        ref = _parse_anchor(anchor) or _EVERY_OTHER_DEFAULT_ANCHOR
        week_num = (date - ref).days // 7
        return week_num % 2 == 0
    return True


def expand_venues(weeks_ahead: int = 8) -> list[dict]:
    """Read data/venues.json and generate concrete DanceEvent dicts."""
    venues = atomic_io.read_json(VENUES_JSON, default=[])
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
            time_range = _parse_time_range(sched.get("time", ""))
            note = sched.get("note", "")
            anchor = sched.get("anchor")
            d = today
            while d < end_window:
                if (
                    d.isoweekday() % 7 == target_wday
                    and d.strftime("%Y-%m-%d") not in exclude_dates
                    and _schedule_date_allowed(d, sched)
                ):
                    if _matches_schedule_note(d, note, day_name, anchor):
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


# ── File I/O with locking ─────────────────────────────────────────────

def _read_json(path: Path) -> list[dict]:
    """Strict read: a missing store file is empty, a corrupt one raises.

    Returning [] on a parse error is how a truncated active.json used to read
    as "no events" and get written straight back over the real data.
    """
    return atomic_io.read_json(path, default=[])


def _write_json(path: Path, data) -> None:
    """Atomic write (unique temp file + fsync + rename)."""
    atomic_io.write_json(path, data)


def _append_changelog(action: str, event_id: str, details: str = "") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "event_id": event_id,
        "details": details,
    }
    atomic_io.append_line(CHANGELOG, json.dumps(entry))


# ── Public API ────────────────────────────────────────────────────────

def load_active() -> list[dict]:
    return _read_json(ACTIVE_JSON)


def load_archive() -> list[dict]:
    return _read_json(ARCHIVE_JSON)


def load_pending() -> list[dict]:
    return _read_json(PENDING_JSON)


def load_rejected() -> list[dict]:
    return _read_json(REJECTED_JSON)


def load_blocked() -> list[dict]:
    return _read_json(BLOCKED_JSON)


def _block_key(event: dict) -> Optional[str]:
    """Stable identity for blocking: normalized name + venue, no date.

    Blocking by raw id only works for sources with stable ids. Weekly listings
    from Wix/Eventbrite mint a new id per occurrence, so the id changes every
    week while the name and venue stay put — this key is what survives.
    """
    name = normalize_name(event.get("name") or "")
    if not name:
        return None
    raw_loc = (event.get("location") or "").strip()
    loc = _canonical_location(raw_loc) or raw_loc
    # The same address reaches us punctuated differently depending on the path
    # it took ("101 Union St\n101 Union Street, Newton" from the scraper vs
    # "101 Union St, 101 Union Street, Newton" once stored), so strip all
    # punctuation and whitespace runs before comparing — otherwise the block
    # silently fails to match and the event returns as if never blocked.
    loc = re.sub(r"[^\w\s]", " ", loc.lower())
    loc = re.sub(r"\s+", " ", loc).strip()
    return f"{name}|{loc}"


def _blocked_keys(blocked: list[dict]) -> set:
    """Name+venue keys for the blocklist, tolerating pre-existing records."""
    keys = set()
    for b in blocked:
        key = b.get("block_key") or _block_key(b)
        if key:
            keys.add(key)
    return keys


def save_active(events: list[dict]) -> None:
    _write_json(ACTIVE_JSON, events)


def save_archive(events: list[dict]) -> None:
    _write_json(ARCHIVE_JSON, events)


def save_pending(events: list[dict]) -> None:
    _write_json(PENDING_JSON, events)


def save_rejected(events: list[dict]) -> None:
    _write_json(REJECTED_JSON, events)


def save_blocked(events: list[dict]) -> None:
    _write_json(BLOCKED_JSON, events)


@_locked
def _queue_rejected(event: dict, reason: str, review_type: str = "non_latin") -> dict:
    """Append or update an event in the rejected review queue."""
    rejected = load_rejected()
    now = datetime.now(timezone.utc).isoformat()
    record = dict(event)
    record["_rejected_reason"] = reason
    record["_review_type"] = review_type

    for i, existing in enumerate(rejected):
        if existing.get("id") == event.get("id"):
            record["_rejected_at"] = existing.get("_rejected_at", now)
            rejected[i] = record
            save_rejected(rejected)
            return record

    record["_rejected_at"] = now
    rejected.append(record)
    save_rejected(rejected)
    return record


def _slug_base(name: str) -> str:
    base = unicode_normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60]


def slugify(name: str, event_id: str) -> str:
    return f"{_slug_base(name)}-{event_id[:8].lower()}"


def _resolve_slug_collisions(events: list[dict]) -> list[tuple[str, str]]:
    """Give every published event a /event/ URL of its own.

    slugify() suffixes the name with the first 8 characters of the id, and
    whole families of ids share those 8 characters ("fiesta-2026...",
    "bobas-2026..."). Colliding events all answered on one URL, and the site's
    findBySlug() returns whichever comes first in the published list, so the
    shipped URL for one Fiesta night rendered a different night at a different
    venue and the rest were unreachable.

    The slug the registry already bound to an id stays with that id — that URL
    is public — and every other member of the collision falls back to a hash of
    its id, which is stable across runs. Returns the (id, slug) pairs moved.
    """
    by_slug: dict[str, list[dict]] = {}
    for ev in events:
        if ev.get("slug"):
            by_slug.setdefault(ev["slug"], []).append(ev)
    collisions = {slug: evs for slug, evs in by_slug.items() if len(evs) > 1}
    if not collisions:
        return []

    # A missing registry just means no slug has shipped yet. A corrupt one
    # raises: rebuilding "who owns this URL" from nothing would silently
    # reassign public URLs.
    from slug_registry import REGISTRY_PATH
    registry = atomic_io.read_json(REGISTRY_PATH, default={"entries": {}})
    entries = registry.get("entries") or {}
    shipped = {slug: e["id"] for slug, e in entries.items() if e.get("id")}

    moved: list[tuple[str, str]] = []
    for slug, group in collisions.items():
        ids = sorted(ev.get("id") or "" for ev in group)
        keeper = shipped.get(slug)
        if keeper not in ids:
            keeper = ids[0]
        for ev in group:
            if ev.get("id") == keeper:
                continue
            digest = hashlib.md5((ev.get("id") or ev["slug"]).encode()).hexdigest()[:8]
            ev["slug"] = f"{_slug_base(ev.get('name', ''))}-{digest}"
            moved.append((ev.get("id") or "?", ev["slug"]))
    return moved


def validate_event(event: dict) -> list[str]:
    """Return list of validation issues (empty = valid)."""
    issues = []
    if not event.get("name"):
        issues.append("missing name")
    if not event.get("startDate"):
        issues.append("missing startDate")
    if not event.get("location"):
        issues.append("missing location")
    if event.get("lat") is None and event.get("location") and not event.get("venueUnknown"):
        coords = geocode(event["location"])
        if coords:
            event["lat"], event["lng"] = coords
        else:
            issues.append("could not geocode location")
    if event.get("styles") in (None, [], ["other"]):
        combined = f"{event.get('name', '')} {event.get('description', '')}"
        detected = detect_styles(combined)
        if detected != ["other"]:
            event["styles"] = detected
        else:
            issues.append("styles=other (could not auto-detect)")
    return issues


def _infer_location(event: dict) -> None:
    """Fill missing location from description, known venues, or Eventbrite URL."""
    if event.get("venueUnknown"):
        return
    if event.get("location"):
        event["location"] = clean_location(event["location"])
        return

    text = f"{event.get('name', '')}\n{event.get('description', '')}"
    pin = re.search(r"📍\s*(?:Location:?\s*)?([^\n]+)", text)
    if pin:
        event["location"] = clean_location(pin.group(1).strip())
        return

    lower = _normalize(text).lower()
    for venue in sorted(VENUE_COORDS, key=len, reverse=True):
        if venue in lower:
            event["location"] = venue
            return

    url = event.get("url") or ""
    if not url and "eventbrite.com" in text:
        m = re.search(r"https://[^\s]*eventbrite\.com[^\s)\"']+", text)
        if m:
            url = m.group(0).rstrip(".,)")
    if url:
        addr = _eventbrite_address(url)
        if addr:
            event["location"] = addr


def _enrich_event(event: dict) -> None:
    """Geocode, detect styles, extract cost if missing. Mutates in place."""
    _infer_location(event)

    if event.get("lat") is None and event.get("location"):
        coords = geocode(event["location"])
        if coords:
            event["lat"], event["lng"] = coords

    if not event.get("styles") or event.get("styles") == ["other"]:
        combined = f"{event.get('name', '')} {event.get('description', '')}"
        detected = detect_styles(combined)
        event["styles"] = detected

    if event.get("cost") is None:
        combined = f"{event.get('name', '')} {event.get('description', '')}"
        event["cost"] = extract_cost(combined)


def _trusted_latin_sources() -> set:
    """Source ids that are curated Latin-dance calendars.

    Marked with ``"latin_by_default": true`` in sources.json. Every event from
    one is Latin dance by construction, so we never keyword-check them — that
    check exists only to screen general/high-noise calendars. Trusting these
    sources is what stops a real social with an unusual title (e.g. "Thursday
    Night Social @ Havana") from being dropped just because the scraped text
    happens not to contain a style word.

    Errors propagate. This used to return an empty set on any exception, which
    turned a malformed sources.json into "no source is trusted" and silently
    rejected every keyword-less event from every curated calendar.
    """
    return {
        s["id"] for s in load_sources()
        if s.get("latin_by_default") and s.get("id")
    }


def _is_out_of_area(event: dict) -> bool:
    """True if the event's coordinates are clearly outside the Boston metro area.

    Feed sources (beatrice-calendar, eventbrite, etc.) supply explicit lat/lng,
    which bypass the geocoder's own distance rejection. This rule catches the
    whole class of out-of-area events at ingest, so they never need per-event
    blocking. Events without coordinates can't be judged here and pass through.
    """
    lat, lng = event.get("lat"), event.get("lng")
    if lat is None or lng is None:
        return False
    return not _is_near_boston(lat, lng)


def _is_latin_relevant(event: dict, trusted_sources: Optional[set] = None) -> bool:
    """Return True if the event is relevant to Latin dance.

    Events from a curated Latin source (``latin_by_default``) always pass.
    Events with a recognized style (bachata, salsa, etc.) always pass.
    Events tagged only as 'other' must mention a Latin dance term in
    their name or description. Pass ``trusted_sources`` to skip re-reading
    sources.json on every call of a batch ingest.
    """
    if trusted_sources is None:
        trusted_sources = _trusted_latin_sources()
    if event.get("source") in trusted_sources:
        return True
    styles = event.get("styles", [])
    if styles != ["other"]:
        return True
    text = (event.get("name", "") + " " + event.get("description", ""))
    return mentions_latin(text)


def _clear_stale_rejected(event_id: str) -> None:
    """Remove an event from rejected.json if it exists (prevents dual-store state)."""
    rejected = load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is not None:
        rejected.pop(idx)
        save_rejected(rejected)


def _remove_from_active(event_id: str) -> None:
    """Drop an event from active.json if present (no-op otherwise)."""
    active = load_active()
    idx = next((i for i, ev in enumerate(active) if ev["id"] == event_id), None)
    if idx is not None:
        active.pop(idx)
        save_active(active)


@_locked
def add_event(
    event: dict,
    force: bool = False,
    skip_latin_check: bool = False,
    blocked_ids: Optional[set] = None,
    quarantine_new: bool = False,
    blocked_keys: Optional[set] = None,
    distinct_from: Optional[list] = None,
    trusted_sources: Optional[set] = None,
) -> dict:
    """Add an event to the active store. Returns result dict with status.

    Dedup tiers:
      certain -> auto-merge (same ID or URL)
      review  -> route to pending.json for review (unless force=True)

    force=True (admin approval) does TWO things: it bypasses the ingest-time
    exclusion guards (blocklist + out-of-area geo-fence) AND it force-merges a
    review-tier dedup match instead of queueing it — i.e. it asserts "any
    fuzzy match IS the same event". Never use it to add an event that merely
    resembles an existing one (a similarly-named distinct event gets swallowed
    into the existing record). For that, pass distinct_from=[existing_ids]:
    it persists a permanent "different" verdict for each pair up front, so
    the fuzzy match neither queues for review nor force-merges. (The old
    workaround — add without force, reject the pending pair, re-add — still
    works, but fails for events the guards drop before dedup ever runs.)
    Pass blocked_ids / blocked_keys / trusted_sources to avoid re-reading
    blocked.json and sources.json on every call during a batch ingest.

    quarantine_new=True routes brand-new events (no duplicate anywhere) to
    pending.json instead of active, so unattended runs can refresh existing
    events without putting unreviewed ones on the map. Re-scrapes update the
    queued copy in place rather than duplicating it.

    Runs under the store lock. Every store file is loaded at most once per
    call, and any move between two files writes the destination before it
    removes the source, so a crash between the two leaves a duplicate that
    dedup catches next run — never a lost event.
    """
    if _is_venue_schedule_record(event):
        return {"status": "rejected", "message": "Venue schedules belong in venues.json"}

    if not event.get("id") or not event.get("startDate"):
        return {"status": "rejected", "message": "event missing id or startDate"}

    if not force:
        if blocked_ids is None or blocked_keys is None:
            _blocked = load_blocked()
            if blocked_ids is None:
                blocked_ids = {b["id"] for b in _blocked}
            if blocked_keys is None:
                blocked_keys = _blocked_keys(_blocked)
        if event.get("id") in blocked_ids:
            return {"status": "blocked", "message": "event is permanently blocked"}
        # Sources that mint a fresh id per occurrence (nlf-events-<slug>-<date>,
        # Eventbrite eb-<numeric>) would otherwise slip past the id check every
        # week, so a blocked weekly class reappears in the queue forever.
        # Matching on name+venue makes the block actually stick.
        key = _block_key(event)
        if key and key in blocked_keys:
            return {"status": "blocked",
                    "message": "event is permanently blocked (name+venue match)"}

        if _is_out_of_area(event):
            # A previously-added event whose coords now fall out of bounds must
            # not linger on the map: drop any stale active copy before rejecting.
            _remove_from_active(event["id"])
            return {
                "status": "rejected_out_of_area",
                "message": f"event outside Boston metro area: {event.get('location', '')}",
            }

    active = load_active()
    archive = load_archive()

    if not skip_latin_check and not _is_latin_relevant(event, trusted_sources):
        # Not a Latin-dance event. It goes to the rejected queue rather than
        # vanishing: a keyword scan is a good first filter but not a verdict,
        # and a queue row is what lets a reviewer rescue a real social with an
        # odd title (event_approve_rejected) or block a recurring false hit
        # for good. Re-scrapes refresh the queued row in place, so the queue
        # holds one row per event, not one per week. An event a human already
        # approved (it is in active or archive) is never re-queued: the
        # dedup below merges the re-scrape instead.
        already_approved = (
            any(e.get("id") == event["id"] for e in active)
            or any(e.get("id") == event["id"] for e in archive)
        )
        if not already_approved:
            reason = "not Latin dance relevant (styles=['other'], no Latin terms)"
            _queue_rejected(event, reason, "non_latin")
            _append_changelog("reject_non_latin", event["id"], reason)
            return {"status": "rejected_non_latin", "message": reason}

    _infer_location(event)

    if distinct_from:
        # A verdict for an event's own id would suppress its certain-tier
        # self-merge forever, so self-pairs are skipped.
        for other_id in distinct_from:
            if other_id and other_id != event["id"]:
                _persist_known_duplicate(event["id"], other_id, "different")
                _append_changelog("distinct_from", event["id"],
                                  f"pre-marked different from {other_id}")

    active_match = find_duplicate_in(event, active)
    archive_match = find_duplicate_in(event, archive)
    if archive_match is not None and not force:
        archive_idx, conf = archive_match
        if conf == "certain":
            # A source series can be archived under its old UID while a newer
            # source has already added the current occurrence under another
            # UID. Re-activating before checking active would create two pins
            # for the same night on every scrape. Fold the refreshed series
            # into the active copy and retire its archived predecessor.
            if active_match is not None and active_match[1] == "certain":
                active_idx = active_match[0]
                existing = active[active_idx]
                reason = _dedup_reason(existing, event, "certain")
                active[active_idx] = merge_event(existing, event)
                _enrich_event(active[active_idx])
                save_active(active)
                archived = archive.pop(archive_idx)
                save_archive(archive)
                _log_dedup("certain", existing, event, "certain", reason)
                _clear_stale_rejected(event["id"])
                _append_changelog(
                    "merge_archived_series",
                    event["id"],
                    f"folded into active {existing['id']}",
                )
                return {
                    "status": "duplicate",
                    "confidence": "certain",
                    "existing": active[active_idx],
                    "retired_archive": archived["id"],
                }

            # Only pull an event back out of the archive when the incoming
            # copy is actually upcoming. Stale scraped files re-listing past
            # dates must not ping-pong events between archive and active
            # (reactivate here, re-archive in archive_past_events) every run.
            incoming_dt = last_occurrence(event)
            if incoming_dt is None or incoming_dt < datetime.now(timezone.utc) - timedelta(hours=24):
                return {
                    "status": "duplicate",
                    "confidence": conf,
                    "message": "already archived; incoming copy is not upcoming",
                }
            archived = archive[archive_idx]
            reason = _dedup_reason(archived, event, conf)
            merged = merge_event(archived, event)
            _enrich_event(merged)
            merged["reactivatedAt"] = datetime.now(timezone.utc).isoformat()
            # Destination first: land the record in active, then retire the
            # archived copy. archive_past_events() reconciles the same id.
            active.append(merged)
            save_active(active)
            archive.pop(archive_idx)
            save_archive(archive)
            _log_dedup("reactivate", archived, event, conf, reason)
            _clear_stale_rejected(merged["id"])
            _append_changelog("reactivate", merged["id"], "from archive (certain)")
            return {"status": "reactivated", "confidence": conf, "event": merged}

    if active_match is not None:
        active_idx, conf = active_match
        existing = active[active_idx]
        reason = _dedup_reason(existing, event, conf)

        if conf == "certain":
            _log_dedup("certain", existing, event, conf, reason)
            active[active_idx] = merge_event(existing, event)
            _enrich_event(active[active_idx])
            save_active(active)
            _clear_stale_rejected(event["id"])
            return {"status": "duplicate", "confidence": conf, "existing": active[active_idx]}

        if conf == "review":
            if force:
                _log_dedup("force", existing, event, conf, reason)
                active[active_idx] = merge_event(existing, event)
                _enrich_event(active[active_idx])
                save_active(active)
                _append_changelog("merge", event["id"], f"force-merged review dup of {existing['id']}")
                return {"status": "merged", "confidence": conf, "event": active[active_idx]}

            _log_dedup("review", existing, event, conf, reason)
            event["_dedup_candidate_of"] = existing["id"]
            event["_dedup_confidence"] = conf
            event["_dedup_reason"] = reason
            pending = load_pending()
            if any(p.get("id") == event["id"] for p in pending):
                return {
                    "status": "pending_review",
                    "confidence": conf,
                    "reason": reason,
                    "new_event": event,
                    "existing_event": existing,
                    "already_pending": True,
                }
            pending.append(event)
            save_pending(pending)
            _append_changelog("pending_review", event["id"], f"review dup of {existing['id']}: {reason}")
            return {
                "status": "pending_review",
                "confidence": conf,
                "reason": reason,
                "new_event": event,
                "existing_event": existing,
            }

    # A brand-new event that already ended is pure churn (stale scraped file):
    # it would only be archived on the next pass. Skip it outright.
    if not force:
        new_dt = last_occurrence(event)
        if new_dt is not None and new_dt < datetime.now(timezone.utc) - timedelta(hours=24):
            return {"status": "skipped_past", "message": "new event is already past"}

    _enrich_event(event)

    if quarantine_new:
        pending = load_pending()
        idx = next((i for i, p in enumerate(pending) if p.get("id") == event["id"]), None)
        event["_quarantined_new"] = True
        if idx is not None:
            # Keep the first-seen timestamp so queue age reflects reality.
            event["_quarantined_at"] = pending[idx].get(
                "_quarantined_at", datetime.now(timezone.utc).isoformat()
            )
            pending[idx] = event
        else:
            event["_quarantined_at"] = datetime.now(timezone.utc).isoformat()
            pending.append(event)
            _append_changelog("quarantine_new", event["id"])
        save_pending(pending)
        return {"status": "quarantined_new", "event": event}

    active.append(event)
    save_active(active)
    _clear_stale_rejected(event["id"])
    _append_changelog("add", event["id"])
    return {"status": "added", "event": event}


@_locked
def archive_past_events() -> list[dict]:
    """Move past events from active to archive. Returns archived events."""
    active = load_active()
    archive = load_archive()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    still_active = []
    newly_archived = []

    for ev in active:
        dt = last_occurrence(ev)
        if dt is None:
            still_active.append(ev)
            continue

        if dt < cutoff:
            ev["archivedAt"] = now.isoformat()
            newly_archived.append(ev)
            _append_changelog("archive", ev["id"])
        else:
            still_active.append(ev)

    if newly_archived:
        # An event can reach the archive twice — a re-scrape that fails to
        # match the archived copy (a changed venue string is enough) lands a
        # fresh active record with the same id, which is archived again on the
        # next run. Appending blindly left byte-identical pairs in the archive,
        # and publish() ships the archive verbatim, so the site rendered the
        # same past event twice. Refresh the stored copy instead.
        by_id = {ev.get("id"): i for i, ev in enumerate(archive) if ev.get("id")}
        for ev in newly_archived:
            idx = by_id.get(ev.get("id"))
            if idx is None:
                by_id[ev.get("id")] = len(archive)
                archive.append(ev)
            else:
                archive[idx] = ev
        # Destination (archive) before source (active).
        save_archive(archive)
        save_active(still_active)

    return newly_archived


@_locked
def archive_event(event_id: str, reason: str = "") -> dict:
    """Move one active event to the archive by hand, whatever its dates.

    Returns ``{"status": "archived", "event": ...}`` or ``{"status":
    "not_found", "event": None}``. The archive is written before active is,
    so an interrupted move duplicates rather than loses.
    """
    active = load_active()
    idx = next((i for i, ev in enumerate(active) if ev.get("id") == event_id), None)
    if idx is None:
        return {"status": "not_found", "event": None,
                "message": f"No active event with id '{event_id}'"}

    event = dict(active[idx])
    event["archivedAt"] = datetime.now(timezone.utc).isoformat()

    archive = load_archive()
    a_idx = next((i for i, ev in enumerate(archive) if ev.get("id") == event_id), None)
    if a_idx is None:
        archive.append(event)
    else:
        archive[a_idx] = event
    save_archive(archive)

    active.pop(idx)
    save_active(active)
    _append_changelog("archive", event_id, reason or "archived by hand")
    return {"status": "archived", "event": event}


def _merge_into_archived(event: dict, candidate_id: str) -> Optional[dict]:
    """Merge an approved event into its dedup candidate when that candidate is
    already archived. Returns None when the candidate is not there, leaving the
    caller on the ordinary add_event() path.

    approve_pending() lands its event with add_event(force=True), and force
    deliberately skips add_event's archive-match branch. So approving a dedup
    pair whose candidate had already been archived appended a second copy to
    active instead of merging, and archive_past_events() then filed it beside
    the original — one past festival, two archive rows, two searchable ghosts
    (Boston Salsa Fest, 2026-08-27). Approval knows the candidate's id
    outright, so send the merge to wherever that candidate actually lives.
    """
    active = load_active()
    if any(e.get("id") == candidate_id for e in active):
        return None

    archive = load_archive()
    idx = next((i for i, e in enumerate(archive) if e.get("id") == candidate_id), None)
    if idx is None:
        return None

    archived = archive[idx]
    confidence = dedup_confidence(archived, event) or "review"
    reason = _dedup_reason(archived, event, confidence)
    merged = merge_event(archived, event)
    _enrich_event(merged)

    # Approving a still-upcoming event has to put it back on the map; one whose
    # merged dates have already passed stays filed. Same cutoff
    # archive_past_events() uses, so the two can never disagree and bounce a
    # record between the stores.
    last = last_occurrence(merged)
    if last is not None and last >= datetime.now(timezone.utc) - timedelta(hours=24):
        merged["reactivatedAt"] = datetime.now(timezone.utc).isoformat()
        # Destination (active) before source (archive).
        active.append(merged)
        save_active(active)
        archive.pop(idx)
        save_archive(archive)
        _clear_stale_rejected(merged["id"])
        _log_dedup("reactivate", archived, event, confidence, reason)
        _append_changelog("reactivate", merged["id"],
                          f"approved {event['id']} merged into archived {candidate_id}")
        return {"status": "reactivated", "confidence": confidence, "event": merged}

    archive[idx] = merged
    save_archive(archive)
    _log_dedup("approve_merge", archived, event, confidence, reason)
    _append_changelog("merge", event["id"], f"folded into archived {candidate_id}")
    return {"status": "merged_into_archive", "confidence": confidence, "event": merged}


# Statuses from add_event / _merge_into_archived that mean "the event now lives
# in a store". Anything else means it went nowhere, and an approval must then
# leave its source row where it was instead of deleting the only copy.
_LANDED_STATUSES = frozenset({
    "added", "duplicate", "merged", "reactivated", "merged_into_archive", "quarantined_new",
})
_PENDING_MARKERS = ("_dedup_candidate_of", "_dedup_confidence", "_dedup_reason",
                    "_quarantined_new", "_quarantined_at")
_REJECTED_MARKERS = ("_rejected_at", "_rejected_reason", "_review_type")


def _without(event: dict, keys: tuple) -> dict:
    return {k: v for k, v in event.items() if k not in keys}


def _not_approved(result: dict, stored: dict, queue: str) -> dict:
    """Shape the failure of an approval so the caller knows nothing moved."""
    return {
        "status": "not_approved",
        "add_status": result.get("status"),
        "message": (
            f"{result.get('message') or result.get('status')} — the event was left "
            f"in {queue}; nothing was removed."
        ),
        "event": stored,
    }


@_locked
def approve_pending(event_id: str, force: bool = False) -> dict:
    """Approve a pending event, moving it to active.

    For a dedup pair (``_dedup_candidate_of`` set), approving *merges* the two
    and persists a permanent ``verdict:"same"`` so future occurrences auto-merge
    with no review. Because that is silent and compounding, this refuses to merge
    across a special-edition boundary unless ``force=True``.

    The candidate may already be archived, in which case the merge happens there
    — see _merge_into_archived. The merged record only returns to active if its
    dates are still ahead.

    Destination first: the event is landed (add_event / archive merge) and only
    then removed from pending. If landing fails, the row stays queued and the
    result says ``status: not_approved`` with the underlying ``add_status``.
    """
    pending = load_pending()
    idx = next((i for i, ev in enumerate(pending) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No pending event with id '{event_id}'"}

    stored = pending[idx]
    candidate_id = stored.get("_dedup_candidate_of")

    if candidate_id and not force:
        candidate = next((e for e in load_active() if e.get("id") == candidate_id), None)
        if candidate is None:
            candidate = next((e for e in load_archive() if e.get("id") == candidate_id), None)
        if candidate is not None and _special_edition_mismatch(stored, candidate):
            return {
                "status": "blocked_special_edition",
                "message": (
                    "Refusing to merge: one of these is a special edition "
                    "(anniversary / festival / takeover / guest-DJ night) and the "
                    "other is the recurring series — special editions stay separate. "
                    "If they genuinely are the same event, call "
                    "event_approve(event_id, force=True). Otherwise "
                    "event_reject(event_id, reason='distinct event')."
                ),
                "new_event": {"id": stored["id"], "name": stored.get("name", "")},
                "existing_event": {"id": candidate["id"], "name": candidate.get("name", "")},
            }

    # The verdict goes in first so add_event's own dedup sees the pair as
    # certain and merges instead of re-queueing. It is rolled back if the
    # approval does not land.
    had_verdict = bool(candidate_id) and \
        _known_duplicate_verdict({"id": event_id}, {"id": candidate_id}) is not None
    if candidate_id:
        _persist_known_duplicate(event_id, candidate_id, "same")

    event = _without(stored, _PENDING_MARKERS)
    issues = validate_event(event)
    result = _merge_into_archived(event, candidate_id) if candidate_id else None
    if result is None:
        result = add_event(event, force=True)

    if result.get("status") not in _LANDED_STATUSES:
        if candidate_id and not had_verdict:
            forget_known_duplicate(event_id, candidate_id)
        return _not_approved(result, stored, "pending.json")

    # Landed. Now, and only now, retire the queued row.
    pending = [p for p in load_pending() if p.get("id") != event_id]
    save_pending(pending)

    if issues:
        result["warnings"] = issues
    # An approved event with no coordinates renders no map pin — it is live but
    # invisible. Flag that loudly so the agent fixes the location before publish
    # instead of shipping a ghost.
    added = result.get("event") or result.get("existing") or {}
    if added.get("lat") is None or added.get("lng") is None:
        result["published_without_coordinates"] = True
        result.setdefault("warnings", []).append(
            "no coordinates — event will NOT appear on the map; fix location via "
            "event_edit or event_set_location_override before publishing"
        )
    _append_changelog("approve", event_id)
    return result


@_locked
def remove_active_event(event_id: str, reason: str = "removed from active", block: bool = False, block_category: str = "other") -> dict:
    """Remove an active event.

    If block=True, moves to blocked.json (permanent, prevents re-scraping).
    If block=False, moves to rejected.json for review (as before).

    The destination (blocklist or rejected queue) is written before the event
    leaves active, so a failure — an invalid block category, a crash — never
    loses the record.
    """
    active = load_active()
    idx = next((i for i, ev in enumerate(active) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No active event with id '{event_id}'"}

    event = active[idx]

    if block:
        outcome = _add_to_blocked(dict(event), block_category, reason)
        if outcome.get("status") != "blocked":
            return outcome
        active.pop(idx)
        save_active(active)
        return outcome

    queued = _queue_rejected(event, reason)
    active.pop(idx)
    save_active(active)
    _append_changelog("remove", event_id, reason)
    return {"status": "removed", "event": queued}


@_locked
def approve_rejected(event_id: str) -> dict:
    """Promote a rejected event to active (bypasses Latin relevance check).

    Lands the event first; the rejected row is only removed once it has.
    """
    rejected = load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No rejected event with id '{event_id}'"}

    stored = rejected[idx]
    event = _without(stored, _REJECTED_MARKERS)

    result = add_event(event, force=True, skip_latin_check=True)
    if result.get("status") not in _LANDED_STATUSES:
        return _not_approved(result, stored, "rejected.json")

    # add_event clears the rejected row on most landing paths; the force-merge
    # path does not, so reconcile here rather than trust it.
    rejected = [r for r in load_rejected() if r.get("id") != event_id]
    save_rejected(rejected)
    _append_changelog("approve_rejected", event_id, "promoted from rejected queue")
    return result


@_locked
def dismiss_rejected(event_id: str, reason: str = "", block: bool = False, block_category: str = "other") -> dict:
    """Dismiss a rejected event.

    If block=True, moves to blocked.json (permanent, prevents re-scraping).
    If block=False, just removes from rejected (for one-off events that won't reappear).
    """
    rejected = load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No rejected event with id '{event_id}'"}

    stored = rejected[idx]

    if block:
        outcome = _add_to_blocked(dict(stored), block_category, reason)
        if outcome.get("status") != "blocked":
            return outcome
        rejected.pop(idx)
        save_rejected(rejected)
        return outcome

    rejected.pop(idx)
    save_rejected(rejected)
    event = _without(stored, _REJECTED_MARKERS)
    _append_changelog("dismiss_rejected", event_id, reason)
    return {"status": "dismissed", "event": event, "reason": reason}


VALID_BLOCK_CATEGORIES = ("defunct", "class_only", "not_latin", "not_dance", "out_of_area", "duplicate_source", "other")


def _add_to_blocked(event: dict, category: str, notes: str = "") -> dict:
    """Internal helper to add an event to the blocklist."""
    if category not in VALID_BLOCK_CATEGORIES:
        return {"status": "error", "message": f"Invalid category '{category}'. Use one of: {VALID_BLOCK_CATEGORIES}"}

    blocked = load_blocked()
    now = datetime.now(timezone.utc).isoformat()

    for key in ("_rejected_at", "_rejected_reason", "_review_type"):
        event.pop(key, None)

    record = {
        "id": event["id"],
        "name": event.get("name", ""),
        "source": event.get("source", ""),
        "blocked_reason": notes or category,
        "blocked_category": category,
        "blocked_at": now,
        "blocked_notes": notes,
        "location": event.get("location", ""),
        # Frozen at block time so the block survives the source re-minting ids.
        "block_key": _block_key(event),
    }

    for i, existing in enumerate(blocked):
        if existing["id"] == event["id"]:
            blocked[i] = record
            save_blocked(blocked)
            _append_changelog("block", event["id"], f"{category}: {notes}")
            return {"status": "blocked", "event": record}

    blocked.append(record)
    save_blocked(blocked)
    _append_changelog("block", event["id"], f"{category}: {notes}")
    return {"status": "blocked", "event": record}


@_locked
def block_event(event_id: str, category: str, notes: str = "") -> dict:
    """Block an event permanently. Removes from active or rejected and adds to blocked.json.

    Categories: defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other

    The blocklist entry is written first; the copies in active / rejected /
    pending (and archive, only when the event is nowhere else) are removed
    after. A failure part-way leaves a copy that the blocklist then rejects on
    re-ingest — never a record that is in neither place.
    """
    if category not in VALID_BLOCK_CATEGORIES:
        return {"status": "error", "message": f"Invalid category '{category}'. Use one of: {VALID_BLOCK_CATEGORIES}"}

    stores = [
        (load_active, save_active),
        (load_rejected, save_rejected),
        (load_pending, save_pending),
    ]
    found = None
    removals: list[tuple[list, int, callable]] = []
    for load, save in stores:
        items = load()
        idx = next((i for i, ev in enumerate(items) if ev["id"] == event_id), None)
        if idx is None:
            continue
        if found is None:
            found = items[idx]
        removals.append((items, idx, save))

    if found is None:
        archive = load_archive()
        a_idx = next((i for i, ev in enumerate(archive) if ev["id"] == event_id), None)
        if a_idx is not None:
            found = archive[a_idx]
            removals.append((archive, a_idx, save_archive))

    if found is None:
        return {"status": "not_found", "message": f"Event '{event_id}' not found in active, rejected, pending, or archive."}

    outcome = _add_to_blocked(dict(found), category, notes)
    if outcome.get("status") != "blocked":
        return outcome

    for items, idx, save in removals:
        items.pop(idx)
        save(items)
    return outcome


@_locked
def unblock_event(event_id: str) -> dict:
    """Remove an event from the blocklist. It will be re-added on the next scrape if still in the source."""
    blocked = load_blocked()
    idx = next((i for i, ev in enumerate(blocked) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"Event '{event_id}' not found in blocked.json."}

    record = blocked.pop(idx)
    save_blocked(blocked)
    _append_changelog("unblock", event_id, f"was: {record.get('blocked_category', '')}")
    return {"status": "unblocked", "event": record}


@_locked
def reject_pending(event_id: str, reason: str = "") -> dict:
    """Reject a pending event."""
    pending = load_pending()
    idx = next((i for i, ev in enumerate(pending) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No pending event with id '{event_id}'"}

    stored = pending[idx]
    candidate_id = stored.get("_dedup_candidate_of")
    # The verdict is the durable outcome of a rejection; record it before the
    # queue row goes so an interruption cannot lose the decision.
    if candidate_id:
        _persist_known_duplicate(event_id, candidate_id, "different")

    pending.pop(idx)
    save_pending(pending)
    event = _without(stored, _PENDING_MARKERS)
    _append_changelog("reject", event_id, reason)
    return {"status": "rejected", "event": event, "reason": reason}


@_locked
def edit_event(event_id: str, updates: dict) -> dict:
    """Edit fields on an active event."""
    active = load_active()
    idx = None
    for i, ev in enumerate(active):
        if ev["id"] == event_id:
            idx = i
            break

    if idx is None:
        return {"status": "not_found", "message": f"No active event with id '{event_id}'"}

    # A link the reviewer deletes has to stay deleted. Re-scrapes accumulate
    # every URL a source has ever carried back into urls[], so clearing a dead
    # alt link only held until the next ingest put it straight back — the same
    # facebook share wrapper and expired instagram post came back to
    # check-links week after week. Record the removals; merge_event honours them.
    before = _event_url_list(active[idx]) if ("url" in updates or "urls" in updates) else []

    for k, v in updates.items():
        if k != "id":
            active[idx][k] = v

    if before:
        kept = {_url_key(u) for u in _event_url_list(active[idx])}
        dropped = {_url_key(u): u for u in _dropped_url_list(active[idx])}
        dropped.update({_url_key(u): u for u in before if _url_key(u) not in kept})
        # An edit that puts a link back overrides an earlier removal.
        surviving = sorted(v for k_, v in dropped.items() if k_ not in kept)
        if surviving:
            active[idx]["_dropped_urls"] = surviving
        else:
            active[idx].pop("_dropped_urls", None)

    # Re-geocode if location changed
    if "location" in updates and (active[idx].get("lat") is None or "location" in updates):
        coords = geocode(updates["location"])
        if coords:
            active[idx]["lat"], active[idx]["lng"] = coords

    save_active(active)
    _append_changelog("edit", event_id, json.dumps(updates))
    return {"status": "updated", "event": active[idx]}


def _load_source_names() -> dict[str, str]:
    """Map source IDs to human-readable organizer names from data/sources.json."""
    return {s["id"]: s["name"] for s in load_sources() if "id" in s and "name" in s}


def _strip_internal_fields(ev: dict, source_names: dict[str, str]) -> None:
    """Add slug/organizer/special, remove internal fields from an event dict."""
    ev["slug"] = slugify(ev["name"], ev["id"])
    _derive_special(ev)
    if ev.get("recurring") and not ev.get("recurrenceLabel"):
        label = recurrence_label(ev)
        if label:
            ev["recurrenceLabel"] = label
    # Map source ID to human-readable organizer name
    source_id = ev.get("source", "")
    if source_id and source_id in source_names:
        ev["organizer"] = source_names[source_id]
    ev.pop("source", None)
    ev.pop("archivedAt", None)
    ev.pop("reactivatedAt", None)
    for key in list(ev.keys()):
        if key.startswith("_"):
            ev.pop(key)


VENUE_CONFLICT_DECISIONS = ("distinct", "replaces", "duplicate")


def load_venue_conflicts() -> dict:
    """The review queue written by the last publish()."""
    return atomic_io.read_json(
        VENUE_CONFLICTS_JSON,
        default={"generated_at": None, "conflicts": [], "suppressed": []},
    )


@_locked
def _venue_exclude_date(venue_id: str, date_str: str) -> bool:
    """Stop a venue hub from generating a pin on one date. Returns True if added."""
    venues = atomic_io.read_json(VENUES_JSON, default=[])
    for venue in venues:
        if venue.get("id") != venue_id:
            continue
        excluded = venue.setdefault("excludeDates", [])
        if date_str in excluded:
            return False
        excluded.append(date_str)
        excluded.sort()
        atomic_io.write_json(VENUES_JSON, venues)
        return True
    raise ValueError(f"No venue with id '{venue_id}' in venues.json")


@_locked
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

    active = load_active()
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
        start, _end, _assumed = _event_local_window(event)
        if start is None:
            return {"status": "error",
                    "error": "Cannot exclude a hub date: event has no parseable startDate."}
        excluded_date = start.strftime("%Y-%m-%d")
        try:
            _venue_exclude_date(hub_id, excluded_date)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

    save_active(active)
    _append_changelog("venue_conflict_resolved", event_id,
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


def _write_venue_conflicts(report: dict) -> None:
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
    atomic_io.write_json(VENUE_CONFLICTS_JSON, payload)

    # Reporting must never be what breaks a publish, so read every field softly.
    for row in payload["suppressed"]:
        ev, hub = row.get("event", {}), row.get("hub", {})
        resolved = " [resolved: duplicate]" if row.get("resolved") else ""
        _log(f"  🔇 folded into venue hub: {ev.get('name')!r} ({ev.get('window', '?')}) "
             f"→ {hub.get('name')} {hub.get('window', '?')}{resolved}")
    if payload["conflicts"]:
        _log(f"  🔎 {len(payload['conflicts'])} venue conflict(s) need review "
             f"(kept on the map meanwhile) — event_list(status=\"venue_conflict\"):")
        for row in payload["conflicts"]:
            overlap = {True: "times overlap", False: "no time overlap"}.get(
                row.get("times_overlap"), "overlap unknown")
            _log(f"       - {row.get('event', {}).get('name')!r} "
                 f"vs {row.get('hub', {}).get('name')} ({overlap})")


# Archived rows ship in the client bundle only so their pages stay findable;
# nobody reads a past event's full blurb from the search dropdown.
ARCHIVED_DESCRIPTION_LIMIT = 300


def _truncate_description(text: str, limit: int = ARCHIVED_DESCRIPTION_LIMIT) -> str:
    """Cut to at most ``limit`` characters at a word boundary, ending in "…"."""
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    boundary = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip(" \n\t,;:-–—") + "…"


def _roll_series_forward(ev: dict, today: datetime) -> bool:
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
    start = _parse_aware(ev.get("startDate", ""))
    if start is None or start >= today:
        return False
    occurrences = _occurrence_instants(ev)
    upcoming = [d for d in occurrences if d >= today]
    if not upcoming:
        return False
    new_start = upcoming[0]
    delta = new_start - start
    ev["firstStartDate"] = ev["startDate"]
    ev["startDate"] = _eastern_iso(new_start)
    end = _parse_aware(ev.get("endDate", ""))
    if end is not None:
        ev["endDate"] = _eastern_iso(end + delta)
    ev["recurrences"] = [_eastern_iso(d) for d in occurrences]
    ev["dayOfWeek"] = _weekday_of(ev["startDate"]) or ev.get("dayOfWeek")
    return True


def _compute_publish(*, enrich_missing: bool = True, record_dedup_log: bool = True) -> dict:
    """Everything a publish would ship, computed without writing a byte.

    Split from the write step so publish_guarded() can measure the result
    against the previous file and, when the tripwire trips, ship nothing at
    all — no half-written JSON, no slug registry that has already retired
    URLs for a publish that never happened.
    """
    source_names = _load_source_names()
    unreliable_sources = unreliable_source_ids()

    # Belt-and-suspenders: never ship pins from unreliable sources even if a
    # stale active row survived (e.g. before the source was demoted).
    active = [
        e for e in load_active()
        if e.get("source") not in unreliable_sources
    ]
    venue_events = expand_venues()

    active, suppressed_venue_ids, venue_report = _suppress_venue_covered_events(venue_events, active)
    # Irregular-schedule venues (nextDateApproximate) never get a pin: their
    # expanded dates are pattern guesses, so users only ever see the venue via
    # a confirmed scraped event. But the venue itself stays findable — when no
    # scraped event covers it, we publish a dateless search-only record below.
    irregular_venues = [
        v for v in venue_events
        if v.get("nextDateApproximate") and v.get("id") not in suppressed_venue_ids
    ]
    venue_events = [
        v for v in venue_events
        if v.get("id") not in suppressed_venue_ids and not v.get("nextDateApproximate")
    ]
    all_events = venue_events + active
    deduped = deduplicate(all_events, record_log=record_dedup_log)
    deduped = collapse_recurring_series(deduped)

    # A live series must advertise its next night, not the one it was first
    # scraped with.
    today = datetime.now(NY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    rolled = [ev.get("id") for ev in deduped if _roll_series_forward(ev, today)]

    # Sort by start date
    deduped.sort(key=lambda e: e.get("startDate", ""))

    # Publishing may repair missing coordinates; read-only diagnostics must not
    # make network calls or mutate even their in-memory preview unexpectedly.
    if enrich_missing:
        for ev in deduped:
            if ev.get("lat") is None or ev.get("lng") is None:
                _enrich_event(ev)

    # Strip internal fields from active events
    for ev in deduped:
        _strip_internal_fields(ev, source_names)

    # Include archived events so their pages persist for SEO. They ship in the
    # client bundle, so their descriptions are cut down to a preview.
    archive = load_archive()
    archived_out = []
    for ev in archive:
        if enrich_missing and (ev.get("lat") is None or ev.get("lng") is None):
            _enrich_event(ev)
        _strip_internal_fields(ev, source_names)
        ev["archived"] = True
        if ev.get("description"):
            ev["description"] = _truncate_description(ev["description"])
        archived_out.append(ev)

    # Dateless search-only records for irregular venues: searchable, with a
    # detail page and a ghost dot when opened — never a pin, feed row, or
    # filter hit. Guessed dates are stripped so no uncertain date ever ships.
    searchonly_out = []
    for ev in irregular_venues:
        rec = dict(ev)
        rec["startDate"] = ""
        rec["endDate"] = ""
        rec.pop("recurrences", None)
        # Weekly-schedule rows would read as "happens every week"; the
        # recurrenceLabel + description carry the real cadence.
        rec.pop("schedule", None)
        rec["searchOnly"] = True
        if rec.get("_sourceId"):
            rec["source"] = rec["_sourceId"]
        if enrich_missing and (rec.get("lat") is None or rec.get("lng") is None):
            _enrich_event(rec)
        _strip_internal_fields(rec, source_names)
        searchonly_out.append(rec)

    published = deduped + archived_out + searchonly_out
    moved_slugs = _resolve_slug_collisions(published)

    missing = [ev for ev in deduped if ev.get("lat") is None or ev.get("lng") is None]
    odd_hours = [(e, h) for e in deduped
                 if (h := implausible_start_hour(e)) is not None]

    return {
        "published": published,
        "deduped": deduped,
        "archived_out": archived_out,
        "searchonly_out": searchonly_out,
        "venue_report": venue_report,
        "moved_slugs": moved_slugs,
        "missing": missing,
        "odd_hours": odd_hours,
        "rolled": rolled,
    }


def preview_publish() -> dict:
    """Compute publish artifacts without writes, geocoding, or dedup logging."""
    with atomic_io.locked(STORE_LOCK):
        return _compute_publish(enrich_missing=False, record_dedup_log=False)


def _legacy_public_json() -> Path:
    """Resolved at call time: tests relocate ROOT."""
    return ROOT / "public" / "events.json"


def _commit_publish(art: dict) -> dict:
    """Write a computed publish to disk and report on it (stderr only)."""
    published = art["published"]
    deduped = art["deduped"]
    venue_report = art["venue_report"]

    _write_venue_conflicts(venue_report)

    if art["searchonly_out"]:
        names = ", ".join(repr(e.get("name", "?")) for e in art["searchonly_out"])
        _log(f"  ℹ️  {len(art['searchonly_out'])} irregular venue(s) published as search-only records: {names}")

    if art["moved_slugs"]:
        _log(f"  🔀 {len(art['moved_slugs'])} event(s) had a colliding slug and were re-slugged:")
        for event_id, slug in art["moved_slugs"]:
            _log(f"       - {event_id} → {slug}")

    # Loudly surface anything shipping without coordinates — those events never
    # render a pin on the map, so they're effectively invisible to visitors.
    if art["missing"]:
        _log(f"  ⚠️  {len(art['missing'])} active event(s) have no coordinates (won't appear on map):")
        for ev in art["missing"]:
            _log(f"       - {ev.get('name', '?')!r}  ({ev.get('location') or 'no location'})")

    if art["rolled"]:
        _log(f"  📅 {len(art['rolled'])} recurring series rolled forward to their next occurrence")

    _write_json(PUBLIC_EVENTS_JSON, published)
    # Legacy path for scripts still referencing public/events.json
    _write_json(_legacy_public_json(), published)

    # Record this run's URLs and re-point any that this publish just retired.
    # Every publish path goes through here — the pipeline's and the agent's —
    # so a slug can never quietly disappear between the index and the site.
    # A registry *problem* must not un-ship the events already written, so it
    # is reported rather than raised — except a corrupt registry file, which
    # must stop the run instead of being rebuilt from nothing.
    registry_result = None
    try:
        from slug_registry import update as _update_slug_registry
        registry_result = _update_slug_registry()
        if registry_result["alias"] or registry_result["ended"]:
            _log(f"  🔗 urls: {registry_result['live']} live, "
                 f"{registry_result['alias']} redirecting, {registry_result['ended']} ended")
    except CorruptJSONError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        _log(f"  ⚠️  slug registry not updated ({exc}) — retired URLs may 404")

    odd_hours = art["odd_hours"]
    if odd_hours:
        _log(f"  ⏰ {len(odd_hours)} event(s) start in the small hours — check for a "
             f"timezone conversion bug before trusting these:")
        for e, h in odd_hours[:5]:
            _log(f"       - {e.get('name', '?')[:52]} starts {h}:00 AM Boston time")

    return {
        "status": "published",
        "count": len(deduped),
        "implausible_start_hours": [
            {"id": e.get("id"), "name": e.get("name"), "hour": h} for e, h in odd_hours
        ],
        "retired_urls": (registry_result["alias"] + registry_result["ended"]) if registry_result else None,
        "archived_count": len(art["archived_out"]),
        "search_only_count": len(art["searchonly_out"]),
        "venue_suppressed_count": len(venue_report.get("suppressed", [])),
        "venue_conflict_count": len(venue_report.get("conflicts", [])),
        "series_rolled_forward": len(art["rolled"]),
        "path": str(PUBLIC_EVENTS_JSON),
    }


def publish() -> dict:
    """Generate events-published.json from active + archived events + expanded venues."""
    with atomic_io.locked(STORE_LOCK):
        return _commit_publish(_compute_publish())


# Refuse to ship a published file whose live-event count collapsed relative to a
# baseline — a broken scrape or an over-zealous review pass must never wipe the
# map. Shared by the deterministic pipeline and the agent's own publish.
TRIPWIRE_MIN_PREVIOUS = 20
TRIPWIRE_MIN_RATIO = 0.7


def _live_event_count(text: Optional[str]) -> int:
    if not text:
        return 0
    try:
        return sum(1 for e in json.loads(text) if not e.get("archived"))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0


def publish_guarded(previous_snapshot: Optional[str] = None) -> dict:
    """publish(), unless the live-event count would collapse below
    ``TRIPWIRE_MIN_RATIO`` of the baseline — then write nothing and report
    ``tripped: True``.

    The check runs on the computed result *before* any file is touched, so a
    tripped publish leaves the published JSON, the venue-conflict queue and the
    slug registry exactly as they were. (Restoring after the fact used to leave
    the registry with URLs retired for a publish that was then rolled back.)

    Baseline defaults to the current published file — the right reference for
    the agent's own publish, which runs after the deterministic refresh already
    published. Callers holding an earlier baseline (run_pipeline, which snapshots
    before scrape/ingest/archive) pass it in explicitly.
    """
    with atomic_io.locked(STORE_LOCK):
        if previous_snapshot is None:
            previous_snapshot = (
                PUBLIC_EVENTS_JSON.read_text() if PUBLIC_EVENTS_JSON.exists() else None
            )
        previous_live = _live_event_count(previous_snapshot)

        art = _compute_publish()
        new_live = sum(1 for e in art["published"] if not e.get("archived"))
        tripped = (
            previous_live >= TRIPWIRE_MIN_PREVIOUS
            and new_live < previous_live * TRIPWIRE_MIN_RATIO
        )
        if tripped:
            message = (
                f"live events would fall {previous_live} → {new_live} "
                f"(below {int(TRIPWIRE_MIN_RATIO * 100)}% of baseline); nothing was "
                "written — do NOT commit. Investigate first."
            )
            _log(f"  🚨 tripwire: {message}")
            return {
                "status": "tripwire",
                "tripped": True,
                "message": message,
                "count": len(art["deduped"]),
                "previous_live_events": previous_live,
                "published_live_events": new_live,
                "venue_suppressed_count": len(art["venue_report"].get("suppressed", [])),
                "venue_conflict_count": len(art["venue_report"].get("conflicts", [])),
                "path": str(PUBLIC_EVENTS_JSON),
            }

        result = _commit_publish(art)
        result["tripped"] = False
        result["previous_live_events"] = previous_live
        result["published_live_events"] = new_live
        return result


@_locked
def ingest_scraped(source_id: Optional[str] = None, quarantine_new: bool = False) -> dict:
    """Ingest events from data/scraped/ into the active store.

    Handles dedup against both active and archive (reactivation).
    Review-tier duplicates are routed to pending.json for review.

    quarantine_new=True additionally routes brand-new events to pending.json
    instead of active (for unattended runs — see add_event).
    """
    if source_id:
        files = [SCRAPED_DIR / f"{source_id}.json"]
    else:
        files = sorted(
            p for p in SCRAPED_DIR.glob("*.json")
            if not p.name.endswith("-raw.json")
        )

    added = 0
    merged = 0
    reactivated = 0
    skipped = 0
    rejected_non_latin = 0
    rejected_out_of_area = 0
    blocked = 0
    pending_review = 0
    quarantined_new = 0
    review_items: list[dict] = []
    corrupt_files: list[str] = []

    _blocked = load_blocked()
    blocked_ids = {b["id"] for b in _blocked}
    blocked_keys = _blocked_keys(_blocked)
    trusted = _trusted_latin_sources()

    # Sources ranked "noisy" (see data/sources.json + source_signal.py) always
    # route brand-new finds to the pending queue for review, even when the run
    # otherwise publishes directly -- their raw feeds are mostly non-dance.
    # Sources marked unreliable scrape for research but never enter the store.
    # A malformed sources.json raises here and aborts the run: silently
    # treating every source as trusted-and-reliable is worse than no ingest.
    noisy_sources = noisy_source_ids()
    unreliable_sources = unreliable_source_ids()

    skipped_unreliable = 0

    for path in files:
        if not path.exists():
            continue
        try:
            events = atomic_io.read_json(path, default=[])
        except CorruptJSONError as exc:
            # A scraper's output is an input, not the store. Skip it loudly so
            # the other sources still ingest, and name it in the result.
            _log(f"  ⚠️  skipping unreadable scrape file {path.name}: {exc}")
            corrupt_files.append(path.name)
            continue

        for ev in events:
            if not ev.get("id"):
                continue
            if ev.get("source") in unreliable_sources:
                skipped_unreliable += 1
                continue
            eff_quarantine = quarantine_new or (ev.get("source") in noisy_sources)
            result = add_event(ev, blocked_ids=blocked_ids, blocked_keys=blocked_keys,
                               quarantine_new=eff_quarantine, trusted_sources=trusted)
            status = result["status"]
            if status == "added":
                added += 1
            elif status == "quarantined_new":
                quarantined_new += 1
            elif status == "merged":
                merged += 1
            elif status == "reactivated":
                reactivated += 1
            elif status == "duplicate":
                skipped += 1
            elif status == "skipped_past":
                skipped += 1
            elif status == "rejected_non_latin":
                rejected_non_latin += 1
            elif status == "rejected_out_of_area":
                rejected_out_of_area += 1
            elif status == "blocked":
                blocked += 1
            elif status == "pending_review":
                pending_review += 1
                review_items.append({
                    "new": result["new_event"]["name"],
                    "existing": result["existing_event"]["name"],
                    "reason": result["reason"],
                })

    result = {
        "status": "ingested",
        "added": added,
        "merged": merged,
        "reactivated": reactivated,
        "skipped_duplicates": skipped,
        "skipped_unreliable": skipped_unreliable,
        "rejected_non_latin": rejected_non_latin,
        # Legacy name for the same count, kept for run_pipeline's summary.
        "dropped_non_latin": rejected_non_latin,
        "rejected_out_of_area": rejected_out_of_area,
        "blocked": blocked,
        "pending_review": pending_review,
        "quarantined_new": quarantined_new,
        "files_processed": len(files),
    }
    if corrupt_files:
        result["files_corrupt"] = corrupt_files
    if review_items:
        result["review_items"] = review_items
    return result


# ── Venues and sources ────────────────────────────────────────────────

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
            elif time_str.strip() and not (_parse_time_range(time_str) or _parse_time(time_str)):
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
                not isinstance(value, str) or _parse_anchor(value) is None
            ):
                problems.append(
                    f"{label}: {bound} must be a YYYY-MM-DD date (got {value!r})"
                )

        anchor = entry.get("anchor")
        if anchor is not None:
            parsed = _parse_anchor(anchor) if isinstance(anchor, str) else None
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


@_locked
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
    venue_id = (venue.get("id") or _slug_base(name)).strip()
    venues = atomic_io.read_json(VENUES_JSON, default=[])
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
        coords = geocode(record["location"])
        if coords:
            record["lat"], record["lng"] = coords
        else:
            warnings.append("could not geocode location — the venue will have no map pin until lat/lng are set")

    venues.append(record)
    atomic_io.write_json(VENUES_JSON, venues)
    _append_changelog("venue_add", venue_id, name)
    result = {"status": "added", "problems": [], "venue": record}
    if warnings:
        result["warnings"] = warnings
    return result


def load_venues() -> list[dict]:
    """Load permanent venues through the same strict JSON reader as the store."""
    return atomic_io.read_json(VENUES_JSON, default=[])


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
            if not isinstance(value, str) or _parse_anchor(value) is None:
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


@_locked
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
        coords = geocode(candidate.get("location", ""))
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
    atomic_io.write_json(VENUES_JSON, venues)
    _append_changelog("venue_edit", venue_id, json.dumps(changed, sort_keys=True))
    return {"status": "updated", "venue": candidate, "changes": changed}


_SOURCE_REQUIRED = ("id", "type", "scraper", "name")
_SOURCE_LOCATORS = ("url", "search_queries", "facebook_events_url")


@_locked
def add_source(source: dict) -> dict:
    """Append a source to data/sources.json.

    Requires ``id``, ``type``, ``scraper``, ``name`` and at least one of
    ``url`` / ``search_queries`` / ``facebook_events_url``; refuses a
    duplicate id. ``enabled`` defaults to true. Returns ``{"status":
    "added"|"invalid"|"exists", "problems": [...]}``; ``added`` results
    carry the stored ``source``.
    """
    if not isinstance(source, dict):
        return {"status": "invalid", "problems": ["source must be an object"]}

    problems = [f"missing {key}" for key in _SOURCE_REQUIRED if not source.get(key)]
    if not any(source.get(key) for key in _SOURCE_LOCATORS):
        problems.append(f"needs one of {', '.join(_SOURCE_LOCATORS)}")
    if problems:
        return {"status": "invalid", "problems": problems}

    sources = atomic_io.read_json(SOURCES_JSON, default=[])
    if any(s.get("id") == source["id"] for s in sources):
        return {"status": "exists", "problems": [f"a source with id {source['id']!r} already exists"]}

    entry = dict(source)
    entry.setdefault("enabled", True)
    sources.append(entry)
    atomic_io.write_json(SOURCES_JSON, sources)
    _append_changelog("source_add", entry["id"], entry["name"])
    return {"status": "added", "problems": [], "source": entry}

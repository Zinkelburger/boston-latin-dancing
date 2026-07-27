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

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unicodedata import normalize as unicode_normalize
from zoneinfo import ZoneInfo

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import ROOT, SCRAPED_DIR, VENUE_COORDS, clean_location, geocode, detect_styles, extract_cost, load_sources, mentions_latin, _eventbrite_address, _normalize, _is_near_boston
from recurrence_utils import recurrence_label

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

NY_TZ = ZoneInfo("America/New_York")

EVENTS_DIR.mkdir(parents=True, exist_ok=True)

_known_duplicates_cache: Optional[list[dict]] = None

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
# physical venue match even when sources name the venue differently.

LOCATION_ALIASES: dict[str, str] = {
    "rumba y timbal": "rumba-y-timbal",
    "rumba y timbal dance studio": "rumba-y-timbal",
    "7 temple st": "rumba-y-timbal",
    "7 temple street": "rumba-y-timbal",
    "j&l dance studio": "jl-dance-studio",
    "j&l dance": "jl-dance-studio",
    "75 pleasant st": "jl-dance-studio",
    "75 pleasant street": "jl-dance-studio",
    "the anchor boston": "the-anchor",
    "the anchor": "the-anchor",
    "1 shipyard park": "the-anchor",
    "shipyard park": "the-anchor",
    "hatch shell on the esplanade": "hatch-shell",
    "hatch memorial shell": "hatch-shell",
    "docks near the hatch memorial shell": "hatch-shell",
    "the dante alighieri society of massachusetts": "dante-alighieri",
    "dante alighieri society": "dante-alighieri",
    "41 hampshire st": "dante-alighieri",
    "marina bay quincy": "marina-bay-quincy",
    "marina bay ferry": "marina-bay-quincy",
    "552 victory road": "marina-bay-quincy",
    "552 victory rd": "marina-bay-quincy",
    "magazine beach": "magazine-beach",
    "magazine beach park": "magazine-beach",
    "668 memorial dr": "magazine-beach",
    "668 memorial drive": "magazine-beach",
    "nature center @ magazine beach park": "magazine-beach",
    "mass audubon magazine beach park nature center": "magazine-beach",
    "the cantab lounge": "cantab-lounge",
    "cantab lounge": "cantab-lounge",
    "738 massachusetts ave": "cantab-lounge",
    # Moves & Vibes (Cambridge) — scraped as "Dance Co", "Dancing Academy",
    # and "Dance and Entertainment Co", with the street as both "5th" and "Fifth".
    "moves & vibes": "moves-vibes",
    "44 5th st": "moves-vibes",
    "44 fifth st": "moves-vibes",
    "44 fifth street": "moves-vibes",
    # La Fábrica Central (Cambridge) — appears with/without accent. (Deliberately
    # not aliasing the bare "450 Massachusetts Ave": special one-off editions are
    # listed by address only, and we don't want them swallowed into the series.)
    "la fabrica": "la-fabrica-central",
    "la fábrica": "la-fabrica-central",
    # El Barco (Boston) — appears by name and as bare address.
    "el barco": "el-barco",
    "50 dalton st": "el-barco",
    "50 dalton street": "el-barco",
    # Wally's Cafe Jazz Club (Boston) — appears with accented apostrophe
    # and with "EE. UU." vs "USA" country suffix across sources.
    "wally's cafe jazz club": "wallys-cafe",
    "wally's cafe": "wallys-cafe",
    "427 massachusetts ave": "wallys-cafe",
}


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
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\b\d{1,2}[/\-]\d{1,2}([/\-]\d{2,4})?\b", "", name)
    name = re.sub(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{1,2}\w*\b", "", name, flags=re.I)
    name = re.sub(r"\bvol\s*\.?\s*\d+\b", "", name)
    name = re.sub(r"#\d+", "", name)
    name = re.sub(r"\b\d{1,2}(st|nd|rd|th)\b", "", name)
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


# Minimum token length eligible for fuzzy (1-edit) matching. Shorter tokens
# match too loosely (e.g. "san"/"sun"), so they require an exact match.
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


def parse_date(iso_str: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None


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
    global _known_duplicates_cache
    if _known_duplicates_cache is not None:
        return _known_duplicates_cache
    if not KNOWN_DUPLICATES_JSON.exists():
        _known_duplicates_cache = []
    else:
        try:
            _known_duplicates_cache = json.loads(KNOWN_DUPLICATES_JSON.read_text())
        except (json.JSONDecodeError, ValueError):
            _known_duplicates_cache = []
    return _known_duplicates_cache


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


def _persist_known_duplicate(id_a: str, id_b: str, verdict: str) -> None:
    """Save a human-reviewed duplicate pair to known_duplicates.json."""
    global _known_duplicates_cache
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

    _known_duplicates_cache = entries
    KNOWN_DUPLICATES_JSON.parent.mkdir(parents=True, exist_ok=True)
    KNOWN_DUPLICATES_JSON.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def list_known_duplicates() -> list[dict]:
    """Return all human-reviewed duplicate verdicts (a copy, newest first)."""
    entries = list(_load_known_duplicates())
    entries.sort(key=lambda e: e.get("reviewed_at", ""), reverse=True)
    return entries


def forget_known_duplicate(id_a: str, id_b: str) -> dict:
    """Delete a stored duplicate verdict so the pair is re-evaluated from scratch.

    Undoes a wrong ``verdict:"same"`` (which otherwise auto-merges the pair
    forever) or a wrong ``verdict:"different"`` (which suppresses the pair from
    review forever). Removing the record does not un-merge already-merged events.
    """
    global _known_duplicates_cache
    pair = set([id_a, id_b])
    entries = _load_known_duplicates()
    kept = [e for e in entries if {e.get("id_a"), e.get("id_b")} != pair]
    if len(kept) == len(entries):
        return {"status": "not_found",
                "message": f"No stored verdict for pair {sorted(pair)}"}
    _known_duplicates_cache = kept
    KNOWN_DUPLICATES_JSON.write_text(json.dumps(kept, indent=2, ensure_ascii=False))
    return {"status": "forgotten", "pair": sorted(pair),
            "remaining": len(kept)}


def _get_day_of_week(event: dict) -> Optional[str]:
    """Return the day of week for an event, from field or parsed startDate."""
    dow = event.get("dayOfWeek")
    if dow:
        return dow
    dt = parse_date(event.get("startDate", ""))
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    _days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    return _days[dt.astimezone(NY_TZ).isoweekday() % 7]


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
            dow_a = _get_day_of_week(a)
            dow_b = _get_day_of_week(b)
            if dow_a and dow_b and dow_a == dow_b:
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
    with open(DEDUP_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _url_host(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url.lower())
    return m.group(1) if m else ""


def _collect_urls(a: dict, b: dict) -> list[str]:
    """Gather unique URLs from both events, keeping one per domain."""
    seen_hosts: set[str] = set()
    seen_urls: set[str] = set()
    result: list[str] = []
    for ev in (a, b):
        for u in [ev.get("url")] + (ev.get("urls") or []):
            if not u:
                continue
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
    for key in ("_verified_at", "_verified_status", "_verified_notes", "_verification_url", "_location_override"):
        if winner.get(key):
            merged[key] = winner[key]
        elif loser.get(key):
            merged[key] = loser[key]

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

    # Accumulate all source URLs into urls[] (primary url stays as-is)
    all_urls = _collect_urls(winner, loser)
    primary = merged.get("url") or ""
    extra = [u for u in all_urls if u and u != primary]
    if extra:
        merged["urls"] = extra

    # Re-scrape of the same event id: refresh date/time from incoming data.
    if winner.get("id") == loser.get("id"):
        for key in ("startDate", "endDate", "dayOfWeek"):
            if loser.get(key):
                merged[key] = loser[key]

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


def deduplicate(events: list[dict]) -> list[dict]:
    """Deduplicate for publish. Only merges 'certain' matches."""
    events.sort(key=source_rank)
    result: list[dict] = []
    for ev in events:
        match = find_duplicate_in(ev, result)
        if match is not None:
            idx, conf = match
            if conf == "certain":
                reason = _dedup_reason(result[idx], ev, conf)
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


def _names_are_same_series(a: str, b: str) -> bool:
    if a == b:
        return True
    if a in b or b in a:
        return True
    words_a = set(a.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
    words_b = set(b.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
    if not words_a or not words_b:
        return False
    shared = _shared_word_count(words_a, words_b)
    smaller = min(len(words_a), len(words_b))
    return shared >= max(2, smaller * 0.6)


def _event_day_of_week(event: dict) -> Optional[str]:
    """Return dayOfWeek from the field or infer from startDate."""
    dow = event.get("dayOfWeek")
    if dow:
        return dow
    dt = parse_date(event.get("startDate", ""))
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    return DAYS_LIST[dt.astimezone(NY_TZ).isoweekday() % 7]


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
        if dt is None or _matches_schedule_note(dt, entry.get("note", ""), day):
            return True
    return False


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


def _suppress_venue_covered_events(venue_events: list[dict], active_events: list[dict]) -> list[dict]:
    """Resolve overlap between venue hubs and scraped events.

    Regular venues: scraped events at the same location+day are suppressed (venue wins).
    Irregular venues (nextDateApproximate): scraped events WIN — the venue entry is
    suppressed when confirmed scraped events exist. This lets the "Date unconfirmed"
    venue entry show only when no confirmed scrape is available.
    """
    regular_hubs = [v for v in venue_events if _is_venue_schedule_record(v) and not v.get("nextDateApproximate")]
    irregular_hubs = [v for v in venue_events if _is_venue_schedule_record(v) and v.get("nextDateApproximate")]

    if not regular_hubs and not irregular_hubs:
        return active_events, set()

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

        if _is_special_edition(normalize_name(ev.get("name", ""))):
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

        # Check regular hubs — suppress scraped event if covered
        suppressed_by_regular = False
        for hub in regular_hubs:
            if not _venue_schedule_covers_event(hub, ev, day):
                continue
            if _scraped_at_venue_hub(hub, ev):
                suppressed_by_regular = True
                break

        if suppressed_by_regular:
            continue

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

    return kept, suppressed_venues


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
        # startDate. Dropping those would lose future occurrences.
        date_set: set[str] = set()
        for ev in group_events:
            if ev.get("startDate"):
                date_set.add(ev["startDate"])
            for r in ev.get("recurrences") or []:
                date_set.add(r)
        dates: list[str] = sorted(date_set)
        if not dates:
            result.extend(group_events)
            continue

        # Preserve the event's duration so endDate never desyncs from startDate
        # when we roll forward to an occurrence no single member's startDate
        # matches (the new startDate often comes from a member's recurrences[]).
        orig_start = parse_date(best.get("startDate", ""))
        orig_end = parse_date(best.get("endDate", ""))
        duration = orig_end - orig_start if (orig_start and orig_end and orig_end >= orig_start) else None

        def _roll_end(new_start_iso: str) -> Optional[str]:
            new_start = parse_date(new_start_iso)
            if new_start is None or duration is None:
                return None
            return (new_start + duration).isoformat()

        now = datetime.now(NY_TZ)
        future_dates = [d for d in dates if parse_date(d) and parse_date(d) >= now]
        new_start = future_dates[0] if future_dates else dates[-1]
        best["startDate"] = new_start
        rolled_end = _roll_end(new_start)
        if rolled_end:
            best["endDate"] = rolled_end

        best["recurring"] = True
        best["recurrences"] = dates

        dt = parse_date(best["startDate"])
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=NY_TZ)
            best["dayOfWeek"] = DAYS_LIST[dt.astimezone(NY_TZ).isoweekday() % 7]

        for ev in group_events[1:]:
            if (best.get("lat") is None or best.get("lng") is None) and ev.get("lat") and ev.get("lng"):
                best["lat"] = ev["lat"]
                best["lng"] = ev["lng"]
            if not best.get("cost") and ev.get("cost"):
                best["cost"] = ev["cost"]
            if not best.get("url") and ev.get("url"):
                best["url"] = ev["url"]

        result.append(best)

    return result


# ── Venue expansion ───────────────────────────────────────────────────

DAY_INDEX = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
             "Thursday": 4, "Friday": 5, "Saturday": 6}
DAYS_LIST = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)


def _parse_time(time_str: str) -> Optional[tuple[int, int]]:
    m = _TIME_RE.search(time_str)
    if not m:
        return None
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


def _matches_schedule_note(date: datetime, note: str, weekday_name: str) -> bool:
    note_lower = note.lower() if note else ""
    nth_match = re.search(r"(\d)(?:st|nd|rd|th)\s+\w+day", note_lower)
    if nth_match:
        nth = int(nth_match.group(1))
        target = _nth_weekday_of_month(date.year, date.month, DAY_INDEX[weekday_name], nth)
        return target is not None and target.date() == date.date()
    if "every other" in note_lower or "alternating" in note_lower:
        ref = datetime(2026, 1, 2)
        week_num = (date - ref).days // 7
        return week_num % 2 == 0
    return True


def expand_venues(weeks_ahead: int = 8) -> list[dict]:
    """Read data/venues.json and generate concrete DanceEvent dicts."""
    if not VENUES_JSON.exists():
        return []

    venues = json.loads(VENUES_JSON.read_text())
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
            d = today
            while d < end_window:
                if d.isoweekday() % 7 == target_wday and d.strftime("%Y-%m-%d") not in exclude_dates:
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
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


def _write_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(path)


def _append_changelog(action: str, event_id: str, details: str = "") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "event_id": event_id,
        "details": details,
    }
    with open(CHANGELOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


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


def slugify(name: str, event_id: str) -> str:
    base = unicode_normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60]
    suffix = event_id[:8].lower()
    return f"{base}-{suffix}"


def validate_event(event: dict) -> list[str]:
    """Return list of validation issues (empty = valid)."""
    issues = []
    if not event.get("name"):
        issues.append("missing name")
    if not event.get("startDate"):
        issues.append("missing startDate")
    if not event.get("location"):
        issues.append("missing location")
    if event.get("lat") is None and event.get("location"):
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
    """
    try:
        return {
            s["id"] for s in load_sources()
            if s.get("latin_by_default") and s.get("id")
        }
    except Exception:
        return set()


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


def _is_latin_relevant(event: dict) -> bool:
    """Return True if the event is relevant to Latin dance.

    Events from a curated Latin source (``latin_by_default``) always pass.
    Events with a recognized style (bachata, salsa, etc.) always pass.
    Events tagged only as 'other' must mention a Latin dance term in
    their name or description.
    """
    if event.get("source") in _trusted_latin_sources():
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


def add_event(
    event: dict,
    force: bool = False,
    skip_latin_check: bool = False,
    blocked_ids: Optional[set] = None,
    quarantine_new: bool = False,
    blocked_keys: Optional[set] = None,
) -> dict:
    """Add an event to the active store. Returns result dict with status.

    Dedup tiers:
      certain -> auto-merge (same ID or URL)
      review  -> route to pending.json for review (unless force=True)

    force=True (admin approval) bypasses the ingest-time exclusion guards
    (blocklist + out-of-area geo-fence). Pass blocked_ids to avoid re-reading
    blocked.json on every call during a batch ingest.

    quarantine_new=True routes brand-new events (no duplicate anywhere) to
    pending.json instead of active, so unattended runs can refresh existing
    events without putting unreviewed ones on the map. Re-scrapes update the
    queued copy in place rather than duplicating it.
    """
    if _is_venue_schedule_record(event):
        return {"status": "rejected", "message": "Venue schedules belong in venues.json"}

    if not event.get("id") or not event.get("startDate"):
        return {"status": "rejected", "message": "event missing id or startDate"}

    if not force:
        if blocked_ids is None:
            blocked_ids = {b["id"] for b in load_blocked()}
        if blocked_keys is None:
            blocked_keys = _blocked_keys(load_blocked())
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

    if not skip_latin_check and not _is_latin_relevant(event):
        # Not a Latin-dance event — drop it, don't record it. General calendars
        # are full of unrelated events; keeping them in a review queue was busywork
        # (a keyword scan decides this fine, no LLM needed). A human can still
        # rescue a genuine false negative by adding it with force=True, which
        # lands it in active — and the check below then lets re-scrapes merge it
        # instead of dropping it forever.
        already_approved = (
            any(e.get("id") == event["id"] for e in load_active())
            or any(e.get("id") == event["id"] for e in load_archive())
        )
        if not already_approved:
            reason = "not Latin dance relevant (styles=['other'], no Latin terms)"
            _append_changelog("drop_non_latin", event["id"], reason)
            return {"status": "dropped_non_latin", "message": reason}

    _infer_location(event)

    active = load_active()
    archive = load_archive()

    archive_match = find_duplicate_in(event, archive)
    if archive_match is not None and not force:
        archive_idx, conf = archive_match
        if conf == "certain":
            # Only pull an event back out of the archive when the incoming
            # copy is actually upcoming. Stale scraped files re-listing past
            # dates must not ping-pong events between archive and active
            # (reactivate here, re-archive in archive_past_events) every run.
            last_date_str = event.get("startDate", "")
            if event.get("recurrences"):
                last_date_str = event["recurrences"][-1]
            incoming_dt = parse_date(last_date_str)
            if incoming_dt is not None and incoming_dt.tzinfo is None:
                incoming_dt = incoming_dt.replace(tzinfo=NY_TZ)
            if incoming_dt is None or incoming_dt < datetime.now(timezone.utc) - timedelta(hours=24):
                return {
                    "status": "duplicate",
                    "confidence": conf,
                    "message": "already archived; incoming copy is not upcoming",
                }
            archived = archive[archive_idx]
            reason = _dedup_reason(archived, event, conf)
            _log_dedup("reactivate", archived, event, conf, reason)
            merged = merge_event(archived, event)
            _enrich_event(merged)
            merged["reactivatedAt"] = datetime.now(timezone.utc).isoformat()
            archive.pop(archive_idx)
            save_archive(archive)
            active.append(merged)
            save_active(active)
            _clear_stale_rejected(merged["id"])
            _append_changelog("reactivate", merged["id"], "from archive (certain)")
            return {"status": "reactivated", "confidence": conf, "event": merged}

    active_match = find_duplicate_in(event, active)
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
        new_last = event["recurrences"][-1] if event.get("recurrences") else event.get("startDate", "")
        new_dt = parse_date(new_last)
        if new_dt is not None:
            if new_dt.tzinfo is None:
                new_dt = new_dt.replace(tzinfo=NY_TZ)
            if new_dt < datetime.now(timezone.utc) - timedelta(hours=24):
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


def archive_past_events() -> list[dict]:
    """Move past events from active to archive. Returns archived events."""
    active = load_active()
    archive = load_archive()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    still_active = []
    newly_archived = []

    for ev in active:
        # For recurring events, check last recurrence
        last_date_str = ev.get("startDate", "")
        if ev.get("recurrences"):
            last_date_str = ev["recurrences"][-1]

        dt = parse_date(last_date_str)
        if dt is None:
            still_active.append(ev)
            continue

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)

        if dt < cutoff:
            ev["archivedAt"] = now.isoformat()
            newly_archived.append(ev)
            _append_changelog("archive", ev["id"])
        else:
            still_active.append(ev)

    if newly_archived:
        archive.extend(newly_archived)
        save_archive(archive)
        save_active(still_active)

    return newly_archived


def approve_pending(event_id: str, force: bool = False) -> dict:
    """Approve a pending event, moving it to active.

    For a dedup pair (``_dedup_candidate_of`` set), approving *merges* the two
    and persists a permanent ``verdict:"same"`` so future occurrences auto-merge
    with no review. Because that is silent and compounding, this refuses to merge
    across a special-edition boundary unless ``force=True``.
    """
    pending = load_pending()
    idx = None
    for i, ev in enumerate(pending):
        if ev["id"] == event_id:
            idx = i
            break

    if idx is None:
        return {"status": "not_found", "message": f"No pending event with id '{event_id}'"}

    event = pending[idx]
    candidate_id = event.get("_dedup_candidate_of")

    if candidate_id and not force:
        candidate = next((e for e in load_active() if e.get("id") == candidate_id), None)
        if candidate is None:
            candidate = next((e for e in load_archive() if e.get("id") == candidate_id), None)
        if candidate is not None and _special_edition_mismatch(event, candidate):
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
                "new_event": {"id": event["id"], "name": event.get("name", "")},
                "existing_event": {"id": candidate["id"], "name": candidate.get("name", "")},
            }

    pending.pop(idx)
    save_pending(pending)

    if candidate_id:
        _persist_known_duplicate(event_id, candidate_id, "same")

    for key in ("_dedup_candidate_of", "_dedup_confidence", "_dedup_reason",
                "_quarantined_new", "_quarantined_at"):
        event.pop(key, None)

    issues = validate_event(event)
    result = add_event(event, force=True)
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


def remove_active_event(event_id: str, reason: str = "removed from active", block: bool = False, block_category: str = "other") -> dict:
    """Remove an active event.

    If block=True, moves to blocked.json (permanent, prevents re-scraping).
    If block=False, moves to rejected.json for review (as before).
    """
    active = load_active()
    idx = next((i for i, ev in enumerate(active) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No active event with id '{event_id}'"}

    event = active.pop(idx)
    save_active(active)

    if block:
        return _add_to_blocked(event, block_category, reason)

    queued = _queue_rejected(event, reason)
    _append_changelog("remove", event_id, reason)
    return {"status": "removed", "event": queued}


def approve_rejected(event_id: str) -> dict:
    """Promote a rejected event to active (bypasses Latin relevance check)."""
    rejected = load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No rejected event with id '{event_id}'"}

    event = rejected.pop(idx)
    save_rejected(rejected)
    for key in ("_rejected_at", "_rejected_reason", "_review_type"):
        event.pop(key, None)

    result = add_event(event, force=True, skip_latin_check=True)
    _append_changelog("approve_rejected", event_id, "promoted from rejected queue")
    return result


def dismiss_rejected(event_id: str, reason: str = "", block: bool = False, block_category: str = "other") -> dict:
    """Dismiss a rejected event.

    If block=True, moves to blocked.json (permanent, prevents re-scraping).
    If block=False, just removes from rejected (for one-off events that won't reappear).
    """
    rejected = load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No rejected event with id '{event_id}'"}

    event = rejected.pop(idx)
    save_rejected(rejected)

    if block:
        return _add_to_blocked(event, block_category, reason)

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


def block_event(event_id: str, category: str, notes: str = "") -> dict:
    """Block an event permanently. Removes from active or rejected and adds to blocked.json.

    Categories: defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other
    """
    if category not in VALID_BLOCK_CATEGORIES:
        return {"status": "error", "message": f"Invalid category '{category}'. Use one of: {VALID_BLOCK_CATEGORIES}"}

    event = None

    active = load_active()
    idx = next((i for i, ev in enumerate(active) if ev["id"] == event_id), None)
    if idx is not None:
        event = active.pop(idx)
        save_active(active)

    rejected = load_rejected()
    r_idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if r_idx is not None:
        r_event = rejected.pop(r_idx)
        save_rejected(rejected)
        if event is None:
            event = r_event

    pending = load_pending()
    p_idx = next((i for i, ev in enumerate(pending) if ev["id"] == event_id), None)
    if p_idx is not None:
        p_event = pending.pop(p_idx)
        save_pending(pending)
        if event is None:
            event = p_event

    if event is None:
        archive = load_archive()
        a_idx = next((i for i, ev in enumerate(archive) if ev["id"] == event_id), None)
        if a_idx is not None:
            event = archive.pop(a_idx)
            save_archive(archive)

    if event is None:
        return {"status": "not_found", "message": f"Event '{event_id}' not found in active, rejected, pending, or archive."}

    return _add_to_blocked(event, category, notes)


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


def reject_pending(event_id: str, reason: str = "") -> dict:
    """Reject a pending event."""
    pending = load_pending()
    idx = None
    for i, ev in enumerate(pending):
        if ev["id"] == event_id:
            idx = i
            break

    if idx is None:
        return {"status": "not_found", "message": f"No pending event with id '{event_id}'"}

    event = pending.pop(idx)
    save_pending(pending)

    candidate_id = event.pop("_dedup_candidate_of", None)
    event.pop("_dedup_confidence", None)
    event.pop("_dedup_reason", None)
    event.pop("_quarantined_new", None)
    event.pop("_quarantined_at", None)
    if candidate_id:
        _persist_known_duplicate(event_id, candidate_id, "different")

    _append_changelog("reject", event_id, reason)
    return {"status": "rejected", "event": event, "reason": reason}


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

    for k, v in updates.items():
        if k != "id":
            active[idx][k] = v

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
    sources_path = ROOT / "data" / "sources.json"
    if not sources_path.exists():
        return {}
    with open(sources_path) as f:
        sources = json.load(f)
    return {s["id"]: s["name"] for s in sources if "id" in s and "name" in s}


def _strip_internal_fields(ev: dict, source_names: dict[str, str]) -> None:
    """Add slug/organizer, remove internal fields from an event dict."""
    ev["slug"] = slugify(ev["name"], ev["id"])
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


def publish() -> dict:
    """Generate events-published.json from active + archived events + expanded venues."""
    source_names = _load_source_names()

    active = load_active()
    venue_events = expand_venues()

    active, suppressed_venue_ids = _suppress_venue_covered_events(venue_events, active)
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
    deduped = deduplicate(all_events)
    deduped = collapse_recurring_series(deduped)

    # Sort by start date
    deduped.sort(key=lambda e: e.get("startDate", ""))

    # Re-geocode any events still missing coordinates
    for ev in deduped:
        if ev.get("lat") is None or ev.get("lng") is None:
            _enrich_event(ev)

    # Strip internal fields from active events
    for ev in deduped:
        _strip_internal_fields(ev, source_names)

    # Include archived events so their pages persist for SEO
    archive = load_archive()
    archived_out = []
    for ev in archive:
        if ev.get("lat") is None or ev.get("lng") is None:
            _enrich_event(ev)
        _strip_internal_fields(ev, source_names)
        ev["archived"] = True
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
        if rec.get("lat") is None or rec.get("lng") is None:
            _enrich_event(rec)
        _strip_internal_fields(rec, source_names)
        searchonly_out.append(rec)
    if searchonly_out:
        names = ", ".join(repr(e.get("name", "?")) for e in searchonly_out)
        print(f"  ℹ️  {len(searchonly_out)} irregular venue(s) published as search-only records: {names}")

    published = deduped + archived_out + searchonly_out

    # Loudly surface anything shipping without coordinates — those events never
    # render a pin on the map, so they're effectively invisible to visitors.
    missing = [ev for ev in deduped if ev.get("lat") is None or ev.get("lng") is None]
    if missing:
        print(f"  ⚠️  {len(missing)} active event(s) have no coordinates (won't appear on map):")
        for ev in missing:
            print(f"       - {ev.get('name', '?')!r}  ({ev.get('location') or 'no location'})")

    _write_json(PUBLIC_EVENTS_JSON, published)
    # Legacy path for scripts still referencing public/events.json
    _write_json(ROOT / "public" / "events.json", published)
    return {
        "status": "published",
        "count": len(deduped),
        "archived_count": len(archived_out),
        "search_only_count": len(searchonly_out),
        "path": str(PUBLIC_EVENTS_JSON),
    }


# Refuse to ship a published file whose live-event count collapsed relative to a
# baseline — a broken scrape or an over-zealous review pass must never wipe the
# map. Shared by the deterministic pipeline and the agent's own publish.
TRIPWIRE_MIN_PREVIOUS = 20
TRIPWIRE_MIN_RATIO = 0.7

_LEGACY_PUBLIC_JSON = ROOT / "public" / "events.json"


def _live_event_count(text: Optional[str]) -> int:
    if not text:
        return 0
    try:
        return sum(1 for e in json.loads(text) if not e.get("archived"))
    except (json.JSONDecodeError, AttributeError, TypeError):
        return 0


def publish_guarded(previous_snapshot: Optional[str] = None) -> dict:
    """publish(), but restore the previous published files and report
    ``tripped: True`` if the live-event count collapses below
    ``TRIPWIRE_MIN_RATIO`` of the baseline.

    Baseline defaults to the current published file — the right reference for
    the agent's own publish, which runs after the deterministic refresh already
    published. Callers holding an earlier baseline (run_pipeline, which snapshots
    before scrape/ingest/archive) pass it in explicitly.
    """
    if previous_snapshot is None:
        previous_snapshot = (
            PUBLIC_EVENTS_JSON.read_text() if PUBLIC_EVENTS_JSON.exists() else None
        )
    previous_live = _live_event_count(previous_snapshot)

    result = publish()

    new_live = _live_event_count(PUBLIC_EVENTS_JSON.read_text())
    tripped = (
        previous_live >= TRIPWIRE_MIN_PREVIOUS
        and new_live < previous_live * TRIPWIRE_MIN_RATIO
    )
    if tripped and previous_snapshot is not None:
        PUBLIC_EVENTS_JSON.write_text(previous_snapshot)
        _LEGACY_PUBLIC_JSON.write_text(previous_snapshot)

    result["tripped"] = tripped
    result["previous_live_events"] = previous_live
    result["published_live_events"] = new_live
    if tripped:
        result["status"] = "tripwire"
        result["message"] = (
            f"live events fell {previous_live} → {new_live} "
            f"(below {int(TRIPWIRE_MIN_RATIO * 100)}% of baseline); published files "
            "restored to the pre-publish snapshot — do NOT commit. Investigate first."
        )
    return result


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
    dropped_non_latin = 0
    rejected_out_of_area = 0
    blocked = 0
    pending_review = 0
    quarantined_new = 0
    review_items: list[dict] = []

    _blocked = load_blocked()
    blocked_ids = {b["id"] for b in _blocked}
    blocked_keys = _blocked_keys(_blocked)

    # Sources ranked "noisy" (see data/sources.json + source_signal.py) always
    # route brand-new finds to the pending queue for review, even when the run
    # otherwise publishes directly -- their raw feeds are mostly non-dance.
    try:
        from source_signal import noisy_source_ids
        noisy_sources = noisy_source_ids()
    except Exception:
        noisy_sources = set()

    for path in files:
        if not path.exists():
            continue
        try:
            events = json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            continue

        for ev in events:
            if not ev.get("id"):
                continue
            eff_quarantine = quarantine_new or (ev.get("source") in noisy_sources)
            result = add_event(ev, blocked_ids=blocked_ids, blocked_keys=blocked_keys,
                               quarantine_new=eff_quarantine)
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
            elif status == "dropped_non_latin":
                dropped_non_latin += 1
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
        "dropped_non_latin": dropped_non_latin,
        "rejected_out_of_area": rejected_out_of_area,
        "blocked": blocked,
        "pending_review": pending_review,
        "quarantined_new": quarantined_new,
        "files_processed": len(files),
    }
    if review_items:
        result["review_items"] = review_items
    return result

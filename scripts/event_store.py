"""
Event store: the canonical lifecycle layer for boston-latin-dance events.

Manages three JSON files:
  data/events/active.json   – current/upcoming events (published to map)
  data/events/archive.json  – past events (for dedup + history)
  data/events/pending.json  – unreviewed user submissions

Also reads:
  data/venues.json          – permanent weekly venue schedules

Provides:
  - CRUD operations with dedup, geocode, validation
  - Archive lifecycle (active -> archive when past)
  - Reactivation (archive -> active when event recurs)
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
from scraper_utils import ROOT, SCRAPED_DIR, VENUE_COORDS, clean_location, geocode, detect_styles, extract_cost, _eventbrite_address, _normalize
from recurrence_utils import recurrence_label

# ── Paths ─────────────────────────────────────────────────────────────

EVENTS_DIR = ROOT / "data" / "events"
ACTIVE_JSON = EVENTS_DIR / "active.json"
ARCHIVE_JSON = EVENTS_DIR / "archive.json"
PENDING_JSON = EVENTS_DIR / "pending.json"
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
        date_a = date_a.replace(tzinfo=timezone.utc)
    if date_b.tzinfo is None:
        date_b = date_b.replace(tzinfo=timezone.utc)
    return date_a.astimezone(NY_TZ).date() == date_b.astimezone(NY_TZ).date()


def _dates_within(a: dict, b: dict, hours: float) -> Optional[bool]:
    """True if dates within range, False if not, None if dates unparseable."""
    date_a = parse_date(a.get("startDate", ""))
    date_b = parse_date(b.get("startDate", ""))
    if not date_a or not date_b:
        return None
    if date_a.tzinfo is None:
        date_a = date_a.replace(tzinfo=timezone.utc)
    if date_b.tzinfo is None:
        date_b = date_b.replace(tzinfo=timezone.utc)
    date_a = date_a.astimezone(NY_TZ)
    date_b = date_b.astimezone(NY_TZ)
    return abs((date_a - date_b).total_seconds()) < hours * 3600


def _url_match(a: dict, b: dict) -> bool:
    """Check if both events link to the same URL (strong identity signal)."""
    url_a = (a.get("url") or "").rstrip("/").lower()
    url_b = (b.get("url") or "").rstrip("/").lower()
    return bool(url_a) and url_a == url_b


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
        overlap = words_a & words_b
        smaller = min(len(words_a), len(words_b))
        if smaller > 0 and len(overlap) >= max(2, smaller * 0.5):
            word_overlap_strong = True

    # "certain" tier: multiple strong signals converge — these are always the
    # same event from different sources (e.g. Eventbrite + calendar listing)
    if same_day is True and same_loc and (names_exact or names_substring or word_overlap_strong):
        return "certain"

    if names_exact and same_loc and within_24h is True:
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

    # Re-scrape of the same event id: refresh date/time from incoming data.
    if winner.get("id") == loser.get("id"):
        for key in ("startDate", "endDate", "dayOfWeek"):
            if loser.get(key):
                merged[key] = loser[key]

    return merged


def find_duplicate_in(event: dict, pool: list[dict]) -> Optional[tuple[int, str]]:
    """Return (index, confidence) of best duplicate in pool, or None."""
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
    """Deduplicate for publish. Merges certain matches always; merges review
    matches only when locations also match (avoids false-merging different
    events that just share keywords and are within 24h)."""
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
            elif conf == "review" and _locations_same(result[idx], ev):
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


def _names_are_same_series(a: str, b: str) -> bool:
    if a == b:
        return True
    if a in b or b in a:
        return True
    words_a = set(a.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
    words_b = set(b.split()) - {"the", "at", "in", "and", "of", "by", "a", "an"}
    if not words_a or not words_b:
        return False
    overlap = words_a & words_b
    smaller = min(len(words_a), len(words_b))
    return len(overlap) >= max(2, smaller * 0.6)


def _event_day_of_week(event: dict) -> Optional[str]:
    """Return dayOfWeek from the field or infer from startDate."""
    dow = event.get("dayOfWeek")
    if dow:
        return dow
    dt = parse_date(event.get("startDate", ""))
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return DAYS_LIST[dt.astimezone(NY_TZ).isoweekday() % 7]


def _venue_schedule_covers_day(venue_event: dict, day: str) -> bool:
    schedule = venue_event.get("schedule") or []
    return any(entry.get("dayOfWeek") == day for entry in schedule)


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
    """Drop scraped events whose night is already on a venue hub schedule at the same location."""
    venue_hubs = [v for v in venue_events if _is_venue_schedule_record(v)]
    if not venue_hubs:
        return active_events

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

        suppressed = False
        for hub in venue_hubs:
            if not _venue_schedule_covers_day(hub, day):
                continue
            if _scraped_at_venue_hub(hub, ev):
                suppressed = True
                break

        if not suppressed:
            kept.append(ev)

    return kept


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
        dates: list[str] = sorted({ev["startDate"] for ev in group_events if ev.get("startDate")})
        if not dates:
            result.extend(group_events)
            continue

        now = datetime.now().astimezone()
        future_dates = [d for d in dates if parse_date(d) and parse_date(d) >= now]
        if future_dates:
            best["startDate"] = future_dates[0]
            for ev in group_events:
                if ev["startDate"] == future_dates[0]:
                    best["endDate"] = ev.get("endDate", ev["startDate"])
                    break
        else:
            best["startDate"] = dates[-1]

        best["recurring"] = True
        best["recurrences"] = dates

        dt = parse_date(best["startDate"])
        if dt:
            days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
            best["dayOfWeek"] = days[dt.isoweekday() % 7]

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
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_window = today + timedelta(weeks=weeks_ahead)
    events: list[dict] = []
    est = timezone(timedelta(hours=-4))

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

        recurrences = [dt.replace(tzinfo=est).isoformat() for dt in all_dates]
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


def save_active(events: list[dict]) -> None:
    _write_json(ACTIVE_JSON, events)


def save_archive(events: list[dict]) -> None:
    _write_json(ARCHIVE_JSON, events)


def save_pending(events: list[dict]) -> None:
    _write_json(PENDING_JSON, events)


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


def add_event(event: dict, force: bool = False) -> dict:
    """Add an event to the active store. Returns result dict with status.

    Dedup tiers:
      certain -> auto-merge (same ID or URL)
      review  -> route to pending.json for review (unless force=True)
    """
    if _is_venue_schedule_record(event):
        return {"status": "rejected", "message": "Venue schedules belong in venues.json"}

    if not event.get("id") or not event.get("startDate"):
        return {"status": "rejected", "message": "event missing id or startDate"}

    _infer_location(event)

    active = load_active()
    archive = load_archive()

    archive_match = find_duplicate_in(event, archive)
    if archive_match is not None and not force:
        archive_idx, conf = archive_match
        if conf == "certain":
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

    _enrich_event(event)
    active.append(event)
    save_active(active)
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
            dt = dt.replace(tzinfo=timezone.utc)

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


def approve_pending(event_id: str) -> dict:
    """Approve a pending event, moving it to active."""
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

    candidate_id = event.get("_dedup_candidate_of")
    if candidate_id:
        _persist_known_duplicate(event_id, candidate_id, "same")

    for key in ("_dedup_candidate_of", "_dedup_confidence", "_dedup_reason"):
        event.pop(key, None)

    issues = validate_event(event)
    result = add_event(event, force=True)
    if issues:
        result["warnings"] = issues
    _append_changelog("approve", event_id)
    return result


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
    if ev.get("recurring"):
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

    active = _suppress_venue_covered_events(venue_events, active)
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

    published = deduped + archived_out

    _write_json(PUBLIC_EVENTS_JSON, published)
    # Legacy path for scripts still referencing public/events.json
    _write_json(ROOT / "public" / "events.json", published)
    return {
        "status": "published",
        "count": len(deduped),
        "archived_count": len(archived_out),
        "path": str(PUBLIC_EVENTS_JSON),
    }


def ingest_scraped(source_id: Optional[str] = None) -> dict:
    """Ingest events from data/scraped/ into the active store.

    Handles dedup against both active and archive (reactivation).
    Review-tier duplicates are routed to pending.json for review.
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
    pending_review = 0
    review_items: list[dict] = []

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
            result = add_event(ev)
            status = result["status"]
            if status == "added":
                added += 1
            elif status == "merged":
                merged += 1
            elif status == "reactivated":
                reactivated += 1
            elif status == "duplicate":
                skipped += 1
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
        "pending_review": pending_review,
        "files_processed": len(files),
    }
    if review_items:
        result["review_items"] = review_items
    return result

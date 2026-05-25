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
import fcntl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unicodedata import normalize as unicode_normalize

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scraper_utils import ROOT, SCRAPED_DIR, geocode, detect_styles, extract_cost

# ── Paths ─────────────────────────────────────────────────────────────

EVENTS_DIR = ROOT / "data" / "events"
ACTIVE_JSON = EVENTS_DIR / "active.json"
ARCHIVE_JSON = EVENTS_DIR / "archive.json"
PENDING_JSON = EVENTS_DIR / "pending.json"
CHANGELOG = EVENTS_DIR / "changelog.jsonl"
VENUES_JSON = ROOT / "data" / "venues.json"
PUBLIC_EVENTS_JSON = ROOT / "public" / "events.json"

DEDUP_LOG = EVENTS_DIR / "dedup-log.jsonl"

EVENTS_DIR.mkdir(parents=True, exist_ok=True)

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
    return False


def _dates_within(a: dict, b: dict, hours: float) -> Optional[bool]:
    """True if dates within range, False if not, None if dates unparseable."""
    date_a = parse_date(a.get("startDate", ""))
    date_b = parse_date(b.get("startDate", ""))
    if not date_a or not date_b:
        return None
    return abs((date_a - date_b).total_seconds()) < hours * 3600


def _url_match(a: dict, b: dict) -> bool:
    """Check if both events link to the same URL (strong identity signal)."""
    url_a = (a.get("url") or "").rstrip("/").lower()
    url_b = (b.get("url") or "").rstrip("/").lower()
    return bool(url_a) and url_a == url_b


# ── Tiered dedup confidence ──────────────────────────────────────────
#
# Returns one of:
#   "certain"   – auto-merge, no review needed
#   "likely"    – auto-merge, log for audit
#   "uncertain" – route to pending for human/agent review
#   None        – not a duplicate

def dedup_confidence(a: dict, b: dict) -> Optional[str]:
    """Determine dedup confidence between two events.

    Tier 1 – CERTAIN (auto-merge silently):
      - Same ID
      - Same URL
      - Exact normalized name + same date (within 4 hours)

    Tier 2 – LIKELY (auto-merge, log):
      - Exact normalized name + same date (within 24 hours)
      - Substring name match + same location (alias/coords) + within 24h
      - Normalized name match + same location + within 24h

    Tier 3 – UNCERTAIN (route to pending for review):
      - Substring name match + within 24h but different/unknown locations
      - High word overlap (>=50%) + same location + within 24h
      - Same name + no parseable dates

    Returns None if no match detected.
    """
    # Venue schedule hubs are distinct map entries from scraped night-specific series.
    if _is_venue_schedule_record(a) != _is_venue_schedule_record(b):
        return None

    name_a = normalize_name(a["name"])
    name_b = normalize_name(b["name"])
    if not name_a or not name_b:
        return None

    # ── Tier 1: CERTAIN ──
    if a["id"] == b["id"]:
        return "certain"

    if _url_match(a, b):
        return "certain"

    names_exact = (name_a == name_b)
    names_substring = (name_a in name_b or name_b in name_a) and not names_exact
    within_4h = _dates_within(a, b, 4)
    within_24h = _dates_within(a, b, 24)

    if names_exact and within_4h is True:
        return "certain"

    # ── Tier 2: LIKELY ──
    same_loc = _locations_same(a, b)

    if names_exact and within_24h is True:
        return "likely"

    if names_substring and same_loc and within_24h is True:
        return "likely"

    # ── Tier 3: UNCERTAIN ──
    if names_substring and within_24h is True:
        return "uncertain"

    words_a = _content_words(name_a)
    words_b = _content_words(name_b)
    if words_a and words_b:
        overlap = words_a & words_b
        smaller = min(len(words_a), len(words_b))
        if smaller > 0 and len(overlap) >= max(2, smaller * 0.5):
            if same_loc and within_24h is True:
                return "uncertain"

    if names_exact and within_24h is None:
        return "uncertain"

    return None


def _dedup_reason(a: dict, b: dict, confidence: str) -> str:
    """Build a human-readable reason string for the audit log."""
    parts = []
    name_a = normalize_name(a["name"])
    name_b = normalize_name(b["name"])

    if a["id"] == b["id"]:
        parts.append("same_id")
    elif _url_match(a, b):
        parts.append("same_url")
    elif name_a == name_b:
        parts.append("exact_name")
    elif name_a in name_b or name_b in name_a:
        parts.append("substring_name")
    else:
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

    return merged


def find_duplicate_in(event: dict, pool: list[dict]) -> Optional[tuple[int, str]]:
    """Return (index, confidence) of best duplicate in pool, or None.

    Scans for certain first, then likely, then uncertain. Returns the
    highest-confidence match found.
    """
    best_idx: Optional[int] = None
    best_conf: Optional[str] = None
    conf_rank = {"certain": 0, "likely": 1, "uncertain": 2}

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
    """Deduplicate for publish. Only merges certain+likely; uncertain kept separate."""
    events.sort(key=source_rank)
    result: list[dict] = []
    for ev in events:
        match = find_duplicate_in(ev, result)
        if match is not None:
            idx, conf = match
            if conf in ("certain", "likely"):
                reason = _dedup_reason(result[idx], ev, conf)
                _log_dedup("merge", result[idx], ev, conf, reason)
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

        for j, ev_j in enumerate(events):
            if j in assigned:
                continue
            name_j = normalize_name(ev_j["name"])
            loc_j = _location_key(ev_j.get("location", ""))

            # Venue schedule hubs (e.g. Havana Club) share a location/name with
            # scraped night-specific series but are distinct map entries.
            if _is_venue_schedule_record(ev_i) != _is_venue_schedule_record(ev_j):
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
        dates: list[str] = sorted({ev["startDate"] for ev in group_events})

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


def _enrich_event(event: dict) -> None:
    """Geocode, detect styles, extract cost if missing. Mutates in place."""
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
      certain  -> auto-merge silently
      likely   -> auto-merge, logged to dedup-log.jsonl
      uncertain -> route to pending.json for review (unless force=True)
    """
    active = load_active()
    archive = load_archive()

    # Check archive for reactivation
    archive_match = find_duplicate_in(event, archive)
    if archive_match is not None and not force:
        archive_idx, conf = archive_match
        if conf in ("certain", "likely"):
            archived = archive[archive_idx]
            reason = _dedup_reason(archived, event, conf)
            _log_dedup("reactivate", archived, event, conf, reason)
            merged = merge_event(archived, event)
            merged["reactivatedAt"] = datetime.now(timezone.utc).isoformat()
            archive.pop(archive_idx)
            save_archive(archive)
            active.append(merged)
            save_active(active)
            _append_changelog("reactivate", merged["id"], f"from archive ({conf})")
            return {"status": "reactivated", "confidence": conf, "event": merged}

    # Check active for duplicate
    active_match = find_duplicate_in(event, active)
    if active_match is not None:
        active_idx, conf = active_match
        existing = active[active_idx]
        reason = _dedup_reason(existing, event, conf)

        if conf == "certain":
            _log_dedup("skip_certain", existing, event, conf, reason)
            active[active_idx] = merge_event(existing, event)
            save_active(active)
            return {"status": "duplicate", "confidence": conf, "existing": active[active_idx]}

        if conf == "likely":
            _log_dedup("auto_merge", existing, event, conf, reason)
            active[active_idx] = merge_event(existing, event)
            save_active(active)
            _append_changelog("merge", event["id"], f"likely duplicate of {existing['id']}")
            return {"status": "merged", "confidence": conf, "event": active[active_idx]}

        if conf == "uncertain":
            if force:
                _log_dedup("force_merge", existing, event, conf, reason)
                active[active_idx] = merge_event(existing, event)
                save_active(active)
                _append_changelog("merge", event["id"], f"force-merged uncertain dup of {existing['id']}")
                return {"status": "merged", "confidence": conf, "event": active[active_idx]}

            _log_dedup("pending_review", existing, event, conf, reason)
            event["_dedup_candidate_of"] = existing["id"]
            event["_dedup_confidence"] = conf
            event["_dedup_reason"] = reason
            pending = load_pending()
            pending.append(event)
            save_pending(pending)
            _append_changelog("pending_review", event["id"], f"uncertain dup of {existing['id']}: {reason}")
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

    issues = validate_event(event)
    result = add_event(event)
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


def publish() -> dict:
    """Generate public/events.json from active events + expanded venues."""
    active = load_active()
    venue_events = expand_venues()

    all_events = venue_events + active
    deduped = deduplicate(all_events)
    deduped = collapse_recurring_series(deduped)

    # Sort by start date
    deduped.sort(key=lambda e: e.get("startDate", ""))

    # Add slugs, strip internal fields
    for ev in deduped:
        ev["slug"] = slugify(ev["name"], ev["id"])
        ev.pop("source", None)
        ev.pop("archivedAt", None)
        ev.pop("reactivatedAt", None)
        for key in list(ev.keys()):
            if key.startswith("_"):
                ev.pop(key)

    _write_json(PUBLIC_EVENTS_JSON, deduped)
    return {
        "status": "published",
        "count": len(deduped),
        "path": str(PUBLIC_EVENTS_JSON),
    }


def ingest_scraped(source_id: Optional[str] = None) -> dict:
    """Ingest events from data/scraped/ into the active store.

    Handles dedup against both active and archive (reactivation).
    Uncertain duplicates are routed to pending.json for review.
    """
    if source_id:
        files = [SCRAPED_DIR / f"{source_id}.json"]
    else:
        files = sorted(SCRAPED_DIR.glob("*.json"))

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

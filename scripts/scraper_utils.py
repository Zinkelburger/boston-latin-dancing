"""
Shared helpers for all event source scrapers.

Single source of truth for: style detection, cost extraction, venue
coordinates, geocoding (Nominatim + venue lookup + cache), and output writing.
All scrapers import from here so the DanceEvent schema stays consistent.
"""

import html
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCRAPED_DIR = DATA_DIR / "scraped"
GEOCODE_CACHE_PATH = DATA_DIR / "geocode-cache.json"

SCRAPED_DIR.mkdir(parents=True, exist_ok=True)

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
NY_TZ = ZoneInfo("America/New_York")

VALID_STYLES = {"bachata", "salsa", "kizomba", "zouk", "merengue", "other"}

STYLE_PATTERNS = [
    ("bachata", re.compile(r"bachata", re.I)),
    ("salsa", re.compile(r"salsa|timba", re.I)),
    ("kizomba", re.compile(r"kizomba", re.I)),
    ("zouk", re.compile(r"zouk", re.I)),
    ("merengue", re.compile(r"merengue", re.I)),
]

VENUE_COORDS = {
    "lili latin dance": (42.336527, -71.047731),
    "tambó salsa": (42.365000, -71.091000),
    "tambo salsa": (42.365000, -71.091000),
    "salsa y control dance studio": (42.352700, -71.131800),
    "salsa y control": (42.352700, -71.131800),
    "arts at the armory": (42.399600, -71.098900),
    "magazine beach, cambridge": (42.358400, -71.114700),
    "magazine beach": (42.358400, -71.114700),
    "magazine beach park": (42.358400, -71.114700),
    "the nature center @ magazine beach park": (42.358400, -71.114700),
    "the dante alighieri society of massachusetts": (42.367900, -71.088500),
    "dante alighieri society": (42.367900, -71.088500),
    "cantab lounge": (42.365300, -71.103100),
    "lou's": (42.3736, -71.1212),
    "lous": (42.3736, -71.1212),
    "13 brattle st": (42.3736, -71.1212),
    "13 brattle street": (42.3736, -71.1212),
    "pkl": (42.335200, -71.046400),
    "distillery gallery": (42.340000, -71.055000),
    "club cafe boston": (42.345300, -71.072100),
    "docks near the hatch memorial shell": (42.357256, -71.073702),
    "hatch shell on the esplanade": (42.357256, -71.073702),
    "hatch memorial shell": (42.357256, -71.073702),
    "the anchor": (42.3731772, -71.0526574),
    "1 shipyard park": (42.3731772, -71.0526574),
    "shipyard park": (42.3731772, -71.0526574),
    # Venues discovered from geocoding failures
    "havana club": (42.3646071, -71.1043523),
    "288 green st": (42.3646071, -71.1043523),
    "j&l dance studio": (42.4271, -71.0662),
    "j&l dance": (42.4271, -71.0662),
    "west end johnnies": (42.3632, -71.0618),
    "west end johnnie's": (42.3632, -71.0618),
    "bachata room boston": (42.3636, -71.1010),
    "bachata room": (42.3636, -71.1010),
    "la fabrica central": (42.3636, -71.1010),
    "provenza lounge": (42.4668, -70.9495),
    "juliet": (42.3804, -71.0993),
    "el barco": (42.3474, -71.0812),
    "blackstone community center": (42.3406, -71.0714),
    "dewey square park": (42.3527443, -71.0553933),
    "dewey square": (42.3527443, -71.0553933),
    "60 rowes wharf": (42.3562, -71.0502),
    "cambridge common": (42.3763, -71.1217),
    "10 waterside ave": (42.3485, -71.0440),
    "kendall/mit open space": (42.3625, -71.0862),
    "292 main street, cambridge": (42.3625, -71.0862),
    "moves & vibes dance and entertainment co": (42.3697, -71.0816),
    "moves & vibes dance": (42.3697, -71.0816),
    "shore leave": (42.3451, -71.0672),
    "mango studio rental": (42.3633, -71.1006),
    "112 bishop allen drive": (42.3633, -71.1006),
    "sunset cantina": (42.3508, -71.1165),
    "916 commonwealth avenue": (42.3508, -71.1165),
    "multicultural arts center": (42.3699327, -71.0796219),
    "long live roxbury": (42.3277529, -71.0748973),
    "152 hampden st": (42.3277529, -71.0748973),
    "faces brewing co": (42.4265326, -71.0686213),
    "faces brewing co.": (42.4265326, -71.0686213),
    "marina bay ferry": (42.299906, -71.0312343),
    "marina bay quincy": (42.2985665, -71.0293661),
    "552 victory road": (42.299906, -71.0312343),
    "the event factory kitchen & stage": (41.7246794, -71.456138),
    "event factory kitchen": (41.7246794, -71.456138),
    "providence rink": (41.8249423, -71.4115681),
    "isles of shoals steamship company": (43.0799477, -70.7595803),
    "dancing fools": (42.393693, -71.119445),
    "351 summer st": (42.393693, -71.119445),
    "morse library": (42.2846114, -71.345821),
    "14 e central st": (42.2846114, -71.345821),
    "sol de mexico": (42.1530123, -71.4913262),
    "350 e main st": (42.1530123, -71.4913262),
    "rumba y timbal dance company": (42.3668113, -71.104309),
    "rumba y timbal": (42.3668113, -71.104309),
    "7 temple st": (42.3668113, -71.104309),
    "luna fitness club": (42.2953653, -71.0488803),
    "east boston memorial park": (42.3713138, -71.0329399),
    "seven hills park": (42.397751, -71.124514),
    "seven hills park, somerville": (42.397751, -71.124514),
    "seven hills stage": (42.397751, -71.124514),
    "bremen street park amphitheater": (42.3757, -71.0357),
    "bremen street park": (42.3757, -71.0357),
    "carson beach": (42.3262, -71.0396),
    "carson beach, boston": (42.3262, -71.0396),
    "mother's beach": (42.3262, -71.0396),
    "franklin park": (42.3068, -71.0925),
    "franklin park playstead": (42.3068, -71.0925),
    "pierpont rd": (42.3068, -71.0925),
    "the grove at lawn on d": (42.34445, -71.04488),
    "lawn on d": (42.34445, -71.04488),
    "420 d street": (42.34445, -71.04488),
    "420 d st": (42.34445, -71.04488),
    "astela dance studio": (42.2973914, -71.2885582),
    "2 brook st, wellesley": (42.2973914, -71.2885582),
    "agave mexican grill": (42.3404499, -71.5909618),
    "agave mexican grill & cantina": (42.3404499, -71.5909618),
    "197a boston post": (42.3404499, -71.5909618),
}

BOSTON = (42.36, -71.06)
MAX_DISTANCE_KM = 50

# ── Style detection ──────────────────────────────────────────────────

def detect_styles(text: str) -> list[str]:
    found = [style for style, pat in STYLE_PATTERNS if pat.search(text)]
    return found if found else ["other"]


# ── Latin-dance keyword filter ───────────────────────────────────────
#
# The single source of truth for "does this text mention Latin social dance".
# Broader than STYLE_PATTERNS (which only names the five map styles): it also
# catches the umbrella terms and rhythm/venue words that reliably mark a Latin
# event even when the headline style isn't spelled out. Used two ways:
#   • high-noise general calendars (Somerville Arts Council, etc.) filter their
#     scrape with filter_latin_events() so unrelated events never enter the
#     pipeline in the first place — no LLM pass needed to reject a craft fair.
#   • event_store imports mentions_latin() so ingest and scrapers agree on the
#     exact same rule.
LATIN_KEYWORD_RE = re.compile(
    r"\b(salsa|bachata|kizomba|zouk|merengue|latin|cumbia|reggaeton"
    r"|timba|son(?:go)?|cha\s*cha|mambo|rumba|guaguanco|cubana?|tropical"
    r"|rueda|casino|afro[-\s]?latin|afro[-\s]?cuban|afro[-\s]?caribbean"
    r"|bossa\s*nova|bugal[uú]|dominican)\b",
    re.I,
)


def mentions_latin(text: str) -> bool:
    """True if free text mentions any Latin social-dance term."""
    return bool(LATIN_KEYWORD_RE.search(text or ""))


def is_latin_event(event: dict) -> bool:
    """True if an event dict looks like Latin social dance.

    Passes when a concrete style was detected (bachata/salsa/…), otherwise
    falls back to a keyword scan of the name + description. This is the gate
    general-calendar scrapers apply before emitting an event.
    """
    styles = [s for s in event.get("styles", []) if s and s != "other"]
    if styles:
        return True
    return mentions_latin(f"{event.get('name', '')} {event.get('description', '')}")


def filter_latin_events(events: list[dict]) -> list[dict]:
    """Keep only the Latin-relevant events from a scrape of a general calendar.

    Returns the kept list and prints how many were dropped, so a scraper over a
    noisy municipal calendar can safely emit the whole page and let this drop
    the craft fairs and blues shows without ever recording them.
    """
    kept = [e for e in events if is_latin_event(e)]
    dropped = len(events) - len(kept)
    if dropped:
        print(f"  Keyword filter: kept {len(kept)} Latin events, dropped {dropped}")
    return kept


# ── Cost extraction ──────────────────────────────────────────────────

_COST_PATTERNS = [
    re.compile(r"(?:cover|cost|admission|entry|ticket)[:\s]*\$?\s*(\$?\d+(?:\s*[-–]\s*\$?\d+)?)", re.I),
    re.compile(r"\$(\d+)\s*(?:at\s+(?:the\s+)?door|online|advance)", re.I),
    re.compile(r"(\$\d+(?:\s*[-–/]\s*\$?\d+)?)\s*(?:at\s+(?:the\s+)?door|online|advance|cover|entry)", re.I),
    re.compile(r"(?:FREE|free)\s+(?:EVENT|event)", re.I),
]

def extract_cost(text: str) -> Optional[str]:
    for pat in _COST_PATTERNS:
        m = pat.search(text)
        if m:
            if re.search(r"free", m.group(0), re.I):
                return "Free"
            return m.group(0).strip()

    dollar = re.search(r"\$\d+", text)
    if dollar:
        return dollar.group(0)

    if re.search(r"\bfree\b", text, re.I):
        return "Free"
    return None


# ── Geocoding ────────────────────────────────────────────────────────

def _load_geo_cache() -> dict:
    if GEOCODE_CACHE_PATH.exists():
        try:
            return json.loads(GEOCODE_CACHE_PATH.read_text())
        except Exception:
            pass
    return {}

def _save_geo_cache(cache: dict) -> None:
    GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2))

_geo_cache = _load_geo_cache()

# How long to trust a "not found" before re-attempting. Negative results are
# often transient (rate-limit, a venue not yet in OSM); expiring them means the
# cache self-heals on the next publish instead of staying poisoned forever.
NEG_CACHE_TTL_DAYS = 14


def _cache_coords(entry) -> Optional[tuple[float, float]]:
    """Coordinates from a cache entry, or None if it isn't a positive hit."""
    if isinstance(entry, dict) and "lat" in entry and "lng" in entry:
        return (entry["lat"], entry["lng"])
    return None


def _neg_cache_expired(entry) -> bool:
    """Whether a negative cache entry should be retried.

    Legacy bare ``null`` entries (no timestamp) always expire, so old poisoned
    entries get one fresh attempt under the improved query logic.
    """
    if not isinstance(entry, dict):
        return True
    ts = entry.get("miss")
    if not ts:
        return True
    try:
        when = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when > timedelta(days=NEG_CACHE_TTL_DAYS)


def _dist_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = lat1 - lat2
    dlng = (lng1 - lng2) * math.cos(math.radians(lat1))
    return math.sqrt(dlat * dlat + dlng * dlng) * 111

def _is_near_boston(lat: float, lng: float) -> bool:
    return _dist_km(lat, lng, *BOSTON) <= MAX_DISTANCE_KM

# Sentinel: the geocoder was reachable-but-failed (timeout, rate-limit, 5xx).
# A transient failure must never be cached as a negative result — otherwise one
# bad network moment poisons a location's coords permanently.
_GEO_TRANSIENT = object()


def _nominatim_query(query: str, retries: int = 2):
    """Geocode one query string.

    Returns ``(lat, lng)`` on success, ``None`` on a definitive no-result, and
    ``_GEO_TRANSIENT`` when the service errored (so the caller can avoid caching
    a false negative). Retries with backoff on rate-limit / server errors.
    """
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
                headers={"User-Agent": "boston-latin-dance-dev/0.1"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    return (float(data[0]["lat"]), float(data[0]["lon"]))
                return None  # reached the service, genuinely nothing found
            # 429 (rate-limited) or 5xx -> transient, back off and retry
        except Exception:
            pass  # network/timeout/JSON error -> transient
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return _GEO_TRANSIENT

def _normalize(s: str) -> str:
    return s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')

def clean_location(location: str) -> str:
    """Normalize location strings from scrapers (newlines, HTML entities, spacing)."""
    s = html.unescape(location or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\n+", ", ", s)
    s = re.sub(r",\s*,+", ", ", s)
    return s.strip()

def _lookup_venue(location: str) -> Optional[tuple[float, float]]:
    lower = _normalize(clean_location(location)).lower().strip()
    if lower in VENUE_COORDS:
        return VENUE_COORDS[lower]
    for venue, coords in VENUE_COORDS.items():
        if venue in lower:
            return coords
    return None

def _eventbrite_address(url: str) -> Optional[str]:
    """Extract streetAddress from Eventbrite JSON-LD (no API key needed)."""
    if not url or "eventbrite.com" not in url:
        return None
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "boston-latin-dance-dev/0.1"},
            timeout=10,
        )
        m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', resp.text)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return None

# Country-name comma parts that confuse freeform geocoding. Includes the
# Spanish "EE. UU." / "Estados Unidos" that Eventbrite emits for locale=es.
_COUNTRY_PART_RE = re.compile(
    r"^(?:ee\.?\s*uu\.?|e\.\s*e\.\s*u\.\s*u\.|usa|u\.s\.a?\.?|"
    r"united states(?: of america)?|estados unidos)$",
    re.I,
)

# Street-type abbreviations, so "620 Massachusetts Ave" and "620 Massachusetts
# Avenue" collapse to one fragment instead of both being queried.
_ADDR_ABBREV = [
    (re.compile(r"\bavenue\b", re.I), "ave"),
    (re.compile(r"\bstreet\b", re.I), "st"),
    (re.compile(r"\bboulevard\b", re.I), "blvd"),
    (re.compile(r"\bdrive\b", re.I), "dr"),
    (re.compile(r"\broad\b", re.I), "rd"),
]


def _norm_part(part: str) -> str:
    s = re.sub(r"\d{5}(-\d{4})?", "", part.lower())
    for pat, repl in _ADDR_ABBREV:
        s = pat.sub(repl, s)
    return re.sub(r"\s+", " ", s).strip()


def _build_query_variants(location: str) -> list[str]:
    cleaned = re.sub(r"#\w+\s*", "", clean_location(location))
    cleaned = re.sub(r",\s*FL\s+\d+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+-\s+\d+\w*\s+Floor", "", cleaned, flags=re.I)
    cleaned = cleaned.strip()

    # Split on commas; drop country names; collapse duplicate fragments.
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    parts = [p for p in parts if not _COUNTRY_PART_RE.match(p)]
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        norm = _norm_part(part)
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(part)

    def with_region(s: str) -> str:
        return s if re.search(r"\b(?:ma|boston)\b", s, re.I) else f"{s}, Boston, MA"

    variants: list[str] = []

    def add(s: str) -> None:
        s = s.strip().strip(",").strip()
        if not s:
            return
        v = with_region(s)
        if v not in variants:
            variants.append(v)

    full = ", ".join(deduped)
    add(full)

    # Street-anchored variant: start at the first part beginning with a house
    # number, dropping any leading venue/business/park name that breaks freeform
    # search ("Rumba Y Timbal Dance Company, 7 Temple St, Cambridge, MA").
    street_idx = next((i for i, p in enumerate(deduped) if re.match(r"^\d+\s", p)), None)
    if street_idx is not None and street_idx > 0:
        add(", ".join(deduped[street_idx:]))

    # Last resort: drop a leading house number entirely.
    no_number = re.sub(r"^\d+\s+", "", full)
    if no_number != full:
        add(no_number)

    return variants


def geocode(location: str) -> Optional[tuple[float, float]]:
    """Return (lat, lng) for a location string, or None."""
    location = clean_location(location)
    if not location:
        return None

    venue = _lookup_venue(location)
    if venue:
        return venue

    if len(location) < 5:
        return None

    lower = location.lower().strip()
    if lower in _geo_cache:
        entry = _geo_cache[lower]
        coords = _cache_coords(entry)
        if coords is not None:
            return coords
        if not _neg_cache_expired(entry):
            return None
        # expired / legacy negative -> fall through and retry

    had_transient = False
    for query in _build_query_variants(location):
        result = _nominatim_query(query)
        if result is _GEO_TRANSIENT:
            had_transient = True
            continue
        if result and _is_near_boston(*result):
            _geo_cache[lower] = {"lat": result[0], "lng": result[1]}
            _save_geo_cache(_geo_cache)
            return result
        if result:
            print(f"  Rejected (too far): \"{query}\" -> {result[0]}, {result[1]} ({_dist_km(*result, *BOSTON):.1f}km)")
        time.sleep(1.1)

    # Only record a negative when the service actually answered "not found" for
    # every variant. A transient failure is left uncached so we retry next time.
    if not had_transient:
        _geo_cache[lower] = {"miss": datetime.now(timezone.utc).isoformat()}
        _save_geo_cache(_geo_cache)
    return None


# ── DanceEvent builder ───────────────────────────────────────────────

def make_event(
    *,
    id: str,
    name: str,
    start: datetime,
    end: Optional[datetime] = None,
    location: str = "",
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    description: str = "",
    url: Optional[str] = None,
    styles: Optional[list[str]] = None,
    cost: Optional[str] = None,
    recurring: bool = False,
    source: str = "",
    venue_unknown: bool = False,
) -> dict:
    """Build a DanceEvent dict matching the TypeScript schema.

    ``venue_unknown`` marks a listing whose ``location`` is a region rather than
    an address — a promoted festival we know is happening somewhere in Boston,
    say. Geocoding one of those drops a pin on the region's centroid (City
    Hall, for "Boston, MA"), which reads as a real venue, so those events ship
    without coordinates and stay off the map instead.
    """
    if end is None:
        end = start

    combined = f"{name} {description}"
    if styles is None:
        styles = detect_styles(combined)
    if cost is None:
        cost = extract_cost(combined)

    if (lat is None or lng is None) and not venue_unknown:
        coords = geocode(location)
        if coords:
            lat, lng = coords

    return {
        "id": id,
        "name": name,
        "startDate": start.isoformat() if start.tzinfo else start.replace(tzinfo=NY_TZ).isoformat(),
        "endDate": end.isoformat() if end.tzinfo else end.replace(tzinfo=NY_TZ).isoformat(),
        "dayOfWeek": DAYS[start.astimezone(NY_TZ).isoweekday() % 7],
        "location": location,
        "lat": lat,
        "lng": lng,
        "description": description,
        "url": url,
        "styles": styles,
        "cost": cost,
        "recurring": recurring,
        "source": source,
        **({"venueUnknown": True} if venue_unknown else {}),
    }


def filter_future_events(events: list[dict], grace_days: int = 1) -> list[dict]:
    """Remove events whose startDate is in the past (with grace period)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    kept = []
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev["startDate"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=NY_TZ)
            if dt >= cutoff:
                kept.append(ev)
        except (ValueError, KeyError):
            kept.append(ev)
    removed = len(events) - len(kept)
    if removed:
        print(f"  Filtered {removed} past events")
    return kept


def write_scraped(source_id: str, events: list[dict]) -> Path:
    """Write scraped events to data/scraped/<source_id>.json."""
    out = SCRAPED_DIR / f"{source_id}.json"
    out.write_text(json.dumps(events, indent=2, ensure_ascii=False))
    print(f"Wrote {len(events)} events to {out}")
    return out


# ── Scraper health / silent-failure detection ────────────────────────
#
# A scraper that writes [] is ambiguous: it could mean "the page is fine, there
# just aren't any Latin events right now" (normal) or "the markup we parse is
# gone, so we'd miss events even if they existed" (needs a redesign). We tell
# them apart by the RAW count — how many events the parser pulled out *before*
# the Latin keyword filter. Raw > 0 with kept 0 is normal; raw == 0 on a page
# that reached us means our selectors matched nothing → alert.
SCRAPER_HEALTH_PATH = DATA_DIR / "scraper-health.json"


def load_scrape_health() -> dict:
    if SCRAPER_HEALTH_PATH.exists():
        try:
            return json.loads(SCRAPER_HEALTH_PATH.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def record_scrape_health(
    source_id: str,
    raw_found: int,
    kept: int,
    *,
    fetched: bool = True,
    note: str = "",
) -> str:
    """Record whether a scrape looks healthy and return the status.

    status:
      "ok"                – found the page structure (raw_found > 0)
      "structure_missing" – page reached us but our parser matched nothing;
                            the scraper likely needs a redesign (ALERT)
      "fetch_error"       – couldn't even fetch the page (transient/site down)
    """
    if not fetched:
        status = "fetch_error"
    elif raw_found == 0:
        status = "structure_missing"
        if not note:
            note = "page fetched but no events matched our parser — markup may have changed; redesign the scraper"
    else:
        status = "ok"

    health = load_scrape_health()
    health[source_id] = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "raw_found": raw_found,
        "kept": kept,
        "status": status,
        "note": note,
    }
    SCRAPER_HEALTH_PATH.write_text(json.dumps(health, indent=2, ensure_ascii=False))
    if status != "ok":
        print(f"  ⚠️  scraper health: {source_id} → {status}. {note}")
    return status


def load_sources() -> list[dict]:
    """Load data/sources.json."""
    return json.loads((DATA_DIR / "sources.json").read_text())


def get_source(source_id: str) -> Optional[dict]:
    for s in load_sources():
        if s["id"] == source_id:
            return s
    return None

"""
Shared helpers for all event source scrapers.

Single source of truth for: style detection, cost extraction, venue
coordinates, geocoding (Nominatim + venue lookup + cache), and output writing.
All scrapers import from here so the DanceEvent schema stays consistent.
"""

import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCRAPED_DIR = DATA_DIR / "scraped"
GEOCODE_CACHE_PATH = DATA_DIR / "geocode-cache.json"

SCRAPED_DIR.mkdir(parents=True, exist_ok=True)

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

VALID_STYLES = {"bachata", "salsa", "kizomba", "zouk", "merengue", "other"}

STYLE_PATTERNS = [
    ("bachata", re.compile(r"bachata", re.I)),
    ("salsa", re.compile(r"salsa", re.I)),
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
    "60 rowes wharf": (42.3562, -71.0502),
    "cambridge common": (42.3763, -71.1217),
    "10 waterside ave": (42.3485, -71.0440),
}

BOSTON = (42.36, -71.06)
MAX_DISTANCE_KM = 50

# ── Style detection ──────────────────────────────────────────────────

def detect_styles(text: str) -> list[str]:
    found = [style for style, pat in STYLE_PATTERNS if pat.search(text)]
    return found if found else ["other"]


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

def _dist_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = lat1 - lat2
    dlng = (lng1 - lng2) * math.cos(math.radians(lat1))
    return math.sqrt(dlat * dlat + dlng * dlng) * 111

def _is_near_boston(lat: float, lng: float) -> bool:
    return _dist_km(lat, lng, *BOSTON) <= MAX_DISTANCE_KM

def _nominatim_query(query: str) -> Optional[tuple[float, float]]:
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": "boston-latin-dance-dev/0.1"},
            timeout=10,
        )
        data = resp.json()
        if data:
            return (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        pass
    return None

def _lookup_venue(location: str) -> Optional[tuple[float, float]]:
    lower = location.lower().strip()
    if lower in VENUE_COORDS:
        return VENUE_COORDS[lower]
    for venue, coords in VENUE_COORDS.items():
        if venue in lower:
            return coords
    return None

def _build_query_variants(location: str) -> list[str]:
    cleaned = re.sub(r"#\w+\s*", "", location)
    cleaned = re.sub(r",\s*FL\s+\d+", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+-\s+\d+\w*\s+Floor", "", cleaned, flags=re.I)
    cleaned = cleaned.strip()

    parts = [p.strip() for p in cleaned.split(",")]
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        norm = re.sub(r"\d{5}(-\d{4})?", "", part.lower()).strip()
        if norm not in seen:
            seen.add(norm)
            deduped.append(part)
    deduped_str = ", ".join(deduped)

    base = deduped_str if ("MA" in deduped_str or "Boston" in deduped_str) else f"{deduped_str}, Boston, MA"
    variants = [base]
    no_number = re.sub(r"^\d+\s+", "", base)
    if no_number != base:
        variants.append(no_number)
    return variants


def geocode(location: str) -> Optional[tuple[float, float]]:
    """Return (lat, lng) for a location string, or None."""
    if not location:
        return None

    venue = _lookup_venue(location)
    if venue:
        return venue

    if len(location) < 5:
        return None

    lower = location.lower().strip()
    if lower in _geo_cache:
        cached = _geo_cache[lower]
        return tuple(cached.values()) if cached else None

    for query in _build_query_variants(location):
        result = _nominatim_query(query)
        if result and _is_near_boston(*result):
            _geo_cache[lower] = {"lat": result[0], "lng": result[1]}
            _save_geo_cache(_geo_cache)
            return result
        if result:
            print(f"  Rejected (too far): \"{query}\" -> {result[0]}, {result[1]} ({_dist_km(*result, *BOSTON):.1f}km)")
        time.sleep(1.1)

    _geo_cache[lower] = None
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
) -> dict:
    """Build a DanceEvent dict matching the TypeScript schema."""
    if end is None:
        end = start

    combined = f"{name} {description}"
    if styles is None:
        styles = detect_styles(combined)
    if cost is None:
        cost = extract_cost(combined)

    if lat is None or lng is None:
        coords = geocode(location)
        if coords:
            lat, lng = coords

    return {
        "id": id,
        "name": name,
        "startDate": start.isoformat() if start.tzinfo else start.replace(tzinfo=timezone.utc).isoformat(),
        "endDate": end.isoformat() if end.tzinfo else end.replace(tzinfo=timezone.utc).isoformat(),
        "dayOfWeek": DAYS[start.isoweekday() % 7],
        "location": location,
        "lat": lat,
        "lng": lng,
        "description": description,
        "url": url,
        "styles": styles,
        "cost": cost,
        "recurring": recurring,
        "source": source,
    }


def filter_future_events(events: list[dict], grace_days: int = 1) -> list[dict]:
    """Remove events whose startDate is in the past (with grace period)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    kept = []
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev["startDate"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
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


def load_sources() -> list[dict]:
    """Load data/sources.json."""
    return json.loads((DATA_DIR / "sources.json").read_text())


def get_source(source_id: str) -> Optional[dict]:
    for s in load_sources():
        if s["id"] == source_id:
            return s
    return None

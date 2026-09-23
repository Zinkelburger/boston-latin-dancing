"""Venue identity: location aliases, same-place tests, location inference,
and the Boston-area geo-fence."""

import math
import re
from pathlib import Path
from typing import Optional

import atomic_io
from scraper_utils import (
    VENUE_COORDS,
    _eventbrite_address,
    _is_near_boston,
    _normalize,
    clean_location,
)

from . import paths


# Maps variant location names to a canonical key so events at the same
# physical venue match even when sources name the venue differently. The
# table itself is data, not code: data/location-aliases.json, shaped
# {"canonical-key": ["alias", "alias", ...]}. Keys starting with "_" are notes.


def load_location_aliases(path: Optional[Path] = None) -> dict[str, str]:
    """Flatten the aliases file into the alias -> canonical-key map that
    _canonical_location() walks. Insertion order is preserved because the
    substring pass takes the first alias that matches, so the file's order is
    the precedence order. A missing file means no aliases; a corrupt one raises."""
    raw = atomic_io.read_json(path or paths.LOCATION_ALIASES_JSON, default={})
    aliases: dict[str, str] = {}
    for key, variants in raw.items():
        if key.startswith("_"):
            continue
        for alias in variants:
            aliases[alias.lower().strip()] = key
    return aliases


LOCATION_ALIASES: dict[str, str] = load_location_aliases()


def canonical_location(location: str) -> Optional[str]:
    """Return a canonical location key, or None if no alias matches."""
    lower = location.lower().strip()
    if lower in LOCATION_ALIASES:
        return LOCATION_ALIASES[lower]
    for alias, key in LOCATION_ALIASES.items():
        if alias in lower:
            return key
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


def locations_same(a: dict, b: dict) -> bool:
    """Check if two events are at the same venue (aliases, coords, or string)."""
    canon_a = canonical_location(a.get("location", ""))
    canon_b = canonical_location(b.get("location", ""))
    if canon_a and canon_b:
        return canon_a == canon_b
    if _coords_close(a, b):
        return True
    loc_a = (a.get("location") or "").lower().strip()
    loc_b = (b.get("location") or "").lower().strip()
    if loc_a and loc_b:
        return loc_a == loc_b
    return False


def location_key(location: str) -> str:
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


def infer_location(event: dict) -> None:
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


# A location that ends in a US state code ("Dallas, TX", "New York, NY 10001",
# "San Diego, CA, USA"). Upper-case only: a case-insensitive match would read
# the last word of "come on in" as Indiana.
_TRAILING_STATE_RE = re.compile(
    r"(?:^|[,\s])"
    r"(A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]"
    r"|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])"
    r"(?:\s+\d{5}(?:-\d{4})?)?"
    r"(?:,?\s*(?:US|USA|U\.S\.A?\.?|United States))?\s*$"
)
_NEW_ENGLAND_STATES = frozenset({"MA", "NH", "RI", "CT", "ME", "VT"})


def _location_names_far_state(location: str) -> bool:
    """True if ``location`` ends in a US state outside New England.

    The text fallback for events the geocoder refused to place: it rejects a
    hit more than MAX_DISTANCE_KM from Boston and leaves lat/lng empty, so a
    calendar entry for "Dallas, TX" reaches ingest with no coordinates and
    would otherwise land in the review queue every week.
    """
    m = _TRAILING_STATE_RE.search((location or "").strip())
    return bool(m) and m.group(1) not in _NEW_ENGLAND_STATES


def is_out_of_area(event: dict) -> bool:
    """True if the event is clearly outside the Boston metro area.

    Feed sources (beatrice-calendar, eventbrite, etc.) supply explicit lat/lng,
    which bypass the geocoder's own distance rejection. This rule catches the
    whole class of out-of-area events at ingest, so they never need per-event
    blocking. An event without coordinates is judged on its location text
    alone: a trailing non-New-England state code is out of area, anything
    else passes through.
    """
    lat, lng = event.get("lat"), event.get("lng")
    if lat is None or lng is None:
        return _location_names_far_state(event.get("location") or "")
    return not _is_near_boston(lat, lng)

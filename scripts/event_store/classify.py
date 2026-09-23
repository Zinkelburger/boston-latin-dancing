"""What kind of event this is: Latin relevance, big one-off events, class
hints, validation and enrichment (geocode, styles, cost)."""

import re
from typing import Optional

import scraper_utils
from scraper_utils import detect_styles, extract_cost, mentions_latin

from .locations import infer_location
from .names import is_special_edition, normalize_name
from . import sources


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


def derive_special(ev: dict) -> None:
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


def looks_like_class(event: dict) -> bool:
    """Heuristic: reads like a class/workshop with no social component.

    Advisory only. Returns True when class-y words appear and no social/party
    signal offsets them (an event that runs "lesson at 8, social at 9" has both
    and is not flagged).
    """
    text = f"{event.get('name', '')} {event.get('description', '')}"
    if not _CLASS_HINT_RE.search(text):
        return False
    return not _SOCIAL_HINT_RE.search(text)


def special_edition_mismatch(a: dict, b: dict) -> bool:
    """True when exactly one of two events is a special edition.

    Merging across this line folds an anniversary/festival/takeover/guest night
    into its recurring series (or vice-versa), which must never happen.
    """
    return is_special_edition(normalize_name(a.get("name", ""))) != \
        is_special_edition(normalize_name(b.get("name", "")))


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
        coords = scraper_utils.geocode(event["location"])
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


def enrich_event(event: dict) -> None:
    """Geocode, detect styles, extract cost if missing. Mutates in place."""
    infer_location(event)

    if event.get("lat") is None and event.get("location"):
        coords = scraper_utils.geocode(event["location"])
        if coords:
            event["lat"], event["lng"] = coords

    if not event.get("styles") or event.get("styles") == ["other"]:
        combined = f"{event.get('name', '')} {event.get('description', '')}"
        detected = detect_styles(combined)
        event["styles"] = detected

    if event.get("cost") is None:
        combined = f"{event.get('name', '')} {event.get('description', '')}"
        event["cost"] = extract_cost(combined)


def is_latin_relevant(event: dict, trusted_sources: Optional[set] = None) -> bool:
    """Return True if the event is relevant to Latin dance.

    Events from a curated Latin source (``latin_by_default``) always pass.
    Events with a recognized style (bachata, salsa, etc.) always pass.
    Events tagged only as 'other' must mention a Latin dance term in
    their name or description. Pass ``trusted_sources`` to skip re-reading
    sources.json on every call of a batch ingest.
    """
    if trusted_sources is None:
        trusted_sources = sources.trusted_latin_sources()
    if event.get("source") in trusted_sources:
        return True
    styles = event.get("styles", [])
    if styles != ["other"]:
        return True
    text = (event.get("name", "") + " " + event.get("description", ""))
    return mentions_latin(text)

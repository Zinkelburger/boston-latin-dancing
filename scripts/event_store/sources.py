"""Source registry lookups: precedence between sources, which calendars
are trusted as Latin by default, and adding a source to data/sources.json."""

import atomic_io
import scraper_utils

from . import paths
from .storage import append_changelog, locked


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


def is_venue_schedule_record(event: dict) -> bool:
    """Venue hub records carry a weekly schedule and must not collapse into scraped series."""
    return bool(event.get("schedule"))


def source_rank(event: dict) -> int:
    if is_venue_schedule_record(event):
        return VENUE_HUB_RANK
    return SOURCE_PRIORITY.get(event.get("source", ""), 50)


def pick_winner(a: dict, b: dict) -> tuple[dict, dict]:
    """Return (winner, loser) by source precedence. Never uses description length."""
    if source_rank(a) <= source_rank(b):
        return a, b
    return b, a


def trusted_latin_sources() -> set:
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
        s["id"] for s in scraper_utils.load_sources()
        if s.get("latin_by_default") and s.get("id")
    }


def load_source_names() -> dict[str, str]:
    """Map source IDs to human-readable organizer names from data/sources.json."""
    return {s["id"]: s["name"] for s in scraper_utils.load_sources() if "id" in s and "name" in s}


_SOURCE_REQUIRED = ("id", "type", "scraper", "name")
_SOURCE_LOCATORS = ("url", "search_queries", "facebook_events_url")


@locked
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

    sources = atomic_io.read_json(paths.SOURCES_JSON, default=[])
    if any(s.get("id") == source["id"] for s in sources):
        return {"status": "exists", "problems": [f"a source with id {source['id']!r} already exists"]}

    entry = dict(source)
    entry.setdefault("enabled", True)
    sources.append(entry)
    atomic_io.write_json(paths.SOURCES_JSON, sources)
    append_changelog("source_add", entry["id"], entry["name"])
    return {"status": "added", "problems": [], "source": entry}

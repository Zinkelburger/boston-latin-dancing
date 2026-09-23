"""Where the store lives on disk.

Every module reads these through the module (``paths.ACTIVE_JSON``), never
by copying the value at import time, so tests can point the whole store at a
temporary directory by patching this one module.
"""

from scraper_utils import ROOT, SCRAPED_DIR  # noqa: F401 - SCRAPED_DIR is part of the store's layout

DATA_DIR = ROOT / "data"
EVENTS_DIR = DATA_DIR / "events"

ACTIVE_JSON = EVENTS_DIR / "active.json"          # current/upcoming events (published to map)
ARCHIVE_JSON = EVENTS_DIR / "archive.json"        # past events (for dedup + history)
PENDING_JSON = EVENTS_DIR / "pending.json"        # dedup pairs and quarantined new events
REJECTED_JSON = EVENTS_DIR / "rejected.json"      # non-Latin events flagged for review
BLOCKED_JSON = EVENTS_DIR / "blocked.json"        # permanently excluded (checked at ingest)
CHANGELOG = EVENTS_DIR / "changelog.jsonl"
DEDUP_LOG = EVENTS_DIR / "dedup-log.jsonl"
# Publish-time review queue: scraped events that collide with a venue hub but
# are not obviously the hub's regular night. Regenerated every publish.
VENUE_CONFLICTS_JSON = EVENTS_DIR / "venue-conflicts.json"

VENUES_JSON = DATA_DIR / "venues.json"
KNOWN_DUPLICATES_JSON = DATA_DIR / "known_duplicates.json"
SOURCES_JSON = DATA_DIR / "sources.json"
LOCATION_ALIASES_JSON = DATA_DIR / "location-aliases.json"

PUBLIC_EVENTS_JSON = DATA_DIR / "events-published.json"

# The store-wide lock (see storage). The sidecar is <STORE_LOCK>.lock.
STORE_LOCK = EVENTS_DIR / "store"

EVENTS_DIR.mkdir(parents=True, exist_ok=True)

"""
Event store: the canonical lifecycle layer for boston-latin-dance events.

Store files (see paths):
  data/events/active.json   – current/upcoming events (published to map)
  data/events/archive.json  – past events (for dedup + history)
  data/events/pending.json  – dedup pairs and quarantined new events
  data/events/rejected.json – non-Latin events flagged for agent review
  data/events/blocked.json  – permanently excluded events (checked at ingest)
  data/venues.json          – permanent weekly venue schedules

Modules:
  paths, storage     – file locations, the store-wide lock, JSON I/O, changelog
  ingest             – add_event() and ingest_scraped()
  dedup              – duplicate grading and merging (known_duplicates holds
                       the human verdicts)
  archive            – active <-> archive moves
  curation           – reviewer actions on the queues and on active events
  blocklist          – permanent exclusions
  venues, schedule   – venue hubs and their weekly schedules
  venue_conflicts    – scraped events that land on a hub's night
  series             – collapsing per-date records into one recurring pin
  publishing         – building data/events-published.json
  classify, names, locations, occurrences, urls, sources, slugs – helpers

This module re-exports the operations the pipeline, CLIs and MCP server use.
Import helpers from their own module.
"""

from .archive import archive_event, archive_past_events
from .blocklist import VALID_BLOCK_CATEGORIES, block_event, unblock_event
from .curation import (
    approve_pending,
    approve_rejected,
    dismiss_rejected,
    edit_event,
    reject_pending,
    remove_active_event,
)
from .dedup import dedup_confidence, deduplicate, find_duplicate_in, merge_event
from .ingest import add_event, ingest_scraped
from .known_duplicates import forget_known_duplicate, list_known_duplicates
from .occurrences import implausible_start_hour, last_occurrence
from .publishing import (
    TRIPWIRE_MIN_PREVIOUS,
    TRIPWIRE_MIN_RATIO,
    preview_publish,
    publish,
    publish_guarded,
)
from .schedule import validate_venue_schedule
from .series import collapse_recurring_series
from .sources import add_source
from .storage import (
    load_active,
    load_archive,
    load_blocked,
    load_pending,
    load_rejected,
    save_active,
    save_archive,
    save_blocked,
    save_pending,
    save_rejected,
    store_lock,
)
from .venue_conflicts import load_venue_conflicts, resolve_venue_conflict
from .venues import add_venue, edit_venue, expand_venues, load_venues

__all__ = [
    "TRIPWIRE_MIN_PREVIOUS", "TRIPWIRE_MIN_RATIO", "VALID_BLOCK_CATEGORIES",
    "add_event", "add_source", "add_venue", "approve_pending", "approve_rejected",
    "archive_event", "archive_past_events", "block_event", "collapse_recurring_series",
    "dedup_confidence", "deduplicate", "dismiss_rejected", "edit_event", "edit_venue",
    "expand_venues", "find_duplicate_in", "forget_known_duplicate",
    "implausible_start_hour", "ingest_scraped", "last_occurrence", "list_known_duplicates",
    "load_active", "load_archive", "load_blocked", "load_pending", "load_rejected",
    "load_venue_conflicts", "load_venues", "merge_event", "preview_publish", "publish",
    "publish_guarded", "reject_pending", "remove_active_event", "resolve_venue_conflict",
    "save_active", "save_archive", "save_blocked", "save_pending", "save_rejected",
    "store_lock", "unblock_event", "validate_venue_schedule",
]

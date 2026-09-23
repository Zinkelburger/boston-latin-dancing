"""Permanent exclusions (data/events/blocked.json), checked at ingest by id
and by a name+venue key that survives sources re-minting ids."""

import re
from datetime import datetime, timezone
from typing import Optional

from . import storage
from .locations import canonical_location
from .names import normalize_name
from .storage import append_changelog, locked


def block_key(event: dict) -> Optional[str]:
    """Stable identity for blocking: normalized name + venue, no date.

    Blocking by raw id only works for sources with stable ids. Weekly listings
    from Wix/Eventbrite mint a new id per occurrence, so the id changes every
    week while the name and venue stay put — this key is what survives.
    """
    name = normalize_name(event.get("name") or "")
    if not name:
        return None
    raw_loc = (event.get("location") or "").strip()
    loc = canonical_location(raw_loc) or raw_loc
    # The same address reaches us punctuated differently depending on the path
    # it took ("101 Union St\n101 Union Street, Newton" from the scraper vs
    # "101 Union St, 101 Union Street, Newton" once stored), so strip all
    # punctuation and whitespace runs before comparing — otherwise the block
    # silently fails to match and the event returns as if never blocked.
    loc = re.sub(r"[^\w\s]", " ", loc.lower())
    loc = re.sub(r"\s+", " ", loc).strip()
    return f"{name}|{loc}"


def blocked_keys(blocked: list[dict]) -> set:
    """Name+venue keys for the blocklist, tolerating pre-existing records."""
    keys = set()
    for b in blocked:
        key = b.get("block_key") or block_key(b)
        if key:
            keys.add(key)
    return keys


VALID_BLOCK_CATEGORIES = ("defunct", "class_only", "not_latin", "not_dance", "out_of_area", "duplicate_source", "other")


def add_to_blocked(event: dict, category: str, notes: str = "") -> dict:
    """Internal helper to add an event to the blocklist."""
    if category not in VALID_BLOCK_CATEGORIES:
        return {"status": "error", "message": f"Invalid category '{category}'. Use one of: {VALID_BLOCK_CATEGORIES}"}

    blocked = storage.load_blocked()
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
        "block_key": block_key(event),
    }

    for i, existing in enumerate(blocked):
        if existing["id"] == event["id"]:
            blocked[i] = record
            storage.save_blocked(blocked)
            append_changelog("block", event["id"], f"{category}: {notes}")
            return {"status": "blocked", "event": record}

    blocked.append(record)
    storage.save_blocked(blocked)
    append_changelog("block", event["id"], f"{category}: {notes}")
    return {"status": "blocked", "event": record}


@locked
def block_event(event_id: str, category: str, notes: str = "") -> dict:
    """Block an event permanently. Removes from active or rejected and adds to blocked.json.

    Categories: defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other

    The blocklist entry is written first; the copies in active / rejected /
    pending (and archive, only when the event is nowhere else) are removed
    after. A failure part-way leaves a copy that the blocklist then rejects on
    re-ingest — never a record that is in neither place.
    """
    if category not in VALID_BLOCK_CATEGORIES:
        return {"status": "error", "message": f"Invalid category '{category}'. Use one of: {VALID_BLOCK_CATEGORIES}"}

    stores = [
        (storage.load_active, storage.save_active),
        (storage.load_rejected, storage.save_rejected),
        (storage.load_pending, storage.save_pending),
    ]
    found = None
    removals: list[tuple[list, int, callable]] = []
    for load, save in stores:
        items = load()
        idx = next((i for i, ev in enumerate(items) if ev["id"] == event_id), None)
        if idx is None:
            continue
        if found is None:
            found = items[idx]
        removals.append((items, idx, save))

    if found is None:
        archive = storage.load_archive()
        a_idx = next((i for i, ev in enumerate(archive) if ev["id"] == event_id), None)
        if a_idx is not None:
            found = archive[a_idx]
            removals.append((archive, a_idx, storage.save_archive))

    if found is None:
        return {"status": "not_found", "message": f"Event '{event_id}' not found in active, rejected, pending, or archive."}

    outcome = add_to_blocked(dict(found), category, notes)
    if outcome.get("status") != "blocked":
        return outcome

    for items, idx, save in removals:
        items.pop(idx)
        save(items)
    return outcome


@locked
def unblock_event(event_id: str) -> dict:
    """Remove an event from the blocklist. It will be re-added on the next scrape if still in the source."""
    blocked = storage.load_blocked()
    idx = next((i for i, ev in enumerate(blocked) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"Event '{event_id}' not found in blocked.json."}

    record = blocked.pop(idx)
    storage.save_blocked(blocked)
    append_changelog("unblock", event_id, f"was: {record.get('blocked_category', '')}")
    return {"status": "unblocked", "event": record}

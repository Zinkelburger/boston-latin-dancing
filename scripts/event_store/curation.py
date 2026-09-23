"""Reviewer actions: approving, rejecting and dismissing queued events,
removing active ones, and editing an event by hand."""

import json

import scraper_utils

from . import storage
from .archive import merge_into_archived
from .blocklist import add_to_blocked
from .classify import special_edition_mismatch, validate_event
from .ingest import add_event
from .known_duplicates import forget_known_duplicate, known_duplicate_verdict, persist_known_duplicate
from .storage import append_changelog, locked, queue_rejected
from .urls import dropped_url_list, event_url_list, url_key


# Statuses from add_event / _merge_into_archived that mean "the event now lives
# in a store". Anything else means it went nowhere, and an approval must then
# leave its source row where it was instead of deleting the only copy.
_LANDED_STATUSES = frozenset({
    "added", "duplicate", "merged", "reactivated", "merged_into_archive", "quarantined_new",
})
_PENDING_MARKERS = ("_dedup_candidate_of", "_dedup_confidence", "_dedup_reason",
                    "_quarantined_new", "_quarantined_at")
_REJECTED_MARKERS = ("_rejected_at", "_rejected_reason", "_review_type")


def _without(event: dict, keys: tuple) -> dict:
    return {k: v for k, v in event.items() if k not in keys}


def _not_approved(result: dict, stored: dict, queue: str) -> dict:
    """Shape the failure of an approval so the caller knows nothing moved."""
    return {
        "status": "not_approved",
        "add_status": result.get("status"),
        "message": (
            f"{result.get('message') or result.get('status')} — the event was left "
            f"in {queue}; nothing was removed."
        ),
        "event": stored,
    }


@locked
def approve_pending(event_id: str, force: bool = False) -> dict:
    """Approve a pending event, moving it to active.

    For a dedup pair (``_dedup_candidate_of`` set), approving *merges* the two
    and persists a permanent ``verdict:"same"`` so future occurrences auto-merge
    with no review. Because that is silent and compounding, this refuses to merge
    across a special-edition boundary unless ``force=True``.

    The candidate may already be archived, in which case the merge happens there
    — see _merge_into_archived. The merged record only returns to active if its
    dates are still ahead.

    Destination first: the event is landed (add_event / archive merge) and only
    then removed from pending. If landing fails, the row stays queued and the
    result says ``status: not_approved`` with the underlying ``add_status``.
    """
    pending = storage.load_pending()
    idx = next((i for i, ev in enumerate(pending) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No pending event with id '{event_id}'"}

    stored = pending[idx]
    candidate_id = stored.get("_dedup_candidate_of")

    if candidate_id and not force:
        candidate = next((e for e in storage.load_active() if e.get("id") == candidate_id), None)
        if candidate is None:
            candidate = next((e for e in storage.load_archive() if e.get("id") == candidate_id), None)
        if candidate is not None and special_edition_mismatch(stored, candidate):
            return {
                "status": "blocked_special_edition",
                "message": (
                    "Refusing to merge: one of these is a special edition "
                    "(anniversary / festival / takeover / guest-DJ night) and the "
                    "other is the recurring series — special editions stay separate. "
                    "If they genuinely are the same event, call "
                    "event_approve(event_id, force=True). Otherwise "
                    "event_reject(event_id, reason='distinct event')."
                ),
                "new_event": {"id": stored["id"], "name": stored.get("name", "")},
                "existing_event": {"id": candidate["id"], "name": candidate.get("name", "")},
            }

    # The verdict goes in first so add_event's own dedup sees the pair as
    # certain and merges instead of re-queueing. It is rolled back if the
    # approval does not land.
    had_verdict = bool(candidate_id) and \
        known_duplicate_verdict({"id": event_id}, {"id": candidate_id}) is not None
    if candidate_id:
        persist_known_duplicate(event_id, candidate_id, "same")

    event = _without(stored, _PENDING_MARKERS)
    issues = validate_event(event)
    result = merge_into_archived(event, candidate_id) if candidate_id else None
    if result is None:
        result = add_event(event, force=True)

    if result.get("status") not in _LANDED_STATUSES:
        if candidate_id and not had_verdict:
            forget_known_duplicate(event_id, candidate_id)
        return _not_approved(result, stored, "pending.json")

    # Landed. Now, and only now, retire the queued row.
    pending = [p for p in storage.load_pending() if p.get("id") != event_id]
    storage.save_pending(pending)

    if issues:
        result["warnings"] = issues
    # An approved event with no coordinates renders no map pin — it is live but
    # invisible. Flag that loudly so the agent fixes the location before publish
    # instead of shipping a ghost.
    added = result.get("event") or result.get("existing") or {}
    if added.get("lat") is None or added.get("lng") is None:
        result["published_without_coordinates"] = True
        result.setdefault("warnings", []).append(
            "no coordinates — event will NOT appear on the map; fix location via "
            "event_edit or event_set_location_override before publishing"
        )
    append_changelog("approve", event_id)
    return result


@locked
def remove_active_event(event_id: str, reason: str = "removed from active", block: bool = False, block_category: str = "other") -> dict:
    """Remove an active event.

    If block=True, moves to blocked.json (permanent, prevents re-scraping).
    If block=False, moves to rejected.json for review (as before).

    The destination (blocklist or rejected queue) is written before the event
    leaves active, so a failure — an invalid block category, a crash — never
    loses the record.
    """
    active = storage.load_active()
    idx = next((i for i, ev in enumerate(active) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No active event with id '{event_id}'"}

    event = active[idx]

    if block:
        outcome = add_to_blocked(dict(event), block_category, reason)
        if outcome.get("status") != "blocked":
            return outcome
        active.pop(idx)
        storage.save_active(active)
        return outcome

    queued = queue_rejected(event, reason)
    active.pop(idx)
    storage.save_active(active)
    append_changelog("remove", event_id, reason)
    return {"status": "removed", "event": queued}


@locked
def approve_rejected(event_id: str) -> dict:
    """Promote a rejected event to active (bypasses Latin relevance check).

    Lands the event first; the rejected row is only removed once it has.
    """
    rejected = storage.load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No rejected event with id '{event_id}'"}

    stored = rejected[idx]
    event = _without(stored, _REJECTED_MARKERS)

    result = add_event(event, force=True, skip_latin_check=True)
    if result.get("status") not in _LANDED_STATUSES:
        return _not_approved(result, stored, "rejected.json")

    # add_event clears the rejected row on most landing paths; the force-merge
    # path does not, so reconcile here rather than trust it.
    rejected = [r for r in storage.load_rejected() if r.get("id") != event_id]
    storage.save_rejected(rejected)
    append_changelog("approve_rejected", event_id, "promoted from rejected queue")
    return result


@locked
def dismiss_rejected(event_id: str, reason: str = "", block: bool = False, block_category: str = "other") -> dict:
    """Dismiss a rejected event.

    If block=True, moves to blocked.json (permanent, prevents re-scraping).
    If block=False, just removes from rejected (for one-off events that won't reappear).
    """
    rejected = storage.load_rejected()
    idx = next((i for i, ev in enumerate(rejected) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No rejected event with id '{event_id}'"}

    stored = rejected[idx]

    if block:
        outcome = add_to_blocked(dict(stored), block_category, reason)
        if outcome.get("status") != "blocked":
            return outcome
        rejected.pop(idx)
        storage.save_rejected(rejected)
        return outcome

    rejected.pop(idx)
    storage.save_rejected(rejected)
    event = _without(stored, _REJECTED_MARKERS)
    append_changelog("dismiss_rejected", event_id, reason)
    return {"status": "dismissed", "event": event, "reason": reason}


@locked
def reject_pending(event_id: str, reason: str = "") -> dict:
    """Reject a pending event."""
    pending = storage.load_pending()
    idx = next((i for i, ev in enumerate(pending) if ev["id"] == event_id), None)
    if idx is None:
        return {"status": "not_found", "message": f"No pending event with id '{event_id}'"}

    stored = pending[idx]
    candidate_id = stored.get("_dedup_candidate_of")
    # The verdict is the durable outcome of a rejection; record it before the
    # queue row goes so an interruption cannot lose the decision.
    if candidate_id:
        persist_known_duplicate(event_id, candidate_id, "different")

    pending.pop(idx)
    storage.save_pending(pending)
    event = _without(stored, _PENDING_MARKERS)
    append_changelog("reject", event_id, reason)
    return {"status": "rejected", "event": event, "reason": reason}


@locked
def edit_event(event_id: str, updates: dict) -> dict:
    """Edit fields on an active event."""
    active = storage.load_active()
    idx = None
    for i, ev in enumerate(active):
        if ev["id"] == event_id:
            idx = i
            break

    if idx is None:
        return {"status": "not_found", "message": f"No active event with id '{event_id}'"}

    # A link the reviewer deletes has to stay deleted. Re-scrapes accumulate
    # every URL a source has ever carried back into urls[], so clearing a dead
    # alt link only held until the next ingest put it straight back — the same
    # facebook share wrapper and expired instagram post came back to
    # check-links week after week. Record the removals; merge_event honours them.
    before = event_url_list(active[idx]) if ("url" in updates or "urls" in updates) else []

    for k, v in updates.items():
        if k != "id":
            active[idx][k] = v

    # Record a hand-corrected time so re-scrapes cannot revert it (merge_event
    # honours it). One-offs only: a series' startDate has to keep advancing.
    if ("startDate" in updates or "endDate" in updates) and not active[idx].get("recurring"):
        active[idx]["_time_override"] = {
            k: active[idx][k] for k in ("startDate", "endDate") if active[idx].get(k)
        }

    if before:
        kept = {url_key(u) for u in event_url_list(active[idx])}
        dropped = {url_key(u): u for u in dropped_url_list(active[idx])}
        dropped.update({url_key(u): u for u in before if url_key(u) not in kept})
        # An edit that puts a link back overrides an earlier removal.
        surviving = sorted(v for k_, v in dropped.items() if k_ not in kept)
        if surviving:
            active[idx]["_dropped_urls"] = surviving
        else:
            active[idx].pop("_dropped_urls", None)

    # Re-geocode if location changed
    if "location" in updates and (active[idx].get("lat") is None or "location" in updates):
        coords = scraper_utils.geocode(updates["location"])
        if coords:
            active[idx]["lat"], active[idx]["lng"] = coords

    storage.save_active(active)
    append_changelog("edit", event_id, json.dumps(updates))
    return {"status": "updated", "event": active[idx]}

"""Moving events between active and the archive."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import storage
from .classify import enrich_event
from .dedup import dedup_confidence, dedup_reason, log_dedup, merge_event
from .occurrences import last_occurrence
from .storage import append_changelog, clear_stale_rejected, locked


@locked
def archive_past_events() -> list[dict]:
    """Move past events from active to archive. Returns archived events."""
    active = storage.load_active()
    archive = storage.load_archive()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    still_active = []
    newly_archived = []

    for ev in active:
        dt = last_occurrence(ev)
        if dt is None:
            still_active.append(ev)
            continue

        if dt < cutoff:
            ev["archivedAt"] = now.isoformat()
            newly_archived.append(ev)
            append_changelog("archive", ev["id"])
        else:
            still_active.append(ev)

    if newly_archived:
        # An event can reach the archive twice — a re-scrape that fails to
        # match the archived copy (a changed venue string is enough) lands a
        # fresh active record with the same id, which is archived again on the
        # next run. Appending blindly left byte-identical pairs in the archive,
        # and publish() ships the archive verbatim, so the site rendered the
        # same past event twice. Refresh the stored copy instead.
        by_id = {ev.get("id"): i for i, ev in enumerate(archive) if ev.get("id")}
        for ev in newly_archived:
            idx = by_id.get(ev.get("id"))
            if idx is None:
                by_id[ev.get("id")] = len(archive)
                archive.append(ev)
            else:
                archive[idx] = ev
        # Destination (archive) before source (active).
        storage.save_archive(archive)
        storage.save_active(still_active)

    return newly_archived


@locked
def archive_event(event_id: str, reason: str = "") -> dict:
    """Move one active event to the archive by hand, whatever its dates.

    Returns ``{"status": "archived", "event": ...}`` or ``{"status":
    "not_found", "event": None}``. The archive is written before active is,
    so an interrupted move duplicates rather than loses.
    """
    active = storage.load_active()
    idx = next((i for i, ev in enumerate(active) if ev.get("id") == event_id), None)
    if idx is None:
        return {"status": "not_found", "event": None,
                "message": f"No active event with id '{event_id}'"}

    event = dict(active[idx])
    event["archivedAt"] = datetime.now(timezone.utc).isoformat()

    archive = storage.load_archive()
    a_idx = next((i for i, ev in enumerate(archive) if ev.get("id") == event_id), None)
    if a_idx is None:
        archive.append(event)
    else:
        archive[a_idx] = event
    storage.save_archive(archive)

    active.pop(idx)
    storage.save_active(active)
    append_changelog("archive", event_id, reason or "archived by hand")
    return {"status": "archived", "event": event}


def merge_into_archived(event: dict, candidate_id: str) -> Optional[dict]:
    """Merge an approved event into its dedup candidate when that candidate is
    already archived. Returns None when the candidate is not there, leaving the
    caller on the ordinary add_event() path.

    approve_pending() lands its event with add_event(force=True), and force
    deliberately skips add_event's archive-match branch. So approving a dedup
    pair whose candidate had already been archived appended a second copy to
    active instead of merging, and archive_past_events() then filed it beside
    the original — one past festival, two archive rows, two searchable ghosts
    (Boston Salsa Fest, 2026-08-27). Approval knows the candidate's id
    outright, so send the merge to wherever that candidate actually lives.
    """
    active = storage.load_active()
    if any(e.get("id") == candidate_id for e in active):
        return None

    archive = storage.load_archive()
    idx = next((i for i, e in enumerate(archive) if e.get("id") == candidate_id), None)
    if idx is None:
        return None

    archived = archive[idx]
    confidence = dedup_confidence(archived, event) or "review"
    reason = dedup_reason(archived, event, confidence)
    merged = merge_event(archived, event)
    enrich_event(merged)

    # Approving a still-upcoming event has to put it back on the map; one whose
    # merged dates have already passed stays filed. Same cutoff
    # archive_past_events() uses, so the two can never disagree and bounce a
    # record between the stores.
    last = last_occurrence(merged)
    if last is not None and last >= datetime.now(timezone.utc) - timedelta(hours=24):
        merged["reactivatedAt"] = datetime.now(timezone.utc).isoformat()
        # Destination (active) before source (archive).
        active.append(merged)
        storage.save_active(active)
        archive.pop(idx)
        storage.save_archive(archive)
        clear_stale_rejected(merged["id"])
        log_dedup("reactivate", archived, event, confidence, reason)
        append_changelog("reactivate", merged["id"],
                          f"approved {event['id']} merged into archived {candidate_id}")
        return {"status": "reactivated", "confidence": confidence, "event": merged}

    archive[idx] = merged
    storage.save_archive(archive)
    log_dedup("approve_merge", archived, event, confidence, reason)
    append_changelog("merge", event["id"], f"folded into archived {candidate_id}")
    return {"status": "merged_into_archive", "confidence": confidence, "event": merged}

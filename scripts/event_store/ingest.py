"""Getting events into the store: add_event() for one event, and
ingest_scraped() for everything under data/scraped/."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import atomic_io
import source_signal
from atomic_io import CorruptJSONError

from . import blocklist, paths, sources, storage
from .classify import enrich_event, is_latin_relevant
from .dedup import dedup_reason, find_duplicate_in, log_dedup, merge_event
from .known_duplicates import persist_known_duplicate
from .locations import infer_location, is_out_of_area
from .occurrences import last_occurrence
from .sources import is_venue_schedule_record
from .storage import (
    append_changelog,
    clear_stale_rejected,
    locked,
    log,
    queue_rejected,
    remove_from_active,
)


@locked
def add_event(
    event: dict,
    force: bool = False,
    skip_latin_check: bool = False,
    blocked_ids: Optional[set] = None,
    quarantine_new: bool = False,
    blocked_keys: Optional[set] = None,
    distinct_from: Optional[list] = None,
    trusted_sources: Optional[set] = None,
) -> dict:
    """Add an event to the active store. Returns result dict with status.

    Dedup tiers:
      certain -> auto-merge (same ID or URL)
      review  -> route to pending.json for review (unless force=True)

    force=True (admin approval) does TWO things: it bypasses the ingest-time
    exclusion guards (blocklist + out-of-area geo-fence) AND it force-merges a
    review-tier dedup match instead of queueing it — i.e. it asserts "any
    fuzzy match IS the same event". Never use it to add an event that merely
    resembles an existing one (a similarly-named distinct event gets swallowed
    into the existing record). For that, pass distinct_from=[existing_ids]:
    it persists a permanent "different" verdict for each pair up front, so
    the fuzzy match neither queues for review nor force-merges. (The old
    workaround — add without force, reject the pending pair, re-add — still
    works, but fails for events the guards drop before dedup ever runs.)
    Pass blocked_ids / blocked_keys / trusted_sources to avoid re-reading
    blocked.json and sources.json on every call during a batch ingest.

    quarantine_new=True routes brand-new events (no duplicate anywhere) to
    pending.json instead of active, so unattended runs can refresh existing
    events without putting unreviewed ones on the map. Re-scrapes update the
    queued copy in place rather than duplicating it.

    Runs under the store lock. Every store file is loaded at most once per
    call, and any move between two files writes the destination before it
    removes the source, so a crash between the two leaves a duplicate that
    dedup catches next run — never a lost event.
    """
    if is_venue_schedule_record(event):
        return {"status": "rejected", "message": "Venue schedules belong in venues.json"}

    if not event.get("id") or not event.get("startDate"):
        return {"status": "rejected", "message": "event missing id or startDate"}

    if not force:
        if blocked_ids is None or blocked_keys is None:
            _blocked = storage.load_blocked()
            if blocked_ids is None:
                blocked_ids = {b["id"] for b in _blocked}
            if blocked_keys is None:
                blocked_keys = blocklist.blocked_keys(_blocked)
        if event.get("id") in blocked_ids:
            return {"status": "blocked", "message": "event is permanently blocked"}
        # Sources that mint a fresh id per occurrence (nlf-events-<slug>-<date>,
        # Eventbrite eb-<numeric>) would otherwise slip past the id check every
        # week, so a blocked weekly class reappears in the queue forever.
        # Matching on name+venue makes the block actually stick.
        key = blocklist.block_key(event)
        if key and key in blocked_keys:
            return {"status": "blocked",
                    "message": "event is permanently blocked (name+venue match)"}

        if is_out_of_area(event):
            # A previously-added event whose coords now fall out of bounds must
            # not linger on the map: drop any stale active copy before rejecting.
            remove_from_active(event["id"])
            return {
                "status": "rejected_out_of_area",
                "message": f"event outside Boston metro area: {event.get('location', '')}",
            }

    active = storage.load_active()
    archive = storage.load_archive()

    if not skip_latin_check and not is_latin_relevant(event, trusted_sources):
        # Not a Latin-dance event. It goes to the rejected queue rather than
        # vanishing: a keyword scan is a good first filter but not a verdict,
        # and a queue row is what lets a reviewer rescue a real social with an
        # odd title (event_approve_rejected) or block a recurring false hit
        # for good. Re-scrapes refresh the queued row in place, so the queue
        # holds one row per event, not one per week. An event a human already
        # approved (it is in active or archive) is never re-queued: the
        # dedup below merges the re-scrape instead.
        already_approved = (
            any(e.get("id") == event["id"] for e in active)
            or any(e.get("id") == event["id"] for e in archive)
        )
        if not already_approved:
            reason = "not Latin dance relevant (styles=['other'], no Latin terms)"
            queue_rejected(event, reason, "non_latin")
            append_changelog("reject_non_latin", event["id"], reason)
            return {"status": "rejected_non_latin", "message": reason}

    infer_location(event)

    if distinct_from:
        # A verdict for an event's own id would suppress its certain-tier
        # self-merge forever, so self-pairs are skipped.
        for other_id in distinct_from:
            if other_id and other_id != event["id"]:
                persist_known_duplicate(event["id"], other_id, "different")
                append_changelog("distinct_from", event["id"],
                                  f"pre-marked different from {other_id}")

    active_match = find_duplicate_in(event, active)
    archive_match = find_duplicate_in(event, archive)
    if archive_match is not None and not force:
        archive_idx, conf = archive_match
        if conf == "certain":
            # A source series can be archived under its old UID while a newer
            # source has already added the current occurrence under another
            # UID. Re-activating before checking active would create two pins
            # for the same night on every scrape. Fold the refreshed series
            # into the active copy and retire its archived predecessor.
            if active_match is not None and active_match[1] == "certain":
                active_idx = active_match[0]
                existing = active[active_idx]
                reason = dedup_reason(existing, event, "certain")
                active[active_idx] = merge_event(existing, event)
                enrich_event(active[active_idx])
                storage.save_active(active)
                archived = archive.pop(archive_idx)
                storage.save_archive(archive)
                log_dedup("certain", existing, event, "certain", reason)
                clear_stale_rejected(event["id"])
                append_changelog(
                    "merge_archived_series",
                    event["id"],
                    f"folded into active {existing['id']}",
                )
                return {
                    "status": "duplicate",
                    "confidence": "certain",
                    "existing": active[active_idx],
                    "retired_archive": archived["id"],
                }

            # Only pull an event back out of the archive when the incoming
            # copy is actually upcoming. Stale scraped files re-listing past
            # dates must not ping-pong events between archive and active
            # (reactivate here, re-archive in archive_past_events) every run.
            incoming_dt = last_occurrence(event)
            if incoming_dt is None or incoming_dt < datetime.now(timezone.utc) - timedelta(hours=24):
                return {
                    "status": "duplicate",
                    "confidence": conf,
                    "message": "already archived; incoming copy is not upcoming",
                }
            archived = archive[archive_idx]
            reason = dedup_reason(archived, event, conf)
            merged = merge_event(archived, event)
            enrich_event(merged)
            merged["reactivatedAt"] = datetime.now(timezone.utc).isoformat()
            # Destination first: land the record in active, then retire the
            # archived copy. archive_past_events() reconciles the same id.
            active.append(merged)
            storage.save_active(active)
            archive.pop(archive_idx)
            storage.save_archive(archive)
            log_dedup("reactivate", archived, event, conf, reason)
            clear_stale_rejected(merged["id"])
            append_changelog("reactivate", merged["id"], "from archive (certain)")
            return {"status": "reactivated", "confidence": conf, "event": merged}

    if active_match is not None:
        active_idx, conf = active_match
        existing = active[active_idx]
        reason = dedup_reason(existing, event, conf)

        if conf == "certain":
            log_dedup("certain", existing, event, conf, reason)
            active[active_idx] = merge_event(existing, event)
            enrich_event(active[active_idx])
            storage.save_active(active)
            clear_stale_rejected(event["id"])
            return {"status": "duplicate", "confidence": conf, "existing": active[active_idx]}

        if conf == "review":
            if force:
                log_dedup("force", existing, event, conf, reason)
                active[active_idx] = merge_event(existing, event)
                enrich_event(active[active_idx])
                storage.save_active(active)
                append_changelog("merge", event["id"], f"force-merged review dup of {existing['id']}")
                return {"status": "merged", "confidence": conf, "event": active[active_idx]}

            log_dedup("review", existing, event, conf, reason)
            event["_dedup_candidate_of"] = existing["id"]
            event["_dedup_confidence"] = conf
            event["_dedup_reason"] = reason
            pending = storage.load_pending()
            if any(p.get("id") == event["id"] for p in pending):
                return {
                    "status": "pending_review",
                    "confidence": conf,
                    "reason": reason,
                    "new_event": event,
                    "existing_event": existing,
                    "already_pending": True,
                }
            pending.append(event)
            storage.save_pending(pending)
            append_changelog("pending_review", event["id"], f"review dup of {existing['id']}: {reason}")
            return {
                "status": "pending_review",
                "confidence": conf,
                "reason": reason,
                "new_event": event,
                "existing_event": existing,
            }

    # A brand-new event that already ended is pure churn (stale scraped file):
    # it would only be archived on the next pass. Skip it outright.
    if not force:
        new_dt = last_occurrence(event)
        if new_dt is not None and new_dt < datetime.now(timezone.utc) - timedelta(hours=24):
            return {"status": "skipped_past", "message": "new event is already past"}

    enrich_event(event)

    if quarantine_new:
        pending = storage.load_pending()
        idx = next((i for i, p in enumerate(pending) if p.get("id") == event["id"]), None)
        event["_quarantined_new"] = True
        if idx is not None:
            # Keep the first-seen timestamp so queue age reflects reality.
            event["_quarantined_at"] = pending[idx].get(
                "_quarantined_at", datetime.now(timezone.utc).isoformat()
            )
            pending[idx] = event
        else:
            event["_quarantined_at"] = datetime.now(timezone.utc).isoformat()
            pending.append(event)
            append_changelog("quarantine_new", event["id"])
        storage.save_pending(pending)
        return {"status": "quarantined_new", "event": event}

    active.append(event)
    storage.save_active(active)
    clear_stale_rejected(event["id"])
    append_changelog("add", event["id"])
    return {"status": "added", "event": event}


@locked
def ingest_scraped(source_id: Optional[str] = None, quarantine_new: bool = False) -> dict:
    """Ingest events from data/scraped/ into the active store.

    Handles dedup against both active and archive (reactivation).
    Review-tier duplicates are routed to pending.json for review.

    quarantine_new=True additionally routes brand-new events to pending.json
    instead of active (for unattended runs — see add_event).
    """
    if source_id:
        files = [paths.SCRAPED_DIR / f"{source_id}.json"]
    else:
        files = sorted(
            p for p in paths.SCRAPED_DIR.glob("*.json")
            if not p.name.endswith("-raw.json")
        )

    added = 0
    merged = 0
    reactivated = 0
    skipped = 0
    rejected_non_latin = 0
    rejected_out_of_area = 0
    blocked = 0
    pending_review = 0
    quarantined_new = 0
    review_items: list[dict] = []
    corrupt_files: list[str] = []

    _blocked = storage.load_blocked()
    blocked_ids = {b["id"] for b in _blocked}
    blocked_keys = blocklist.blocked_keys(_blocked)
    trusted = sources.trusted_latin_sources()

    # Sources ranked "noisy" (see data/sources.json + source_signal.py) always
    # route brand-new finds to the pending queue for review, even when the run
    # otherwise publishes directly -- their raw feeds are mostly non-dance.
    # Sources marked unreliable scrape for research but never enter the store.
    # A malformed sources.json raises here and aborts the run: silently
    # treating every source as trusted-and-reliable is worse than no ingest.
    noisy_sources = source_signal.noisy_source_ids()
    unreliable_sources = source_signal.unreliable_source_ids()

    skipped_unreliable = 0

    for path in files:
        if not path.exists():
            continue
        try:
            events = atomic_io.read_json(path, default=[])
        except CorruptJSONError as exc:
            # A scraper's output is an input, not the store. Skip it loudly so
            # the other sources still ingest, and name it in the result.
            log(f"  ⚠️  skipping unreadable scrape file {path.name}: {exc}")
            corrupt_files.append(path.name)
            continue

        for ev in events:
            if not ev.get("id"):
                continue
            if ev.get("source") in unreliable_sources:
                skipped_unreliable += 1
                continue
            eff_quarantine = quarantine_new or (ev.get("source") in noisy_sources)
            result = add_event(ev, blocked_ids=blocked_ids, blocked_keys=blocked_keys,
                               quarantine_new=eff_quarantine, trusted_sources=trusted)
            status = result["status"]
            if status == "added":
                added += 1
            elif status == "quarantined_new":
                quarantined_new += 1
            elif status == "merged":
                merged += 1
            elif status == "reactivated":
                reactivated += 1
            elif status == "duplicate":
                skipped += 1
            elif status == "skipped_past":
                skipped += 1
            elif status == "rejected_non_latin":
                rejected_non_latin += 1
            elif status == "rejected_out_of_area":
                rejected_out_of_area += 1
            elif status == "blocked":
                blocked += 1
            elif status == "pending_review":
                pending_review += 1
                review_items.append({
                    "new": result["new_event"]["name"],
                    "existing": result["existing_event"]["name"],
                    "reason": result["reason"],
                })

    result = {
        "status": "ingested",
        "added": added,
        "merged": merged,
        "reactivated": reactivated,
        "skipped_duplicates": skipped,
        "skipped_unreliable": skipped_unreliable,
        "rejected_non_latin": rejected_non_latin,
        # Legacy name for the same count, kept for run_pipeline's summary.
        "dropped_non_latin": rejected_non_latin,
        "rejected_out_of_area": rejected_out_of_area,
        "blocked": blocked,
        "pending_review": pending_review,
        "quarantined_new": quarantined_new,
        "files_processed": len(files),
    }
    if corrupt_files:
        result["files_corrupt"] = corrupt_files
    if review_items:
        result["review_items"] = review_items
    return result

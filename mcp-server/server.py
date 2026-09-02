#!/usr/bin/env python3
"""
Boston Latin Dance – Event Lifecycle MCP Server.

Local stdio-based MCP server for managing the event pipeline:
  - Add/edit/archive events
  - Approve/reject submissions
  - Run scrapers and ingest results
  - Publish public/events.json

Run: .venv/bin/python mcp-server/server.py   (from the repo root)

stdio transport: stdout IS the protocol channel. Nothing in this file may
print to stdout — diagnostics go through _log() to stderr.
"""

import functools
import hashlib
import json
import sys
import traceback
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:  # mcp >= 2.0 renamed FastMCP; keep the 1.x name working too.
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - depends on the installed SDK
    from mcp.server.fastmcp import FastMCP as _Server

from atomic_io import CorruptJSONError, read_json  # noqa: E402
from event_store import (  # noqa: E402
    VALID_BLOCK_CATEGORIES,
    VENUES_JSON,
    _looks_like_class,
    _special_edition_mismatch,
    add_event,
    add_source,
    add_venue,
    approve_pending,
    approve_rejected,
    archive_event,
    archive_past_events,
    block_event,
    dismiss_rejected,
    edit_event,
    find_duplicate_in,
    forget_known_duplicate,
    ingest_scraped,
    list_known_duplicates,
    load_active,
    load_archive,
    load_blocked,
    load_pending,
    load_rejected,
    load_venue_conflicts,
    parse_date,
    publish_guarded,
    reject_pending,
    remove_active_event,
    resolve_venue_conflict,
    unblock_event,
    validate_venue_schedule,
)
from run_pipeline import run_scrapers  # noqa: E402
from scraper_utils import (  # noqa: E402
    detect_styles,
    geocode,
    load_scrape_health,
    load_sources,
    make_event,
)
from verify_events import verify_all  # noqa: E402

mcp = _Server("boston-latin-dance")

SCRAPE_TIMEOUT_SECONDS = 180


# ── Plumbing ──────────────────────────────────────────────────────────


def _log(message: str) -> None:
    """Diagnostics for the human running the client. Never stdout."""
    print(message, file=sys.stderr, flush=True)


def _dump(payload) -> str:
    return json.dumps(payload, indent=2, default=str)


def _error(message: str, error_type: str, **extra) -> str:
    return _dump({"error": message, "type": error_type, **extra})


def tool(fn: Callable[..., str]) -> Callable[..., str]:
    """Register ``fn`` as an MCP tool that always returns a JSON string.

    A raised exception would surface as an opaque MCP protocol error; the
    agent gets more from ``{"error": ..., "type": ...}``. CorruptJSONError is
    passed through verbatim because its message names the broken file and
    says not to treat it as empty — that is exactly what the agent must see.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> str:
        try:
            return fn(*args, **kwargs)
        except CorruptJSONError as exc:
            _log(f"{fn.__name__}: {exc}")
            return _error(str(exc), "CorruptJSONError", path=str(exc.path))
        except KeyError as exc:
            _log(f"{fn.__name__}: KeyError {exc}\n{traceback.format_exc()}")
            return _error(f"missing key {exc}", "KeyError")
        except Exception as exc:  # noqa: BLE001 - every failure becomes a payload
            _log(f"{fn.__name__}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            return _error(str(exc) or type(exc).__name__, type(exc).__name__)

    return mcp.tool()(wrapper)


def _split_csv(raw: Optional[str]) -> Optional[list[str]]:
    if not raw:
        return None
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _find(pool: list[dict], event_id: str) -> Optional[dict]:
    return next((e for e in pool if e.get("id") == event_id), None)


# ── Event tools ───────────────────────────────────────────────────────


@tool
def event_list(
    status: str = "active",
    style: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List events by status (active/pending/rejected/blocked/archive/venue_conflict). Optionally filter by style or text search.

    venue_conflict returns scraped events that collide with a venue hub's
    weekly night. Each row is self-contained — both sides in full, plus whether
    the clock times overlap — so the call can be made per row without reading
    anything else. Resolve with venue_conflict_resolve().
    """
    if limit < 0:
        return _error(f"limit must be >= 0, got {limit}", "ValueError")

    if status == "venue_conflict":
        queue = load_venue_conflicts()
        rows = queue.get("conflicts", [])[:limit]
        return _dump({
            "generated_at": queue.get("generated_at"),
            "needs_review": len(queue.get("conflicts", [])),
            "conflicts": rows,
            # Read-only: what suppression already folded away this publish. Not
            # decisions to make — a list to skim so a wrong fold is caught by
            # reading rather than by someone noticing a missing pin next month.
            "auto_suppressed": [
                {"id": r["id"], "name": r["event"]["name"], "window": r["event"]["window"],
                 "hub": r["hub"]["name"], "hub_window": r["hub"]["window"]}
                for r in queue.get("suppressed", [])
            ],
            "how_to_resolve": (
                'venue_conflict_resolve(event_id, decision) — "distinct" (both real, '
                'both get pins), "replaces" (event takes over the venue that night; '
                'the hub skips that date), "duplicate" (it is just the hub\'s weekly '
                'night; fold it in). Decisions persist across re-scrapes.'
            ),
        })

    loaders = {
        "active": load_active,
        "pending": load_pending,
        "rejected": load_rejected,
        "blocked": load_blocked,
        "archive": load_archive,
    }
    if status not in loaders:
        return _error(
            f"Invalid status '{status}'. Use active/pending/rejected/blocked/archive/venue_conflict.",
            "ValueError",
        )
    events = loaders[status]()
    total = len(events)

    if style:
        events = [e for e in events if style.lower() in [s.lower() for s in e.get("styles", [])]]

    if search:
        q = search.lower()
        events = [e for e in events if q in e.get("name", "").lower() or q in e.get("location", "").lower() or q in e.get("description", "").lower()]

    events = events[:limit]
    # Cache active once for pending dedup-candidate comparisons below.
    active_by_id = {a["id"]: a for a in load_active()} if status == "pending" else {}
    summary = []
    for e in events:
        row = {
            "id": e["id"],
            "name": e["name"],
            "startDate": e.get("startDate", ""),
            "location": e.get("location", ""),
            "styles": e.get("styles", []),
            "recurring": e.get("recurring", False),
            "has_coords": e.get("lat") is not None,
        }
        if status == "rejected":
            row["rejected_reason"] = e.get("_rejected_reason", "")
            row["review_type"] = e.get("_review_type", "")
        if status == "pending":
            if e.get("_quarantined_new"):
                row["quarantined_new"] = True
            if e.get("_dedup_candidate_of"):
                row["dedup_candidate_of"] = e["_dedup_candidate_of"]
                row["dedup_reason"] = e.get("_dedup_reason", "")
                candidate = active_by_id.get(e["_dedup_candidate_of"])
                # Approving across this line would fold a special edition into
                # its recurring series (blocked without force) — surface it.
                if candidate is not None and _special_edition_mismatch(e, candidate):
                    row["special_edition_mismatch"] = True
            # Advisory: reads like a class/workshop rather than a social dance.
            if _looks_like_class(e):
                row["looks_like_class"] = True
        summary.append(row)

    return _dump({"count": len(summary), "total": total, "events": summary})


@tool
def event_get(event_id: str) -> str:
    """Get full details of a specific event by ID. Searches active, pending, rejected, blocked, then archive."""
    for pool_name, pool in [
        ("active", load_active()),
        ("pending", load_pending()),
        ("rejected", load_rejected()),
        ("blocked", load_blocked()),
        ("archive", load_archive()),
    ]:
        ev = _find(pool, event_id)
        if ev is not None:
            return _dump({"status": pool_name, "event": ev})
    return _error(f"Event '{event_id}' not found in any pool.", "NotFound")


@tool
def event_add(
    name: str,
    start_date: str,
    location: str,
    end_date: Optional[str] = None,
    description: str = "",
    url: Optional[str] = None,
    styles: Optional[str] = None,
    cost: Optional[str] = None,
    recurring: bool = False,
    source: str = "manual",
    event_id: Optional[str] = None,
    force: bool = False,
    distinct_from: Optional[str] = None,
    dry_run: bool = False,
) -> str:
    """Add a new event to the active store. Handles dedup, geocode, and style detection automatically.

    Args:
        name: Event name
        start_date: ISO datetime string (e.g. 2026-06-15T20:00:00-04:00)
        location: Venue address
        end_date: ISO datetime for event end (defaults to start_date)
        description: Event description
        url: Event URL
        styles: Comma-separated dance styles (e.g. "salsa,bachata"). Auto-detected if omitted.
        cost: Cost string (e.g. "$15", "Free"). Auto-detected if omitted.
        recurring: Whether this is a recurring event
        source: Source identifier
        event_id: Custom event ID (auto-generated if omitted)
        force: If True, merge into an existing duplicate instead of rejecting.
            Only for pairs you have confirmed are the SAME event — a fuzzy
            name match force-merges too, swallowing a distinct event. When
            force-adding an event that resembles an existing DISTINCT event,
            also pass distinct_from.
        distinct_from: Comma-separated ids of existing events this one merely
            resembles but is NOT. Persists permanent "different" verdicts so
            the fuzzy match neither queues for review nor force-merges (e.g.
            a festival pre-party vs the festival itself).
        dry_run: Build the event and report what add_event would do (which
            existing event it would merge into or queue against) without
            writing anything. Use before force=True.
    """
    start = parse_date(start_date)
    if start is None:
        return _error(f"start_date '{start_date}' is not an ISO datetime", "ValueError")
    end = start
    if end_date:
        end = parse_date(end_date)
        if end is None:
            return _error(f"end_date '{end_date}' is not an ISO datetime", "ValueError")

    if not event_id:
        hash_input = f"{name}{start_date}{location}"
        event_id = f"{source}-{hashlib.sha1(hash_input.encode()).hexdigest()[:16]}"

    # Same builder the scrapers use, so a manual event carries exactly the
    # fields (dayOfWeek, cost, styles, coords) a scraped one would.
    event = make_event(
        id=event_id,
        name=name,
        start=start,
        end=end,
        location=location,
        description=description,
        url=url,
        styles=_split_csv(styles),
        cost=cost,
        recurring=recurring,
        source=source,
    )

    distinct_ids = [s.strip() for s in distinct_from.split(",") if s.strip()] if distinct_from else None

    if dry_run:
        report = {"dry_run": True, "event": event, "force": force, "distinct_from": distinct_ids}
        for pool_name, pool in (("active", load_active()), ("archive", load_archive())):
            hit = find_duplicate_in(event, pool)
            if hit is not None:
                idx, confidence = hit
                existing = pool[idx]
                report["duplicate"] = {
                    "pool": pool_name,
                    "id": existing["id"],
                    "name": existing.get("name"),
                    "confidence": confidence,
                    "special_edition_mismatch": _special_edition_mismatch(event, existing),
                }
                if distinct_ids and existing["id"] in distinct_ids:
                    report["would"] = f"add as distinct from {existing['id']}"
                elif confidence == "certain" or force:
                    report["would"] = f"merge into {pool_name} event {existing['id']}"
                else:
                    report["would"] = f"queue in pending as a review-tier match of {existing['id']}"
                break
        else:
            report["would"] = "add to active (no duplicate found)"
        return _dump(report)

    result = add_event(event, force=force, distinct_from=distinct_ids)
    return _dump(result)


@tool
def event_edit(event_id: str, updates_json: str) -> str:
    """Edit fields on an active event. Pass updates as a JSON object string.

    Example updates_json: {"cost": "$20", "url": "https://example.com"}
    """
    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError as e:
        return _error(f"Invalid JSON: {e}", "JSONDecodeError")
    if not isinstance(updates, dict):
        return _error("updates_json must be a JSON object", "ValueError")

    return _dump(edit_event(event_id, updates))


@tool
def event_archive(event_id: Optional[str] = None, reason: str = "manual") -> str:
    """Archive past events. If event_id is given, archive that specific event. Otherwise archive all past events automatically."""
    if event_id:
        return _dump(archive_event(event_id, reason=reason))

    archived = archive_past_events()
    return _dump({
        "status": "done",
        "archived_count": len(archived),
        "archived": [{"id": e["id"], "name": e["name"]} for e in archived],
    })


# ── Pending / submission review ───────────────────────────────────────


@tool
def event_approve(event_id: str, force: bool = False, dry_run: bool = False) -> str:
    """Approve a pending submission, moving it to active after validation and geocoding.

    For a dedup pair this MERGES the two events and permanently records them as
    the same (future occurrences auto-merge with no review). If the pair straddles
    a special-edition boundary (an anniversary/festival/takeover/guest night vs its
    recurring series) the merge is refused — pass force=True only if they truly are
    the same event; otherwise use event_reject(reason="distinct event").

    dry_run=True reports what approving would do (and what force would
    override) without writing anything.
    """
    if dry_run:
        pending = _find(load_pending(), event_id)
        if pending is None:
            return _error(f"Event '{event_id}' not found in pending.", "NotFound")
        report = {"dry_run": True, "force": force, "event_id": event_id, "name": pending.get("name")}
        candidate_id = pending.get("_dedup_candidate_of")
        if candidate_id:
            candidate = _find(load_active(), candidate_id)
            mismatch = candidate is not None and _special_edition_mismatch(pending, candidate)
            report["merge_into"] = candidate_id
            report["dedup_reason"] = pending.get("_dedup_reason", "")
            report["special_edition_mismatch"] = mismatch
            if mismatch and not force:
                report["would"] = "refuse the merge (special edition vs series); force=True would merge"
            else:
                report["would"] = f"merge into {candidate_id} and record the pair as the same forever"
        else:
            report["would"] = "move to active"
        if _looks_like_class(pending):
            report["looks_like_class"] = True
        return _dump(report)

    return _dump(approve_pending(event_id, force=force))


@tool
def event_reject(event_id: str, reason: str = "") -> str:
    """Reject a pending submission with an optional reason."""
    return _dump(reject_pending(event_id, reason))


@tool
def event_remove(
    event_id: str,
    reason: str = "removed from active",
    block: bool = False,
    block_category: str = "other",
    dry_run: bool = False,
) -> str:
    """Remove an active event.

    If block=False (default): queues in rejected.json for review.
    If block=True: permanently blocks the event (prevents re-scraping).

    block_category (when block=True): defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other

    dry_run=True reports what would happen without writing.
    """
    if block and block_category not in VALID_BLOCK_CATEGORIES:
        return _error(
            f"Invalid block_category '{block_category}'. Use one of: {sorted(VALID_BLOCK_CATEGORIES)}",
            "ValueError",
        )
    if dry_run:
        ev = _find(load_active(), event_id)
        if ev is None:
            return _error(f"Event '{event_id}' not found in active.", "NotFound")
        return _dump({
            "dry_run": True,
            "event_id": event_id,
            "name": ev.get("name"),
            "would": (
                f"remove from active and block permanently ({block_category})"
                if block else "remove from active and queue in rejected for review"
            ),
            "reason": reason,
        })

    return _dump(remove_active_event(event_id, reason, block=block, block_category=block_category))


@tool
def venue_conflict_resolve(event_id: str, decision: str, note: str = "") -> str:
    """Rule on an event that collides with a venue hub's weekly night.

    decision:
      distinct  — both are real and both keep a pin (an afternoon workshop that
                  hands off to the venue's regular night, say).
      replaces  — the event takes over the venue that night; the hub is told to
                  skip that date so no phantom pin ships for the usual night.
      duplicate — the scrape is just the hub's weekly night; fold it in.

    The ruling is stored on the event and survives re-scrapes, so a pair you
    have already judged will not come back next week. Run event_publish()
    afterwards to apply it.
    """
    return _dump(resolve_venue_conflict(event_id, decision, note=note))


@tool
def event_approve_rejected(event_id: str) -> str:
    """Promote a rejected event to active (bypasses Latin dance keyword check)."""
    return _dump(approve_rejected(event_id))


@tool
def event_dismiss_rejected(event_id: str, reason: str = "", block: bool = False, block_category: str = "other") -> str:
    """Dismiss a rejected event.

    If block=True, permanently blocks the event (prevents re-scraping from adding it back).
    If block=False (default), just removes from rejected queue.

    block_category (when block=True): defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other
    """
    return _dump(dismiss_rejected(event_id, reason, block=block, block_category=block_category))


@tool
def event_block(event_id: str, category: str, notes: str = "", dry_run: bool = False) -> str:
    """Permanently block an event from appearing on the map. Prevents re-scraping from adding it back.

    Searches active, rejected, and archive to find and remove the event, then adds to blocked.json.

    Categories:
      defunct          - event used to exist but organizer discontinued it
      class_only       - only classes, no social dancing
      not_latin        - confirmed not Latin dance relevant
      not_dance        - music class, fitness, drum circle, etc.
      out_of_area      - not in Boston metro area
      duplicate_source - covered by another source or venue entry
      other            - catch-all

    dry_run=True reports which pool the event would be pulled from without writing.
    """
    if category not in VALID_BLOCK_CATEGORIES:
        return _error(
            f"Invalid category '{category}'. Use one of: {sorted(VALID_BLOCK_CATEGORIES)}",
            "ValueError",
        )
    if dry_run:
        for pool_name, loader in (("active", load_active), ("rejected", load_rejected), ("archive", load_archive)):
            ev = _find(loader(), event_id)
            if ev is not None:
                return _dump({
                    "dry_run": True,
                    "event_id": event_id,
                    "name": ev.get("name"),
                    "found_in": pool_name,
                    "would": f"remove from {pool_name} and block permanently ({category})",
                    "notes": notes,
                })
        if _find(load_blocked(), event_id) is not None:
            return _dump({"dry_run": True, "event_id": event_id, "would": "nothing: already blocked"})
        return _error(f"Event '{event_id}' not found in active, rejected, or archive.", "NotFound")

    return _dump(block_event(event_id, category, notes))


@tool
def event_unblock(event_id: str) -> str:
    """Remove an event from the blocklist. It will be re-added on the next scrape if still in the source."""
    return _dump(unblock_event(event_id))


@tool
def event_list_blocked(category: Optional[str] = None) -> str:
    """List all permanently blocked events. Optionally filter by category.

    Categories: defunct, class_only, not_latin, not_dance, out_of_area, duplicate_source, other
    """
    blocked = load_blocked()
    if category:
        if category not in VALID_BLOCK_CATEGORIES:
            return _error(
                f"Invalid category '{category}'. Use one of: {sorted(VALID_BLOCK_CATEGORIES)}",
                "ValueError",
            )
        blocked = [b for b in blocked if b.get("blocked_category") == category]
    summary = [{
        "id": b["id"],
        "name": b.get("name", ""),
        "blocked_category": b.get("blocked_category", ""),
        "blocked_reason": b.get("blocked_reason", ""),
        "blocked_at": b.get("blocked_at", ""),
    } for b in blocked]
    return _dump({"count": len(summary), "blocked": summary})


# ── Known-duplicate verdicts ──────────────────────────────────────────


@tool
def known_duplicate_list() -> str:
    """List human-reviewed duplicate verdicts from data/known_duplicates.json.

    Each entry is a pair of event IDs with a verdict: "same" (future occurrences
    auto-merge silently) or "different" (the pair is never flagged for review
    again). Use this to audit what past approvals committed the pipeline to.
    """
    entries = list_known_duplicates()
    return _dump({"count": len(entries), "known_duplicates": entries})


@tool
def known_duplicate_forget(id_a: str, id_b: str, dry_run: bool = False) -> str:
    """Delete a stored duplicate verdict so the pair is re-evaluated on next scrape.

    Undoes a wrong "same" verdict (which otherwise auto-merges the pair forever)
    or a wrong "different" verdict (which suppresses it from review forever).
    Removing the record does NOT un-merge events that were already merged — fix
    those with event_edit / event_add if needed.

    dry_run=True shows the verdict that would be deleted without deleting it.
    """
    if dry_run:
        pair = {id_a, id_b}
        matches = [e for e in list_known_duplicates() if {e.get("id_a"), e.get("id_b")} == pair]
        if not matches:
            return _dump({"dry_run": True, "would": "nothing: no verdict stored for this pair",
                          "id_a": id_a, "id_b": id_b})
        return _dump({"dry_run": True, "would": "delete this verdict", "verdicts": matches})

    return _dump(forget_known_duplicate(id_a, id_b))


# ── Scraping and ingestion ────────────────────────────────────────────


@tool
def event_scrape(source_id: Optional[str] = None, quarantine_new: bool = False) -> str:
    """Run scrapers and ingest new events into the active store.

    If source_id is given, run only that scraper. Otherwise run all enabled scrapers.
    After scraping, automatically archives past events. Does NOT publish — call event_publish() separately.
    quarantine_new=True routes brand-new events to pending.json for review instead of active.

    Uses the same runner (and scraper list) as the cron pipeline, so a source
    that works here works there. Facebook sources require browser MCP and are
    not auto-runnable.
    """
    results = run_scrapers(only=source_id, timeout=SCRAPE_TIMEOUT_SECONDS)
    if source_id and not results:
        return _error(f"Unknown or non-runnable source '{source_id}'.", "ValueError")

    ingest_result = ingest_scraped(source_id, quarantine_new=quarantine_new)
    archived = archive_past_events()

    return _dump({
        "scrape_results": results,
        "scrapers_failed": [r["source_id"] for r in results if not r.get("ok")],
        "ingestion": ingest_result,
        "archived_count": len(archived),
    })


@tool
def event_ingest(source_id: Optional[str] = None, quarantine_new: bool = False) -> str:
    """Ingest events from data/scraped/ into active store WITHOUT re-running scrapers.

    Useful after manually placing a JSON file in data/scraped/ or after browser-based scraping.
    quarantine_new=True routes brand-new events to pending.json for review instead of active.
    """
    return _dump(ingest_scraped(source_id, quarantine_new=quarantine_new))


@tool
def scraper_health() -> str:
    """Report each scraper's last-run health, flagging ones that need a redesign.

    A scraper writes [] both when a page has no Latin events (fine) and when its
    parser matched nothing because the page markup changed (broken). This tells
    them apart via `raw_found` — events parsed BEFORE the keyword filter:

      status "ok"                – found the page structure (raw_found > 0)
      status "structure_missing" – page loaded but parser matched nothing → the
                                   scraper needs a redesign; we may be silently
                                   missing events. ALERT the user.
      status "fetch_error"       – page unreachable last run (usually transient)

    Returns the full health map plus a `needs_redesign` list of source ids to
    call out. Run this during the weekly review and flag any redesign-needed
    scrapers prominently in the summary.
    """
    health = load_scrape_health()
    needs_redesign = [sid for sid, h in health.items()
                      if h.get("status") == "structure_missing"]
    return _dump({"needs_redesign": needs_redesign, "health": health})


# ── Publishing ────────────────────────────────────────────────────────


@tool
def event_publish() -> str:
    """Regenerate public/events.json from active events + expanded venues.

    This is the build step that produces the file the frontend reads.
    Always run this after making changes to see them on the map.

    Guarded: if the live-event count collapses below 70% of the previously
    published file, the published files are restored and the result reports
    "status": "tripwire" with "tripped": true — do NOT commit; investigate first.
    """
    return _dump(publish_guarded())


# ── Venue management ──────────────────────────────────────────────────


@tool
def venue_list() -> str:
    """List all permanent weekly venues from data/venues.json."""
    venues = read_json(VENUES_JSON, default=[])
    summary = [{
        "id": v["id"],
        "name": v["name"],
        "location": v.get("location", ""),
        "styles": v.get("styles", []),
        "schedule_days": [s["dayOfWeek"] for s in v.get("schedule", [])],
    } for v in venues]
    return _dump({"count": len(summary), "venues": summary})


@tool
def venue_add(
    venue_id: str,
    name: str,
    location: str,
    schedule_json: str,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    url: Optional[str] = None,
    styles: Optional[str] = None,
    cost: Optional[str] = None,
    description: str = "",
) -> str:
    """Add a new permanent venue to data/venues.json.

    Args:
        venue_id: Unique venue identifier (e.g. "havana-club")
        name: Venue/event series name
        location: Full street address
        schedule_json: JSON array of schedule objects, e.g. [{"dayOfWeek": "Friday", "time": "9:00 PM – 2:00 AM", "note": ""}]
        lat/lng: Coordinates (auto-geocoded if omitted)
        url: Website URL
        styles: Comma-separated styles (e.g. "salsa,bachata")
        cost: Cost string
        description: Venue description
    """
    try:
        schedule = json.loads(schedule_json)
    except json.JSONDecodeError as e:
        return _error(f"Invalid schedule JSON: {e}", "JSONDecodeError")
    issues = validate_venue_schedule(schedule)
    if issues:
        return _error("Invalid schedule: " + "; ".join(issues), "ValueError", issues=issues)

    if lat is None or lng is None:
        coords = geocode(location)
        if coords:
            lat, lng = coords

    venue = {
        "id": venue_id,
        "name": name,
        "location": location,
        "lat": lat,
        "lng": lng,
        "url": url,
        "styles": _split_csv(styles) or detect_styles(f"{name} {description}"),
        "cost": cost,
        "description": description,
        "schedule": schedule,
    }
    return _dump(add_venue(venue))


# ── Source management ─────────────────────────────────────────────────


@tool
def source_list() -> str:
    """List all registered event sources from data/sources.json."""
    sources = load_sources()
    return _dump({"count": len(sources), "sources": sources})


@tool
def source_add(
    source_id: str,
    source_type: str,
    name: str,
    scraper: str,
    url: Optional[str] = None,
    config_json: Optional[str] = None,
) -> str:
    """Register a new event source in data/sources.json.

    Args:
        source_id: Unique source identifier (e.g. "my-venue-events")
        source_type: Source type (ics, eventbrite, wix-events, facebook, api)
        name: Human-readable source name
        scraper: Script filename that handles this source (e.g. "scrape_ics.py")
        url: Primary URL for the source
        config_json: Additional config as JSON object (merged into entry)
    """
    entry = {
        "id": source_id,
        "type": source_type,
        "name": name,
        "scraper": scraper,
        "enabled": True,
    }
    if url:
        entry["url"] = url
    if config_json:
        try:
            extra = json.loads(config_json)
        except json.JSONDecodeError as e:
            return _error(f"Invalid config_json: {e}", "JSONDecodeError")
        if not isinstance(extra, dict):
            return _error("config_json must be a JSON object", "ValueError")
        entry.update(extra)

    return _dump(add_source(entry))


# ── Verification ──────────────────────────────────────────────────────


@tool
def event_verify(
    event_id: Optional[str] = None,
    stale_days: Optional[int] = None,
) -> str:
    """Verify event details against their source URLs.

    Checks each event's URL (organizer site, Eventbrite, etc.) to confirm
    the event is still active and details are accurate. Facebook and Instagram
    events are flagged for browser-based follow-up.

    Args:
        event_id: Verify just this one event. If omitted, verify all.
        stale_days: Only re-verify events not checked in N+ days.

    Returns a report with status per event:
      confirmed, location_mismatch, date_mismatch, cancelled, page_gone,
      needs_review, needs_browser, no_source, unverifiable
    """
    report = verify_all(event_id=event_id, stale_days=stale_days)

    summary: dict[str, int] = {}
    for entry in report:
        s = entry["status"]
        summary[s] = summary.get(s, 0) + 1

    # "confirmed" and "reachable_only" both need no action — a reachable page with
    # no structured data just couldn't be checked, it isn't a problem to fix.
    flagged = [
        {
            "event_id": e["event_id"],
            "event_name": e["event_name"],
            "status": e["status"],
            "notes": e.get("notes", ""),
            "source_url": e.get("source_url"),
            "our_location": e.get("our_location", ""),
            "source_location": e.get("source_location", ""),
            "our_date": e.get("our_date", ""),
            "source_date": e.get("source_date", ""),
        }
        for e in report
        if e["status"] not in ("confirmed", "reachable_only")
    ]

    return _dump({
        "total_verified": len(report),
        "summary": summary,
        "flagged_count": len(flagged),
        "flagged": flagged,
    })


@tool
def event_set_location_override(event_id: str, location: str) -> str:
    """Set a location override on an event so re-scraping won't revert it.

    Use after verifying that an event's location in the ICS/source is wrong
    and you've confirmed the correct location.

    Args:
        event_id: The event to fix
        location: The correct location string
    """
    before = _find(load_active(), event_id)
    if before is None:
        return _error(f"Event '{event_id}' not found in active.", "NotFound")

    # edit_event re-geocodes a changed location, saves under the store's
    # lock and writes the changelog — the same path every other edit takes.
    result = edit_event(event_id, {"_location_override": location, "location": location})
    if result.get("status") != "updated":
        return _error(result.get("message", f"Event '{event_id}' not found in active."), "NotFound")
    after = result["event"]
    return _dump({
        "status": "override_set",
        "event_id": event_id,
        "location": location,
        "geocoded": after.get("lat") is not None
        and (after.get("lat"), after.get("lng")) != (before.get("lat"), before.get("lng")),
    })


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
